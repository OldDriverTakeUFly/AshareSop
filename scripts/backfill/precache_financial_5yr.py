"""Pre-cache financial data for 5-year backtest universe (backfill mode).

The existing `_get_financial` only does forward-incremental fetch (pulls
newer dates than max cached). It does NOT backfill historical gaps. So
for stocks whose cache starts at 2023+, the 2018-2022 gap is never filled.

This script bypasses `_get_financial` and calls the Tushare API directly
with a wide date range, then inserts into the financial table via
`_financial_insert`. This forces a full historical pull.

Coverage needed:
  - 5-year backtest starts 2021-01-04
  - fetch_financial_data uses periods=12 (3-year look-back)
  - So we need data from 2018-01-01 onwards

Usage::

    cd /home/leo/Projects/CodeAgentDashboard

    # Default: top 200 by 2021-01-04 turnover
    PYTHONPATH=. .venv/bin/python scripts/precache_financial_5yr.py

    # Custom universe size:
    UNIVERSE_SIZE=50 PYTHONPATH=. .venv/bin/python scripts/precache_financial_5yr.py

Expected time: ~20-40 min for 200 stocks × 7 endpoints (rate-limited 400/min).
"""
import os, sys, time
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")

import pandas as pd
from stockhot.data_layer.market_db import get_connection as get_market_conn
from davis_analyzer.tushare_client import TushareClient

UNIVERSE_SIZE = int(os.environ.get("UNIVERSE_SIZE", "200"))
# Start from 2018 to cover periods=12 look-back from 2021-01-04
START = "20180101"
END = "20260731"


def build_universe(top_n: int) -> list[str]:
    with get_market_conn() as c:
        rows = c.execute(
            "SELECT ts_code FROM daily_price WHERE trade_date='20210104' "
            "AND close > 0 AND vol > 0 ORDER BY amount DESC LIMIT ?",
            (top_n,),
        ).fetchall()
    return [r[0] for r in rows]


def count_in_range(ts_code: str, endpoint: str, start: str, end: str) -> int:
    with get_market_conn() as c:
        row = c.execute(
            "SELECT COUNT(*) FROM financial WHERE ts_code=? AND endpoint=? "
            "AND end_date >= ? AND end_date <= ?",
            (ts_code, endpoint, start, end),
        ).fetchone()
        return row[0] if row else 0


def main():
    print(f"\n{'='*70}")
    print(f"  财务数据预缓存 — 强制回填模式 (5 年回测准备)")
    print(f"  覆盖: {START} → {END}")
    print(f"{'='*70}\n")

    universe = build_universe(UNIVERSE_SIZE)
    print(f"  Universe: {len(universe)} stocks (top by 2021-01-04 turnover)\n")

    client = TushareClient()

    # 7 endpoints with their API functions and field specs
    # Must match tushare_client.py get_* methods exactly
    endpoint_specs = [
        ("income",          client._pro.income,
         "ts_code,end_date,ann_date,total_revenue,n_income,n_income_attr_p"),
        ("balancesheet",    client._pro.balancesheet,
         "ts_code,end_date,ann_date,total_assets,total_liab,contract_liab"),
        ("cashflow",        client._pro.cashflow,
         "ts_code,end_date,ann_date,n_cashflow_act,c_pay_acq_const_fiolta"),
        ("fina_indicator",  client._pro.fina_indicator,
         "ts_code,end_date,ann_date,roe,roe_dt,q_profit_yoy,netprofit_margin,grossprofit_margin,debt_to_assets,quick_ratio,eps"),
        ("forecast",        client._pro.forecast,
         "ts_code,end_date,ann_date,predict_net_profit,predict_eps,predict_type"),
        ("dividend",        client._pro.dividend,
         "ts_code,end_date,ann_date,div_proc,cash_div,stk_div,div_cash"),
        ("stk_holdernumber", client._pro.stk_holdernumber,
         "ts_code,end_date,ann_date,holder_num"),
    ]

    t0 = time.time()
    total_new = 0

    for i, code in enumerate(universe):
        stock_t0 = time.time()
        stock_new = 0

        for endpoint_name, api_fn, fields in endpoint_specs:
            # Check if already has enough data in 2018-2022 range
            existing = count_in_range(code, endpoint_name, "20180101", "20221231")
            # Each year has 4 quarters × 5 years = 20 expected
            if existing >= 15:  # already sufficient
                continue

            try:
                # Direct API call — bypass _get_financial's forward-only logic
                df = client._call(
                    endpoint_name,
                    api_fn,
                    {
                        "ts_code": code,
                        "start_date": START,
                        "end_date": END,
                        "fields": fields,
                    },
                )
                if df is not None and len(df) > 0:
                    client._financial_insert(endpoint_name, code, df)
                    stock_new += len(df)
            except Exception as e:
                # Rate limit or API error — log and continue
                if "限频" in str(e) or "rate" in str(e).lower():
                    print(f"  ⚠ {code} {endpoint_name}: rate limited, sleeping 60s")
                    time.sleep(60)
                    try:
                        df = client._call(endpoint_name, api_fn, {
                            "ts_code": code, "start_date": START,
                            "end_date": END, "fields": fields,
                        })
                        if df is not None and len(df) > 0:
                            client._financial_insert(endpoint_name, code, df)
                            stock_new += len(df)
                    except Exception as e2:
                        print(f"  ⚠ {code} {endpoint_name} retry failed: {e2}")
                else:
                    logger.debug(f"  {code} {endpoint_name}: {e}")

        total_new += stock_new
        elapsed = time.time() - stock_t0

        if (i + 1) % 10 == 0 or i == 0 or stock_new > 50:
            total_elapsed = time.time() - t0
            avg = total_elapsed / (i + 1)
            eta = avg * (len(universe) - i - 1)
            print(f"  [{i+1:>3}/{len(universe)}] {code}: +{stock_new} rows "
                  f"({elapsed:.1f}s) | total +{total_new:,} | "
                  f"ETA {eta/60:.1f}min", flush=True)

    total_elapsed = time.time() - t0
    print(f"\n{'='*70}")
    print(f"  ✓ 完成: {len(universe)} stocks, +{total_new:,} new rows")
    print(f"  耗时: {total_elapsed/60:.1f}min ({total_elapsed/max(len(universe),1):.1f}s/stock)")
    print(f"{'='*70}")

    # Final verification
    print(f"\n  回填后 2018-2022 覆盖验证:")
    for ep_name, _, _ in endpoint_specs:
        total = sum(count_in_range(c, ep_name, "20180101", "20221231") for c in universe)
        avg = total / len(universe) if universe else 0
        print(f"    {ep_name}: {total:,} rows total, {avg:.1f} avg/stock "
              f"(理想: ~20/stock)")


if __name__ == "__main__":
    main()
