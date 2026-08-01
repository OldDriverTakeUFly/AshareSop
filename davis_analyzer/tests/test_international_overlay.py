"""Unit tests for international_overlay — overseas risk scoring & regime downgrade.

Tests the sub-signal scorers (pure functions) and the downgrade logic without
hitting the database. Database-dependent functions (get_international_risk)
are tested via monkeypatch of the loader.
"""

from __future__ import annotations

from davis_analyzer.international_overlay import (
    FORCE_BEAR_SCORE,
    DOWNGRADE_SCORE,
    InternationalRisk,
    SubSignal,
    _score_bond_yield,
    _score_us_vix,
    _score_usdjpy,
    _score_us_equity,
    apply_overseas_overlay,
    get_international_risk,
)


# ── Sub-signal scorers ────────────────────────────────────────────────


class TestBondYieldScorer:
    def test_flat_yield_zero_risk(self):
        s = _score_bond_yield({"us_10y": 4.5, "us_10y_change_bp": 0.0}, None)
        assert s.score == 0.0

    def test_falling_yield_zero_risk(self):
        s = _score_bond_yield({"us_10y": 4.4, "us_10y_change_bp": -10.0}, None)
        assert s.score == 0.0

    def test_surge_10bp_scores_half(self):
        s = _score_bond_yield({"us_10y": 4.6, "us_10y_change_bp": 10.0}, None)
        assert 40 <= s.score <= 60

    def test_violent_surge_20bp_max(self):
        s = _score_bond_yield({"us_10y": 4.7, "us_10y_change_bp": 20.0}, None)
        assert s.score == 100.0

    def test_missing_change_derived_from_prev(self):
        s = _score_bond_yield({"us_10y": 4.6, "us_10y_change_bp": None}, {"us_10y": 4.5})
        assert s.score > 0  # +10bp derived

    def test_no_data(self):
        s = _score_bond_yield({}, None)
        assert s.score == 0.0
        assert s.raw_value is None


class TestUsVixScorer:
    def test_calm(self):
        s = _score_us_vix({"us_vix": 18.0}, None)
        assert s.score == 0.0

    def test_fear_30(self):
        s = _score_us_vix({"us_vix": 30.0}, None)
        assert 45 <= s.score <= 55

    def test_panic_40_plus(self):
        s = _score_us_vix({"us_vix": 45.0}, None)
        assert s.score == 100.0

    def test_qvix_fallback(self):
        s = _score_us_vix({"us_vix": None, "vix": 25.0}, None)
        assert s.score > 0
        assert "QVIX" in s.detail


class TestUsdJpyScorer:
    def test_normal(self):
        s = _score_usdjpy({"usd_jpy": 150.0})
        assert s.score == 0.0

    def test_warn_160(self):
        s = _score_usdjpy({"usd_jpy": 160.0})
        assert 25 <= s.score <= 40

    def test_extreme_165_plus(self):
        s = _score_usdjpy({"usd_jpy": 166.0})
        assert s.score == 100.0
        assert "套息脆弱" in s.detail


class TestUsEquityScorer:
    def test_rally(self):
        s = _score_us_equity({"sp500_pct": 1.5, "nasdaq_pct": 2.0})
        assert s.score == 0.0

    def test_moderate_drop(self):
        s = _score_us_equity({"sp500_pct": -1.0, "nasdaq_pct": -2.0})
        assert 40 <= s.score <= 60  # worst = -2% → ~50

    def test_crash(self):
        s = _score_us_equity({"nasdaq_pct": -5.0})
        assert s.score == 100.0

    def test_uses_worst(self):
        s = _score_us_equity({"sp500_pct": 0.5, "nasdaq_pct": -3.0})
        assert s.score > 50  # driven by -3%


# ── Downgrade logic ───────────────────────────────────────────────────


class TestApplyOverlay:
    def _risk(self, score: float, sufficient: bool = True) -> InternationalRisk:
        return InternationalRisk(
            trade_date="20260730",
            composite_score=score,
            data_sufficient=sufficient,
            confidence_note="test",
        )

    def test_low_risk_unchanged(self):
        for regime in ("bull", "bear", "neutral"):
            assert apply_overseas_overlay(regime, self._risk(20.0)) == regime

    def test_high_risk_demotes_bull(self):
        assert apply_overseas_overlay("bull", self._risk(55.0)) == "neutral"

    def test_extreme_risk_forces_bear(self):
        assert apply_overseas_overlay("bull", self._risk(80.0)) == "bear"
        assert apply_overseas_overlay("neutral", self._risk(80.0)) == "bear"

    def test_never_upgrades(self):
        # bear stays bear even at 0 risk
        assert apply_overseas_overlay("bear", self._risk(0.0)) == "bear"

    def test_missing_data_no_downgrade(self):
        assert apply_overseas_overlay("bull", self._risk(90.0, sufficient=False)) == "bull"


# ── get_international_risk (monkeypatched loader) ─────────────────────


class TestGetInternationalRisk:
    def test_missing_data_fails_safe(self, monkeypatch):
        monkeypatch.setattr(
            "davis_analyzer.international_overlay._load_overseas_row", lambda d: None
        )
        risk = get_international_risk("20260101")
        assert risk.composite_score == 0.0
        assert not risk.data_sufficient

    def test_extreme_day(self, monkeypatch):
        monkeypatch.setattr(
            "davis_analyzer.international_overlay._load_overseas_row",
            lambda d: {
                "us_10y": 4.7, "us_10y_change_bp": 15.0,
                "us_vix": 35.0, "usd_jpy": 164.0,
                "sp500_pct": -2.5, "nasdaq_pct": -3.0, "dow_pct": -2.0,
            },
        )
        monkeypatch.setattr(
            "davis_analyzer.international_overlay._load_prev_overseas", lambda d, **k: None
        )
        risk = get_international_risk("20260717")
        assert risk.composite_score >= FORCE_BEAR_SCORE  # multi-signal resonance
        assert risk.level == "极端"

    def test_calm_day(self, monkeypatch):
        monkeypatch.setattr(
            "davis_analyzer.international_overlay._load_overseas_row",
            lambda d: {
                "us_10y": 4.3, "us_10y_change_bp": -2.0,
                "us_vix": 15.0, "usd_jpy": 150.0,
                "sp500_pct": 0.5, "nasdaq_pct": 0.8, "dow_pct": 0.3,
            },
        )
        monkeypatch.setattr(
            "davis_analyzer.international_overlay._load_prev_overseas", lambda d, **k: None
        )
        risk = get_international_risk("20260701")
        assert risk.composite_score < DOWNGRADE_SCORE
        assert risk.level == "偏低"
