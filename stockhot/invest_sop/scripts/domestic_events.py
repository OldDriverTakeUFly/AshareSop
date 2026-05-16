"""Collect domestic event data: restricted shares, financial news, manual events.

Table: invest_domestic_events
"""

import argparse
import os
import re
import traceback
from datetime import datetime

import akshare as ak

from stockhot.invest_sop.utils.db_helpers import upsert_record
from stockhot.invest_sop.utils.trading_calendar import is_trading_day

TABLE = "invest_domestic_events"

SEVERITY_KEYWORDS: dict[str, list[str]] = {
    "🟠": ["降息", "加息", "利率决议", "LPR", "MLF", "逆回购", "降准", "印花税", "IPO重启",
           "注册制", "反垄断", "制裁", "贸易战", "关税", "政治局", "国务院", "政策",
           "宏观审慎", "外汇管制", "资本市场改革"],
    "🟡": ["PMI", "CPI", "PPI", "GDP", "社融", "M2", "新增贷款", "进出口", "贸易顺差",
           "工业增加值", "零售销售", "固定资产投资", "非农", "就业", "通胀", "失业率",
           "EIA", "API", "OPEC", "FOMC", "纪要", "褐皮书", "消费者信心"],
    "🔴": ["黑天鹅", "熔断", "暴跌", "金融危机", "银行挤兑", "战争", "冲突升级",
           "地缘政治", "突发", "紧急", "恐慌", "制裁加码", "债务危机", "违约潮"],
}


def strip_proxy() -> dict[str, str]:
    removed: dict[str, str] = {}
    for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"]:
        if key in os.environ:
            removed[key] = os.environ.pop(key)
    return removed


def restore_proxy(removed: dict[str, str]) -> None:
    os.environ.update(removed)


def _call_akshare(method_name: str, **kwargs):
    removed = strip_proxy()
    try:
        method = getattr(ak, method_name)
        return method(**kwargs)
    finally:
        restore_proxy(removed)


def grade_severity(text: str) -> str:
    """Auto-grade event severity based on keyword matching."""
    for severity in ["🔴", "🟠", "🟡"]:
        for keyword in SEVERITY_KEYWORDS[severity]:
            if keyword in text:
                return severity
    return "🟢"


def _collect_restricted_release_events(target_date: str) -> list[dict]:
    events = []
    try:
        df = _call_akshare("stock_restricted_release_summary_em")
        if df is None or len(df) == 0:
            return events
        for _, row in df.iterrows():
            release_date = str(row.get("解禁时间", ""))
            market_cap = row.get("实际解禁市值", 0)
            stock_count = row.get("当日解禁股票家数", 0)
            if not release_date or market_cap == 0:
                continue
            cap_display = f"{market_cap / 1e8:.1f}亿" if market_cap >= 1e8 else f"{market_cap / 1e4:.1f}万"
            event_name = f"限售股解禁: {release_date} 共{stock_count}家 解禁市值{cap_display}元"
            severity = "🟡" if market_cap > 500e8 else "🟢"
            events.append({
                "date": target_date,
                "event_name": event_name,
                "affected_sector": "全市场",
                "impact_direction": "偏空",
                "severity": severity,
                "source": "eastmoney_restricted",
            })
    except Exception as e:
        print(f"  [WARN] restricted_release failed: {e}")
        traceback.print_exc()
    return events


def _collect_cls_news_events(target_date: str) -> list[dict]:
    events = []
    try:
        df = _call_akshare("stock_info_global_cls")
        if df is None or len(df) == 0:
            return events
        for _, row in df.iterrows():
            title = str(row.get("标题", ""))
            content = str(row.get("内容", ""))
            pub_date = str(row.get("发布日期", ""))
            if not title:
                continue
            if target_date not in pub_date:
                continue
            combined = f"{title} {content}"
            severity = grade_severity(combined)
            affected = "宏观"
            for sector_kw in ["有色", "煤炭", "钢铁", "化工", "新能源", "半导体", "消费", "医药", "银行", "地产"]:
                if sector_kw in combined:
                    affected = sector_kw
                    break
            events.append({
                "date": target_date,
                "event_name": title[:200],
                "affected_sector": affected,
                "impact_direction": None,
                "severity": severity,
                "source": "cls_news",
            })
    except Exception as e:
        print(f"  [WARN] cls_news failed: {e}")
        traceback.print_exc()
    return events


def main():
    parser = argparse.ArgumentParser(description="Collect domestic event data")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--add-event", action="append", default=[], help="Manual event name (repeatable)")
    parser.add_argument("--sector", default="手动录入", help="Affected sector for manual events")
    parser.add_argument("--severity", default=None, help="Severity emoji for manual events (auto-graded if omitted)")
    args = parser.parse_args()

    print(f"[domestic_events] date={args.date} dry_run={args.dry_run}")

    if not is_trading_day(args.date):
        print(f"[SKIP] {args.date} is not a trading day")
        return

    all_events: list[dict] = []

    print("  Collecting restricted release events...")
    all_events.extend(_collect_restricted_release_events(args.date))
    print(f"  Found {len(all_events)} restricted release events")

    print("  Collecting CLS news events...")
    cls_events = _collect_cls_news_events(args.date)
    all_events.extend(cls_events)
    print(f"  Found {len(cls_events)} CLS news events")

    for event_text in args.add_event:
        severity = args.severity or grade_severity(event_text)
        all_events.append({
            "date": args.date,
            "event_name": event_text,
            "affected_sector": args.sector,
            "impact_direction": None,
            "severity": severity,
            "source": "manual",
        })

    print(f"[TOTAL] {len(all_events)} events collected")
    for ev in all_events:
        print(f"  {ev['severity']} [{ev['affected_sector']}] {ev['event_name'][:80]}")

    if not args.dry_run:
        for ev in all_events:
            upsert_record(TABLE, ev, unique_keys=["date", "event_name"])
        print(f"[SAVED] {len(all_events)} events to {TABLE}")
    else:
        print("[DRY-RUN] Skipping DB write")


if __name__ == "__main__":
    main()
