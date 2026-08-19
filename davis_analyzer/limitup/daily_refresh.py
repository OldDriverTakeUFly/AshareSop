"""每日增量刷新：limitup 研究所需的五类数据（幂等、单表失败互不影响）.

数据链路时序（工作日）：
  18:00 run_daily_scan 落 daily_price/daily_basic + limit_pool（无 ext）
  18:30 盘后总结补全市场日线
  19:20 本任务：limit_list_d（含 float_mv ext）/ moneyflow / top_list /
       intraday_feature（本地派生）/ corp_event 解禁与增减持

口径说明：intraday_feature 沿用 compute_intraday_features.py 的 B 口径
（shadow 分母 h-l、body_ratio 带符号），与 market_db 列注释一致。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pandas as pd
from loguru import logger

from davis_analyzer.limitup import db


def _trading_dates(conn: sqlite3.Connection, lookback_days: int) -> list[str]:
    """Recent trading dates: last N rows of the daily_price calendar."""
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM daily_price ORDER BY trade_date DESC LIMIT ?",
        (lookback_days,),
    ).fetchall()
    return sorted(r[0] for r in rows)


def missing_dates(
    conn: sqlite3.Connection, table: str, dates: list[str]
) -> list[str]:
    """Dates present in daily_price calendar but absent from `table`."""
    if not dates:
        return []
    have = {
        r[0]
        for r in conn.execute(
            f"SELECT DISTINCT trade_date FROM {table} WHERE trade_date>=?",
            (dates[0],),
        )
    }
    return [d for d in dates if d not in have]


# ── limit_list_d（复用 backfill，ext 感知的幂等）──

def refresh_limit_pool(conn: sqlite3.Connection, dates: list[str], fetch_fn) -> int:
    """Re-fetch days whose limit_pool rows lack float_mv in limit_pool_ext."""
    from davis_analyzer.limitup import backfill

    backfill.ensure_ext_table(conn)
    written = 0
    for d in dates:
        if backfill.day_has_ext(conn, db.to_dash_date(d)):
            continue
        for limit_type, pool_kind in backfill.POOL_KIND_BY_TYPE.items():
            df = fetch_fn(d, limit_type)
            if df is None or df.empty:
                continue
            written += backfill.write_pool_day(conn, d, df, limit_type, pool_kind)
    return written


# ── moneyflow / top_list（Tushare 单日全市场）──

_MF_INSERT = (
    "INSERT OR REPLACE INTO moneyflow (trade_date, ts_code, buy_sm_amount, "
    "sell_sm_amount, buy_md_amount, sell_md_amount, buy_lg_amount, sell_lg_amount, "
    "buy_elg_amount, sell_elg_amount, net_mf_amount, fetched_at) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
)

_TL_INSERT = (
    "INSERT OR REPLACE INTO top_list (trade_date, ts_code, name, close, pct_change, "
    "turnover_rate, amount, l_sell, l_buy, l_amount, net_amount, net_rate, "
    "amount_rate, float_values, reason, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _num(v: object) -> object:
    try:
        f = float(v)  # type: ignore[arg-type]
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def refresh_moneyflow(conn: sqlite3.Connection, dates: list[str], fetch_fn) -> int:
    now = datetime.now().timestamp()
    total = 0
    for d in dates:
        df = fetch_fn(d)
        if df is None or df.empty:
            logger.warning("moneyflow 无数据 {}", d)
            continue
        conn.executemany(
            _MF_INSERT,
            [
                (
                    d, r.get("ts_code"), _num(r.get("buy_sm_amount")),
                    _num(r.get("sell_sm_amount")), _num(r.get("buy_md_amount")),
                    _num(r.get("sell_md_amount")), _num(r.get("buy_lg_amount")),
                    _num(r.get("sell_lg_amount")), _num(r.get("buy_elg_amount")),
                    _num(r.get("sell_elg_amount")), _num(r.get("net_mf_amount")), now,
                )
                for _, r in df.iterrows()
            ],
        )
        total += len(df)
    conn.commit()
    return total


def refresh_top_list(conn: sqlite3.Connection, dates: list[str], fetch_fn) -> int:
    now = datetime.now().timestamp()
    total = 0
    for d in dates:
        df = fetch_fn(d)
        if df is None or df.empty:
            continue  # 非龙虎榜日空数据是常态
        conn.executemany(
            _TL_INSERT,
            [
                (
                    d, r.get("ts_code"), r.get("name"), _num(r.get("close")),
                    _num(r.get("pct_change")), _num(r.get("turnover_rate")),
                    _num(r.get("amount")), _num(r.get("l_sell")),
                    _num(r.get("l_buy")), _num(r.get("l_amount")),
                    _num(r.get("net_amount")), _num(r.get("net_rate")),
                    _num(r.get("amount_rate")), _num(r.get("float_values")),
                    r.get("reason"), now,
                )
                for _, r in df.iterrows()
            ],
        )
        total += len(df)
    conn.commit()
    return total


# ── intraday_feature（本地派生，B 口径）──

def compute_intraday_features(prices: pd.DataFrame) -> pd.DataFrame:
    """gap/振幅/收盘位置/上下影线/实体比，分母 h-l（B 口径，与历史 2025+ 段一致）."""
    p = prices.dropna(subset=["open", "high", "low", "close", "pre_close"]).copy()
    rng = p["high"] - p["low"]
    p = p[rng > 0].copy()
    rng = p["high"] - p["low"]
    pc = p["pre_close"]
    p["gap"] = p["open"] / pc - 1
    p["amplitude"] = rng / pc
    p["close_position"] = ((p["close"] - p["low"]) / rng).clip(0, 1)
    p["upper_shadow"] = ((p["high"] - p[["open", "close"]].max(axis=1)) / rng).clip(0, 1)
    p["lower_shadow"] = ((p[["open", "close"]].min(axis=1) - p["low"]) / rng).clip(0, 1)
    p["body_ratio"] = (p["close"] - p["open"]) / rng
    return p


def refresh_intraday_features(conn: sqlite3.Connection, dates: list[str]) -> int:
    if not dates:
        return 0
    now = datetime.now().timestamp()
    total = 0
    for d in dates:
        prices = pd.read_sql_query(
            "SELECT ts_code, trade_date, open, high, low, close, pre_close "
            "FROM daily_price WHERE trade_date=?",
            conn, params=(d,),
        )
        feats = compute_intraday_features(prices)
        if feats.empty:
            continue
        conn.executemany(
            "INSERT OR REPLACE INTO intraday_feature (ts_code, trade_date, gap, "
            "amplitude, close_position, upper_shadow, lower_shadow, body_ratio, "
            "fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    r["ts_code"], r["trade_date"], _num(r["gap"]), _num(r["amplitude"]),
                    _num(r["close_position"]), _num(r["upper_shadow"]),
                    _num(r["lower_shadow"]), _num(r["body_ratio"]), now,
                )
                for _, r in feats.iterrows()
            ],
        )
        total += len(feats)
    conn.commit()
    return total


# ── corp_event：解禁 + 增减持（ann_date 滚动窗）──

_CE_INSERT = (
    "INSERT OR IGNORE INTO corp_event (ts_code, ann_date, event_type, direction, "
    "magnitude, details_json, source, fetched_at) VALUES (?,?,?,?,?,?,?,?)"
)


def refresh_corp_events(
    conn: sqlite3.Connection, start: str, end: str,
    fetch_float_fn, fetch_trade_fn,
) -> int:
    now = datetime.now().timestamp()
    total = 0
    for df, event_type, fetcher in (
        (fetch_float_fn(start, end), "share_float", fetch_float_fn),
        (fetch_trade_fn(start, end), "holder_trade", fetch_trade_fn),
    ):
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            ann = str(r.get("ann_date") or r.get("trade_date") or "")[:8]
            if not ann.isdigit():
                continue
            if event_type == "share_float":
                direction, magnitude = "negative", _num(r.get("float_ratio"))
                details = {"end_date": str(r.get("float_date") or r.get("end_date") or "")}
            else:
                in_de = str(r.get("in_de") or "").upper()
                direction = "increase" if "I" in in_de else "decrease"
                magnitude = _num(r.get("change_ratio"))
                details = {"holder_type": str(r.get("holder_type") or "")}
            conn.execute(
                _CE_INSERT,
                (
                    r.get("ts_code"), ann, event_type, direction, magnitude,
                    json.dumps(details, ensure_ascii=False, sort_keys=True),
                    "limitup_daily", now,
                ),
            )
            total += 1
    conn.commit()
    return total


# ── daily_price 质量自愈（多写入方混写防线）──
# 历史教训（2026-08）：并行写入方曾写 close-only 行（high/low NULL，~3k 行/日）
# 与占位 adj_factor=1.0（整日覆盖），分别击穿形态 rolling 与除权判定；
# 另有当日全市场日线迟至 19:30 仍未落库（上游时序不稳），致候选/模拟盘
# 连续空转（2026-0818/0819）——ensure_daily_price_full 兜底当日完整性。

_NULL_OHLC_THRESHOLD = 100    # 单日 NULL high 超此数视为写入方污染（正常<10）
_PLACEHOLDER_ADJ_THRESHOLD = 1000  # 单日 adj=1.0 超此数视为占位（正常~116 只）
_FULL_DAY_THRESHOLD = 1000    # 当日行数低于此数视为未完成落库（正常 ~5500）


def ensure_daily_price_full(conn: sqlite3.Connection, day: str, client: object) -> int:
    """Ensure the day's full-market daily_price exists (fallback for slow writers).

    If the day has fewer than _FULL_DAY_THRESHOLD rows, fetch the full market
    from Tushare `daily` + `adj_factor` and upsert (INSERT OR REPLACE).
    Returns rows upserted (0 if already complete / fetch failed).
    """
    n = conn.execute(
        "SELECT COUNT(*) FROM daily_price WHERE trade_date=?", (day,)
    ).fetchone()[0]
    if n >= _FULL_DAY_THRESHOLD:
        return 0
    logger.info("daily_price {} 仅 {} 行（<{}），全市场兜底拉取", day, n,
                _FULL_DAY_THRESHOLD)
    df = client._call("daily", client._pro.daily, {"trade_date": day})
    if df is None or df.empty:
        logger.warning("daily 兜底拉取失败 {}", day)
        return 0
    adj = client._call("adj_factor", client._pro.adj_factor, {"trade_date": day})
    adj_map: dict[str, float] = {}
    if adj is not None and not adj.empty:
        adj_map = dict(zip(adj["ts_code"], adj["adj_factor"].astype(float)))
    now = datetime.now().timestamp()
    upserted = 0
    for _, r in df.iterrows():
        code = r.get("ts_code")
        if not code:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO daily_price (ts_code, trade_date, open, high, "
            "low, close, pre_close, pct_chg, vol, amount, adj_factor, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, day, _num(r.get("open")), _num(r.get("high")),
             _num(r.get("low")), _num(r.get("close")), _num(r.get("pre_close")),
             _num(r.get("pct_chg")), _num(r.get("vol")), _num(r.get("amount")),
             adj_map.get(code), now),
        )
        upserted += 1
    conn.commit()
    logger.info("daily_price {} 兜底完成: {} 行 upsert（此前 {}）", day, upserted, n)
    return upserted


def repair_daily_price_gaps(conn: sqlite3.Connection, dates: list[str], client: object) -> dict:
    """Repair close-only rows (NULL high/low) and placeholder adj_factor=1.0 rows.

    Only touches rows whose high IS NULL / (adj IS NULL or 疑似占位)；合法值不动。
    """
    fixed_ohlc = fixed_adj = 0
    for d in dates:
        n_null = conn.execute(
            "SELECT COUNT(*) FROM daily_price WHERE trade_date=? AND high IS NULL",
            (d,),
        ).fetchone()[0]
        n_adj1 = conn.execute(
            "SELECT COUNT(*) FROM daily_price WHERE trade_date=? AND adj_factor=1.0",
            (d,),
        ).fetchone()[0]
        if n_null < _NULL_OHLC_THRESHOLD and n_adj1 < _PLACEHOLDER_ADJ_THRESHOLD:
            continue
        df = client._call("daily", client._pro.daily, {"trade_date": d})
        if df is None or df.empty:
            logger.warning("daily 自愈拉取失败 {}", d)
            continue
        need_ohlc = n_null >= _NULL_OHLC_THRESHOLD
        need_adj = n_adj1 >= _PLACEHOLDER_ADJ_THRESHOLD
        for _, r in df.iterrows():
            if need_ohlc:
                cur = conn.execute(
                    "SELECT high FROM daily_price WHERE ts_code=? AND trade_date=?",
                    (r["ts_code"], d),
                ).fetchone()
                if cur and cur[0] is None and r.get("high") is not None:
                    conn.execute(
                        "UPDATE daily_price SET high=?, low=?, vol=COALESCE(vol,?), "
                        "amount=COALESCE(amount,?) WHERE ts_code=? AND trade_date=?",
                        (float(r["high"]), float(r["low"]), r.get("vol"),
                         r.get("amount"), r["ts_code"], d),
                    )
                    fixed_ohlc += 1
            if need_adj and r.get("adj_factor") is None:
                continue  # daily 接口无 adj；占位修复由 adj_factor 接口做
        conn.commit()
        if need_adj:
            adf = client._call("adj_factor", client._pro.adj_factor, {"trade_date": d})
            if adf is not None and not adf.empty:
                for _, r in adf.iterrows():
                    cur = conn.execute(
                        "SELECT adj_factor FROM daily_price WHERE ts_code=? AND trade_date=?",
                        (r["ts_code"], d),
                    ).fetchone()
                    if cur and cur[0] == 1.0 and abs(float(r["adj_factor"]) - 1.0) > 1e-9:
                        conn.execute(
                            "UPDATE daily_price SET adj_factor=? WHERE ts_code=? "
                            "AND trade_date=?",
                            (float(r["adj_factor"]), r["ts_code"], d),
                        )
                        fixed_adj += 1
                conn.commit()
    if fixed_ohlc or fixed_adj:
        logger.info("daily_price 自愈: high/low {} 行, adj {} 行", fixed_ohlc, fixed_adj)
    return {"ohlc": fixed_ohlc, "adj": fixed_adj}


# ── 总入口 ──

def run_daily_refresh(conn: sqlite3.Connection, lookback_days: int = 7) -> dict:
    """Refresh all limitup-relevant tables for the recent window (fault-isolated)."""
    from davis_analyzer.tushare_client import TushareClient

    dates = _trading_dates(conn, lookback_days)
    if not dates:
        return {"error": "daily_price 日历为空"}
    client = TushareClient()
    summary: dict[str, object] = {"dates": dates}

    def _mf(d: str) -> pd.DataFrame:
        return client._call("moneyflow", client._pro.moneyflow, {"trade_date": d})

    def _tl(d: str) -> pd.DataFrame:
        return client._call("top_list", client._pro.top_list, {"trade_date": d})

    steps: list[tuple[str, object]] = [
        ("daily_price_full", lambda: ensure_daily_price_full(conn, dates[-1], client)),
        ("daily_price_repair", lambda: repair_daily_price_gaps(conn, dates, client)),
        ("limit_pool", lambda: refresh_limit_pool(
            conn, dates, lambda d, t: client._call(
                "limit_list_d", client._pro.limit_list_d,
                {"trade_date": d, "limit_type": t},
            ),
        )),
        ("moneyflow", lambda: refresh_moneyflow(
            conn, missing_dates(conn, "moneyflow", dates), _mf)),
        ("top_list", lambda: refresh_top_list(
            conn, missing_dates(conn, "top_list", dates), _tl)),
        ("intraday_feature", lambda: refresh_intraday_features(
            conn, missing_dates(conn, "intraday_feature", dates))),
        ("corp_event", lambda: refresh_corp_events(
            conn,
            _shift_day(dates[0], -3), dates[-1],
            lambda s, e: client._call(
                "share_float", client._pro.share_float,
                {"start_date": s, "end_date": e}),
            lambda s, e: client._call(
                "stk_holdertrade", client._pro.stk_holdertrade,
                {"start_date": s, "end_date": e}),
        )),
    ]
    for name, step in steps:
        try:
            summary[name] = step()
        except Exception as exc:  # 单表失败不影响其他表（daily-scan 同款纪律）
            logger.exception("{} 刷新失败", name)
            summary[name] = f"FAILED: {exc}"
    logger.info("daily refresh: {}", summary)
    return summary


def _shift_day(ymd: str, days: int) -> str:
    dt = pd.to_datetime(ymd, format="%Y%m%d") + pd.Timedelta(days=days)
    return dt.strftime("%Y%m%d")
