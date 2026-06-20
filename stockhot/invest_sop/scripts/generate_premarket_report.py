"""Generate pre-market SOP report from database data.

Usage:
    PYTHONPATH=. .venv/bin/python stockhot/invest_sop/scripts/generate_premarket_report.py [--date YYYY-MM-DD] [--template-only]
"""

import argparse
from datetime import datetime
from pathlib import Path

from stockhot.invest_sop.config import INVEST_REPORTS_DIR
from stockhot.invest_sop.utils.db_helpers import query_by_date
from stockhot.sell_monitor import build_section_holdings_monitor
from stockhot.storage.database import get_connection

SECTORS = ["AI", "半导体", "软件", "锂电", "光伏", "新能源车", "有色", "化工", "煤炭"]
NA = "数据不可用"


def _val(data: dict, key: str, fmt: str = "{}") -> str:
    v = data.get(key)
    if v is None:
        return NA
    return fmt.format(v)


def _pct(data: dict, key: str) -> str:
    v = data.get(key)
    if v is None:
        return NA
    return f"{v:+.2f}%" if v != 0 else "0.00%"


def _bp(data: dict, key: str) -> str:
    v = data.get(key)
    if v is None:
        return NA
    return f"{v:+.1f}bp"


def _fetch_overseas(date: str) -> dict | None:
    rows = query_by_date("invest_overseas_market", date, date_column="date")
    return rows[0] if rows else None


def _fetch_events(date: str) -> list[dict]:
    return query_by_date("invest_domestic_events", date, date_column="date")


def _fetch_futures(date: str) -> dict | None:
    rows = query_by_date("invest_futures_sentiment", date, date_column="date")
    return rows[0] if rows else None


def _fetch_cycle_assessments() -> list[dict]:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM invest_cycle_assessments ORDER BY sector"
        )
        return [dict(row) for row in cursor]
    finally:
        conn.close()


def _fetch_active_holdings() -> list[dict]:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM invest_holdings WHERE status='active' ORDER BY id"
        )
        return [dict(row) for row in cursor]
    finally:
        conn.close()


def build_section_1(overseas: dict | None, events: list[dict], futures: dict | None) -> str:
    lines = ["## 一、市场环境评估\n"]
    lines.append("### 1.1 海外市场")

    if overseas:
        lines.append(f"- 美股：标普{_pct(overseas, 'sp500_pct')} 纳指{_pct(overseas, 'nasdaq_pct')} 道指{_pct(overseas, 'dow_pct')}")
        lines.append(f"- 美债10Y：{_val(overseas, 'us_10y', '{:.4f}')}（变动{_bp(overseas, 'us_10y_change_bp')}）")
        lines.append(f"- VIX：{_val(overseas, 'vix')}")
        lines.append(f"- A50夜盘：{_pct(overseas, 'a50_pct')}")
        lines.append(f"- USD/CNY：{_val(overseas, 'usd_cny')}")
    else:
        for label in ["美股", "美债10Y", "VIX", "A50夜盘", "USD/CNY"]:
            lines.append(f"- {label}：{NA}")

    if futures:
        lines.append(f"- 股指期货：IF {_pct(futures, 'if_pct')} / IC {_pct(futures, 'ic_pct')} / IM {_pct(futures, 'im_pct')}")
    else:
        lines.append(f"- 股指期货：{NA}")

    lines.append("")
    lines.append("### 1.2 重大事件")
    lines.append("| 事件 | 影响板块 | 影响方向 | 影响程度 |")
    lines.append("|------|----------|----------|----------|")
    if events:
        for ev in events:
            name = ev.get("event_name", NA)
            sector = ev.get("affected_sector", "-")
            direction = ev.get("impact_direction", "-")
            severity = ev.get("severity", "-")
            lines.append(f"| {name} | {sector} | {direction} | {severity} |")
    else:
        lines.append(f"| {NA} | | | |")

    lines.append("")
    lines.append("### 1.3 国内政策/事件")
    lines.append("| 事件 | 影响范围 | 影响方向 |")
    lines.append("|------|----------|----------|")
    lines.append(f"| {NA} | | |")

    lines.append("")
    lines.append("### 1.4 综合判断")
    lines.append("- 市场情绪：🟡中性")
    lines.append("- 信心度：-/5分")
    lines.append("- 建议总仓位：-%- -%")

    return "\n".join(lines)


def build_section_2(cycles: list[dict]) -> str:
    lines = ["", "---", "", "## 二、板块周期评估", ""]
    lines.append("| 板块 | 周期位置 | 拥挤度 | 今日倾向 |")
    lines.append("|------|----------|--------|----------|")

    cycle_map = {c["sector"]: c for c in cycles}
    for sector in SECTORS:
        c = cycle_map.get(sector, {})
        pos = c.get("cycle_position")
        if pos:
            cycle_str = pos
        else:
            cycle_str = "□复苏□繁荣□衰退"
        crowd = c.get("crowding_score")
        crowd_str = f"{crowd}/12" if crowd is not None else "__/12"
        lines.append(f"| {sector} | {cycle_str} | {crowd_str} | 偏多/中性/偏空 |")

    lines.append("")
    lines.append("**周期变化提醒**：")
    return "\n".join(lines)


def build_section_3(holdings: list[dict]) -> str:
    lines = ["", "---", "", "## 三、持仓标的操作决策", ""]

    if not holdings:
        lines.append("（无活跃持仓）")
        return "\n".join(lines)

    for i, h in enumerate(holdings, 1):
        name = h.get("name", NA)
        code = h.get("code", NA)
        pos_pct = h.get("position_pct", "-")
        entry = h.get("entry_price", "-")
        current = h.get("current_price", "-")
        sl_logic = h.get("stop_loss_logic", "-")
        sl_tech = h.get("stop_loss_technical", "-")
        sl_hard = h.get("stop_loss_hard", "-")
        target = h.get("target_price", "-")

        lines.append(f"### 标的{i}：{name} ({code}) | 当前仓位：{pos_pct}%")
        lines.append("")
        lines.append("| 维度 | 评估 | 说明 |")
        lines.append("|------|------|------|")
        lines.append("| 买入逻辑 | | |")
        lines.append("| 逻辑状态 | ✅完好 / ⚠️动摇 / ❌破坏 | |")
        lines.append("| 事件影响 | 🟡 | |")
        lines.append("| 技术状态 | 强势/震荡/弱势 | 均线：__  支撑：__  压力：__ |")
        lines.append("| 周期位置 | 复苏/繁荣/衰退 | |")
        lines.append("| **操作决策** | **持有/减仓/加仓/清仓** | |")
        lines.append(f"| 止损价 | {sl_hard}（距当前-%） | 逻辑止损{sl_logic} / 技术止损{sl_tech} / 硬止损{sl_hard} |")
        lines.append(f"| 目标价 | {target}（距当前-%） | |")
        lines.append("| 执行方式 | 竞价减仓/开盘后观察/设提醒 | |")
        lines.append("")

    return "\n".join(lines)


def build_section_4() -> str:
    return "\n".join([
        "", "---", "", "## 四、新增标的备选", "",
        "（暂无备选标的）", "",
    ])


def build_section_5() -> str:
    return "\n".join([
        "", "---", "", "## 五、今日重点关注", "",
        "### 5.1 时间事件",
        "| 时间 | 事件 | 关注标的/板块 |",
        "|------|------|--------------|",
        f"| | {NA} | |",
        "",
        "### 5.2 关键价位提醒",
        "| 标的 | 价位 | 类型 | 触发动作 |",
        "|------|------|------|----------|",
        f"| | | 支撑/压力/止损 | |",
        "",
        "### 5.3 情绪阈值（满足条件时执行预案）",
        "| 条件 | 预案 |",
        "|------|------|",
        "| 开盘跌幅 > -% | |",
        "",
    ])


def build_section_6() -> str:
    return "\n".join([
        "---", "", "## 六、风控检查", "",
        "| 检查项 | 当前值 | 限制 | 是否合规 |",
        "|--------|--------|------|----------|",
        "| 总仓位 | -% | -%- -% | ✅/❌ |",
        "| 最大单票仓位 | -%(标的：-) | ≤25% | ✅/❌ |",
        "| 最大板块集中度 | -%(板块：-) | ≤40% | ✅/❌ |",
        "| 持仓数量 | -只 | ≤8只 | ✅/❌ |",
        "| 最小止损距离 | -(标的：-) | ≥-12% | ✅/❌ |",
        "",
    ])


def build_section_7() -> str:
    return "\n".join([
        "---", "", "## 七、昨日复盘（简要）", "",
        "- 昨日操作执行情况：",
        "- 昨日判断准确度：",
        "- 经验教训：",
        "",
    ])


def generate_template(date: str) -> str:
    weekday_map = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}
    dt = datetime.strptime(date, "%Y-%m-%d")
    weekday = weekday_map[dt.weekday()]

    parts = [
        f"# 盘前SOP报告 | {date} 星期{weekday}",
        "",
    ]

    sections = [
        build_section_1(None, [], None),
        build_section_2([]),
        build_section_3([]),
        build_section_holdings_monitor([], date),
        build_section_4(),
        build_section_5(),
        build_section_6(),
        build_section_7(),
    ]

    parts.extend(sections)
    return "\n".join(parts)


def generate_report(date: str) -> str:
    weekday_map = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}
    dt = datetime.strptime(date, "%Y-%m-%d")
    weekday = weekday_map[dt.weekday()]

    overseas = _fetch_overseas(date)
    events = _fetch_events(date)
    futures = _fetch_futures(date)
    cycles = _fetch_cycle_assessments()
    holdings = _fetch_active_holdings()

    parts = [
        f"# 盘前SOP报告 | {date} 星期{weekday}",
        "",
    ]

    sections = [
        build_section_1(overseas, events, futures),
        build_section_2(cycles),
        build_section_3(holdings),
        build_section_holdings_monitor(holdings, date),
        build_section_4(),
        build_section_5(),
        build_section_6(),
        build_section_7(),
    ]

    parts.extend(sections)
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate pre-market SOP report")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--template-only", action="store_true", dest="template_only")
    args = parser.parse_args()

    INVEST_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.template_only:
        content = generate_template(args.date)
    else:
        content = generate_report(args.date)

    out_path: Path = INVEST_REPORTS_DIR / f"{args.date}_pre_market.md"
    out_path.write_text(content, encoding="utf-8")
    print(f"[OK] Report saved to {out_path}")


if __name__ == "__main__":
    main()
