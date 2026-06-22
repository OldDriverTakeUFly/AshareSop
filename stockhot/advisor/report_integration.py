"""AI advisor section builder for pre-market report injection.

Queries advisor_runs table for the given trade date, groups recommendations
by type (build/adjust/clear/t_trade), and formats them into markdown tables
wrapped in sentinel markers. Follows the same sentinel-based injection
pattern as sell_monitor's build_section_holdings_monitor.
"""

from __future__ import annotations

import json

from stockhot.storage.database import get_connection

ADVISOR_SECTION_START = "<!-- ADVISOR_SECTION_START -->"
ADVISOR_SECTION_END = "<!-- ADVISOR_SECTION_END -->"

_DISCLAIMER = "> ⚠️ 以上建议仅供参考，不构成投资建议。AI 建议基于日线数据，可能存在滞后。"

_CONFIDENCE_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

_SECTION_HEADERS = {
    "build": "### 📊 建仓建议",
    "adjust": "### 🔄 调仓建议",
    "clear": "### ⚠️ 清仓建议",
    "t_trade": "### 📐 做T建议（低置信度）",
}


def _fetch_recommendations(date: str) -> list[dict]:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM advisor_runs WHERE trade_date = ? ORDER BY confidence DESC, action",
            (date,),
        )
        return [dict(row) for row in cursor]
    finally:
        conn.close()


def _parse_reasoning_json(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _format_entry_zone(entry_zone) -> str:
    if not entry_zone:
        return "-"
    if isinstance(entry_zone, (list, tuple)) and len(entry_zone) == 2:
        return f"{entry_zone[0]}-{entry_zone[1]}"
    return str(entry_zone)


def _fmt(val, suffix: str = "") -> str:
    if val is None:
        return "-"
    return f"{val}{suffix}"


def _format_build_table(rows: list[dict]) -> list[str]:
    lines = [
        "| 代码 | 操作 | 置信度 | 入场区间 | 止损 | 目标价 | 理由 |",
        "|------|------|--------|----------|------|--------|------|",
    ]
    for r in rows:
        parsed = _parse_reasoning_json(r.get("reasoning_json"))
        entry = _format_entry_zone(parsed.get("entry_zone"))
        lines.append(
            f"| {r['stock_code']} | {r['action']} | {r['confidence']} "
            f"| {entry} | {_fmt(parsed.get('stop_loss'))} "
            f"| {_fmt(parsed.get('target'))} | {parsed.get('reasoning', '-')} |"
        )
    return lines


def _format_adjust_table(rows: list[dict]) -> list[str]:
    lines = [
        "| 代码 | 操作 | 置信度 | 理由 |",
        "|------|------|--------|------|",
    ]
    for r in rows:
        parsed = _parse_reasoning_json(r.get("reasoning_json"))
        lines.append(
            f"| {r['stock_code']} | {r['action']} | {r['confidence']} "
            f"| {parsed.get('reasoning', '-')} |"
        )
    return lines


def _format_clear_table(rows: list[dict]) -> list[str]:
    lines = [
        "| 代码 | 操作 | 紧急度 | 理由 |",
        "|------|------|--------|------|",
    ]
    for r in rows:
        parsed = _parse_reasoning_json(r.get("reasoning_json"))
        lines.append(
            f"| {r['stock_code']} | {r['action']} | {r['confidence']} "
            f"| {parsed.get('reasoning', '-')} |"
        )
    return lines


def _format_t_trade_table(rows: list[dict]) -> list[str]:
    lines = [
        "| 代码 | 操作 | 置信度 | 入场区间 | 理由 |",
        "|------|------|--------|----------|------|",
    ]
    for r in rows:
        parsed = _parse_reasoning_json(r.get("reasoning_json"))
        entry = _format_entry_zone(parsed.get("entry_zone"))
        lines.append(
            f"| {r['stock_code']} | {r['action']} | {r['confidence']} "
            f"| {entry} | {parsed.get('reasoning', '-')} |"
        )
    return lines


_FORMATTERS = {
    "build": _format_build_table,
    "adjust": _format_adjust_table,
    "clear": _format_clear_table,
    "t_trade": _format_t_trade_table,
}


def _sort_by_confidence(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda r: (_CONFIDENCE_ORDER.get(r.get("confidence", ""), 3), r.get("stock_code", "")),
    )


def build_advisor_section(date: str) -> str:
    """Build the AI advisor section for the pre-market report.

    Args:
        date: Trade date string (YYYY-MM-DD).

    Returns:
        Markdown string wrapped in ADVISOR_SECTION sentinels containing:
          - Title with date
          - Tables for build/adjust/clear/t_trade recommendations
          - "暂无 AI 建议" if no recommendations exist
          - Disclaimer at the bottom
    """
    lines = [
        "",
        "---",
        "",
        ADVISOR_SECTION_START,
        f"## AI 综合建议（{date}）",
        "",
    ]

    all_recs = _fetch_recommendations(date)

    grouped: dict[str, list[dict]] = {}
    for rec in all_recs:
        rec_type = rec.get("recommendation_type", "")
        if rec_type in _FORMATTERS:
            grouped.setdefault(rec_type, []).append(rec)

    if not grouped:
        lines.append("暂无 AI 建议")
        lines.append("")
    else:
        for rec_type in ("build", "adjust", "clear", "t_trade"):
            rows = grouped.get(rec_type)
            if not rows:
                continue
            lines.append(_SECTION_HEADERS[rec_type])
            lines.append("")
            lines.extend(_FORMATTERS[rec_type](_sort_by_confidence(rows)))
            lines.append("")

    lines.append(_DISCLAIMER)
    lines.append(ADVISOR_SECTION_END)
    return "\n".join(lines)
