"""Resume vol/amount backfill for remaining dates.

After cleanup, 202 dates still need vol/amount data. This script fetches them
with the CORRECT column order (ts_code, trade_date first).
"""
import os, sys, time
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")

from stockhot.data_layer.market_db import get_connection, init_db
from davis_analyzer.tushare_client import TushareClient

init_db()
client = TushareClient()

with get_connection() as c:
    dp_dates = [r[0] for r in c.execute(
        "SELECT DISTINCT trade_date FROM daily_price WHERE vol IS NULL ORDER BY trade_date"
    ).fetchall()]

print(f"Vol/amount backfill (resume)")
print(f"  Dates needing vol/amount: {len(dp_dates)}")
if dp_dates:
    print(f"  Date range: {dp_dates[0]} → {dp_dates[-1]}")
print(f"  Est time: {len(dp_dates)/400*60:.1f} min\n", flush=True)

t0 = time.time()
total_updated = 0
total_inserted = 0
errors = []

for i, date in enumerate(dp_dates):
    try:
        df = client._pro.daily(
            trade_date=date,
            fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
        )
        if df is None or df.empty:
            continue

        # CORRECT ORDER: ts_code, trade_date first
        records = []
        for r in df.to_dict("records"):
            records.append((
                r.get("ts_code", ""),
                str(r.get("trade_date", date)),
                r.get("open"),
                r.get("high"),
                r.get("low"),
                r.get("close"),
                r.get("pre_close"),
                r.get("pct_chg"),
                r.get("vol"),
                r.get("amount"),
            ))

        with get_connection() as conn:
            before = conn.execute(
                "SELECT COUNT(*) FROM daily_price WHERE trade_date=?", (date,)
            ).fetchone()[0]

            conn.executemany(
                "INSERT INTO daily_price "
                "(ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(ts_code, trade_date) DO UPDATE SET "
                "  open=excluded.open, high=excluded.high, low=excluded.low, "
                "  close=excluded.close, pre_close=excluded.pre_close, "
                "  pct_chg=excluded.pct_chg, vol=excluded.vol, amount=excluded.amount",
                records,
            )
            conn.commit()

            after = conn.execute(
                "SELECT COUNT(*) FROM daily_price WHERE trade_date=?", (date,)
            ).fetchone()[0]
            total_updated += before
            total_inserted += (after - before)

        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            pct = (i + 1) / len(dp_dates) * 100
            eta = elapsed / (i + 1) * (len(dp_dates) - i - 1)
            print(f"  [{i+1}/{len(dp_dates)}] {date}: +{len(records)} "
                  f"(updated={total_updated:,}, inserted={total_inserted:,}) "
                  f"{pct:.0f}% ETA {eta/60:.1f}min", flush=True)
    except Exception as e:
        errors.append((date, str(e)))
        print(f"  [ERROR] {date}: {e}", flush=True)

elapsed = time.time() - t0
print(f"\nDone: {total_updated:,} updated, {total_inserted:,} inserted, "
      f"{len(errors)} errors in {elapsed/60:.1f}min", flush=True)

# Verify
with get_connection() as c:
    vol_total = c.execute("SELECT COUNT(*) FROM daily_price WHERE vol IS NOT NULL AND vol > 0").fetchone()[0]
    all_total = c.execute("SELECT COUNT(*) FROM daily_price").fetchone()[0]
    null_vol = c.execute("SELECT COUNT(*) FROM daily_price WHERE vol IS NULL").fetchone()[0]
    pct = vol_total * 100 // all_total if all_total else 0
    print(f"\n{'='*60}")
    print(f"Verification:")
    print(f"  vol>0: {vol_total:,} / {all_total:,} ({pct}%)")
    print(f"  NULL vol: {null_vol:,}")
