import pytest

from davis_analyzer.constants import DAVIS_DOUBLE_WEIGHTS
from davis_analyzer.distress import (
    calculate_distress_score,
    check_balance_sheet,
    check_delta_g_positive,
    check_eps_decline,
    check_financial_health,
    check_operating_cf,
    check_pe_pb_percentile,
    check_profit_inflection,
    check_revenue_inflection,
    check_roe_trend,
)
from davis_analyzer.scoring import calculate_davis_double_score, rank_stocks
from davis_analyzer.types import DavisDoubleScore

# ── Layer 1 signal tests ────────────────────────────────────────────────────


class TestCheckEpsDecline:
    def test_decline_exceeds_30_percent(self):
        assert check_eps_decline([0.5, 1.0]) == pytest.approx(1.0, abs=0.01)

    def test_decline_just_under_30_percent(self):
        result = check_eps_decline([0.71, 1.0])
        assert 0.0 < result < 1.0
        assert result == pytest.approx(0.29 / 0.30, abs=0.02)

    def test_no_decline_returns_zero(self):
        assert check_eps_decline([1.5, 1.0]) == 0.0

    def test_insufficient_history(self):
        assert check_eps_decline([0.5]) == 0.0
        assert check_eps_decline([]) == 0.0

    def test_previous_near_zero(self):
        assert check_eps_decline([0.5, 0.0]) == 0.0

    def test_same_quarter_comparison_5_periods(self):
        # eps_history[0] vs eps_history[4] when 5+ elements
        # [2.0, 99, 99, 99, 1.0] → decline = (1.0-2.0)/1.0 = -1.0 → 0.0
        assert check_eps_decline([2.0, 99.0, 99.0, 99.0, 1.0]) == 0.0
        # [0.5, 99, 99, 99, 1.0] → decline = 0.5 → 1.0
        assert check_eps_decline([0.5, 99.0, 99.0, 99.0, 1.0]) == pytest.approx(1.0, abs=0.01)


class TestCheckPePbPercentile:
    def test_both_very_low(self):
        result = check_pe_pb_percentile(0.02, 0.02)
        assert result == pytest.approx(0.98, abs=0.01)

    def test_both_at_10_percent(self):
        result = check_pe_pb_percentile(0.10, 0.10)
        assert result == pytest.approx(0.90, abs=0.01)

    def test_both_mid(self):
        result = check_pe_pb_percentile(0.50, 0.50)
        assert result == pytest.approx(0.50, abs=0.01)

    def test_both_high(self):
        result = check_pe_pb_percentile(0.90, 0.90)
        assert result == pytest.approx(0.10, abs=0.01)

    def test_both_max(self):
        result = check_pe_pb_percentile(1.0, 1.0)
        assert result == pytest.approx(0.0, abs=0.01)


class TestCheckFinancialHealth:
    def test_healthy(self):
        assert check_financial_health(0.3, 100.0) == 1.0

    def test_high_debt_only(self):
        assert check_financial_health(0.6, 100.0) == 0.5

    def test_negative_cf_only(self):
        assert check_financial_health(0.3, -50.0) == 0.5

    def test_both_bad(self):
        assert check_financial_health(0.6, -50.0) == 0.0


# ── Layer 2 signal tests ────────────────────────────────────────────────────


class TestCheckBalanceSheet:
    def test_very_low_debt_ratio(self):
        result = check_balance_sheet(100.0, 1000.0)
        assert result == pytest.approx(0.8, abs=0.01)

    def test_no_debt(self):
        result = check_balance_sheet(0.0, 1000.0)
        assert result == pytest.approx(1.0, abs=0.01)

    def test_high_debt_ratio(self):
        result = check_balance_sheet(600.0, 1000.0)
        assert result == 0.0

    def test_exactly_half(self):
        result = check_balance_sheet(500.0, 1000.0)
        assert result == 0.0

    def test_zero_assets(self):
        assert check_balance_sheet(100.0, 0.0) == 0.0


class TestCheckOperatingCf:
    def test_positive_with_assets(self):
        result = check_operating_cf(200.0, 1000.0)
        assert result == pytest.approx(0.2, abs=0.01)

    def test_very_large_cf_ratio_capped(self):
        result = check_operating_cf(2000.0, 1000.0)
        assert result == 1.0

    def test_negative_cf(self):
        result = check_operating_cf(-50.0, 1000.0)
        assert result == 0.0

    def test_zero_cf(self):
        result = check_operating_cf(0.0, 1000.0)
        assert result == 0.0

    def test_no_assets_fallback_positive(self):
        assert check_operating_cf(100.0) == 1.0

    def test_no_assets_fallback_negative(self):
        assert check_operating_cf(-50.0) == 0.0


class TestCheckRoeTrend:
    def test_significant_improvement(self):
        result = check_roe_trend([16.0, 10.0])
        assert result == pytest.approx(1.0, abs=0.01)

    def test_moderate_improvement(self):
        result = check_roe_trend([12.0, 10.0])
        assert result == pytest.approx(0.4, abs=0.01)

    def test_stable(self):
        result = check_roe_trend([10.0, 10.0])
        assert result == 0.0

    def test_declining(self):
        result = check_roe_trend([8.0, 10.0])
        assert result == 0.0

    def test_insufficient_data(self):
        assert check_roe_trend([5.0]) == 0.0
        assert check_roe_trend([]) == 0.0

    def test_same_quarter_comparison_5_periods(self):
        # [16, 99, 99, 99, 10] → diff = 6 → min(1.0, 6/5) = 1.0
        result = check_roe_trend([16.0, 99.0, 99.0, 99.0, 10.0])
        assert result == pytest.approx(1.0, abs=0.01)
        # [8, 99, 99, 99, 10] → diff = -2 → 0.0
        result = check_roe_trend([8.0, 99.0, 99.0, 99.0, 10.0])
        assert result == 0.0


# ── Layer 3 signal tests ────────────────────────────────────────────────────


class TestCheckRevenueInflection:
    def test_strong_inflection(self):
        # swing = 5 - (-10) = 15 → 15/20 = 0.75
        result = check_revenue_inflection([5.0, -10.0])
        assert result == pytest.approx(0.75, abs=0.01)

    def test_full_inflection(self):
        # swing = 15 - (-10) = 25 → 25/20 = 1.25 → capped at 1.0
        result = check_revenue_inflection([15.0, -10.0])
        assert result == pytest.approx(1.0, abs=0.01)

    def test_still_declining(self):
        # swing = -3 - (-10) = 7 → 7/20 = 0.35
        result = check_revenue_inflection([-3.0, -10.0])
        assert result == pytest.approx(0.35, abs=0.01)

    def test_decelerating_growth(self):
        # swing = 5 - 10 = -5 → 0.0
        result = check_revenue_inflection([5.0, 10.0])
        assert result == 0.0

    def test_insufficient_data(self):
        assert check_revenue_inflection([5.0]) == 0.0


class TestCheckProfitInflection:
    def test_strong_inflection(self):
        # swing = 8 - (-5) = 13 → 13/20 = 0.65
        result = check_profit_inflection([8.0, -5.0])
        assert result == pytest.approx(0.65, abs=0.01)

    def test_no_inflection(self):
        # swing = 5 - 10 = -5 → 0.0
        result = check_profit_inflection([5.0, 10.0])
        assert result == 0.0

    def test_insufficient_data(self):
        assert check_profit_inflection([]) == 0.0


class TestCheckDeltaGPositive:
    def test_strong_positive(self):
        result = check_delta_g_positive(0.20)
        assert result == pytest.approx(1.0, abs=0.01)

    def test_moderate_positive(self):
        result = check_delta_g_positive(0.05)
        assert result == pytest.approx(0.5, abs=0.01)

    def test_zero(self):
        assert check_delta_g_positive(0.0) == 0.0

    def test_negative(self):
        assert check_delta_g_positive(-3.0) == 0.0


# ── Distress score aggregation tests ────────────────────────────────────────


class TestCalculateDistressScore:
    def _make_high_score_kwargs(self):
        return dict(
            eps_history=[0.5, 1.0],
            pe_pct=0.01,
            pb_pct=0.01,
            debt_ratio=0.1,
            operating_cf=500.0,
            total_debt=100.0,
            total_assets=1000.0,
            roe_history=[16.0, 10.0],
            revenue_history=[15.0, -10.0],
            profit_history=[15.0, -5.0],
            delta_g=0.20,
            ts_code="000001.SZ",
        )

    def _make_low_score_kwargs(self):
        return dict(
            eps_history=[1.5, 1.0],
            pe_pct=1.0,
            pb_pct=1.0,
            debt_ratio=0.9,
            operating_cf=-50.0,
            total_debt=900.0,
            total_assets=1000.0,
            roe_history=[5.0, 10.0],
            revenue_history=[-5.0, 5.0],
            profit_history=[-5.0, 5.0],
            delta_g=-0.10,
            ts_code="000002.SZ",
        )

    def test_high_score_profile(self):
        result = calculate_distress_score(**self._make_high_score_kwargs())
        assert result.layer1_score > 90.0
        assert result.layer2_score > 50.0
        assert result.layer3_score > 90.0
        assert result.total_score > 70.0
        assert result.ts_code == "000001.SZ"

    def test_low_score_profile(self):
        result = calculate_distress_score(**self._make_low_score_kwargs())
        assert result.layer1_score == 0.0
        assert result.layer2_score == 0.0
        assert result.layer3_score == 0.0
        assert result.total_score == 0.0

    def test_signals_detail_contains_floats(self):
        result = calculate_distress_score(**self._make_high_score_kwargs())
        detail = result.signals_detail
        assert "layer1" in detail
        assert "layer2" in detail
        assert "layer3" in detail
        assert len(detail["layer1"]) == 3
        assert len(detail["layer2"]) == 3
        assert len(detail["layer3"]) == 3
        for layer_key in ("layer1", "layer2", "layer3"):
            for signal_val in detail[layer_key].values():
                assert isinstance(signal_val, float)
                assert 0.0 <= signal_val <= 1.0

    def test_total_weighted_formula(self):
        result = calculate_distress_score(**self._make_high_score_kwargs())
        expected = result.layer1_score * 0.3 + result.layer2_score * 0.3 + result.layer3_score * 0.4
        assert result.total_score == pytest.approx(expected, abs=0.02)


class TestDistressScoresDifferentiated:
    """Verify different stock profiles produce meaningfully different scores."""

    def _profile_distressed_reversal(self):
        """A distressed stock with strong reversal signals."""
        return dict(
            eps_history=[0.5, 1.0],
            pe_pct=0.02,
            pb_pct=0.03,
            debt_ratio=0.2,
            operating_cf=400.0,
            total_debt=200.0,
            total_assets=1000.0,
            roe_history=[14.0, 10.0],
            revenue_history=[12.0, -8.0],
            profit_history=[10.0, -6.0],
            delta_g=0.15,
            ts_code="DISTRESSED.SZ",
        )

    def _profile_healthy_growing(self):
        """A healthy stock already growing — low distress-reversal score."""
        return dict(
            eps_history=[1.5, 1.0],
            pe_pct=0.80,
            pb_pct=0.75,
            debt_ratio=0.65,
            operating_cf=-30.0,
            total_debt=700.0,
            total_assets=1000.0,
            roe_history=[6.0, 10.0],
            revenue_history=[3.0, 8.0],
            profit_history=[2.0, 7.0],
            delta_g=-0.05,
            ts_code="HEALTHY.SZ",
        )

    def test_scores_differ_by_more_than_5_points(self):
        score_a = calculate_distress_score(**self._profile_distressed_reversal())
        score_b = calculate_distress_score(**self._profile_healthy_growing())
        assert abs(score_a.total_score - score_b.total_score) > 5.0

    def test_distressed_scores_higher(self):
        score_a = calculate_distress_score(**self._profile_distressed_reversal())
        score_b = calculate_distress_score(**self._profile_healthy_growing())
        assert score_a.total_score > score_b.total_score

    def test_all_layer_scores_differentiated(self):
        """At least 2 of 3 layer scores should differ between profiles."""
        score_a = calculate_distress_score(**self._profile_distressed_reversal())
        score_b = calculate_distress_score(**self._profile_healthy_growing())
        diffs = [
            abs(score_a.layer1_score - score_b.layer1_score),
            abs(score_a.layer2_score - score_b.layer2_score),
            abs(score_a.layer3_score - score_b.layer3_score),
        ]
        assert sum(1 for d in diffs if d > 5.0) >= 2


# ── Davis Double scoring tests ──────────────────────────────────────────────


class TestCalculateDavisDoubleScore:
    def test_known_calculation(self):
        result = calculate_davis_double_score(
            valuation_score=80.0,
            prosperity_score=70.0,
            distress_score=60.0,
            trend_score=70.0,
            ts_code="000001.SZ",
            name="TestStock",
        )
        w = DAVIS_DOUBLE_WEIGHTS
        expected = (
            80.0 * w["valuation"]
            + 70.0 * w["trend"]
            + 70.0 * w["prosperity"]
            + 60.0 * w["distress"]
        )
        assert result.final_score == pytest.approx(expected, abs=0.01)
        assert result.final_score == pytest.approx(70.5, abs=0.01)
        assert result.valuation_score == 80.0
        assert result.prosperity_score == 70.0
        assert result.distress_score == 60.0
        assert result.trend_score == 70.0
        assert result.rank == 0
        assert result.ts_code == "000001.SZ"
        assert result.name == "TestStock"

    def test_uses_weights_from_constants(self):
        w = DAVIS_DOUBLE_WEIGHTS
        result = calculate_davis_double_score(100.0, 100.0, 100.0, trend_score=100.0)
        assert result.final_score == pytest.approx(
            100.0 * w["valuation"]
            + 100.0 * w["trend"]
            + 100.0 * w["prosperity"]
            + 100.0 * w["distress"],
            abs=0.01,
        )

    def test_zero_scores(self):
        result = calculate_davis_double_score(0.0, 0.0, 0.0, trend_score=0.0)
        assert result.final_score == 0.0

    def test_max_scores(self):
        result = calculate_davis_double_score(100.0, 100.0, 100.0, trend_score=100.0)
        assert result.final_score == pytest.approx(100.0, abs=0.01)

    def test_trend_score_default_zero(self):
        result_without = calculate_davis_double_score(80.0, 70.0, 60.0)
        result_with = calculate_davis_double_score(80.0, 70.0, 60.0, trend_score=0.0)
        assert result_without.final_score == result_with.final_score
        assert result_without.trend_score == 0.0


class TestRankStocks:
    def _make_score(self, ts_code, name, final):
        return DavisDoubleScore(
            ts_code=ts_code,
            name=name,
            valuation_score=50.0,
            prosperity_score=50.0,
            distress_score=50.0,
            final_score=final,
            rank=0,
        )

    def test_sorting_and_ranking(self):
        stocks = [
            self._make_score("A", "Low", 40.0),
            self._make_score("B", "High", 90.0),
            self._make_score("C", "Mid", 65.0),
        ]
        result = rank_stocks(stocks)
        assert len(result) == 3
        assert result[0].ts_code == "B"
        assert result[0].rank == 1
        assert result[1].ts_code == "C"
        assert result[1].rank == 2
        assert result[2].ts_code == "A"
        assert result[2].rank == 3

    def test_top_n_filtering(self):
        stocks = [
            self._make_score("A", "S1", 90.0),
            self._make_score("B", "S2", 80.0),
            self._make_score("C", "S3", 70.0),
            self._make_score("D", "S4", 60.0),
            self._make_score("E", "S5", 50.0),
        ]
        result = rank_stocks(stocks, top_n=3)
        assert len(result) == 3
        assert result[0].rank == 1
        assert result[2].rank == 3

    def test_empty_list(self):
        result = rank_stocks([])
        assert result == []

    def test_default_top_n(self):
        stocks = [self._make_score(f"S{i}", f"N{i}", float(100 - i)) for i in range(50)]
        result = rank_stocks(stocks)
        assert len(result) == 30
