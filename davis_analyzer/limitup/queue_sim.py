"""打板排队模拟（盘后分钟线回放）：实测成交率与逆向选择的平行数据管道.

语义（分钟线下的干净近似）：
- 上板 = 分钟内到达过涨停价（high >= 涨停价）；挂单发生在上板分钟之后
- 开板 = 后续分钟 low < 涨停价 → 挂在涨停价的买单必然成交（买一被吃穿价格才会离开涨停价）
- 收盘未开板 → 排队未成交（一字/硬板）
- 收益 = 次日开盘 / 滚停价 − 1（成交记录滞后一日回填）

注意：Tushare stk_mins 限频 1 次/分钟，fetch_minutes 自带 ≥61s pacing。
产出表：market_data.db.limitup_queue_sim（PK trade_date+ts_code）。
不打乱双臂模拟盘口径——平行积累校准数据（§3.4），攒 ≥30 笔成交后再评估
是否以实测成交率替换概率模型。
"""

from __future__ import annotations

import sqlite3
import time

import pandas as pd
from loguru import logger

from davis_analyzer.limitup.events import limit_ratio_for

_TOL = 1e-9
_MINS_CALL_GAP = 61.0  # stk_mins 限频 1 次/分钟
_last_mins_call = 0.0


def fetch_minutes(client: object, ts_code: str, day: str) -> pd.DataFrame:
    """Fetch 1-minute bars for one stock-day (paced + patient rate-limit retry)."""
    global _last_mins_call

    def _paced_call() -> pd.DataFrame:
        global _last_mins_call
        wait = _MINS_CALL_GAP - (time.monotonic() - _last_mins_call)
        if wait > 0:
            logger.info("stk_mins pacing: sleep {:.0f}s ({})", wait, ts_code)
            time.sleep(wait)
        _last_mins_call = time.monotonic()
        return client._call(
            "stk_mins", client._pro.stk_mins,
            {"ts_code": ts_code, "freq": "1min",
             "start_date": f"{day[:4]}-{day[4:6]}-{day[6:8]} 09:00:00",
             "end_date": f"{day[:4]}-{day[4:6]}-{day[6:8]} 15:10:00"},
        )

    try:
        df = _paced_call()
    except Exception as exc:  # tushare SDK 限频直接抛出（_call 快速重试已烧尽）
        logger.warning("stk_mins {} 调用失败（{}），65s 后重试一次", ts_code, exc)
        df = None
    if df is None and _MINS_CALL_GAP > 0:
        time.sleep(65.0)
        try:
            df = _paced_call()
        except Exception:
            logger.error("stk_mins {} 重试仍失败，跳过该标的", ts_code)
            return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    col_time = "trade_time" if "trade_time" in df.columns else "time"
    df = df.sort_values(col_time).reset_index(drop=True)
    df = df.rename(columns={col_time: "time"})
    return df


def simulate_queue(minutes: pd.DataFrame, pre_close: float, ratio: float) -> dict:
    """Pure queue simulation on one stock-day of minute bars."""
    limit_px = round(pre_close * (1 + ratio) + 1e-9, 2)
    if minutes.empty or pre_close <= 0:
        return {"boarded": False, "filled": False, "limit_price": limit_px}
    high, low = minutes["high"].astype(float), minutes["low"].astype(float)
    touch = high >= limit_px - _TOL
    if not touch.any():
        return {"boarded": False, "filled": False, "limit_price": limit_px}
    i0 = touch.idxmax()  # 首次上板分钟
    after = minutes.loc[i0 + 1:] if i0 + 1 < len(minutes) else minutes.iloc[0:0]
    opened = after[after["low"].astype(float) < limit_px - _TOL]
    result = {
        "boarded": True,
        "first_touch": str(minutes.at[i0, "time"]),
        "filled": bool(not opened.empty),
        "limit_price": limit_px,
    }
    if not opened.empty:
        result["fill_time"] = str(opened.iloc[0]["time"])
    else:
        result["fill_time"] = None
    return result


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS limitup_queue_sim ("
        "trade_date TEXT NOT NULL, ts_code TEXT NOT NULL, name TEXT, "
        "enhanced INTEGER, boarded INTEGER, filled INTEGER, first_touch TEXT, "
        "fill_time TEXT, limit_price REAL, ret_open_1 REAL, "
        "PRIMARY KEY (trade_date, ts_code))"
    )
    conn.commit()


def run_queue_sim(conn: sqlite3.Connection, monitor_day: str, client: object,
                  candidates_fn=None) -> pd.DataFrame:
    """Simulate queueing for the previous trading day's first_board candidates.

    candidates_fn(conn, prev_day) allows tests to inject; production uses
    limitup.candidates.build_candidates.
    """
    from davis_analyzer.limitup.candidates import build_candidates

    ensure_table(conn)
    prev = conn.execute(
        "SELECT MAX(trade_date) FROM daily_price WHERE trade_date<?", (monitor_day,)
    ).fetchone()[0]
    if prev is None:
        return pd.DataFrame()
    cands = (candidates_fn or build_candidates)(conn, prev)
    if cands.empty:
        logger.info("queue_sim: {} 无候选（前一日 {}）", monitor_day, prev)
        return pd.DataFrame()

    rows = []
    px = pd.read_sql_query(
        "SELECT ts_code, open AS d1_open, pre_close FROM daily_price WHERE trade_date=?",
        conn, params=(monitor_day,),
    ).set_index("ts_code")
    nxt = conn.execute(
        "SELECT MIN(trade_date) FROM daily_price WHERE trade_date>?", (monitor_day,)
    ).fetchone()[0]
    nxt_open = {}
    if nxt:
        for r in conn.execute(
            "SELECT ts_code, open FROM daily_price WHERE trade_date=?", (nxt,)
        ).fetchall():
            nxt_open[r[0]] = r[1]

    for _, c in cands.iterrows():
        code = c["ts_code"]
        pre = px.loc[code]["pre_close"] if code in px.index else None
        if pre is None or float(pre) <= 0:
            continue
        mins = fetch_minutes(client, code, monitor_day)
        sim = simulate_queue(mins, float(pre), limit_ratio_for(code))
        ret = None
        if sim["filled"] and code in nxt_open and nxt_open[code]:
            ret = float(nxt_open[code]) / sim["limit_price"] - 1
        rows.append({
            "trade_date": monitor_day, "ts_code": code, "name": c.get("name"),
            "enhanced": bool(c.get("enhanced", False)),
            **{k: sim.get(k) for k in ("boarded", "filled", "first_touch",
                                       "fill_time", "limit_price")},
            "ret_open_1": ret,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    conn.executemany(
        "INSERT OR REPLACE INTO limitup_queue_sim (trade_date, ts_code, name, "
        "enhanced, boarded, filled, first_touch, fill_time, limit_price, ret_open_1) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (r["trade_date"], r["ts_code"], r["name"], int(r["enhanced"]),
             int(r["boarded"]), int(r["filled"]), r["first_touch"], r["fill_time"],
             r["limit_price"], r["ret_open_1"])
            for _, r in df.iterrows()
        ],
    )
    conn.commit()
    _backfill_returns(conn)
    logger.info("queue_sim {}: {} 候选，上板 {}，成交 {}", monitor_day, len(df),
                int(df["boarded"].sum()), int(df["filled"].sum()))
    return df


def _backfill_returns(conn: sqlite3.Connection) -> None:
    """Fill ret_open_1 for earlier filled rows whose next-day data now exists."""
    rows = conn.execute(
        "SELECT trade_date, ts_code, limit_price FROM limitup_queue_sim "
        "WHERE filled=1 AND ret_open_1 IS NULL"
    ).fetchall()
    for d, code, limit_px in rows:
        nxt = conn.execute(
            "SELECT MIN(trade_date) FROM daily_price WHERE trade_date>?", (d,)
        ).fetchone()[0]
        if not nxt:
            continue
        op = conn.execute(
            "SELECT open FROM daily_price WHERE trade_date=? AND ts_code=? AND open>0",
            (nxt, code),
        ).fetchone()
        if op:
            conn.execute(
                "UPDATE limitup_queue_sim SET ret_open_1=? WHERE trade_date=? AND ts_code=?",
                (float(op[0]) / limit_px - 1, d, code),
            )
    conn.commit()


def queue_summary(conn: sqlite3.Connection, day: str) -> str:
    """One-line summary for Feishu daily push (adverse-selection readout)."""
    ensure_table(conn)
    rows = conn.execute(
        "SELECT boarded, filled, ret_open_1 FROM limitup_queue_sim WHERE trade_date=?",
        (day,),
    ).fetchall()
    if not rows:
        return f"排队模拟[{day}]: 无记录"
    n, boarded = len(rows), sum(r[0] for r in rows)
    filled = [r for r in rows if r[1]]
    rets = [r[2] for r in filled if r[2] is not None]
    ret_s = f"{sum(rets) / len(rets):+.2%}" if rets else "待回填"
    unfilled = [r[2] for r in rows if r[0] and not r[1] and r[2] is not None]
    unf_s = f"{sum(unfilled) / len(unfilled):+.2%}" if unfilled else "—"
    return (f"排队模拟[{day}]: 候选{n} 上板{boarded} 成交{len(filled)}"
            f"（成交组次日开盘均值 {ret_s} vs 未成交组 {unf_s}——逆向选择观测）")
