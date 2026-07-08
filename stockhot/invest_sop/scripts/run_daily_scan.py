#!/usr/bin/env python
"""Daily market scan orchestrator for after-hours data collection.

Runs the 6 stockhot analysis modules (limit_up → dragon_tiger +
fund_flow + index_technical + volatility → risk_alert) in the order
mandated by the daily-market-scan skill, with try/except isolation so
one module's failure does not block others. Persists all results to
SQLite.

Designed for crontab — run at 17:00+ on trading days (after the
dragon-tiger list is published ~17:30).

Usage:
    .venv/bin/python stockhot/invest_sop/scripts/run_daily_scan.py [--date YYYY-MM-DD]

Crontab example (17:00 Mon–Fri):
    0 17 * * 1-5 cd /home/leo/Projects/CodeAgentDashboard && \\
        PYTHONPATH=/home/leo/Projects/CodeAgentDashboard \\
        .venv/bin/python stockhot/invest_sop/scripts/run_daily_scan.py \\
        >> stockhot/invest_sop/logs/daily_scan.log 2>&1
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

# ── Wave 1: limit_up (must run first — risk_alert reads its DB output) ──


def run_wave_1(trade_date: str) -> bool:
    """limit_up analysis —涨停池/炸板池/跌停池/连板梯队/板块联动."""
    try:
        from stockhot.limit_up import run_limit_up_analysis

        result = run_limit_up_analysis(trade_date)
        count = len(result.get("limit_up_pool", [])) if isinstance(result, dict) else "?"
        print(f"[{trade_date}] Wave 1 limit_up: OK ({count} 涨停)")
        return True
    except Exception as e:
        print(f"[{trade_date}] Wave 1 limit_up: FAILED — {type(e).__name__}: {e}")
        return False


# ── Wave 2: dragon_tiger + fund_flow + index_technical + volatility
#    (all parallel-safe, none reads the others' DB output) ──


def run_wave_2(trade_date: str) -> tuple[bool, bool, bool, bool]:
    """dragon_tiger (龙虎榜) + fund_flow (资金流) + index_technical (技术面) + volatility (波动率)."""
    # fund_flow
    ff_ok = False
    try:
        from stockhot.fund_flow import run_fund_flow_analysis

        run_fund_flow_analysis(trade_date)
        print(f"[{trade_date}] Wave 2 fund_flow: OK")
        ff_ok = True
    except Exception as e:
        print(f"[{trade_date}] Wave 2 fund_flow: FAILED — {type(e).__name__}: {e}")

    # dragon_tiger
    dt_ok = False
    try:
        from stockhot.dragon_tiger import run_dragon_tiger_analysis

        run_dragon_tiger_analysis(trade_date)
        print(f"[{trade_date}] Wave 2 dragon_tiger: OK")
        dt_ok = True
    except Exception as e:
        print(f"[{trade_date}] Wave 2 dragon_tiger: FAILED — {type(e).__name__}: {e}")

    # index_technical (大盘技术面)
    it_ok = False
    try:
        from stockhot.index_technical import run_index_technical_analysis
        from stockhot.storage.database import save_daily_data

        result = run_index_technical_analysis(trade_date)
        if result.get("status") == "success":
            save_daily_data({"date": trade_date, "index_technical": result})
        print(f"[{trade_date}] Wave 2 index_technical: OK")
        it_ok = True
    except Exception as e:
        print(f"[{trade_date}] Wave 2 index_technical: FAILED — {type(e).__name__}: {e}")

    # volatility (波动率观察 — 中国版 VIX 五层体系)
    vol_ok = False
    try:
        from stockhot.volatility import run_volatility_analysis

        run_volatility_analysis(trade_date)
        print(f"[{trade_date}] Wave 2 volatility: OK")
        vol_ok = True
    except Exception as e:
        print(f"[{trade_date}] Wave 2 volatility: FAILED — {type(e).__name__}: {e}")

    return ff_ok, dt_ok, it_ok, vol_ok


# ── Wave 3: risk_alert (reads upstream DB data — must run last) ──


def run_wave_3(trade_date: str) -> bool:
    """risk_alert — ST/异常波动/资金出逃/高位连板 (reads limit_up + dragon_tiger)."""
    try:
        from stockhot.risk_alert import run_risk_alert_analysis

        run_risk_alert_analysis(trade_date)
        print(f"[{trade_date}] Wave 3 risk_alert: OK")
        return True
    except Exception as e:
        print(f"[{trade_date}] Wave 3 risk_alert: FAILED — {type(e).__name__}: {e}")
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run daily market scan (6 modules)")
    parser.add_argument("--date", default=None, help="Trade date YYYY-MM-DD (default: today)")
    args = parser.parse_args(argv)

    trade_date = args.date or date.today().isoformat()

    # Check trading day
    try:
        from stockhot.invest_sop.utils.trading_calendar import is_trading_day

        if not is_trading_day(trade_date):
            print(f"[{trade_date}] Not a trading day, skipping scan.")
            return 0
    except Exception:
        pass  # If calendar check fails, proceed anyway

    print(f"[{trade_date}] === Daily Market Scan starting ===")

    results = {}
    results["limit_up"] = run_wave_1(trade_date)
    ff_ok, dt_ok, it_ok, vol_ok = run_wave_2(trade_date)
    results["fund_flow"] = ff_ok
    results["dragon_tiger"] = dt_ok
    results["index_technical"] = it_ok
    results["volatility"] = vol_ok
    results["risk_alert"] = run_wave_3(trade_date)

    succeeded = sum(results.values())
    total = len(results)
    print(f"[{trade_date}] === Scan complete: {succeeded}/{total} modules succeeded ===")
    for mod, ok in results.items():
        status = "✓" if ok else "✗"
        print(f"  {status} {mod}")

    # Exit 0 if at least limit_up succeeded (core data), else 1
    return 0 if results["limit_up"] else 1


if __name__ == "__main__":
    sys.exit(main())
