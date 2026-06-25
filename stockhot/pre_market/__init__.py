"""Pre-market information review module.

Generates a pre-market briefing that:
1. Collects overnight news / catalysts (via web search, since Tushare's
   ``news``/``ths_hot`` interfaces require higher-tier permissions)
2. Compares today's macro backdrop with yesterday's (delta detection)
3. Reads yesterday's after-hours summary and flags:
   - Whether overnight news confirms or challenges yesterday's hotspots
   - Risk of profit-taking on yesterday's leaders
   - Individual stock news changes for holdings/watchlist

The module provides data-collection helpers; the actual report writing
is done by the agent following the ``after-hours-review`` skill pattern.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from stockhot.core.logging import logger


# ---------------------------------------------------------------------------
# Yesterday's after-hours summary reader
# ---------------------------------------------------------------------------

DOCS_DIR = Path(__file__).resolve().parents[2] / "docs" / "盘后总结"


@dataclass
class YesterdaySummary:
    """Parsed key points from yesterday's after-hours summary."""
    date: str = ""
    hotspots: list[dict] = field(default_factory=list)  # [{"name": ..., "reason": ...}]
    top_stocks: list[dict] = field(default_factory=list)  # [{"code": ..., "name": ...}]
    risk_notes: list[str] = field(default_factory=list)
    next_day_focus: list[str] = field(default_factory=list)
    market_sentiment: str = ""
    raw_path: str = ""


def read_yesterday_summary(days_back: int = 1) -> YesterdaySummary | None:
    """Read and parse yesterday's after-hours summary markdown.

    Args:
        days_back: How many days to look back (1 = yesterday).

    Returns:
        A YesterdaySummary with parsed hotspots/stocks/risks, or None if
        no summary file exists.
    """
    target_date = datetime.now() - timedelta(days=days_back)
    # Try exact date and nearby dates (weekend handling)
    for offset in range(4):
        d = target_date - timedelta(days=offset)
        fname = d.strftime("%Y-%m-%d") + "_盘后总结.md"
        fpath = DOCS_DIR / fname
        if fpath.exists():
            return _parse_summary(fpath)
    return None


def _parse_summary(fpath: Path) -> YesterdaySummary:
    """Parse a 盘后总结 markdown into structured data."""
    text = fpath.read_text(encoding="utf-8")
    summary = YesterdaySummary(raw_path=str(fpath))

    # Extract date from filename
    m = re.search(r"(\d{4}-\d{2}-\d{2})", fpath.name)
    if m:
        summary.date = m.group(1)

    # Extract hotspots (## 热点 sections)
    # Pattern: "### 热点 N：xxx" or "热点 N：xxx"
    for m in re.finditer(r"热点\s*\d*[：:]\s*(.+?)(?:\n|$)", text):
        summary.hotspots.append({"name": m.group(1).strip()})

    # Extract stock names from tables (| name | or | code name |)
    for m in re.finditer(r"\|\s*([0-9]{6})\s*\|\s*(\S+)", text):
        summary.top_stocks.append({"code": m.group(1), "name": m.group(2)})

    # Extract risk notes (lines containing 风险/炸板/高位/止盈)
    for line in text.splitlines():
        if any(kw in line for kw in ["风险", "炸板", "高位", "止盈", "回调"]):
            clean = line.strip().lstrip("|-*> ")
            if clean and len(clean) > 5:
                summary.risk_notes.append(clean)

    # Extract next-day focus
    in_focus = False
    for line in text.splitlines():
        if "明日关注" in line or "next_day" in line.lower():
            in_focus = True
            continue
        if in_focus:
            if line.startswith("## ") or line.startswith("---"):
                in_focus = False
            elif line.strip().startswith(("1.", "2.", "3.", "4.", "5.", "-")):
                summary.next_day_focus.append(line.strip())

    # Extract sentiment
    for m in re.finditer(r"情绪[：:]\s*(强|中偏强|中性|中偏弱|弱)", text):
        summary.market_sentiment = m.group(1)

    logger.info(
        f"Parsed yesterday summary ({summary.date}): "
        f"{len(summary.hotspots)} hotspots, {len(summary.top_stocks)} stocks, "
        f"{len(summary.risk_notes)} risk notes"
    )
    return summary


# ---------------------------------------------------------------------------
# Macro delta detection
# ---------------------------------------------------------------------------

@dataclass
class MacroDelta:
    """Change in macro indicators since the last snapshot."""
    indicators_changed: list[str] = field(default_factory=list)
    interpretation: str = ""
    risk_level: str = "无变化"  # 无变化/利好/利空


def detect_macro_delta(current_snap, yesterday_snap=None) -> MacroDelta:
    """Compare two macro snapshots and flag changes.

    Args:
        current_snap: Today's MacroSnapshot (from stockhot.macro).
        yesterday_snap: Yesterday's MacroSnapshot. If None, compares
            current values against "expected" ranges.

    Returns:
        A MacroDelta describing what changed.
    """
    delta = MacroDelta()

    if yesterday_snap is None:
        # No yesterday snapshot — just flag key current readings
        if current_snap.pmi is not None:
            if current_snap.pmi < 50:
                delta.indicators_changed.append(f"PMI {current_snap.pmi:.1f} 低于荣枯线（制造业收缩）")
                delta.risk_level = "利空"
            elif current_snap.pmi < 51:
                delta.indicators_changed.append(f"PMI {current_snap.pmi:.1f} 仅微过荣枯线（扩张力度弱）")
        if current_snap.cpi_ppi_scissors is not None and current_snap.cpi_ppi_scissors < -2:
            delta.indicators_changed.append(
                f"CPI-PPI 剪刀差 {current_snap.cpi_ppi_scissors:+.1f}pp（工业品价格承压）"
            )
        delta.interpretation = "无昨日快照对比，仅标注当前关键宏观状态"
        return delta

    # Compare indicators
    fields = [
        ("pmi", "PMI", 0.3),          # threshold for meaningful change
        ("cpi_yoy", "CPI同比", 0.2),
        ("ppi_yoy", "PPI同比", 0.3),
        ("m1_yoy", "M1同比", 0.5),
        ("m2_yoy", "M2同比", 0.5),
        ("shibor_on", "Shibor隔夜", 0.1),
    ]
    for field_name, label, threshold in fields:
        cur = getattr(current_snap, field_name, None)
        old = getattr(yesterday_snap, field_name, None)
        if cur is not None and old is not None:
            change = cur - old
            if abs(change) >= threshold:
                direction = "↑" if change > 0 else "↓"
                delta.indicators_changed.append(
                    f"{label} {old:.1f}→{cur:.1f}（{direction}{abs(change):.1f}）"
                )
                # Assess direction
                if field_name in ("pmi", "m1_yoy", "m2_yoy"):
                    delta.risk_level = "利好" if change > 0 else "利空"
                elif field_name == "shibor_on":
                    delta.risk_level = "利空" if change > 0 else "利好"

    if not delta.indicators_changed:
        delta.interpretation = "宏观指标无显著变化"
    else:
        delta.interpretation = f"宏观{'利好' if delta.risk_level == '利好' else '利空' if delta.risk_level == '利空' else '中性'}：{'; '.join(delta.indicators_changed)}"

    return delta


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def format_pre_market_briefing(
    yesterday: YesterdaySummary | None,
    macro_delta: MacroDelta,
    overnight_news: list[dict],
    stock_news: list[dict],
) -> str:
    """Format the pre-market briefing markdown.

    Args:
        yesterday: Parsed yesterday's after-hours summary (or None).
        macro_delta: Macro indicator changes.
        overnight_news: List of {"title": ..., "source": ..., "impact": ...}.
        stock_news: List of {"code": ..., "name": ..., "news": ..., "impact": ...}.

    Returns:
        A markdown string for the pre-market report.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# {today} 盘前信息整理\n",
        f"> **生成时间**：{datetime.now().strftime('%H:%M')} | "
        f"数据来源：WebSearch 隔夜资讯 + Tushare 宏观数据 + 昨日盘后总结对比\n",
    ]

    # Section 1: Macro delta
    lines.append("## 一、宏观面变化\n")
    if macro_delta.indicators_changed:
        lines.append(f"> **宏观变化判定：{macro_delta.risk_level}**\n")
        for change in macro_delta.indicators_changed:
            lines.append(f"- {change}")
        lines.append(f"\n**解读**：{macro_delta.interpretation}\n")
    else:
        lines.append("> 宏观指标无显著变化（PMI/CPI/PPI/M2 等月度数据通常盘前不变）\n")

    # Section 2: Overnight news
    lines.append("## 二、隔夜资讯要闻\n")
    if overnight_news:
        for item in overnight_news:
            impact = item.get("impact", "")
            impact_emoji = "🔴" if "利空" in impact else "🟢" if "利好" in impact else "⚪"
            lines.append(f"- {impact_emoji} **{item.get('title', '')}**")
            if item.get("source"):
                lines.append(f"  - 来源：{item['source']}")
            if impact:
                lines.append(f"  - 影响：{impact}")
        lines.append("")
    else:
        lines.append("> 暂无重大隔夜资讯\n")

    # Section 3: Yesterday comparison
    if yesterday:
        lines.append(f"## 三、与昨日（{yesterday.date}）盘后对比\n")
        if yesterday.hotspots:
            lines.append("### 昨日热点回顾\n")
            for h in yesterday.hotspots:
                lines.append(f"- {h['name']}")
            lines.append("")

        lines.append("### 预期变化分析\n")
        lines.append("> 以下分析隔夜资讯对昨日热点的确认/挑战程度：\n")
        if overnight_news:
            lines.append(
                "需结合隔夜资讯具体内容判断（见上方第二节），"
                "重点关注：昨日热点是否有新的催化确认，或出现利空证伪。\n"
            )
        else:
            lines.append(
                "无重大隔夜资讯，昨日热点预期延续概率较大，"
                "但需警惕获利回吐风险（尤其是连板高标）。\n"
            )

        if yesterday.risk_notes:
            lines.append("### 昨日已识别风险（今日是否兑现）\n")
            for risk in yesterday.risk_notes[:5]:
                lines.append(f"- ⚠️ {risk}")
            lines.append("")

        if yesterday.next_day_focus:
            lines.append("### 昨日提示的今日关注点\n")
            for f in yesterday.next_day_focus[:5]:
                lines.append(f"- {f}")
            lines.append("")
    else:
        lines.append("## 三、昨日盘后对比\n")
        lines.append("> ⚠️ 未找到昨日盘后总结文件，无法做预期对比\n")

    # Section 4: Individual stock news
    lines.append("## 四、个股消息面\n")
    if stock_news:
        for item in stock_news:
            impact = item.get("impact", "")
            lines.append(
                f"- **{item.get('name', item.get('code', ''))}**"
                f"（{item.get('code', '')}）：{item.get('news', '')}"
            )
            if impact:
                lines.append(f"  - 影响：{impact}")
        lines.append("")
    else:
        lines.append("> 暂无重大个股消息\n")

    # Section 5: Summary
    lines.append("## 五、盘前总结\n")
    lines.append(
        f"**宏观面**：{macro_delta.interpretation}\n\n"
        f"**预期管理**："
    )
    if yesterday and yesterday.hotspots:
        lines.append(
            f"昨日核心热点为 {'、'.join(h['name'][:10] for h in yesterday.hotspots[:3])}。"
        )
    if overnight_news:
        lines.append("隔夜有重大资讯，需重新评估预期。")
    else:
        lines.append("隔夜无重大利空，昨日预期大概率延续。")

    lines.append(
        "\n**风险提示**：以上信息仅供参考，不构成投资建议。"
        "市场有风险，投资需谨慎。\n"
    )

    return "\n".join(lines)
