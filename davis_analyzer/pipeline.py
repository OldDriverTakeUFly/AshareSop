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

from loguru import logger

from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.financial_fetcher import fetch_batch_financial
from davis_analyzer.prosperity import batch_prosperity
from davis_analyzer.scoring import calculate_davis_double_score, rank_stocks
from davis_analyzer.stock_universe import build_stock_universe
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import DavisDoubleScore, StockInfo
from davis_analyzer.valuation import batch_valuation

# Pre-filter threshold: only process stocks with valuation_score above this
_VALUATION_PRE_FILTER = 50.0


def run_screening_pipeline(
    dry_run: bool = False,
    top_n: int = 30,
) -> list[DavisDoubleScore]:
    """Execute the full Davis Double Play screening pipeline.

    Args:
        dry_run: If True, only use cached data. Returns empty list if no cache.
        top_n: Number of top-ranked stocks to return.

    Returns:
        List of DavisDoubleScore sorted by final_score descending, limited to top_n.
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
            return []
        logger.exception("Failed to create TushareClient")
        raise

    # ── Step 2/8: Build stock universe ──
    logger.info("Step 2/8: Building stock universe...")
    stock_infos: list[StockInfo] = build_stock_universe(client)
    if not stock_infos:
        logger.warning("Stock universe is empty — aborting pipeline")
        return []
    logger.info("Stock universe built: {} stocks", len(stock_infos))

    name_map: dict[str, str] = {s.ts_code: s.name for s in stock_infos}

    # ── Step 3/8: Fetch valuation data ──
    logger.info("Step 3/8: Fetching valuation data for {} stocks...", len(stock_infos))
    valuation_map = batch_valuation(client, stock_infos)
    logger.info("Valuation data fetched for {} stocks", len(valuation_map))

    # ── Step 4/8: Pre-filter by valuation score ──
    filtered_codes: list[str] = [
        ts_code
        for ts_code, (score, _, _) in valuation_map.items()
        if score > _VALUATION_PRE_FILTER
    ]
    logger.info(
        "Step 4/8: Pre-filtered to {} stocks (valuation_score > {})",
        len(filtered_codes),
        _VALUATION_PRE_FILTER,
    )

    if not filtered_codes:
        logger.warning("No stocks passed valuation pre-filter — returning empty list")
        return []

    # ── Step 5/8: Fetch financial data for filtered stocks ──
    logger.info(
        "Step 5/8: Fetching financial data for {} stocks...", len(filtered_codes)
    )
    financial_map = fetch_batch_financial(client, filtered_codes)
    logger.info("Financial data fetched for {} stocks", len(financial_map))

    # ── Step 6/8: Calculate prosperity scores ──
    logger.info("Step 6/8: Calculating prosperity (景气度) scores...")
    prosperity_map = batch_prosperity(financial_map)
    logger.info("Prosperity scores calculated for {} stocks", len(prosperity_map))

    # ── Step 7/8: Calculate distress scores + combine ──
    logger.info(
        "Step 7/8: Calculating distress scores for {} stocks...", len(prosperity_map)
    )
    scored_stocks: list[DavisDoubleScore] = []

    for ts_code in prosperity_map:
        try:
            if ts_code not in valuation_map:
                continue

            val_score, pe_pct, pb_pct = valuation_map[ts_code]
            prosp = prosperity_map[ts_code]

            fin_data = financial_map.get(ts_code)
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

            # ── Step 8/8: Calculate final Davis Double score ──
            davis_score = calculate_davis_double_score(
                valuation_score=val_score,
                prosperity_score=prosp.composite_score,
                distress_score=distress.total_score,
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
    return ranked
