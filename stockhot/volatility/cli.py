"""CLI for the volatility observation module.

用法：
    python -m stockhot.volatility analyze [--date YYYY-MM-DD] [--index 000001.SH] [--days 1300]

输出 markdown 表格：指数 | RV20 | RV20分位 | RV60 | RV60分位 | 恐慌等级，
以及市场层 iVIX/V/R 摘要。
"""

from __future__ import annotations

import argparse
import sys

from stockhot.storage.database import init_database
from stockhot.volatility.analyzer import (
    DEFAULT_INDICES,
    INDEX_NAMES,
    run_volatility_analysis,
)


def _format_analysis(result: dict) -> str:
    """把 run_volatility_analysis 结果格式化为 markdown。"""
    if result.get("status") == "no_data":
        return "⚠️ 波动率数据全部不可用（所有指数采集失败）"

    lines = []
    date = result.get("date", "?")
    lines.append(f"# {date} 波动率观察\n")
    lines.append(f"> **摘要**：{result.get('summary', '')}\n")

    # 指数表
    lines.append("## 指数已实现波动率（Layer 1+2）\n")
    lines.append("| 指数 | RV20(%) | RV20分位 | RV60(%) | RV60分位 | 恐慌等级 |")
    lines.append("|------|:---:|:---:|:---:|:---:|:---:|")
    for ts_code in DEFAULT_INDICES:
        r = result.get("indices", {}).get(ts_code, {})
        if r.get("status") != "success":
            lines.append(f"| {r.get('name', ts_code)} | — | — | — | — | 数据不可用 |")
            continue
        lines.append(
            f"| {r['name']} | {r['rv20']:.1f} | P{r['rv20_pct']:.0f} | "
            f"{r['rv60']:.1f} | P{r['rv60_pct']:.0f} | **{r['panic_level']}** |"
        )

    # 市场层 iVIX/V/R
    market = result.get("market", {})
    if market.get("status") == "success":
        lines.append("\n## 市场隐含波动率（Layer 5）\n")
        lines.append("| 指标 | 数值 |")
        lines.append("|------|:---:|")
        lines.append(f"| iVIX（50ETF QVIX） | **{market['ivix_current']:.1f}** |")
        lines.append(f"| iVIX 历史分位 | P{market.get('ivix_pct', 0):.0f} |")
        lines.append(f"| iVIX 恐慌等级 | {market.get('ivix_panic_level', '?')} |")
        vr = market.get("vr_ratio")
        vr_str = f"{vr:.2f}" if vr is not None else "N/A"
        lines.append(f"| V/R 比率（IV/RV） | {vr_str} |")
        rv50 = market.get("rv50_approx")
        rv50_str = f"{rv50:.1f}%" if rv50 is not None else "N/A"
        lines.append(f"| 沪深300 RV20（V/R 分母） | {rv50_str} |")

    lines.append("\n---\n*方法论：`docs/方法论/A股波动率观察框架方法论深度研报.md`*")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m stockhot.volatility",
        description="A 股波动率观察（中国版 VIX 五层体系）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="计算 RV 分位 + iVIX/V/R")
    p_analyze.add_argument(
        "--index",
        action="append",
        default=None,
        help="指定指数代码（可多次指定，默认 5 大指数）",
    )
    p_analyze.add_argument("--date", default=None, help="日期标记 YYYY-MM-DD")
    p_analyze.add_argument("--days", type=int, default=1300, help="回溯交易日数（默认 1300≈5年）")

    args = parser.parse_args(argv)

    # 对齐 advisor/cli.py：调用前确保 DB schema 就绪（save_daily_data 依赖）
    init_database()

    if args.command == "analyze":
        indices = args.index if args.index else DEFAULT_INDICES
        for code in indices:
            if code not in INDEX_NAMES:
                print(f"⚠️ 警告：{code} 不在已知指数列表，仍会尝试分析", file=sys.stderr)

        result = run_volatility_analysis(date=args.date, indices=indices, days=args.days)
        print(_format_analysis(result))

        if result.get("status") != "success":
            return 1
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
