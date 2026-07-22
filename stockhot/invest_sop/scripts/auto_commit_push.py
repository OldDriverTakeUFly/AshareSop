#!/usr/bin/env python
"""自动提交并推送生成的报告文件到 GitHub — 幂等，无变更则静默退出.

Designed for crontab — 每日盘后（19:00）运行。扫描指定报告目录下是否有未提交的
变更，有则 add + commit + push，无则静默退出（return 0）。

监控的报告产物（与 .gitignore 配合，仅这些路径纳入自动同步）：
- docs/盘后总结/*.md          盘后总结（after-hours-review skill）
- docs/盘后复盘/*.md          盘后复盘
- docs/回测记录/*.md          回测记录
- storage/files/reports/invest_sop/*.md   盘前报告
- davis_analyzer/studies/*深度研报.md     深度研报（report_generator）

⚠️ 只处理上述报告路径——代码、配置、数据库等变更一律不碰，避免误提交半成品代码。
    代码类变更仍由 agent/人工显式 commit。

Usage:
    .venv/bin/python stockhot/invest_sop/scripts/auto_commit_push.py [--dry-run]

Crontab (每日盘后 19:00):
    0 19 * * 1-5 cd /path && PYTHONPATH=/path \\
        .venv/bin/python stockhot/invest_sop/scripts/auto_commit_push.py \\
        >> stockhot/invest_sop/logs/auto_sync.log 2>&1
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

# 项目根（脚本位于 stockhot/invest_sop/scripts/，上溯 3 层）
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 纳入自动同步的报告路径模式（相对仓库根，传给 git 的 pathspec）
REPORT_PATHSPECS: list[str] = [
    "docs/盘后总结/*.md",
    "docs/盘后复盘/*.md",
    "docs/回测记录/*.md",
    "storage/files/reports/invest_sop/*.md",
    "davis_analyzer/studies/*深度研报.md",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="自动提交并推送报告文件到 GitHub")
    parser.add_argument("--dry-run", action="store_true",
                        help="只检测不提交/推送（打印将要处理的文件）")
    args = parser.parse_args(argv)

    today = date.today().isoformat()
    print(f"[{today}] === 报告自动同步开始 ===")

    # ── 交易日校验（非交易日跳过——盘后报告只在交易日产生）──
    try:
        from stockhot.invest_sop.utils.trading_calendar import is_trading_day
        if not is_trading_day(today):
            print(f"[{today}] 非交易日，跳过自动同步")
            return 0
    except Exception as e:
        # 日历失败不阻断（宁可多跑一次，也不因日历故障漏推）
        print(f"[WARN] 交易日校验失败（{e}），继续执行")

    # ── 检测未提交的报告文件变更 ──
    changed = _detect_report_changes()
    if not changed:
        print(f"[{today}] 无报告文件变更，静默退出")
        return 0

    print(f"[{today}] 检测到 {len(changed)} 个报告文件变更：")
    for f in changed:
        print(f"    {f}")

    if args.dry_run:
        print("[DRY-RUN] 不提交/推送")
        return 0

    # ── add + commit + push（错误隔离，失败返回非零）──
    try:
        _git_add(changed)
        _git_commit(today, changed)
        _git_push()
        print(f"[{today}] ✓ 已同步 {len(changed)} 个报告文件到 GitHub")
        return 0
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] git 操作失败（returncode={e.returncode}）")
        print(f"        stdout: {e.stdout}")
        print(f"        stderr: {e.stderr}")
        return 1
    except Exception as e:
        print(f"[ERROR] 同步失败：{type(e).__name__}: {e}")
        return 1


def _detect_report_changes() -> list[str]:
    """用 git status --porcelain 检测报告路径下的未提交变更，返回文件路径列表."""
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", *REPORT_PATHSPECS],
        capture_output=True, text=True, check=True,
        cwd=str(PROJECT_ROOT),
    )
    changed: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        # porcelain 格式：XY <path>，X/Y 是状态码，path 前有 3 个字符
        # 处理带引号的中文路径（如 "docs/\343..."）
        path = line[3:].strip().strip('"')
        if path:
            changed.append(path)
    return changed


def _git_add(paths: list[str]) -> None:
    """git add 指定的报告文件."""
    result = subprocess.run(
        ["git", "add", "--", *paths],
        capture_output=True, text=True, check=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.stdout:
        print(result.stdout, end="")


def _git_commit(today: str, paths: list[str]) -> None:
    """git commit，消息含日期和文件数."""
    message = f"feat(docs): auto-sync 报告 {today}（{len(paths)} 个文件）"
    result = subprocess.run(
        ["git", "commit", "-m", message],
        capture_output=True, text=True, check=True,
        cwd=str(PROJECT_ROOT),
    )
    # commit 成功输出到 stdout（如 "[master xxx] message"）
    for line in result.stdout.splitlines():
        if line.strip():
            print(f"    {line}")
    if result.stderr:
        for line in result.stderr.splitlines():
            if line.strip():
                print(f"    {line}")


def _git_push() -> None:
    """git push 到 origin master（SSH 免密，见 install.sh 文档）."""
    result = subprocess.run(
        ["git", "push", "origin", "master"],
        capture_output=True, text=True, check=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        # git push 进度信息走 stderr，非错误（check=True 已过滤真正的失败）
        for line in result.stderr.splitlines():
            if line.strip():
                print(f"    {line}")


if __name__ == "__main__":
    sys.exit(main())
