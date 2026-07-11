"""Cross-sectional factor scoring for the backtest engine.

This module produces a point-in-time factor score for every stock in the
universe as of a given historical date (``as_of``).  The score drives
the rebalance logic in :mod:`davis_analyzer.backtest`.

Four factors are blended (all point-in-time correct):

    * **Momentum** — :func:`davis_analyzer.momentum.analyze_momentum`
      accepts a ``today`` parameter; passing ``today=as_of`` yields a score
      using only data up to ``as_of``.

    * **Valuation** — :func:`davis_analyzer.valuation.fetch_valuation_history`
      accepts an ``as_of`` parameter that anchors the PE/PB look-back window
      to the rebalance date instead of ``date.today()``.

    * **Prosperity (景气度)** — :func:`davis_analyzer.prosperity.calculate_prosperity_score`
      consumes quarterly ``FinancialData``.  Point-in-time correctness is
      guaranteed upstream: :func:`fetch_financial_data` now filters rows by
      ``ann_date <= as_of`` so only *already-disclosed* quarters are seen.

    * **Distress (困境)** — :func:`davis_analyzer.distress.calculate_distress_score`
      consumes the same disclosed-quarter history plus the PE/PB percentile
      from the valuation factor.

Default weights mirror ``DAVIS_DOUBLE_WEIGHTS`` (valuation 0.30 + prosperity
0.30 + distress 0.25 + trend 0.15), with momentum substituting for trend.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from loguru import logger

from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo
from davis_analyzer.valuation import (
    calculate_valuation_score,
    detect_cyclical,
    fetch_valuation_history,
)


@dataclass
class FactorConfig:
    """Weights and knobs for the cross-sectional factor blend.

    Weights need not sum to 1 — they are normalised inside :func:`_blend` so
    mis-configuration degrades gracefully rather than producing out-of-range
    scores.  Default weights approximate ``DAVIS_DOUBLE_WEIGHTS`` with
    momentum standing in for the trend leg.
    """

    momentum_weight: float = 0.25
    valuation_weight: float = 0.25
    prosperity_weight: float = 0.30
    distress_weight: float = 0.20
    # Skip stocks whose momentum engine returns data_sufficient=False.
    require_momentum_data: bool = True
    # Minimum quarters of disclosed financial data to attempt prosperity/distress.
    min_quarters_for_financial: int = 2


@dataclass
class FactorScore:
    """Per-stock factor result for a single rebalance date."""

    ts_code: str
    composite: float
    momentum_score: float | None = None
    valuation_score: float | None = None
    prosperity_score: float | None = None
    distress_score: float | None = None


def _blend(
    momentum: float | None,
    valuation: float | None,
    prosperity: float | None,
    distress: float | None,
    cfg: FactorConfig,
) -> float:
    """Weighted-normalised blend of the four sub-scores.

    Only available sub-scores participate; the normaliser divides by the sum
    of weights whose sub-score is present, so a missing factor does not
    silently drag the composite to zero.
    """
    total_w = 0.0
    acc = 0.0
    if momentum is not None:
        acc += momentum * cfg.momentum_weight
        total_w += cfg.momentum_weight
    if valuation is not None:
        acc += valuation * cfg.valuation_weight
        total_w += cfg.valuation_weight
    if prosperity is not None:
        acc += prosperity * cfg.prosperity_weight
        total_w += cfg.prosperity_weight
    if distress is not None:
        acc += distress * cfg.distress_weight
        total_w += cfg.distress_weight
    if total_w == 0:
        return 0.0
    return acc / total_w


def score_universe_at(
    client: TushareClient,
    as_of: date,
    stock_infos: dict[str, StockInfo],
    config: FactorConfig | None = None,
) -> dict[str, float]:
    """Score every stock in *stock_infos* as of *as_of*.

    Returns ``{ts_code: composite_score}`` for stocks that produced at least
    one valid sub-score.  Stocks with no usable data are omitted (the engine
    treats them as un-rankable).

    Each API failure is logged and skipped — one bad stock never aborts the
    whole cross-section.
    """
    cfg = config or FactorConfig()
    scores: dict[str, float] = {}

    for ts_code, info in stock_infos.items():
        try:
            # ── Momentum (point-in-time via today=as_of) ──
            mom_signal = analyze_momentum(client, ts_code, today=as_of)
            momentum_score: float | None = None
            if mom_signal is not None and mom_signal.data_sufficient:
                momentum_score = mom_signal.momentum_score
            elif mom_signal is not None and not cfg.require_momentum_data:
                momentum_score = mom_signal.momentum_score

            # ── Valuation (point-in-time via as_of) ──
            history = fetch_valuation_history(client, ts_code, as_of=as_of)
            valuation_score: float | None = None
            pe_pct = 0.5
            pb_pct = 0.5
            if history:
                is_cyc = detect_cyclical(info.industry)
                valuation_score, pe_pct, pb_pct = calculate_valuation_score(history, is_cyc)

            # ── Prosperity + Distress (point-in-time via as_of on fetcher) ──
            # fetch_financial_data filters by ann_date <= as_of, so the list
            # only contains quarters that were publicly disclosed as of as_of.
            prosperity_score: float | None = None
            distress_score: float | None = None
            fin_data = fetch_financial_data(client, ts_code, as_of=as_of)
            if len(fin_data) >= cfg.min_quarters_for_financial:
                prosp = calculate_prosperity_score(fin_data)
                prosperity_score = prosp.composite_score

                # Distress needs the valuation percentiles + latest balance sheet.
                if valuation_score is not None:
                    eps_history = [fd.eps for fd in fin_data]
                    roe_history = [fd.roe for fd in fin_data]
                    revenue_growth_history = [
                        fd.yoy_revenue_growth for fd in fin_data if fd.yoy_revenue_growth is not None
                    ]
                    profit_growth_history = [
                        fd.yoy_profit_growth for fd in fin_data if fd.yoy_profit_growth is not None
                    ]
                    latest = fin_data[0]
                    total_debt = latest.total_debt
                    total_assets = latest.total_assets
                    debt_ratio = total_debt / total_assets if total_assets > 0 else 0.0

                    distress = calculate_distress_score(
                        eps_history=eps_history,
                        pe_pct=pe_pct,
                        pb_pct=pb_pct,
                        debt_ratio=debt_ratio,
                        operating_cf=latest.operating_cf,
                        total_debt=total_debt,
                        total_assets=total_assets,
                        roe_history=roe_history,
                        revenue_history=revenue_growth_history,
                        profit_history=profit_growth_history,
                        delta_g=prosp.delta_g,
                        ts_code=ts_code,
                    )
                    distress_score = distress.total_score

            composite = _blend(
                momentum_score, valuation_score, prosperity_score, distress_score, cfg
            )
            if all(
                s is None
                for s in (momentum_score, valuation_score, prosperity_score, distress_score)
            ):
                continue  # no usable signal — skip

            scores[ts_code] = round(composite, 2)
        except Exception:
            logger.debug("Factor scoring failed for {} at {}", ts_code, as_of)

    logger.info(
        "Cross-section scored: {}/{} stocks at as_of={}",
        len(scores),
        len(stock_infos),
        as_of,
    )
    return scores
