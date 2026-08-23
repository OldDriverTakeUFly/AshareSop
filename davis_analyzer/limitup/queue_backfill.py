"""历史分钟线排队模拟回补（每小时 1 只，按封单比降序遍历全历史首板）.

stk_mins 限频 1 次/小时（5000 积分实测），回补策略：
- 每次运行取「尚未模拟且封单比最高」的首板事件 1 只
- 按日期降序（最近优先，对当前校准最有用）
- 全历史 1338 天 × 1 只/天 = 1338 小时 ≈ 56 天跑完
- cron: 0 * * * * （每小时整点）
"""

from __future__ import annotations

import sqlite3
import sys

from loguru import logger

from davis_analyzer.limitup import db
from davis_analyzer.limitup.queue_sim import (
    ensure_table,
    fetch_minutes,
    simulate_queue,
)
from davis_analyzer.limitup.events import limit_ratio_for


def next_backfill_target(conn: sqlite3.Connection) -> tuple[str, str] | None:
    """Find the next un-simulated first board (highest seal_ratio, recent first)."""
    ensure_table(conn)
    row = conn.execute(
        """
        SELECT lp.trade_date, lp.ts_code, lp.seal_amount
        FROM limit_pool lp
        LEFT JOIN limitup_queue_sim qs
          ON qs.trade_date = lp.trade_date
          AND qs.ts_code = REPLACE(REPLACE(lp.ts_code, '.SZ', ''), '.SH', '')
             || CASE WHEN lp.ts_code LIKE '6%' THEN '.SH' ELSE '.SZ' END
        WHERE lp.pool_kind = 'limit_up'
          AND lp.consecutive_boards = 1
          AND lp.seal_amount > 0
          AND lp.ts_code NOT LIKE '%.%'  -- 只取无后缀行（避免重复）
          AND qs.ts_code IS NULL          -- 尚未模拟
        ORDER BY lp.trade_date DESC,       -- 最近优先
                 lp.seal_amount / MAX(lp.seal_amount, 1) DESC  -- 封单绝对额降序
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return row[0], row[1]


def run_backfill_step(conn: sqlite3.Connection, client: object) -> dict:
    """One backfill step: pick next target, fetch minute bars, simulate, store."""
    target = next_backfill_target(conn)
    if target is None:
        logger.info("backfill: 全部首板已覆盖")
        return {"status": "done"}

    trade_date_raw, code_raw = target
    day = trade_date_raw.replace("-", "")  # limit_pool 用 YYYY-MM-DD
    code = db.to_suffixed_code(code_raw)

    # 取前一日（事件日 = 涨停日；模拟日 = 次日）
    prev = conn.execute(
        "SELECT MAX(trade_date) FROM daily_price WHERE trade_date < ?",
        (day,),
    ).fetchone()[0]
    if prev is None:
        return {"status": "skip", "reason": f"no prev day for {day}"}

    # 事件日的 pre_close（作为模拟日的涨停基准参考）
    # 注意：queue_sim 模拟的是「次日是否上板」，需要次日的 pre_close
    pre_row = conn.execute(
        "SELECT pre_close FROM daily_price WHERE trade_date=? AND ts_code=?",
        (day, code),
    ).fetchone()
    if pre_row is None or pre_row[0] is None or pre_row[0] <= 0:
        return {"status": "skip", "reason": f"no pre_close for {code} {day}"}

    # 取模拟日（= 事件日的次日）的价格
    px_row = conn.execute(
        "SELECT pre_close FROM daily_price WHERE trade_date=? AND ts_code=?",
        (day, code),
    ).fetchone()
    if px_row is None:
        return {"status": "skip", "reason": f"no price for {code} {day}"}

    logger.info("backfill: {} {} (事件日 {})", code, day, prev)
    mins = fetch_minutes(client, code, day)
    if mins.empty:
        return {"status": "skip", "reason": f"stk_mins empty for {code} {day}"}

    sim = simulate_queue(mins, float(pre_row[0]), limit_ratio_for(code))

    # 次日开盘价（计算收益）
    nxt = conn.execute(
        "SELECT MIN(trade_date) FROM daily_price WHERE trade_date > ?", (day,)
    ).fetchone()[0]
    ret = None
    if nxt and sim.get("filled"):
        op = conn.execute(
            "SELECT open FROM daily_price WHERE trade_date=? AND ts_code=? AND open > 0",
            (nxt, code),
        ).fetchone()
        if op:
            ret = float(op[0]) / sim["limit_price"] - 1

    name_row = conn.execute(
        "SELECT name FROM limit_pool WHERE trade_date=? AND ts_code=? AND pool_kind='limit_up'",
        (trade_date_raw, code_raw),
    ).fetchone()
    name = name_row[0] if name_row else code_raw

    conn.execute(
        "INSERT OR REPLACE INTO limitup_queue_sim "
        "(trade_date, ts_code, name, enhanced, boarded, filled, first_touch, "
        "fill_time, limit_price, ret_open_1, scope) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (day, code, name, 0, int(sim.get("boarded", False)),
         int(sim.get("filled", False)), sim.get("first_touch"),
         sim.get("fill_time"), sim.get("limit_price"), ret, "backfill"),
    )
    conn.commit()
    logger.info("backfill: {} {} → boarded={} filled={} ret={}",
                code, day, sim.get("boarded"), sim.get("filled"),
                f"{ret:+.2%}" if ret is not None else "—")
    return {"status": "ok", "code": code, "day": day, **{k: v for k, v in sim.items() if v is not None}}


def main() -> None:
    from davis_analyzer.tushare_client import TushareClient

    conn = db.connect()
    try:
        client = TushareClient()
        result = run_backfill_step(conn, client)
        print(f"backfill: {result}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
