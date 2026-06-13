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
        assert check_eps_decline([0.5, 1.0]) is True

    def test_decline_just_under_30_percent(self):
        assert check_eps_decline([0.71, 1.0]) is False

    def test_no_decline(self):
        assert check_eps_decline([1.5, 1.0]) is False

    def test_insufficient_history(self):
        assert check_eps_decline([0.5]) is False
        assert check_eps_decline([]) is False

    def test_previous_near_zero(self):
        assert check_eps_decline([0.5, 0.0]) is False


class TestCheckPePbPercentile:
    def test_pe_below_10_percent(self):
        assert check_pe_pb_percentile(0.05, 0.50) is True

    def test_pb_below_10_percent(self):
        assert check_pe_pb_percentile(0.50, 0.05) is True

    def test_both_above_10_percent(self):
        assert check_pe_pb_percentile(0.50, 0.50) is False

    def test_both_below_10_percent(self):
        assert check_pe_pb_percentile(0.05, 0.05) is True


class TestCheckFinancialHealth:
    def test_healthy(self):
        assert check_financial_health(0.3, 100.0) is True

    def test_high_debt(self):
        assert check_financial_health(0.6, 100.0) is False

    def test_negative_cf(self):
        assert check_financial_health(0.3, -50.0) is False

    def test_both_bad(self):
        assert check_financial_health(0.6, -50.0) is False


# ── Layer 2 signal tests ────────────────────────────────────────────────────


class TestCheckBalanceSheet:
    def test_low_debt_ratio(self):
        assert check_balance_sheet(300.0, 1000.0) is True

    def test_high_debt_ratio(self):
        assert check_balance_sheet(600.0, 1000.0) is False

    def test_exactly_half(self):
        assert check_balance_sheet(500.0, 1000.0) is False

    def test_zero_assets(self):
        assert check_balance_sheet(100.0, 0.0) is False


class TestCheckOperatingCf:
    def test_positive(self):
        assert check_operating_cf(100.0) is True

    def test_zero(self):
        assert check_operating_cf(0.0) is False

    def test_negative(self):
        assert check_operating_cf(-50.0) is False


class TestCheckRoeTrend:
    def test_improving(self):
        assert check_roe_trend([12.0, 10.0]) is True

    def test_stable(self):
        assert check_roe_trend([10.0, 10.0]) is True

    def test_declining(self):
        assert check_roe_trend([8.0, 10.0]) is False

    def test_insufficient_data(self):
        assert check_roe_trend([5.0]) is False
        assert check_roe_trend([]) is False


# ── Layer 3 signal tests ────────────────────────────────────────────────────


class TestCheckRevenueInflection:
    def test_inflection_positive(self):
        assert check_revenue_inflection([5.0, -10.0]) is True

    def test_inflection_to_zero(self):
        assert check_revenue_inflection([0.0, -10.0]) is True

    def test_still_declining(self):
        assert check_revenue_inflection([-3.0, -10.0]) is False

    def test_already_growing(self):
        assert check_revenue_inflection([10.0, 5.0]) is False

    def test_insufficient_data(self):
        assert check_revenue_inflection([5.0]) is False


class TestCheckProfitInflection:
    def test_inflection_positive(self):
        assert check_profit_inflection([8.0, -5.0]) is True

    def test_no_inflection(self):
        assert check_profit_inflection([10.0, 5.0]) is False

    def test_insufficient_data(self):
        assert check_profit_inflection([]) is False


class TestCheckDeltaGPositive:
    def test_positive(self):
        assert check_delta_g_positive(5.0) is True

    def test_zero(self):
        assert check_delta_g_positive(0.0) is False

    def test_negative(self):
        assert check_delta_g_positive(-3.0) is False


# ── Distress score aggregation tests ────────────────────────────────────────


class TestCalculateDistressScore:
    def _make_all_true_kwargs(self):
        return dict(
            eps_history=[0.5, 1.0],
            pe_pct=0.05,
            pb_pct=0.50,
            debt_ratio=0.3,
            operating_cf=100.0,
            total_debt=300.0,
            total_assets=1000.0,
            roe_history=[12.0, 10.0],
            revenue_history=[5.0, -10.0],
            profit_history=[8.0, -5.0],
            delta_g=5.0,
            ts_code="000001.SZ",
        )

    def _make_all_false_kwargs(self):
        return dict(
            eps_history=[1.5, 1.0],
            pe_pct=0.50,
            pb_pct=0.50,
            debt_ratio=0.6,
            operating_cf=-50.0,
            total_debt=600.0,
            total_assets=1000.0,
            roe_history=[8.0, 10.0],
            revenue_history=[10.0, 5.0],
            profit_history=[10.0, 5.0],
            delta_g=-3.0,
            ts_code="000002.SZ",
        )

    def test_all_true_high_score(self):
        result = calculate_distress_score(**self._make_all_true_kwargs())
        assert result.layer1_score == 100.0
        assert result.layer2_score == 100.0
        assert result.layer3_score == 100.0
        assert result.total_score == pytest.approx(100.0, abs=0.01)
        assert result.ts_code == "000001.SZ"

    def test_all_false_low_score(self):
        result = calculate_distress_score(**self._make_all_false_kwargs())
        assert result.layer1_score == 0.0
        assert result.layer2_score == 0.0
        assert result.layer3_score == 0.0
        assert result.total_score == 0.0

    def test_mixed_signals_partial_score(self):
        result = calculate_distress_score(
            eps_history=[0.5, 1.0],
            pe_pct=0.50,
            pb_pct=0.50,
            debt_ratio=0.6,
            operating_cf=100.0,
            total_debt=300.0,
            total_assets=1000.0,
            roe_history=[8.0, 10.0],
            revenue_history=[5.0, -10.0],
            profit_history=[8.0, -5.0],
            delta_g=-3.0,
            ts_code="000003.SZ",
        )
        assert result.layer1_score == pytest.approx(33.33, abs=0.01)
        assert result.layer2_score == pytest.approx(66.67, abs=0.01)
        assert result.layer3_score == pytest.approx(66.67, abs=0.01)
        expected_total = 33.33 * 0.3 + 66.67 * 0.3 + 66.67 * 0.4
        assert result.total_score == pytest.approx(expected_total, abs=0.5)

    def test_signals_detail_structure(self):
        result = calculate_distress_score(**self._make_all_true_kwargs())
        detail = result.signals_detail
        assert "layer1" in detail
        assert "layer2" in detail
        assert "layer3" in detail
        assert len(detail["layer1"]) == 3
        assert len(detail["layer2"]) == 3
        assert len(detail["layer3"]) == 3


# ── Davis Double scoring tests ──────────────────────────────────────────────


class TestCalculateDavisDoubleScore:
    def test_known_calculation(self):
        result = calculate_davis_double_score(
            valuation_score=80.0,
            prosperity_score=70.0,
            distress_score=60.0,
            ts_code="000001.SZ",
            name="TestStock",
        )
        expected = 80.0 * 0.35 + 70.0 * 0.35 + 60.0 * 0.30
        assert result.final_score == pytest.approx(expected, abs=0.01)
        assert result.final_score == pytest.approx(70.5, abs=0.01)
        assert result.valuation_score == 80.0
        assert result.prosperity_score == 70.0
        assert result.distress_score == 60.0
        assert result.rank == 0
        assert result.ts_code == "000001.SZ"
        assert result.name == "TestStock"

    def test_uses_weights_from_constants(self):
        w = DAVIS_DOUBLE_WEIGHTS
        result = calculate_davis_double_score(100.0, 100.0, 100.0)
        assert result.final_score == pytest.approx(
            100.0 * w["valuation"] + 100.0 * w["prosperity"] + 100.0 * w["distress"],
            abs=0.01,
        )

    def test_zero_scores(self):
        result = calculate_davis_double_score(0.0, 0.0, 0.0)
        assert result.final_score == 0.0

    def test_max_scores(self):
        result = calculate_davis_double_score(100.0, 100.0, 100.0)
        assert result.final_score == 100.0


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
