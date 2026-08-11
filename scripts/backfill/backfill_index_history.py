"""Backfill index_daily to 2021-01-01 for HMM training + MA120/MA250."""
import os, sys, time
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")

from stockhot.data_layer.market_db import get_connection, init_db
from davis_analyzer.tushare_client import TushareClient

init_db()
client = TushareClient()

INDICES = ["000001.SH", "399006.SZ", "000300.SH", "399001.SZ"]
START = "20210101"
END = "20260723"

for idx_code in INDICES:
    # Check current coverage
    with get_connection() as c:
        row = c.execute(
            "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM index_daily WHERE ts_code=?",
            (idx_code,)
        ).fetchone()
    print(f"\n{idx_code}: currently {row[0]} rows ({row[1]} → {row[2]})")

    # Fetch full history
    print(f"  Fetching {START} → {END}...")
    try:
        df = client._pro.index_daily(ts_code=idx_code, start_date=START, end_date=END)
        if df is None or df.empty:
            print(f"  No data returned")
            continue
        print(f"  Got {len(df)} rows")

        # Insert
        records = []
        for r in df.to_dict("records"):
            records.append((
                idx_code, str(r.get("trade_date", "")),
                float(r["open"]) if r.get("open") else None,
                float(r["high"]) if r.get("high") else None,
                float(r["low"]) if r.get("low") else None,
                float(r["close"]) if r.get("close") else None,
                float(r["vol"]) if r.get("vol") else None,
                float(r["amount"]) if r.get("amount") else None,
                float(r["pct_chg"]) if r.get("pct_chg") else None,
                time.time(),
            ))

        with get_connection() as c:
            c.executemany(
                "INSERT OR REPLACE INTO index_daily "
                "(ts_code, trade_date, open, high, low, close, vol, amount, pct_chg, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                records,
            )
            c.commit()
        print(f"  Inserted {len(records)} rows ✓")
    except Exception as e:
        print(f"  ERROR: {e}")

# Summary
print("\n=== Summary ===")
with get_connection() as c:
    for idx_code in INDICES:
        row = c.execute(
            "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM index_daily WHERE ts_code=?",
            (idx_code,)
        ).fetchone()
        print(f"  {idx_code}: {row[0]} rows ({row[1]} → {row[2]})")
