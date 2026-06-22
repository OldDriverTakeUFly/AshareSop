"""Backfill historical data for VIX, 碳酸锂/多晶硅 futures and spot prices.

Iterates over ALL calendar days (not just A-share trading days) because
US VIX trades on a different calendar and CBOE CSV has its own dates.

Usage:
    PYTHONPATH=. .venv/bin/python stockhot/invest_sop/scripts/backfill.py \
        --start-date 2026-02-16 --end-date 2026-05-16 [--source all] [--dry-run]
"""

import argparse
from datetime import datetime, timedelta

import pandas as pd

from stockhot.invest_sop.scripts.supply_chain import (
    _collect_lc_ps_futures,
    _collect_lc_ps_spot,
)
from stockhot.invest_sop.utils.db_helpers import upsert_record
from stockhot.storage.database import get_connection

TABLE_OVERSEAS = "invest_overseas_market"
TABLE_SUPPLY = "invest_supply_chain"


def _download_vix_lookup() -> dict[str, float]:
    print("[VIX] Downloading CBOE VIX history CSV (once)...")
    url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
    df = pd.read_csv(url)
    lookup = {}
    for _, row in df.iterrows():
        # CBOE DATE format: MM/DD/YYYY
        try:
            dt = datetime.strptime(str(row["DATE"]).strip(), "%m/%d/%Y")
            key = dt.strftime("%Y-%m-%d")
            lookup[key] = round(float(row["CLOSE"]), 4)
        except (ValueError, KeyError):
            continue
    print(f"[VIX] Loaded {len(lookup)} dates from CBOE CSV")
    return lookup


def _ensure_tables() -> None:
    from stockhot.storage.database import init_database

    init_database()
    conn = get_connection()
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(invest_overseas_market)")]
        if "us_vix" not in cols:
            conn.execute("ALTER TABLE invest_overseas_market ADD COLUMN us_vix REAL")
            conn.commit()
            print("[DB] Added us_vix column to invest_overseas_market.")
    finally:
        conn.close()


def backfill(start_date: str, end_date: str, source: str = "all", dry_run: bool = False) -> None:
    _ensure_tables()

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    dates = []
    d = start
    while d <= end:
        dates.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)

    total = len(dates)
    print(
        f"[BACKFILL] {start_date} -> {end_date} ({total} calendar days), source={source}, dry_run={dry_run}"
    )
    print()

    vix_lookup = {}
    run_vix = source in ("vix", "all")
    run_lc = source in ("lc", "all")
    run_ps = source in ("ps", "all")
    # _collect_lc_ps_futures handles both LC0 and PS0; "lc"/"ps"/"all" all need it
    run_futures = run_lc or run_ps or source == "all"
    run_spot = run_futures

    if run_vix:
        vix_lookup = _download_vix_lookup()
        print()

    records_written = 0
    failures = []

    for i, date_str in enumerate(dates, 1):
        vix_val = None
        lc_val = None
        ps_val = None

        if run_vix:
            try:
                vix_val = vix_lookup.get(date_str)
                if vix_val is not None and not dry_run:
                    upsert_record(
                        TABLE_OVERSEAS,
                        {"date": date_str, "us_vix": vix_val},
                        unique_keys=["date"],
                    )
                    records_written += 1
            except Exception as e:
                print(f"  [ERROR] VIX upsert failed for {date_str}: {e}")
                failures.append((date_str, "vix", str(e)))

        if run_futures:
            try:
                futures = _collect_lc_ps_futures(date_str)
                for rec in futures:
                    metric = rec.get("metric_name", "")
                    val = rec.get("value")
                    if "碳酸锂" in metric:
                        lc_val = val
                    elif "多晶硅" in metric:
                        ps_val = val
                    if not dry_run:
                        upsert_record(
                            TABLE_SUPPLY, rec, unique_keys=["date", "sector", "metric_name"]
                        )
                        records_written += 1
            except Exception as e:
                print(f"  [ERROR] LC/PS futures failed for {date_str}: {e}")
                failures.append((date_str, "futures", str(e)))

        if run_spot:
            try:
                spots = _collect_lc_ps_spot(date_str)
                for rec in spots:
                    if not dry_run:
                        upsert_record(
                            TABLE_SUPPLY, rec, unique_keys=["date", "sector", "metric_name"]
                        )
                        records_written += 1
            except Exception as e:
                print(f"  [ERROR] LC/PS spot failed for {date_str}: {e}")
                failures.append((date_str, "spot", str(e)))

        if i % 5 == 0 or i == total:
            parts = []
            if run_vix:
                parts.append(f"VIX={vix_val}")
            if run_futures or run_lc:
                parts.append(f"LC0={lc_val}")
            if run_futures or run_ps:
                parts.append(f"PS0={ps_val}")
            print(f"Processing {i}/{total}... {' '.join(parts)}")

    print()
    print("=" * 60)
    print(f"[SUMMARY] Dates processed: {total}")
    print(f"[SUMMARY] Records written: {records_written}")
    print(f"[SUMMARY] Failures: {len(failures)}")
    if failures:
        for dt, src, err in failures:
            print(f"  {dt} ({src}): {err}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Backfill historical data for VIX, LC, PS")
    parser.add_argument("--start-date", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="End date YYYY-MM-DD")
    parser.add_argument(
        "--source",
        choices=["vix", "lc", "ps", "all"],
        default="all",
        help="Data source to backfill (default: all)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write to DB")
    args = parser.parse_args()

    backfill(args.start_date, args.end_date, args.source, args.dry_run)


if __name__ == "__main__":
    main()
