"""一次性数据修复: 重拉 2026-07-16 → 2026-08-21 全市场 adj_factor.

背景(2026-08-22 定位): repository._save_daily_prices 的 trade_date 单键
dict 合并 bug 把单票因子广播到全市场, 污染 daily_price.adj_factor
(相邻日跳变>3x 残留 16568 行)。代码已修复, 本脚本重拉窗口内全部
因子按 (ts_code, trade_date) 复合键 UPDATE 覆盖。

- 幂等可重跑; 只 UPDATE 已存在行, 不插新行
- 修复前快照: daily_price_adj_backup_20260822 (同库, 仅 adj_factor 列)
- 验证: 重拉后扫描相邻日跳变, 窗口内应仅剩真实除权(<1% 行)
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path("/home/leo/Projects/CodeAgentDashboard")
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from loguru import logger

from stockhot.data_layer.market_db import get_connection
from stockhot.data_layer.tushare_gateway import get_gateway

START, END = "20260716", "20260821"


def trade_dates(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM daily_price "
        "WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
        (START, END),
    ).fetchall()
    return [r[0] for r in rows]


def main() -> None:
    gw = get_gateway()
    conn = get_connection()
    dates = trade_dates(conn)
    logger.info("修复窗口 {} → {}: {} 个交易日", START, END, len(dates))
    total_updated = 0
    for d in dates:
        adj_df = gw.call(
            "adj_factor", trade_date=d, paginate=True,
            fields="ts_code,trade_date,adj_factor",
        )
        if adj_df is None or adj_df.empty:
            logger.warning("{} 因子返回为空, 跳过", d)
            continue
        cur = conn.cursor()
        cur.executemany(
            "UPDATE daily_price SET adj_factor=? "
            "WHERE ts_code=? AND trade_date=?",
            [(float(r.adj_factor), r.ts_code, d) for r in adj_df.itertuples()],
        )
        conn.commit()
        total_updated += cur.rowcount
        logger.info("{}: API {} 行, UPDATE 命中 {}", d, len(adj_df), cur.rowcount)
        time.sleep(1.0)  # 温和限流
    logger.info("完成: 共 UPDATE {} 行", total_updated)
    conn.close()


if __name__ == "__main__":
    main()
