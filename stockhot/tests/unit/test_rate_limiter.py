import time
import pandas as pd
from stockhot.core.rate_limiter import RateLimiter, safe_akshare_call


def test_rate_limiter_respects_interval():
    limiter = RateLimiter(100)  # 100/sec = 10ms interval
    start = time.monotonic()
    limiter.acquire()
    limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.01  # At least one interval waited


def test_safe_akshare_call_returns_empty_on_exception():
    def failing_fn():
        raise ValueError("test error")

    result = safe_akshare_call(failing_fn)
    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_safe_akshare_call_returns_empty_on_none():
    result = safe_akshare_call(lambda: None)
    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_safe_akshare_call_returns_df_on_success():
    df = pd.DataFrame({"a": [1, 2, 3]})
    result = safe_akshare_call(lambda: df)
    assert len(result) == 3
