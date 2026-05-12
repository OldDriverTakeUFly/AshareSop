"""Rate limiter for AkShare API calls."""

import time
import threading
import pandas as pd
from stockhot.core.logging import logger


class RateLimiter:
    """Thread-safe rate limiter for AkShare API calls."""

    def __init__(self, calls_per_second: float = 1.5):
        self._min_interval = 1.0 / calls_per_second
        self._last_call = 0.0
        self._lock = threading.Lock()

    def acquire(self):
        """Block until an API call slot is available."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        pass


# Module-level singleton — 1.5 req/sec default (AkShare safe limit)
akshare_limiter = RateLimiter(1.5)


def safe_akshare_call(fn, *args, **kwargs) -> pd.DataFrame:
    """Call an AkShare function with rate limiting and empty-check.

    Returns empty DataFrame on error or empty result.
    """
    try:
        akshare_limiter.acquire()
        df = fn(*args, **kwargs)
        if df is None or (hasattr(df, 'empty') and df.empty):
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.warning(f"AkShare call failed: {e}")
        return pd.DataFrame()
