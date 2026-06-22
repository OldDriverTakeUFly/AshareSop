#!/usr/bin/env python
"""Daily orchestration script for the AI trading advisor.

This script is designed to be called by crontab. It:
1. Runs the advisor daily command for all holdings + watchlist
2. Generates the premarket report (which includes AI recommendations section)
3. Logs progress and errors

Usage:
    .venv/bin/python stockhot/invest_sop/scripts/run_daily_advisor.py [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PYTHON = str(PROJECT_ROOT / ".venv" / "bin" / "python")
REPORT_SCRIPT = str(
    PROJECT_ROOT
    / "stockhot"
    / "invest_sop"
    / "scripts"
    / "generate_premarket_report.py"
)


def run_advisor(trade_date: str) -> bool:
    """Run the advisor daily command. Returns True on success."""
    cmd = [PYTHON, "-m", "stockhot.advisor", "daily", "--date", trade_date]
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return result.returncode == 0


def run_report(trade_date: str) -> bool:
    """Generate the premarket report. Returns True on success."""
    cmd = [PYTHON, REPORT_SCRIPT, "--date", trade_date]
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    """Run daily advisor + report generation. Returns 0 on success, 1 on failure."""
    parser = argparse.ArgumentParser(
        description="Run daily advisor + report generation"
    )
    parser.add_argument(
        "--date", default=None, help="Trade date (default: today)"
    )
    args = parser.parse_args(argv)

    trade_date = args.date or date.today().isoformat()

    print(f"[{trade_date}] Starting daily advisor...")
    advisor_ok = run_advisor(trade_date)
    if not advisor_ok:
        print(f"[{trade_date}] Advisor completed with errors")

    print(f"[{trade_date}] Generating premarket report...")
    report_ok = run_report(trade_date)

    if advisor_ok and report_ok:
        print(f"[{trade_date}] Daily advisor completed successfully")
        return 0
    else:
        print(f"[{trade_date}] Daily advisor completed with partial failures")
        return 1


if __name__ == "__main__":
    sys.exit(main())
