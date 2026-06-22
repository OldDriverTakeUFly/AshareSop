"""TDD tests for conflict resolver — hardcoded precedence ladder.

Tests cover all 6 scenarios (A–F), precedence enforcement, and
no-signal HOLD fallback.
"""

from __future__ import annotations

from stockhot.advisor.conflict_resolver import (
    PRECEDENCE_LADDER,
    arbitrate,
    classify_scenario,
)
from stockhot.advisor.signal_aggregator import AggregatedSignals
from stockhot.advisor.types import UnifiedSignal


def _make_signal(name="technical", value=50.0, source="technical_analyzer") -> UnifiedSignal:
    return UnifiedSignal(
        name=name,
        value=value,
        polarity="higher_is_better",
        data_timestamp=None,
        data_age_days=None,
        source=source,
        details={},
    )


def _make_sell_signal(signal_type, triggered=False) -> dict:
    return {
        "triggered": triggered,
        "signal_type": signal_type,
        "details": {},
    }


def _make_agg(
    tech_score=50.0,
    davis_score=50.0,
    sell_signals=None,
) -> AggregatedSignals:
    return AggregatedSignals(
        code="000001",
        technical=_make_signal("technical", tech_score, "technical_analyzer"),
        davis=_make_signal("davis", davis_score, "davis_analyzer"),
        realtime_price={},
        sell_signals=sell_signals or [],
        data_freshness={},
    )


# ── Scenario A: TRIM_TAKE_PROFIT ───────────────────────────────────


class TestScenarioA:
    def test_classify_trim_take_profit(self):
        agg = _make_agg(
            tech_score=65.0,
            davis_score=75.0,
            sell_signals=[_make_sell_signal("target_reached", triggered=True)],
        )
        result = classify_scenario(agg)
        assert result.scenario == "TRIM_TAKE_PROFIT"
        assert result.is_conflict is True

    def test_arbitrate_action_trim(self):
        agg = _make_agg(
            tech_score=65.0,
            davis_score=75.0,
            sell_signals=[_make_sell_signal("target_reached", triggered=True)],
        )
        result = arbitrate(agg)
        assert result.action == "TRIM"
        assert result.primary_signal == "target_reached"

    def test_davis_below_70_not_scenario_a(self):
        agg = _make_agg(
            tech_score=65.0,
            davis_score=65.0,
            sell_signals=[_make_sell_signal("target_reached", triggered=True)],
        )
        result = classify_scenario(agg)
        assert result.scenario != "TRIM_TAKE_PROFIT"


# ── Scenario B: EXIT_THESIS_INVALIDATED ────────────────────────────


class TestScenarioB:
    def test_classify_exit_thesis_invalidated(self):
        agg = _make_agg(
            tech_score=65.0,
            davis_score=50.0,
            sell_signals=[
                _make_sell_signal("thesis_broken", triggered=True),
                _make_sell_signal("target_reached", triggered=False),
            ],
        )
        result = classify_scenario(agg)
        assert result.scenario == "EXIT_THESIS_INVALIDATED"
        assert result.is_conflict is True

    def test_arbitrate_action_exit(self):
        agg = _make_agg(
            tech_score=65.0,
            davis_score=50.0,
            sell_signals=[
                _make_sell_signal("thesis_broken", triggered=True),
            ],
        )
        result = arbitrate(agg)
        assert result.action == "EXIT"
        assert result.primary_signal == "thesis_broken"

    def test_tech_below_60_not_scenario_b(self):
        agg = _make_agg(
            tech_score=55.0,
            davis_score=50.0,
            sell_signals=[
                _make_sell_signal("thesis_broken", triggered=True),
            ],
        )
        result = classify_scenario(agg)
        assert result.scenario != "EXIT_THESIS_INVALIDATED"


# ── Scenario C: VALUE_ENTRY ────────────────────────────────────────


class TestScenarioC:
    def test_classify_value_entry(self):
        agg = _make_agg(
            tech_score=35.0,
            davis_score=80.0,
            sell_signals=[],
        )
        result = classify_scenario(agg)
        assert result.scenario == "VALUE_ENTRY"
        assert result.is_conflict is False

    def test_arbitrate_action_buy(self):
        agg = _make_agg(
            tech_score=35.0,
            davis_score=80.0,
            sell_signals=[],
        )
        result = arbitrate(agg)
        assert result.action == "BUY"
        assert result.primary_signal == "agreement"

    def test_with_sell_signal_not_scenario_c(self):
        agg = _make_agg(
            tech_score=35.0,
            davis_score=80.0,
            sell_signals=[_make_sell_signal("hard_stop", triggered=True)],
        )
        result = classify_scenario(agg)
        assert result.scenario != "VALUE_ENTRY"

    def test_davis_below_70_not_scenario_c(self):
        agg = _make_agg(
            tech_score=35.0,
            davis_score=65.0,
            sell_signals=[],
        )
        result = classify_scenario(agg)
        assert result.scenario != "VALUE_ENTRY"


# ── Scenario D: RISK_EXIT ──────────────────────────────────────────


class TestScenarioD:
    def test_classify_risk_exit(self):
        agg = _make_agg(
            tech_score=50.0,
            davis_score=50.0,
            sell_signals=[
                _make_sell_signal("hard_stop", triggered=True),
                _make_sell_signal("thesis_broken", triggered=False),
            ],
        )
        result = classify_scenario(agg)
        assert result.scenario == "RISK_EXIT"
        assert result.is_conflict is True

    def test_arbitrate_action_exit(self):
        agg = _make_agg(
            tech_score=50.0,
            davis_score=50.0,
            sell_signals=[
                _make_sell_signal("hard_stop", triggered=True),
                _make_sell_signal("thesis_broken", triggered=False),
            ],
        )
        result = arbitrate(agg)
        assert result.action == "EXIT"
        assert result.primary_signal == "hard_stop"

    def test_thesis_also_broken_not_scenario_d(self):
        agg = _make_agg(
            tech_score=50.0,
            davis_score=50.0,
            sell_signals=[
                _make_sell_signal("hard_stop", triggered=True),
                _make_sell_signal("thesis_broken", triggered=True),
            ],
        )
        result = classify_scenario(agg)
        assert result.scenario != "RISK_EXIT"


# ── Scenario E: FULL_EXIT ──────────────────────────────────────────


class TestScenarioE:
    def test_classify_full_exit(self):
        agg = _make_agg(
            tech_score=50.0,
            davis_score=50.0,
            sell_signals=[
                _make_sell_signal("target_reached", triggered=True),
                _make_sell_signal("thesis_broken", triggered=True),
            ],
        )
        result = classify_scenario(agg)
        assert result.scenario == "FULL_EXIT"
        assert result.is_conflict is True

    def test_arbitrate_action_exit(self):
        agg = _make_agg(
            tech_score=50.0,
            davis_score=50.0,
            sell_signals=[
                _make_sell_signal("target_reached", triggered=True),
                _make_sell_signal("thesis_broken", triggered=True),
            ],
        )
        result = arbitrate(agg)
        assert result.action == "EXIT"


# ── Scenario F: NEUTRAL ────────────────────────────────────────────


class TestScenarioF:
    def test_classify_neutral(self):
        agg = _make_agg(
            tech_score=50.0,
            davis_score=50.0,
            sell_signals=[],
        )
        result = classify_scenario(agg)
        assert result.scenario == "NEUTRAL"
        assert result.is_conflict is False

    def test_arbitrate_action_hold(self):
        agg = _make_agg(
            tech_score=50.0,
            davis_score=50.0,
            sell_signals=[
                _make_sell_signal("hard_stop", triggered=False),
            ],
        )
        result = arbitrate(agg)
        assert result.action == "HOLD"
        assert result.primary_signal == "none"

    def test_all_bullish_with_holding_is_hold(self):
        agg = _make_agg(
            tech_score=75.0,
            davis_score=80.0,
            sell_signals=[
                _make_sell_signal("hard_stop", triggered=False),
                _make_sell_signal("thesis_broken", triggered=False),
            ],
        )
        result = arbitrate(agg)
        assert result.action == "HOLD"
        assert result.primary_signal == "agreement"

    def test_all_bullish_no_holding_is_buy(self):
        agg = _make_agg(
            tech_score=75.0,
            davis_score=80.0,
            sell_signals=[],
        )
        result = arbitrate(agg)
        assert result.action == "BUY"


# ── Precedence enforcement ─────────────────────────────────────────


class TestPrecedence:
    def test_hard_stop_beats_target_reached(self):
        agg = _make_agg(
            sell_signals=[
                _make_sell_signal("hard_stop", triggered=True),
                _make_sell_signal("target_reached", triggered=True),
            ],
        )
        result = arbitrate(agg)
        assert result.action == "EXIT"
        assert result.primary_signal == "hard_stop"
        assert "target_reached" in result.conflict_notes

    def test_thesis_broken_beats_target_reached(self):
        agg = _make_agg(
            sell_signals=[
                _make_sell_signal("thesis_broken", triggered=True),
                _make_sell_signal("target_reached", triggered=True),
            ],
        )
        result = arbitrate(agg)
        assert result.action == "EXIT"
        assert result.primary_signal == "thesis_broken"
        assert "target_reached" in result.conflict_notes

    def test_hard_stop_beats_thesis_broken(self):
        agg = _make_agg(
            sell_signals=[
                _make_sell_signal("hard_stop", triggered=True),
                _make_sell_signal("thesis_broken", triggered=True),
            ],
        )
        result = arbitrate(agg)
        assert result.action == "EXIT"
        assert result.primary_signal == "hard_stop"

    def test_hard_stop_beats_all(self):
        agg = _make_agg(
            sell_signals=[
                _make_sell_signal("hard_stop", triggered=True),
                _make_sell_signal("thesis_broken", triggered=True),
                _make_sell_signal("target_reached", triggered=True),
                _make_sell_signal("trailing_stop", triggered=True),
            ],
        )
        result = arbitrate(agg)
        assert result.primary_signal == "hard_stop"
        assert result.action == "EXIT"

    def test_precedence_ladder_order(self):
        assert PRECEDENCE_LADDER == ["hard_stop", "thesis_broken", "target_reached", "agreement"]


# ── No-signal HOLD ─────────────────────────────────────────────────


class TestNoSignalHold:
    def test_empty_sell_signals_hold(self):
        agg = _make_agg(sell_signals=[])
        result = arbitrate(agg)
        assert result.action == "HOLD"

    def test_all_not_triggered_hold(self):
        agg = _make_agg(
            sell_signals=[
                _make_sell_signal("hard_stop", triggered=False),
                _make_sell_signal("trailing_stop", triggered=False),
                _make_sell_signal("target_reached", triggered=False),
                _make_sell_signal("thesis_broken", triggered=False),
            ],
        )
        result = arbitrate(agg)
        assert result.action == "HOLD"

    def test_none_primary_when_no_signals(self):
        agg = _make_agg(sell_signals=[])
        result = arbitrate(agg)
        assert result.primary_signal == "none"
