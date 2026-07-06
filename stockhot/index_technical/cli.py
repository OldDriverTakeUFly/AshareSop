"""CLI for index technical analysis.

独立命令行工具，手动跑单指数/全指数分析，打印 markdown 表。

用法：
    python -m stockhot.index_technical.cli analyze                 # 全默认 4 指数
    python -m stockhot.index_technical.cli analyze --index 000300.SH  # 指定指数
    python -m stockhot.index_technical.cli analyze --date 2026-07-06 # 指定日期标记
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from stockhot.index_technical.analyzer import (
    DEFAULT_INDICES,
    INDEX_NAMES,
    run_index_technical_analysis,
)

# 阶段对应的 emoji 标记（用于表格视觉区分）
STAGE_EMOJI = {
    "主升浪": "🚀",
    "上涨中回调": "↩️",
    "高位震荡筑顶": "⚠️",
    "主跌浪": "💥",
    "下跌中反弹": "🔄",
    "低位筑底": "🌱",
    "阶段不明确": "❓",
}


def _format_analysis(result: dict) -> str:
    """把 run_index_technical_analysis 的结果格式化为 markdown。"""
    lines = []
    date = result.get("date", "?")
    lines.append(f"# 大盘技术面分析（{date}）\n")

    if result.get("status") != "success":
        lines.append(f"> ⚠️ 数据不可用：{result.get('summary', '')}\n")
        return "\n".join(lines)

    indices = result.get("indices", {})

    # 主表
    lines.append("## 指数技术面一览\n")
    lines.append("| 指数 | 收盘 | 涨跌% | 技术评分 | 状态 | 阶段 | 置信度 | MA20 | 支撑 | 压力 | 盘前预期 |")
    lines.append("|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|------|------|------|")
    for ts_code, r in indices.items():
        if r.get("status") != "success":
            lines.append(f"| {r.get('name', ts_code)} | — | — | — | — | 数据不可用 | — | — | — | — | — |")
            continue
        emoji = STAGE_EMOJI.get(r["stage"], "")
        stage_str = f"{emoji} {r['stage']}"
        support_str = "/".join(str(s) for s in r.get("support", [])[:2]) or "—"
        resist_str = "/".join(str(s) for s in r.get("resistance", [])[-2:]) or "—"
        lines.append(
            f"| {r['name']} | {r['close']} | {r['pct_chg']:+.2f}% | "
            f"{r['technical_score']} | {r['technical_state']} | "
            f"**{stage_str}** | {r['stage_confidence']}% | "
            f"{r.get('ma20') or '—'} | {support_str} | {resist_str} | "
            f"{r['expected_action']} |"
        )

    # 各指数阶段详情
    lines.append("\n## 阶段判定详情\n")
    for ts_code, r in indices.items():
        if r.get("status") != "success":
            continue
        emoji = STAGE_EMOJI.get(r["stage"], "")
        lines.append(f"### {emoji} {r['name']}（{ts_code}）—— {r['stage']}（置信度 {r['stage_confidence']}%）\n")
        lines.append(f"- 收盘 **{r['close']}**（{r['pct_chg']:+.2f}%），技术评分 **{r['technical_score']}/100**（{r['technical_state']}）")
        lines.append(f"- MA5={r.get('ma5') or '—'} / MA10={r.get('ma10') or '—'} / MA20={r.get('ma20') or '—'} / MA60={r.get('ma60') or '—'}")
        lines.append(f"- MACD柱={r.get('macd_hist') or '—'} / RSI={r.get('rsi') or '—'} / KDJ-K={r.get('kdj_k') or '—'}")
        lines.append(f"- 布林带：上轨 {r.get('boll_upper') or '—'} / 下轨 {r.get('boll_lower') or '—'}")
        lines.append(f"- 支撑位：{r.get('support') or '—'}")
        lines.append(f"- 压力位：{r.get('resistance') or '—'}")
        lines.append(f"- **命中条件**：{', '.join(r['reasons']) if r['reasons'] else '—'}")
        lines.append(f"- **盘前预期**：{r['expected_action']}")
        lines.append(f"- **信心度**：{r['confidence_score']}/5 分\n")

    # 整体定性
    lines.append("## 整体技术面定性\n")
    lines.append(f"> {result.get('summary', '')}\n")

    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m stockhot.index_technical.cli",
        description="大盘指数技术面分析（6 阶段趋势识别）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="运行指数技术面分析")
    p_analyze.add_argument(
        "--index",
        action="append",
        default=None,
        help="指定指数代码（可多次指定，默认 4 大指数）",
    )
    p_analyze.add_argument("--date", default=None, help="日期标记 YYYY-MM-DD（仅用于结果标识）")
    p_analyze.add_argument("--days", type=int, default=120, help="拉取交易日数（默认 120）")

    args = parser.parse_args(argv)

    if args.command == "analyze":
        indices = args.index if args.index else DEFAULT_INDICES
        # 校验指数代码
        for code in indices:
            if code not in INDEX_NAMES:
                print(f"⚠️ 警告：{code} 不在已知指数列表，仍会尝试分析", file=sys.stderr)

        result = run_index_technical_analysis(date=args.date, indices=indices, days=args.days)
        print(_format_analysis(result))

        # 退出码：全失败返回 1
        if result.get("status") != "success":
            return 1
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
