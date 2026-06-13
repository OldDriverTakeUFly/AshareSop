"""Report generation for Davis Double-Knock analysis.

Pure template + data — no LLM/AI involved.  Each stock report ≤1500 words.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from davis_analyzer.config import STUDIES_DIR
from davis_analyzer.prosperity import dupont_decomposition
from davis_analyzer.templates import STOCK_REPORT_TEMPLATE, SUMMARY_INDEX_TEMPLATE

if TYPE_CHECKING:
    from davis_analyzer.types import (
        DavisDoubleScore,
        DistressSignal,
        FinancialData,
        PipelineResult,
        ProsperityScore,
        StockInfo,
        ValuationData,
    )


# ── helpers ──────────────────────────────────────────────────────────


def _get_investment_conclusion(final_score: float) -> str:
    """Return investment conclusion text based on final Davis score."""
    if final_score >= 80:
        return "强烈推荐关注 — 戴维斯双击潜力巨大"
    if final_score >= 65:
        return "推荐关注 — 戴维斯双击条件较为充分"
    if final_score >= 50:
        return "可关注 — 部分戴维斯双击条件已具备"
    return "谨慎观察 — 戴维斯双击条件尚不充分"


def _get_valuation_judgment(pe_percentile: float) -> str:
    """Return valuation judgment text based on PE percentile."""
    if pe_percentile < 0.20:
        return "严重低估"
    if pe_percentile < 0.40:
        return "偏低估"
    if pe_percentile < 0.60:
        return "合理"
    return "偏高"


# ── public API ───────────────────────────────────────────────────────


def _compute_dupont_conclusion(
    financial_data: FinancialData | None,
) -> str:
    """Compute DuPont decomposition conclusion from latest FinancialData.

    Returns a driver-classification string, or a data-insufficiency message
    when the required fields are missing or would cause division-by-zero.
    """
    if financial_data is None:
        return "数据不足"

    revenue = financial_data.revenue
    net_profit = financial_data.net_profit
    total_assets = financial_data.total_assets
    total_debt = financial_data.total_debt

    # Need non-zero revenue for net_margin and asset_turnover
    if abs(revenue) < 1e-9:
        return "数据周期不足，需至少2期财报"

    net_margin = net_profit / revenue

    if abs(total_assets) < 1e-9:
        return "数据周期不足，需至少2期财报"

    asset_turnover = revenue / total_assets

    equity = total_assets - total_debt
    if abs(equity) < 1e-9:
        return "数据周期不足，需至少2期财报"

    leverage_ratio = total_assets / equity

    return dupont_decomposition(
        roe=financial_data.roe,
        net_margin=net_margin,
        asset_turnover=asset_turnover,
        leverage_ratio=leverage_ratio,
    )


def generate_stock_report(
    stock_info: StockInfo,
    valuation_data: tuple[float, float, float],
    prosperity: ProsperityScore,
    distress: DistressSignal,
    davis_score: DavisDoubleScore,
    financial_data: FinancialData | None = None,
    valuation_history_latest: ValuationData | None = None,
) -> str:
    """Generate a single-stock deep-analysis markdown report.

    Parameters
    ----------
    stock_info : StockInfo
        Basic stock metadata (code, name, industry, …).
    valuation_data : tuple
        ``(score, pe_percentile, pb_percentile)`` from valuation scoring.
    prosperity : ProsperityScore
        Prosperity dimension scores.
    distress : DistressSignal
        Distress-reversal signals and scores.
    davis_score : DavisDoubleScore
        Final composite score and rank.
    financial_data : FinancialData | None
        Latest period FinancialData — used for DuPont decomposition.
        When None, DuPont conclusion shows a data-insufficiency message.
    valuation_history_latest : ValuationData | None
        Latest row from valuation history (pe_ttm, pb, total_mv).
        When None, those display fields default to 0.0.

    Returns
    -------
    str  Markdown report string.
    """
    _val_score, pe_percentile, pb_percentile = valuation_data

    dupont_conclusion = _compute_dupont_conclusion(financial_data)

    total_mv = valuation_history_latest.total_mv if valuation_history_latest else 0.0
    pe_ttm = valuation_history_latest.pe_ttm if valuation_history_latest else 0.0
    pb = valuation_history_latest.pb if valuation_history_latest else 0.0

    signals_summary = _build_signals_summary(distress)
    risk_notes = _build_risk_notes(stock_info, distress)

    kwargs = dict(
        name=stock_info.name,
        ts_code=stock_info.ts_code,
        industry=stock_info.industry,
        total_mv=total_mv,
        pe_ttm=pe_ttm,
        pb=pb,
        pe_percentile=pe_percentile * 100,
        pb_percentile=pb_percentile * 100,
        valuation_judgment=_get_valuation_judgment(pe_percentile),
        composite_score=prosperity.composite_score,
        revenue_score=prosperity.revenue_score,
        profit_score=prosperity.profit_score,
        slope_score=prosperity.slope_score,
        duration_score=prosperity.duration_score,
        delta_g=prosperity.delta_g,
        dupont_conclusion=dupont_conclusion,
        distress_total=distress.total_score,
        layer1_score=distress.layer1_score,
        layer2_score=distress.layer2_score,
        layer3_score=distress.layer3_score,
        signals_summary=signals_summary,
        final_score=davis_score.final_score,
        valuation_score=davis_score.valuation_score,
        trend_score=davis_score.trend_score,
        prosperity_score=davis_score.prosperity_score,
        distress_score=davis_score.distress_score,
        rank=davis_score.rank,
        risk_notes=risk_notes,
        investment_conclusion=_get_investment_conclusion(davis_score.final_score),
    )

    return STOCK_REPORT_TEMPLATE.format(**kwargs)


def generate_summary_index(
    top_stocks: list[DavisDoubleScore],
    stock_infos: dict[str, StockInfo],
    valuation_data: dict[str, tuple[float, float, float]],
    prosperity_data: dict[str, ProsperityScore],
) -> str:
    """Generate the summary index markdown for top-ranked stocks.

    Parameters
    ----------
    top_stocks : list[DavisDoubleScore]
        Scored stocks sorted by rank.
    stock_infos : dict[str, StockInfo]
        ``ts_code → StockInfo`` lookup.
    valuation_data : dict[str, tuple]
        ``ts_code → (score, pe_pct, pb_pct)`` lookup.
    prosperity_data : dict[str, ProsperityScore]
        ``ts_code → ProsperityScore`` lookup.

    Returns
    -------
    str  Markdown summary index.
    """
    rows: list[str] = []
    for ds in top_stocks:
        info = stock_infos.get(ds.ts_code)
        val = valuation_data.get(ds.ts_code)
        prop = prosperity_data.get(ds.ts_code)

        name = info.name if info else "未知"
        industry = info.industry if info else "未知"
        pe_pct = val[1] * 100 if val else 0.0
        pb_pct = val[2] * 100 if val else 0.0
        prop_score = prop.composite_score if prop else 0.0

        rows.append(
            f"| {ds.rank} | {ds.ts_code} | {name} | {industry} "
            f"| {pe_pct:.1f}% | {pb_pct:.1f}% "
            f"| {ds.trend_score:.1f} | {prop_score:.1f} | {ds.distress_score:.1f} "
            f"| {ds.final_score:.1f} |"
        )

    date_str = datetime.now().strftime("%Y-%m-%d")
    return SUMMARY_INDEX_TEMPLATE.format(
        date=date_str,
        top_n=len(top_stocks),
        table_rows="\n".join(rows),
    )


def save_all_reports(
    result: PipelineResult,
    output_dir: str | None = None,
) -> list[str]:
    """Generate and persist individual reports + summary index.

    Parameters
    ----------
    result : PipelineResult
        Pipeline result containing scores and all intermediate data dicts.
    output_dir : str | None
        Directory to write reports into.  Defaults to
        ``davis_analyzer/studies/`` from config.

    Returns
    -------
    list[str]  Absolute paths of all files written.
    """
    out = Path(output_dir) if output_dir else STUDIES_DIR
    out.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []

    top_scores: list[DavisDoubleScore] = []
    stock_infos: dict[str, StockInfo] = {}
    valuation_map: dict[str, tuple[float, float, float]] = {}
    prosperity_map: dict[str, ProsperityScore] = {}

    for ds in result.scores:
        ts_code = ds.ts_code
        si = result.stock_infos.get(ts_code)
        val = result.valuation_data.get(ts_code)
        prop = result.prosperity_scores.get(ts_code)
        dist = result.distress_signals.get(ts_code)
        fin_list = result.financial_data.get(ts_code)

        if si is None or val is None or prop is None or dist is None:
            continue

        fin_latest = fin_list[0] if fin_list else None

        md = generate_stock_report(
            stock_info=si,
            valuation_data=val,
            prosperity=prop,
            distress=dist,
            davis_score=ds,
            financial_data=fin_latest,
        )

        filename = f"{ds.rank}_{ds.ts_code}_{ds.name}_深度研报.md"
        filepath = out / filename
        filepath.write_text(md, encoding="utf-8")
        saved.append(str(filepath))

        top_scores.append(ds)
        stock_infos[ts_code] = si
        valuation_map[ts_code] = val
        prosperity_map[ts_code] = prop

    if top_scores:
        summary_md = generate_summary_index(
            top_scores, stock_infos, valuation_map, prosperity_map
        )
        date_str = datetime.now().strftime("%Y%m%d")
        summary_name = f"戴维斯双击估值筛选汇总_{date_str}.md"
        summary_path = out / summary_name
        summary_path.write_text(summary_md, encoding="utf-8")
        saved.append(str(summary_path))

    return saved


# ── internal helpers ─────────────────────────────────────────────────


def _build_signals_summary(distress: DistressSignal) -> str:
    """Build human-readable signals summary from distress detail dict."""
    detail = distress.signals_detail or {}
    if not detail:
        return "暂无显著信号"

    skip_keys = {"risk_items"}
    lines: list[str] = []
    for category, items in detail.items():
        if category in skip_keys:
            continue
        if isinstance(items, list):
            for item in items:
                lines.append(f"- **{category}**：{item}")
        elif isinstance(items, str):
            lines.append(f"- **{category}**：{items}")
        elif isinstance(items, dict):
            for k, v in items.items():
                lines.append(f"- **{category}/{k}**：{v}")

    return "\n".join(lines) if lines else "暂无显著信号"


def _build_risk_notes(stock_info: StockInfo, distress: DistressSignal) -> str:
    """Build stock-specific risk notes section."""
    notes: list[str] = []

    if stock_info.is_cyclical:
        notes.append(
            "- 该股属于周期性行业，业绩波动较大，需关注行业景气周期位置"
        )

    detail = distress.signals_detail or {}
    high_risk_items = detail.get("risk_items", [])
    if isinstance(high_risk_items, list):
        for item in high_risk_items:
            notes.append(f"- {item}")

    return "\n".join(notes)
