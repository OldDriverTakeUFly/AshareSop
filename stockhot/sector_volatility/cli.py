"""CLI for sector volatility analysis.

用法：
    python -m stockhot.sector_volatility analyze [--days 1300] [--no-cache] [--top 10]

首次运行约 10 分钟（拉 4650 只成分股日线），后续走缓存约 2 分钟。
"""

from __future__ import annotations

import argparse
import sys

from stockhot.storage.database import init_database
from stockhot.sector_volatility.analyzer import run_sector_volatility_analysis


def _format_analysis(result: dict, top_n: int = 10) -> str:
    """把结果格式化为 markdown。"""
    if result.get("status") == "no_data":
        return "⚠️ 板块波动率数据全部不可用"

    lines = []
    date = result.get("date", "?")
    lines.append(f"# {date} 申万一级板块情绪温度\n")
    lines.append(f"> **摘要**：{result.get('summary', '')}\n")

    # 截面排名表（Top N 恐慌 + Top N 平静）
    ranking = result.get("cross_section_ranking", [])
    if ranking:
        # 恐慌 Top N
        panic_top = ranking[:top_n]
        calm_top = list(reversed(ranking[-top_n:]))

        lines.append(f"## 恐慌程度 Top {top_n}（RV20 分位最高）\n")
        lines.append("| 排名 | 板块 | RV20(%) | 分位 | 恐慌等级 |")
        lines.append("|:---:|------|:---:|:---:|:---:|")
        for i, r in enumerate(panic_top, 1):
            lines.append(
                f"| {i} | {r['name']} | {r['sector_rv20']:.1f} | "
                f"**P{r['rv20_pct']:.0f}** | {r['panic_level']} |"
            )

        lines.append(f"\n## 平静程度 Top {top_n}（RV20 分位最低）\n")
        lines.append("| 排名 | 板块 | RV20(%) | 分位 | 恐慌等级 |")
        lines.append("|:---:|------|:---:|:---:|:---:|")
        for i, r in enumerate(calm_top, 1):
            lines.append(
                f"| {i} | {r['name']} | {r['sector_rv20']:.1f} | "
                f"P{r['rv20_pct']:.0f} | {r['panic_level']} |"
            )

    # P90+ 恐慌板块汇总
    panic_sectors = [r for r in ranking if r["rv20_pct"] >= 90]
    if panic_sectors:
        lines.append(f"\n## ⚠️ P90+ 恐慌板块（{len(panic_sectors)} 个）\n")
        for r in panic_sectors:
            lines.append(f"- **{r['name']}** P{r['rv20_pct']:.0f}（RV20={r['sector_rv20']:.1f}%，{r['panic_level']}）")

    # 全板块速查表（31 个，紧凑）
    lines.append("\n## 全板块速查（31 个申万一级）\n")
    lines.append("| 板块 | RV20(%) | 分位 | 恐慌等级 |")
    lines.append("|------|:---:|:---:|:---:|")
    for r in ranking:
        lines.append(
            f"| {r['name']} | {r['sector_rv20']:.1f} | P{r['rv20_pct']:.0f} | {r['panic_level']} |"
        )

    lines.append(
        f"\n---\n*方法论：`docs/方法论/A股波动率观察框架方法论深度研报.md` §2.2（板块等权 RV + 各板块自身历史分位）*"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m stockhot.sector_volatility",
        description="申万一级板块情绪与恐慌程度（成分股等权 RV + 历史分位）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="计算 31 板块 RV + 分位")
    p_analyze.add_argument("--date", default=None, help="日期标记 YYYY-MM-DD")
    p_analyze.add_argument("--days", type=int, default=1300, help="回溯交易日数（默认 1300≈5年）")
    p_analyze.add_argument("--no-cache", action="store_true", help="忽略缓存，全量重建（首次/调试用）")
    p_analyze.add_argument("--top", type=int, default=10, help="恐慌/平静 Top N（默认 10）")

    args = parser.parse_args(argv)
    init_database()

    if args.command == "analyze":
        print("⚠️ 板块 RV 计算量大（首次约 10 分钟，缓存后约 2 分钟），请耐心等待...\n", file=sys.stderr)
        result = run_sector_volatility_analysis(
            date=args.date,
            days=args.days,
            use_cache=not args.no_cache,
        )
        print(_format_analysis(result, top_n=args.top))
        return 0 if result.get("status") == "success" else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
