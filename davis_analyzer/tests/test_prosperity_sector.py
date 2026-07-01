"""Tests for davis_analyzer.prosperity_sector — sector-level 景气度 engine."""

import pytest

from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import (
    aggregate_industry_prosperity,
    build_stock_details,
    classify_industry_stage,
    classify_stock_stage,
    compute_relative_delta_g,
    generate_risk_warnings,
    screen_g_delta_g_ignition,
)
from davis_analyzer.types import (
    FinancialData,
    IndustryProsperityScore,
    ProsperityScore,
    ProsperityStockDetail,
    StockInfo,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _ps(
    ts_code: str = "000001.SZ",
    revenue_score: float = 60.0,
    profit_score: float = 60.0,
    slope_score: float = 60.0,
    duration_score: float = 60.0,
    composite_score: float = 60.0,
    delta_g: float = 5.0,
    relative_delta_g: float | None = None,
) -> ProsperityScore:
    return ProsperityScore(
        ts_code=ts_code,
        revenue_score=revenue_score,
        profit_score=profit_score,
        slope_score=slope_score,
        duration_score=duration_score,
        composite_score=composite_score,
        delta_g=delta_g,
        relative_delta_g=delta_g if relative_delta_g is None else relative_delta_g,
    )


def _si(
    ts_code: str = "000001.SZ",
    name: str = "TestStock",
    industry: str = "电子",
    list_status: str = "L",
    is_cyclical: bool = False,
) -> StockInfo:
    return StockInfo(
        ts_code=ts_code,
        name=name,
        industry=industry,
        list_status=list_status,
        is_cyclical=is_cyclical,
    )


def _fd(
    ts_code: str = "000001.SZ",
    period: str = "2024Q1",
    revenue: float = 100.0,
    net_profit: float = 10.0,
    **kw,
) -> FinancialData:
    defaults = dict(
        ts_code=ts_code,
        report_period=period,
        revenue=revenue,
        net_profit=net_profit,
        eps=0.5,
        roe=12.0,
        operating_cf=15.0,
        total_debt=50.0,
        total_assets=200.0,
    )
    defaults.update(kw)
    return FinancialData(**defaults)


def _make_industry_stocks(
    industry: str,
    n: int,
    prefix: str = "0000",
    base_composite: float = 60.0,
    base_delta_g: float = 5.0,
) -> tuple[dict[str, ProsperityScore], dict[str, StockInfo]]:
    """Build n stocks in the same industry with varying scores."""
    scores: dict[str, ProsperityScore] = {}
    infos: dict[str, StockInfo] = {}
    for i in range(n):
        code = f"{prefix}{i:02d}.SZ"
        comp = base_composite + i * 2.0
        scores[code] = _ps(
            ts_code=code,
            composite_score=comp,
            delta_g=base_delta_g + i,
        )
        infos[code] = _si(ts_code=code, name=f"S{i}", industry=industry)
    return scores, infos


# ── A. aggregate_industry_prosperity ─────────────────────────────────


class TestAggregateIndustryProsperity:
    def test_empty_inputs(self):
        result = aggregate_industry_prosperity({}, {})
        assert result == []

    def test_empty_stock_infos(self):
        scores, _ = _make_industry_stocks("电子", 6)
        result = aggregate_industry_prosperity(scores, {})
        assert result == []

    def test_skips_industry_below_min_stocks(self):
        scores, infos = _make_industry_stocks("电子", 3)
        result = aggregate_industry_prosperity(scores, infos, min_stocks=5)
        assert result == []

    def test_single_industry_above_min(self):
        scores, infos = _make_industry_stocks("电子", 6)
        result = aggregate_industry_prosperity(scores, infos, min_stocks=5)
        assert len(result) == 1
        assert result[0].industry == "电子"
        assert result[0].stock_count == 6

    def test_computes_mean_composite(self):
        scores, infos = _make_industry_stocks("电子", 5, base_composite=60.0)
        result = aggregate_industry_prosperity(scores, infos)
        expected_avg = sum(s.composite_score for s in scores.values()) / 5
        assert result[0].avg_composite_score == pytest.approx(expected_avg, abs=0.01)

    def test_computes_median_delta_g(self):
        scores, infos = _make_industry_stocks("电子", 5, base_delta_g=0.0)
        # delta_g values: 0,1,2,3,4 → median = 2
        result = aggregate_industry_prosperity(scores, infos)
        assert result[0].median_delta_g == pytest.approx(2.0, abs=0.01)

    def test_computes_sub_scores_means(self):
        scores, infos = _make_industry_stocks("电子", 6, base_composite=60.0)
        result = aggregate_industry_prosperity(scores, infos)
        expected_rev = sum(s.revenue_score for s in scores.values()) / 6
        assert result[0].avg_revenue_score == pytest.approx(expected_rev, abs=0.01)

    def test_sorted_by_composite_desc(self):
        scores_a, infos_a = _make_industry_stocks("电子", 5, prefix="0001", base_composite=80.0)
        scores_b, infos_b = _make_industry_stocks("医药", 5, prefix="0002", base_composite=50.0)
        all_scores = {**scores_a, **scores_b}
        all_infos = {**infos_a, **infos_b}
        result = aggregate_industry_prosperity(all_scores, all_infos)
        assert len(result) == 2
        assert result[0].industry == "电子"
        assert result[0].avg_composite_score > result[1].avg_composite_score

    def test_top_stock_codes(self):
        scores, infos = _make_industry_stocks("电子", 6, base_composite=60.0)
        result = aggregate_industry_prosperity(scores, infos)
        # highest composite first
        top = result[0].top_stock_codes
        assert len(top) <= 10
        assert top[0] == "000005.SZ"  # highest composite (60 + 5*2 = 70)

    def test_top_stock_codes_max_10(self):
        scores, infos = _make_industry_stocks("电子", 15, base_composite=60.0)
        result = aggregate_industry_prosperity(scores, infos)
        assert len(result[0].top_stock_codes) == 10

    def test_stock_missing_from_infos_skipped(self):
        scores, infos = _make_industry_stocks("电子", 6)
        # Add a score with no matching StockInfo
        scores["99999.SZ"] = _ps(ts_code="99999.SZ", composite_score=99.0)
        result = aggregate_industry_prosperity(scores, infos)
        assert result[0].stock_count == 6
        assert "99999.SZ" not in result[0].top_stock_codes

    def test_custom_min_stocks(self):
        scores, infos = _make_industry_stocks("电子", 3)
        result = aggregate_industry_prosperity(scores, infos, min_stocks=3)
        assert len(result) == 1
        assert result[0].stock_count == 3

    def test_all_stocks_one_industry(self):
        scores, infos = _make_industry_stocks("电子", 10)
        result = aggregate_industry_prosperity(scores, infos)
        assert len(result) == 1

    def test_returns_industry_prosperity_score_type(self):
        scores, infos = _make_industry_stocks("电子", 5)
        result = aggregate_industry_prosperity(scores, infos)
        assert isinstance(result[0], IndustryProsperityScore)

    def test_ignition_count_default_zero(self):
        scores, infos = _make_industry_stocks("电子", 5)
        result = aggregate_industry_prosperity(scores, infos)
        assert result[0].ignition_count == 0

    def test_c1_market_cap_weighted_average(self):
        """C1: with market_cap_map, uses weighted average instead of simple."""
        scores = {
            "S1.SZ": _ps(ts_code="S1.SZ", revenue_score=80.0, composite_score=80.0),
            "S2.SZ": _ps(ts_code="S2.SZ", revenue_score=60.0, composite_score=60.0),
        }
        infos = {
            "S1.SZ": _si(ts_code="S1.SZ", industry="化工"),
            "S2.SZ": _si(ts_code="S2.SZ", industry="化工"),
        }
        caps = {"S1.SZ": 1000.0, "S2.SZ": 100.0}
        result = aggregate_industry_prosperity(scores, infos, min_stocks=2, market_cap_map=caps)
        # Weighted: (80*1000 + 60*100) / 1100 = 78.18
        assert result[0].avg_revenue_score == pytest.approx(78.18, abs=0.1)

    def test_c1_fallback_simple_avg_when_no_market_cap(self):
        """C1: market_cap_map=None → simple average."""
        scores = {
            "S1.SZ": _ps(ts_code="S1.SZ", revenue_score=80.0),
            "S2.SZ": _ps(ts_code="S2.SZ", revenue_score=60.0),
        }
        infos = {
            "S1.SZ": _si(ts_code="S1.SZ", industry="化工"),
            "S2.SZ": _si(ts_code="S2.SZ", industry="化工"),
        }
        result = aggregate_industry_prosperity(scores, infos, min_stocks=2)
        assert result[0].avg_revenue_score == pytest.approx(70.0, abs=0.01)

    def test_c1_fallback_simple_avg_when_all_zero_caps(self):
        """C1: all market caps zero → simple average."""
        scores = {
            "S1.SZ": _ps(ts_code="S1.SZ", revenue_score=80.0),
            "S2.SZ": _ps(ts_code="S2.SZ", revenue_score=60.0),
        }
        infos = {
            "S1.SZ": _si(ts_code="S1.SZ", industry="化工"),
            "S2.SZ": _si(ts_code="S2.SZ", industry="化工"),
        }
        caps = {"S1.SZ": 0.0, "S2.SZ": 0.0}
        result = aggregate_industry_prosperity(scores, infos, min_stocks=2, market_cap_map=caps)
        assert result[0].avg_revenue_score == pytest.approx(70.0, abs=0.01)


# ── B. classify_stock_stage ──────────────────────────────────────────


class TestClassifyStockStage:
    def test_acceleration_high_growth_positive_delta(self):
        score = _ps(revenue_score=90.0, delta_g=5.0)
        assert classify_stock_stage(score) == "加速期"

    def test_deceleration_high_growth_negative_delta(self):
        score = _ps(revenue_score=90.0, delta_g=-5.0)
        assert classify_stock_stage(score) == "减速期"

    def test_rising_inflection_low_growth_positive_delta(self):
        score = _ps(revenue_score=50.0, delta_g=5.0)
        assert classify_stock_stage(score) == "上升拐点"

    def test_boundary_revenue_score_exactly_80(self):
        """revenue_score == 80 is transition zone; with rdg not > 5 → not high growth → 上升拐点."""
        score = _ps(revenue_score=80.0, delta_g=5.0)
        assert classify_stock_stage(score) == "上升拐点"

    def test_boundary_delta_g_exactly_zero_high_growth(self):
        """delta_g == 0 with confirmed high growth → 减速期."""
        score = _ps(revenue_score=90.0, delta_g=0.0)
        assert classify_stock_stage(score) == "减速期"

    def test_boundary_delta_g_exactly_zero_low_growth(self):
        score = _ps(revenue_score=50.0, delta_g=0.0)
        assert classify_stock_stage(score) == "下降拐点"

    def test_negative_delta_g_low_growth(self):
        score = _ps(revenue_score=50.0, delta_g=-10.0)
        assert classify_stock_stage(score) == "下降拐点"

    def test_custom_deceleration_threshold(self):
        score = _ps(revenue_score=90.0, delta_g=-5.0)
        result = classify_stock_stage(score)
        assert result == "减速期"

    def test_b1_or_logic_profit_driven_acceleration(self):
        """B1: revenue_score < 80 but profit_score > 85 → high growth."""
        score = _ps(revenue_score=70.0, profit_score=90.0, delta_g=5.0)
        assert classify_stock_stage(score) == "加速期"

    def test_b2_transition_zone_positive_rdg(self):
        """B2: max_score=80 (transition zone), relative_delta_g=6 (>5) → high growth."""
        score = _ps(revenue_score=80.0, delta_g=5.0, relative_delta_g=6.0)
        assert classify_stock_stage(score) == "加速期"

    def test_b2_transition_zone_negative_rdg(self):
        """B2: max_score=80 (transition zone), relative_delta_g=-6 (<-5) → not high growth."""
        score = _ps(revenue_score=80.0, delta_g=5.0, relative_delta_g=-6.0)
        assert classify_stock_stage(score) == "上升拐点"

    def test_b2_transition_zone_neutral_rdg(self):
        """B2: max_score=80 (transition zone), relative_delta_g=0 → conservative not high growth."""
        score = _ps(revenue_score=80.0, delta_g=5.0, relative_delta_g=0.0)
        assert classify_stock_stage(score) == "上升拐点"


# ── C. classify_industry_stage ───────────────────────────────────────


class TestClassifyIndustryStage:
    def test_acceleration(self):
        ind = IndustryProsperityScore(
            industry="电子",
            stock_count=5,
            avg_composite_score=70.0,
            median_delta_g=5.0,
            avg_revenue_score=90.0,
            avg_profit_score=60.0,
            avg_slope_score=60.0,
            avg_duration_score=60.0,
            stage="",
            ignition_count=0,
            top_stock_codes=[],
        )
        assert classify_industry_stage(ind) == "加速期"

    def test_deceleration(self):
        ind = IndustryProsperityScore(
            industry="电子",
            stock_count=5,
            avg_composite_score=70.0,
            median_delta_g=-3.0,
            avg_revenue_score=90.0,
            avg_profit_score=60.0,
            avg_slope_score=60.0,
            avg_duration_score=60.0,
            stage="",
            ignition_count=0,
            top_stock_codes=[],
        )
        assert classify_industry_stage(ind) == "减速期"

    def test_inflection(self):
        ind = IndustryProsperityScore(
            industry="电子",
            stock_count=5,
            avg_composite_score=50.0,
            median_delta_g=5.0,
            avg_revenue_score=50.0,
            avg_profit_score=60.0,
            avg_slope_score=60.0,
            avg_duration_score=60.0,
            stage="",
            ignition_count=0,
            top_stock_codes=[],
        )
        assert classify_industry_stage(ind) == "上升拐点"

    def test_boundary_avg_revenue_score_exactly_80(self):
        ind = IndustryProsperityScore(
            industry="电子",
            stock_count=5,
            avg_composite_score=70.0,
            median_delta_g=5.0,
            avg_revenue_score=80.0,
            avg_profit_score=60.0,
            avg_slope_score=60.0,
            avg_duration_score=60.0,
            stage="",
            ignition_count=0,
            top_stock_codes=[],
        )
        assert classify_industry_stage(ind) == "上升拐点"

    def test_boundary_median_delta_g_exactly_zero(self):
        ind = IndustryProsperityScore(
            industry="电子",
            stock_count=5,
            avg_composite_score=70.0,
            median_delta_g=0.0,
            avg_revenue_score=90.0,
            avg_profit_score=60.0,
            avg_slope_score=60.0,
            avg_duration_score=60.0,
            stage="",
            ignition_count=0,
            top_stock_codes=[],
        )
        assert classify_industry_stage(ind) == "减速期"

    def test_c2_or_logic_profit_driven(self):
        """C2: avg_revenue < 80 but avg_profit > 85 → high growth."""
        ind = IndustryProsperityScore(
            industry="电子",
            stock_count=5,
            avg_composite_score=70.0,
            median_delta_g=5.0,
            avg_revenue_score=70.0,
            avg_profit_score=90.0,
            avg_slope_score=60.0,
            avg_duration_score=60.0,
            stage="",
            ignition_count=0,
            top_stock_codes=[],
        )
        assert classify_industry_stage(ind) == "加速期"

    def test_c2_transition_zone_positive_delta(self):
        """C2: max_score=80 (transition), median_delta_g=6 (>5) → high growth."""
        ind = IndustryProsperityScore(
            industry="电子",
            stock_count=5,
            avg_composite_score=70.0,
            median_delta_g=6.0,
            avg_revenue_score=80.0,
            avg_profit_score=60.0,
            avg_slope_score=60.0,
            avg_duration_score=60.0,
            stage="",
            ignition_count=0,
            top_stock_codes=[],
        )
        assert classify_industry_stage(ind) == "加速期"

    def test_c2_transition_zone_negative_delta(self):
        """C2: max_score=80 (transition), median_delta_g=-6 (<-5) → not high growth."""
        ind = IndustryProsperityScore(
            industry="电子",
            stock_count=5,
            avg_composite_score=70.0,
            median_delta_g=-6.0,
            avg_revenue_score=80.0,
            avg_profit_score=60.0,
            avg_slope_score=60.0,
            avg_duration_score=60.0,
            stage="",
            ignition_count=0,
            top_stock_codes=[],
        )
        assert classify_industry_stage(ind) == "下降拐点"


# ── D. screen_g_delta_g_ignition ─────────────────────────────────────


class TestScreenGDeltaGIgnition:
    def test_empty_dict(self):
        assert screen_g_delta_g_ignition({}) == set()

    def test_all_qualify(self):
        scores = {
            "a.SZ": _ps(ts_code="a.SZ", revenue_score=90, delta_g=5),
            "b.SZ": _ps(ts_code="b.SZ", revenue_score=85, delta_g=10),
        }
        result = screen_g_delta_g_ignition(scores)
        assert result == {"a.SZ", "b.SZ"}

    def test_none_qualify(self):
        scores = {
            "a.SZ": _ps(ts_code="a.SZ", revenue_score=50, delta_g=5),
            "b.SZ": _ps(ts_code="b.SZ", revenue_score=90, delta_g=-5),
        }
        result = screen_g_delta_g_ignition(scores)
        assert result == set()

    def test_boundary_revenue_score_exactly_80(self):
        scores = {"a.SZ": _ps(ts_code="a.SZ", revenue_score=80, delta_g=5)}
        result = screen_g_delta_g_ignition(scores)
        assert result == set()

    def test_boundary_delta_g_exactly_zero(self):
        scores = {"a.SZ": _ps(ts_code="a.SZ", revenue_score=90, delta_g=0.0)}
        result = screen_g_delta_g_ignition(scores)
        assert result == set()

    def test_custom_delta_g_threshold(self):
        scores = {
            "a.SZ": _ps(ts_code="a.SZ", revenue_score=90, delta_g=3),
            "b.SZ": _ps(ts_code="b.SZ", revenue_score=90, delta_g=1),
        }
        result = screen_g_delta_g_ignition(scores, delta_g_threshold=2.0)
        assert result == {"a.SZ"}

    def test_mixed_qualifications(self):
        scores = {
            "qualify.SZ": _ps(ts_code="qualify.SZ", revenue_score=95, delta_g=10),
            "no_rev.SZ": _ps(ts_code="no_rev.SZ", revenue_score=50, delta_g=10),
            "no_delta.SZ": _ps(ts_code="no_delta.SZ", revenue_score=95, delta_g=-1),
            "boundary.SZ": _ps(ts_code="boundary.SZ", revenue_score=80, delta_g=5),
        }
        result = screen_g_delta_g_ignition(scores)
        assert result == {"qualify.SZ"}

    def test_returns_set_type(self):
        scores = {"a.SZ": _ps(ts_code="a.SZ", revenue_score=90, delta_g=5)}
        result = screen_g_delta_g_ignition(scores)
        assert isinstance(result, set)

    def test_d1_or_logic_profit_driven(self):
        """D1: revenue_score < 80 but profit_score > 80 → high growth condition met."""
        scores = {"p.SZ": _ps(ts_code="p.SZ", revenue_score=70, profit_score=90, delta_g=5)}
        result = screen_g_delta_g_ignition(scores)
        assert "p.SZ" in result

    def test_d1_negative_cf_excludes(self):
        """D1: all growth conditions met but operating_cf < 0 → excluded."""
        scores = {"S1.SZ": _ps(ts_code="S1.SZ", revenue_score=90, delta_g=5)}
        fin_data = {
            "S1.SZ": [_fd(ts_code="S1.SZ", period="2024Q4", operating_cf=-5.0)],
        }
        result = screen_g_delta_g_ignition(scores, financial_data=fin_data)
        assert "S1.SZ" not in result

    def test_d1_positive_cf_includes(self):
        """D1: all conditions met including operating_cf > 0 → included."""
        scores = {"S1.SZ": _ps(ts_code="S1.SZ", revenue_score=90, delta_g=5)}
        fin_data = {
            "S1.SZ": [_fd(ts_code="S1.SZ", period="2024Q4", operating_cf=100.0)],
        }
        result = screen_g_delta_g_ignition(scores, financial_data=fin_data)
        assert "S1.SZ" in result

    def test_d1_industry_median_adjustment(self):
        """D1: pre-computed relative_delta_g < 0 → excluded."""
        scores = {"S1.SZ": _ps(ts_code="S1.SZ", revenue_score=90, delta_g=3, relative_delta_g=-2.0)}
        infos = {"S1.SZ": _si(ts_code="S1.SZ", industry="化工")}
        medians = {"化工": 5.0}
        # rel_dg already pre-computed as -2 < 0 → excluded
        result = screen_g_delta_g_ignition(
            scores, stock_infos=infos, industry_median_delta_g=medians
        )
        assert "S1.SZ" not in result

    def test_d1_all_conditions_met(self):
        """D1: high growth + relative delta > 0 + CF > 0 → ignition."""
        scores = {"S1.SZ": _ps(ts_code="S1.SZ", revenue_score=90, delta_g=8)}
        infos = {"S1.SZ": _si(ts_code="S1.SZ", industry="化工")}
        fin_data = {
            "S1.SZ": [_fd(ts_code="S1.SZ", period="2024Q4", operating_cf=100.0)],
        }
        medians = {"化工": 2.0}
        result = screen_g_delta_g_ignition(
            scores,
            stock_infos=infos,
            financial_data=fin_data,
            industry_median_delta_g=medians,
        )
        assert "S1.SZ" in result


# ── E. generate_risk_warnings ────────────────────────────────────────


class TestGenerateRiskWarnings:
    def test_no_risks(self):
        score = _ps(delta_g=5.0, revenue_score=60.0, slope_score=60.0, duration_score=60.0)
        assert generate_risk_warnings(score, []) == []

    def test_growth_slowdown(self):
        score = _ps(delta_g=-1.0, revenue_score=60.0, slope_score=60.0, duration_score=60.0)
        result = generate_risk_warnings(score, [])
        assert "增速放缓" in result

    def test_growth_insufficient(self):
        score = _ps(delta_g=5.0, revenue_score=30.0, slope_score=60.0, duration_score=60.0)
        result = generate_risk_warnings(score, [])
        assert "增速不足" in result

    def test_trend_downward(self):
        score = _ps(delta_g=5.0, revenue_score=60.0, slope_score=30.0, duration_score=60.0)
        result = generate_risk_warnings(score, [])
        assert "趋势下行" in result

    def test_duration_questionable(self):
        score = _ps(delta_g=5.0, revenue_score=60.0, slope_score=60.0, duration_score=20.0)
        result = generate_risk_warnings(score, [])
        assert "景气持续性存疑" in result

    def test_all_risks(self):
        score = _ps(delta_g=-5.0, revenue_score=20.0, slope_score=10.0, duration_score=10.0)
        result = generate_risk_warnings(score, [])
        assert "增速放缓" in result
        assert "增速不足" in result
        assert "趋势下行" in result
        assert "景气持续性存疑" in result
        assert len(result) == 4

    def test_boundary_delta_g_zero_no_warning(self):
        score = _ps(delta_g=0.0, revenue_score=60.0, slope_score=60.0, duration_score=60.0)
        result = generate_risk_warnings(score, [])
        assert "增速放缓" not in result

    def test_boundary_revenue_35_no_warning(self):
        score = _ps(delta_g=5.0, revenue_score=35.0, slope_score=60.0, duration_score=60.0)
        result = generate_risk_warnings(score, [])
        assert "增速不足" not in result

    def test_boundary_slope_40_no_warning(self):
        score = _ps(delta_g=5.0, revenue_score=60.0, slope_score=40.0, duration_score=60.0)
        result = generate_risk_warnings(score, [])
        assert "趋势下行" not in result

    def test_boundary_duration_25_no_warning(self):
        score = _ps(delta_g=5.0, revenue_score=60.0, slope_score=60.0, duration_score=25.0)
        result = generate_risk_warnings(score, [])
        assert "景气持续性存疑" not in result

    def test_accepts_financial_data(self):
        score = _ps(delta_g=5.0, revenue_score=60.0, slope_score=60.0, duration_score=60.0)
        fd = [_fd()]
        result = generate_risk_warnings(score, fd)
        assert isinstance(result, list)

    def test_empty_financial_data(self):
        score = _ps(delta_g=5.0, revenue_score=60.0, slope_score=60.0, duration_score=60.0)
        result = generate_risk_warnings(score, [])
        assert result == []


# ── F. build_stock_details ───────────────────────────────────────────


class TestBuildStockDetails:
    def test_empty_inputs(self):
        result = build_stock_details({}, {}, {}, set())
        assert result == {}

    def test_basic_structure(self):
        scores, infos = _make_industry_stocks("电子", 6)
        fd = {code: [_fd(ts_code=code)] for code in scores}
        result = build_stock_details(scores, infos, fd, set())
        assert len(result) == 6
        for code in scores:
            assert code in result
            assert isinstance(result[code], ProsperityStockDetail)

    def test_rank_in_industry(self):
        scores, infos = _make_industry_stocks("电子", 6, base_composite=60.0)
        fd = {code: [_fd(ts_code=code)] for code in scores}
        result = build_stock_details(scores, infos, fd, set())
        # Stock with highest composite should have rank 1
        # composites: 60,62,64,66,68,70 → 000005.SZ has 70
        assert result["000005.SZ"].rank_in_industry == 1
        assert result["000004.SZ"].rank_in_industry == 2

    def test_rank_across_industries(self):
        scores_a, infos_a = _make_industry_stocks("电子", 5, prefix="0001", base_composite=80.0)
        scores_b, infos_b = _make_industry_stocks("医药", 5, prefix="0002", base_composite=50.0)
        all_scores = {**scores_a, **scores_b}
        all_infos = {**infos_a, **infos_b}
        fd = {code: [_fd(ts_code=code)] for code in all_scores}
        result = build_stock_details(all_scores, all_infos, fd, set())
        # Ranks are within-industry
        for code in scores_a:
            detail = result[code]
            assert 1 <= detail.rank_in_industry <= 5
        for code in scores_b:
            detail = result[code]
            assert 1 <= detail.rank_in_industry <= 5

    def test_is_ignition_flag(self):
        scores, infos = _make_industry_stocks("电子", 5)
        fd = {code: [_fd(ts_code=code)] for code in scores}
        ignition_set = {"000001.SZ", "000003.SZ"}
        result = build_stock_details(scores, infos, fd, ignition_set)
        assert result["000001.SZ"].is_ignition is True
        assert result["000003.SZ"].is_ignition is True
        assert result["000000.SZ"].is_ignition is False

    def test_stage_classification(self):
        scores = {
            "accel.SZ": _ps(ts_code="accel.SZ", revenue_score=90, delta_g=5),
            "decel.SZ": _ps(ts_code="decel.SZ", revenue_score=90, delta_g=-5),
            "inflect.SZ": _ps(ts_code="inflect.SZ", revenue_score=50, delta_g=5),
        }
        infos = {
            "accel.SZ": _si(ts_code="accel.SZ", industry="电子"),
            "decel.SZ": _si(ts_code="decel.SZ", industry="电子"),
            "inflect.SZ": _si(ts_code="inflect.SZ", industry="电子"),
        }
        fd = {code: [_fd(ts_code=code)] for code in scores}
        result = build_stock_details(scores, infos, fd, set())
        assert result["accel.SZ"].stage == "加速期"
        assert result["decel.SZ"].stage == "减速期"
        assert result["inflect.SZ"].stage == "上升拐点"

    def test_risk_warnings_populated(self):
        scores = {
            "risky.SZ": _ps(
                ts_code="risky.SZ", delta_g=-5, revenue_score=20, slope_score=10, duration_score=10
            ),
        }
        infos = {"risky.SZ": _si(ts_code="risky.SZ", industry="电子")}
        fd = {"risky.SZ": [_fd(ts_code="risky.SZ")]}
        result = build_stock_details(scores, infos, fd, set())
        assert len(result["risky.SZ"].risk_warnings) == 4

    def test_stock_missing_from_infos_skipped(self):
        scores = {
            "has.SZ": _ps(ts_code="has.SZ"),
            "missing.SZ": _ps(ts_code="missing.SZ"),
        }
        infos = {"has.SZ": _si(ts_code="has.SZ", industry="电子")}
        fd = {code: [_fd(ts_code=code)] for code in scores}
        result = build_stock_details(scores, infos, fd, set())
        assert "has.SZ" in result
        assert "missing.SZ" not in result

    def test_stock_missing_from_financial_data(self):
        scores = {
            "nofd.SZ": _ps(ts_code="nofd.SZ", revenue_score=60, delta_g=5),
        }
        infos = {"nofd.SZ": _si(ts_code="nofd.SZ", industry="电子")}
        # No financial_data entry
        result = build_stock_details(scores, infos, {}, set())
        assert "nofd.SZ" in result
        assert isinstance(result["nofd.SZ"].risk_warnings, list)

    def test_name_from_stock_info(self):
        scores, infos = _make_industry_stocks("电子", 5)
        fd = {code: [_fd(ts_code=code)] for code in scores}
        result = build_stock_details(scores, infos, fd, set())
        assert result["000000.SZ"].name == "S0"

    def test_industry_from_stock_info(self):
        scores, infos = _make_industry_stocks("医药", 5)
        fd = {code: [_fd(ts_code=code)] for code in scores}
        result = build_stock_details(scores, infos, fd, set())
        assert result["000000.SZ"].industry == "医药"

    def test_prosperity_score_attached(self):
        scores, infos = _make_industry_stocks("电子", 5)
        fd = {code: [_fd(ts_code=code)] for code in scores}
        result = build_stock_details(scores, infos, fd, set())
        assert result["000000.SZ"].prosperity_score is scores["000000.SZ"]

    def test_custom_min_stocks_for_ranking(self):
        scores, infos = _make_industry_stocks("电子", 3)
        fd = {code: [_fd(ts_code=code)] for code in scores}
        result = build_stock_details(scores, infos, fd, set(), min_stocks=3)
        assert len(result) == 3

    def test_large_input(self):
        scores, infos = _make_industry_stocks("电子", 50, base_composite=50.0)
        fd = {code: [_fd(ts_code=code)] for code in scores}
        result = build_stock_details(scores, infos, fd, set())
        assert len(result) == 50
        ranks = [d.rank_in_industry for d in result.values()]
        assert sorted(ranks) == list(range(1, 51))

    def test_b3_relative_delta_g_computed(self):
        """B3: with >=5 stocks in industry, relative_delta_g = delta_g - industry_median."""
        scores, infos = _make_industry_stocks("电子", 6, base_delta_g=3.0)
        fd = {code: [_fd(ts_code=code)] for code in scores}
        # delta_g values: 3,4,5,6,7,8 → median = 5.5
        result = build_stock_details(scores, infos, fd, set())
        for code in scores:
            expected_rdg = round(scores[code].delta_g - 5.5, 2)
            assert result[code].prosperity_score.relative_delta_g == expected_rdg

    def test_b3_relative_delta_g_fallback_below_min_stocks(self):
        """B3: with <5 stocks, industry median = 0 → relative_delta_g = delta_g."""
        scores, infos = _make_industry_stocks("电子", 3, base_delta_g=3.0)
        fd = {code: [_fd(ts_code=code)] for code in scores}
        result = build_stock_details(scores, infos, fd, set())
        for code in scores:
            assert result[code].prosperity_score.relative_delta_g == scores[code].delta_g

    def test_b4_dupont_driver_computed(self):
        """B5: dupont_driver is set on ProsperityStockDetail when fd present."""
        scores, infos = _make_industry_stocks("电子", 6)
        fd = {code: [_fd(ts_code=code)] for code in scores}
        result = build_stock_details(scores, infos, fd, set())
        for code in scores:
            assert result[code].dupont_driver is not None
            assert isinstance(result[code].dupont_driver, str)

    def test_b4_dupont_driver_none_without_fd(self):
        """B5: dupont_driver stays None when no financial_data."""
        scores, infos = _make_industry_stocks("电子", 6)
        result = build_stock_details(scores, infos, {}, set())
        for code in scores:
            assert result[code].dupont_driver is None

    def test_b4_dupont_negative_roe(self):
        """B5: negative roe → 'ROE为负（需警惕）'."""
        scores = {"x.SZ": _ps(ts_code="x.SZ", revenue_score=60, delta_g=5)}
        infos = {"x.SZ": _si(ts_code="x.SZ", industry="电子")}
        fd = {"x.SZ": [_fd(ts_code="x.SZ", roe=-5.0)]}
        result = build_stock_details(scores, infos, fd, set())
        assert result["x.SZ"].dupont_driver == "ROE为负（需警惕）"

    def test_b4_dupont_uses_equity_multiplier_not_debt_ratio(self):
        """Regression: leverage_ratio must be the equity multiplier
        (assets/equity), NOT debt/assets. With debt/assets=0.1 but
        assets/equity=1.11, the two formulas diverge:
          - OLD buggy (debt/assets=0.1): max(|0.25|,|1.0|,|0.1|) = turnover → 周转率
          - NEW (assets/equity=1.11): max(|0.25|,|1.0|,|1.11|) = leverage → 杠杆
        Asserting the NEW result matches a direct dupont_decomposition call
        with the equity multiplier, locking the contract."""
        fd = {
            "x.SZ": [
                _fd(
                    ts_code="x.SZ",
                    roe=15.0,
                    revenue=200.0,
                    net_profit=50.0,
                    total_debt=20.0,
                    total_assets=200.0,
                )
            ]
        }
        scores = {"x.SZ": _ps(ts_code="x.SZ")}
        infos = {"x.SZ": _si(ts_code="x.SZ")}
        result = build_stock_details(scores, infos, fd, set())
        driver = result["x.SZ"].dupont_driver
        from davis_analyzer.prosperity import dupont_decomposition

        expected = dupont_decomposition(
            roe=15.0,
            net_margin=0.25,
            asset_turnover=1.0,
            leverage_ratio=200.0 / 180.0,  # equity multiplier
        )
        assert driver == expected
        # The OLD buggy debt/assets formula would have yielded 周转率驱动 here;
        # the corrected equity-multiplier formula yields 杠杆驱动.
        assert driver == "杠杆驱动（需警惕可持续性）"


# ── G. compute_relative_delta_g ──────────────────────────────────────


class TestComputeRelativeDeltaG:
    def test_basic_median_subtraction(self):
        scores, infos = _make_industry_stocks("电子", 6, base_delta_g=3.0)
        result = compute_relative_delta_g(scores, infos)
        # delta_g values: 3,4,5,6,7,8 → median = 5.5
        for code in scores:
            expected = round(scores[code].delta_g - 5.5, 2)
            assert result[code] == expected
            assert scores[code].relative_delta_g == expected

    def test_fallback_below_min_stocks(self):
        scores, infos = _make_industry_stocks("电子", 3, base_delta_g=3.0)
        result = compute_relative_delta_g(scores, infos)
        for code in scores:
            assert result[code] == scores[code].delta_g

    def test_empty_inputs(self):
        assert compute_relative_delta_g({}, {}) == {}

    def test_stock_missing_from_infos_skipped(self):
        scores = {"a.SZ": _ps(ts_code="a.SZ", delta_g=5.0)}
        result = compute_relative_delta_g(scores, {})
        assert "a.SZ" not in result


# ── H. generate_risk_warnings CF check ──────────────────────────────


class TestGenerateRiskWarningsCF:
    def test_negative_operating_cf_warning(self):
        score = _ps(delta_g=5.0, revenue_score=60.0, slope_score=60.0, duration_score=60.0)
        fd = [_fd(operating_cf=-10.0)]
        result = generate_risk_warnings(score, fd)
        assert "经营性现金流为负" in result

    def test_positive_operating_cf_no_warning(self):
        score = _ps(delta_g=5.0, revenue_score=60.0, slope_score=60.0, duration_score=60.0)
        fd = [_fd(operating_cf=100.0)]
        result = generate_risk_warnings(score, fd)
        assert "经营性现金流为负" not in result


# ── E2. relative_delta_g lifecycle ───────────────────────────────────


class TestE2RelativeDeltaGLifecycle:
    """E2: relative_delta_g full lifecycle — calculate → compute → warn."""

    def test_default_relative_delta_g_is_zero(self):
        data = [
            _fd(ts_code="A.SZ", period="2023Q1", yoy_revenue_growth=0.1, yoy_profit_growth=0.1),
            _fd(ts_code="A.SZ", period="2023Q2", yoy_revenue_growth=0.2, yoy_profit_growth=0.2),
        ]
        score = calculate_prosperity_score(data)
        assert score.relative_delta_g == 0.0

    def test_compute_sets_relative_delta_g(self):
        scores, infos = _make_industry_stocks("电子", 6, base_delta_g=3.0)
        compute_relative_delta_g(scores, infos)
        for code in scores:
            assert scores[code].relative_delta_g != 0.0

    def test_risk_warning_on_negative_relative_delta_g(self):
        score = _ps(
            relative_delta_g=-3.0,
            delta_g=5.0,
            revenue_score=60.0,
            slope_score=60.0,
            duration_score=60.0,
        )
        result = generate_risk_warnings(score, [])
        assert "增速放缓" in result

    def test_no_risk_warning_on_zero_relative_delta_g(self):
        score = _ps(
            relative_delta_g=0.0,
            delta_g=5.0,
            revenue_score=60.0,
            slope_score=60.0,
            duration_score=60.0,
        )
        result = generate_risk_warnings(score, [])
        assert "增速放缓" not in result

    def test_full_lifecycle_integration(self):
        scores, infos = _make_industry_stocks("化工", 6, base_delta_g=5.0)
        scores["00000.SZ"] = _ps(ts_code="00000.SZ", delta_g=-2.0, composite_score=40.0)
        infos["00000.SZ"] = _si(ts_code="00000.SZ", industry="化工")
        compute_relative_delta_g(scores, infos)
        below_median = scores["00000.SZ"]
        assert below_median.relative_delta_g < 0
        fd = [_fd(ts_code="00000.SZ")]
        warnings = generate_risk_warnings(below_median, fd)
        assert "增速放缓" in warnings


# ── E5. Transition zone boundary tests ───────────────────────────────


class TestE5StockStageBoundaries:
    """E5: Exact boundary values for classify_stock_stage."""

    def test_revenue_exactly_85_transition_zone(self):
        score = _ps(revenue_score=85.0, delta_g=5.0, relative_delta_g=0.0)
        assert classify_stock_stage(score) == "上升拐点"

    def test_revenue_exactly_75_transition_zone(self):
        score = _ps(revenue_score=75.0, delta_g=5.0, relative_delta_g=0.0)
        assert classify_stock_stage(score) == "上升拐点"

    def test_relative_delta_g_exactly_5_not_high_growth(self):
        score = _ps(revenue_score=80.0, delta_g=5.0, relative_delta_g=5.0)
        assert classify_stock_stage(score) == "上升拐点"

    def test_relative_delta_g_exactly_neg5_not_high_growth(self):
        score = _ps(revenue_score=80.0, delta_g=5.0, relative_delta_g=-5.0)
        assert classify_stock_stage(score) == "上升拐点"

    def test_relative_delta_g_just_above_5_is_high_growth(self):
        score = _ps(revenue_score=80.0, delta_g=5.0, relative_delta_g=5.01)
        assert classify_stock_stage(score) == "加速期"

    def test_relative_delta_g_just_below_neg5(self):
        score = _ps(revenue_score=80.0, delta_g=5.0, relative_delta_g=-5.01)
        assert classify_stock_stage(score) == "上升拐点"


class TestE5IndustryStageBoundaries:
    """E5: Exact boundary values for classify_industry_stage."""

    def _make_ind(self, avg_revenue: float, median_delta: float) -> IndustryProsperityScore:
        return IndustryProsperityScore(
            industry="电子",
            stock_count=5,
            avg_composite_score=60.0,
            median_delta_g=median_delta,
            avg_revenue_score=avg_revenue,
            avg_profit_score=60.0,
            avg_slope_score=60.0,
            avg_duration_score=60.0,
            stage="",
            ignition_count=0,
            top_stock_codes=[],
        )

    def test_avg_revenue_exactly_85(self):
        ind = self._make_ind(85.0, 5.0)
        assert classify_industry_stage(ind) == "上升拐点"

    def test_avg_revenue_exactly_75(self):
        ind = self._make_ind(75.0, 5.0)
        assert classify_industry_stage(ind) == "上升拐点"

    def test_median_delta_g_exactly_5(self):
        ind = self._make_ind(80.0, 5.0)
        assert classify_industry_stage(ind) == "上升拐点"

    def test_median_delta_g_exactly_neg5(self):
        ind = self._make_ind(80.0, -5.0)
        assert classify_industry_stage(ind) == "下降拐点"


class TestE5IgnitionBoundaries:
    """E5: screen_g_delta_g_ignition at exact 85/75 boundaries."""

    def test_revenue_exactly_85_transition_needs_rdg_above_5(self):
        scores = {"a.SZ": _ps(ts_code="a.SZ", revenue_score=85.0, delta_g=5, relative_delta_g=5.0)}
        assert screen_g_delta_g_ignition(scores) == set()

    def test_revenue_exactly_85_qualifies_when_rdg_above_5(self):
        scores = {"a.SZ": _ps(ts_code="a.SZ", revenue_score=85.0, delta_g=5, relative_delta_g=6.0)}
        assert screen_g_delta_g_ignition(scores) == {"a.SZ"}

    def test_revenue_exactly_75_transition_needs_rdg_above_5(self):
        scores = {"a.SZ": _ps(ts_code="a.SZ", revenue_score=75.0, delta_g=5, relative_delta_g=5.0)}
        assert screen_g_delta_g_ignition(scores) == set()

    def test_relative_delta_g_exactly_5_in_transition(self):
        scores = {"a.SZ": _ps(ts_code="a.SZ", revenue_score=80.0, delta_g=5, relative_delta_g=5.0)}
        result = screen_g_delta_g_ignition(scores)
        assert "a.SZ" not in result


# ── E6. build_stock_details inflection + dupont ─────────────────────


class TestE6BuildStockDetailsInflection:
    """E6: build_stock_details populates inflection and dupont_driver."""

    def test_inflection_populated_with_fd(self):
        scores = {"x.SZ": _ps(ts_code="x.SZ", revenue_score=60, delta_g=5)}
        infos = {"x.SZ": _si(ts_code="x.SZ", industry="电子")}
        fd = {"x.SZ": [_fd(ts_code="x.SZ"), _fd(ts_code="x.SZ", period="2024Q2")]}
        result = build_stock_details(scores, infos, fd, set())
        assert result["x.SZ"].inflection is not None
        assert result["x.SZ"].inflection.stage is not None

    def test_dupont_driver_set_with_valid_revenue_assets(self):
        scores = {"x.SZ": _ps(ts_code="x.SZ", revenue_score=60, delta_g=5)}
        infos = {"x.SZ": _si(ts_code="x.SZ", industry="电子")}
        fd = {
            "x.SZ": [
                _fd(ts_code="x.SZ", revenue=100.0, total_assets=200.0, total_debt=50.0, roe=15.0)
            ]
        }
        result = build_stock_details(scores, infos, fd, set())
        assert result["x.SZ"].dupont_driver is not None
        assert isinstance(result["x.SZ"].dupont_driver, str)
        assert len(result["x.SZ"].dupont_driver) > 0

    def test_dupont_driver_none_without_fd(self):
        scores = {"x.SZ": _ps(ts_code="x.SZ", revenue_score=60, delta_g=5)}
        infos = {"x.SZ": _si(ts_code="x.SZ", industry="电子")}
        result = build_stock_details(scores, infos, {}, set())
        assert result["x.SZ"].dupont_driver is None

    def test_dupont_negative_roe(self):
        scores = {"x.SZ": _ps(ts_code="x.SZ", revenue_score=60, delta_g=5)}
        infos = {"x.SZ": _si(ts_code="x.SZ", industry="电子")}
        fd = {"x.SZ": [_fd(ts_code="x.SZ", roe=-5.0)]}
        result = build_stock_details(scores, infos, fd, set())
        assert result["x.SZ"].dupont_driver == "ROE为负（需警惕）"


# ── E7. CF boundary in generate_risk_warnings ────────────────────────


class TestE7CFBoundaryRiskWarnings:
    """E7: operating_cf exact boundary in generate_risk_warnings."""

    def test_cf_slightly_negative_fires_warning(self):
        score = _ps(delta_g=5.0, revenue_score=60.0, slope_score=60.0, duration_score=60.0)
        fd = [_fd(operating_cf=-0.001)]
        result = generate_risk_warnings(score, fd)
        assert "经营性现金流为负" in result

    def test_cf_exactly_zero_no_warning(self):
        score = _ps(delta_g=5.0, revenue_score=60.0, slope_score=60.0, duration_score=60.0)
        fd = [_fd(operating_cf=0.0)]
        result = generate_risk_warnings(score, fd)
        assert "经营性现金流为负" not in result

    def test_cf_slightly_positive_no_warning(self):
        score = _ps(delta_g=5.0, revenue_score=60.0, slope_score=60.0, duration_score=60.0)
        fd = [_fd(operating_cf=0.001)]
        result = generate_risk_warnings(score, fd)
        assert "经营性现金流为负" not in result
