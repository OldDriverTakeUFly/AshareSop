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
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.financial_fetcher import fetch_batch_financial
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.momentum import analyze_momentum_batch
from davis_analyzer.prosperity import batch_prosperity
from davis_analyzer.prosperity_sector import (
    classify_stock_stage,
    compute_relative_delta_g,
    screen_g_delta_g_ignition,
)
from davis_analyzer.scoring import calculate_davis_double_score, rank_stocks
from davis_analyzer.stock_universe import build_stock_universe
from davis_analyzer.trend import batch_trend
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import (
    DavisDoubleScore,
    DividendSignal,
    DistressSignal,
    FinancialData,
    ForecastSignal,
    ForwardOverlay,
    MomentumSignal,
    PipelineResult,
    ProsperityScore,
    PsCrossCheck,
    StockInfo,
    ValuationData,
)
from davis_analyzer.valuation import batch_valuation, fetch_valuation_history
from davis_analyzer.valuation_forward import (
    calculate_forward_overlay,
    calculate_ps_crosscheck,
)

# Pre-filter threshold: only process stocks with valuation_score above this
_VALUATION_PRE_FILTER = 50.0


def run_screening_pipeline(
    dry_run: bool = False,
    top_n: int = 30,
) -> PipelineResult:
    """Execute the full Davis Double Play screening pipeline.

    Args:
        dry_run: When True and the TushareClient cannot be created (e.g. no
            token), return an empty result instead of raising. This is *not*
            a cache-only mode — the pipeline still issues live API calls once
            the client exists. True offline replay is not implemented.
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
            logger.warning("dry_run=True but no cached data available — returning empty list")
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
    logger.info("Step 5/8: Fetching financial data for {} stocks...", len(filtered_codes))
    financial_data: dict[str, list[FinancialData]] = fetch_batch_financial(client, filtered_codes)
    logger.info("Financial data fetched for {} stocks", len(financial_data))

    # ── Step 6/8: Calculate prosperity scores ──
    logger.info("Step 6/8: Calculating prosperity (景气度) scores...")
    prosperity_scores: dict[str, ProsperityScore] = batch_prosperity(financial_data)
    logger.info("Prosperity scores calculated for {} stocks", len(prosperity_scores))

    # ── Step 7/8: Calculate distress scores + combine ──
    logger.info("Step 7/8: Calculating distress scores for {} stocks...", len(prosperity_scores))

    # ── Step 7.5: Fetch daily PE/PB series and calculate trend scores ──
    logger.info("Step 7.5: Calculating PE/PB trend scores for {} stocks...", len(prosperity_scores))
    trend_history_map: dict[str, tuple[pd.Series, pd.Series]] = {}
    valuation_history_map: dict[str, list[ValuationData]] = {}
    for ts_code in prosperity_scores:
        try:
            history = fetch_valuation_history(client, ts_code)
            if not history:
                continue
            valuation_history_map[ts_code] = history
            dates = pd.to_datetime([v.trade_date for v in history], format="%Y%m%d")
            pe_series = pd.Series([v.pe_ttm for v in history], index=dates)
            pb_series = pd.Series([v.pb for v in history], index=dates)
            trend_history_map[ts_code] = (pe_series, pb_series)
        except Exception:
            logger.exception("Trend history fetch failed for {}", ts_code)

    trend_scores: dict[str, float] = batch_trend(trend_history_map, stock_infos)
    logger.info("Trend scores calculated for {} stocks", len(trend_scores))

    # ── Step 7.6: Supplementary factors (momentum / dividend / forecast) ──
    # These run over the filtered set (prosperity_scores.keys(), ~2300 stocks)
    # and attach as supplementary signals — they never alter the 4-dimension
    # final_score (see guardrails). Always on; each engine is independently
    # fault-tolerant so one failing factor doesn't break the pipeline.
    logger.info(
        "Step 7.6: Computing supplementary factors for {} stocks...", len(prosperity_scores)
    )
    filtered_infos = {code: stock_infos[code] for code in prosperity_scores if code in stock_infos}

    momentum_signals: dict[str, MomentumSignal] = {}
    try:
        momentum_signals = analyze_momentum_batch(client, filtered_infos)
        logger.info("Momentum signals computed for {} stocks", len(momentum_signals))
    except Exception:
        logger.exception("Momentum batch failed — continuing without momentum signals")

    dividend_signals: dict[str, DividendSignal] = {}
    for code in prosperity_scores:
        try:
            dividend_signals[code] = analyze_dividend(client, code)
        except Exception:
            logger.debug("Dividend analysis failed for {}", code)
    logger.info("Dividend signals computed for {} stocks", len(dividend_signals))

    forecast_signals: dict[str, ForecastSignal] = {}
    for code in prosperity_scores:
        try:
            sig = analyze_forecast(client, code, prosperity_scores.get(code))
            if sig is not None:
                forecast_signals[code] = sig
        except Exception:
            logger.debug("Forecast analysis failed for {}", code)
    logger.info("Forecast signals computed for {} stocks", len(forecast_signals))

    # ── Step 7.7: Forward overlay (前景估值调整) ──
    # Parallel signal — never alters the 4-dimension final_score. Adjusts the
    # backward-looking PE percentile by a bounded [+15, −20] amount using cycle
    # stage, forecast, revision, ΔG, and ignition. Also computes the PS-PE
    # divergence cross-check from the same valuation history.
    logger.info("Step 7.7: Computing forward overlays for {} stocks...", len(prosperity_scores))

    # 7.7a: fill relative_delta_g (industry-relative acceleration) — the main
    # pipeline never calls compute_relative_delta_g, so without this the
    # transition-zone logic in classify_stock_stage sees only 0.0.
    compute_relative_delta_g(prosperity_scores, stock_infos)

    # 7.7b: ignition set (二次点火) — computed once, reused per-stock.
    ignition_set: set[str] = set()
    try:
        ignition_set = screen_g_delta_g_ignition(
            prosperity_scores, stock_infos, financial_data
        )
    except Exception:
        logger.exception("Ignition screen failed — continuing without ignition flags")

    forward_overlays: dict[str, ForwardOverlay] = {}
    ps_crosschecks: dict[str, PsCrossCheck] = {}
    for ts_code in prosperity_scores:
        try:
            prosp = prosperity_scores[ts_code]
            stage = classify_stock_stage(prosp)

            # Forecast revision reuses the cached get_forecast payload (no
            # extra API call beyond what analyze_forecast already issued).
            revision = None
            try:
                revision = analyze_forecast_revision(client, ts_code)
            except Exception:
                logger.debug("Forecast revision failed for {}", ts_code)

            fin_list = financial_data.get(ts_code, [])
            # Latest non-None profit growth for the 30% threshold rule.
            profit_growth = next(
                (fd.yoy_profit_growth for fd in fin_list if fd.yoy_profit_growth is not None),
                None,
            )

            # pe_percentile from valuation_data (0.0–1.0).
            _, pe_pct, _ = valuation_data.get(ts_code, (50.0, 0.5, 0.5))

            overlay = calculate_forward_overlay(
                stage=stage,
                relative_delta_g=prosp.relative_delta_g,
                forecast=forecast_signals.get(ts_code),
                revision=revision,
                is_ignition=ts_code in ignition_set,
                delta_g_quarters=len(fin_list),
                profit_growth=profit_growth,
                historical_pe_percentile=pe_pct,
            )
            forward_overlays[ts_code] = overlay

            # PS cross-check from the valuation history captured in Step 7.5.
            history = valuation_history_map.get(ts_code)
            if history:
                ps_check = calculate_ps_crosscheck(history)
                if ps_check is not None:
                    ps_crosschecks[ts_code] = ps_check
        except Exception:
            logger.debug("Forward overlay failed for {}", ts_code)
    logger.info(
        "Forward overlays computed for {} stocks, PS cross-checks for {}",
        len(forward_overlays),
        len(ps_crosschecks),
    )

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
            debt_ratio = total_debt / total_assets if total_assets > 0 else 0.0
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

    logger.info("Step 8/8: Scored {} stocks, ranking top {}...", len(scored_stocks), top_n)
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
        momentum_signals=momentum_signals,
        dividend_signals=dividend_signals,
        forecast_signals=forecast_signals,
        forward_overlays=forward_overlays,
        ps_crosschecks=ps_crosschecks,
    )
