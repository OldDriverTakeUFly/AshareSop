"""Collect overseas market data: US indices, US 10Y yield, USD/CNY, A50, VIX.

Table: invest_overseas_market
"""

import argparse
import os
import traceback
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd

from stockhot.invest_sop.utils.db_helpers import upsert_record
from stockhot.invest_sop.utils.trading_calendar import is_trading_day

TABLE = "invest_overseas_market"


def strip_proxy() -> dict[str, str]:
    """Remove proxy env vars, return dict for restoration."""
    removed: dict[str, str] = {}
    for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"]:
        if key in os.environ:
            removed[key] = os.environ.pop(key)
    return removed


def restore_proxy(removed: dict[str, str]) -> None:
    os.environ.update(removed)


def _call_akshare(method_name: str, **kwargs):
    """Call an akshare method with proxy stripping."""
    removed = strip_proxy()
    try:
        method = getattr(ak, method_name)
        return method(**kwargs)
    finally:
        restore_proxy(removed)


def _calc_pct_change(df: pd.DataFrame) -> float | None:
    """Calculate percentage change from last 2 rows of a dataframe (close column)."""
    if df is None or len(df) < 2:
        return None
    # Find close-like column
    close_col = None
    for col in df.columns:
        if "close" in str(col).lower() or "收盘" in str(col):
            close_col = col
            break
    if close_col is None:
        # Try last numeric column
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


def _get_last_value(df: pd.DataFrame, col_name: str) -> float | None:
    """Get last row value from a specific column."""
    if df is None or len(df) == 0:
        return None
    if col_name not in df.columns:
        return None
    val = df[col_name].iloc[-1]
    if pd.isna(val):
        return None
    return float(val)


def _collect_cboe_vix(date_str: str) -> float | None:
    try:
        url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
        df = pd.read_csv(url)
        # CBOE DATE format: MM/DD/YYYY
        target = datetime.strptime(date_str, "%Y-%m-%d").strftime("%m/%d/%Y")
        row = df[df["DATE"] == target]
        if row.empty:
            print(f"  [WARN] CBOE VIX: no data for {date_str}")
            return None
        return round(float(row["CLOSE"].iloc[0]), 4)
    except Exception as e:
        print(f"  [WARN] CBOE VIX: {e}")
        traceback.print_exc()
        return None


def collect_overseas_data(target_date: str) -> dict:
    """Collect all overseas market data points."""
    results: dict = {}
    errors: list[str] = []

    # US indices: S&P500, Nasdaq, Dow
    for symbol, key in [(".DJI", "dow"), (".IXIC", "nasdaq"), (".INX", "sp500")]:
        try:
            df = _call_akshare("index_us_stock_sina", symbol=symbol)
            if df is not None and len(df) >= 2:
                pct = _calc_pct_change(df)
                results[f"{key}_pct"] = pct
                print(f"  [OK] {key}: pct={pct}")
            else:
                errors.append(f"{key}: insufficient data rows")
        except Exception as e:
            errors.append(f"{key}: {e}")
            traceback.print_exc()

    # US Treasury yields (10Y + 2Y) — Tushare us_tycr (120 pts, reliable).
    # AKShare bond_zh_us_rate() was used before but never succeeded (0% fill rate),
    # replaced 2026-07-30. us_tycr returns y10/y2 as decimals (e.g. 4.65 = 4.65%).
    try:
        from davis_analyzer.tushare_client import TushareClient

        client = TushareClient()
        end = target_date.replace("-", "")
        start = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=10)).strftime("%Y%m%d")
        df = client._pro.us_tycr(start_date=start, end_date=end)
        if df is not None and len(df) >= 1:
            df = df.sort_values("date")
            cur_10y = float(df["y10"].iloc[-1])
            results["us_10y"] = round(cur_10y, 4)
            results["us_2y"] = round(float(df["y2"].iloc[-1]), 4)
            if len(df) >= 2:
                prev_10y = float(df["y10"].iloc[-2])
                results["us_10y_change_bp"] = round((cur_10y - prev_10y) * 100, 2)
            print(f"  [OK] US 10Y: {results['us_10y']} ({results.get('us_10y_change_bp', 0)}bp), 2Y: {results['us_2y']}")
        else:
            errors.append("us_10y: us_tycr returned no data")
    except Exception as e:
        errors.append(f"us_10y: {e}")
        traceback.print_exc()

    # USD/CNY
    try:
        date_clean = target_date.replace("-", "")
        df = _call_akshare(
            "currency_boc_sina", symbol="美元", start_date=date_clean, end_date=date_clean
        )
        if df is not None and len(df) >= 1:
            results["usd_cny"] = _get_last_value(df, "央行中间价")
            print(f"  [OK] USD/CNY: {results.get('usd_cny')}")
        else:
            errors.append("usd_cny: no data returned")
    except Exception as e:
        errors.append(f"usd_cny: {e}")
        traceback.print_exc()

    # USD/JPY — Tushare fx_daily (2000 pts). Yen weakness >160 = carry-trade
    # unwind risk (the trigger of the 2024-08-05 global crash). Critical for
    # the international overlay's 套息风险 signal.
    try:
        from davis_analyzer.tushare_client import TushareClient

        client = TushareClient()
        end = target_date.replace("-", "")
        start = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y%m%d")
        df = client._pro.fx_daily(ts_code="USDJPY.FXCM", start_date=start, end_date=end)
        if df is not None and len(df) >= 1:
            df = df.sort_values("trade_date")
            results["usd_jpy"] = round(float(df["bid_close"].iloc[-1]), 4)
            print(f"  [OK] USD/JPY: {results.get('usd_jpy')}")
        else:
            errors.append("usd_jpy: fx_daily returned no data")
    except Exception as e:
        errors.append(f"usd_jpy: {e}")
        traceback.print_exc()

    # A50 futures
    try:
        df = _call_akshare("futures_foreign_hist", symbol="CHA50CFD")
        if df is not None and len(df) >= 2:
            results["a50_pct"] = _calc_pct_change(df)
            print(f"  [OK] A50: pct={results.get('a50_pct')}")
        else:
            errors.append("a50: insufficient data rows")
    except Exception as e:
        errors.append(f"a50: {e}")
        traceback.print_exc()

    # VIX (China 50ETF QVIX) — 走 DAL 缓存（与 volatility 模块共享）
    try:
        from stockhot.data_layer import get_repository

        repo = get_repository()
        ivix_df = repo.get_ivix_history(days=30)
        if not ivix_df.empty:
            results["vix"] = round(float(ivix_df["close"].iloc[-1]), 4)
            print(f"  [OK] VIX (QVIX via DAL): {results.get('vix')}")
        else:
            errors.append("vix: no data in DAL cache")
    except Exception as e:
        errors.append(f"vix: {e}")
        traceback.print_exc()

    # US VIX (CBOE)
    try:
        vix_val = _collect_cboe_vix(target_date)
        if vix_val is not None:
            results["us_vix"] = vix_val
            print(f"  [OK] US VIX (CBOE): {results.get('us_vix')}")
        else:
            errors.append("us_vix: no data found for date")
    except Exception as e:
        errors.append(f"us_vix: {e}")
        traceback.print_exc()

    if errors:
        print(f"  [WARN] Errors: {errors}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Collect overseas market data")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"[overseas_market_data] date={args.date} dry_run={args.dry_run}")

    if not is_trading_day(args.date):
        print(f"[SKIP] {args.date} is not a trading day")
        return

    data = collect_overseas_data(args.date)
    data["date"] = args.date

    # Filter out None values for clean output
    clean = {k: v for k, v in data.items() if v is not None}
    print(f"[RESULT] {clean}")

    if not args.dry_run:
        upsert_record(TABLE, clean, unique_keys=["date"])
        print(f"[SAVED] {len(clean)} fields to {TABLE}")
    else:
        print("[DRY-RUN] Skipping DB write")


if __name__ == "__main__":
    main()
