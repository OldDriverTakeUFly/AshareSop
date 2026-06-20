"""Comprehensive tests for T17 report injection."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from stockhot.sell_monitor import (
    SELL_SIGNALS_END,
    SELL_SIGNALS_START,
    build_section_holdings_monitor,
)


def _holding(
    code="000001",
    name="TestStock",
    current_price=11.0,
    stop_loss_hard=9.0,
    target_price=13.0,
    position_pct=8.0,
    thesis_snapshot_json=None,
) -> dict:
    h = {
        "code": code,
        "name": name,
        "current_price": current_price,
        "stop_loss_hard": stop_loss_hard,
        "target_price": target_price,
        "position_pct": position_pct,
    }
    if thesis_snapshot_json is not None:
        h["thesis_snapshot_json"] = thesis_snapshot_json
    return h


class TestSentinelWrapping:
    def test_start_sentinel_present(self):
        result = build_section_holdings_monitor([], "2024-01-15")
        assert SELL_SIGNALS_START in result

    def test_end_sentinel_present(self):
        result = build_section_holdings_monitor([], "2024-01-15")
        assert SELL_SIGNALS_END in result

    def test_start_before_end(self):
        result = build_section_holdings_monitor([], "2024-01-15")
        assert result.index(SELL_SIGNALS_START) < result.index(SELL_SIGNALS_END)

    def test_title_inside_sentinels(self):
        result = build_section_holdings_monitor([], "2024-01-15")
        start = result.index(SELL_SIGNALS_START)
        end = result.index(SELL_SIGNALS_END)
        title_pos = result.index("## 持仓监控（卖出时机）")
        assert start < title_pos < end


class TestEmptyHoldings:
    def test_empty_holdings_shows_placeholder(self):
        result = build_section_holdings_monitor([], "2024-01-15")
        assert "无持仓" in result

    def test_empty_holdings_has_title(self):
        result = build_section_holdings_monitor([], "2024-01-15")
        assert "## 持仓监控（卖出时机）" in result


class TestNoSignalsTriggered:
    def test_no_signals_shows_message(self):
        h = _holding(current_price=11.0, stop_loss_hard=9.0, target_price=13.0)
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "无活跃卖出信号" in result

    def test_no_signals_does_not_show_holding_name(self):
        h = _holding(name="NoTrigger", current_price=11.0)
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "NoTrigger" not in result


class TestHardStopTriggered:
    def test_hard_stop_shown_in_table(self):
        h = _holding(current_price=8.5, stop_loss_hard=9.0, target_price=13.0)
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "硬止损触发" in result

    def test_hard_stop_shows_holding_name(self):
        h = _holding(name="DangerStock", current_price=8.5, stop_loss_hard=9.0)
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "DangerStock" in result

    def test_hard_stop_shows_stop_price(self):
        h = _holding(current_price=8.5, stop_loss_hard=9.0)
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "止损价9.00" in result

    def test_hard_stop_does_not_show_placeholder(self):
        h = _holding(current_price=8.5, stop_loss_hard=9.0)
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "无活跃卖出信号" not in result


class TestTargetReachedTriggered:
    def test_target_reached_shown_in_table(self):
        h = _holding(current_price=13.5, target_price=13.0)
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "目标价达成" in result

    def test_target_reached_shows_trim_suggestion_heavy(self):
        h = _holding(current_price=13.5, target_price=13.0, position_pct=12.0)
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "减半" in result

    def test_target_reached_shows_trim_suggestion_medium(self):
        h = _holding(current_price=13.5, target_price=13.0, position_pct=6.0)
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "减1/3" in result

    def test_target_reached_shows_trim_suggestion_light(self):
        h = _holding(current_price=13.5, target_price=13.0, position_pct=3.0)
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "暂不减持" in result


class TestThesisBrokenTriggered:
    def test_thesis_broken_shown_when_signal_triggers(self, monkeypatch):
        import stockhot.sell_monitor as sell_monitor_mod

        def fake_check(holding, score):
            return {
                "triggered": True,
                "signal_type": "thesis_broken",
                "details": {
                    "buy_percentile": 80.0,
                    "current_percentile": 55.0,
                    "decline": 25.0,
                },
            }

        monkeypatch.setattr(sell_monitor_mod, "check_thesis_broken", fake_check)
        h = _holding(current_price=11.0, stop_loss_hard=9.0, target_price=13.0)
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "逻辑破坏" in result

    def test_thesis_broken_not_shown_without_current_data(self):
        h = _holding(current_price=11.0, stop_loss_hard=9.0, target_price=13.0)
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "逻辑破坏" not in result


class TestMultipleSignals:
    def test_two_signals_on_same_holding(self):
        snapshot = json.dumps({"percentile_rank": 80})
        h = _holding(
            current_price=8.5,
            stop_loss_hard=9.0,
            target_price=8.0,
            thesis_snapshot_json=snapshot,
        )
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "硬止损触发" in result
        assert "目标价达成" in result

    def test_multiple_holdings_both_triggered(self):
        h1 = _holding(code="000001", name="StockA", current_price=8.0, stop_loss_hard=9.0)
        h2 = _holding(code="000002", name="StockB", current_price=8.0, stop_loss_hard=9.0)
        result = build_section_holdings_monitor([h1, h2], "2024-01-15")
        assert "StockA" in result
        assert "StockB" in result

    def test_mixed_triggered_and_not(self):
        h1 = _holding(code="000001", name="Triggered", current_price=8.0, stop_loss_hard=9.0)
        h2 = _holding(code="000002", name="Safe", current_price=11.0, stop_loss_hard=9.0)
        result = build_section_holdings_monitor([h1, h2], "2024-01-15")
        assert "Triggered" in result
        assert "Safe" not in result
        assert "无活跃卖出信号" not in result


class TestGracefulDegradation:
    def test_missing_current_price_skips_holding(self):
        h = _holding()
        h["current_price"] = None
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "无活跃卖出信号" in result

    def test_missing_stop_loss_hard_does_not_crash(self):
        h = _holding(current_price=11.0)
        h["stop_loss_hard"] = None
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "## 持仓监控（卖出时机）" in result

    def test_missing_target_price_does_not_crash(self):
        h = _holding(current_price=11.0)
        h["target_price"] = None
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "## 持仓监控（卖出时机）" in result

    def test_non_numeric_current_price_skips(self):
        h = _holding()
        h["current_price"] = "N/A"
        result = build_section_holdings_monitor([h], "2024-01-15")
        assert "无活跃卖出信号" in result


class TestReportInjection:
    def test_generate_template_includes_monitor_section(self):
        from stockhot.invest_sop.scripts.generate_premarket_report import (
            generate_template,
        )

        result = generate_template("2024-01-15")
        assert SELL_SIGNALS_START in result
        assert SELL_SIGNALS_END in result
        assert "## 持仓监控（卖出时机）" in result

    def test_generate_template_monitor_after_section_3(self):
        from stockhot.invest_sop.scripts.generate_premarket_report import (
            generate_template,
        )

        result = generate_template("2024-01-15")
        section3_pos = result.index("## 三、持仓标的操作决策")
        monitor_pos = result.index("## 持仓监控（卖出时机）")
        assert section3_pos < monitor_pos

    def test_generate_template_monitor_before_section_4(self):
        from stockhot.invest_sop.scripts.generate_premarket_report import (
            generate_template,
        )

        result = generate_template("2024-01-15")
        monitor_pos = result.index("## 持仓监控（卖出时机）")
        section4_pos = result.index("## 四、新增标的备选")
        assert monitor_pos < section4_pos

    def test_generate_report_includes_monitor_section(self):
        from stockhot.invest_sop.scripts.generate_premarket_report import (
            generate_report,
        )

        with patch(
            "stockhot.invest_sop.scripts.generate_premarket_report._fetch_overseas",
            return_value=None,
        ), patch(
            "stockhot.invest_sop.scripts.generate_premarket_report._fetch_events",
            return_value=[],
        ), patch(
            "stockhot.invest_sop.scripts.generate_premarket_report._fetch_futures",
            return_value=None,
        ), patch(
            "stockhot.invest_sop.scripts.generate_premarket_report._fetch_cycle_assessments",
            return_value=[],
        ), patch(
            "stockhot.invest_sop.scripts.generate_premarket_report._fetch_active_holdings",
            return_value=[],
        ):
            result = generate_report("2024-01-15")

        assert SELL_SIGNALS_START in result
        assert SELL_SIGNALS_END in result
        assert "## 持仓监控（卖出时机）" in result
        assert "无持仓" in result

    def test_generate_report_monitor_between_section_3_and_4(self):
        from stockhot.invest_sop.scripts.generate_premarket_report import (
            generate_report,
        )

        with patch(
            "stockhot.invest_sop.scripts.generate_premarket_report._fetch_overseas",
            return_value=None,
        ), patch(
            "stockhot.invest_sop.scripts.generate_premarket_report._fetch_events",
            return_value=[],
        ), patch(
            "stockhot.invest_sop.scripts.generate_premarket_report._fetch_futures",
            return_value=None,
        ), patch(
            "stockhot.invest_sop.scripts.generate_premarket_report._fetch_cycle_assessments",
            return_value=[],
        ), patch(
            "stockhot.invest_sop.scripts.generate_premarket_report._fetch_active_holdings",
            return_value=[],
        ):
            result = generate_report("2024-01-15")

        section3_pos = result.index("## 三、持仓标的操作决策")
        monitor_pos = result.index("## 持仓监控（卖出时机）")
        section4_pos = result.index("## 四、新增标的备选")
        assert section3_pos < monitor_pos < section4_pos

    def test_generate_report_with_triggered_signal(self):
        from stockhot.invest_sop.scripts.generate_premarket_report import (
            generate_report,
        )

        h = _holding(
            name="BustedStock",
            current_price=8.0,
            stop_loss_hard=9.0,
            target_price=13.0,
        )
        with patch(
            "stockhot.invest_sop.scripts.generate_premarket_report._fetch_overseas",
            return_value=None,
        ), patch(
            "stockhot.invest_sop.scripts.generate_premarket_report._fetch_events",
            return_value=[],
        ), patch(
            "stockhot.invest_sop.scripts.generate_premarket_report._fetch_futures",
            return_value=None,
        ), patch(
            "stockhot.invest_sop.scripts.generate_premarket_report._fetch_cycle_assessments",
            return_value=[],
        ), patch(
            "stockhot.invest_sop.scripts.generate_premarket_report._fetch_active_holdings",
            return_value=[h],
        ):
            result = generate_report("2024-01-15")

        assert "硬止损触发" in result
        assert "BustedStock" in result
