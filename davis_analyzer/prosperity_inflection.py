"""Rule-based inflection (拐点) analysis engine.

Identifies inflection quarters, assesses recovery/deterioration catalysts,
and generates human-readable narratives for the four-stage classification:

    加速期  / 减速期  / 上升拐点  / 下降拐点
"""

from __future__ import annotations

from davis_analyzer.types import (
    CatalystSignal,
    FinancialData,
    InflectionAnalysis,
    ProsperityScore,
)

_SLOPE_LOOKBACK = 3
_CASHFLOW_STRENGTH = 80.0
_DEBT_STRENGTH = 70.0
_REVENUE_STABILIZING_STRENGTH = 60.0


def analyze_inflection(
    score: ProsperityScore,
    stage: str,
    financial_data: list[FinancialData],
) -> InflectionAnalysis:
    """Build a full InflectionAnalysis for a stock given its ProsperityScore and stage.

    Args:
        score: ProsperityScore with sub-scores and delta_g.
        stage: one of "加速期", "减速期", "上升拐点", "下降拐点".
        financial_data: chronological or unsorted list of FinancialData records.

    Returns:
        InflectionAnalysis with inflection_quarter, catalysts, and narrative populated.
    """
    ts_code = score.ts_code
    primary_driver = _determine_primary_driver(stage, score)

    if not financial_data:
        analysis = InflectionAnalysis(
            ts_code=ts_code,
            stage=stage,
            inflection_quarter=None,
            primary_driver=primary_driver,
            catalysts=[],
            narrative="",
        )
        analysis.narrative = generate_inflection_narrative(analysis)
        return analysis

    sorted_data = sorted(financial_data, key=lambda d: d.report_period)

    valid = []
    for d in sorted_data:
        growth = d.yoy_revenue_growth
        if growth is not None:
            valid.append((growth * 100, d.report_period))
    yoy_series = [v[0] for v in valid]
    report_periods = [v[1] for v in valid]

    inflection_quarter = identify_inflection_quarter(yoy_series, report_periods)
    catalysts = assess_catalysts(sorted_data)

    if stage in ("下降拐点", "减速期"):
        catalysts = _assess_risk_factors(sorted_data, score)

    analysis = InflectionAnalysis(
        ts_code=ts_code,
        stage=stage,
        inflection_quarter=inflection_quarter,
        primary_driver=primary_driver,
        catalysts=catalysts,
        narrative="",
    )
    analysis.narrative = generate_inflection_narrative(analysis)
    return analysis


def identify_inflection_quarter(
    yoy_growth_series: list[float],
    report_periods: list[str],
) -> str | None:
    """Find the most recent quarter where YoY growth crossed zero.

    Series and periods must be chronologically ordered (oldest first).
    Returns the report_period of the crossing quarter, or None when fewer
    than 2 data points or no sign change is detected.
    """
    n = min(len(yoy_growth_series), len(report_periods))
    if n < 2:
        return None

    inflection_q: str | None = None
    for i in range(1, n):
        prev = yoy_growth_series[i - 1]
        curr = yoy_growth_series[i]
        crossed = (prev < 0 <= curr) or (prev >= 0 > curr)
        if crossed:
            inflection_q = report_periods[i]
    return inflection_q


def assess_catalysts(
    financial_data: list[FinancialData],
) -> list[CatalystSignal]:
    """Identify recovery catalysts from financial data (most-recent quarter focus).

    Checks:
        - ROE improving QoQ (latest > previous)
        - Operating cash-flow positive
        - Debt ratio declining QoQ
        - Revenue trend positive over last 3 quarters
    """
    if len(financial_data) < 2:
        return []

    sorted_data = sorted(financial_data, key=lambda d: d.report_period)
    latest = sorted_data[-1]
    previous = sorted_data[-2]
    catalysts: list[CatalystSignal] = []

    if latest.roe > previous.roe:
        base = max(abs(previous.roe), 1e-9)
        strength = min(100.0, (latest.roe - previous.roe) / base * 100)
        catalysts.append(
            CatalystSignal(
                signal_type="roe_improving",
                description=f"ROE从{previous.roe:.1f}%提升至{latest.roe:.1f}%",
                strength=round(strength, 1),
            )
        )

    if latest.operating_cf > 0:
        catalysts.append(
            CatalystSignal(
                signal_type="cashflow_positive",
                description=f"经营现金流为正（{latest.operating_cf:.0f}）",
                strength=_CASHFLOW_STRENGTH,
            )
        )

    prev_assets = max(previous.total_assets, 1e-9)
    curr_assets = max(latest.total_assets, 1e-9)
    prev_ratio = previous.total_debt / prev_assets
    curr_ratio = latest.total_debt / curr_assets
    if curr_ratio < prev_ratio:
        catalysts.append(
            CatalystSignal(
                signal_type="debt_declining",
                description=f"资产负债率从{prev_ratio:.1%}降至{curr_ratio:.1%}",
                strength=_DEBT_STRENGTH,
            )
        )

    if len(sorted_data) >= _SLOPE_LOOKBACK:
        recent = [d.revenue for d in sorted_data[-_SLOPE_LOOKBACK:]]
        if recent[-1] > recent[0]:
            catalysts.append(
                CatalystSignal(
                    signal_type="revenue_stabilizing",
                    description="营收环比企稳回升",
                    strength=_REVENUE_STABILIZING_STRENGTH,
                )
            )

    return catalysts


def generate_inflection_narrative(analysis: InflectionAnalysis) -> str:
    """Build a human-readable summary from an InflectionAnalysis."""
    stage_templates = {
        "上升拐点": "上升拐点确认",
        "下降拐点": "下降拐点确认",
        "加速期": "景气加速上行",
        "减速期": "增速边际放缓",
    }
    parts: list[str] = [stage_templates.get(analysis.stage, analysis.stage)]

    if analysis.inflection_quarter:
        parts.append(f"拐点出现在{analysis.inflection_quarter}")

    parts.append(analysis.primary_driver)

    if analysis.catalysts:
        top = analysis.catalysts[0]
        parts.append(f"核心信号：{top.description}")

    return "，".join(parts)


def _determine_primary_driver(stage: str, score: ProsperityScore) -> str:
    """Determine the primary growth driver description for a given stage."""
    if stage == "加速期":
        if score.profit_score > score.revenue_score:
            return "利润加速增长"
        return "营收加速增长"
    if stage == "减速期":
        if score.profit_score < score.revenue_score:
            return "利润增速边际放缓"
        return "营收增速边际放缓"
    if stage == "上升拐点":
        return "营收企稳回升"
    return "增速持续走弱"


def _assess_risk_factors(
    financial_data: list[FinancialData],
    score: ProsperityScore,
) -> list[CatalystSignal]:
    """Identify deterioration risk factors for 下降拐点 / 减速期 stages."""
    if len(financial_data) < 2:
        return []

    sorted_data = sorted(financial_data, key=lambda d: d.report_period)
    latest = sorted_data[-1]
    previous = sorted_data[-2]
    risks: list[CatalystSignal] = []

    if latest.roe < previous.roe:
        risks.append(
            CatalystSignal(
                signal_type="roe_improving",
                description=f"ROE从{previous.roe:.1f}%下滑至{latest.roe:.1f}%",
                strength=60.0,
            )
        )

    if latest.operating_cf < 0:
        risks.append(
            CatalystSignal(
                signal_type="cashflow_positive",
                description=f"经营现金流为负（{latest.operating_cf:.0f}）",
                strength=50.0,
            )
        )

    prev_assets = max(previous.total_assets, 1e-9)
    curr_assets = max(latest.total_assets, 1e-9)
    prev_ratio = previous.total_debt / prev_assets
    curr_ratio = latest.total_debt / curr_assets
    if curr_ratio > prev_ratio:
        risks.append(
            CatalystSignal(
                signal_type="debt_declining",
                description=f"资产负债率从{prev_ratio:.1%}升至{curr_ratio:.1%}",
                strength=55.0,
            )
        )

    if score.delta_g < 0:
        risks.append(
            CatalystSignal(
                signal_type="revenue_stabilizing",
                description=f"ΔG={score.delta_g:.1f}（增速持续走弱）",
                strength=65.0,
            )
        )

    return risks
