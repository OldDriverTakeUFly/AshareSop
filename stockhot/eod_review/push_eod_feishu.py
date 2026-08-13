#!/usr/bin/env python
"""盘后总结飞书推送 + GitHub 同步脚本.

由盘后总结 cron（每工作日 18:30）在 after-hours-review skill 生成报告后调用。
流程：
1. 提交并推送 docs/盘后总结/{date}_盘后总结.md 到 GitHub（保证飞书链接有效）
2. 从 SQLite 读取当日盘面数据，生成飞书纯文本摘要
3. 推送到飞书群（企业自建应用，未配置则静默跳过）

与 run_daily_advisor.py（盘前）平行，但盘后总结报告由 skill（LLM）生成，
本脚本只负责「搬运已生成的 md + 推送摘要」，不重新生成报告。

Usage:
    .venv/bin/python -m stockhot.eod_review.push_eod_feishu [--date YYYY-MM-DD] [--no-feishu] [--force]

幂等：git 提交与飞书推送均按交易日去重。当日已推过飞书则跳过（--force 可强制重推），
避免 agent 在一个 session 内反复调用导致飞书群被刷屏。
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

# 副作用 import：配置 loguru 文件 sink，使 feishu_bot 的 send_text 日志写入
# logs/stockhot_*.log（否则只进 stderr，飞书发送不可追溯）。必须在 feishu_bot 使用
# logger 之前完成——loguru 的 logger 是进程级全局单例。
from stockhot.core.logging import logger  # noqa: F401

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PYTHON = str(PROJECT_ROOT / ".venv" / "bin" / "python")

# 盘后总结在仓库中的相对路径（git add 的 pathspec）
_REPORT_PATHSPEC = "docs/盘后总结/"
_GITHUB_REPO = "OldDriverTakeUFly/AshareSop"
_GITHUB_BRANCH = "master"

# 飞书推送幂等锁目录：每个交易日一个 marker 文件，防止 agent 在一个 session 内
# 重复调用本脚本导致飞书群被刷屏（git 提交幂等，但飞书发送原本不幂等）。
# 放在 logs/ 下（已被 .gitignore），不污染仓库。
_PUSH_LOCK_DIR = PROJECT_ROOT / "logs" / ".eod_feishu_push"


def commit_push_report(trade_date: str) -> bool:
    """提交并推送盘后总结到 GitHub（仅 docs/盘后总结/ 目录，不动其他改动）.

    幂等：无变更则跳过。SSH 免密无人值守。

    Returns:
        True 表示推送成功或无变更，False 表示 git 操作失败。
    """
    try:
        # 仅检测盘后总结目录的未提交变更（避免误提交其他无关改动）
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", _REPORT_PATHSPEC],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT), check=True,
        )
        if not status.stdout.strip():
            print(f"[{trade_date}] 盘后总结无变更，跳过 git 提交")
            return True

        subprocess.run(
            ["git", "add", "--", _REPORT_PATHSPEC],
            cwd=str(PROJECT_ROOT), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"feat(docs): add {trade_date}_盘后总结"],
            cwd=str(PROJECT_ROOT), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push", "origin", "master"],
            cwd=str(PROJECT_ROOT), check=True, capture_output=True,
        )
        print(f"[{trade_date}] ✓ 盘后总结已提交并推送到 GitHub")
        return True
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode() if e.stderr else str(e)
        print(f"[{trade_date}] [WARN] git 提交/推送失败（飞书链接可能暂时无效）: {err[:200]}")
        return False


def build_eod_feishu_summary(trade_date: str) -> str:
    """生成盘后总结的飞书纯文本摘要.

    直接从 SQLite 读取当日盘面数据（与 after-hours-review skill 同源），
    格式化为飞书友好的纯文本（emoji + 换行 + 缩进，约 600-900 字）。

    Args:
        trade_date: 报告日期 YYYY-MM-DD

    Returns:
        飞书纯文本摘要（含 GitHub 完整报告链接）
    """
    weekday_map = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}
    dt = datetime.strptime(trade_date, "%Y-%m-%d")
    weekday = weekday_map[dt.weekday()]

    # ── 读 SQLite 当日数据 ──
    from stockhot.storage.database import get_daily_data

    data = get_daily_data(trade_date)
    limit_up = data.get("limit_up_pool", [])
    broken = data.get("broken_pool", [])
    limit_down = data.get("limit_down_pool", [])
    ffm = data.get("fund_flow_market", [])
    vol = data.get("volatility", {})

    # 大盘主力资金流（时序，取最后一条 = 当日）
    main_net = ffm[-1].get("main_net") if ffm else None

    # 波动率
    indices_vol = vol.get("indices", {})
    p90_count = sum(
        1 for v in indices_vol.values() if (v.get("rv20_pct") or 0) >= 90
    )
    # 最恐慌指数
    most_panic = max(
        indices_vol.values(),
        key=lambda x: x.get("rv20_pct") or 0,
        default={},
    )
    mkt = vol.get("market", {})

    # 涨停高度股（连板数降序 Top 3）
    lu_sorted = sorted(
        limit_up, key=lambda x: x.get("consecutive_boards") or 1, reverse=True
    )
    top_boards = lu_sorted[:3]

    # 板块资金流：主力流出 Top 3（砸盘）+ 流入 Top 3（避险）
    fs = data.get("fund_flow_sector", [])
    fs_by_outflow = sorted(
        fs, key=lambda x: x.get("main_net") or 0
    )  # 升序，最负在前
    fs_by_inflow = sorted(fs, key=lambda x: x.get("main_net") or 0, reverse=True)

    # ── 组装摘要 ──
    lines: list[str] = [
        f"📊 盘后总结 | {trade_date} 星期{weekday}",
        "",
        "🎯 市场概况",
        f"涨停 {len(limit_up)} | 炸板 {len(broken)} | 跌停 {len(limit_down)}",
    ]
    if main_net is not None:
        direction = "净流入" if main_net >= 0 else "净流出"
        lines.append(f"大盘主力{direction} {abs(main_net):.1f} 亿")

    # 波动率温度（≥3 指数 P90+ 为系统性恐慌，1-2 为结构性恐慌，0 为无恐慌区）
    lines.append("")
    lines.append("🌡 波动率温度")
    if p90_count >= 3:
        panic_label = "系统性恐慌"
    elif p90_count >= 1:
        panic_label = "结构性恐慌"
    else:
        panic_label = "无恐慌区"
    lines.append(f"{p90_count}/5 指数 P90+ → {panic_label}")
    if p90_count > 0:
        name = most_panic.get("name", "?")
        rv = most_panic.get("rv20_pct", 0)
        lines.append(f"最恐慌 {name} P{rv:.0f}")
    ivix = mkt.get("ivix_current")
    vr = mkt.get("vr_ratio")
    if ivix is not None and vr is not None:
        opt = "期权便宜" if vr < 0.9 else ("期权偏贵" if vr > 1.3 else "期权合理")
        lines.append(f"iVIX={ivix} V/R={vr}（{opt}）")

    # 高度股
    lines.append("")
    lines.append("🏆 连板梯队（高度股）")
    if top_boards:
        for r in top_boards:
            b = int(r.get("consecutive_boards") or 1)
            lines.append(f"  · {r.get('name', '?')}({r.get('code', '?')}) {b}板 {r.get('sector', '?')}")
    else:
        lines.append("  · 无涨停")

    # 资金砸盘 Top 3
    lines.append("")
    lines.append("💸 主力流出 Top 3（砸盘）")
    for r in fs_by_outflow[:3]:
        amt = r.get("main_net")
        if amt is not None:
            lines.append(f"  · {r.get('name', '?')} {amt:.1f}亿")

    # 资金流入 Top 3
    lines.append("")
    lines.append("💰 主力流入 Top 3（避险）")
    for r in fs_by_inflow[:3]:
        amt = r.get("main_net")
        if amt is not None and amt > 0:
            lines.append(f"  · {r.get('name', '?')} +{amt:.1f}亿")

    # GitHub 完整报告链接
    fname = f"{trade_date}_盘后总结.md"
    # URL encode 中文名（盘后总结）
    from urllib.parse import quote
    rel_path = f"docs/盘后总结/{fname}"
    lines.append("")
    lines.append("📄 完整报告")
    lines.append(f"  https://github.com/{_GITHUB_REPO}/blob/{_GITHUB_BRANCH}/{quote(rel_path)}")
    lines.append("")
    lines.append("（以上为盘后摘要，完整报告含板块排名/热点深度/游资动向/明日关注，详见链接）")

    return "\n".join(lines)


def _push_marker_path(trade_date: str) -> Path:
    """当日飞书推送幂等锁 marker 文件路径."""
    return _PUSH_LOCK_DIR / f"{trade_date}.ok"


def _is_feishu_pushed(trade_date: str) -> bool:
    """当日飞书摘要是否已成功推送过."""
    return _push_marker_path(trade_date).exists()


def _mark_feishu_pushed(trade_date: str) -> None:
    """记录当日飞书推送成功（仅在 send_text 成功后调用，失败不落锁以便重试）."""
    marker = _push_marker_path(trade_date)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"pushed at {datetime.now().isoformat()}\n", encoding="utf-8")


def push_eod_feishu(trade_date: str, *, force: bool = False) -> bool:
    """生成盘后摘要并推送到飞书群（幂等：当日已推过则跳过）.

    幂等保护使每个交易日只推一次飞书。即使 agent 在一个 session 内反复调用本脚本
    （历史上发生过 14 次循环），也只会发出一条消息——与 commit_push_report 的 git
    幂等行为对齐。force=True 可强制重推（数据刷新后手动触发）。

    Returns:
        True 表示推送成功、当日已推过（跳过）或飞书未配置（静默跳过）；
        False 表示推送失败。
    """
    # 幂等检查：当日已成功推送过则跳过（除非 force）
    if not force and _is_feishu_pushed(trade_date):
        print(f"[{trade_date}] 当日飞书摘要已推送过，跳过（--force 可强制重推）")
        return True

    try:
        from stockhot.notification.feishu_bot import get_feishu_notifier
    except Exception as e:
        print(f"[{trade_date}] [WARN] 飞书模块导入失败: {type(e).__name__}: {e}")
        return False

    notifier = get_feishu_notifier()
    if notifier is None:
        print(f"[{trade_date}] 飞书未配置，跳过推送")
        return True  # 未配置不算失败（也不落 marker，配置后仍可推）

    try:
        summary = build_eod_feishu_summary(trade_date)
        asyncio.run(notifier.send_text(summary))
        _mark_feishu_pushed(trade_date)  # 只在发送成功后落锁
        print(f"[{trade_date}] ✓ 盘后摘要已推送到飞书（{len(summary)} 字符）")
        return True
    except Exception as e:
        print(f"[{trade_date}] [WARN] 飞书推送失败: {type(e).__name__}: {e}")
        return False


def main(argv: list[str] | None = None) -> int:
    """盘后总结 GitHub 同步 + 飞书推送. Returns 0 on success, 1 on failure."""
    parser = argparse.ArgumentParser(description="盘后总结 GitHub 同步 + 飞书推送")
    parser.add_argument("--date", default=None, help="Trade date (default: today)")
    parser.add_argument(
        "--no-feishu", action="store_true",
        help="跳过飞书推送（默认推送，飞书未配置时自动跳过）",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="强制重推飞书摘要（默认当日已推过则跳过，幂等保护）",
    )
    args = parser.parse_args(argv)

    trade_date = args.date or date.today().isoformat()

    print(f"[{trade_date}] Committing & pushing 盘后总结 to GitHub...")
    commit_push_report(trade_date)

    if not args.no_feishu:
        print(f"[{trade_date}] Pushing 盘后摘要 to Feishu...")
        push_eod_feishu(trade_date, force=args.force)
    else:
        print(f"[{trade_date}] --no-feishu，跳过飞书推送")

    return 0


if __name__ == "__main__":
    sys.exit(main())
