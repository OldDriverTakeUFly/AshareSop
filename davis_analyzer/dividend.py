"""红利 (dividend) factor engine.

The 4-dimension Davis Double model and the prosperity engine are entirely
growth-oriented — they cannot rank dividend payers, which the
``multi-factor-screening`` skill's 红利型 (income) domain requires. This
module turns the ``dividend`` endpoint into a scored factor.

Two sub-scores (each 0–100):

* continuity — count of consecutive trailing years with an *executed* payout
  (``div_proc == '实施'``). Plans get cancelled; only 实施 counts.
* yield — latest annual cash-dividend yield = ``cash_div / latest_close``.

``dividend_score`` = 0.5 × continuity + 0.5 × yield.

Non-dividend-payers (or payers with insufficient history) return a low-but-
non-zero score rather than 0, so the factor doesn't zero out growth stocks
that simply don't pay — those should be ranked via the other dimensions.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

from davis_analyzer.constants import (
    DIVIDEND_FULL_YIELD_PCT,
    DIVIDEND_LOOKBACK_YEARS,
)
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import DividendSignal

if TYPE_CHECKING:
    pass


def _to_float(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _year_of(end_date: str) -> int | None:
    """Extract the 4-digit year from a YYYYMMDD report period."""
    if not end_date or len(end_date) < 4:
        return None
    try:
        return int(end_date[:4])
    except ValueError:
        return None


def _executed_cash_per_year(df: pd.DataFrame) -> dict[int, float]:
    """Group executed payouts by report-year, summing cash_div.

    A year may have multiple rows (interim + final); we sum them. Only rows
    with ``div_proc == '实施'`` are counted — announced-but-unexecuted plans
    are dropped because they can be cancelled.
    """
    if df is None or df.empty:
        return {}
    df = df.copy()
    df["div_proc"] = df.get("div_proc", "").fillna("").astype(str)
    df["end_date"] = df.get("end_date", "").fillna("").astype(str)
    executed = df[df["div_proc"].str.contains("实施", na=False)]
    out: dict[int, float] = {}
    for _, row in executed.iterrows():
        year = _year_of(str(row.get("end_date", "")))
        cash = _to_float(row.get("cash_div"))
        if year is None or cash is None or cash <= 0:
            continue
        out[year] = out.get(year, 0.0) + cash
    return out


def _consecutive_trailing_years(years_with_payout: list[int], as_of_year: int) -> int:
    """Count consecutive trailing years ending at/ before *as_of_year*.

    Walks backwards from the most recent payout year ≤ as_of_year; stops at
    the first gap.
    """
    if not years_with_payout:
        return 0
    eligible = sorted({y for y in years_with_payout if y <= as_of_year}, reverse=True)
    if not eligible:
        return 0
    count = 1
    for y in eligible[1:]:
        if y == eligible[count - 1] - 1:
            count += 1
        else:
            break
    return count


def _continuity_score(consec: int, lookback: int) -> float:
    """Map consecutive-year count to 0–100, saturating at *lookback* years."""
    if consec <= 0:
        return 10.0  # non-payer floor: low but not zero
    return min(100.0, 10.0 + (consec / lookback) * 90.0)


def _yield_score(yield_pct: float | None) -> float:
    """Map annual yield (%) to 0–100, saturating at DIVIDEND_FULL_YIELD_PCT."""
    if yield_pct is None or yield_pct <= 0:
        return 10.0
    return min(100.0, 10.0 + (yield_pct / DIVIDEND_FULL_YIELD_PCT) * 90.0)


def analyze_dividend(
    client: TushareClient,
    ts_code: str,
    today: date | None = None,
) -> DividendSignal:
    """Build a DividendSignal for *ts_code*.

    Always returns a signal (never None) — a non-payer scores 10/10 → 10,
    which ranks below any real payer without zeroing out growth stocks.

    Parameters
    ----------
    client : TushareClient
    ts_code : str
    today : date | None
        Injected for deterministic tests; determines the as-of year.
    """
    ref = today or date.today()
    as_of_year = ref.year

    # Look back a few years beyond the lookback window to detect gaps.
    start_year = as_of_year - (DIVIDEND_LOOKBACK_YEARS + 2)
    start = f"{start_year}0101"
    end = ref.strftime("%Y%m%d")

    try:
        div_df = client.get_dividend(ts_code, start, end)
    except Exception:
        logger.exception("dividend fetch failed for {}", ts_code)
        div_df = pd.DataFrame()

    payout_by_year = _executed_cash_per_year(div_df)
    payout_years_sorted = sorted(payout_by_year.keys())

    consecutive = _consecutive_trailing_years(payout_years_sorted, as_of_year)
    cont_score = _continuity_score(consecutive, DIVIDEND_LOOKBACK_YEARS)

    # Latest annual yield: most recent payout year's cash_div / latest close.
    latest_yield: float | None = None
    latest_price: float | None = None
    if payout_by_year:
        latest_year = max(y for y in payout_by_year if y <= as_of_year)
        cash = payout_by_year[latest_year]
        # Fetch latest close for the yield denominator.
        try:
            price_df = client.get_daily_prices(ts_code, end, end)
            if price_df is not None and not price_df.empty:
                latest_price = _to_float(price_df.iloc[-1].get("close"))
        except Exception:
            logger.debug("price fetch failed for {} dividend yield", ts_code)
        if latest_price and latest_price > 0:
            latest_yield = cash / latest_price * 100.0

    yld_score = _yield_score(latest_yield)
    blended = round(0.5 * cont_score + 0.5 * yld_score, 2)

    payout_periods = [f"{y}1231" for y in payout_years_sorted]
    sufficient = bool(payout_by_year)

    return DividendSignal(
        ts_code=ts_code,
        consecutive_years=consecutive,
        latest_yield_pct=round(latest_yield, 4) if latest_yield is not None else None,
        dividend_score=blended,
        payout_years=payout_periods,
        data_sufficient=sufficient,
    )
