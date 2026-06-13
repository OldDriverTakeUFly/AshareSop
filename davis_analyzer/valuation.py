"""PE/PB percentile valuation engine with cyclical stock handling and negative EPS fallback."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

import numpy as np
from loguru import logger

from davis_analyzer.constants import (
    CYCLICAL_INDUSTRIES,
    EPS_NEAR_ZERO_THRESHOLD,
    PERCENTILE_DAYS,
)
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo, ValuationData


def calculate_percentile(current_value: float, historical_values: list[float]) -> float:
    """Return percentile (0.0-1.0) of *current_value* within *historical_values*.

    Uses the "fractional rank" method: count values <= current, divide by total.
    Edge cases:
      - empty *historical_values* → 0.5 (neutral)
      - current == min → 0.0
      - current == max → 1.0
    """
    if not historical_values:
        return 0.5

    arr = np.array(historical_values, dtype=np.float64)
    count_le = float(np.sum(arr <= current_value))
    return count_le / len(arr)


def fetch_valuation_history(
    client: TushareClient,
    ts_code: str,
    days: int = PERCENTILE_DAYS,
) -> list[ValuationData]:
    """Fetch *days* calendar-days of daily_basic data and parse into ValuationData list.

    Rows with NaN PE or PB are skipped.
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    end_str = end_date.strftime("%Y%m%d")
    start_str = start_date.strftime("%Y%m%d")

    df = client.get_daily_basic(ts_code, start_str, end_str)

    if df is None or df.empty:
        return []

    results: list[ValuationData] = []
    for _, row in df.iterrows():
        pe_raw: object = row.get("pe_ttm")
        pb_raw: object = row.get("pb")
        ps_raw: object = row.get("ps")
        mv_raw: object = row.get("total_mv")

        if _is_nan(pe_raw) or _is_nan(pb_raw):
            continue

        results.append(
            ValuationData(
                ts_code=str(row.get("ts_code", ts_code)),
                trade_date=str(row.get("trade_date", "")),
                pe_ttm=float(pe_raw),  # type: ignore[arg-type]
                pb=float(pb_raw),  # type: ignore[arg-type]
                ps=float(ps_raw) if not _is_nan(ps_raw) else 0.0,  # type: ignore[arg-type]
                total_mv=float(mv_raw) if not _is_nan(mv_raw) else 0.0,  # type: ignore[arg-type]
            )
        )

    return results


def _is_nan(value: object) -> bool:
    try:
        return value is None or np.isnan(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return True


def detect_cyclical(industry: str) -> bool:
    """Return True if *industry* is in CYCLICAL_INDUSTRIES."""
    return industry in CYCLICAL_INDUSTRIES


def handle_negative_eps(pe_values: list[float]) -> bool:
    """Return True if any PE value is negative or near-zero (< EPS_NEAR_ZERO_THRESHOLD).

    When True, PE is unreliable and PB should be used instead.
    """
    for pe in pe_values:
        if pe < 0 or pe < EPS_NEAR_ZERO_THRESHOLD:
            return True
    return False


def calculate_valuation_score(
    valuation_history: list[ValuationData],
    is_cyclical: bool,
) -> tuple[float, float, float]:
    """Calculate valuation score from history.

    Returns (score, pe_percentile, pb_percentile).

    Scoring logic:
      - Cyclical stock OR negative EPS detected → PB-only score
      - Otherwise → weighted blend: PE×0.6 + PB×0.4
      - Lower percentile → higher score (undervalued = good)
      - Score range: 0-100
    """
    if not valuation_history:
        return 50.0, 0.5, 0.5

    latest = valuation_history[0]

    pe_series = [v.pe_ttm for v in valuation_history]
    pb_series = [v.pb for v in valuation_history]

    pe_percentile = calculate_percentile(latest.pe_ttm, pe_series)
    pb_percentile = calculate_percentile(latest.pb, pb_series)

    use_pb_only = is_cyclical or handle_negative_eps(pe_series)

    if use_pb_only:
        score = (1.0 - pb_percentile) * 100.0
    else:
        score = ((1.0 - pe_percentile) * 0.6 + (1.0 - pb_percentile) * 0.4) * 100.0

    return score, pe_percentile, pb_percentile


def batch_valuation(
    client: TushareClient,
    stock_infos: Sequence[StockInfo],
) -> dict[str, tuple[float, float, float]]:
    """Run valuation scoring for a batch of stocks.

    Returns dict mapping ts_code → (score, pe_percentile, pb_percentile).
    Stocks that fail are skipped (error logged).
    """
    results: dict[str, tuple[float, float, float]] = {}

    for info in stock_infos:
        try:
            history = fetch_valuation_history(client, info.ts_code)
            if not history:
                logger.warning("No valuation history for {}", info.ts_code)
                continue

            is_cyc = detect_cyclical(info.industry)
            score_tuple = calculate_valuation_score(history, is_cyc)
            results[info.ts_code] = score_tuple
        except Exception:
            logger.exception("Valuation failed for {}", info.ts_code)

    return results
