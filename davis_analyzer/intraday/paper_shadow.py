"""做T策略模拟盘影子验证（盘后回放，每晚 cron 19:55）.

模式同 limitup/queue_sim：不改变任何生产账户，当晚拉当日 5 分钟线、
在 paper_positions 的**真实底仓**上回放获胜配置 gap_smart，成交写入
研究库影子台账（intraday_shadow_trade / intraday_shadow_run），前向
积累纸面验证数据。与 paper_trading 主流程完全隔离（AGENTS 沙盒条款）。

数据路径：
- 当日分钟线优先读研究库缓存（backfill_chunk 已覆盖的月份）；
  否则 baostack 现场拉取（只回放不落盘，避免月块台账被部分数据污染）。
- 日线锚（pre_close/close/MA20 历史）来自生产库 daily_price——
  依赖 19:20 的 limitup daily_refresh cron 先行完成。
"""

from __future__ import annotations

import time

import pandas as pd
from loguru import logger

from davis_analyzer.intraday import db
from davis_analyzer.intraday.backfill import fetch_range, parse_baostock_frame, to_bs_code
from davis_analyzer.intraday.engine import (
    Bar, DayCtx, IntradayConfig, simulate_day,
)
from davis_analyzer.limitup.events import limit_ratio_for

SMART_CONFIG = dict(
    gap_pct=0.03, exit_time="14:00",
    require={"trend_up": True, "vol_ratio1_max": 2.5},
)


# ── 影子台账 ──

def ensure_shadow_tables(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS intraday_shadow_trade ("
        "trade_date TEXT NOT NULL, ts_code TEXT NOT NULL, strategy TEXT NOT NULL, "
        "base_shares INTEGER, shares INTEGER, "
        "entry_time TEXT, entry_px REAL, exit_time TEXT, exit_px REAL, "
        "pnl REAL, net_bps REAL, created_at TEXT, "
        "PRIMARY KEY (trade_date, ts_code, strategy))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS intraday_shadow_run ("
        "trade_date TEXT PRIMARY KEY, status TEXT NOT NULL, "
        "n_universe INTEGER, n_trades INTEGER, note TEXT, created_at TEXT)"
    )
    conn.commit()


# ── 输入组装 ──

_bs_session = None


def _bs():
    """lazy 登录的 baostock 会话单例（缓存未命中时才需要）."""
    global _bs_session
    if _bs_session is None:
        import baostock

        lg = baostock.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock login 失败: {lg.error_msg}")
        _bs_session = baostock
    return _bs_session


def bs_logout() -> None:
    global _bs_session
    if _bs_session is not None:
        _bs_session.logout()
        _bs_session = None


def base_positions() -> dict[str, int]:
    """真实底仓：paper_positions 全账户按 code 合计股数."""
    import sqlite3

    from stockhot.core.config import DB_PATH

    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT ts_code, SUM(shares) FROM paper_positions WHERE shares>0 "
            "GROUP BY ts_code"
        ).fetchall()
    finally:
        con.close()
    return {r[0]: int(r[1]) for r in rows}


def day_bars(conn, code: str, day: str, bs_mod=None) -> pd.DataFrame:
    """当日分钟线：研究库缓存优先，缺失时 baostock 现场拉取（不落盘）."""
    cached = pd.read_sql_query(
        "SELECT trade_time, open, high, low, close, volume FROM minute_bar "
        "WHERE ts_code=? AND trade_date=? AND freq='5min' ORDER BY trade_time",
        conn, params=(code, day),
    )
    if not cached.empty:
        return cached
    bs = bs_mod or _bs()  # 现场拉取（只回放不落盘）
    raw = fetch_range(bs, to_bs_code(code), day, day)
    bars = parse_baostock_frame(raw, code, "5min")
    return bars[["trade_time", "open", "high", "low", "close", "volume"]]


def day_features(conn, mkt_conn, code: str, day: str, day_open: float,
                 bar0_vol: float) -> dict:
    """shadow 专用的单日因果特征（trend_up / vol_ratio1；smart 配置所需）."""
    # 严格取昨日及以前的完整日线（当日行可能由旁路管道部分写入，不可依赖）
    hist = pd.read_sql_query(
        "SELECT close FROM daily_price WHERE ts_code=? AND trade_date<? "
        "AND close>0 ORDER BY trade_date DESC LIMIT 21",
        mkt_conn, params=(code, day),
    )
    if len(hist) < 21:
        return {"trend_up": None, "vol_ratio1": None}
    ma20 = hist["close"].iloc[1:].mean()          # 截至前日的 20 日均
    trend_up = bool(hist["close"].iloc[0] > ma20)  # 昨收 vs 该均线

    vols = pd.read_sql_query(
        "SELECT trade_date, volume FROM minute_bar "
        "WHERE ts_code=? AND freq='5min' AND trade_time='09:35' "
        "AND trade_date<? ORDER BY trade_date DESC LIMIT 20",
        conn, params=(code, day),
    )
    vol_ratio1 = (
        float(bar0_vol) / float(vols["volume"].median())
        if len(vols) >= 10 and float(vols["volume"].median()) > 0 else None
    )
    return {"trend_up": trend_up, "vol_ratio1": vol_ratio1}


# ── 主流程 ──

def run_shadow(trade_date: str, db_path: str | None = None,
               trade_fraction: float = 0.30, persist: bool = True) -> dict:
    """回放一个交易日并写影子台账（persist=False 只看不写）。返回运行摘要."""
    from davis_analyzer.intraday.strategies import GapDownSmart
    from davis_analyzer.limitup.db import connect as market_connect

    conn = db.connect(db_path)
    ensure_shadow_tables(conn)
    mkt = market_connect()
    summary = {"trade_date": trade_date, "n_universe": 0, "n_trades": 0,
               "trades": [], "status": "ok", "note": ""}
    try:
        # 当日 close 优先取日线（可能由旁路管道部分写入），pre_close 一律自行推导
        today_close = dict(mkt.execute(
            "SELECT ts_code, close FROM daily_price WHERE trade_date=? AND close>0",
            (trade_date,),
        ).fetchall())
        if not today_close:
            summary["status"] = "skipped_daily"
            summary["note"] = "daily_price 无当日行"
            return summary

        positions = base_positions()
        universe = sorted(positions)
        summary["n_universe"] = len(universe)

        strategy = GapDownSmart(**SMART_CONFIG)
        for code in universe:
            try:
                bars_df = day_bars(conn, code, trade_date)
            except Exception as exc:
                logger.warning("shadow {} {} 拉取失败: {}", trade_date, code, exc)
                continue
            if bars_df.empty or len(bars_df) < 10:
                continue
            prow = mkt.execute(
                "SELECT close FROM daily_price WHERE ts_code=? AND trade_date<? "
                "AND close>0 ORDER BY trade_date DESC LIMIT 1",
                (code, trade_date),
            ).fetchone()
            if prow is None or not prow[0]:
                continue  # 无昨收锚
            pre_close = float(prow[0])
            feat = day_features(conn, mkt, code, trade_date,
                                float(bars_df.iloc[0]["open"]),
                                float(bars_df.iloc[0]["volume"]))
            base = int(positions[code])
            trade_shares = int(base * trade_fraction / 100) * 100
            if trade_shares < 100:
                continue  # 真实底仓太薄，做不了T
            ratio = limit_ratio_for(code)
            daily_close = float(today_close.get(code) or bars_df.iloc[-1]["close"])
            ctx = DayCtx(
                code, trade_date, pre_close, daily_close,
                round(pre_close * (1 + ratio) + 1e-9, 2),
                round(pre_close * (1 - ratio) + 1e-9, 2),
                base, trade_shares, -1.0, features=feat,
            )
            bars = [
                Bar(r.trade_time, float(r.open), float(r.high),
                    float(r.low), float(r.close))
                for r in bars_df.itertuples(index=False)
            ]
            strategy.reset()
            res = simulate_day(strategy, ctx, bars, IntradayConfig())
            if res is None:
                continue
            summary["trades"].append(res)
            if not persist:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO intraday_shadow_trade "
                "(trade_date, ts_code, strategy, base_shares, shares, "
                "entry_time, entry_px, exit_time, exit_px, pnl, net_bps, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (trade_date, code, res.strategy, base,
                 max(res.shares_bought, res.shares_sold),
                 res.entry_time, res.avg_buy, res.exit_time, res.avg_sell,
                 res.pnl, res.net_bps, time.strftime("%Y-%m-%d %H:%M:%S")),
            )
        summary["n_trades"] = len(summary["trades"])
        if not persist:
            return summary
        conn.execute(
            "INSERT OR REPLACE INTO intraday_shadow_run "
            "(trade_date, status, n_universe, n_trades, note, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (trade_date, summary["status"], summary["n_universe"],
             summary["n_trades"], summary["note"],
             time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    finally:
        bs_logout()
        mkt.close()
        conn.close()
    return summary


def shadow_report(db_path: str | None = None) -> str:
    """影子台账累计报表（纸面验证进度）."""
    conn = db.connect(db_path)
    try:
        ensure_shadow_tables(conn)
        trades = pd.read_sql_query(
            "SELECT * FROM intraday_shadow_trade ORDER BY trade_date", conn
        )
        runs = pd.read_sql_query(
            "SELECT * FROM intraday_shadow_run ORDER BY trade_date DESC LIMIT 10",
            conn,
        )
    finally:
        conn.close()
    if trades.empty:
        return "影子台账为空——shadow 尚未产生交易"
    n_days = runs[runs.status == "ok"].shape[0]
    win = (trades.pnl > 0).mean()
    lines = [
        f"影子验证累计: {len(trades)} 笔 / {n_days} 个运行日 | "
        f"胜率 {win:.1%} | mean {trades.net_bps.mean():+.0f}bps | "
        f"med {trades.net_bps.median():+.0f}bps | 总净利 ¥{trades.pnl.sum():,.0f}",
        "（对照回测预期: mean +79bps / 胜率 55%——样本 ≥30 笔后开始有判定力）",
    ]
    return "\n".join(lines)
