"""Sell-timing monitor — frozen signal API.

Four independent sell signals. No combination, no priority arbitration,
no confidence scoring. Each signal outputs structured fields only.

The holding dict keys mirror the ``invest_holdings`` table schema in
``stockhot/storage/database.py``: ``stop_loss_hard``, ``target_price``,
``position_pct``, etc. The ``thesis_snapshot_json`` key referenced by
:func:`check_thesis_broken` is added by T16 schema extension.
"""

from __future__ import annotations

import pandas as pd


def check_hard_stop_loss(holding: dict, current_price: float) -> dict:
    """Check if hard stop-loss is triggered.

    Args:
        holding: Dict with at least a ``stop_loss_hard`` key (float).
        current_price: Current stock price.

    Returns:
        Dict with keys:
          - ``"triggered"``: bool
          - ``"signal_type"``: ``"hard_stop"``
          - ``"details"``: ``{"stop_price": float, "current_price":
            float, "pct_to_stop": float}``

    Trigger: ``current_price <= holding["stop_loss_hard"]``.
    The equality case (``==``) triggers because the check uses ``<=``.
    """
    raise NotImplementedError


def check_trailing_stop(holding: dict, ohlcv_df: pd.DataFrame) -> dict:
    """Check if trailing stop-loss is triggered.

    Trailing stop = ``max(MA20 latest, recent 20-day low) * (1 - 0.02
    buffer)``. Depends on
    ``stockhot.technical_analyzer.indicators.ma`` (T11).

    Args:
        holding: Holding dict (position metadata).
        ohlcv_df: OHLCV DataFrame with ``close, low`` columns and a
            ``DatetimeIndex``, ascending.

    Returns:
        Dict with keys:
          - ``"triggered"``: bool
          - ``"signal_type"``: ``"trailing_stop"``
          - ``"details"``: ``{"ma20": float, "recent_low": float,
            "trailing_stop": float}``

    Edge: OHLCV with fewer than 20 rows → ``{"triggered": False,
    "details": {"error": "insufficient_data"}}``.
    """
    raise NotImplementedError


def check_target_reached(holding: dict, current_price: float) -> dict:
    """Check if target price is reached.

    Trigger: ``current_price >= holding["target_price"]``. Partial trim
    suggestion scales with position concentration: ``position_pct > 10``
    → ``"1/2"``, ``> 5`` → ``"1/3"``, else ``"none"``.

    Args:
        holding: Dict with ``target_price`` and ``position_pct`` keys.
        current_price: Current stock price.

    Returns:
        Dict with keys:
          - ``"triggered"``: bool
          - ``"signal_type"``: ``"target_reached"``
          - ``"details"``: ``{"target": float, "current": float,
            "suggested_trim": "1/3" | "1/2" | "none"}``
    """
    raise NotImplementedError


def check_thesis_broken(holding: dict, current_davis_score: dict) -> dict:
    """Check if investment thesis is broken (relative percentile scoring).

    Uses RELATIVE percentile rank comparison, not absolute score.
    Trigger: percentile rank decline > 20 positions.

    Args:
        holding: Dict possibly containing ``thesis_snapshot_json``.
        current_davis_score: Current Davis Double score dict with
            percentile rank info.

    Returns (with snapshot):
        ``{"triggered": bool, "signal_type": "thesis_broken",
        "details": {"buy_percentile": float, "current_percentile":
        float, "decline": float}}``

    Returns (SKIP, no snapshot):
        ``{"triggered": False, "signal_type": "thesis_broken",
        "details": {"status": "SKIP", "reason": "no_snapshot"}}``
    """
    raise NotImplementedError
