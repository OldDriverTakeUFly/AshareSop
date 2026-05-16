"""Collect futures sentiment data: IF/IC/IM futures, basis, northbound, margin.

Table: invest_futures_sentiment
"""

import argparse
import os
import traceback
from datetime import datetime

import akshare as ak
import pandas as pd

from stockhot.invest_sop.utils.db_helpers import upsert_record
from stockhot.invest_sop.utils.trading_calendar import is_trading_day

TABLE = "invest_futures_sentiment"


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


def _calc_pct_change(df: pd.DataFrame) -> float | None:
    if df is None or len(df) < 2:
        return None
    close_col = None
    for col in df.columns:
        if "收盘" in str(col) or "close" in str(col).lower():
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


def _collect_futures_pct(target_date: str) -> dict[str, float | None]:
    result = {}
    for symbol, key in [("IF0", "if_pct"), ("IC0", "ic_pct"), ("IM0", "im_pct")]:
        try:
            df = _call_akshare("futures_main_sina", symbol=symbol)
            pct = _calc_pct_change(df)
            result[key] = pct
            print(f"  [OK] {symbol}: pct={pct}")
        except Exception as e:
            result[key] = None
            print(f"  [WARN] {symbol} failed: {e}")
    return result


def _collect_futures_basis(target_date: str) -> dict[str, float | None]:
    result: dict[str, float | None] = {"if_basis": None, "ic_basis": None}
    date_clean = target_date.replace("-", "")
    try:
        df = _call_akshare("futures_spot_price", date=date_clean, vars_list=["IF", "IC", "IM"])
        if df is None or len(df) == 0:
            print("  [INFO] futures_spot_price returned 0 rows (non-trading day?)")
            return result
        basis_map = {"IF": "if_basis", "IC": "ic_basis"}
        for _, row in df.iterrows():
            sym = str(row.get("symbol", ""))
            if sym in basis_map:
                rate = row.get("near_basis_rate")
                if rate is not None and not pd.isna(rate):
                    result[basis_map[sym]] = round(float(rate), 4)
                    print(f"  [OK] {sym} basis: {rate}")
    except Exception as e:
        print(f"  [WARN] futures basis failed: {e}")
        traceback.print_exc()
    return result


def _collect_northbound(target_date: str) -> float | None:
    try:
        df = _call_akshare("stock_hsgt_hist_em", symbol="北向资金")
        if df is None or len(df) == 0:
            return None
        col = None
        for c in df.columns:
            if "当日成交净买额" in str(c):
                col = c
                break
        if col is None:
            return None
        val = df[col].iloc[-1]
        if pd.isna(val):
            return None
        result = round(float(val) / 1e8, 4)
        print(f"  [OK] Northbound net: {result}亿")
        return result
    except Exception as e:
        print(f"  [WARN] Northbound failed: {e}")
        return None


def _collect_margin(target_date: str) -> float | None:
    date_clean = target_date.replace("-", "")
    try:
        df = _call_akshare("stock_margin_sse", start_date=date_clean, end_date=date_clean)
        if df is None or len(df) == 0:
            return None
        col = None
        for c in df.columns:
            if "融资余额" in str(c) and "买入" not in str(c):
                col = c
                break
        if col is None:
            return None
        val = df[col].iloc[-1]
        if pd.isna(val):
            return None
        result = round(float(val) / 1e8, 4)
        print(f"  [OK] Margin balance: {result}亿")
        return result
    except Exception as e:
        print(f"  [WARN] Margin failed: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Collect futures sentiment data")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"[futures_sentiment] date={args.date} dry_run={args.dry_run}")

    if not is_trading_day(args.date):
        print(f"[SKIP] {args.date} is not a trading day")
        return

    data: dict = {"date": args.date, "put_call_ratio": None}

    print("  Collecting IF/IC/IM futures % change...")
    data.update(_collect_futures_pct(args.date))

    print("  Collecting futures basis...")
    data.update(_collect_futures_basis(args.date))

    print("  Collecting northbound flow...")
    data["northbound_net"] = _collect_northbound(args.date)

    print("  Collecting margin balance...")
    data["margin_balance"] = _collect_margin(args.date)

    clean = {k: v for k, v in data.items() if v is not None}
    print(f"[RESULT] {clean}")

    if not args.dry_run:
        upsert_record(TABLE, clean, unique_keys=["date"])
        print(f"[SAVED] {len(clean)} fields to {TABLE}")
    else:
        print("[DRY-RUN] Skipping DB write")


if __name__ == "__main__":
    main()
