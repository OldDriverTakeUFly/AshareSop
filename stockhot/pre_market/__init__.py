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

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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


# ---------------------------------------------------------------------------
# News recency classification + recent overseas trend (T0/T1/T2 framework)
#
# See `.agents/skills/invest-sop-pre-market/references/news-recency-framework.md`
# for the full methodology. The core idea: a news item's age determines whether it
# may serve as TODAY's pre-market basis (T0), as trend-assist with a confirmed
# digestion status (T1), or only as background (T2). A multi-day decline/rally
# headline must be cross-checked against actual subsequent price action before it
# enters the day's forecast — otherwise stale news silently becomes the basis.
# ---------------------------------------------------------------------------


@dataclass
class RecencyVerdict:
    """Outcome of classifying a news item's recency for today's report."""

    tier: str  # "T0" | "T1" | "T2"
    days_old: int
    usage: str  # human-readable guidance on how this item may be used today

    @property
    def can_be_today_basis(self) -> bool:
        """Whether the item may be cited as TODAY's pre-market basis."""
        return self.tier == "T0"


# Recency tier boundaries (in calendar days between event_date and today).
# T0: occurred today or the just-completed session (overnight) -> can be a basis.
# T1: 2-3 days old -> trend-assist only, must carry a digestion status.
# T2: 4+ days old -> background only, never enters today's forecast logic.
_T0_MAX_DAYS = 1  # today (0) and yesterday (1) count as T0
_T1_MAX_DAYS = 3  # D-2, D-3 are T1


def classify_news_recency(
    event_date: str | date | datetime,
    today: str | date | datetime | None = None,
) -> RecencyVerdict:
    """Classify how old a news item is relative to ``today`` and how it may be used.

    Returns a :class:`RecencyVerdict` with a tier (T0/T1/T2) and guidance text.

    Tier semantics (see the news-recency framework reference):

    - **T0 (today / just-completed session)** — may be cited as TODAY's pre-market
      basis. Example: overnight US session that just closed, an event from today.
    - **T1 (D-2 ~ D-3)** — **must not** be today's basis. May only appear as
      trend-assist, and must carry a digestion status (消化/加剧/中性) derived from
      actual subsequent price action. Example: a sharp drop 3 days ago — check
      whether the index has since recovered before mentioning it at all.
    - **T2 (D-4 and older)** — background only. Does not enter today's forecast
      logic and is not written into "今日重点".

    Args:
        event_date: When the news/event happened. Accepts 'YYYY-MM-DD' string,
            ``date`` or ``datetime``.
        today: The reference "today" (defaults to the real current date). Accepts
            the same types. Mainly used for deterministic testing.

    Returns:
        RecencyVerdict with tier, days_old and a usage guidance string.
    """
    ev = _to_date(event_date)
    ref = _to_date(today) if today is not None else date.today()
    days_old = max((ref - ev).days, 0)  # future-dated events clamp to 0 (treat as T0)

    if days_old <= _T0_MAX_DAYS:
        tier = "T0"
        usage = (
            "当日核心依据（T0）：可作今日盘前最终预判依据，写入「今日重点」。"
        )
    elif days_old <= _T1_MAX_DAYS:
        tier = "T1"
        usage = (
            "趋势辅助（T1）：不可作今日预判依据。引用时必须标注消化状态"
            "（消化/加剧/中性），依据近 3 日实际走势判定。"
        )
    else:
        tier = "T2"
        usage = (
            "背景参考（T2）：仅作背景，不进当日预判逻辑，不写入「今日重点」。"
            "如确有证据表明仍在发酵，须附最近走势/讨论证据并降级为 T1 处理。"
        )

    return RecencyVerdict(tier=tier, days_old=days_old, usage=usage)


def _to_date(value: str | date | datetime) -> date:
    """Coerce a date-like value to a ``date`` object."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


@dataclass
class OverseasTrendDay:
    """One day of overseas market action."""

    date: str
    sp500_pct: float | None
    nasdaq_pct: float | None
    dow_pct: float | None
    vix: float | None
    a50_pct: float | None


@dataclass
class OverseasTrendSummary:
    """Recent multi-day overseas trend, for cross-checking stale news impact."""

    days: list[OverseasTrendDay] = field(default_factory=list)
    # Cumulative move over the window (sum of daily pct), None if no data.
    sp500_cum: float | None = None
    nasdaq_cum: float | None = None
    dow_cum: float | None = None
    vix_latest: float | None = None
    vix_trend: str = ""  # "rising" | "falling" | "flat" | "" (when unknown)
    digestion_hint: str = ""
    # The event_date of the most recent sharp move in the window, if any, for
    # cross-check: "the drop on X has since been / not been recovered".
    latest_sharp_move_date: str = ""

    @property
    def available_days(self) -> int:
        return len(self.days)


def read_recent_overseas_trend(
    days_back: int = 3,
    end_date: str | date | datetime | None = None,
) -> OverseasTrendSummary:
    """Read the recent multi-day overseas market trend for news cross-checking.

    Pulls the last ``days_back`` calendar days of ``invest_overseas_market`` rows
    (ordered ascending) and computes cumulative index moves + a VIX trend label.
    This is the data the agent uses to judge whether an older shock headline has
    been **digested / intensified / neutral** before allowing it anywhere near
    today's forecast — see :func:`classify_news_recency` and the news-recency
    framework.

    Args:
        days_back: Trailing window in calendar days (default 3).
        end_date: End of the window (defaults to today).

    Returns:
        :class:`OverseasTrendSummary`. Empty ``days`` means no data collected for
        the window — the caller must treat any stale news as "digestion unknown".
    """
    # Lazy import to avoid coupling this module's import time to invest_sop.
    from stockhot.invest_sop.utils.db_helpers import query_by_date_range

    ref = _to_date(end_date) if end_date is not None else date.today()
    rows = query_by_date_range(
        "invest_overseas_market", end_date=ref, days_back=days_back
    )

    summary = OverseasTrendSummary()
    for row in rows:
        day = OverseasTrendDay(
            date=row.get("date", ""),
            sp500_pct=_safe_float(row.get("sp500_pct")),
            nasdaq_pct=_safe_float(row.get("nasdaq_pct")),
            dow_pct=_safe_float(row.get("dow_pct")),
            vix=_safe_float(row.get("us_vix")) or _safe_float(row.get("vix")),
            a50_pct=_safe_float(row.get("a50_pct")),
        )
        summary.days.append(day)

    if not summary.days:
        summary.digestion_hint = "近 %d 日无海外市场数据，过期消息的消化状态无法判定。" % days_back
        return summary

    summary.sp500_cum = _sum_present(d.sp500_pct for d in summary.days)
    summary.nasdaq_cum = _sum_present(d.nasdaq_pct for d in summary.days)
    summary.dow_cum = _sum_present(d.dow_pct for d in summary.days)
    summary.vix_latest = next(
        (d.vix for d in reversed(summary.days) if d.vix is not None), None
    )
    summary.vix_trend = _vix_trend_label(summary.days)

    # Detect the most recent sharp move (|daily| >= 2%) for cross-check hinting.
    sharp_threshold = 2.0
    for day in reversed(summary.days):
        for pct in (day.sp500_pct, day.nasdaq_pct, day.dow_pct):
            if pct is not None and abs(pct) >= sharp_threshold:
                summary.latest_sharp_move_date = day.date
                break
        if summary.latest_sharp_move_date:
            break

    summary.digestion_hint = _digestion_hint(summary)
    return summary


def _safe_float(value) -> float | None:
    """Return ``value`` as float, or None if it is None/empty/unparseable."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN check
        return None
    return f


def _sum_present(values) -> float | None:
    """Sum non-None floats; return None if none present."""
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(sum(present), 4)


def _vix_trend_label(days: list[OverseasTrendDay]) -> str:
    """Label the VIX direction over the window: rising/falling/flat."""
    vix_series = [d.vix for d in days if d.vix is not None]
    if len(vix_series) < 2:
        return ""
    delta = vix_series[-1] - vix_series[0]
    if delta > 1.0:
        return "rising"
    if delta < -1.0:
        return "falling"
    return "flat"


def _digestion_hint(summary: OverseasTrendSummary) -> str:
    """Produce a plain-language hint about whether recent shocks were digested."""
    if not summary.days or len(summary.days) < 2:
        return ""
    # Use the index with the widest data coverage as the reference.
    cum = next(
        (c for c in (summary.sp500_cum, summary.nasdaq_cum, summary.dow_cum) if c is not None),
        None,
    )
    if cum is None:
        return ""
    if not summary.latest_sharp_move_date:
        if abs(cum) < 1.0:
            return "近 %d 日海外指数窄幅波动（累计 %.2f%%），无显著冲击待消化。" % (
                len(summary.days),
                cum,
            )
        return ""
    # There was a sharp move within the window.
    if cum > 1.0:
        return (
            "窗口内 %s 出现剧烈波动，但近 %d 日累计 %.2f%% 已反向修复，"
            "该冲击大概率已被消化，不宜再据此预判当日。" % (
                summary.latest_sharp_move_date,
                len(summary.days),
                cum,
            )
        )
    if cum < -1.0:
        return (
            "窗口内 %s 出现剧烈波动，且近 %d 日累计 %.2f%% 仍在延续同向，"
            "影响可能仍在发酵，谨慎参考，不作主导依据。" % (
                summary.latest_sharp_move_date,
                len(summary.days),
                cum,
            )
        )
    return (
        "窗口内 %s 出现剧烈波动，但近 %d 日累计 %.2f%% 趋于平稳，"
        "影响消化状态不明确，标注待观察。" % (
            summary.latest_sharp_move_date,
            len(summary.days),
            cum,
        )
    )
