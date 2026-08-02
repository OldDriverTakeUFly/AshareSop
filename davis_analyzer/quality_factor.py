"""Quality factor (QMJ-style) — profitability + earnings quality + leverage safety.

Implements a 3-sub-dimension quality score using ONLY existing financial data
(ROE, operating cash flow, net profit, total assets, total liabilities — all
100% covered in the financial table). No new data fetching required.

Sub-dimensions:
  1. Profitability (40%): ROE level (0-20% → 0-100) + OCF/Assets (0-10% → 0-100)
  2. Earnings Quality (30%): OCF/NetProfit ratio (accrual anomaly detection)
  3. Leverage Safety (30%): Debt/Assets ratio inverted (0% → 100, 60% → 0)

Academic basis: Asness et al. (2018) "Quality Minus Junk" + Sloan (1996)
accrual anomaly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from davis_analyzer.tushare_client import TushareClient


@dataclass
class QualityScore:
    """Quality factor output (0-100, higher = better quality)."""

    ts_code: str
    quality_score: float           # composite 0-100
    profitability_score: float     # sub-dimension 1: ROE level + CF-ROA
    earnings_quality_score: float  # sub-dimension 2: OCF/NI ratio + stability
    leverage_score: float          # sub-dimension 3: debt/assets inverted
    roe: float | None = None
    ocf_ni_ratio: float | None = None
    debt_ratio: float | None = None
    data_sufficient: bool = True


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def analyze_quality(
    client: "TushareClient",
    ts_code: str,
    today: date | None = None,
) -> QualityScore | None:
    """Compute QMJ-style quality score for a single stock.

    Args:
        client: TushareClient instance.
        ts_code: Stock code, e.g. "300750.SZ".
        today: Reference date for point-in-time correctness.

    Returns:
        QualityScore dataclass, or None if insufficient data.
    """
    from davis_analyzer.financial_fetcher import fetch_financial_data

    ref = today or date.today()
    try:
        fin = fetch_financial_data(client, ts_code, periods=8, as_of=ref)  # 8 quarters = 2 years
    except Exception:
        logger.debug("Quality: financial fetch failed for {}", ts_code)
        return None

    return compute_quality_from_fin(ts_code, fin)


def compute_quality_from_fin(ts_code: str, fin: list) -> QualityScore | None:
    """Compute quality score from pre-fetched FinancialData list.

    This avoids re-fetching financial data when it's already available
    (e.g., in _compute_factor_scores_at where prosperity already fetched it).

    Args:
        ts_code: Stock code.
        fin: List of FinancialData objects (most recent first).

    Returns:
        QualityScore dataclass, or None if insufficient data.
    """
    if not fin or len(fin) < 2:
        return None

    # Use the most recent quarter
    latest = fin[0]
    roe = latest.roe
    operating_cf = latest.operating_cf
    net_profit = latest.net_profit
    total_assets = latest.total_assets
    total_debt = latest.total_debt

    # ── Sub-dimension 1: Profitability ──
    profitability_score = 50.0  # neutral default

    # ROE level: 0% → 0, 20% → 100
    roe_val = roe if roe is not None else 0.0
    roe_level_score = _clamp(roe_val / 20.0 * 100.0)

    # CF-ROA: OCF / total_assets, 0% → 0, 10% → 100
    cf_roa_score = 50.0
    if total_assets and total_assets > 0 and operating_cf is not None:
        cf_roa = operating_cf / total_assets
        cf_roa_score = _clamp(cf_roa / 0.10 * 100.0)

    profitability_score = roe_level_score * 0.6 + cf_roa_score * 0.4

    # ── Sub-dimension 2: Earnings Quality (OCF/NetProfit) ──
    earnings_quality_score = 50.0  # neutral default
    ocf_ni_ratio = None

    if net_profit and net_profit > 0 and operating_cf is not None:
        ocf_ni_ratio = operating_cf / net_profit
        # ratio ≥ 1 → 100 (cash covers earnings), ratio ≤ 0 → 0
        earnings_quality_score = _clamp(ocf_ni_ratio * 100.0)

        # Stability bonus: check if OCF/NI ratio is consistent across quarters
        if len(fin) >= 4:
            ratios = []
            for f in fin[:4]:
                if f.net_profit and f.net_profit > 0 and f.operating_cf is not None:
                    r = f.operating_cf / f.net_profit
                    ratios.append(r)
            if len(ratios) >= 3:
                # Lower variance → more stable → bonus
                import statistics
                try:
                    std = statistics.stdev(ratios)
                    # std < 0.5 → +10 bonus, std > 2.0 → no bonus
                    stability_bonus = _clamp((2.0 - std) / 2.0 * 10.0, 0, 10)
                    earnings_quality_score = _clamp(earnings_quality_score + stability_bonus)
                except statistics.StatisticsError:
                    pass
    elif net_profit and net_profit <= 0:
        # Loss-making company → low earnings quality
        earnings_quality_score = 20.0

    # ── Sub-dimension 3: Leverage Safety ──
    leverage_score = 50.0
    debt_ratio = None

    if total_assets and total_assets > 0 and total_debt is not None:
        debt_ratio = total_debt / total_assets
        # 0% debt → 100, 60% debt → 0
        leverage_score = _clamp((0.6 - debt_ratio) / 0.6 * 100.0)

    # ── Composite ──
    quality_total = (
        profitability_score * 0.4
        + earnings_quality_score * 0.3
        + leverage_score * 0.3
    )
    quality_total = _clamp(quality_total)

    return QualityScore(
        ts_code=ts_code,
        quality_score=round(quality_total, 2),
        profitability_score=round(profitability_score, 2),
        earnings_quality_score=round(earnings_quality_score, 2),
        leverage_score=round(leverage_score, 2),
        roe=round(roe_val, 2) if roe is not None else None,
        ocf_ni_ratio=round(ocf_ni_ratio, 3) if ocf_ni_ratio is not None else None,
        debt_ratio=round(debt_ratio, 4) if debt_ratio is not None else None,
        data_sufficient=True,
    )
