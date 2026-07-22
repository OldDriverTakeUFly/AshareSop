"""盘前报告飞书纯文本摘要生成器.

把盘前 SOP 报告的核心信息格式化为飞书友好的纯文本摘要（约 800-1200 字），
供 run_daily_advisor.py 在报告生成后推送到飞书群。

设计原则：
- 复用 generate_premarket_report 的数据获取函数，不重复造轮子
- 直接用结构化数据格式化，不走 markdown 解析（飞书 text 模式不渲染 md 表格）
- 用换行 + emoji + 缩进组织信息层次，适配手机端阅读
- 摘要末尾附 GitHub 完整报告链接

Usage:
    from stockhot.invest_sop.scripts.premarket_feishu_summary import (
        build_premarket_feishu_summary,
    )
    summary = build_premarket_feishu_summary("2026-07-22")
"""

from __future__ import annotations

from datetime import datetime

# GitHub 仓库地址（与 git remote origin 一致）
_GITHUB_REPO = "OldDriverTakeUFly/AshareSop"
_GITHUB_BRANCH = "master"
# 盘前报告在仓库中的相对路径
_REPORT_REL_PATH = "storage/files/reports/invest_sop"

# 复用 generate_premarket_report 的数据获取与推导函数（单一真相源）
from stockhot.invest_sop.scripts.generate_premarket_report import (  # noqa: E402
    _derive_market_sentiment,
    _fetch_active_holdings,
    _fetch_latest_index_technical,
    _fetch_latest_volatility,
    _format_volatility_row,
)


def build_premarket_feishu_summary(date: str) -> str:
    """生成盘前报告的飞书纯文本摘要.

    Args:
        date: 报告日期，格式 YYYY-MM-DD

    Returns:
        飞书友好的纯文本摘要（含 emoji、换行、GitHub 链接）
    """
    weekday_map = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}
    dt = datetime.strptime(date, "%Y-%m-%d")
    weekday = weekday_map[dt.weekday()]

    # ── 拉取数据（复用 generate_premarket_report 的函数）──
    index_tech = _fetch_latest_index_technical(date)
    holdings = _fetch_active_holdings()
    volatility = _fetch_latest_volatility(date)
    sentiment, confidence, position_range, tech_summary = _derive_market_sentiment(index_tech)

    lines: list[str] = [
        f"📊 盘前SOP报告 | {date} 星期{weekday}",
        "",
        "🎯 整体判断",
        f"情绪：{sentiment}",
        f"信心度：{confidence}/5 | 建议仓位：{position_range}",
    ]
    if tech_summary:
        # 技术面定性可能较长，截断到合理长度避免摘要过长
        short_summary = tech_summary[:120] + ("…" if len(tech_summary) > 120 else "")
        lines.append(f"技术面：{short_summary}")

    # ── 大盘技术面（4 指数逐行）──
    lines.append("")
    lines.append("📈 大盘技术面（前一交易日收盘）")
    if index_tech and index_tech.get("status") == "success":
        for ts_code, r in index_tech.get("indices", {}).items():
            if r.get("status") != "success":
                lines.append(f"  · {r.get('name', ts_code)} 数据不可用")
                continue
            pct = r.get("pct_chg", 0)
            pct_str = f"{pct:+.2f}%" if pct is not None else "?"
            lines.append(
                f"  · {r['name']} {r.get('close', '?')} ({pct_str}) "
                f"{r.get('stage', '?')} — {r.get('expected_action', '?')}"
            )
    else:
        lines.append("  · 数据不可用（前一交易日 index_technical 未采集）")

    # ── 持仓决策 ──
    lines.append("")
    lines.append("💼 持仓标的")
    if holdings:
        for h in holdings:
            name = h.get("name", "?")
            code = h.get("code", "?")
            pos = h.get("position_pct")
            target = h.get("target_price", "-")
            sl_hard = h.get("stop_loss_hard", "-")
            pos_str = f"{pos}%" if pos is not None else "未设"
            lines.append(
                f"  · {name}({code}) 仓位{pos_str} | 硬止损{sl_hard} 目标{target}"
            )
    else:
        lines.append("  · （无活跃持仓）")

    # ── AI 综合建议 ──
    lines.append("")
    lines.append("🤖 AI 综合建议")
    adv_lines = _format_advisor_brief(date)
    lines.extend(adv_lines)

    # ── 风控提示 ──
    vol_current, vol_compliance = _format_volatility_row(volatility)
    lines.append("")
    lines.append("⚠️ 风控检查")
    lines.append(f"  波动率：{vol_current}")
    lines.append(f"  动作：{vol_compliance}")

    # ── 完整报告 GitHub 链接 ──
    lines.append("")
    lines.append("📄 完整报告")
    lines.append(f"  https://github.com/{_GITHUB_REPO}/blob/{_GITHUB_BRANCH}/{_REPORT_REL_PATH}/{date}_pre_market.md")
    lines.append("")
    lines.append("（以上为盘前摘要，完整报告含板块周期/重点关注/复盘等节，详见链接）")

    return "\n".join(lines)


def _format_advisor_brief(date: str) -> list[str]:
    """把 advisor 建议格式化为飞书纯文本（逐行，非表格）.

    复用 build_advisor_section 读 advisor_runs 表，但转为纯文本。
    若无建议返回占位。
    """
    try:
        from stockhot.advisor.report_integration import build_advisor_section

        section_md = build_advisor_section(date)
    except Exception as e:
        return [f"  · 数据读取失败（{type(e).__name__}）"]

    # build_advisor_section 返回 markdown，含哨兵标记
    # 解析其中的建议条目（markdown 表格行）
    # 格式参考 report_integration.py：| 代码 | 操作 | 置信度 | ... |
    lines: list[str] = []
    found_rows = False
    for line in section_md.splitlines():
        line = line.strip()
        # 跳过 markdown 表头/分隔线/空行/标题/哨兵
        if (
            not line
            or line.startswith("<!--")
            or line.startswith("##")
            or line.startswith("---")
            or line.startswith("|--")
            or line.startswith("| 代码")
            or line.startswith("> ")
            or line.startswith("| 维度")
        ):
            continue
        if line.startswith("|") and "|" in line[1:]:
            # 表格数据行：| 代码 | 操作 | ... |
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 4 and cells[0]:
                found_rows = True
                code = cells[0]
                action = cells[1] if len(cells) > 1 else ""
                conf = cells[2] if len(cells) > 2 else ""
                # 理由可能较长，截断
                reason = cells[-1] if len(cells) > 3 else ""
                reason_short = reason[:40] + ("…" if len(reason) > 40 else "")
                lines.append(f"  · {code} {action} | {conf} | {reason_short}")

    if not found_rows:
        lines.append("  · 暂无 AI 建议")

    return lines
