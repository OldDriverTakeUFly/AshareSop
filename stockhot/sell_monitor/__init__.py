"""Holdings monitor — premarket report section builder.

Injects sell-signal monitoring into the premarket report. Runs 3
signals per holding (hard_stop, target_reached, thesis_broken).
``check_trailing_stop`` is intentionally excluded because it requires
OHLCV data which is too slow to fetch during report generation.
"""

from __future__ import annotations

from stockhot.sell_monitor.signals import (
    check_hard_stop_loss,
    check_target_reached,
    check_thesis_broken,
)

SELL_SIGNALS_START = "<!-- SELL_SIGNALS_START -->"
SELL_SIGNALS_END = "<!-- SELL_SIGNALS_END -->"

_SIGNAL_LABELS = {
    "hard_stop": "硬止损触发",
    "target_reached": "目标价达成",
    "thesis_broken": "逻辑破坏",
}


def _format_detail(signal: dict) -> str:
    sig_type = signal["signal_type"]
    details = signal["details"]

    if sig_type == "hard_stop":
        return (
            f"止损价{details['stop_price']:.2f}, "
            f"现价{details['current_price']:.2f}, "
            f"距止损{details['pct_to_stop']:+.2f}%"
        )

    if sig_type == "target_reached":
        trim = details["suggested_trim"]
        trim_label = {"1/2": "减半", "1/3": "减1/3", "none": "暂不减持"}.get(
            trim, trim
        )
        return (
            f"目标价{details['target']:.2f}, "
            f"现价{details['current']:.2f}, "
            f"建议{trim_label}"
        )

    if sig_type == "thesis_broken":
        return (
            f"买入百分位{details['buy_percentile']:.1f}, "
            f"当前{details['current_percentile']:.1f}, "
            f"下降{details['decline']:.1f}"
        )

    return str(details)


def build_section_holdings_monitor(holdings: list[dict], date: str) -> str:
    """Build the holdings-monitor section for the premarket report.

    Runs 3 sell signals per holding: ``check_hard_stop_loss``,
    ``check_target_reached``, ``check_thesis_broken``. Each signal is
    wrapped in ``try/except`` so a data error on one holding does not
    block the others.

    Args:
        holdings: List of holding dicts from ``invest_holdings`` table.
        date: Report date string (``YYYY-MM-DD``).

    Returns:
        Markdown wrapped in SELL_SIGNALS sentinels. Contains:
          - Title ``## 持仓监控（卖出时机）``
          - Per-holding table of triggered signals
          - "无活跃卖出信号" if no signals triggered
          - "无持仓" if holdings list is empty
    """
    lines = [
        "",
        "---",
        "",
        SELL_SIGNALS_START,
        "## 持仓监控（卖出时机）",
        "",
    ]

    if not holdings:
        lines.append("无持仓")
        lines.append("")
        lines.append(SELL_SIGNALS_END)
        return "\n".join(lines)

    any_triggered = False

    for h in holdings:
        name = h.get("name", "-")
        code = h.get("code", "-")
        current_price_raw = h.get("current_price")

        if current_price_raw is None:
            continue

        try:
            current_price = float(current_price_raw)
        except (TypeError, ValueError):
            continue

        triggered_signals: list[dict] = []

        # 1. Hard stop loss
        if h.get("stop_loss_hard") is not None:
            try:
                result = check_hard_stop_loss(h, current_price)
                if result["triggered"]:
                    triggered_signals.append(result)
            except (KeyError, TypeError, ValueError):
                pass

        # 2. Target reached (NOT trailing_stop — requires OHLCV)
        if h.get("target_price") is not None:
            try:
                result = check_target_reached(h, current_price)
                if result["triggered"]:
                    triggered_signals.append(result)
            except (KeyError, TypeError, ValueError):
                pass

        # 3. Thesis broken (current_davis_score={} → SKIP if no snapshot)
        try:
            result = check_thesis_broken(h, {})
            if result["triggered"]:
                triggered_signals.append(result)
        except (KeyError, TypeError, ValueError):
            pass

        if triggered_signals:
            any_triggered = True
            lines.append(f"### {name} ({code})")
            lines.append("")
            lines.append("| 信号类型 | 详情 |")
            lines.append("|----------|------|")
            for sig in triggered_signals:
                label = _SIGNAL_LABELS.get(
                    sig["signal_type"], sig["signal_type"]
                )
                detail_str = _format_detail(sig)
                lines.append(f"| {label} | {detail_str} |")
            lines.append("")

    if not any_triggered:
        lines.append("无活跃卖出信号")
        lines.append("")

    lines.append(SELL_SIGNALS_END)
    return "\n".join(lines)
