"""事件驱动日历 — 规则推算 A 股关键事件节点.

用固定规则推算三类事件（零 API 依赖）：
  1. 宏观数据发布：CPI/PPI(10日)、PMI(月末)、社融/M2(12日)
  2. 期货期权交割：股指期货(第3个周五)、股指期权(第4个周三)、A50(月末)
  3. 央行政策：LPR(每月20日)、美联储 FOMC(需手动维护)

用途：
  - premarket_strategy 内嵌事件提醒（事件前 1 交易日标注）
  - 盘后收评可引用当日事件解释波动
  - intraday_manager 可在事件日调整推送策略

行业大会等非规则事件通过 manual_events 表手动维护。
"""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path


@dataclass
class MarketEvent:
    """市场事件."""
    event_date: date
    name: str
    category: str          # 宏观/交割/央行/会议
    impact: str = "中"     # 高/中/低
    description: str = ""


def _adjust_weekend(d: date) -> date:
    """周末顺延：周六提前到周五，周日延后到周一."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """获取某月第 n 个指定的星期几."""
    cal = calendar.monthcalendar(year, month)
    day = cal[n - 1][weekday]
    if day == 0:
        day = cal[n][weekday]
    return date(year, month, day)


def generate_events(
    start: date | None = None,
    end: date | None = None,
) -> list[MarketEvent]:
    """生成指定时间范围内的事件列表.

    参数：
        start: 开始日期（默认当月1日）
        end: 结束日期（默认3个月后）

    返回：
        MarketEvent 列表（按日期排序）
    """
    if start is None:
        start = date.today().replace(day=1)
    if end is None:
        end = (date.today() + timedelta(days=180))

    events: list[MarketEvent] = []

    # 遍历范围内的每个月
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        last_day = calendar.monthrange(y, m)[1]

        # ── 宏观数据发布 ──
        # CPI/PPI: 每月10日左右（遇周末顺延）
        cpi_date = _adjust_weekend(date(y, m, 10))
        if start <= cpi_date <= end:
            events.append(MarketEvent(
                cpi_date, f"CPI/PPI 公布（{m-1}月数据）" if m > 1 else f"CPI/PPI 公布（上年12月）",
                "宏观", "高", "国家统计局公布 CPI/PPI，通胀数据影响货币政策预期"
            ))

        # 社融/M2: 每月10-15日
        shrz_date = _adjust_weekend(date(y, m, 12))
        if start <= shrz_date <= end:
            events.append(MarketEvent(
                shrz_date, f"社融/M2 公布（{m-1}月数据）" if m > 1 else f"社融/M2 公布（上年12月）",
                "宏观", "高", "央行公布社融和 M2，反映流动性变化"
            ))

        # PMI: 每月最后一天（公布当月数据，实际在月末或次月1日）
        pmi_date = _adjust_weekend(date(y, m, last_day))
        if start <= pmi_date <= end:
            events.append(MarketEvent(
                pmi_date, f"PMI 公布（{m}月数据）",
                "宏观", "高", "国家统计局公布制造业 PMI，景气度风向标"
            ))

        # ── 央行政策 ──
        # LPR: 每月20日（遇周末顺延）
        lpr_date = _adjust_weekend(date(y, m, 20))
        if start <= lpr_date <= end:
            events.append(MarketEvent(
                lpr_date, "LPR 公布",
                "央行", "高", "央行公布贷款市场报价利率，直接影响利率预期"
            ))

        # ── 国际宏观（北京时间次日影响 A 股）──
        # 美国 NFP(非农): 每月第一个周五（北京时间晚8:30）
        cal_m = calendar.monthcalendar(y, m)
        nfp_fri = cal_m[0][calendar.FRIDAY]
        if nfp_fri == 0:
            nfp_fri = cal_m[1][calendar.FRIDAY]
        nfp_date = date(y, m, nfp_fri)
        if start <= nfp_date <= end:
            events.append(MarketEvent(
                nfp_date, "🇺🇸 NFP 非农就业（美）",
                "国际", "高", "美国非农公布，A 股次日波动放大 1.3x"
            ))

        # 美国 CPI: 每月10-13日左右（影响 A 股次日）
        us_cpi_date = _adjust_weekend(date(y, m, 13))
        if start <= us_cpi_date <= end:
            events.append(MarketEvent(
                us_cpi_date, "🇺🇸 美国 CPI 公布",
                "国际", "中", "美国通胀数据，影响美联储政策预期"
            ))

        # ── 期货期权交割 ──
        # 股指期货：第3个周五
        fri3 = _nth_weekday(y, m, calendar.FRIDAY, 3)
        if start <= fri3 <= end:
            events.append(MarketEvent(
                fri3, "股指期货交割日",
                "交割", "中", "IF/IH/IC 股指期货交割，可能加剧指数波动"
            ))

        # 股指期权：第4个周三
        wed4 = _nth_weekday(y, m, calendar.WEDNESDAY, 4)
        if start <= wed4 <= end:
            events.append(MarketEvent(
                wed4, "股指期权交割日",
                "交割", "中", "50ETF/300ETF 期权交割，末日期权波动加大"
            ))

        # A50：月末倒数第2个工作日（简化推算）
        a50_date = _adjust_weekend(date(y, m, last_day - 1))
        if start <= a50_date <= end:
            events.append(MarketEvent(
                a50_date, "富时A50 交割日",
                "交割", "低", "新加坡富时A50期指交割"
            ))

        # 递增月份
        m += 1
        if m > 12:
            m = 1
            y += 1

    # 加载手动维护的事件（行业大会等）
    events.extend(_load_manual_events(start, end))

    events.sort(key=lambda e: e.event_date)
    return events


def _load_manual_events(start: date, end: date) -> list[MarketEvent]:
    """从 JSON 文件加载手动维护的事件（行业大会等）.

    文件路径：stockhot/invest_sop/manual_events.json
    格式：
    [
      {"date": "2026-08-15", "name": "世界人工智能大会", "category": "会议", "impact": "高", "description": "..."}
    ]
    """
    path = Path(__file__).parent / "manual_events.json"
    if not path.exists():
        return []
    events = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data:
            d = date.fromisoformat(item["date"])
            if start <= d <= end:
                events.append(MarketEvent(
                    d, item["name"], item.get("category", "会议"),
                    item.get("impact", "中"), item.get("description", "")
                ))
    except Exception:
        pass
    return events


def get_upcoming_events(days_ahead: int = 3) -> list[MarketEvent]:
    """获取未来 N 天内的事件（含今天）."""
    today = date.today()
    end = today + timedelta(days=days_ahead)
    events = generate_events(today, end)
    return events


def get_events_on_date(d: date) -> list[MarketEvent]:
    """获取某日的所有事件."""
    events = generate_events(d, d)
    return events


def format_events_for_report(events: list[MarketEvent]) -> str:
    """格式化事件为报告文本（用于盘前策略表内嵌）."""
    if not events:
        return ""

    lines = ["📅 近期事件提醒："]
    for e in events:
        weekday = "周" + "一二三四五六日"[e.event_date.weekday()]
        impact_emoji = {"高": "🔴", "中": "🟡", "低": "⚪"}.get(e.impact, "⚪")
        d_str = e.event_date.strftime("%m-%d")
        lines.append(
            f"  {impact_emoji} {d_str}({weekday}) {e.name}"
            f" [{e.category}]{f' — {e.description}' if e.description else ''}"
        )
    return "\n".join(lines)
