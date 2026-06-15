"""Rate-limited Tushare Pro API client with structured SQLite cache and retry logic.

Cache schema (3 typed tables) replaces the former single-table ``api_cache``:

* ``stock_basic_cache``  — full stock list, refreshed on a 7-day TTL.
* ``daily_basic_cache``  — daily PE/PB/PS/market-cap, refreshed daily with
  incremental fetch (only new trade dates are pulled).
* ``financial_cache``    — quarterly reports (income/balancesheet/cashflow/
  fina_indicator), stored permanently per ``(ts_code, end_date, endpoint)`` and
  fetched incrementally as new report periods become available.

The legacy ``api_cache`` table is left intact so that ``migrate_cache`` can port
its rows into the new tables.
"""

import json
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import tushare as ts
from loguru import logger

from davis_analyzer.config import CACHE_DIR, get_tushare_token
from davis_analyzer.constants import TUSHARE_RATE_LIMIT

# Cache DB lives inside the cache directory
_CACHE_DB = CACHE_DIR / "tushare_cache.db"

# Per-table TTL (seconds). Financial data is quarterly and immutable once
# published, so it is cached permanently (no expiry).
_TTL_STOCK_BASIC = 7 * 24 * 3600
_TTL_DAILY_BASIC = 24 * 3600


def _init_cache_db(db_path: Path) -> None:
    """Create the structured cache tables (and keep the legacy table intact)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        # Legacy table — retained for migration. Never written by this client now.
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_basic_cache (
                ts_code     TEXT PRIMARY KEY,
                name        TEXT,
                industry    TEXT,
                list_status TEXT,
                fetched_at  REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_basic_cache (
                ts_code    TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                pe_ttm     REAL,
                pb         REAL,
                ps         REAL,
                total_mv   REAL,
                fetched_at REAL NOT NULL,
                PRIMARY KEY (ts_code, trade_date)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS financial_cache (
                ts_code    TEXT NOT NULL,
                end_date   TEXT NOT NULL,
                endpoint   TEXT NOT NULL,
                payload    TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                PRIMARY KEY (ts_code, end_date, endpoint)
            )
            """
        )
        conn.commit()


def _next_date_str(date_str: str) -> str:
    """Return the calendar day after ``date_str`` (``YYYYMMDD``)."""
    d = datetime.strptime(date_str, "%Y%m%d").date() + timedelta(days=1)
    return d.strftime("%Y%m%d")


class TushareClient:
    """Wraps Tushare Pro API with rate limiting, retry, and structured SQLite cache."""

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

    # ── core request wrapper (rate-limit + retry, no caching) ──

    def _call(self, endpoint: str, api_fn, params: dict) -> pd.DataFrame:
        """Execute an API call with rate limiting and retry (no caching).

        Caching is handled per public method so each endpoint can apply the
        correct TTL / incremental-fetch strategy.
        """
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
        """Return the A-share stock list with 7-day TTL caching."""
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            row = conn.execute(
                "SELECT COUNT(*), MAX(fetched_at) FROM stock_basic_cache"
            ).fetchone()

        count = row[0] if row else 0
        latest = row[1] if row else None
        now = time.time()
        if count and latest is not None and (now - latest) < _TTL_STOCK_BASIC:
            logger.debug("stock_basic cache fresh ({} rows)", count)
            return self._stock_basic_from_cache()

        df = self._call(
            "stock_basic",
            self._pro.stock_basic,
            {
                "exchange": "",
                "list_status": "L",
                "fields": "ts_code,name,industry,list_status",
            },
        )
        if not df.empty:
            self._stock_basic_replace(df)
        return self._stock_basic_from_cache()

    def get_daily_basic(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Return daily valuation data for ``ts_code`` with incremental fetch.

        Only trade dates newer than the most recent cached date are requested
        from the API; the full requested range is then served from cache.
        """
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            row = conn.execute(
                "SELECT MAX(trade_date), MAX(fetched_at) FROM daily_basic_cache WHERE ts_code=?",
                (ts_code,),
            ).fetchone()

        max_date = row[0] if row else None
        latest_fetched = row[1] if row else None
        fetched_today = (
            latest_fetched is not None
            and datetime.fromtimestamp(latest_fetched).date() == date.today()
        )

        # Already have every requested trade date (historical data is immutable).
        if max_date is not None and max_date >= end_date:
            return self._daily_basic_from_cache(ts_code, start_date, end_date)
        # Already queried today — any gap is just non-trading days, no new data.
        if fetched_today:
            return self._daily_basic_from_cache(ts_code, start_date, end_date)

        # Incremental fetch: only dates after the newest cached trade date.
        fetch_start = _next_date_str(max_date) if max_date else start_date
        if fetch_start < start_date:
            fetch_start = start_date
        if fetch_start <= end_date:
            logger.info(
                "Incremental daily_basic: {} [{} → {}]", ts_code, fetch_start, end_date
            )
            df = self._call(
                "daily_basic",
                self._pro.daily_basic,
                {
                    "ts_code": ts_code,
                    "start_date": fetch_start,
                    "end_date": end_date,
                    "fields": "ts_code,trade_date,pe_ttm,pb,ps,total_mv",
                },
            )
            self._daily_basic_insert(ts_code, df)

        return self._daily_basic_from_cache(ts_code, start_date, end_date)

    def get_income(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._get_financial(
            "income",
            self._pro.income,
            "ts_code,end_date,total_revenue,n_income,n_income_attr_p",
            ts_code,
            start_date,
            end_date,
        )

    def get_balancesheet(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._get_financial(
            "balancesheet",
            self._pro.balancesheet,
            "ts_code,end_date,total_assets,total_liab",
            ts_code,
            start_date,
            end_date,
        )

    def get_cashflow(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._get_financial(
            "cashflow",
            self._pro.cashflow,
            "ts_code,end_date,n_cashflow_act",
            ts_code,
            start_date,
            end_date,
        )

    def get_fina_indicator(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._get_financial(
            "fina_indicator",
            self._pro.fina_indicator,
            "ts_code,end_date,roe,eps,dt_eps,revenue_ps",
            ts_code,
            start_date,
            end_date,
        )

    # ── structured-cache read/write helpers ──

    @staticmethod
    def _stock_basic_from_cache() -> pd.DataFrame:
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            rows = conn.execute(
                "SELECT ts_code, name, industry, list_status FROM stock_basic_cache"
            ).fetchall()
        return pd.DataFrame(rows, columns=["ts_code", "name", "industry", "list_status"])

    def _stock_basic_replace(self, df: pd.DataFrame) -> None:
        now = time.time()
        records = [
            (r["ts_code"], r["name"], r["industry"], r.get("list_status", "L"), now)
            for r in df.to_dict("records")
        ]
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            conn.execute("DELETE FROM stock_basic_cache")
            conn.executemany(
                """
                INSERT OR REPLACE INTO stock_basic_cache
                    (ts_code, name, industry, list_status, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                records,
            )
            conn.commit()

    @staticmethod
    def _daily_basic_from_cache(ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            rows = conn.execute(
                """
                SELECT ts_code, trade_date, pe_ttm, pb, ps, total_mv
                FROM daily_basic_cache
                WHERE ts_code = ? AND trade_date >= ? AND trade_date <= ?
                ORDER BY trade_date DESC
                """,
                (ts_code, start_date, end_date),
            ).fetchall()
        return pd.DataFrame(
            rows, columns=["ts_code", "trade_date", "pe_ttm", "pb", "ps", "total_mv"]
        )

    @staticmethod
    def _daily_basic_insert(ts_code: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        now = time.time()
        records = []
        for r in df.to_dict("records"):
            records.append(
                (
                    r.get("ts_code", ts_code),
                    str(r.get("trade_date", "")),
                    r.get("pe_ttm"),
                    r.get("pb"),
                    r.get("ps"),
                    r.get("total_mv"),
                    now,
                )
            )
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO daily_basic_cache
                    (ts_code, trade_date, pe_ttm, pb, ps, total_mv, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )
            conn.commit()

    def _get_financial(
        self,
        endpoint: str,
        api_fn,
        fields: str,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Fetch quarterly financial data with incremental (per-report-period) fetch."""
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            row = conn.execute(
                "SELECT MAX(end_date), MAX(fetched_at) FROM financial_cache "
                "WHERE ts_code=? AND endpoint=?",
                (ts_code, endpoint),
            ).fetchone()

        max_end = row[0] if row else None
        latest_fetched = row[1] if row else None
        fetched_today = (
            latest_fetched is not None
            and datetime.fromtimestamp(latest_fetched).date() == date.today()
        )

        # Already have every report period through end_date — data is permanent.
        if max_end is not None and max_end >= end_date:
            return self._financial_from_cache(endpoint, ts_code, start_date, end_date)
        # Already checked today — no new quarterly report appears intraday.
        if fetched_today:
            return self._financial_from_cache(endpoint, ts_code, start_date, end_date)

        # Fetch only report periods newer than the newest cached end_date.
        fetch_start = _next_date_str(max_end) if max_end else start_date
        if fetch_start < start_date:
            fetch_start = start_date
        if fetch_start <= end_date:
            logger.info(
                "Incremental {}: {} [{} → {}]", endpoint, ts_code, fetch_start, end_date
            )
            df = self._call(
                endpoint,
                api_fn,
                {
                    "ts_code": ts_code,
                    "start_date": fetch_start,
                    "end_date": end_date,
                    "fields": fields,
                },
            )
            self._financial_insert(endpoint, ts_code, df)

        return self._financial_from_cache(endpoint, ts_code, start_date, end_date)

    @staticmethod
    def _financial_insert(endpoint: str, ts_code: str, df: pd.DataFrame) -> None:
        """Insert financial rows, one JSON payload per (ts_code, end_date)."""
        if df is None or df.empty:
            return
        now = time.time()
        # Match downstream dedup semantics (one row per ts_code+end_date).
        deduped = df.drop_duplicates(subset=["ts_code", "end_date"], keep="first")
        records = []
        for r in deduped.to_dict("records"):
            records.append(
                (
                    r.get("ts_code", ts_code),
                    str(r.get("end_date", "")),
                    endpoint,
                    json.dumps(r, ensure_ascii=False),
                    now,
                )
            )
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO financial_cache
                    (ts_code, end_date, endpoint, payload, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                records,
            )
            conn.commit()

    @staticmethod
    def _financial_from_cache(
        endpoint: str, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            rows = conn.execute(
                """
                SELECT payload
                FROM financial_cache
                WHERE ts_code = ? AND endpoint = ? AND end_date >= ? AND end_date <= ?
                ORDER BY end_date DESC
                """,
                (ts_code, endpoint, start_date, end_date),
            ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([json.loads(r[0]) for r in rows])
