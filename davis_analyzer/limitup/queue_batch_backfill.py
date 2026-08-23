"""baostock 全历史涨停排队模拟批量回补.

覆盖 limit_pool 中全部涨停事件（含所有连板数），约 82,514 条。
数据源：baostock 5 分钟线（无频次限制，BSD 开源，已安全审计）。
产出：limitup_queue_sim 表 scope='backfill_full'。
预计耗时：快速模式 ~83 分钟 / 保守模式（0.2s 间隔）~4.6 小时。
"""

from __future__ import annotations

import sqlite3
import sys
import time

import pandas as pd
from loguru import logger

from davis_analyzer.limitup import db as limitup_db
from davis_analyzer.limitup.events import limit_ratio_for
from davis_analyzer.limitup.queue_sim import ensure_table, simulate_queue


def _to_baostock_code(ts_code: str) -> str:
    """600572.SH → sh.600572; 000631.SZ → sz.000631."""
    if "." not in ts_code:
        ts_code = limitup_db.to_suffixed_code(ts_code)
    code, suffix = ts_code.split(".")
    return f"{'sh' if suffix == 'SH' else 'sz'}.{code}"


def fetch_baostock_minutes(bs_module, ts_code: str, day: str) -> pd.DataFrame:
    """Fetch 5-minute bars via baostock for one stock-day."""
    bs_code = _to_baostock_code(ts_code)
    rs = bs_module.query_history_k_data_plus(
        bs_code, "time,open,high,low,close,volume",
        start_date=f"{day[:4]}-{day[4:6]}-{day[6:8]}",
        end_date=f"{day[:4]}-{day[4:6]}-{day[6:8]}",
        frequency="5", adjustflag="3",
    )
    if rs.error_code != "0":
        return pd.DataFrame()
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def batch_backfill(conn: sqlite3.Connection, sleep_s: float = 0.1,
                   batch_size: int = 0) -> dict:
    """Backfill all limit-up events. batch_size>0 = limited run (testing)."""
    import baostock as bs

    ensure_table(conn)

    # 取全部涨停事件（去重：同日同股只取一条）
    events = conn.execute(
        """
        SELECT DISTINCT lp.trade_date, lp.ts_code, lp.name, lp.consecutive_boards
        FROM limit_pool lp
        LEFT JOIN limitup_queue_sim qs ON qs.trade_date = lp.trade_date AND qs.ts_code = lp.ts_code
        WHERE lp.pool_kind = 'limit_up'
          AND lp.ts_code NOT LIKE '%.%'
          AND qs.ts_code IS NULL
        ORDER BY lp.trade_date DESC, lp.consecutive_boards DESC
        """
    ).fetchall()

    if not events:
        return {"status": "done", "processed": 0, "total": 0}

    total = len(events)
    if batch_size > 0:
        events = events[:batch_size]
        logger.info("batch_backfill: 限定前 {} 条（共 {} 待处理）", batch_size, total)

    logger.info("batch_backfill: {} 条待处理（全量 {}）", len(events), total)

    # 预加载日历（取次日语义）
    cal = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM daily_price ORDER BY trade_date").fetchall()]
    cal_set = set(cal)
    next_day_map = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}

    # 预加载全部 pre_close 与 next_open
    pre_close_map = {}
    for r in conn.execute(
        "SELECT trade_date, ts_code, pre_close FROM daily_price WHERE pre_close > 0"
    ).fetchall():
        pre_close_map[(r[1], r[0])] = r[2]

    next_open_cache: dict[str, dict[str, float]] = {}  # day → {code: open}
    def get_next_open(code: str, day: str) -> float | None:
        nxt = next_day_map.get(day)
        if not nxt:
            return None
        if nxt not in next_open_cache:
            rows = conn.execute(
                "SELECT ts_code, open FROM daily_price WHERE trade_date=? AND open > 0",
                (nxt,),
            ).fetchall()
            next_open_cache[nxt] = {r[0]: r[1] for r in rows}
        return next_open_cache[nxt].get(code)

    bs.login()
    processed = boarded_n = filled_n = skipped = 0
    t0 = time.time()

    try:
        for trade_date_dash, code_raw, name, boards in events:
            day = trade_date_dash.replace("-", "")
            code = limitup_db.to_suffixed_code(code_raw)

            pre = pre_close_map.get((code, day))
            if pre is None or pre <= 0:
                skipped += 1
                continue

            mins = fetch_baostock_minutes(bs, code, day)
            if mins.empty:
                skipped += 1
                continue

            sim = simulate_queue(mins, float(pre), limit_ratio_for(code))

            ret = None
            if sim.get("filled"):
                nxt_open = get_next_open(code, day)
                if nxt_open:
                    ret = float(nxt_open) / sim["limit_price"] - 1

            conn.execute(
                "INSERT OR REPLACE INTO limitup_queue_sim "
                "(trade_date, ts_code, name, enhanced, boarded, filled, first_touch, "
                "fill_time, limit_price, ret_open_1, scope) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (day, code, name, 0, int(sim.get("boarded", False)),
                 int(sim.get("filled", False)), sim.get("first_touch"),
                 sim.get("fill_time"), sim.get("limit_price"), ret,
                 "backfill_full"),
            )
            processed += 1
            if sim.get("boarded"):
                boarded_n += 1
            if sim.get("filled"):
                filled_n += 1

            if processed % 500 == 0:
                conn.commit()
                elapsed = time.time() - t0
                rate = processed / elapsed
                eta_min = (len(events) - processed) / rate / 60 if rate > 0 else 0
                logger.info(
                    "batch_backfill 进度: {}/{} ({:.0f}%) "
                    "上板={} 成交={} 跳过={} | {:.1f}/s ETA {:.0f}min",
                    processed, len(events), processed / len(events) * 100,
                    boarded_n, filled_n, skipped, rate, eta_min,
                )

            if sleep_s > 0:
                time.sleep(sleep_s)

        conn.commit()
    finally:
        bs.logout()

    elapsed = time.time() - t0
    logger.info(
        "batch_backfill 完成: 处理={} 上板={} 成交={} 跳过={} 耗时={:.0f}s",
        processed, boarded_n, filled_n, skipped, elapsed,
    )
    return {
        "status": "done", "processed": processed, "total": total,
        "boarded": boarded_n, "filled": filled_n, "skipped": skipped,
        "elapsed_s": elapsed,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="baostock 全历史涨停排队模拟回补")
    parser.add_argument("--sleep", type=float, default=0.1, help="每条间隔秒数（默认 0.1）")
    parser.add_argument("--batch", type=int, default=0, help="限定处理条数（0=全量）")
    args = parser.parse_args()

    conn = limitup_db.connect()
    try:
        result = batch_backfill(conn, sleep_s=args.sleep, batch_size=args.batch)
        print(f"\n回补结果: {result}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
