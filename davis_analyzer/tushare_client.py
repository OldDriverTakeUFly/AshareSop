"""Rate-limited Tushare Pro API client with SQLite cache and retry logic."""

import hashlib
import io
import json
import sqlite3
import time
from pathlib import Path

import pandas as pd
import tushare as ts
from loguru import logger

from davis_analyzer.config import CACHE_DIR, get_tushare_token
from davis_analyzer.constants import TUSHARE_RATE_LIMIT

# Cache DB lives inside the cache directory
_CACHE_DB = CACHE_DIR / "tushare_cache.db"


def _params_hash(params: dict) -> str:
    encoded = json.dumps(params, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _init_cache_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_cache (
                endpoint  TEXT NOT NULL,
                params_hash TEXT NOT NULL,
                response   TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                PRIMARY KEY (endpoint, params_hash)
            )
            """
        )
        conn.commit()


class TushareClient:
    """Wraps Tushare Pro API with rate limiting, retry, and SQLite cache."""

    _MAX_RETRIES: int = 3
    _BACKOFF_BASE: float = 1.0

    def __init__(self) -> None:
        token = get_tushare_token()
        self._pro = ts.pro_api(token)
        self._request_timestamps: list[float] = []
        self._rate_limit = TUSHARE_RATE_LIMIT
        _init_cache_db(_CACHE_DB)
        logger.info("TushareClient initialised (rate_limit={}/min)", self._rate_limit)

    # ── rate limiter ──

    def _wait_for_rate_limit(self) -> None:
        """Block until the request rate is within bounds."""
        now = time.time()
        window = 60.0
        self._request_timestamps = [
            t for t in self._request_timestamps if now - t < window
        ]
        if len(self._request_timestamps) >= self._rate_limit:
            oldest = self._request_timestamps[0]
            sleep_time = oldest + window - now + 0.1
            if sleep_time > 0:
                logger.warning("Rate limit reached — sleeping {:.1f}s", sleep_time)
                time.sleep(sleep_time)
        self._request_timestamps.append(time.time())

    # ── cache ──

    def _cache_get(self, endpoint: str, params: dict) -> pd.DataFrame | None:
        ph = _params_hash(params)
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            row = conn.execute(
                "SELECT response FROM api_cache WHERE endpoint=? AND params_hash=?",
                (endpoint, ph),
            ).fetchone()
        if row is None:
            return None
        logger.debug("Cache HIT: endpoint={}, hash={}", endpoint, ph[:8])
        return pd.read_json(io.StringIO(row[0]), orient="records")

    def _cache_put(self, endpoint: str, params: dict, df: pd.DataFrame) -> None:
        if df.empty:
            return
        ph = _params_hash(params)
        payload = df.to_json(orient="records", force_ascii=False)
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO api_cache (endpoint, params_hash, response, fetched_at)
                VALUES (?, ?, ?, ?)
                """,
                (endpoint, ph, payload, time.time()),
            )
            conn.commit()
        logger.debug("Cache SET: endpoint={}, hash={}", endpoint, ph[:8])

    # ── core request wrapper ──

    def _call(self, endpoint: str, api_fn, params: dict) -> pd.DataFrame:
        """Execute an API call with cache → rate-limit → retry."""
        cached = self._cache_get(endpoint, params)
        if cached is not None:
            return cached

        last_exc: Exception | None = None
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                self._wait_for_rate_limit()
                logger.info(
                    "API call: endpoint={}, attempt={}/{}", endpoint, attempt, self._MAX_RETRIES
                )
                df: pd.DataFrame = api_fn(**params)
                if df is None:
                    df = pd.DataFrame()
                self._cache_put(endpoint, params, df)
                return df
            except Exception as exc:
                last_exc = exc
                backoff = self._BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "API error on attempt {}/{} for '{}': {} — retrying in {:.1f}s",
                    attempt,
                    self._MAX_RETRIES,
                    endpoint,
                    exc,
                    backoff,
                )
                if attempt < self._MAX_RETRIES:
                    time.sleep(backoff)

        logger.error("API call failed after {} retries: endpoint={}", self._MAX_RETRIES, endpoint)
        raise last_exc  # type: ignore[misc]

    # ── public API ──

    def get_stock_list(self) -> pd.DataFrame:
        return self._call(
            "stock_basic",
            self._pro.stock_basic,
            {
                "exchange": "",
                "list_status": "L",
                "fields": "ts_code,name,industry,list_status",
            },
        )

    def get_daily_basic(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._call(
            "daily_basic",
            self._pro.daily_basic,
            {
                "ts_code": ts_code,
                "start_date": start_date,
                "end_date": end_date,
                "fields": "ts_code,trade_date,pe_ttm,pb,ps,total_mv",
            },
        )

    def get_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._call(
            "daily",
            self._pro.daily,
            {
                "ts_code": ts_code,
                "start_date": start_date,
                "end_date": end_date,
            },
        )

    def get_income(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._call(
            "income",
            self._pro.income,
            {
                "ts_code": ts_code,
                "start_date": start_date,
                "end_date": end_date,
                "fields": "ts_code,end_date,total_revenue,n_income,n_income_attr_p",
            },
        )

    def get_balancesheet(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._call(
            "balancesheet",
            self._pro.balancesheet,
            {
                "ts_code": ts_code,
                "start_date": start_date,
                "end_date": end_date,
                "fields": "ts_code,end_date,total_assets,total_liab",
            },
        )

    def get_cashflow(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._call(
            "cashflow",
            self._pro.cashflow,
            {
                "ts_code": ts_code,
                "start_date": start_date,
                "end_date": end_date,
                "fields": "ts_code,end_date,n_cashflow_act",
            },
        )

    def get_fina_indicator(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._call(
            "fina_indicator",
            self._pro.fina_indicator,
            {
                "ts_code": ts_code,
                "start_date": start_date,
                "end_date": end_date,
                "fields": "ts_code,end_date,roe,eps,dt_eps,revenue_ps",
            },
        )
