"""Tests for davis_analyzer.prosperity — 景气度 scoring engine."""

import pytest

from davis_analyzer.constants import PROSPERITY_WEIGHTS
from davis_analyzer.prosperity import (
    batch_prosperity,
    calculate_delta_g,
    calculate_duration_score,
    calculate_profit_score,
    calculate_prosperity_score,
    calculate_revenue_score,
    calculate_slope_score,
    dupont_decomposition,
    _extract_yoy_series,
    _growth_to_raw_score,
    _growth_to_raw_score_profit,
)
from davis_analyzer.types import FinancialData, ProsperityScore


def _fd(ts_code: str = "000001.SZ", period: str = "2024Q1", **kw) -> FinancialData:
    defaults = dict(
        ts_code=ts_code,
        report_period=period,
        revenue=100.0,
        net_profit=10.0,
        eps=0.5,
        roe=12.0,
        operating_cf=15.0,
        total_debt=50.0,
        total_assets=200.0,
        yoy_revenue_growth=None,
        yoy_profit_growth=None,
    )
    defaults.update(kw)
    return FinancialData(**defaults)


class TestGrowthToRawScore:
    def test_high_growth_over_30(self):
        assert _growth_to_raw_score(50) == pytest.approx(85.71, abs=0.01)

    def test_medium_growth_20(self):
        assert _growth_to_raw_score(20) == pytest.approx(65.0, abs=0.01)

    def test_low_growth_5(self):
        assert _growth_to_raw_score(5) == pytest.approx(37.5, abs=0.01)

    def test_negative_growth(self):
        assert _growth_to_raw_score(-10) == pytest.approx(12.5, abs=0.01)

    def test_deep_negative_clamps_to_zero(self):
        assert _growth_to_raw_score(-30) == 0.0

    def test_zero_growth(self):
        assert _growth_to_raw_score(0) == pytest.approx(25.0, abs=0.01)


class TestGrowthToRawScoreProfit:
    def test_high_growth_over_50(self):
        assert _growth_to_raw_score_profit(60) == pytest.approx(84.0, abs=0.01)

    def test_at_50_boundary(self):
        assert _growth_to_raw_score_profit(50) == pytest.approx(80.0, abs=0.01)

    def test_mid_growth_30(self):
        assert _growth_to_raw_score_profit(30) == pytest.approx(60.0, abs=0.01)

    def test_at_20_boundary(self):
        assert _growth_to_raw_score_profit(20) == pytest.approx(50.0, abs=0.01)

    def test_low_growth_10(self):
        assert _growth_to_raw_score_profit(10) == pytest.approx(37.5, abs=0.01)

    def test_negative_growth(self):
        assert _growth_to_raw_score_profit(-10) == pytest.approx(12.5, abs=0.01)

    def test_zero_growth(self):
        assert _growth_to_raw_score_profit(0) == pytest.approx(25.0, abs=0.01)


class TestRevenueScore:
    def test_empty_history(self):
        assert calculate_revenue_score([]) == 0.0

    def test_single_high_growth(self):
        score = calculate_revenue_score([50])
        assert 80 <= score <= 100

    def test_single_negative_growth(self):
        score = calculate_revenue_score([-10])
        assert 0 <= score <= 20

    def test_declining_growth_trend(self):
        declining = [10, 20, 30, 40]
        score_declining = calculate_revenue_score(declining)
        growing = [40, 30, 20, 10]
        score_growing = calculate_revenue_score(growing)
        assert score_growing > score_declining

    def test_profit_score_independent_from_revenue(self):
        data = [25, 15, 35]
        assert calculate_profit_score(data) != calculate_revenue_score(data)

    def test_profit_score_independent_mapping(self):
        score = calculate_profit_score([50])
        assert score == pytest.approx(80.0, abs=0.01)

    def test_profit_score_at_20pct(self):
        score = calculate_profit_score([20])
        assert score == pytest.approx(50.0, abs=0.01)


class TestSlopeScore:
    def test_too_few_points_returns_50(self):
        assert calculate_slope_score([1, 2]) == 50.0

    def test_too_few_points_single(self):
        assert calculate_slope_score([5]) == 50.0

    def test_empty(self):
        assert calculate_slope_score([]) == 50.0

    def test_strongly_increasing(self):
        score = calculate_slope_score([10, 20, 30, 40, 50])
        assert score > 70

    def test_strongly_decreasing(self):
        score = calculate_slope_score([50, 40, 30, 20, 10])
        assert score < 30

    def test_flat_series(self):
        score = calculate_slope_score([100, 100, 100, 100])
        assert score == pytest.approx(50.0, abs=1.0)


class TestDurationScore:
    def test_zero_positive_quarters(self):
        assert calculate_duration_score([-5, 10, 20]) == 0

    def test_one_positive_quarter(self):
        score = calculate_duration_score([5, -10])
        assert score == pytest.approx(27.5, abs=0.01)

    def test_two_positive_quarters(self):
        score = calculate_duration_score([5, 10, -3])
        assert score == pytest.approx(53.75, abs=0.01)

    def test_three_positive_quarters(self):
        score = calculate_duration_score([5, 10, 15, -1])
        assert score == pytest.approx(80.0, abs=0.01)

    def test_four_plus_positive_quarters(self):
        assert calculate_duration_score([5, 10, 15, 20, 25]) == 100.0

    def test_all_negative(self):
        assert calculate_duration_score([-5, -10, -15]) == 0

    def test_empty(self):
        assert calculate_duration_score([]) == 0

    def test_duration_bonus_high_growth(self):
        score = calculate_duration_score([50, 50, 50])
        assert score == pytest.approx(100.0, abs=0.01)

    def test_duration_bonus_low_growth(self):
        score = calculate_duration_score([0.1, 0.1, 0.1, 0.1])
        assert score == pytest.approx(100.0, abs=0.01)


class TestDeltaG:
    def test_growth_decelerating(self):
        assert calculate_delta_g(30, 50) == -20.0

    def test_growth_accelerating(self):
        assert calculate_delta_g(40, 10) == 30.0

    def test_no_change(self):
        assert calculate_delta_g(25, 25) == 0.0

    def test_negative_to_positive(self):
        assert calculate_delta_g(10, -5) == 15.0


class TestDuPontDecomposition:
    def test_net_margin_driven(self):
        result = dupont_decomposition(
            roe=18.0, net_margin=5.0, asset_turnover=0.50, leverage_ratio=1.2
        )
        assert result == "净利率驱动（定价权强）"

    def test_turnover_driven(self):
        result = dupont_decomposition(
            roe=18.0, net_margin=0.05, asset_turnover=2.5, leverage_ratio=1.44
        )
        assert result == "周转率驱动（需求/效率提升）"

    def test_leverage_driven(self):
        result = dupont_decomposition(
            roe=18.0, net_margin=0.03, asset_turnover=0.40, leverage_ratio=15.0
        )
        assert result == "杠杆驱动（需警惕可持续性）"

    def test_tie_goes_to_net_margin(self):
        result = dupont_decomposition(
            roe=10.0, net_margin=0.50, asset_turnover=0.50, leverage_ratio=0.40
        )
        assert result == "净利率驱动（定价权强）"


class TestProsperityScore:
    def _make_data_with_growth(
        self,
        revenues: list[float],
        profits: list[float],
        ts_code: str = "000001.SZ",
    ) -> list[FinancialData]:
        quarters = ["2023Q1", "2023Q2", "2023Q3", "2023Q4", "2024Q1"]
        result = []
        for i, (rev, prof) in enumerate(zip(revenues, profits)):
            yoy_rev = None
            yoy_prof = None
            if i >= 4:
                base_rev = revenues[i - 4]
                base_prof = profits[i - 4]
                yoy_rev = (rev - base_rev) / abs(base_rev) if abs(base_rev) > 1e-9 else None
                yoy_prof = (prof - base_prof) / abs(base_prof) if abs(base_prof) > 1e-9 else None
            result.append(
                _fd(
                    ts_code=ts_code,
                    period=quarters[i],
                    revenue=rev,
                    net_profit=prof,
                    yoy_revenue_growth=yoy_rev,
                    yoy_profit_growth=yoy_prof,
                )
            )
        return result

    def test_composite_uses_correct_weights(self):
        data = self._make_data_with_growth(
            revenues=[100, 130, 160, 200, 250],
            profits=[10, 14, 18, 24, 30],
        )
        result = calculate_prosperity_score(data)
        w = PROSPERITY_WEIGHTS
        expected = (
            result.revenue_score * w["revenue"]
            + result.profit_score * w["profit"]
            + result.slope_score * w["slope"]
            + result.duration_score * w["duration"]
        )
        assert result.composite_score == pytest.approx(expected, abs=0.01)

    def test_delta_g_from_last_two_quarters(self):
        data = self._make_data_with_growth(
            revenues=[100, 110, 125, 150, 180],
            profits=[10, 11, 13, 16, 20],
        )
        result = calculate_prosperity_score(data)
        assert isinstance(result.delta_g, float)

    def test_empty_data_raises(self):
        with pytest.raises(ValueError):
            calculate_prosperity_score([])

    def test_returns_prosperity_score_type(self):
        data = self._make_data_with_growth(
            revenues=[100, 120],
            profits=[10, 12],
        )
        result = calculate_prosperity_score(data)
        assert isinstance(result, ProsperityScore)
        assert result.ts_code == "000001.SZ"

    def test_cashflow_quality_reduces_profit_score(self):
        data = [
            _fd(
                ts_code="000001.SZ",
                period="2023Q1",
                net_profit=10.0,
                operating_cf=15.0,
                yoy_revenue_growth=0.2,
                yoy_profit_growth=0.3,
            ),
            _fd(
                ts_code="000001.SZ",
                period="2023Q2",
                net_profit=10.0,
                operating_cf=5.0,
                yoy_revenue_growth=0.2,
                yoy_profit_growth=0.3,
            ),
        ]
        result = calculate_prosperity_score(data)
        unadjusted = calculate_profit_score([30.0, 30.0])
        assert result.profit_score < unadjusted
        assert result.profit_score == pytest.approx(30.0, abs=0.01)

    def test_cashflow_quality_skip_when_net_profit_negative(self):
        data = [
            _fd(
                ts_code="000001.SZ",
                period="2023Q1",
                net_profit=-5.0,
                operating_cf=5.0,
                yoy_revenue_growth=0.2,
                yoy_profit_growth=0.3,
            ),
            _fd(
                ts_code="000001.SZ",
                period="2023Q2",
                net_profit=-5.0,
                operating_cf=5.0,
                yoy_revenue_growth=0.2,
                yoy_profit_growth=0.3,
            ),
        ]
        result = calculate_prosperity_score(data)
        unadjusted = calculate_profit_score([30.0, 30.0])
        assert result.profit_score == pytest.approx(unadjusted, abs=0.01)

    def test_delta_g_three_quarter_ma(self):
        data = [
            _fd(
                ts_code="000001.SZ",
                period="2023Q1",
                yoy_revenue_growth=0.1,
                yoy_profit_growth=0.1,
            ),
            _fd(
                ts_code="000001.SZ",
                period="2023Q2",
                yoy_revenue_growth=0.2,
                yoy_profit_growth=0.2,
            ),
            _fd(
                ts_code="000001.SZ",
                period="2023Q3",
                yoy_revenue_growth=0.3,
                yoy_profit_growth=0.3,
            ),
        ]
        result = calculate_prosperity_score(data)
        assert result.delta_g == pytest.approx(10.0, abs=0.01)

    def test_delta_g_fallback_two_quarters(self):
        data = [
            _fd(
                ts_code="000001.SZ",
                period="2023Q1",
                yoy_revenue_growth=0.1,
                yoy_profit_growth=0.1,
            ),
            _fd(
                ts_code="000001.SZ",
                period="2023Q2",
                yoy_revenue_growth=0.4,
                yoy_profit_growth=0.4,
            ),
        ]
        result = calculate_prosperity_score(data)
        assert result.delta_g == pytest.approx(30.0, abs=0.01)


class TestBatchProsperity:
    def test_skips_insufficient_data(self):
        single = [_fd(ts_code="000001.SZ", period="2024Q1")]
        result = batch_prosperity({"000001.SZ": single})
        assert "000001.SZ" not in result

    def test_processes_multiple_stocks(self):
        data_a = [
            _fd(ts_code="000001.SZ", period="2023Q1", revenue=100, net_profit=10),
            _fd(ts_code="000001.SZ", period="2023Q2", revenue=120, net_profit=12),
        ]
        data_b = [
            _fd(ts_code="000002.SZ", period="2023Q1", revenue=200, net_profit=20),
            _fd(ts_code="000002.SZ", period="2023Q2", revenue=250, net_profit=28),
        ]
        result = batch_prosperity({"000001.SZ": data_a, "000002.SZ": data_b})
        assert len(result) == 2
        assert "000001.SZ" in result
        assert "000002.SZ" in result

    def test_empty_map(self):
        assert batch_prosperity({}) == {}


class TestWeights:
    def test_weights_sum_to_one(self):
        total = sum(PROSPERITY_WEIGHTS.values())
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_expected_keys(self):
        expected = {"revenue", "profit", "slope", "duration"}
        assert set(PROSPERITY_WEIGHTS.keys()) == expected

    def test_exact_weight_values(self):
        assert PROSPERITY_WEIGHTS["revenue"] == 0.30
        assert PROSPERITY_WEIGHTS["profit"] == 0.30
        assert PROSPERITY_WEIGHTS["slope"] == 0.25
        assert PROSPERITY_WEIGHTS["duration"] == 0.15


# ── E1. _extract_yoy_series ───────────────────────────────────────────


class TestE1ExtractYoySeries:
    """E1: _extract_yoy_series filters None values and multiplies by 100."""

    def test_empty_list_returns_empty(self):
        assert _extract_yoy_series([], "yoy_revenue_growth") == []

    def test_all_none_returns_empty(self):
        data = [
            _fd(period="2023Q1", yoy_revenue_growth=None),
            _fd(period="2023Q2", yoy_revenue_growth=None),
        ]
        assert _extract_yoy_series(data, "yoy_revenue_growth") == []

    def test_all_real_values_multiplied_by_100(self):
        data = [
            _fd(period="2023Q1", yoy_revenue_growth=5.0),
            _fd(period="2023Q2", yoy_revenue_growth=3.0),
        ]
        result = _extract_yoy_series(data, "yoy_revenue_growth")
        assert result == [500.0, 300.0]

    def test_mixed_none_and_real_keeps_only_real(self):
        data = [
            _fd(period="2023Q1", yoy_revenue_growth=None),
            _fd(period="2023Q2", yoy_revenue_growth=0.05),
            _fd(period="2023Q3", yoy_revenue_growth=None),
            _fd(period="2023Q4", yoy_revenue_growth=0.10),
        ]
        result = _extract_yoy_series(data, "yoy_revenue_growth")
        assert result == [5.0, 10.0]

    def test_genuine_zero_preserved_not_filtered(self):
        """0.0 is not None — must be preserved (T2 fix regression guard)."""
        data = [
            _fd(period="2023Q1", yoy_revenue_growth=0.0),
            _fd(period="2023Q2", yoy_revenue_growth=None),
        ]
        result = _extract_yoy_series(data, "yoy_revenue_growth")
        assert result == [0.0]

    def test_profit_attr_works(self):
        data = [
            _fd(period="2023Q1", yoy_profit_growth=0.2),
            _fd(period="2023Q2", yoy_profit_growth=None),
        ]
        result = _extract_yoy_series(data, "yoy_profit_growth")
        assert result == [20.0]


# ── E4. Decay weighting ──────────────────────────────────────────────


class TestE4DecayWeighting:
    """E4: Recent quarters weighted more heavily via exponential decay (0.8^i)."""

    def test_revenue_recent_high_scores_better(self):
        """[50, 0] (recent high) should score higher than [0, 50] (old high)."""
        recent_high = calculate_revenue_score([50, 0])
        old_high = calculate_revenue_score([0, 50])
        assert recent_high > old_high

    def test_profit_recent_high_scores_better(self):
        """[50, 0] (recent high) should score higher than [0, 50] (old high)."""
        recent_high = calculate_profit_score([50, 0])
        old_high = calculate_profit_score([0, 50])
        assert recent_high > old_high

    def test_revenue_decay_three_quarters(self):
        """With 3 quarters, most recent dominates."""
        recent_first = calculate_revenue_score([40, 10, 0])
        old_first = calculate_revenue_score([0, 10, 40])
        assert recent_first > old_first


# ── E7. CF quality in scoring (calculate_prosperity_score) ───────────


class TestE7CFQualityScoring:
    """E7: Cash-flow quality adjustment in calculate_prosperity_score."""

    @staticmethod
    def _make_data(net_profit: float, operating_cf: float) -> list[FinancialData]:
        return [
            _fd(
                ts_code="000001.SZ",
                period="2023Q1",
                net_profit=net_profit,
                operating_cf=operating_cf,
                yoy_revenue_growth=0.2,
                yoy_profit_growth=0.3,
            ),
            _fd(
                ts_code="000001.SZ",
                period="2023Q2",
                net_profit=net_profit,
                operating_cf=operating_cf,
                yoy_revenue_growth=0.2,
                yoy_profit_growth=0.3,
            ),
        ]

    def test_cf_equal_to_net_profit_no_reduction(self):
        """operating_cf == net_profit → ratio exactly 1.0 → no reduction."""
        data = self._make_data(net_profit=10.0, operating_cf=10.0)
        result = calculate_prosperity_score(data)
        unadjusted = calculate_profit_score([30.0, 30.0])
        assert result.profit_score == pytest.approx(unadjusted, abs=0.01)

    def test_cf_much_greater_clamped_to_one(self):
        """operating_cf >> net_profit → ratio clamped to 1.0 → no reduction."""
        data = self._make_data(net_profit=10.0, operating_cf=1000.0)
        result = calculate_prosperity_score(data)
        unadjusted = calculate_profit_score([30.0, 30.0])
        assert result.profit_score == pytest.approx(unadjusted, abs=0.01)

    def test_cf_zero_reduces_profit_score_to_zero(self):
        """operating_cf == 0 with positive net_profit → cf_quality = 0."""
        data = self._make_data(net_profit=10.0, operating_cf=0.0)
        result = calculate_prosperity_score(data)
        assert result.profit_score == pytest.approx(0.0, abs=0.01)

    def test_cf_skip_when_net_profit_negative(self):
        """Negative net_profit → CF quality adjustment skipped entirely."""
        data = self._make_data(net_profit=-5.0, operating_cf=5.0)
        result = calculate_prosperity_score(data)
        unadjusted = calculate_profit_score([30.0, 30.0])
        assert result.profit_score == pytest.approx(unadjusted, abs=0.01)


# ── E8. calculate_profit_score parity ────────────────────────────────


class TestE8ProfitScoreParity:
    """E8: calculate_profit_score parity tests mirroring revenue coverage."""

    def test_empty_list_returns_zero(self):
        assert calculate_profit_score([]) == 0.0

    def test_single_negative_growth_low_score(self):
        score = calculate_profit_score([-10.0])
        assert score <= 20.0

    def test_multi_quarter_decay_weighting(self):
        """Recent high growth scores better than old high growth."""
        recent_high = calculate_profit_score([50.0, 0.0])
        old_high = calculate_profit_score([0.0, 50.0])
        assert recent_high > old_high

    def test_all_positive_growth_high_score(self):
        score = calculate_profit_score([30.0, 25.0, 20.0])
        assert score > 50.0

    def test_single_high_growth(self):
        score = calculate_profit_score([50.0])
        assert 70.0 <= score <= 100.0
