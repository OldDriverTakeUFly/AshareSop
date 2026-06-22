"""TDD tests for signal aggregator.

Mocks at MODULE level where names are imported (per project convention).
The critical test verifies ``check_thesis_broken`` receives real davis
score data, not ``{}``.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

import stockhot.advisor.signal_aggregator as agg
from stockhot.advisor.signal_aggregator import (
    AggregatedSignals,
    aggregate_signals,
    compute_confidence_multiplier,
)
from stockhot.advisor.types import UnifiedSignal


def _make_unified(
    name="technical",
    value=70.0,
    age_days=None,
    source="technical_analyzer",
) -> UnifiedSignal:
    ts = None
    if age_days is not None:
        ts = (date.today() - timedelta(days=age_days)).isoformat()
    return UnifiedSignal(
        name=name,
        value=value,
        polarity="higher_is_better",
        data_timestamp=ts,
        data_age_days=age_days,
        source=source,
        details={},
    )


def _make_holding(
    code="000001",
    stop_loss_hard=8.0,
    target_price=15.0,
    current_price=10.5,
    position_pct=8.0,
    snapshot=None,
) -> dict:
    h = {
        "code": code,
        "stop_loss_hard": stop_loss_hard,
        "target_price": target_price,
        "current_price": current_price,
        "position_pct": position_pct,
    }
    if snapshot is not None:
        h["thesis_snapshot_json"] = snapshot
    return h


def _mock_realtime(code="000001", price=10.5) -> dict:
    return {
        "code": code,
        "current_price": price,
        "change_pct": 2.3,
        "volume": 999000.0,
        "timestamp": date.today().isoformat(),
    }


def _mock_davis_score(final_score=65.0, percentile_rank=70.0, distress_score=40.0) -> dict:
    return {
        "final_score": final_score,
        "percentile_rank": percentile_rank,
        "distress_score": distress_score,
        "data_date": date.today().isoformat(),
    }


def _setup_all_mocks(monkeypatch, holding=None):
    monkeypatch.setattr(agg, "fetch_technical_signal", lambda code, df: _make_unified())
    monkeypatch.setattr(agg, "fetch_realtime_price", lambda code: _mock_realtime())
    monkeypatch.setattr(
        agg,
        "fetch_davis_signal",
        lambda code: _make_unified(name="davis", value=65.0, source="davis_analyzer"),
    )
    monkeypatch.setattr(agg, "get_current_davis_score", lambda code: _mock_davis_score())


# ── aggregate_signals — basic structure ────────────────────────────


class TestAggregateSignalsBasic:
    def test_returns_aggregated_signals_type(self, monkeypatch):
        _setup_all_mocks(monkeypatch)

        result = aggregate_signals("000001")

        assert isinstance(result, AggregatedSignals)

    def test_code_field_set(self, monkeypatch):
        _setup_all_mocks(monkeypatch)

        result = aggregate_signals("000001")

        assert result.code == "000001"

    def test_technical_signal_populated(self, monkeypatch):
        _setup_all_mocks(monkeypatch)

        result = aggregate_signals("000001")

        assert result.technical.name == "technical"
        assert result.technical.source == "technical_analyzer"

    def test_davis_signal_populated(self, monkeypatch):
        _setup_all_mocks(monkeypatch)

        result = aggregate_signals("000001")

        assert result.davis.name == "davis"
        assert result.davis.source == "davis_analyzer"

    def test_realtime_price_populated(self, monkeypatch):
        _setup_all_mocks(monkeypatch)

        result = aggregate_signals("000001")

        assert result.realtime_price["current_price"] == 10.5

    def test_data_freshness_built(self, monkeypatch):
        _setup_all_mocks(monkeypatch)

        result = aggregate_signals("000001")

        assert "technical" in result.data_freshness
        assert "davis" in result.data_freshness


# ── holding=None (candidate stock) ─────────────────────────────────


class TestNoHolding:
    def test_sell_signals_empty_when_no_holding(self, monkeypatch):
        _setup_all_mocks(monkeypatch)

        result = aggregate_signals("000001", holding=None)

        assert result.sell_signals == []

    def test_sell_signals_empty_by_default(self, monkeypatch):
        _setup_all_mocks(monkeypatch)

        result = aggregate_signals("000001")

        assert result.sell_signals == []

    def test_no_holding_does_not_call_sell_signals(self, monkeypatch):
        _setup_all_mocks(monkeypatch)
        called = []

        def mock_hard_stop(h, p):
            called.append("hard_stop")
            return {"triggered": False}

        monkeypatch.setattr(agg, "check_hard_stop_loss", mock_hard_stop)

        aggregate_signals("000001", holding=None)

        assert called == []


# ── holding provided (existing position) ───────────────────────────


class TestWithHolding:
    def test_four_sell_signals_called(self, monkeypatch):
        _setup_all_mocks(monkeypatch)
        holding = _make_holding()

        result = aggregate_signals("000001", holding=holding)

        assert len(result.sell_signals) == 4

    def test_signal_types_present(self, monkeypatch):
        _setup_all_mocks(monkeypatch)
        holding = _make_holding()

        result = aggregate_signals("000001", holding=holding)

        types = [s["signal_type"] for s in result.sell_signals]
        assert "hard_stop" in types
        assert "trailing_stop" in types
        assert "target_reached" in types
        assert "thesis_broken" in types

    def test_hard_stop_uses_realtime_price(self, monkeypatch):
        _setup_all_mocks(monkeypatch)
        captured = {}

        def mock_hard_stop(h, price):
            captured["price"] = price
            return {"triggered": False, "signal_type": "hard_stop"}

        monkeypatch.setattr(agg, "check_hard_stop_loss", mock_hard_stop)
        holding = _make_holding()

        aggregate_signals("000001", holding=holding)

        assert captured["price"] == 10.5

    def test_target_reached_uses_realtime_price(self, monkeypatch):
        _setup_all_mocks(monkeypatch)
        captured = {}

        def mock_target(h, price):
            captured["price"] = price
            return {"triggered": False, "signal_type": "target_reached"}

        monkeypatch.setattr(agg, "check_target_reached", mock_target)
        holding = _make_holding()

        aggregate_signals("000001", holding=holding)

        assert captured["price"] == 10.5

    def test_trailing_stop_gets_ohlcv(self, monkeypatch):
        _setup_all_mocks(monkeypatch)
        captured = {}
        ohlcv = pd.DataFrame(
            {
                "close": [10, 11],
                "low": [9, 10],
                "open": [10, 10],
                "high": [11, 11],
                "volume": [100, 200],
            },
            index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"]),
        )

        def mock_trailing(h, df):
            captured["df"] = df
            return {"triggered": False, "signal_type": "trailing_stop"}

        monkeypatch.setattr(agg, "check_trailing_stop", mock_trailing)
        holding = _make_holding()

        aggregate_signals("000001", holding=holding, ohlcv_df=ohlcv)

        assert captured["df"] is ohlcv


# ── CRITICAL: check_thesis_broken receives real davis score ────────


class TestThesisBrokenReceivesRealDavis:
    def test_thesis_broken_gets_real_davis_score(self, monkeypatch):
        _setup_all_mocks(monkeypatch)
        captured = {}

        def mock_thesis(holding, davis_score):
            captured["davis_score"] = davis_score
            return {"triggered": False, "signal_type": "thesis_broken"}

        monkeypatch.setattr(agg, "check_thesis_broken", mock_thesis)
        holding = _make_holding()

        aggregate_signals("000001", holding=holding)

        assert captured["davis_score"] != {}
        assert "final_score" in captured["davis_score"]
        assert "percentile_rank" in captured["davis_score"]

    def test_thesis_broken_davis_score_from_get_current(self, monkeypatch):
        expected_davis = _mock_davis_score(
            final_score=45.0, percentile_rank=45.0, distress_score=20.0
        )
        monkeypatch.setattr(agg, "fetch_technical_signal", lambda c, df: _make_unified())
        monkeypatch.setattr(agg, "fetch_realtime_price", lambda c: _mock_realtime())
        monkeypatch.setattr(agg, "fetch_davis_signal", lambda c: _make_unified(name="davis"))
        monkeypatch.setattr(agg, "get_current_davis_score", lambda c: expected_davis)

        captured = {}

        def mock_thesis(holding, davis_score):
            captured["davis_score"] = davis_score
            return {"triggered": False, "signal_type": "thesis_broken"}

        monkeypatch.setattr(agg, "check_thesis_broken", mock_thesis)
        holding = _make_holding()

        aggregate_signals("000001", holding=holding)

        assert captured["davis_score"]["final_score"] == 45.0
        assert captured["davis_score"]["percentile_rank"] == 45.0

    def test_thesis_broken_not_called_with_empty_dict(self, monkeypatch):
        _setup_all_mocks(monkeypatch)
        captured = {}

        def mock_thesis(holding, davis_score):
            captured["davis_score"] = davis_score
            return {"triggered": False, "signal_type": "thesis_broken"}

        monkeypatch.setattr(agg, "check_thesis_broken", mock_thesis)
        holding = _make_holding()

        aggregate_signals("000001", holding=holding)

        assert len(captured["davis_score"]) > 0


# ── compute_confidence_multiplier ──────────────────────────────────


class TestComputeConfidenceMultiplier:
    def _make_agg(self, tech_age=None, davis_age=None):
        return AggregatedSignals(
            code="000001",
            technical=_make_unified(age_days=tech_age),
            davis=_make_unified(name="davis", age_days=davis_age),
            realtime_price={},
            sell_signals=[],
            data_freshness={"technical": tech_age, "davis": davis_age},
        )

    def test_all_fresh_returns_1(self):
        agg_data = self._make_agg(tech_age=1, davis_age=1)

        assert compute_confidence_multiplier(agg_data) == 1.0

    def test_none_ages_returns_1(self):
        agg_data = self._make_agg(tech_age=None, davis_age=None)

        assert compute_confidence_multiplier(agg_data) == 1.0

    def test_age_10_returns_0_5(self):
        agg_data = self._make_agg(tech_age=10, davis_age=1)

        assert compute_confidence_multiplier(agg_data) == 0.5

    def test_age_35_returns_0_3(self):
        agg_data = self._make_agg(tech_age=35, davis_age=1)

        assert compute_confidence_multiplier(agg_data) == 0.3

    def test_davis_stale_returns_0_5(self):
        agg_data = self._make_agg(tech_age=1, davis_age=10)

        assert compute_confidence_multiplier(agg_data) == 0.5

    def test_worst_case_30_plus_takes_priority(self):
        agg_data = self._make_agg(tech_age=35, davis_age=10)

        assert compute_confidence_multiplier(agg_data) == 0.3

    def test_boundary_7_not_penalized(self):
        agg_data = self._make_agg(tech_age=7, davis_age=1)

        assert compute_confidence_multiplier(agg_data) == 1.0

    def test_boundary_8_penalized(self):
        agg_data = self._make_agg(tech_age=8, davis_age=1)

        assert compute_confidence_multiplier(agg_data) == 0.5
