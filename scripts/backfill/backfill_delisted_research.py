"""回补退市股日线 + 复权因子 + 全市场更名时间线 → 独立研究库.

实验0010 幸存者剪枝修复 (2026-08-31): daily_price 全表 0 条 2026 年前终止的
代码——退市股历史整体缺席, 回测宇宙天然幸存者剪枝。本脚本把退市股数据落
**独立研究库** storage/database/market_data_delisted.db (表内标注 source):

1. daily_price_delisted — list_status='D' 全部代码的日线 (∪ adj_factor 按日合并)
2. namechange — 全市场证券更名时间线 (历史 ST 口径重建用: 现名无法回溯当时是否 ST)

生产/实盘进程不设 MARKET_DB_ATTACH_DELISTED=1, 物理上读不到本库
(混入机制见 stockhot/data_layer/market_db.py _attach_delisted_research_view)。

幂等: (ts_code, trade_date) 主键 + INSERT OR REPLACE, 已完成代码自动跳过, 可断点续跑。
用法: .venv/bin/python scripts/backfill/backfill_delisted_research.py [--namechange-only]
"""
import os, sys, time, json
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")

import sqlite3
import pandas as pd
from stockhot.data_layer.market_db import get_connection, DELISTED_DB_PATH
from stockhot.storage.database import init_database
from davis_analyzer.tushare_client import TushareClient

init_database()
client = TushareClient()

START, END = "20150105", "20260831"
SUMMARY_PATH = "logs/backfill_delisted_research.json"

DDL = [
    """CREATE TABLE IF NOT EXISTS daily_price_delisted (
        ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL, pre_close REAL,
        pct_chg REAL, vol REAL, amount REAL, adj_factor REAL,
        fetched_at TEXT, source TEXT,
        PRIMARY KEY (ts_code, trade_date))""",
    """CREATE TABLE IF NOT EXISTS namechange (
        ts_code TEXT NOT NULL, name TEXT, start_date TEXT, end_date TEXT,
        ann_date TEXT, change_reason TEXT,
        PRIMARY KEY (ts_code, start_date, name))""",
    "CREATE INDEX IF NOT EXISTS idx_dpd_date ON daily_price_delisted (trade_date)",
]


def backfill_daily(con: sqlite3.Connection) -> dict:
    with get_connection() as c:
        codes = [r[0] for r in c.execute(
            "SELECT ts_code FROM stock_basic WHERE list_status='D' ORDER BY ts_code")]
    done = {r[0] for r in con.execute(
        "SELECT DISTINCT ts_code FROM daily_price_delisted")}
    todo = [x for x in codes if x not in done]
    print(f"退市股 {len(codes)} 只, 已回补 {len(done)}, 待拉 {len(todo)} "
          f"(预估 {len(todo) * 2 / 400:.1f} 分钟)")

    summary = {"n_codes": len(codes), "done_before": len(done),
               "fetched": 0, "rows": 0, "empty": [], "ranges": {}}
    t0 = time.time()
    for i, code in enumerate(todo):
        try:
            ddf = client._call("daily", client._pro.daily,
                               dict(ts_code=code, start_date=START, end_date=END))
            adf = client._call("adj_factor", client._pro.adj_factor,
                               dict(ts_code=code, start_date=START, end_date=END))
            if ddf is None or ddf.empty:
                summary["empty"].append(code)
                continue
            adj = {} if adf is None or adf.empty else {
                r["trade_date"]: r["adj_factor"] for _, r in adf.iterrows()}
            rows = []
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            for _, r in ddf.iterrows():
                rows.append((code, r["trade_date"], r.get("open"), r.get("high"),
                             r.get("low"), r.get("close"), r.get("pre_close"),
                             r.get("pct_chg"), r.get("vol"), r.get("amount"),
                             adj.get(r["trade_date"]), now,
                             "tushare_daily_delisted_backfill"))
            con.executemany(
                "INSERT OR REPLACE INTO daily_price_delisted VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.commit()
            summary["fetched"] += 1
            summary["rows"] += len(rows)
            summary["ranges"][code] = [ddf["trade_date"].min(), ddf["trade_date"].max()]
            if (i + 1) % 40 == 0:
                print(f"  {i+1}/{len(todo)} ({time.time()-t0:.0f}s) 累计 {summary['rows']} 行")
        except Exception as e:
            print(f"  !! {code} 失败: {e}")
    print(f"日线完成: 新拉 {summary['fetched']} 只 / {summary['rows']} 行; "
          f"空返回 {len(summary['empty'])} 只 {summary['empty'][:10]}")
    return summary


def backfill_namechange(con: sqlite3.Connection) -> int:
    total, offset = 0, 0
    while True:
        df = client._call("namechange", client._pro.namechange,
                          dict(limit=10000, offset=offset))
        if df is None or df.empty:
            break
        con.executemany(
            "INSERT OR REPLACE INTO namechange VALUES (?,?,?,?,?,?)",
            [(r["ts_code"], r.get("name"), r.get("start_date"), r.get("end_date"),
              r.get("ann_date"), r.get("change_reason")) for _, r in df.iterrows()])
        con.commit()
        total += len(df)
        offset += len(df)
        print(f"  namechange 累计 {total} 行")
        if len(df) < 10000:
            break
    return total


def main():
    namechange_only = "--namechange-only" in sys.argv
    DELISTED_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DELISTED_DB_PATH) as con:
        con.execute("PRAGMA journal_mode=WAL")
        for stmt in DDL:
            con.execute(stmt)
        summary = {"daily": None, "namechange_rows": 0}
        if not namechange_only:
            summary["daily"] = backfill_daily(con)
        summary["namechange_rows"] = backfill_namechange(con)
        # 数据体检: 行情末日前 30 天内仍有成交的占比 + 总量
        n_dpd = con.execute("SELECT COUNT(*) FROM daily_price_delisted").fetchone()[0]
        n_nc = con.execute("SELECT COUNT(*) FROM namechange").fetchone()[0]
        summary["final_counts"] = {"daily_price_delisted": n_dpd, "namechange": n_nc}
    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"完成: {summary['final_counts']} → {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
