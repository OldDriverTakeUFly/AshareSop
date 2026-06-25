"""Rate limiter for AkShare API calls."""

import os
import time
import threading
import pandas as pd
from stockhot.core.logging import logger

# Network/transport exceptions that warrant a retry.
# AkShare's data sources (东方财富/新浪/同花顺) are domestic Chinese sites;
# a local HTTP proxy can intermittently drop these connections.
import urllib.error
from urllib3.exceptions import ProxyError as Urllib3ProxyError

try:  # requests is an akshare dependency, available at runtime
    import requests
    from requests.exceptions import (
        ConnectionError as RequestsConnectionError,
        ProxyError as RequestsProxyError,
        Timeout as RequestsTimeout,
    )

    _RETRYABLE_EXC: tuple = (
        RequestsProxyError,
        RequestsConnectionError,
        RequestsTimeout,
        Urllib3ProxyError,
        urllib.error.URLError,
        ConnectionError,
    )
except ImportError:  # pragma: no cover - requests missing
    requests = None
    _RETRYABLE_EXC = (Urllib3ProxyError, urllib.error.URLError, ConnectionError)


# Maximum retry attempts for transient network errors.
_MAX_RETRIES = 2
# Base backoff (seconds); actual wait = base * attempt (1s, 2s, ...).
_RETRY_BASE_DELAY = 1.0

# Proxy env var keys that may be set system-wide. When retrying domestic
# data sources, we temporarily clear these so requests/urllib connect
# directly instead of via a (possibly flaky) local proxy.
_PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
    "ALL_PROXY", "all_proxy",
)


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


def _call_without_proxy(fn, *args, **kwargs):
    """Invoke ``fn`` with proxy env vars temporarily removed.

    AkShare data sources are domestic Chinese sites; routing them through a
    local proxy (e.g. 127.0.0.1:7897) can cause intermittent
    ``ProxyError``/``RemoteDisconnected`` failures. This helper clears the
    proxy environment for the duration of the call so the connection goes
    direct, then restores the original values.
    """
    saved = {}
    for key in _PROXY_ENV_KEYS:
        if key in os.environ:
            saved[key] = os.environ.pop(key)
    try:
        return fn(*args, **kwargs)
    finally:
        os.environ.update(saved)


def safe_akshare_call(fn, *args, **kwargs) -> pd.DataFrame:
    """Call an AkShare function with rate limiting, retries, and empty-check.

    Behaviour:
    - First attempt uses whatever proxy is configured in the environment.
    - If the call raises a transient network error (ProxyError,
      ConnectionError, Timeout, URLError), it retries up to
      ``_MAX_RETRIES`` times with the proxy temporarily removed (direct
      connection), since AkShare sources are domestic sites.
    - On a non-retryable error or after exhausting retries, returns an
      empty DataFrame (never raises).
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):  # 1 initial + _MAX_RETRIES
        try:
            if attempt == 0:
                akshare_limiter.acquire()
                df = fn(*args, **kwargs)
            else:
                # Retry: clear proxy, wait with linear backoff, re-acquire.
                delay = _RETRY_BASE_DELAY * attempt
                logger.info(
                    f"AkShare retry {attempt}/{_MAX_RETRIES} for "
                    f"{getattr(fn, '__name__', fn)} after {delay:.0f}s "
                    f"(direct, no proxy)"
                )
                time.sleep(delay)
                akshare_limiter.acquire()
                df = _call_without_proxy(fn, *args, **kwargs)
            if df is None or (hasattr(df, "empty") and df.empty):
                return pd.DataFrame()
            return df
        except _RETRYABLE_EXC as e:
            last_exc = e
            # Retryable: loop to next attempt.
            continue
        except Exception as e:
            # Non-retryable: log and give up immediately.
            logger.warning(f"AkShare call failed: {e}")
            return pd.DataFrame()

    # Exhausted retries.
    logger.warning(
        f"AkShare call failed after {_MAX_RETRIES} retries: {last_exc}"
    )
    return pd.DataFrame()
