"""Main orchestration pipeline — ties all Davis Double Play engines together.

8-step screening process:
  1. Create TushareClient
  2. Build stock universe (~4500 stocks)
  3. Fetch valuation data for all stocks
  4. Pre-filter: valuation_score > 50 → ~500-800 stocks
  5. Fetch financial data for filtered stocks only
  6. Calculate 景气度 (prosperity) scores
  7. Calculate distress scores for each stock
  8. Calculate Davis Double scores and rank
"""

from __future__ import annotations

import pandas as pd
from loguru import logger

from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_batch_financial
from davis_analyzer.prosperity import batch_prosperity
from davis_analyzer.scoring import calculate_davis_double_score, rank_stocks
from davis_analyzer.stock_universe import build_stock_universe
from davis_analyzer.trend import batch_trend
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import (
    DavisDoubleScore,
    DistressSignal,
    FinancialData,
    PipelineResult,
    ProsperityScore,
    StockInfo,
)
from davis_analyzer.valuation import batch_valuation, fetch_valuation_history

# Pre-filter threshold: only process stocks with valuation_score above this
_VALUATION_PRE_FILTER = 50.0


def run_screening_pipeline(
    dry_run: bool = False,
    top_n: int = 30,
) -> PipelineResult:
    """Execute the full Davis Double Play screening pipeline.

    Args:
        dry_run: If True, only use cached data. Returns empty list if no cache.
        top_n: Number of top-ranked stocks to return.

    Returns:
        PipelineResult containing sorted scores and all intermediate data.
    """
    # ── Step 1/8: Create client ──
    logger.info("Step 1/8: Initialising TushareClient (dry_run={})...", dry_run)
    try:
        client = TushareClient()
    except Exception:
        if dry_run:
            logger.warning(
                "dry_run=True but no cached data available — returning empty list"
            )
            return PipelineResult(
                scores=[],
                stock_infos={},
                valuation_data={},
                prosperity_scores={},
                distress_signals={},
                financial_data={},
            )
        logger.exception("Failed to create TushareClient")
        raise

    # ── Step 2/8: Build stock universe ──
    logger.info("Step 2/8: Building stock universe...")
    stock_list: list[StockInfo] = build_stock_universe(client)
    if not stock_list:
        logger.warning("Stock universe is empty — aborting pipeline")
        return PipelineResult(
            scores=[],
            stock_infos={},
            valuation_data={},
            prosperity_scores={},
            distress_signals={},
            financial_data={},
        )
    logger.info("Stock universe built: {} stocks", len(stock_list))

    stock_infos: dict[str, StockInfo] = {s.ts_code: s for s in stock_list}
    name_map: dict[str, str] = {s.ts_code: s.name for s in stock_list}

    # ── Step 3/8: Fetch valuation data ──
    logger.info("Step 3/8: Fetching valuation data for {} stocks...", len(stock_list))
    valuation_data: dict[str, tuple] = batch_valuation(client, stock_list)
    logger.info("Valuation data fetched for {} stocks", len(valuation_data))

    # ── Step 4/8: Pre-filter by valuation score ──
    filtered_codes: list[str] = [
        ts_code
        for ts_code, (score, _, _) in valuation_data.items()
        if score > _VALUATION_PRE_FILTER
    ]
    logger.info(
        "Step 4/8: Pre-filtered to {} stocks (valuation_score > {})",
        len(filtered_codes),
        _VALUATION_PRE_FILTER,
    )

    if not filtered_codes:
        logger.warning("No stocks passed valuation pre-filter — returning empty list")
        return PipelineResult(
            scores=[],
            stock_infos=stock_infos,
            valuation_data=valuation_data,
            prosperity_scores={},
            distress_signals={},
            financial_data={},
        )

    # ── Step 5/8: Fetch financial data for filtered stocks ──
    logger.info(
        "Step 5/8: Fetching financial data for {} stocks...", len(filtered_codes)
    )
    financial_data: dict[str, list[FinancialData]] = fetch_batch_financial(
        client, filtered_codes
    )
    logger.info("Financial data fetched for {} stocks", len(financial_data))

    # ── Step 6/8: Calculate prosperity scores ──
    logger.info("Step 6/8: Calculating prosperity (景气度) scores...")
    prosperity_scores: dict[str, ProsperityScore] = batch_prosperity(financial_data)
    logger.info("Prosperity scores calculated for {} stocks", len(prosperity_scores))

    # ── Step 7/8: Calculate distress scores + combine ──
    logger.info(
        "Step 7/8: Calculating distress scores for {} stocks...", len(prosperity_scores)
    )

    # ── Step 7.5: Fetch daily PE/PB series and calculate trend scores ──
    logger.info("Step 7.5: Calculating PE/PB trend scores for {} stocks...", len(prosperity_scores))
    trend_history_map: dict[str, tuple[pd.Series, pd.Series]] = {}
    for ts_code in prosperity_scores:
        try:
            history = fetch_valuation_history(client, ts_code)
            if not history:
                continue
            dates = pd.to_datetime([v.trade_date for v in history], format="%Y%m%d")
            pe_series = pd.Series([v.pe_ttm for v in history], index=dates)
            pb_series = pd.Series([v.pb for v in history], index=dates)
            trend_history_map[ts_code] = (pe_series, pb_series)
        except Exception:
            logger.exception("Trend history fetch failed for {}", ts_code)

    trend_scores: dict[str, float] = batch_trend(trend_history_map, stock_infos)
    logger.info("Trend scores calculated for {} stocks", len(trend_scores))

    scored_stocks: list[DavisDoubleScore] = []
    distress_signals: dict[str, DistressSignal] = {}

    for ts_code in prosperity_scores:
        try:
            if ts_code not in valuation_data:
                continue

            val_score, pe_pct, pb_pct = valuation_data[ts_code]
            prosp = prosperity_scores[ts_code]

            fin_data = financial_data.get(ts_code)
            if not fin_data:
                continue

            eps_history = [fd.eps for fd in fin_data]  # most-recent-first
            roe_history = [fd.roe for fd in fin_data]
            revenue_growth_history = [fd.yoy_revenue_growth for fd in fin_data]
            profit_growth_history = [fd.yoy_profit_growth for fd in fin_data]

            latest = fin_data[0]  # Use latest period for balance sheet
            total_debt = latest.total_debt
            total_assets = latest.total_assets
            debt_ratio = (
                total_debt / total_assets if total_assets > 0 else 0.0
            )
            operating_cf = latest.operating_cf

            distress = calculate_distress_score(
                eps_history=eps_history,
                pe_pct=pe_pct,
                pb_pct=pb_pct,
                debt_ratio=debt_ratio,
                operating_cf=operating_cf,
                total_debt=total_debt,
                total_assets=total_assets,
                roe_history=roe_history,
                revenue_history=revenue_growth_history,
                profit_history=profit_growth_history,
                delta_g=prosp.delta_g,
                ts_code=ts_code,
            )
            distress_signals[ts_code] = distress

            # ── Step 8/8: Calculate final Davis Double score ──
            davis_score = calculate_davis_double_score(
                valuation_score=val_score,
                prosperity_score=prosp.composite_score,
                distress_score=distress.total_score,
                trend_score=trend_scores.get(ts_code, 50.0),
                ts_code=ts_code,
                name=name_map.get(ts_code, ""),
            )
            scored_stocks.append(davis_score)

        except Exception:
            logger.exception("Error processing stock {} — skipping", ts_code)
            continue

    logger.info(
        "Step 8/8: Scored {} stocks, ranking top {}...", len(scored_stocks), top_n
    )
    ranked = rank_stocks(scored_stocks, top_n)

    logger.info(
        "Pipeline complete — {} stocks scored, returning top {}",
        len(scored_stocks),
        len(ranked),
    )
    return PipelineResult(
        scores=ranked,
        stock_infos=stock_infos,
        valuation_data=valuation_data,
        prosperity_scores=prosperity_scores,
        distress_signals=distress_signals,
        financial_data=financial_data,
        trend_scores=trend_scores,
    )
