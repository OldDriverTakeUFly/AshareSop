"""筹码集中度 (chip concentration) engine from shareholder-count trend.

A declining shareholder count (股东户数) is the canonical "主力收集 / 筹码集中"
bullish signal: the same float is being concentrated into fewer, larger
accounts. A rising count signals dispersion (动能减弱).

This module wraps the ``stk_holdernumber`` endpoint and turns the count
trend into a 0–100 ``concentration_score``:

    score = clamp( 50 + (decline_fraction / FULL_DECLINE) * 50 , 0, 100 )

where ``decline_fraction = (first - last) / first`` over the look-back
window. Net growth over the window scores below 50.

Source: this is exactly the manual workflow documented in
``research-report/references/engine-usage.md §11`` — this engine turns it
into a reusable, scored factor.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

from davis_analyzer.constants import (
    HOLDER_CONCENTRATION_FULL_DECLINE,
    HOLDER_LOOKBACK_PERIODS,
)
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import HolderConcentration

_LOOKBACK_MONTHS = max(HOLDER_LOOKBACK_PERIODS, 4) * 3 * 2  # ~2 years cushion


def _to_int(v) -> int | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _trend_label(net_change_fraction: float | None) -> str:
    if net_change_fraction is None:
        return "数据不足"
    if net_change_fraction < 0:
        return "集中(动能增强)"
    return "分散(动能减弱)"


def analyze_holder_concentration(
    client: TushareClient,
    ts_code: str,
    lookback: int = HOLDER_LOOKBACK_PERIODS,
    today: date | None = None,
) -> HolderConcentration | None:
    """Build a HolderConcentration for *ts_code*, or None when no data.

    Parameters
    ----------
    client : TushareClient
    ts_code : str
    lookback : int
        Number of most-recent reporting periods to score over.
    today : date | None
        Injected for deterministic tests.
    """
    ref = today or date.today()
    end = ref.strftime("%Y%m%d")
    start = (ref - pd.Timedelta(days=int(_LOOKBACK_MONTHS * 30.5))).strftime("%Y%m%d")
    try:
        df = client.get_stk_holdernumber(ts_code, start, end)
    except Exception:
        logger.exception("stk_holdernumber fetch failed for {}", ts_code)
        return None

    if df is None or df.empty:
        return None

    df = df.copy()
    df["end_date"] = df["end_date"].fillna("").astype(str)
    df = df[df["end_date"].str.len() == 8]
    if df.empty:
        return None

    df = df.sort_values("end_date").drop_duplicates(subset=["end_date"], keep="last")

    # Take the most recent `lookback` periods.
    window = df.tail(lookback)

    counts: list[int] = []
    periods: list[str] = []
    for _, row in window.iterrows():
        n = _to_int(row.get("holder_num"))
        if n is not None and n > 0:
            counts.append(n)
            periods.append(str(row["end_date"]))

    if len(counts) < 2:
        return HolderConcentration(
            ts_code=ts_code,
            holder_counts=counts,
            periods=periods,
            concentration_score=50.0,
            trend="数据不足",
            latest_chg_pct=None,
        )

    first, last = counts[0], counts[-1]
    net_fraction = (last - first) / first  # negative = concentrated (bullish)
    decline_fraction = -net_fraction  # positive = concentrated

    score = max(
        0.0,
        min(100.0, 50.0 + (decline_fraction / HOLDER_CONCENTRATION_FULL_DECLINE) * 50.0),
    )

    # QoQ change of the most recent period.
    latest_chg: float | None = None
    if len(counts) >= 2:
        prev = counts[-2]
        if prev:
            latest_chg = (counts[-1] - prev) / prev * 100.0

    return HolderConcentration(
        ts_code=ts_code,
        holder_counts=counts,
        periods=periods,
        concentration_score=round(score, 2),
        trend=_trend_label(net_fraction),
        latest_chg_pct=round(latest_chg, 2) if latest_chg is not None else None,
    )
