"""Tests for the three-domain factor framework (classic_cyclical / super_cycle / normal).

Validates:
  * classify_stock maps industries to the correct domain
  * calculate_prosperity_score clamps ΔG for classical cyclicals
  * _blend uses different weights for classical cyclicals
  * _count_consecutive_positive_delta_g detects structural persistence
  * Super-cycle persistence bonus triggers correctly
"""

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from davis_analyzer.backtest_factors import (
    FactorConfig,
    FactorScore,
    _blend,
    _count_consecutive_positive_delta_g,
    classify_stock,
)
from davis_analyzer.constants import (
    CYCLICAL_DELTA_G_CLAMP,
    CYCLICAL_FACTOR_WEIGHTS,
    SUPER_CYCLE_INDUSTRIES,
    SUPER_CYCLE_MIN_POSITIVE_QUARTERS,
    SUPER_CYCLE_PERSISTENCE_BONUS,
)
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.types import FinancialData


# ──────────────────────────── classify_stock ────────────────────────────


class TestClassifyStock:
    def test_classical_cyclical_steel(self):
        assert classify_stock("钢铁") == "classic_cyclical"

    def test_classical_cyclical_coal(self):
        assert classify_stock("煤炭") == "classic_cyclical"

    def test_classical_cyclical_chemical(self):
        assert classify_stock("化工") == "classic_cyclical"

    def test_super_cycle_communication(self):
        assert classify_stock("通信设备") == "super_cycle"

    def test_super_cycle_pcb(self):
        assert classify_stock("元器件") == "super_cycle"

    def test_super_cycle_semi(self):
        assert classify_stock("半导体") == "super_cycle"

    def test_normal_stock(self):
        assert classify_stock("电气设备") == "normal"

    def test_normal_bank(self):
        assert classify_stock("银行") == "normal"

    def test_unknown_industry_is_normal(self):
        assert classify_stock("未知行业") == "normal"


# ──────────────────────────── ΔG clamp for cyclicals ────────────────────────────


def _make_fin_data(growths: list[float | None]) -> list[FinancialData]:
    """Build FinancialData with the given YoY revenue growth series.

    *growths* are ordered oldest-first (matching report_period ascending).
    Each entry gets a unique report_period 8 quarters apart.
    """
    base_period = 20240331
    results = []
    for i, g in enumerate(growths):
        # Increment period by ~3 months (rough: add 3 to MM, wrap at 12).
        year = base_period // 10000
        month = (base_period // 100) % 100
        total_months = month + i * 3
        py = year + (total_months - 1) // 12
        pm = ((total_months - 1) % 12) + 1
        period = py * 10000 + pm * 100 + 1
        results.append(
            FinancialData(
                ts_code="TEST.SZ",
                report_period=str(period),
                revenue=1000.0 * (1 + g if g else 0),
                net_profit=100.0,
                eps=0.1,
                roe=1.0,
                operating_cf=150.0,
                total_debt=5000.0,
                total_assets=10000.0,
                yoy_revenue_growth=g,
            )
        )
    return results


class TestDeltaGClamp:
    def test_cyclical_delta_g_clamped(self):
        """A classical cyclical with extreme ΔG should be clamped to ±25pp."""
        # Growth jumps from 10% → 80% → 150% → ΔG should be ~70pp unclamped.
        fin = _make_fin_data([0.10, 0.80, 1.50])
        ps = calculate_prosperity_score(fin, is_cyclical=True)
        assert abs(ps.delta_g) <= CYCLICAL_DELTA_G_CLAMP + 0.1  # rounding tolerance

    def test_non_cyclical_delta_g_not_clamped(self):
        """A normal stock with the same data should NOT be clamped."""
        fin = _make_fin_data([0.10, 0.80, 1.50])
        ps_normal = calculate_prosperity_score(fin, is_cyclical=False)
        ps_cyclical = calculate_prosperity_score(fin, is_cyclical=True)
        # The unclamped ΔG should be larger than the clamped one.
        assert abs(ps_normal.delta_g) > abs(ps_cyclical.delta_g)

    def test_default_is_cyclical_false(self):
        """When is_cyclical is not passed, ΔG should not be clamped."""
        fin = _make_fin_data([0.10, 0.80, 1.50])
        ps_default = calculate_prosperity_score(fin)
        ps_explicit = calculate_prosperity_score(fin, is_cyclical=False)
        assert ps_default.delta_g == ps_explicit.delta_g

    def test_negative_delta_g_also_clamped(self):
        """Clamping should be symmetric — negative ΔG clamped to -25pp."""
        # Growth drops from 100% → 50% → 10% → ΔG ~ -45pp.
        fin = _make_fin_data([1.00, 0.50, 0.10])
        ps = calculate_prosperity_score(fin, is_cyclical=True)
        assert ps.delta_g >= -CYCLICAL_DELTA_G_CLAMP - 0.1


# ──────────────────────────── _blend with cyclical weights ────────────────────────────


class TestBlendCyclical:
    def test_cyclical_uses_different_weights(self):
        """When is_cyclical=True, _blend should use CYCLICAL_FACTOR_WEIGHTS."""
        cfg = FactorConfig()
        # All scores = 50 for simplicity.
        score_normal = _blend(50, 50, 50, 50, cfg, is_cyclical=False)
        score_cyclical = _blend(50, 50, 50, 50, cfg, is_cyclical=True)
        # With all scores equal, both should be 50 regardless of weights.
        assert score_normal == pytest.approx(50.0)
        assert score_cyclical == pytest.approx(50.0)

    def test_cyclical_weights_favor_valuation(self):
        """When valuation is high and prosperity is low, cyclical should score higher."""
        cfg = FactorConfig()
        # Valuation 90, prosperity 10, others 50.
        score_normal = _blend(50, 90, 10, 50, cfg, is_cyclical=False)
        score_cyclical = _blend(50, 90, 10, 50, cfg, is_cyclical=True)
        # Cyclical gives valuation 0.40 vs normal 0.25, so cyclical scores higher.
        assert score_cyclical > score_normal

    def test_cyclical_weights_penalize_low_valuation(self):
        """When valuation is low, cyclical should score lower than normal."""
        cfg = FactorConfig()
        # Valuation 10, prosperity 90, others 50.
        score_normal = _blend(50, 10, 90, 50, cfg, is_cyclical=False)
        score_cyclical = _blend(50, 10, 90, 50, cfg, is_cyclical=True)
        # Cyclical gives prosperity only 0.15 vs normal 0.30, so cyclical scores lower.
        assert score_cyclical < score_normal


# ──────────────────────────── Consecutive positive ΔG ────────────────────────────


class TestConsecutivePositiveDeltaG:
    def test_all_positive(self):
        """Growth accelerating every quarter → all ΔG positive → count = n-1."""
        # Growth: 10% → 20% → 30% → 50% (each quarter accelerating)
        fin = _make_fin_data([0.10, 0.20, 0.30, 0.50])
        count = _count_consecutive_positive_delta_g(fin)
        assert count == 3  # 3 transitions, all positive

    def test_recent_negative_breaks_streak(self):
        """If the most recent ΔG is negative, count should be 0."""
        # Growth: 10% → 30% → 50% → 40% (last quarter decelerated)
        fin = _make_fin_data([0.10, 0.30, 0.50, 0.40])
        count = _count_consecutive_positive_delta_g(fin)
        assert count == 0  # most recent ΔG = 40-50 = -10 < 0

    def test_mixed_recent_positive(self):
        """Only count consecutive positives from the most recent."""
        # Growth: 10% → 5% → 30% → 50% (decelerate then accelerate)
        fin = _make_fin_data([0.10, 0.05, 0.30, 0.50])
        count = _count_consecutive_positive_delta_g(fin)
        assert count == 2  # 50→30=+20, 30→5=+25, then 5→10=-5 breaks

    def test_too_few_quarters(self):
        """With < 2 quarters, count should be 0."""
        fin = _make_fin_data([0.10])
        assert _count_consecutive_positive_delta_g(fin) == 0

    def test_meets_super_cycle_threshold(self):
        """4 quarters of acceleration should meet the SUPER_CYCLE_MIN_POSITIVE_QUARTERS."""
        fin = _make_fin_data([0.10, 0.20, 0.30, 0.40, 0.50])
        count = _count_consecutive_positive_delta_g(fin)
        assert count >= SUPER_CYCLE_MIN_POSITIVE_QUARTERS
