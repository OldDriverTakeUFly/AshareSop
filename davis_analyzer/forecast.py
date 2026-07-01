"""业绩预告 (earnings pre-announcement) leading-indicator engine.

The prosperity engine is purely backward-looking (realised financials). This
module surfaces the *only* forward-looking signal Tushare exposes cheaply:
the management earnings pre-announcement (``forecast`` endpoint), which
discloses a YoY net-profit change range for an upcoming report period before
the formal financials land.

A forecast row maps to a 0–100 ``leading_score``:

    p_change_mid > 50 %  → 80–100   (预增 / 扭亏)
    20–50 %              → 50–80
    0–20 %               → 25–50
    < 0 %                → 0–20     (预减 / 首亏 / 续亏)

When a realised ProsperityScore is supplied, the score is *boosted* if the
pre-announced midpoint exceeds the latest realised profit growth (true
acceleration), and *penalised* when management guides below the realised
trend (deceleration). This makes the forecast a leading overlay on top of
the lagging prosperity score, not a replacement for it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

from davis_analyzer.constants import (
    FORECAST_HIGH_THRESHOLD,
    FORECAST_MID_THRESHOLD,
    FORECAST_STALE_DAYS,
)
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import ForecastSignal

if TYPE_CHECKING:
    from davis_analyzer.types import ProsperityScore

_FORECAST_LOOKBACK_MONTHS = 18  # cover the last ~6 reporting periods


def _forecast_midpoint(row: pd.Series) -> float | None:
    """Return the midpoint of p_change_min/p_change_max, or None if unbounded.

    Tushare leaves the unbounded side as 0 for some types (e.g. 预增 with no
    upper cap); we treat a 0 bound as "no information" only when the other
    bound is also missing/zero.
    """
    lo = row.get("p_change_min")
    hi = row.get("p_change_max")
    lo_f = _to_float(lo)
    hi_f = _to_float(hi)
    if lo_f is None and hi_f is None:
        return None
    if lo_f is None:
        return hi_f
    if hi_f is None:
        return lo_f
    return (lo_f + hi_f) / 2.0


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


def _growth_to_forecast_score(mid: float) -> float:
    """Map a forecast YoY-profit-change midpoint (%) to a 0–100 score.

    Bands mirror :func:`_growth_to_raw_score_profit` but are exposed here so
    the forecast engine is self-contained (no private import).
    """
    if mid > FORECAST_HIGH_THRESHOLD:
        return 80.0 + min((mid - FORECAST_HIGH_THRESHOLD) / 50.0 * 20.0, 20.0)
    if mid > FORECAST_MID_THRESHOLD:
        return 50.0 + (mid - FORECAST_MID_THRESHOLD) / (
            FORECAST_HIGH_THRESHOLD - FORECAST_MID_THRESHOLD
        ) * 30.0
    if mid >= 0.0:
        return 25.0 + mid / FORECAST_MID_THRESHOLD * 25.0
    return max(0.0, 25.0 + mid * 1.25)


def _is_stale(ann_date: str, today: date | None = None) -> bool:
    """True when *ann_date* (YYYYMMDD) is older than FORECAST_STALE_DAYS."""
    if not ann_date or len(ann_date) != 8:
        return True
    try:
        d = datetime.strptime(ann_date, "%Y%m%d").date()
    except ValueError:
        return True
    ref = today or date.today()
    return (ref - d).days > FORECAST_STALE_DAYS


def _pick_most_relevant(df: pd.DataFrame) -> pd.Series | None:
    """Choose the single most relevant forecast row.

    Preference order:
      1. the row covering the most recent report period (max end_date) …
      2. … that is not stale; fall back to the newest ann_date if all stale.
    """
    if df is None or df.empty:
        return None
    df = df.copy()
    df["ann_date"] = df["ann_date"].fillna("").astype(str)
    df["end_date"] = df["end_date"].fillna("").astype(str)
    # Prefer rows whose end_date is a real reporting period (length 8).
    df = df[df["end_date"].str.len() == 8]
    if df.empty:
        return None
    # newest report period first, then newest announcement
    df = df.sort_values(["end_date", "ann_date"], ascending=[False, False])
    fresh = df[~df["ann_date"].apply(_is_stale)]
    chosen = fresh.iloc[0] if not fresh.empty else df.iloc[0]
    return chosen


def analyze_forecast(
    client: TushareClient,
    ts_code: str,
    prosperity_score: ProsperityScore | None = None,
    today: date | None = None,
) -> ForecastSignal | None:
    """Build a ForecastSignal for *ts_code*, or None when no forecast exists.

    Parameters
    ----------
    client : TushareClient
    ts_code : str
    prosperity_score : ProsperityScore | None
        Latest realised prosperity; used to detect forecast acceleration vs
        the realised trend (delta_g boost). Optional.
    today : date | None
        Injected for deterministic tests.
    """
    end = (today or date.today()).strftime("%Y%m%d")
    start = (
        (today or date.today())
        - pd.Timedelta(days=int(_FORECAST_LOOKBACK_MONTHS * 30.5))
    ).strftime("%Y%m%d")
    try:
        df = client.get_forecast(ts_code, start, end)
    except Exception:
        logger.exception("forecast fetch failed for {}", ts_code)
        return None

    row = _pick_most_relevant(df)
    if row is None:
        return None

    mid = _forecast_midpoint(row)
    base_score = _growth_to_forecast_score(mid) if mid is not None else 50.0

    # Boost/penalise against the realised trend when both are available.
    # delta_g is the latest realised profit *growth* level proxy here; a
    # forecast midpoint above it means management guides acceleration,
    # below it means deceleration. The ±15 swing keeps the overlay from
    # drowning out the realised prosperity score.
    if prosperity_score is not None and mid is not None:
        realised_proxy = prosperity_score.delta_g
        adj = _clamp((mid - realised_proxy) * 0.3, -15.0, 15.0)
        base_score = _clamp(base_score + adj, 0.0, 100.0)

    return ForecastSignal(
        ts_code=ts_code,
        ann_date=str(row.get("ann_date", "")),
        end_date=str(row.get("end_date", "")),
        type=str(row.get("type", "")),
        p_change_min=_to_float(row.get("p_change_min")),
        p_change_max=_to_float(row.get("p_change_max")),
        p_change_mid=mid,
        leading_score=round(base_score, 2),
        is_stale=_is_stale(str(row.get("ann_date", "")), today),
    )


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
