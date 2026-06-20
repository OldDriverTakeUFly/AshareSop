"""Comprehensive TDD tests for check_target_reached signal."""

from __future__ import annotations

import pytest

from stockhot.sell_monitor.signals import check_target_reached


def _make_holding(target_price: float = 15.0, position_pct: float = 8.0) -> dict:
    return {
        "code": "000001",
        "name": "TestStock",
        "target_price": target_price,
        "position_pct": position_pct,
    }


class TestTargetReached:
    def test_triggered_when_above_target(self):
        holding = _make_holding(target_price=15.0)
        result = check_target_reached(holding, current_price=15.5)

        assert result["triggered"] is True

    def test_triggered_at_boundary(self):
        holding = _make_holding(target_price=15.0)
        result = check_target_reached(holding, current_price=15.0)

        assert result["triggered"] is True

    def test_not_triggered_when_below_target(self):
        holding = _make_holding(target_price=15.0)
        result = check_target_reached(holding, current_price=14.0)

        assert result["triggered"] is False

    def test_large_position_suggests_half_trim(self):
        holding = _make_holding(target_price=15.0, position_pct=12.0)
        result = check_target_reached(holding, current_price=16.0)

        assert result["triggered"] is True
        assert result["details"]["suggested_trim"] == "1/2"

    def test_medium_position_suggests_third_trim(self):
        holding = _make_holding(target_price=15.0, position_pct=8.0)
        result = check_target_reached(holding, current_price=16.0)

        assert result["triggered"] is True
        assert result["details"]["suggested_trim"] == "1/3"

    def test_small_position_no_trim(self):
        holding = _make_holding(target_price=15.0, position_pct=3.0)
        result = check_target_reached(holding, current_price=16.0)

        assert result["triggered"] is True
        assert result["details"]["suggested_trim"] == "none"

    def test_not_triggered_always_none_trim(self):
        holding = _make_holding(target_price=15.0, position_pct=12.0)
        result = check_target_reached(holding, current_price=14.0)

        assert result["triggered"] is False
        assert result["details"]["suggested_trim"] == "none"

    def test_boundary_position_pct_10(self):
        holding = _make_holding(target_price=15.0, position_pct=10.0)
        result = check_target_reached(holding, current_price=16.0)

        assert result["details"]["suggested_trim"] == "1/3"

    def test_boundary_position_pct_5(self):
        holding = _make_holding(target_price=15.0, position_pct=5.0)
        result = check_target_reached(holding, current_price=16.0)

        assert result["details"]["suggested_trim"] == "none"

    def test_signal_type_always_target_reached(self):
        holding = _make_holding(target_price=15.0)
        result = check_target_reached(holding, current_price=14.0)

        assert result["signal_type"] == "target_reached"

    def test_details_has_all_keys(self):
        holding = _make_holding(target_price=15.0)
        result = check_target_reached(holding, current_price=16.0)

        details = result["details"]
        assert "target" in details
        assert "current" in details
        assert "suggested_trim" in details

    def test_details_values_correct(self):
        holding = _make_holding(target_price=15.0)
        result = check_target_reached(holding, current_price=15.5)

        assert result["details"]["target"] == pytest.approx(15.0)
        assert result["details"]["current"] == pytest.approx(15.5)

    def test_returned_types_are_native(self):
        holding = _make_holding(target_price=15.0)
        result = check_target_reached(holding, current_price=16.0)

        assert type(result["triggered"]) is bool

    def test_position_pct_missing_defaults_to_none(self):
        holding = {"code": "000001", "target_price": 15.0}
        result = check_target_reached(holding, current_price=16.0)

        assert result["triggered"] is True
        assert result["details"]["suggested_trim"] == "none"
