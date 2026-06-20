"""Comprehensive TDD tests for check_hard_stop_loss signal."""

from __future__ import annotations

import pytest

from stockhot.sell_monitor.signals import check_hard_stop_loss


def _make_holding(stop_loss_hard: float = 10.0) -> dict:
    return {
        "code": "000001",
        "name": "TestStock",
        "stop_loss_hard": stop_loss_hard,
    }


class TestHardStopLoss:
    def test_triggered_when_below_stop(self):
        holding = _make_holding(stop_loss_hard=10.0)
        result = check_hard_stop_loss(holding, current_price=8.0)

        assert result["triggered"] is True

    def test_triggered_at_boundary(self):
        holding = _make_holding(stop_loss_hard=10.0)
        result = check_hard_stop_loss(holding, current_price=10.0)

        assert result["triggered"] is True

    def test_not_triggered_when_above_stop(self):
        holding = _make_holding(stop_loss_hard=10.0)
        result = check_hard_stop_loss(holding, current_price=11.0)

        assert result["triggered"] is False

    def test_pct_to_stop_positive_when_triggered(self):
        holding = _make_holding(stop_loss_hard=10.0)
        result = check_hard_stop_loss(holding, current_price=8.0)

        assert result["details"]["pct_to_stop"] > 0

    def test_pct_to_stop_negative_when_not_triggered(self):
        holding = _make_holding(stop_loss_hard=10.0)
        result = check_hard_stop_loss(holding, current_price=12.0)

        assert result["details"]["pct_to_stop"] < 0

    def test_pct_to_stop_value(self):
        holding = _make_holding(stop_loss_hard=10.0)
        result = check_hard_stop_loss(holding, current_price=8.0)

        assert result["details"]["pct_to_stop"] == pytest.approx(25.0, abs=0.01)

    def test_signal_type_always_hard_stop(self):
        holding = _make_holding(stop_loss_hard=10.0)
        result = check_hard_stop_loss(holding, current_price=9.5)

        assert result["signal_type"] == "hard_stop"

    def test_details_has_all_keys(self):
        holding = _make_holding(stop_loss_hard=10.0)
        result = check_hard_stop_loss(holding, current_price=9.0)

        details = result["details"]
        assert "stop_price" in details
        assert "current_price" in details
        assert "pct_to_stop" in details

    def test_details_values_correct(self):
        holding = _make_holding(stop_loss_hard=10.0)
        result = check_hard_stop_loss(holding, current_price=8.0)

        assert result["details"]["stop_price"] == pytest.approx(10.0)
        assert result["details"]["current_price"] == pytest.approx(8.0)

    def test_returned_types_are_native(self):
        holding = _make_holding(stop_loss_hard=10.0)
        result = check_hard_stop_loss(holding, current_price=9.5)

        assert type(result["triggered"]) is bool

    def test_different_stop_levels(self):
        holding = _make_holding(stop_loss_hard=50.0)
        result = check_hard_stop_loss(holding, current_price=48.0)

        assert result["triggered"] is True
        assert result["details"]["stop_price"] == pytest.approx(50.0)
