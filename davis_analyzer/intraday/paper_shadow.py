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

数据增强（2026-08-28，满月复盘 T014 的输入）：
- intraday_shadow_universe：每日全宇宙特征快照（gap/振幅/trend_up/
  vol_ratio1 + 状态分类），近阈值（-3%~-2%）与被过滤样本不再丢失；
- intraday_shadow_exit_alt：每笔成交的收盘竞价退出反事实 + 持仓期 MAE；
- intraday_shadow_mkt：每日市场环境（复用 limitup.sentiment 三轴 regime）。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

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
NEAR_MISS_GAP = 0.02  # 近阈值下沿：gap 在 [-3%, -2%] 记为 near_miss


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
    conn.execute(
        "CREATE TABLE IF NOT EXISTS intraday_shadow_universe ("
        "trade_date TEXT NOT NULL, ts_code TEXT NOT NULL, "
        "shares INTEGER, pre_close REAL, n_bars INTEGER, "
        "gap_pct REAL, amplitude REAL, trend_up INTEGER, vol_ratio1 REAL, "
        "state TEXT NOT NULL, created_at TEXT, "
        "PRIMARY KEY (trade_date, ts_code))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS intraday_shadow_exit_alt ("
        "trade_date TEXT NOT NULL, ts_code TEXT NOT NULL, strategy TEXT NOT NULL, "
        "entry_time TEXT, entry_px REAL, exit_time_actual TEXT, "
        "net_bps_actual REAL, exit_px_d0_close REAL, net_bps_d0_close REAL, "
        "delta_bps REAL, mae_bps REAL, created_at TEXT, "
        "PRIMARY KEY (trade_date, ts_code, strategy))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS intraday_shadow_mkt ("
        "trade_date TEXT PRIMARY KEY, "
        "limit_up_count REAL, lianban_count REAL, max_boards REAL, "
        "broken_rate REAL, promo_12 REAL, premium REAL, red_rate REAL, "
        "up_down_ratio REAL, new_high_ratio REAL, index_ma_bull INTEGER, "
        "regime_label TEXT, created_at TEXT)"
    )
    conn.commit()


# ── 纯函数：判别 / 分类 / 反事实 ──

def eval_require(require: dict, features: dict) -> tuple[bool, str]:
    """镜像 GapDownSmart._passes：返回 (是否通过, 首个未过的约束名)."""
    if not require:
        return True, ""
    if not features:
        return False, "nohist"
    for key, want in require.items():
        feat_key = key[:-4] if key.endswith(("_min", "_max")) else key
        v = features.get(feat_key)
        if v is None or v != v:  # None 或 NaN
            return False, f"nohist:{feat_key}"
        if isinstance(want, bool):
            if bool(v) != want:
                return False, feat_key
        elif key.endswith("_min") and v < want:
            return False, feat_key
        elif key.endswith("_max") and v > want:
            return False, feat_key
    return True, ""


def classify_state(open_px: float, pre_close: float, feat: dict,
                   require: dict, tradeable: bool,
                   gap_th: float = 0.03,
                   near_th: float = NEAR_MISS_GAP) -> str:
    """单股票日状态分类（信号语义与引擎 bar0 判定一致，含等号）.

    traded / filtered_<约束> / near_miss / no_signal / thin_base
    （no_bars / no_pre_close 由调用方在锚缺失时给定）。
    """
    if not tradeable:
        return "thin_base"
    gap = open_px / pre_close - 1.0
    if gap > -gap_th:  # 未达低开阈值
        return "near_miss" if gap <= -near_th else "no_signal"
    ok, reason = eval_require(require, feat)
    return "traded" if ok else f"filtered_{reason}"


def exit_alt_metrics(entry_px: float, shares: int, net_bps_actual: float,
                     bars: list[Bar], entry_time: str, daily_close: float,
                     config: IntradayConfig) -> dict:
    """同入场、收盘竞价退出的反事实净收益 + 持仓期最差低点（MAE）.

    成本口径逐项复刻 DayRunner：买=滑点内含于 entry_px+佣金；卖=滑点
    内含+佣金+印花税；net_bps 分母为单边名义本金均值。
    """
    slip = config.slippage_bps / 1e4
    buy_per = entry_px * (1 + config.commission_bps / 1e4)
    sell_fill = daily_close * (1 - slip)
    pnl = shares * (
        sell_fill * (1 - (config.commission_bps + config.stamp_tax_bps) / 1e4)
        - buy_per
    )
    notional = shares * (entry_px + sell_fill) / 2
    net_bps_close = pnl / notional * 1e4 if notional > 0 else 0.0
    lows = [b.low for b in bars if b.time >= entry_time]
    mae = (min(lows) / entry_px - 1.0) * 1e4 if lows else None
    return {
        "exit_px_d0_close": round(sell_fill, 4),
        "net_bps_d0_close": round(net_bps_close, 2),
        "delta_bps": round(net_bps_close - net_bps_actual, 2),
        "mae_bps": round(mae, 1) if mae is not None else None,
    }


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


# ── 快照构建与落库 ──

def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _f(v) -> float | None:
    """pandas 标量 → 可入库 float（NaN/None → None）."""
    return None if pd.isna(v) else float(v)


def _build_snapshot(mkt, conn, code: str, day: str, base: int,
                    trade_fraction: float, gap_th: float,
                    require: dict) -> tuple[dict, pd.DataFrame | None]:
    """单股票日快照。返回 (snap, bars_df)；bars_df None 表示分钟线不可得.

    snap.state ∈ no_bars / no_pre_close / thin_base / traded /
    filtered_<约束|nohist:特征> / near_miss / no_signal。
    """
    snap: dict = {
        "shares": base, "pre_close": None, "n_bars": 0,
        "gap_pct": None, "amplitude": None,
        "trend_up": None, "vol_ratio1": None, "state": "no_bars",
    }
    try:
        bars_df = day_bars(conn, code, day)
    except Exception as exc:
        logger.warning("snapshot {} {} 拉取失败: {}", day, code, exc)
        return snap, None
    if bars_df.empty or len(bars_df) < 10:
        return snap, None
    snap["n_bars"] = len(bars_df)

    prow = mkt.execute(
        "SELECT close FROM daily_price WHERE ts_code=? AND trade_date<? "
        "AND close>0 ORDER BY trade_date DESC LIMIT 1",
        (code, day),
    ).fetchone()
    if prow is None or not prow[0]:
        snap["state"] = "no_pre_close"
        return snap, None
    pre_close = float(prow[0])
    snap["pre_close"] = pre_close

    feat = day_features(conn, mkt, code, day,
                        float(bars_df.iloc[0]["open"]),
                        float(bars_df.iloc[0]["volume"]))
    snap["trend_up"] = feat.get("trend_up")
    snap["vol_ratio1"] = feat.get("vol_ratio1")

    open0 = float(bars_df.iloc[0]["open"])
    snap["gap_pct"] = round(open0 / pre_close - 1.0, 6)
    snap["amplitude"] = round(
        (float(bars_df["high"].max()) - float(bars_df["low"].min())) / pre_close,
        6,
    )
    trade_shares = int(base * trade_fraction / 100) * 100
    snap["state"] = classify_state(
        open0, pre_close, feat, require, trade_shares >= 100,
        gap_th=gap_th,
    )
    return snap, bars_df


def _write_universe(conn, day: str, code: str, snap: dict) -> None:
    tu = snap["trend_up"]
    vr = snap["vol_ratio1"]
    conn.execute(
        "INSERT OR REPLACE INTO intraday_shadow_universe "
        "(trade_date, ts_code, shares, pre_close, n_bars, gap_pct, amplitude, "
        "trend_up, vol_ratio1, state, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (day, code, snap["shares"], snap["pre_close"], snap["n_bars"],
         snap["gap_pct"], snap["amplitude"],
         None if tu is None else int(bool(tu)),
         None if vr is None else round(float(vr), 3),
         snap["state"], _now()),
    )


def _write_exit_alt(conn, day: str, code: str, strategy: str,
                    entry_time: str, entry_px: float, exit_time: str,
                    net_bps_actual: float, shares: int,
                    bars: list[Bar], daily_close: float) -> None:
    alt = exit_alt_metrics(
        entry_px, shares, net_bps_actual, bars, entry_time, daily_close,
        IntradayConfig(),
    )
    conn.execute(
        "INSERT OR REPLACE INTO intraday_shadow_exit_alt "
        "(trade_date, ts_code, strategy, entry_time, entry_px, exit_time_actual, "
        "net_bps_actual, exit_px_d0_close, net_bps_d0_close, delta_bps, mae_bps, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (day, code, strategy, entry_time, entry_px, exit_time,
         net_bps_actual, alt["exit_px_d0_close"], alt["net_bps_d0_close"],
         alt["delta_bps"], alt["mae_bps"], _now()),
    )


_MKT_COLS = ("limit_up_count", "lianban_count", "max_boards", "broken_rate",
             "promo_12", "premium", "red_rate", "up_down_ratio",
             "new_high_ratio")


def write_mkt_env(conn, mkt, days: list[str]) -> int:
    """写每日市场环境（复用 limitup.sentiment 三轴 regime，含 110 日回看）."""
    if not days:
        return 0
    from davis_analyzer.limitup import sentiment

    start = (datetime.strptime(min(days), "%Y%m%d")
             - timedelta(days=110)).strftime("%Y%m%d")
    frame = sentiment.build_market_regime(mkt, start, max(days))
    if frame.empty:
        return 0
    lut = {
        str(d).replace("-", ""): row
        for d, row in frame.set_index("trade_date").iterrows()
    }
    n = 0
    for day in days:
        row = lut.get(day)
        if row is None:
            continue
        vals = [_f(row.get(c)) for c in _MKT_COLS]
        bull = row.get("index_ma_bull")
        label = row.get("regime_label")
        conn.execute(
            "INSERT OR REPLACE INTO intraday_shadow_mkt "
            "(trade_date, limit_up_count, lianban_count, max_boards, "
            "broken_rate, promo_12, premium, red_rate, up_down_ratio, "
            "new_high_ratio, index_ma_bull, regime_label, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (day, *vals,
             None if pd.isna(bull) else int(bool(bull)),
             None if pd.isna(label) else str(label), _now()),
        )
        n += 1
    return n


# ── 主流程 ──

def run_shadow(trade_date: str, db_path: str | None = None,
               trade_fraction: float = 0.30, persist: bool = True) -> dict:
    """回放一个交易日并写影子台账（persist=False 只看不写）。返回运行摘要."""
    from davis_analyzer.intraday.strategies import GapDownSmart
    from davis_analyzer.limitup.db import connect as market_connect

    conn = db.connect(db_path)
    ensure_shadow_tables(conn)
    mkt = market_connect()
    cfg = IntradayConfig()
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
            base = int(positions[code])
            snap, bars_df = _build_snapshot(
                mkt, conn, code, trade_date, base, trade_fraction,
                strategy.gap_pct, strategy.require,
            )
            if persist:
                _write_universe(conn, trade_date, code, snap)
            if snap["state"] != "traded" or bars_df is None:
                continue
            pre_close = float(snap["pre_close"])
            trade_shares = int(base * trade_fraction / 100) * 100
            ratio = limit_ratio_for(code)
            daily_close = float(today_close.get(code)
                                or bars_df.iloc[-1]["close"])
            ctx = DayCtx(
                code, trade_date, pre_close, daily_close,
                round(pre_close * (1 + ratio) + 1e-9, 2),
                round(pre_close * (1 - ratio) + 1e-9, 2),
                base, trade_shares, -1.0,
                features={"trend_up": snap["trend_up"],
                          "vol_ratio1": snap["vol_ratio1"]},
            )
            bars = [
                Bar(r.trade_time, float(r.open), float(r.high),
                    float(r.low), float(r.close))
                for r in bars_df.itertuples(index=False)
            ]
            strategy.reset()
            res = simulate_day(strategy, ctx, bars, cfg)
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
                 res.pnl, res.net_bps, _now()),
            )
            _write_exit_alt(
                conn, trade_date, code, res.strategy,
                res.entry_time, res.avg_buy, res.exit_time, res.net_bps,
                max(res.shares_bought, res.shares_sold), bars, daily_close,
            )
        summary["n_trades"] = len(summary["trades"])
        if not persist:
            return summary
        conn.execute(
            "INSERT OR REPLACE INTO intraday_shadow_run "
            "(trade_date, status, n_universe, n_trades, note, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (trade_date, summary["status"], summary["n_universe"],
             summary["n_trades"], summary["note"], _now()),
        )
        write_mkt_env(conn, mkt, [trade_date])
        conn.commit()
    finally:
        bs_logout()
        mkt.close()
        conn.close()
    return summary


def rebuild_snapshots(start: str, end: str,
                      db_path: str | None = None) -> dict:
    """为历史影子日回填快照/市场环境/退出对照（不动 trade/run 台账）.

    局限：底仓股数为当前快照（历史逐日底仓不可重建）；价格与特征口径
    与当日回放一致。已有成交按台账的 entry_px 复算 d0_close 反事实。
    """
    from davis_analyzer.limitup.db import connect as market_connect

    conn = db.connect(db_path)
    ensure_shadow_tables(conn)
    mkt = market_connect()
    stats = {"days": 0, "universe_rows": 0, "exit_alt_rows": 0, "mkt_rows": 0}
    try:
        days = [r[0] for r in mkt.execute(
            "SELECT DISTINCT trade_date FROM daily_price "
            "WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
            (start, end),
        ).fetchall()]
        positions = base_positions()
        universe = sorted(positions)
        require = SMART_CONFIG["require"]
        gap_th = SMART_CONFIG["gap_pct"]
        for day in days:
            today_close = dict(mkt.execute(
                "SELECT ts_code, close FROM daily_price "
                "WHERE trade_date=? AND close>0", (day,),
            ).fetchall())
            trades = {
                r[0]: r for r in conn.execute(
                    "SELECT ts_code, strategy, entry_time, entry_px, shares, "
                    "net_bps, exit_time FROM intraday_shadow_trade "
                    "WHERE trade_date=?",
                    (day,),
                ).fetchall()
            }
            for code in universe:
                base = int(positions[code])
                snap, bars_df = _build_snapshot(
                    mkt, conn, code, day, base, 0.30, gap_th, require,
                )
                _write_universe(conn, day, code, snap)
                stats["universe_rows"] += 1
                t = trades.get(code)
                if t is None or bars_df is None:
                    continue
                bars = [
                    Bar(r.trade_time, float(r.open), float(r.high),
                        float(r.low), float(r.close))
                    for r in bars_df.itertuples(index=False)
                ]
                _write_exit_alt(
                    conn, day, code, t[1], t[2], float(t[3]), t[6],
                    float(t[5]), int(t[4]),
                    bars, float(today_close.get(code) or t[3]),
                )
                stats["exit_alt_rows"] += 1
            stats["days"] += 1
        stats["mkt_rows"] = write_mkt_env(conn, mkt, days)
        conn.commit()
    finally:
        bs_logout()
        mkt.close()
        conn.close()
    return stats


# ── 报表 ──

def enrich_report(db_path: str | None = None) -> str:
    """快照/对照/环境累计报表（数据增强进度）."""
    conn = db.connect(db_path)
    try:
        ensure_shadow_tables(conn)
        uni = pd.read_sql_query(
            "SELECT state, COUNT(*) AS n FROM intraday_shadow_universe "
            "GROUP BY state ORDER BY n DESC", conn)
        alt = pd.read_sql_query(
            "SELECT COUNT(*) AS n, AVG(delta_bps) AS davg, AVG(mae_bps) AS mavg "
            "FROM intraday_shadow_exit_alt", conn)
        mkt = pd.read_sql_query(
            "SELECT COUNT(*) AS n FROM intraday_shadow_mkt", conn)
    finally:
        conn.close()
    lines = []
    if uni.empty:
        lines.append("快照为空——shadow 尚未积累")
    else:
        dist = ", ".join(f"{r.state}×{r.n}" for r in uni.itertuples(index=False))
        lines.append(f"宇宙快照: {int(uni['n'].sum())} 行（{dist}）")
    if not alt.empty and alt["n"].iloc[0]:
        d = alt["davg"].iloc[0]
        m = alt["mavg"].iloc[0]
        lines.append(
            f"退出对照: {int(alt['n'].iloc[0])} 笔 | "
            f"Δ(d0_close−14:00) 均值 {d:+.0f}bps | 持仓 MAE 均值 {m:+.0f}bps"
        )
    if not mkt.empty and mkt["n"].iloc[0]:
        lines.append(f"市场环境: {int(mkt['n'].iloc[0])} 天")
    return "\n".join(lines) or "快照为空——shadow 尚未积累"


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
