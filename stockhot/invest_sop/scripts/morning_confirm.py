"""Collect morning confirmation data: A50, Nikkei, KOSPI, USD/CNY.

Table: invest_morning_data
No trading day check — runs every morning.
"""

import argparse
import os
from datetime import datetime

import akshare as ak
import pandas as pd

from stockhot.invest_sop.utils.db_helpers import query_by_date, upsert_record

TABLE = "invest_morning_data"


def strip_proxy() -> dict[str, str]:
    removed: dict[str, str] = {}
    for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"]:
        if key in os.environ:
            removed[key] = os.environ.pop(key)
    return removed


def restore_proxy(removed: dict[str, str]) -> None:
    os.environ.update(removed)


def _call_akshare(method_name: str, **kwargs):
    removed = strip_proxy()
    try:
        method = getattr(ak, method_name)
        return method(**kwargs)
    finally:
        restore_proxy(removed)


def _calc_pct_change(df) -> float | None:
    if df is None or len(df) < 2:
        return None
    close_col = None
    for col in df.columns:
        if "close" in str(col).lower() or "收盘" in str(col):
            close_col = col
            break
    if close_col is None:
        for col in reversed(df.columns):
            if pd.api.types.is_numeric_dtype(df[col]):
                close_col = col
                break
    if close_col is None:
        return None
    current = float(df[close_col].iloc[-1])
    previous = float(df[close_col].iloc[-2])
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 4)


def _get_latest_close(df) -> float | None:
    if df is None or len(df) == 0:
        return None
    close_col = None
    for col in df.columns:
        if "close" in str(col).lower() or "收盘" in str(col):
            close_col = col
            break
    if close_col is None:
        for col in reversed(df.columns):
            if pd.api.types.is_numeric_dtype(df[col]):
                close_col = col
                break
    if close_col is None:
        return None
    val = df[close_col].iloc[-1]
    return round(float(val), 4) if not pd.isna(val) else None


def main():
    parser = argparse.ArgumentParser(description="Collect morning confirmation data (runs daily)")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"[morning_confirm] date={args.date} dry_run={args.dry_run}")

    data: dict = {"date": args.date}
    notes_parts: list[str] = []

    # A50 futures
    print("  Collecting A50 futures...")
    try:
        df = _call_akshare("futures_foreign_hist", symbol="CHA50CFD")
        pct = _calc_pct_change(df)
        data["a50_morning_pct"] = pct
        print(f"  [OK] A50: pct={pct}")
    except Exception as e:
        print(f"  [WARN] A50 failed: {e}")

    # Nikkei — try index_us_stock_sina first, then futures_foreign_hist
    print("  Collecting Nikkei...")
    try:
        df = _call_akshare("index_us_stock_sina", symbol=".N225")
        if df is not None and len(df) >= 2:
            data["nikkei_pct"] = _calc_pct_change(df)
            print(f"  [OK] Nikkei: pct={data['nikkei_pct']}")
        else:
            raise ValueError("insufficient rows")
    except Exception:
        try:
            df = _call_akshare("futures_foreign_hist", symbol="NID")
            data["nikkei_pct"] = _calc_pct_change(df)
            print(f"  [OK] Nikkei (futures fallback): pct={data['nikkei_pct']}")
        except Exception as e2:
            print(f"  [WARN] Nikkei failed: {e2}")

    # KOSPI
    print("  Collecting KOSPI...")
    try:
        df = _call_akshare("index_us_stock_sina", symbol="KS11")
        if df is not None and len(df) >= 2:
            data["kospi_pct"] = _calc_pct_change(df)
            print(f"  [OK] KOSPI: pct={data['kospi_pct']}")
        else:
            print(f"  [WARN] KOSPI: insufficient data rows ({len(df) if df is not None else 0})")
    except Exception as e:
        print(f"  [WARN] KOSPI failed: {e}")

    # USD/CNY
    print("  Collecting USD/CNY...")
    try:
        date_clean = args.date.replace("-", "")
        df = _call_akshare(
            "currency_boc_sina", symbol="美元", start_date=date_clean, end_date=date_clean
        )
        if df is not None and len(df) >= 1:
            col = None
            for c in df.columns:
                if "央行中间价" in str(c):
                    col = c
                    break
            if col:
                val = df[col].iloc[-1]
                if not pd.isna(val):
                    data["usd_cny_morning"] = round(float(val), 4)
                    print(f"  [OK] USD/CNY: {data['usd_cny_morning']}")
    except Exception as e:
        print(f"  [WARN] USD/CNY failed: {e}")

    # Compare with overnight data if exists
    overseas_rows = query_by_date("invest_overseas_market", args.date)
    if overseas_rows:
        overseas = overseas_rows[0]
        deltas: list[str] = []
        if data.get("a50_morning_pct") is not None and overseas.get("a50_pct") is not None:
            delta = round(data["a50_morning_pct"] - overseas["a50_pct"], 2)
            deltas.append(f"A50Δ={delta}%")
        if data.get("usd_cny_morning") is not None and overseas.get("usd_cny") is not None:
            delta = round(data["usd_cny_morning"] - overseas["usd_cny"], 4)
            deltas.append(f"USDCNYΔ={delta}")
        if deltas:
            notes_parts.append("vs overnight: " + ", ".join(deltas))
            print(f"  [DELTA] {'; '.join(deltas)}")

    if notes_parts:
        data["notes"] = "; ".join(notes_parts)

    clean = {k: v for k, v in data.items() if v is not None}
    print(f"[RESULT] {clean}")

    if not args.dry_run:
        upsert_record(TABLE, clean, unique_keys=["date"])
        print(f"[SAVED] {len(clean)} fields to {TABLE}")
    else:
        print("[DRY-RUN] Skipping DB write")


if __name__ == "__main__":
    main()
