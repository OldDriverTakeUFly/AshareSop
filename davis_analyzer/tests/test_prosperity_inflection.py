"""Tests for davis_analyzer.prosperity_inflection — rule-based inflection engine."""

import pytest

from davis_analyzer.prosperity_inflection import (
    analyze_inflection,
    assess_catalysts,
    generate_inflection_narrative,
    identify_inflection_quarter,
)
from davis_analyzer.prosperity_sector import (
    classify_industry_stage,
    classify_stock_stage,
    generate_ignition_reasons,
)
from davis_analyzer.types import (
    CatalystSignal,
    FinancialData,
    InflectionAnalysis,
    IndustryProsperityScore,
    ProsperityScore,
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


def _fd(
    ts_code: str = "000001.SZ",
    period: str = "2024Q1",
    revenue: float = 100.0,
    net_profit: float = 10.0,
    roe: float = 12.0,
    operating_cf: float = 15.0,
    total_debt: float = 50.0,
    total_assets: float = 200.0,
    yoy_revenue_growth: float = 0.0,
    yoy_profit_growth: float = 0.0,
    **kw,
) -> FinancialData:
    defaults = dict(
        ts_code=ts_code,
        report_period=period,
        revenue=revenue,
        net_profit=net_profit,
        eps=0.5,
        roe=roe,
        operating_cf=operating_cf,
        total_debt=total_debt,
        total_assets=total_assets,
        yoy_revenue_growth=yoy_revenue_growth,
        yoy_profit_growth=yoy_profit_growth,
    )
    defaults.update(kw)
    return FinancialData(**defaults)


def _make_quarters(
    ts_code: str = "000001.SZ",
    yoy_revenues: list[float] | None = None,
    roe_series: list[float] | None = None,
    revenue_series: list[float] | None = None,
    cf_series: list[float] | None = None,
    debt_series: list[float] | None = None,
    asset_series: list[float] | None = None,
) -> list[FinancialData]:
    """Build chronological FinancialData records (oldest first).

    yoy_revenues are fractions (e.g. -0.05 = -5%).
    """
    periods = ["2023Q1", "2023Q2", "2023Q3", "2023Q4", "2024Q1", "2024Q2"]
    n = len(periods)
    yoy = yoy_revenues or [0.0] * n
    roes = roe_series or [10.0] * n
    revs = revenue_series or [100.0 * (1 + i * 0.05) for i in range(n)]
    cfs = cf_series or [10.0] * n
    debts = debt_series or [50.0] * n
    assets = asset_series or [200.0] * n
    return [
        _fd(
            ts_code=ts_code,
            period=periods[i],
            revenue=revs[i],
            roe=roes[i],
            operating_cf=cfs[i],
            total_debt=debts[i],
            total_assets=assets[i],
            yoy_revenue_growth=yoy[i],
        )
        for i in range(n)
    ]


# ── A. classify_stock_stage — 4 stages ───────────────────────────────


class TestClassifyStockStageFourValues:
    def test_acceleration(self):
        score = _ps(revenue_score=90.0, delta_g=5.0)
        assert classify_stock_stage(score) == "加速期"

    def test_deceleration(self):
        score = _ps(revenue_score=90.0, delta_g=-5.0)
        assert classify_stock_stage(score) == "减速期"

    def test_rising_inflection(self):
        score = _ps(revenue_score=50.0, delta_g=5.0)
        assert classify_stock_stage(score) == "上升拐点"

    def test_falling_inflection(self):
        score = _ps(revenue_score=50.0, delta_g=-5.0)
        assert classify_stock_stage(score) == "下降拐点"

    def test_never_returns_old_inflection_stage(self):
        """拐点期 must NEVER appear — it's split into 上升拐点 / 下降拐点."""
        for rev in [0, 30, 50, 70, 79.99, 80.0]:
            for dg in [-10, -1, 0, 0.01, 1, 10]:
                score = _ps(revenue_score=rev, delta_g=dg)
                assert classify_stock_stage(score) != "拐点期"

    def test_all_four_values_distinct(self):
        results = {
            classify_stock_stage(_ps(revenue_score=90, delta_g=10)),
            classify_stock_stage(_ps(revenue_score=90, delta_g=-10)),
            classify_stock_stage(_ps(revenue_score=50, delta_g=10)),
            classify_stock_stage(_ps(revenue_score=50, delta_g=-10)),
        }
        assert results == {"加速期", "减速期", "上升拐点", "下降拐点"}


# ── B. classify_industry_stage — 4 stages ────────────────────────────


def _make_industry(
    avg_revenue_score: float = 50.0,
    median_delta_g: float = 5.0,
) -> IndustryProsperityScore:
    return IndustryProsperityScore(
        industry="电子",
        stock_count=5,
        avg_composite_score=60.0,
        median_delta_g=median_delta_g,
        avg_revenue_score=avg_revenue_score,
        avg_profit_score=60.0,
        avg_slope_score=60.0,
        avg_duration_score=60.0,
        stage="",
        ignition_count=0,
        top_stock_codes=[],
    )


class TestClassifyIndustryStageFourValues:
    def test_acceleration(self):
        assert classify_industry_stage(_make_industry(90.0, 5.0)) == "加速期"

    def test_deceleration(self):
        assert classify_industry_stage(_make_industry(90.0, -3.0)) == "减速期"

    def test_rising_inflection(self):
        assert classify_industry_stage(_make_industry(50.0, 3.0)) == "上升拐点"

    def test_falling_inflection(self):
        assert classify_industry_stage(_make_industry(50.0, -3.0)) == "下降拐点"

    def test_never_returns_old_inflection_stage(self):
        for rev in [0, 30, 50, 70, 79.99, 80.0]:
            for dg in [-10, -1, 0, 0.01, 1, 10]:
                ind = _make_industry(rev, dg)
                assert classify_industry_stage(ind) != "拐点期"

    def test_all_four_values_distinct(self):
        results = {
            classify_industry_stage(_make_industry(90, 10)),
            classify_industry_stage(_make_industry(90, -10)),
            classify_industry_stage(_make_industry(50, 10)),
            classify_industry_stage(_make_industry(50, -10)),
        }
        assert results == {"加速期", "减速期", "上升拐点", "下降拐点"}


# ── C. generate_ignition_reasons ─────────────────────────────────────


class TestGenerateIgnitionReasons:
    def test_ignition_stock_returns_reasons(self):
        score = _ps(
            revenue_score=90.0,
            delta_g=8.0,
            profit_score=85.0,
            slope_score=70.0,
        )
        reasons = generate_ignition_reasons(score)
        assert len(reasons) == 4
        assert any("营收评分" in r for r in reasons)
        assert any("ΔG" in r for r in reasons)
        assert any("利润评分" in r for r in reasons)
        assert any("趋势评分" in r for r in reasons)

    def test_non_ignition_stock_empty_reasons(self):
        score = _ps(revenue_score=50.0, delta_g=-5.0, profit_score=40.0, slope_score=30.0)
        assert generate_ignition_reasons(score) == []

    def test_partial_reasons(self):
        score = _ps(revenue_score=50.0, delta_g=8.0, profit_score=40.0, slope_score=30.0)
        reasons = generate_ignition_reasons(score)
        assert len(reasons) == 1
        assert "ΔG" in reasons[0]

    def test_reason_format_contains_value(self):
        score = _ps(revenue_score=90.0, delta_g=5.0)
        reasons = generate_ignition_reasons(score)
        assert any("90" in r for r in reasons)


# ── D. identify_inflection_quarter ───────────────────────────────────


class TestIdentifyInflectionQuarter:
    def test_negative_to_positive_crossing(self):
        series = [-5.0, -3.0, 2.0, 8.0, 15.0]
        periods = ["2023Q1", "2023Q2", "2023Q3", "2023Q4", "2024Q1"]
        assert identify_inflection_quarter(series, periods) == "2023Q3"

    def test_positive_to_negative_crossing(self):
        series = [10.0, 5.0, -2.0, -8.0]
        periods = ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
        assert identify_inflection_quarter(series, periods) == "2023Q3"

    def test_no_crossing_all_positive(self):
        series = [5.0, 8.0, 10.0]
        periods = ["2023Q1", "2023Q2", "2023Q3"]
        assert identify_inflection_quarter(series, periods) is None

    def test_no_crossing_all_negative(self):
        series = [-5.0, -3.0, -1.0]
        periods = ["2023Q1", "2023Q2", "2023Q3"]
        assert identify_inflection_quarter(series, periods) is None

    def test_multiple_crossings_returns_last(self):
        series = [-5.0, 3.0, -2.0, 8.0]
        periods = ["2023Q1", "2023Q2", "2023Q3", "2023Q4"]
        assert identify_inflection_quarter(series, periods) == "2023Q4"

    def test_insufficient_data(self):
        assert identify_inflection_quarter([5.0], ["2023Q1"]) is None

    def test_empty_series(self):
        assert identify_inflection_quarter([], []) is None

    def test_boundary_zero_treated_as_positive(self):
        series = [-5.0, 0.0, 5.0]
        periods = ["2023Q1", "2023Q2", "2023Q3"]
        result = identify_inflection_quarter(series, periods)
        assert result == "2023Q2"

    def test_mismatched_lengths(self):
        series = [-5.0, 3.0, 8.0]
        periods = ["2023Q1", "2023Q2"]
        result = identify_inflection_quarter(series, periods)
        assert result == "2023Q2"


# ── E. assess_catalysts ──────────────────────────────────────────────


class TestAssessCatalysts:
    def test_roe_improving(self):
        data = _make_quarters(
            roe_series=[10.0, 10.0, 10.0, 10.0, 12.0, 15.0],
        )
        catalysts = assess_catalysts(data)
        types = [c.signal_type for c in catalysts]
        assert "roe_improving" in types

    def test_cashflow_positive(self):
        data = _make_quarters(
            cf_series=[-5.0, -5.0, -5.0, 10.0, 15.0, 20.0],
        )
        catalysts = assess_catalysts(data)
        types = [c.signal_type for c in catalysts]
        assert "cashflow_positive" in types

    def test_debt_declining(self):
        data = _make_quarters(
            debt_series=[80.0, 80.0, 80.0, 80.0, 40.0, 30.0],
            asset_series=[200.0] * 6,
        )
        catalysts = assess_catalysts(data)
        types = [c.signal_type for c in catalysts]
        assert "debt_declining" in types

    def test_revenue_stabilizing(self):
        data = _make_quarters(
            revenue_series=[100.0, 90.0, 95.0, 100.0, 110.0, 120.0],
        )
        catalysts = assess_catalysts(data)
        types = [c.signal_type for c in catalysts]
        assert "revenue_stabilizing" in types

    def test_all_catalysts_present(self):
        data = _make_quarters(
            roe_series=[10.0, 10.0, 10.0, 10.0, 12.0, 15.0],
            cf_series=[5.0, 5.0, 5.0, 5.0, 10.0, 15.0],
            debt_series=[80.0, 80.0, 80.0, 80.0, 40.0, 30.0],
            revenue_series=[100.0, 90.0, 95.0, 100.0, 110.0, 120.0],
        )
        catalysts = assess_catalysts(data)
        assert len(catalysts) == 4

    def test_insufficient_data_returns_empty(self):
        assert assess_catalysts([_fd()]) == []

    def test_empty_data_returns_empty(self):
        assert assess_catalysts([]) == []

    def test_catalyst_strength_in_range(self):
        data = _make_quarters(
            roe_series=[10.0, 10.0, 10.0, 10.0, 12.0, 15.0],
        )
        catalysts = assess_catalysts(data)
        for c in catalysts:
            assert 0.0 <= c.strength <= 100.0


# ── F. analyze_inflection ────────────────────────────────────────────


class TestAnalyzeInflection:
    def test_acceleration_stage(self):
        score = _ps(revenue_score=90.0, delta_g=10.0)
        data = _make_quarters(
            yoy_revenues=[0.05, 0.08, 0.12, 0.15, 0.20, 0.25],
        )
        result = analyze_inflection(score, "加速期", data)
        assert isinstance(result, InflectionAnalysis)
        assert result.stage == "加速期"
        assert "加速" in result.primary_driver

    def test_rising_inflection_stage(self):
        score = _ps(revenue_score=50.0, delta_g=8.0)
        data = _make_quarters(
            yoy_revenues=[-0.10, -0.05, 0.02, 0.08, 0.12, 0.15],
            roe_series=[8.0, 9.0, 10.0, 11.0, 12.0, 14.0],
            cf_series=[5.0, 8.0, 10.0, 12.0, 15.0, 20.0],
        )
        result = analyze_inflection(score, "上升拐点", data)
        assert result.stage == "上升拐点"
        assert len(result.catalysts) > 0
        assert "回升" in result.narrative or "恢复" in result.narrative or "上升拐点确认" in result.narrative

    def test_falling_inflection_stage(self):
        score = _ps(revenue_score=50.0, delta_g=-8.0)
        data = _make_quarters(
            yoy_revenues=[0.15, 0.10, 0.05, -0.02, -0.08, -0.15],
            roe_series=[15.0, 14.0, 13.0, 12.0, 10.0, 8.0],
        )
        result = analyze_inflection(score, "下降拐点", data)
        assert result.stage == "下降拐点"
        assert len(result.catalysts) > 0
        assert "下降拐点确认" in result.narrative or "走弱" in result.narrative or "恶化" in result.narrative

    def test_deceleration_stage(self):
        score = _ps(revenue_score=85.0, delta_g=-5.0)
        data = _make_quarters(
            yoy_revenues=[0.30, 0.25, 0.20, 0.15, 0.10, 0.05],
        )
        result = analyze_inflection(score, "减速期", data)
        assert result.stage == "减速期"
        assert "放缓" in result.primary_driver

    def test_insufficient_data_graceful(self):
        """Less than 3 quarters should not crash."""
        score = _ps(revenue_score=50.0, delta_g=5.0)
        data = [_fd(period="2024Q1"), _fd(period="2024Q2")]
        result = analyze_inflection(score, "上升拐点", data)
        assert isinstance(result, InflectionAnalysis)
        assert result.stage == "上升拐点"

    def test_empty_financial_data(self):
        score = _ps(revenue_score=50.0, delta_g=5.0)
        result = analyze_inflection(score, "上升拐点", [])
        assert isinstance(result, InflectionAnalysis)
        assert result.catalysts == []
        assert result.inflection_quarter is None

    def test_inflection_quarter_identified(self):
        score = _ps(revenue_score=50.0, delta_g=8.0)
        data = _make_quarters(
            yoy_revenues=[-0.10, -0.05, 0.02, 0.08, 0.12, 0.15],
        )
        result = analyze_inflection(score, "上升拐点", data)
        assert result.inflection_quarter is not None
        assert result.inflection_quarter == "2023Q3"

    def test_ts_code_preserved(self):
        score = _ps(ts_code="600519.SH", revenue_score=50.0, delta_g=5.0)
        data = _make_quarters(ts_code="600519.SH")
        result = analyze_inflection(score, "上升拐点", data)
        assert result.ts_code == "600519.SH"

    def test_narrative_non_empty(self):
        score = _ps(revenue_score=90.0, delta_g=10.0)
        data = _make_quarters(yoy_revenues=[0.05, 0.08, 0.12, 0.15, 0.20, 0.25])
        result = analyze_inflection(score, "加速期", data)
        assert len(result.narrative) > 0


# ── G. generate_inflection_narrative ─────────────────────────────────


class TestGenerateInflectionNarrative:
    def test_rising_inflection_narrative(self):
        analysis = InflectionAnalysis(
            ts_code="000001.SZ",
            stage="上升拐点",
            inflection_quarter="2024Q3",
            primary_driver="营收企稳回升",
            catalysts=[CatalystSignal(signal_type="roe_improving", description="ROE从10%升至15%", strength=50.0)],
            narrative="",
        )
        result = generate_inflection_narrative(analysis)
        assert "上升拐点确认" in result
        assert "2024Q3" in result

    def test_falling_inflection_narrative(self):
        analysis = InflectionAnalysis(
            ts_code="000001.SZ",
            stage="下降拐点",
            inflection_quarter="2024Q3",
            primary_driver="增速持续走弱",
            catalysts=[],
            narrative="",
        )
        result = generate_inflection_narrative(analysis)
        assert "下降拐点确认" in result

    def test_acceleration_narrative(self):
        analysis = InflectionAnalysis(
            ts_code="000001.SZ",
            stage="加速期",
            inflection_quarter=None,
            primary_driver="营收加速增长",
            catalysts=[],
            narrative="",
        )
        result = generate_inflection_narrative(analysis)
        assert "景气加速" in result

    def test_deceleration_narrative(self):
        analysis = InflectionAnalysis(
            ts_code="000001.SZ",
            stage="减速期",
            inflection_quarter=None,
            primary_driver="增速边际放缓",
            catalysts=[],
            narrative="",
        )
        result = generate_inflection_narrative(analysis)
        assert "放缓" in result

    def test_narrative_includes_catalyst(self):
        analysis = InflectionAnalysis(
            ts_code="000001.SZ",
            stage="上升拐点",
            inflection_quarter="2024Q1",
            primary_driver="营收企稳回升",
            catalysts=[CatalystSignal(signal_type="roe_improving", description="ROE从10%升至12%", strength=20.0)],
            narrative="",
        )
        result = generate_inflection_narrative(analysis)
        assert "ROE从10%升至12%" in result

    def test_narrative_no_inflection_quarter(self):
        analysis = InflectionAnalysis(
            ts_code="000001.SZ",
            stage="加速期",
            inflection_quarter=None,
            primary_driver="营收加速增长",
            catalysts=[],
            narrative="",
        )
        result = generate_inflection_narrative(analysis)
        assert "拐点出现在" not in result
