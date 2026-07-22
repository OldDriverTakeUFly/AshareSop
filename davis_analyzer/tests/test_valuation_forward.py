"""Tests for davis_analyzer.valuation_forward — forward overlay + PS cross-check.

The overlay is a bounded [+15, −20] adjustment to the backward-looking PE
percentile. These tests pin every rule in the rule table and every correction
in the correction layer, so accidental changes to the hardcoded constants are
caught immediately.
"""

from __future__ import annotations

import pytest

from davis_analyzer.constants import (
    BASE_ACCELERATING_AHEAD,
    BASE_ACCELERATING_DECEL,
    BASE_DECLINING,
    BASE_DECELERATING,
    BASE_TURNING_UNCONFIRMED,
    BASE_TURNING_UP,
    FCST_RESONANCE,
    FCST_WEAKENING,
    FCST_WEAK_CONFIRM,
    FORWARD_OVERLAY_MAX,
    FORWARD_OVERLAY_MIN,
    FORWARD_PRICEIN_HALF_CAP,
    IGNITION_BONUS,
    REV_DOWNGRADE,
    REV_UPGRADE,
)
from davis_analyzer.types import (
    ForecastRevision,
    ForecastSignal,
    ValuationData,
)
from davis_analyzer.valuation_forward import (
    _base_adjustment,
    _forecast_adjustment,
    _ignition_adjustment,
    calculate_forward_overlay,
    calculate_ps_crosscheck,
)

# Default kwargs for calculate_forward_overlay — tests override individual
# fields. Represents a neutral, data-sufficient stock with no forward signals.
DEFAULT_KW = dict(
    stage="加速期",
    relative_delta_g=0.0,
    forecast=None,
    revision=None,
    is_ignition=False,
    delta_g_quarters=4,
    profit_growth=50.0,  # above 30% threshold so rule 2 doesn't bind
    historical_pe_percentile=0.50,
)


def _overlay(**overrides) -> "ForwardOverlay":
    """Call calculate_forward_overlay with DEFAULT_KW plus overrides."""
    from davis_analyzer.types import ForwardOverlay  # noqa: F401 (type hint)
    return calculate_forward_overlay(**{**DEFAULT_KW, **overrides})


def _forecast(leading=60.0, stale=False) -> ForecastSignal:
    return ForecastSignal(
        ts_code="x",
        ann_date="20260701",
        end_date="20261231",
        type="预增",
        p_change_min=40,
        p_change_max=80,
        p_change_mid=60,
        leading_score=leading,
        is_stale=stale,
    )


def _revision(score=100.0, direction="上调") -> ForecastRevision:
    return ForecastRevision(
        ts_code="x",
        end_date="20261231",
        initial_mid=50,
        revised_mid=70,
        revision_pp=20,
        revision_direction=direction,
        revision_score=score,
    )


# ── Sub-signal 1: base adjustment ─────────────────────────────────────


class TestBaseAdjustment:
    def test_accelerating_ahead(self):
        assert _base_adjustment("加速期", 10.0) == BASE_ACCELERATING_AHEAD

    def test_accelerating_decel(self):
        assert _base_adjustment("加速期", -5.0) == BASE_ACCELERATING_DECEL
        assert _base_adjustment("加速期", 0.0) == BASE_ACCELERATING_DECEL

    def test_turning_up(self):
        assert _base_adjustment("上升拐点", 5.0) == BASE_TURNING_UP

    def test_turning_unconfirmed(self):
        assert _base_adjustment("上升拐点", -3.0) == BASE_TURNING_UNCONFIRMED

    def test_decelerating_unconditional(self):
        assert _base_adjustment("减速期", 10.0) == BASE_DECELERATING
        assert _base_adjustment("减速期", -10.0) == BASE_DECELERATING

    def test_declining_unconditional(self):
        assert _base_adjustment("下降拐点", 10.0) == BASE_DECLINING
        assert _base_adjustment("下降拐点", -10.0) == BASE_DECLINING

    def test_unknown_stage_zero(self):
        assert _base_adjustment("未知", 10.0) == 0.0


# ── Sub-signal 2: forecast adjustment ─────────────────────────────────


class TestForecastAdjustment:
    def test_resonance_high_leading(self):
        fs = _forecast(leading=60.0)
        assert _forecast_adjustment(fs, None) == FCST_RESONANCE

    def test_weak_confirm(self):
        fs = _forecast(leading=40.0)
        assert _forecast_adjustment(fs, None) == FCST_WEAK_CONFIRM

    def test_weakening(self):
        fs = _forecast(leading=20.0)
        assert _forecast_adjustment(fs, None) == FCST_WEAKENING

    def test_stale_forecast_zero(self):
        fs = _forecast(leading=60.0, stale=True)
        assert _forecast_adjustment(fs, None) == 0.0

    def test_none_forecast_zero(self):
        assert _forecast_adjustment(None, None) == 0.0

    def test_revision_downgrade_stacks(self):
        rev = _revision(score=0.0, direction="下调")
        # downgrade stacks on whatever band score
        assert _forecast_adjustment(None, rev) == REV_DOWNGRADE

    def test_revision_upgrade_stacks(self):
        rev = _revision(score=100.0)
        assert _forecast_adjustment(None, rev) == REV_UPGRADE

    def test_revision_downgrade_stacks_on_resonance(self):
        fs = _forecast(leading=60.0)
        rev = _revision(score=0.0, direction="下调")
        assert _forecast_adjustment(fs, rev) == FCST_RESONANCE + REV_DOWNGRADE


# ── Sub-signal 3: ignition adjustment ─────────────────────────────────


class TestIgnitionAdjustment:
    def test_ignition_with_positive_rdg(self):
        assert _ignition_adjustment(True, 5.0) == IGNITION_BONUS

    def test_no_ignition(self):
        assert _ignition_adjustment(False, 5.0) == 0.0

    def test_ignition_killed_by_negative_rdg(self):
        # conflict rule 4: trend overrides ignition
        assert _ignition_adjustment(True, -5.0) == 0.0


# ── Correction rules ──────────────────────────────────────────────────


class TestRuleDataInsufficient:
    def test_zero_overlay_when_delta_g_quarters_low(self):
        o = _overlay(
            stage="加速期",
            relative_delta_g=10.0,
            forecast=_forecast(60),
            revision=_revision(100),
            is_ignition=True,
            delta_g_quarters=1,
        )
        assert o.final_overlay == 0.0
        assert o.data_sufficient is False
        assert "data_insufficient" in o.adjustments_applied
        assert "不可靠" in o.confidence_note

    def test_two_quarters_is_sufficient(self):
        o = _overlay(delta_g_quarters=2)
        assert o.data_sufficient is True


class TestRule30pctKillsUpside:
    def test_kills_upside_only(self):
        # Without rule: accelerating + rdg>0 + forecast resonance → strongly +
        o = _overlay(
            stage="加速期",
            relative_delta_g=10.0,
            forecast=_forecast(60),
            profit_growth=25.0,  # below 30%
        )
        assert o.final_overlay <= 0.0
        assert "30pct_kills_upside" in o.adjustments_applied

    def test_downside_preserved_below_threshold(self):
        # value-trap case: declining + low profit growth → downside must survive
        o = _overlay(
            stage="下降拐点",
            relative_delta_g=-5.0,
            revision=_revision(0, "下调"),
            profit_growth=-10.0,  # well below 30%
            historical_pe_percentile=0.20,
        )
        assert o.final_overlay < 0.0  # downside not killed

    def test_high_profit_growth_unaffected(self):
        o = _overlay(
            stage="加速期",
            relative_delta_g=10.0,
            forecast=_forecast(60),
            profit_growth=50.0,
        )
        assert o.final_overlay > 0.0
        assert "30pct_kills_upside" not in o.adjustments_applied

    def test_none_profit_growth_skips_rule(self):
        o = _overlay(stage="加速期", relative_delta_g=10.0, forecast=_forecast(60), profit_growth=None)
        assert o.final_overlay > 0.0


class TestRuleConflictForecastDown:
    def test_positive_rdg_with_downgrade_clamped(self):
        o = _overlay(
            stage="加速期",
            relative_delta_g=10.0,
            forecast=_forecast(60),
            revision=_revision(0, "下调"),  # management disagrees
        )
        assert o.final_overlay <= 0.0
        assert "conflict_forecast_down" in o.adjustments_applied

    def test_negative_rdg_with_downgrade_not_tagged(self):
        # conflict rule only applies when rdg>0
        o = _overlay(
            stage="下降拐点",
            relative_delta_g=-5.0,
            revision=_revision(0, "下调"),
        )
        assert "conflict_forecast_down" not in o.adjustments_applied


class TestRuleConflictIgnition:
    def test_ignition_ignored_when_rdg_negative(self):
        o = _overlay(stage="减速期", relative_delta_g=-5.0, is_ignition=True)
        # ignition_adjustment should be 0, so final = base only
        assert o.ignition_adjustment == 0.0


class TestRulePriceinHalve:
    def test_accelerating_high_pe_halved(self):
        # accelerating + rdg>0 + forecast resonance + ignition → raw ~ +22
        # but PE percentile 0.85 → cap at +7
        o = _overlay(
            stage="加速期",
            relative_delta_g=10.0,
            forecast=_forecast(60),
            revision=_revision(100),
            is_ignition=True,
            historical_pe_percentile=0.85,
        )
        assert o.final_overlay == FORWARD_PRICEIN_HALF_CAP
        assert "pricein_halve" in o.adjustments_applied

    def test_no_pricein_at_moderate_pe(self):
        o = _overlay(
            stage="加速期",
            relative_delta_g=10.0,
            forecast=_forecast(60),
            historical_pe_percentile=0.60,
        )
        assert "pricein_halve" not in o.adjustments_applied

    def test_pricein_only_for_accelerating(self):
        # 下降拐点 at high PE should not trigger pricein (it's not growth priced-in)
        o = _overlay(
            stage="下降拐点",
            relative_delta_g=-5.0,
            historical_pe_percentile=0.85,
        )
        assert "pricein_halve" not in o.adjustments_applied


class TestResonanceGate:
    def test_resonance_demoted_without_rdg(self):
        # forecast high but relative_delta_g ≤ 0 → demote to weak-confirm delta
        o = _overlay(
            stage="减速期",
            relative_delta_g=-5.0,
            forecast=_forecast(60),
        )
        # base(减速期) + [weak_confirm instead of resonance]
        # = BASE_DECELERATING + FCST_WEAK_CONFIRM
        expected_raw = BASE_DECELERATING + FCST_WEAK_CONFIRM
        assert o.raw_overlay == pytest.approx(expected_raw, abs=0.01)


# ── Clipping ──────────────────────────────────────────────────────────


class TestClipping:
    def test_upside_capped_at_max(self):
        o = _overlay(
            stage="加速期",
            relative_delta_g=10.0,
            forecast=_forecast(60),
            revision=_revision(100),
            is_ignition=True,
        )
        assert o.final_overlay == FORWARD_OVERLAY_MAX

    def test_downside_capped_at_min(self):
        o = _overlay(
            stage="下降拐点",
            relative_delta_g=-10.0,
            revision=_revision(0, "下调"),
        )
        assert o.final_overlay == FORWARD_OVERLAY_MIN

    def test_clip_is_asymmetric(self):
        assert FORWARD_OVERLAY_MAX < abs(FORWARD_OVERLAY_MIN)


# ── Effective percentile ──────────────────────────────────────────────


class TestEffectivePercentile:
    def test_bullish_overlay_lowers_percentile(self):
        # pe_pct 0.70 (70%), overlay +15 → effective 55
        o = _overlay(
            stage="加速期",
            relative_delta_g=10.0,
            forecast=_forecast(60),
            revision=_revision(100),
            is_ignition=True,
            historical_pe_percentile=0.70,
        )
        assert o.effective_percentile == pytest.approx(55.0, abs=0.5)

    def test_bearish_overlay_raises_percentile(self):
        # pe_pct 0.30 (30%), overlay -20 → effective 50 (value trap!)
        o = _overlay(
            stage="下降拐点",
            relative_delta_g=-10.0,
            revision=_revision(0, "下调"),
            historical_pe_percentile=0.30,
        )
        assert o.effective_percentile == pytest.approx(50.0, abs=0.5)

    def test_no_overflow_below_zero(self):
        o = _overlay(
            stage="下降拐点",
            relative_delta_g=-10.0,
            revision=_revision(0, "下调"),
            historical_pe_percentile=0.05,  # already very low
        )
        assert o.effective_percentile >= 0.0

    def test_no_overflow_above_100(self):
        o = _overlay(
            stage="加速期",
            relative_delta_g=10.0,
            forecast=_forecast(60),
            revision=_revision(100),
            is_ignition=True,
            historical_pe_percentile=0.95,
        )
        assert o.effective_percentile <= 100.0


# ── PS cross-check ────────────────────────────────────────────────────


def _vdata(pe, pb, ps) -> ValuationData:
    return ValuationData(ts_code="x", trade_date="20260701", pe_ttm=pe, pb=pb, ps=ps, total_mv=100.0)


class TestPsCrossCheck:
    def test_ps_discount(self):
        # PE at high percentile, PS at low percentile → margins compressed
        history = [
            _vdata(50, 5, 2),  # latest: high PE, low PS
            _vdata(10, 2, 8),
            _vdata(12, 2, 9),
            _vdata(15, 3, 10),
        ]
        result = calculate_ps_crosscheck(history)
        assert result is not None
        assert result.divergence_flag == "PS_DISCOUNT"
        assert result.divergence_pp < -20

    def test_ps_premium(self):
        # PE at low percentile, PS at high percentile → revenue inflation
        history = [
            _vdata(10, 2, 10),  # latest: low PE, high PS
            _vdata(50, 5, 2),
            _vdata(45, 4, 3),
            _vdata(40, 4, 4),
        ]
        result = calculate_ps_crosscheck(history)
        assert result is not None
        assert result.divergence_flag == "PS_PREMIUM"
        assert result.divergence_pp > 20

    def test_consistent(self):
        history = [
            _vdata(20, 3, 4),  # roughly mid-range on both
            _vdata(10, 2, 2),
            _vdata(30, 4, 6),
            _vdata(15, 2.5, 3),
        ]
        result = calculate_ps_crosscheck(history)
        assert result is not None
        assert result.divergence_flag == "CONSISTENT"

    def test_missing_ps_returns_none(self):
        history = [
            _vdata(50, 5, 0.0),
            _vdata(40, 4, 0.0),
            _vdata(30, 3, 0.0),
        ]
        assert calculate_ps_crosscheck(history) is None

    def test_empty_history_returns_none(self):
        assert calculate_ps_crosscheck([]) is None


# ── Integration: full scenario matrix ─────────────────────────────────


class TestScenarios:
    def test_strongest_upside(self):
        o = _overlay(
            stage="加速期",
            relative_delta_g=10.0,
            forecast=_forecast(60),
            revision=_revision(100),
            is_ignition=True,
            delta_g_quarters=4,
            profit_growth=50,
            historical_pe_percentile=0.70,
        )
        assert o.final_overlay == FORWARD_OVERLAY_MAX
        assert o.effective_percentile == pytest.approx(55.0, abs=0.5)

    def test_strongest_downside(self):
        o = _overlay(
            stage="下降拐点",
            relative_delta_g=-5.0,
            forecast=None,
            revision=_revision(0, "下调"),
            delta_g_quarters=4,
            profit_growth=-15,
            historical_pe_percentile=0.30,
        )
        assert o.final_overlay == FORWARD_OVERLAY_MIN
        assert o.effective_percentile == pytest.approx(50.0, abs=0.5)

    def test_turning_point_no_confirmation(self):
        # 上升拐点 + rdg>0 but no forecast → only base
        o = _overlay(
            stage="上升拐点",
            relative_delta_g=5.0,
            forecast=None,
        )
        assert o.final_overlay == BASE_TURNING_UP

    def test_turning_point_with_forecast_confirmation(self):
        o = _overlay(
            stage="上升拐点",
            relative_delta_g=5.0,
            forecast=_forecast(60),
            revision=_revision(100),
        )
        assert o.final_overlay == FORWARD_OVERLAY_MAX

    def test_accelerating_but_below_30pct_growth(self):
        o = _overlay(
            stage="加速期",
            relative_delta_g=10.0,
            forecast=_forecast(60),
            revision=_revision(100),
            is_ignition=True,
            profit_growth=25,
        )
        assert o.final_overlay == 0.0

    def test_data_insufficient_zeros_all(self):
        o = _overlay(
            stage="加速期",
            relative_delta_g=10.0,
            forecast=_forecast(60),
            revision=_revision(100),
            is_ignition=True,
            delta_g_quarters=1,
            historical_pe_percentile=0.70,
        )
        assert o.final_overlay == 0.0
        assert o.effective_percentile == pytest.approx(70.0, abs=0.5)  # unchanged

    def test_negative_rdg_with_ignition_ignored(self):
        o = _overlay(stage="减速期", relative_delta_g=-5.0, is_ignition=True)
        assert o.ignition_adjustment == 0.0
        assert o.final_overlay == BASE_DECELERATING


# ── Dataclass sanity ──────────────────────────────────────────────────


class TestDataclassSanity:
    def test_all_fields_populated(self):
        o = _overlay()
        assert o.base_adjustment is not None
        assert o.forecast_adjustment is not None
        assert o.ignition_adjustment is not None
        assert o.raw_overlay is not None
        assert o.final_overlay is not None
        assert o.effective_percentile is not None
        assert isinstance(o.adjustments_applied, list)
        assert isinstance(o.data_sufficient, bool)
        assert isinstance(o.confidence_note, str)

    def test_raw_equals_sum_of_subsignals_before_corrections(self):
        # neutral case: no corrections fire
        o = _overlay(stage="减速期", relative_delta_g=0.0)
        assert o.raw_overlay == pytest.approx(
            o.base_adjustment + o.forecast_adjustment + o.ignition_adjustment, abs=0.01
        )
