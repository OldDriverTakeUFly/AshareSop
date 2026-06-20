"""Comprehensive TDD tests for check_thesis_broken signal."""

from __future__ import annotations

import json

import pytest

from stockhot.sell_monitor.signals import check_thesis_broken


def _make_holding(snapshot=None) -> dict:
    return {"code": "000001", "name": "Test", "thesis_snapshot_json": snapshot}


def _make_score(percentile_rank=45) -> dict:
    return {"final_score": 65.0, "percentile_rank": percentile_rank}


class TestThesisBroken:
    def test_triggered_when_decline_exceeds_20(self):
        holding = _make_holding(snapshot=json.dumps({"percentile_rank": 80}))
        score = _make_score(percentile_rank=55)
        result = check_thesis_broken(holding, score)

        assert result["triggered"] is True

    def test_not_triggered_when_decline_within_20(self):
        holding = _make_holding(snapshot=json.dumps({"percentile_rank": 80}))
        score = _make_score(percentile_rank=65)
        result = check_thesis_broken(holding, score)

        assert result["triggered"] is False

    def test_triggered_at_boundary_just_above_20(self):
        holding = _make_holding(snapshot=json.dumps({"percentile_rank": 80}))
        score = _make_score(percentile_rank=59)
        result = check_thesis_broken(holding, score)

        assert result["triggered"] is True

    def test_not_triggered_at_boundary_exactly_20(self):
        holding = _make_holding(snapshot=json.dumps({"percentile_rank": 80}))
        score = _make_score(percentile_rank=60)
        result = check_thesis_broken(holding, score)

        assert result["triggered"] is False

    def test_not_triggered_when_rank_improves(self):
        holding = _make_holding(snapshot=json.dumps({"percentile_rank": 50}))
        score = _make_score(percentile_rank=80)
        result = check_thesis_broken(holding, score)

        assert result["triggered"] is False

    def test_skip_when_no_snapshot(self):
        holding = _make_holding(snapshot=None)
        result = check_thesis_broken(holding, _make_score())

        assert result["triggered"] is False
        assert result["details"]["reason"] == "no_snapshot"

    def test_skip_when_snapshot_is_none(self):
        holding = _make_holding(snapshot=None)
        result = check_thesis_broken(holding, _make_score())

        assert result["triggered"] is False
        assert result["details"]["reason"] == "no_snapshot"

    def test_skip_when_snapshot_is_empty_string(self):
        holding = _make_holding(snapshot="")
        result = check_thesis_broken(holding, _make_score())

        assert result["triggered"] is False
        assert result["details"]["reason"] == "no_snapshot"

    def test_skip_when_invalid_json(self):
        holding = _make_holding(snapshot="not json")
        result = check_thesis_broken(holding, _make_score())

        assert result["triggered"] is False
        assert result["details"]["reason"] == "invalid_snapshot"

    def test_skip_when_no_percentile_in_snapshot(self):
        holding = _make_holding(snapshot=json.dumps({"other": 1}))
        result = check_thesis_broken(holding, _make_score())

        assert result["triggered"] is False
        assert result["details"]["reason"] == "no_percentile_data"

    def test_skip_when_no_percentile_in_current(self):
        holding = _make_holding(snapshot=json.dumps({"percentile_rank": 80}))
        result = check_thesis_broken(holding, {"final_score": 65.0})

        assert result["triggered"] is False
        assert result["details"]["reason"] == "no_percentile_data"

    def test_signal_type_always_thesis_broken(self):
        holding = _make_holding(snapshot=None)
        result = check_thesis_broken(holding, _make_score())

        assert result["signal_type"] == "thesis_broken"

    def test_details_has_correct_keys_when_triggered(self):
        holding = _make_holding(snapshot=json.dumps({"percentile_rank": 80}))
        result = check_thesis_broken(holding, _make_score(percentile_rank=55))

        details = result["details"]
        assert "buy_percentile" in details
        assert "current_percentile" in details
        assert "decline" in details

    def test_details_has_correct_keys_when_skip(self):
        holding = _make_holding(snapshot=None)
        result = check_thesis_broken(holding, _make_score())

        details = result["details"]
        assert "status" in details
        assert "reason" in details

    def test_decline_value_correct(self):
        holding = _make_holding(snapshot=json.dumps({"percentile_rank": 80}))
        result = check_thesis_broken(holding, _make_score(percentile_rank=55))

        assert result["details"]["decline"] == pytest.approx(25.0)

    def test_returned_types_are_native(self):
        holding = _make_holding(snapshot=json.dumps({"percentile_rank": 80}))
        result = check_thesis_broken(holding, _make_score(percentile_rank=55))

        assert type(result["triggered"]) is bool

    def test_dict_snapshot_works(self):
        holding = _make_holding(snapshot={"percentile_rank": 80})
        result = check_thesis_broken(holding, _make_score(percentile_rank=55))

        assert result["triggered"] is True
        assert result["details"]["decline"] == pytest.approx(25.0)
