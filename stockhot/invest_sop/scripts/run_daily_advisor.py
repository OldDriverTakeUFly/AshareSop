#!/usr/bin/env python
"""Daily orchestration script for the AI trading advisor.

This script is designed to be called by crontab. It:
1. Runs the advisor daily command for all holdings + watchlist
2. Generates the premarket report (which includes AI recommendations section)
3. Commits & pushes the report to GitHub (so the Feishu link is valid immediately)
4. Pushes a concise summary to Feishu (企业自建应用 bot)

Usage:
    .venv/bin/python stockhot/invest_sop/scripts/run_daily_advisor.py [--date YYYY-MM-DD] [--no-feishu]
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
    PROJECT_ROOT / "stockhot" / "invest_sop" / "scripts" / "generate_premarket_report.py"
)
# 盘前报告相对仓库根的路径（用于 git add 的 pathspec）
REPORT_PATHSPEC = "storage/files/reports/invest_sop/"


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


def commit_push_report(trade_date: str) -> bool:
    """提交并推送盘前报告到 GitHub，确保飞书摘要里的链接立即有效.

    仅提交盘前报告目录的变更（不动代码/配置）。幂等：无变更则跳过。
    SSH 免密，无人值守可行。

    Returns:
        True 表示推送成功（或无变更），False 表示 git 操作失败。
    """
    try:
        # 检测盘前报告目录有无未提交变更
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", REPORT_PATHSPEC],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT), check=True,
        )
        if not status.stdout.strip():
            print(f"[{trade_date}] 盘前报告无变更，跳过 git 提交")
            return True

        subprocess.run(
            ["git", "add", "--", REPORT_PATHSPEC],
            cwd=str(PROJECT_ROOT), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"feat(docs): add {trade_date}_pre_market"],
            cwd=str(PROJECT_ROOT), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push", "origin", "master"],
            cwd=str(PROJECT_ROOT), check=True, capture_output=True,
        )
        print(f"[{trade_date}] ✓ 盘前报告已提交并推送到 GitHub")
        return True
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode() if e.stderr else str(e)
        print(f"[{trade_date}] [WARN] git 提交/推送失败（飞书链接可能暂时无效）: {err[:200]}")
        return False


def push_premarket_feishu(trade_date: str) -> bool:
    """生成盘前报告摘要并推送到飞书群.

    复用 premarket_feishu_summary.build_premarket_feishu_summary 生成纯文本摘要，
    通过 get_feishu_notifier() 推送（企业自建应用，飞书未配置则静默跳过）。

    Returns:
        True 表示推送成功或飞书未配置（静默跳过），False 表示推送失败。
    """
    import asyncio

    try:
        from stockhot.invest_sop.scripts.premarket_feishu_summary import (
            build_premarket_feishu_summary,
        )
        from stockhot.notification.feishu_bot import get_feishu_notifier
    except Exception as e:
        print(f"[{trade_date}] [WARN] 摘要/飞书模块导入失败: {type(e).__name__}: {e}")
        return False

    notifier = get_feishu_notifier()
    if notifier is None:
        print(f"[{trade_date}] 飞书未配置，跳过推送")
        return True  # 未配置不算失败

    try:
        summary = build_premarket_feishu_summary(trade_date)
        asyncio.run(notifier.send_text(summary))
        print(f"[{trade_date}] ✓ 盘前摘要已推送到飞书（{len(summary)} 字符）")
        return True
    except Exception as e:
        print(f"[{trade_date}] [WARN] 飞书推送失败: {type(e).__name__}: {e}")
        return False


def main(argv: list[str] | None = None) -> int:
    """Run daily advisor + report generation + Feishu push. Returns 0 on success, 1 on failure."""
    parser = argparse.ArgumentParser(description="Run daily advisor + report generation")
    parser.add_argument("--date", default=None, help="Trade date (default: today)")
    parser.add_argument(
        "--no-feishu", action="store_true",
        help="跳过飞书推送（默认推送，飞书未配置时自动跳过）",
    )
    args = parser.parse_args(argv)

    trade_date = args.date or date.today().isoformat()

    print(f"[{trade_date}] Starting daily advisor...")
    advisor_ok = run_advisor(trade_date)
    if not advisor_ok:
        print(f"[{trade_date}] Advisor completed with errors")

    print(f"[{trade_date}] Generating premarket report...")
    report_ok = run_report(trade_date)

    # 报告生成成功 → 先 git 推送（保证飞书链接有效），再发飞书摘要
    feishu_ok = True
    if report_ok:
        print(f"[{trade_date}] Committing & pushing report to GitHub...")
        commit_push_report(trade_date)

        if not args.no_feishu:
            print(f"[{trade_date}] Pushing summary to Feishu...")
            feishu_ok = push_premarket_feishu(trade_date)
        else:
            print(f"[{trade_date}] --no-feishu，跳过飞书推送")

    if advisor_ok and report_ok:
        print(f"[{trade_date}] Daily advisor completed successfully")
        return 0
    else:
        print(f"[{trade_date}] Daily advisor completed with partial failures")
        return 1


if __name__ == "__main__":
    sys.exit(main())
