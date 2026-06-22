"""Lightweight prosperity sector pipeline — fetches financial data for ALL stocks, calculates prosperity, aggregates by industry."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from loguru import logger

from davis_analyzer.financial_fetcher import fetch_batch_financial
from davis_analyzer.prosperity import batch_prosperity
from davis_analyzer.prosperity_sector import (
    aggregate_industry_prosperity,
    build_stock_details,
    classify_industry_stage,
    compute_relative_delta_g,
    screen_g_delta_g_ignition,
)
from davis_analyzer.stock_universe import build_stock_universe
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import (
    FinancialData,
    ProsperityScore,
    ProsperitySectorResult,
    StockInfo,
)

_FETCH_CHUNK_SIZE = 50


def _empty_result() -> ProsperitySectorResult:
    return ProsperitySectorResult(
        industry_scores=[],
        stock_details={},
        stock_infos={},
        prosperity_scores={},
        financial_data={},
        analysis_date=datetime.now().isoformat(),
    )


def run_prosperity_sector_pipeline(
    top_n_per_industry: int = 10,
    progress_callback: Callable[[float, str], None] | None = None,
) -> ProsperitySectorResult:
    """Execute the prosperity sector analysis pipeline.

    Steps:
      1. Create TushareClient
      2. Build stock universe (~4500 stocks)
      3. Fetch financial data for ALL stocks (no valuation pre-filter)
      4. Calculate per-stock prosperity scores
      5. Aggregate to industry level
      6. Screen G+ΔG ignition
      7. Build stock details (stage, ignition, risk, rank)
      8. Classify industry stages
    """
    # ── Step 1/8: Create client ──
    logger.info("Step 1/8: Initialising TushareClient...")
    try:
        client = TushareClient()
    except Exception:
        logger.exception("Failed to create TushareClient")
        raise

    # ── Step 2/8: Build stock universe ──
    logger.info("Step 2/8: Building stock universe...")
    if progress_callback:
        progress_callback(5.0, "正在构建股票宇宙...")
    stock_list: list[StockInfo] = build_stock_universe(client)
    if not stock_list:
        logger.warning("Stock universe is empty — aborting pipeline")
        return _empty_result()
    logger.info("Stock universe built: {} stocks", len(stock_list))

    stock_infos: dict[str, StockInfo] = {s.ts_code: s for s in stock_list}
    ts_codes: list[str] = [s.ts_code for s in stock_list]

    # ── Step 3/8: Fetch financial data for ALL stocks ──
    logger.info("Step 3/8: Fetching financial data for {} stocks...", len(ts_codes))
    if progress_callback:
        progress_callback(10.0, f"正在获取 {len(ts_codes)} 只股票的财务数据...")

    financial_data: dict[str, list[FinancialData]] = {}
    for i in range(0, len(ts_codes), _FETCH_CHUNK_SIZE):
        chunk = ts_codes[i : i + _FETCH_CHUNK_SIZE]
        financial_data.update(fetch_batch_financial(client, chunk))
        if progress_callback:
            pct = 10.0 + (i + _FETCH_CHUNK_SIZE) / len(ts_codes) * 40.0
            progress_callback(
                min(pct, 50.0),
                f"已获取 {min(i + _FETCH_CHUNK_SIZE, len(ts_codes))}/{len(ts_codes)} 只股票财务数据",
            )

    logger.info("Financial data fetched for {} stocks", len(financial_data))

    # ── Step 4/8: Calculate prosperity scores ──
    logger.info("Step 4/8: Calculating prosperity (景气度) scores...")
    if progress_callback:
        progress_callback(55.0, "正在计算景气度评分...")
    prosperity_scores: dict[str, ProsperityScore] = batch_prosperity(financial_data)
    logger.info("Prosperity scores calculated for {} stocks", len(prosperity_scores))

    # ── Step 4.5/8: Build market_cap_map ──
    logger.info("Step 4.5/8: Fetching market cap data...")
    market_cap_map: dict[str, float] = {}
    now = datetime.now()
    end_date = now.strftime("%Y%m%d")
    start_date = (now - timedelta(days=30)).strftime("%Y%m%d")
    for ts_code in prosperity_scores:
        try:
            df = client.get_daily_basic(ts_code, start_date=start_date, end_date=end_date)
            if not df.empty:
                market_cap_map[ts_code] = float(df.iloc[-1]["total_mv"])
        except Exception:
            pass
    logger.info("Market cap data fetched for {} stocks", len(market_cap_map))

    # ── Step 5/8: Aggregate to industry level ──
    logger.info("Step 5/8: Aggregating prosperity to industry level...")
    if progress_callback:
        progress_callback(70.0, "正在汇总行业景气度...")
    industry_scores = aggregate_industry_prosperity(
        prosperity_scores, stock_infos, market_cap_map=market_cap_map
    )
    logger.info("Aggregated {} industries", len(industry_scores))

    compute_relative_delta_g(prosperity_scores, stock_infos)

    # ── Step 6/8: Screen G+ΔG ignition ──
    logger.info("Step 6/8: Screening G+ΔG ignition stocks...")
    if progress_callback:
        progress_callback(80.0, "正在筛选点火股票...")
    ignition_set: set[str] = screen_g_delta_g_ignition(
        prosperity_scores,
        stock_infos=stock_infos,
        financial_data=financial_data,
    )
    logger.info("Ignition stocks identified: {}", len(ignition_set))

    # ── Step 7/8: Build stock details ──
    logger.info("Step 7/8: Building stock details...")
    if progress_callback:
        progress_callback(88.0, "正在构建股票详情...")
    stock_details = build_stock_details(
        prosperity_scores, stock_infos, financial_data, ignition_set
    )
    logger.info("Stock details built for {} stocks", len(stock_details))

    # ── Step 8/8: Classify industry stages ──
    logger.info("Step 8/8: Classifying industry stages...")
    if progress_callback:
        progress_callback(95.0, "正在分类行业阶段...")
    for industry in industry_scores:
        industry.stage = classify_industry_stage(industry)
        industry.ignition_count = sum(
            1 for code in industry.top_stock_codes if code in ignition_set
        )
    logger.info("Industry stages classified for {} industries", len(industry_scores))

    if progress_callback:
        progress_callback(100.0, "行业景气度分析完成")

    logger.info(
        "Pipeline complete — {} industries, {} stock details",
        len(industry_scores),
        len(stock_details),
    )
    return ProsperitySectorResult(
        industry_scores=industry_scores,
        stock_details=stock_details,
        stock_infos=stock_infos,
        prosperity_scores=prosperity_scores,
        financial_data=financial_data,
        analysis_date=datetime.now().isoformat(),
    )
