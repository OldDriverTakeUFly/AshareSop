"""Generate pre-market SOP report from database data.

Usage:
    PYTHONPATH=. .venv/bin/python stockhot/invest_sop/scripts/generate_premarket_report.py [--date YYYY-MM-DD] [--template-only]
"""

import argparse
from datetime import datetime
from pathlib import Path

from stockhot.advisor.report_integration import build_advisor_section
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
        cursor = conn.execute("SELECT * FROM invest_cycle_assessments ORDER BY sector")
        return [dict(row) for row in cursor]
    finally:
        conn.close()


def _fetch_active_holdings() -> list[dict]:
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT * FROM invest_holdings WHERE status='active' ORDER BY id")
        return [dict(row) for row in cursor]
    finally:
        conn.close()


def build_section_1(
    overseas: dict | None,
    events: list[dict],
    futures: dict | None,
    index_technical: dict | None = None,
) -> str:
    lines = ["## 一、市场环境评估\n"]
    lines.append("### 1.1 海外市场")

    if overseas:
        lines.append(
            f"- 美股：标普{_pct(overseas, 'sp500_pct')} 纳指{_pct(overseas, 'nasdaq_pct')} 道指{_pct(overseas, 'dow_pct')}"
        )
        lines.append(
            f"- 美债10Y：{_val(overseas, 'us_10y', '{:.4f}')}（变动{_bp(overseas, 'us_10y_change_bp')}）"
        )
        lines.append(f"- VIX：{_val(overseas, 'vix')}")
        lines.append(f"- A50夜盘：{_pct(overseas, 'a50_pct')}")
        lines.append(f"- USD/CNY：{_val(overseas, 'usd_cny')}")
    else:
        for label in ["美股", "美债10Y", "VIX", "A50夜盘", "USD/CNY"]:
            lines.append(f"- {label}：{NA}")

    if futures:
        lines.append(
            f"- 股指期货：IF {_pct(futures, 'if_pct')} / IC {_pct(futures, 'ic_pct')} / IM {_pct(futures, 'im_pct')}"
        )
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

    # §1.4 综合判断 — 基于 index_technical 自动填充（若前一交易日数据可用）
    lines.append("")
    lines.append("### 1.4 综合判断")
    sentiment, confidence, position_range, tech_summary = _derive_market_sentiment(index_technical)
    lines.append(f"- 市场情绪：{sentiment}")
    lines.append(f"- 信心度：{confidence}/5分")
    lines.append(f"- 建议总仓位：{position_range}")
    if tech_summary:
        lines.append(f"- 技术面依据：{tech_summary}")

    # §1.5 大盘技术面预期 — 展示前一交易日收盘后的指数技术面（盘前用 T-1 数据）
    lines.append("")
    lines.append("### 1.5 大盘技术面预期（前一交易日收盘）")
    tech_table = _format_index_technical_for_premarket(index_technical)
    lines.append(tech_table)

    return "\n".join(lines)


def _derive_market_sentiment(index_technical: dict | None) -> tuple[str, str, str, str]:
    """从 index_technical 数据推导市场情绪/信心度/仓位建议。

    基于前一交易日（T-1）的指数技术面，给出 T 日的盘前预期：
    - 市场情绪：综合技术评分（强势=🟢/震荡=🟡/弱势=🔴）
    - 信心度：各指数阶段反推（主升/筑底=4-5分；回调/反弹=2-3分；筑顶/主跌=1-2分）
    - 建议总仓位：信心度映射（1分=0-20%、2分=20-40%、3分=40-60%、4分=60-80%、5分=80-100%）
    """
    if not index_technical or index_technical.get("status") != "success":
        return ("🟡中性（技术面数据不可用）", "-", "-%- -%", "")

    indices = index_technical.get("indices", {})
    success_indices = [r for r in indices.values() if r.get("status") == "success"]
    if not success_indices:
        return ("🟡中性（技术面数据不可用）", "-", "-%- -%", "")

    # 综合技术评分均值 → 情绪
    avg_score = sum(r.get("technical_score", 50) for r in success_indices) / len(success_indices)
    if avg_score > 65:
        sentiment = f"🟢偏强（技术评分 {avg_score:.1f}）"
    elif avg_score < 35:
        sentiment = f"🔴偏弱（技术评分 {avg_score:.1f}）"
    else:
        sentiment = f"🟡中性（技术评分 {avg_score:.1f}）"

    # 各指数信心度均值 → 总信心度
    avg_conf = sum(r.get("confidence_score", 2) for r in success_indices) / len(success_indices)
    confidence = max(1, min(5, round(avg_conf)))

    # 信心度 → 仓位区间映射
    position_map = {
        1: "0%-20%（低仓位，防御为主）",
        2: "20%-40%（轻仓，谨慎参与）",
        3: "40%-60%（中等仓位，均衡配置）",
        4: "60%-80%（较高仓位，适度进攻）",
        5: "80%-100%（高仓位，积极配置）",
    }
    position_range = position_map[confidence]

    summary = index_technical.get("summary", "")
    return (sentiment, str(confidence), position_range, summary)


def _format_index_technical_for_premarket(index_technical: dict | None) -> str:
    """格式化 index_technical 为盘前报告的 §1.5 表格。"""
    if not index_technical or index_technical.get("status") != "success":
        return f"> {NA}（前一交易日 index_technical 未采集，请先运行 daily-market-scan）"

    indices = index_technical.get("indices", {})
    trade_date = indices.get("000001.SH", {}).get("trade_date", "?") if indices else "?"

    lines = [f"> 数据来源：前一交易日（{trade_date}）收盘后的 index_technical 分析\n"]
    lines.append("| 指数 | 收盘 | 涨跌% | 技术评分 | **阶段** | 置信度 | 盘前预期 |")
    lines.append("|------|:---:|:---:|:---:|:---:|:---:|------|")
    for ts_code, r in indices.items():
        if r.get("status") != "success":
            lines.append(f"| {r.get('name', ts_code)} | — | — | — | 数据不可用 | — | — |")
            continue
        lines.append(
            f"| {r['name']} | {r['close']} | {r['pct_chg']:+.2f}% | "
            f"{r['technical_score']} | **{r['stage']}** | "
            f"{r['stage_confidence']}% | {r['expected_action']} |"
        )
    lines.append(f"\n**整体技术面定性**：{index_technical.get('summary', '-')}")
    return "\n".join(lines)


def _fetch_latest_index_technical(date: str) -> dict | None:
    """读取最近的 index_technical 数据（盘前用 T-1 数据）。

    从 daily_data 表按 trade_date 倒序找最近一条 index_technical 记录。
    """
    from stockhot.storage.database import get_daily_data
    from datetime import datetime, timedelta

    # 尝试最近 5 天，找到第一条有 index_technical 的
    base = datetime.strptime(date, "%Y-%m-%d") if isinstance(date, str) else date
    for offset in range(0, 6):
        try_date = (base - timedelta(days=offset)).strftime("%Y-%m-%d")
        data = get_daily_data(try_date)
        tech = data.get("index_technical")
        if tech and isinstance(tech, dict) and tech.get("status") == "success":
            return tech
    return None


def _fetch_latest_volatility(date: str) -> dict | None:
    """读取最近的 volatility 数据（盘前用 T-1 数据，中国版 VIX 五层体系）。

    从 daily_data 表按 trade_date 倒序找最近一条 volatility 记录，
    镜像 ``_fetch_latest_index_technical`` 的回溯逻辑。
    """
    from datetime import datetime, timedelta

    from stockhot.storage.database import get_daily_data

    base = datetime.strptime(date, "%Y-%m-%d") if isinstance(date, str) else date
    for offset in range(0, 6):
        try_date = (base - timedelta(days=offset)).strftime("%Y-%m-%d")
        data = get_daily_data(try_date)
        vol = data.get("volatility")
        if vol and isinstance(vol, dict) and vol.get("status") == "success":
            return vol
    return None


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
        lines.append(
            f"| 止损价 | {sl_hard}（距当前-%） | 逻辑止损{sl_logic} / 技术止损{sl_tech} / 硬止损{sl_hard} |"
        )
        lines.append(f"| 目标价 | {target}（距当前-%） | |")
        lines.append("| 执行方式 | 竞价减仓/开盘后观察/设提醒 | |")
        lines.append("")

    return "\n".join(lines)


def build_section_4() -> str:
    return "\n".join(
        [
            "",
            "---",
            "",
            "## 四、新增标的备选",
            "",
            "（暂无备选标的）",
            "",
        ]
    )


def build_section_5() -> str:
    return "\n".join(
        [
            "",
            "---",
            "",
            "## 五、今日重点关注",
            "",
            "### 5.1 时间事件",
            "| 时间 | 事件 | 关注标的/板块 |",
            "|------|------|--------------|",
            f"| | {NA} | |",
            "",
            "### 5.2 关键价位提醒",
            "| 标的 | 价位 | 类型 | 触发动作 |",
            "|------|------|------|----------|",
            "| | | 支撑/压力/止损 | |",
            "",
            "### 5.3 情绪阈值（满足条件时执行预案）",
            "| 条件 | 预案 |",
            "|------|------|",
            "| 开盘跌幅 > -% | |",
            "",
        ]
    )


def _format_volatility_row(volatility: dict | None) -> tuple[str, str]:
    """从 volatility 数据推导"市场波动率状态"行的当前值与合规判定。

    返回 (当前值, 是否合规)，对应方法论研报 §8.2 四档行动框架：
        红色（系统性恐慌）→ ❌ 全局降仓一档
        橙色/黄色/绿色   → ✅ 正常持仓 / 关注
        数据不可用        → N/A

    结构性恐慌（仅成长股 P90+）不触发降仓，标 ⚠️ 关注。
    """
    NA = "数据不可用"
    if not volatility or volatility.get("status") != "success":
        return NA, "N/A"

    indices = volatility.get("indices", {})
    ok_indices = [
        r
        for r in indices.values()
        if r.get("status") == "success" and r.get("rv20_pct") is not None
    ]
    if not ok_indices:
        return NA, "N/A"

    panic_indices = [r for r in ok_indices if r["rv20_pct"] >= 90]
    panic_n = len(panic_indices)
    total_n = len(ok_indices)

    market = volatility.get("market", {})
    ivix = market.get("ivix_current", "?")
    vr = market.get("vr_ratio")

    # 找最恐慌指数
    coldest = max(ok_indices, key=lambda r: r["rv20_pct"])
    coldest_str = f"{coldest['name']} P{coldest['rv20_pct']:.0f}"

    vr_str = f"，V/R={vr:.2f}" if vr is not None else ""

    # 系统性恐慌判定：宽基指数（上证/深证/沪深300）中 ≥2 个 P90+，
    # 仅成长股（创业板/科创）P90+ 算结构性恐慌——
    # 因为成长股天然波动大，单独高位不代表全市场恐慌
    BROADBAND_CODES = {"000001.SH", "399001.SZ", "000300.SH"}
    broadband_panic = sum(1 for r in panic_indices if r.get("ts_code") in BROADBAND_CODES)
    is_systemic = broadband_panic >= 2

    if is_systemic:
        current = f"⚠️ 系统性恐慌：{panic_n}/{total_n} 指数 RV≥P90（最恐慌 {coldest_str}），iVIX={ivix}{vr_str}"
        return current, "❌ 全局降仓一档"
    elif panic_n > 0:
        names = "+".join(r["name"] for r in panic_indices)
        current = f"结构性恐慌：{names} RV≥P90（蓝筹正常），iVIX={ivix}{vr_str}"
        return current, "⚠️ 关注风格切换"
    else:
        current = f"正常：无指数 RV≥P90（最恐慌 {coldest_str}），iVIX={ivix}{vr_str}"
        return current, "✅ 正常持仓"


def build_section_6(volatility: dict | None = None) -> str:
    vol_current, vol_compliance = _format_volatility_row(volatility)
    return "\n".join(
        [
            "---",
            "",
            "## 六、风控检查",
            "",
            "| 检查项 | 当前值 | 限制 | 是否合规 |",
            "|--------|--------|------|----------|",
            "| 总仓位 | -% | -%- -% | ✅/❌ |",
            "| 最大单票仓位 | -%(标的：-) | ≤25% | ✅/❌ |",
            "| 最大板块集中度 | -%(板块：-) | ≤40% | ✅/❌ |",
            "| 持仓数量 | -只 | ≤8只 | ✅/❌ |",
            "| 最小止损距离 | -(标的：-) | ≥-12% | ✅/❌ |",
            f"| 市场波动率状态 | {vol_current} | RV≥P90 → 降仓 | {vol_compliance} |",
            "",
        ]
    )


def build_section_7() -> str:
    return "\n".join(
        [
            "---",
            "",
            "## 七、昨日复盘（简要）",
            "",
            "- 昨日操作执行情况：",
            "- 昨日判断准确度：",
            "- 经验教训：",
            "",
        ]
    )


def generate_template(date: str) -> str:
    weekday_map = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}
    dt = datetime.strptime(date, "%Y-%m-%d")
    weekday = weekday_map[dt.weekday()]

    parts = [
        f"# 盘前SOP报告 | {date} 星期{weekday}",
        "",
    ]

    sections = [
        build_section_1(None, [], None, None),
        build_section_2([]),
        build_section_3([]),
        build_section_holdings_monitor([], date),
        build_advisor_section(date),
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
    index_technical = _fetch_latest_index_technical(date)
    volatility = _fetch_latest_volatility(date)

    parts = [
        f"# 盘前SOP报告 | {date} 星期{weekday}",
        "",
    ]

    sections = [
        build_section_1(overseas, events, futures, index_technical),
        build_section_2(cycles),
        build_section_3(holdings),
        build_section_holdings_monitor(holdings, date),
        build_advisor_section(date),
        build_section_4(),
        build_section_5(),
        build_section_6(volatility),
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
