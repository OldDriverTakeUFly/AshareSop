"""Trading calendar utilities using akshare trade date history."""

import os
from datetime import datetime
from typing import Optional

_trade_dates: Optional[set[str]] = None


def _load_trade_dates() -> set[str]:
    """Load trade dates from akshare (Sina source), stripping proxy env vars."""
    global _trade_dates
    if _trade_dates is not None:
        return _trade_dates

    removed: dict[str, str] = {}
    proxy_keys = [
        "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
        "ALL_PROXY", "all_proxy",
    ]
    for key in proxy_keys:
        if key in os.environ:
            removed[key] = os.environ.pop(key)

    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        _trade_dates = set(str(d) for d in df["trade_date"])
    finally:
        os.environ.update(removed)

    return _trade_dates


def is_trading_day(date_str: str) -> bool:
    """Check if a given date is a trading day.

    Args:
        date_str: Date in 'YYYY-MM-DD' format.

    Returns:
        True if the date is a trading day, False otherwise.
    """
    dates = _load_trade_dates()
    return date_str in dates


def get_recent_trade_day(date_str: Optional[str] = None) -> str:
    """Get the most recent trading day on or before the given date.

    Args:
        date_str: Date in 'YYYY-MM-DD' format. Defaults to today.

    Returns:
        The most recent trading day in 'YYYY-MM-DD' format.
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    dates = _load_trade_dates()
    sorted_dates = sorted(dates, reverse=True)
    for d in sorted_dates:
        if d <= date_str:
            return d
    raise ValueError(f"No trading day found on or before {date_str}")
