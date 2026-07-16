"""eod_review 报告生成器 — 将 EODReviewResult 渲染为结构化 markdown.

产出 ``docs/盘后复盘/{date}_量化复盘.md``（与 after-hours-review 的
``docs/盘后总结/`` 并存，不冲突）。

报告核心章节：
1. 情绪温度计（多维交叉）⭐
2. 涨停归因（量化分类）⭐
3. 板块涨幅（结构化）
4. N 日趋势对比 ⭐
5. 跌停池风险警示
6. 数据完整性
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from stockhot.core.config import STORAGE_DIR
from stockhot.core.logging import logger
from stockhot.eod_review.engine import EODReviewResult

_TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

# 报告输出目录（项目 docs/盘后复盘/）
_REPORT_DIR: Path = STORAGE_DIR.parent / "docs" / "盘后复盘"


def generate_report(
    result: EODReviewResult,
    *,
    output_path: Path | None = None,
) -> Path:
    """渲染 EODReviewResult 为 markdown 文件.

    Returns
    -------
    Path
        写入的文件路径。
    """
    date = result.trade_date
    pretty_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    now_str = datetime.now(_TZ_SHANGHAI).strftime("%Y-%m-%d %H:%M")

    md = _render(result, pretty_date, now_str)

    path = output_path or (_REPORT_DIR / f"{pretty_date}_量化复盘.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    logger.info(f"[EOD] 报告已生成: {path}")
    return path


def _render(result: EODReviewResult, pretty_date: str, now_str: str) -> str:
    lines: list[str] = []
    snap = result.snapshot

    lines.append(f"# {pretty_date} 量化复盘")
    lines.append("")
    lines.append(f"> **生成时间**：{now_str} | **数据来源**：Tushare 日线直连（eod_review 引擎）")
    lines.append(
        "> "
        "本报告为独立量化复盘引擎产出，直连 Tushare 拉日线，不依赖 daily-market-scan 采集。"
        "与盘后总结（after-hours-review）互补：本文侧重量化归因与情绪温度计，盘后总结侧重催化搜索。"
    )
    lines.append("")

    # ═══ §1 情绪温度计 ═══
    lines.append("## 一、情绪温度计（多维交叉）⭐")
    lines.append("")
    if result.sentiment:
        s = result.sentiment
        lines.append(f"**综合情绪分：{s.score}/100（{s.label}）**")
        lines.append("")
        lines.append("| 维度 | 信号 |")
        lines.append("|------|------|")
        lines.append(f"| 涨跌停结构 | {s.limit_signal} |")
        lines.append(f"| 融资融券 | {s.margin_signal} |")
        lines.append(f"| 北向资金 | {s.north_signal} |")
        lines.append(f"| 大宗交易 | {s.block_signal} |")
        lines.append("")
        if s.divergence:
            lines.append(f"> **⚠️ 维度背离**：{s.divergence}")
            lines.append("")
    else:
        lines.append("> ⚠️ 情绪数据不可用（融资融券/北向/大宗均缺失）")
        lines.append("")

    # ═══ §2 涨停归因 ═══
    lines.append("## 二、涨停归因（量化分类）⭐")
    lines.append("")
    if result.limit_up_attributions:
        type_counts = Counter(a.attribution_type for a in result.limit_up_attributions)
        lines.append(f"共 **{len(result.limit_up_attributions)}** 只涨停，归因分类：")
        lines.append("")
        lines.append("| 归因类型 | 数量 | 代表个股 |")
        lines.append("|----------|:---:|------|")
        for atype, count in type_counts.most_common():
            samples = [
                f"{a.name}({a.sector})"
                for a in result.limit_up_attributions
                if a.attribution_type == atype
            ][:3]
            lines.append(f"| {atype} | {count} | {'、'.join(samples)} |")
        lines.append("")

        # 连板梯队
        relay_stocks = sorted(
            [a for a in result.limit_up_attributions if a.consecutive_boards >= 2],
            key=lambda x: x.consecutive_boards,
            reverse=True,
        )
        if relay_stocks:
            lines.append("**连板梯队**（情绪温度计）：")
            lines.append("")
            lines.append("| 连板数 | 个股 |")
            lines.append("|:---:|------|")
            # 按连板数分组
            height_groups: dict[int, list[str]] = {}
            for a in relay_stocks:
                height_groups.setdefault(a.consecutive_boards, []).append(a.name)
            for height in sorted(height_groups.keys(), reverse=True):
                lines.append(f"| {height}板 | {'、'.join(height_groups[height])} |")
            lines.append("")
    else:
        lines.append("> ⚠️ 涨停归因数据不可用")
        lines.append("")

    # ═══ §3 板块涨幅 ═══
    lines.append("## 三、板块涨幅（结构化）")
    lines.append("")
    if result.sector_performance:
        lines.append("**强势板块 Top 10**：")
        lines.append("")
        lines.append("| 排名 | 板块 | 等权均涨幅 | 涨停 | 跌停 | 成分股 | 分歧度 | 资金净流入(亿) |")
        lines.append("|:---:|------|:---:|:---:|:---:|:---:|:---:|:---:|")
        for i, s in enumerate(result.sector_performance[:10], 1):
            lines.append(
                f"| {i} | {s.name} | {s.mean_pct:+.2f}% | {s.limit_up_count} | "
                f"{s.limit_down_count} | {s.member_count} | {s.dispersion:.1f}% | "
                f"{s.net_inflow:+.1f} |"
            )
        lines.append("")

        lines.append("**弱势板块 Bottom 10**：")
        lines.append("")
        lines.append("| 排名 | 板块 | 等权均涨幅 | 涨停 | 跌停 | 成分股 |")
        lines.append("|:---:|------|:---:|:---:|:---:|:---:|")
        for i, s in enumerate(result.sector_performance[-10:], 1):
            lines.append(
                f"| {i} | {s.name} | {s.mean_pct:+.2f}% | {s.limit_up_count} | "
                f"{s.limit_down_count} | {s.member_count} |"
            )
        lines.append("")
    else:
        lines.append("> ⚠️ 板块数据不可用")
        lines.append("")

    # ═══ §4 N 日趋势 ═══
    lines.append("## 四、N 日趋势对比 ⭐")
    lines.append("")
    if result.trend:
        t = result.trend
        if t.has_history:
            lines.append(f"（对比近 {t.window} 日均值）")
            lines.append("")
            lines.append("| 指标 | 今日 | 近{}日均值 |".format(t.window))
            lines.append("|------|:---:|:---:|")
            lines.append(f"| 涨停数 | {t.today_limit_up} | {t.avg_limit_up:.1f} |")
            lines.append(f"| 跌停数 | {t.today_limit_down} | {t.avg_limit_down:.1f} |")
            lines.append("")

            if t.sentiment_trend:
                lines.append("**情绪分序列**：")
                lines.append("")
                for item in t.sentiment_trend:
                    lines.append(
                        f"- {item['date']}: {item['score']}/100（{item['label']}）"
                        + (f"，北向{item['north_net']:+.0f}亿" if item.get("north_net") else "")
                    )
                lines.append("")
        else:
            lines.append(
                f"> ⚠️ 历史数据不足（需积累 {t.window} 个交易日的 eod_sentiment 记录）。"
                f"今日涨停 {t.today_limit_up} 只，跌停 {t.today_limit_down} 只。"
                "持续运行本引擎后，N 日趋势对比将自动可用。"
            )
            lines.append("")

        if t.height_distribution:
            lines.append("**当日连板高度分布**：")
            lines.append("")
            for height, count in t.height_distribution.items():
                lines.append(f"- {height}板：{count}只")
            lines.append("")
    else:
        lines.append("> ⚠️ 趋势数据不可用")
        lines.append("")

    # ═══ §5 跌停池 ═══
    lines.append("## 五、跌停池风险警示")
    lines.append("")
    if snap.limit_down:
        lines.append(f"共 **{len(snap.limit_down)}** 只跌停。")
        lines.append("")
        # 按板块聚合跌停
        down_sectors = Counter(s.get("sector", "未知") for s in snap.limit_down)
        lines.append("**跌停板块分布**：")
        lines.append("")
        lines.append("| 板块 | 跌停数 | 代表个股 |")
        lines.append("|------|:---:|------|")
        for sector, count in down_sectors.most_common(8):
            stocks = [
                s["name"]
                for s in snap.limit_down
                if s.get("sector") == sector
            ][:3]
            lines.append(f"| {sector} | {count} | {'、'.join(stocks)} |")
        lines.append("")
    else:
        lines.append("> 无跌停股（或数据不可用）")
        lines.append("")

    # ═══ §6 数据完整性 ═══
    lines.append("## 六、数据完整性")
    lines.append("")
    lines.append("| 维度 | 状态 |")
    lines.append("|------|------|")
    dims = [
        ("全市场日线", snap.daily),
        ("日线+行业", snap.daily_with_industry),
        ("估值(daily_basic)", snap.daily_basic),
        ("涨停池", snap.limit_up),
        ("炸板池", snap.broken),
        ("跌停池", snap.limit_down),
        ("板块资金流", snap.moneyflow_sector),
        ("龙虎榜", snap.dragon_tiger),
        ("北向资金", snap.north_flow),
        ("融资融券", snap.margin),
        ("大宗交易", snap.block_trade),
    ]
    for name, data in dims:
        try:
            length = len(data)
        except TypeError:
            length = 0
        status = f"✅ {length} 行" if length > 0 else "❌ 数据不可用"
        lines.append(f"| {name} | {status} |")
    lines.append("")

    if result.errors:
        lines.append(f"> ⚠️ 分析层失败维度：{', '.join(result.errors)}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "> **免责声明**：本报告为量化数据梳理与归因分析，不构成任何投资建议。"
        "归因基于日线量价特征，事件驱动类需结合 after-hours-review 的催化搜索确认。"
        "市场有风险，投资需谨慎。"
    )
    lines.append("")

    return "\n".join(lines)
