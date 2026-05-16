"""Collect supply chain / commodity data: LME metals, domestic futures basis, BDI, steel, energy.

Table: invest_supply_chain
"""

import argparse
import os
import traceback
from datetime import datetime

import akshare as ak
import pandas as pd

from stockhot.invest_sop.utils.db_helpers import upsert_record
from stockhot.invest_sop.utils.trading_calendar import is_trading_day

TABLE = "invest_supply_chain"


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


def _last_close(df: pd.DataFrame) -> float | None:
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


def _collect_lme_metals(target_date: str) -> list[dict]:
    """LME Copper/Aluminum/Zinc from foreign futures."""
    records = []
    for symbol, name in [("CAD", "LME铜"), ("AHD", "LME铝"), ("ZSD", "LME锌")]:
        try:
            df = _call_akshare("futures_foreign_hist", symbol=symbol)
            val = _last_close(df)
            if val is not None:
                records.append({
                    "date": target_date,
                    "sector": "有色",
                    "metric_name": name,
                    "value": val,
                    "unit": "USD/t",
                    "source": "sina_futures_foreign",
                })
                print(f"  [OK] {name}: {val}")
        except Exception as e:
            print(f"  [WARN] {name} failed: {e}")
    return records


def _collect_domestic_futures_basis(target_date: str) -> list[dict]:
    """Domestic commodity futures spot prices and basis."""
    records = []
    symbol_map = {
        "CU": ("有色", "沪铜现货价"),
        "AL": ("有色", "沪铝现货价"),
        "ZN": ("有色", "沪锌现货价"),
        "J":  ("煤炭", "焦炭现货价"),
        "JM": ("煤炭", "焦煤现货价"),
        "I":  ("煤炭", "铁矿现货价"),
    }
    date_clean = target_date.replace("-", "")
    try:
        df = _call_akshare("futures_spot_price", date=date_clean, vars_list=list(symbol_map.keys()))
        if df is None or len(df) == 0:
            print(f"  [INFO] futures_spot_price returned 0 rows (non-trading day?)")
            return records
        for _, row in df.iterrows():
            sym = str(row.get("symbol", ""))
            if sym not in symbol_map:
                continue
            sector, metric_name = symbol_map[sym]
            spot_price = row.get("spot_price")
            if spot_price is not None and not pd.isna(spot_price):
                records.append({
                    "date": target_date,
                    "sector": sector,
                    "metric_name": metric_name,
                    "value": round(float(spot_price), 2),
                    "unit": "CNY/t",
                    "source": "akshare_futures_spot",
                })
                print(f"  [OK] {metric_name}: {spot_price}")
    except Exception as e:
        print(f"  [WARN] domestic futures basis failed: {e}")
        traceback.print_exc()
    return records


def _collect_bdi(target_date: str) -> list[dict]:
    records = []
    try:
        df = _call_akshare("spot_goods", symbol="波罗的海干散货指数")
        if df is not None and len(df) >= 1:
            idx_col = None
            for col in df.columns:
                if "指数" in str(col):
                    idx_col = col
                    break
            if idx_col is None:
                idx_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]
            val = df[idx_col].iloc[-1]
            if not pd.isna(val):
                records.append({
                    "date": target_date,
                    "sector": "航运",
                    "metric_name": "BDI",
                    "value": round(float(val), 2),
                    "unit": "指数",
                    "source": "sina_spot_goods",
                })
                print(f"  [OK] BDI: {val}")
    except Exception as e:
        print(f"  [WARN] BDI failed: {e}")
    return records


def _collect_steel(target_date: str) -> list[dict]:
    records = []
    try:
        df = _call_akshare("spot_goods", symbol="钢坯价格指数")
        if df is not None and len(df) >= 1:
            idx_col = None
            for col in df.columns:
                if "指数" in str(col):
                    idx_col = col
                    break
            if idx_col is None:
                idx_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]
            val = df[idx_col].iloc[-1]
            if not pd.isna(val):
                records.append({
                    "date": target_date,
                    "sector": "钢铁",
                    "metric_name": "钢坯价格指数",
                    "value": round(float(val), 2),
                    "unit": "指数",
                    "source": "sina_spot_goods",
                })
                print(f"  [OK] 钢坯: {val}")
    except Exception as e:
        print(f"  [WARN] Steel failed: {e}")
    return records


def _collect_lc_ps_futures(target_date: str) -> list[dict]:
    """碳酸锂/多晶硅 futures from GFEX via Sina."""
    records = []
    date_clean = target_date.replace("-", "")
    for symbol, name in [("LC0", "碳酸锂期货收盘价"), ("PS0", "多晶硅期货收盘价")]:
        try:
            df = _call_akshare("futures_main_sina", symbol=symbol, start_date=date_clean, end_date=date_clean)
            val = _last_close(df)
            if val is not None:
                records.append({
                    "date": target_date,
                    "sector": "新能源",
                    "metric_name": name,
                    "value": val,
                    "unit": "CNY/t",
                    "source": "sina_futures_main",
                })
                print(f"  [OK] {name}: {val}")
            else:
                print(f"  [INFO] {name}: no data for {target_date}")
        except Exception as e:
            print(f"  [WARN] {name} failed: {e}")
    return records


def _collect_lc_ps_spot(target_date: str) -> list[dict]:
    """碳酸锂/多晶硅 spot prices via futures_spot_sys (may fail for newer commodities)."""
    records = []
    for name in ["碳酸锂", "多晶硅"]:
        try:
            df = _call_akshare("futures_spot_sys", symbol=name, indicator="市场价格")
            if df is not None and len(df) > 0:
                val = _last_close(df)
                if val is not None:
                    records.append({
                        "date": target_date,
                        "sector": "新能源",
                        "metric_name": f"{name}现货价",
                        "value": val,
                        "unit": "CNY/t",
                        "source": "100ppi",
                    })
                    print(f"  [OK] {name}现货: {val}")
                else:
                    print(f"  [INFO] {name}现货: could not extract price")
        except KeyError as e:
            print(f"  [SKIP] {name}现货 not available: {e}")
        except Exception as e:
            print(f"  [WARN] {name}现货 failed: {e}")
    return records


def _collect_energy(target_date: str) -> list[dict]:
    records = []
    try:
        df = _call_akshare("energy_oil_hist")
        if df is not None and len(df) >= 1:
            last_row = df.iloc[-1]
            for col_prefix, metric_name in [("汽油价格", "汽油价格"), ("柴油价格", "柴油价格")]:
                col = None
                for c in df.columns:
                    if col_prefix in str(c):
                        col = c
                        break
                if col is not None:
                    val = last_row[col]
                    if not pd.isna(val):
                        records.append({
                            "date": target_date,
                            "sector": "能源",
                            "metric_name": metric_name,
                            "value": round(float(val), 2),
                            "unit": "CNY/t",
                            "source": "akshare_energy_oil",
                        })
                        print(f"  [OK] {metric_name}: {val}")
    except Exception as e:
        print(f"  [WARN] Energy failed: {e}")
    return records


def main():
    parser = argparse.ArgumentParser(description="Collect supply chain / commodity data")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"[supply_chain] date={args.date} dry_run={args.dry_run}")

    if not is_trading_day(args.date):
        print(f"[SKIP] {args.date} is not a trading day")
        return

    all_records: list[dict] = []

    print("  Collecting LME metals...")
    all_records.extend(_collect_lme_metals(args.date))

    print("  Collecting domestic futures basis...")
    all_records.extend(_collect_domestic_futures_basis(args.date))

    print("  Collecting BDI...")
    all_records.extend(_collect_bdi(args.date))

    print("  Collecting steel index...")
    all_records.extend(_collect_steel(args.date))

    print("  Collecting energy prices...")
    all_records.extend(_collect_energy(args.date))

    print("  Collecting LC/PS futures...")
    all_records.extend(_collect_lc_ps_futures(args.date))

    print("  Collecting LC/PS spot...")
    all_records.extend(_collect_lc_ps_spot(args.date))

    print(f"[TOTAL] {len(all_records)} supply chain records")
    for rec in all_records:
        print(f"  {rec['sector']}/{rec['metric_name']}: {rec['value']} {rec['unit']}")

    if not args.dry_run:
        for rec in all_records:
            upsert_record(TABLE, rec, unique_keys=["date", "sector", "metric_name"])
        print(f"[SAVED] {len(all_records)} records to {TABLE}")
    else:
        print("[DRY-RUN] Skipping DB write")


if __name__ == "__main__":
    main()
