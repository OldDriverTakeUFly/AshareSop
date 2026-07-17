"""Trading calendar utilities using akshare trade date history."""

from datetime import datetime
from typing import Optional

from stockhot.core.logging import logger
from stockhot.core.rate_limiter import safe_akshare_call

_trade_dates: Optional[set[str]] = None


def _load_trade_dates() -> set[str]:
    """Load trade dates from akshare (Sina source).

    Uses ``safe_akshare_call`` for rate limiting + retry + proxy handling.
    On failure, caches an empty set so the process does not retry on every
    call (the calendar rarely changes).
    """
    global _trade_dates
    if _trade_dates is not None:
        return _trade_dates

    import akshare as ak

    df = safe_akshare_call(ak.tool_trade_date_hist_sina)
    if df.empty:
        logger.warning(
            "[trading_calendar] 交易日历加载失败（AKShare 数据源不可用），"
            "本次进程内 is_trading_day 将始终返回 False"
        )
        _trade_dates = set()
        return _trade_dates

    _trade_dates = set(str(d) for d in df["trade_date"])
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
