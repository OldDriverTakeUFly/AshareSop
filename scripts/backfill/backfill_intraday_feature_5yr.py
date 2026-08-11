"""Backfill intraday_feature (gap/amplitude/close_position/shadows/body_ratio) for 2021-2024.

Pure computation from daily_price OHLC — NO Tushare API call needed.
These features are required for the amplitude filter (max_intraday_amplitude)
and gap factor, which are breakthrough Sharpe contributors.

Usage::

    cd /home/leo/Projects/CodeAgentDashboard
    PYTHONPATH=. .venv/bin/python -m davis_analyzer.scripts.backfill_intraday_feature_5yr

    # or with rtk:
    rtk python -m davis_analyzer.scripts.backfill_intraday_feature_5yr
"""

from __future__ import annotations

import sys
import time
from datetime import datetime

from stockhot.data_layer.market_db import get_connection


def compute_features(row: tuple) -> tuple:
    """Compute intraday features from a single day's OHLC.

    row schema: (ts_code, trade_date, open, high, low, close, pre_close)
    returns: (ts_code, trade_date, gap, amplitude, close_position,
              upper_shadow, lower_shadow, body_ratio)
    """
    ts_code, trade_date, o, h, l, c, pre_c = row

    # Guard against bad data
    if not all(v is not None and v > 0 for v in (o, h, l, c)):
        return None
    if pre_c is None or pre_c <= 0:
        return None
    if h < l:
        return None

    # gap: 今天开盘相对昨收的跳空 (单位: 百分比的小数表示, 如 0.01 = 1%)
    gap = (o - pre_c) / pre_c

    # amplitude: (high - low) / pre_close
    amplitude = (h - l) / pre_c

    # body: 开盘到收盘的实体
    body = abs(c - o)
    body_range = h - l
    body_ratio = body / body_range if body_range > 0 else 0.0

    # close_position: 收盘在当日区间的位置 (0=最低, 1=最高)
    close_position = (c - l) / body_range if body_range > 0 else 0.5

    # 上下影线 (相对前收)
    upper_shadow = (h - max(o, c)) / pre_c
    lower_shadow = (min(o, c) - l) / pre_c

    return (ts_code, trade_date, gap, amplitude, close_position,
            upper_shadow, lower_shadow, body_ratio)


def backfill_year(year: int) -> int:
    """Backfill intraday_feature for a given year from daily_price."""
    start = f"{year}0101"
    end = f"{year}1231"

    with get_connection() as conn:
        # Check what already exists
        existing = conn.execute(
            "SELECT COUNT(*) FROM intraday_feature WHERE trade_date >= ? AND trade_date <= ?",
            (start, end),
        ).fetchone()[0]
        if existing > 0:
            print(f"  [{year}] 已有 {existing:,} 条, 跳过")
            return existing

        # Pull all daily_price rows
        rows = conn.execute(
            "SELECT ts_code, trade_date, open, high, low, close, pre_close "
            "FROM daily_price WHERE trade_date >= ? AND trade_date <= ? "
            "AND vol > 0 AND open > 0 AND high > 0 AND low > 0 AND close > 0",
            (start, end),
        ).fetchall()

        print(f"  [{year}] 从 daily_price 计算 {len(rows):,} 条...")

        # Compute features
        records = []
        skipped = 0
        for row in rows:
            feat = compute_features(row)
            if feat is None:
                skipped += 1
                continue
            # Sanity: amplitude should be in reasonable range (0, 0.5)
            if feat[3] <= 0 or feat[3] > 0.5:
                skipped += 1
                continue
            records.append(feat)

        # Batch insert
        if records:
            conn.executemany(
                "INSERT OR REPLACE INTO intraday_feature "
                "(ts_code, trade_date, gap, amplitude, close_position, "
                "upper_shadow, lower_shadow, body_ratio) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                records,
            )
            conn.commit()

        print(f"  [{year}] ✓ 写入 {len(records):,} 条 (跳过 {skipped} 条异常)")
        return len(records)


def main():
    print("=" * 60)
    print("intraday_feature 5 年回填 (2021-2024)")
    print("纯 daily_price OHLC 计算, 无需 Tushare API")
    print("=" * 60)

    t0 = time.time()
    total = 0
    for year in [2021, 2022, 2023, 2024]:
        n = backfill_year(year)
        total += n

    elapsed = time.time() - t0
    print()
    print(f"✓ 完成: 共写入 {total:,} 条, 耗时 {elapsed:.1f}s")

    # Final verification
    with get_connection() as conn:
        print()
        print("=== 最终覆盖验证 ===")
        for y in [2021, 2022, 2023, 2024, 2025, 2026]:
            row = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT trade_date) "
                "FROM intraday_feature WHERE trade_date >= ? AND trade_date <= ?",
                (f"{y}0101", f"{y}1231"),
            ).fetchone()
            print(f"  {y}: {row[0]:>10,} rows, {row[1]:>4} dates")


if __name__ == "__main__":
    main()
