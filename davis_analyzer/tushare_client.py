"""Rate-limited Tushare Pro API client with structured SQLite cache and retry logic.

Cache schema (3 typed tables) replaces the former single-table ``api_cache``:

* ``stock_basic``  — full stock list, refreshed on a 7-day TTL.
* ``daily_basic``  — daily PE/PB/PS/market-cap, refreshed daily with
  incremental fetch (only new trade dates are pulled).
* ``financial``    — quarterly reports (income/balancesheet/cashflow/
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

# 统一市场库：davis 现在读写 storage/database/market_data.db（与 stockhot 共享）。
# 旧的 davis_analyzer/cache/tushare_cache.db 数据已迁移到 market_data.db，
# 保留 _CACHE_DB 名字以兼容 backtest.py 的 `from ... import _CACHE_DB`。
# 表名去掉 _cache 后缀，对齐 DAL schema（stock_basic → stock_basic 等）。
from stockhot.data_layer.market_db import MARKET_DB_PATH as _CACHE_DB

# Track which (endpoint, ts_code) combos were already checked today.
# forecast (业绩预告) is sparse — most quarters have no announcement, so the
# API returns empty and nothing gets cached. Without this set, the backtest
# would re-fetch 200 stocks × every day = 270k futile API calls (8.6h waste).
# Entries are (endpoint, ts_code, "YYYY-MM-DD"); cleared on new day implicitly
# because the date is part of the key.
_forecast_checked_today: set[tuple[str, str, str]] = set()

# Per-table TTL (seconds). Financial data is quarterly and immutable once
# published, so it is cached permanently (no expiry). Dividend history is
# slow-moving (annual payouts) and the endpoint ignores date filters, so we
# refresh the full history on a 7-day cycle like stock_basic.
_TTL_STOCK_BASIC = 7 * 24 * 3600
_TTL_DAILY_BASIC = 24 * 3600
_TTL_DIVIDEND = 7 * 24 * 3600


def _init_cache_db(db_path: Path) -> None:
    """Create the structured cache tables.

    现在委托给 DAL 的 ``init_db``（schema 单点维护），避免 davis 与 DAL
    各自建表导致列定义冲突。DAL 的 schema 是超集（如 daily_price 12 列），
    ``CREATE TABLE IF NOT EXISTS`` 保证已存在的表不受影响。
    """
    from stockhot.data_layer.market_db import init_db
    init_db(db_path)
    # 保留向后兼容：旓名字迁移工具仍可读 api_cache（如存在）
    db_path.parent.mkdir(parents=True, exist_ok=True)


def _next_date_str(date_str: str) -> str:
    """Return the calendar day after ``date_str`` (``YYYYMMDD``)."""
    d = datetime.strptime(date_str, "%Y%m%d").date() + timedelta(days=1)
    return d.strftime("%Y%m%d")


def _dedupe_financial_rows(df: pd.DataFrame, endpoint: str) -> pd.DataFrame:
    """Collapse to one row per (ts_code, end_date), endpoint-aware.

    Most financial endpoints already return one row per (ts_code, end_date).
    The dividend endpoint returns a lifecycle (预案 / 股东大会通过 / 实施) per
    period — we keep the 实施 (executed) row when present, else the last row,
    so the cached dividend cash_div reflects the actual payout rather than a
    zero-value plan.
    """
    if df is None or df.empty or "end_date" not in df.columns:
        return df
    if endpoint == "dividend" and "div_proc" in df.columns:
        # Sort so 实施 sorts last within each group, then keep last.
        proc_rank = {"预案": 0, "董事会预案": 0, "股东大会通过": 1, "实施": 2, "不分配": 1}
        df = df.copy()
        df["_rank"] = df["div_proc"].fillna("").map(lambda p: proc_rank.get(p, 1))
        df = df.sort_values(["ts_code", "end_date", "_rank"])
        deduped = df.drop_duplicates(subset=["ts_code", "end_date"], keep="last")
        return deduped.drop(columns=["_rank"])
    return df.drop_duplicates(subset=["ts_code", "end_date"], keep="first")


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
        # Persistent read connection for cache lookups.
        # Opening a new sqlite3.connect() per call costs ~0.1ms each, which
        # adds up to ~6h over a 1351-day × 200-stock backtest (1400 calls/day).
        # Reusing one connection cuts this to negligible. check_same_thread=False
        # is safe because we only read from this connection (writes go through
        # separate short-lived connections in _financial_insert).
        self._cache_conn = sqlite3.connect(str(_CACHE_DB), check_same_thread=False)
        self._cache_conn.execute("PRAGMA journal_mode=WAL")
        self._cache_conn.execute("PRAGMA query_only=1")
        logger.info("TushareClient initialised (rate_limit={}/min)", self._rate_limit)

    # ── rate limiter ──

    def _wait_for_rate_limit(self) -> None:
        """Block until the request rate is within bounds."""
        now = time.time()
        window = 60.0
        self._request_timestamps = [t for t in self._request_timestamps if now - t < window]
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
        """Return the A-share stock list with 7-day TTL caching.

        Includes listed (L), delisted (D), and suspended (P) stocks so the
        backtest universe correctly contains delisted names (avoids
        survivorship bias). Downstream callers that only want active stocks
        can filter ``df[df.list_status == "L"]``.
        """
        row = self._cache_conn.execute("SELECT COUNT(*), MAX(fetched_at) FROM stock_basic").fetchone()

        count = row[0] if row else 0
        latest = row[1] if row else None
        now = time.time()
        if count and latest is not None and (now - latest) < _TTL_STOCK_BASIC:
            logger.debug("stock_basic cache fresh ({} rows)", count)
            return self._stock_basic_from_cache()

        # Fetch all three list_status buckets and concatenate.
        parts: list[pd.DataFrame] = []
        for status in ("L", "D", "P"):
            df_part = self._call(
                "stock_basic",
                self._pro.stock_basic,
                {
                    "exchange": "",
                    "list_status": status,
                    "fields": "ts_code,name,industry,list_status,list_date",
                },
            )
            if df_part is not None and not df_part.empty:
                parts.append(df_part)
        df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        if not df.empty:
            self._stock_basic_replace(df)
        return self._stock_basic_from_cache()

    def get_daily_basic(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Return daily valuation data for ``ts_code`` with incremental fetch.

        Only trade dates newer than the most recent cached date are requested
        from the API; the full requested range is then served from cache.
        """
        row = self._cache_conn.execute(
            "SELECT MAX(trade_date), MAX(fetched_at) FROM daily_basic WHERE ts_code=?",
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
            logger.info("Incremental daily_basic: {} [{} → {}]", ts_code, fetch_start, end_date)
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
            "ts_code,end_date,ann_date,total_revenue,n_income,n_income_attr_p",
            ts_code,
            start_date,
            end_date,
        )

    def get_balancesheet(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._get_financial(
            "balancesheet",
            self._pro.balancesheet,
            "ts_code,end_date,ann_date,total_assets,total_liab,contract_liab",
            ts_code,
            start_date,
            end_date,
        )

    def get_cashflow(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._get_financial(
            "cashflow",
            self._pro.cashflow,
            "ts_code,end_date,ann_date,n_cashflow_act,c_pay_acq_const_fiolta",
            ts_code,
            start_date,
            end_date,
        )

    def get_fina_indicator(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._get_financial(
            "fina_indicator",
            self._pro.fina_indicator,
            "ts_code,end_date,ann_date,roe,eps,dt_eps,revenue_ps,grossprofit_margin,rd_exp",
            ts_code,
            start_date,
            end_date,
        )

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Return OHLC (open + close) + adj_factor for ``ts_code`` with incremental fetch.

        Merges ``daily`` (unadjusted open/close) with ``adj_factor`` so callers can
        compute ``adj_close = close * adj_factor`` — the only correct way to
        derive returns across ex-dividend days (naïve pct_chg compounding is
        biased on ex-div days). Cached in ``daily_price``.

        Returns columns: ts_code, trade_date, open, close, adj_factor.

        Note: rows cached before the ``open`` column was added have
        ``open=None``; they are back-filled on the next incremental refresh.
        """
        row = self._cache_conn.execute(
            "SELECT MAX(trade_date), MAX(fetched_at) FROM daily_price WHERE ts_code=?",
            (ts_code,),
        ).fetchone()

        max_date = row[0] if row else None
        latest_fetched = row[1] if row else None
        fetched_today = (
            latest_fetched is not None
            and datetime.fromtimestamp(latest_fetched).date() == date.today()
        )

        if max_date is not None and max_date >= end_date:
            return self._daily_prices_from_cache(ts_code, start_date, end_date)
        if fetched_today:
            return self._daily_prices_from_cache(ts_code, start_date, end_date)

        fetch_start = _next_date_str(max_date) if max_date else start_date
        if fetch_start < start_date:
            fetch_start = start_date
        if fetch_start <= end_date:
            logger.info("Incremental daily_price: {} [{} → {}]", ts_code, fetch_start, end_date)
            daily_df = self._call(
                "daily",
                self._pro.daily,
                {
                    "ts_code": ts_code,
                    "start_date": fetch_start,
                    "end_date": end_date,
                    "fields": "ts_code,trade_date,open,close",
                },
            )
            adj_df = self._call(
                "adj_factor",
                self._pro.adj_factor,
                {
                    "ts_code": ts_code,
                    "start_date": fetch_start,
                    "end_date": end_date,
                    "fields": "ts_code,trade_date,adj_factor",
                },
            )
            self._daily_prices_insert(ts_code, daily_df, adj_df)

        return self._daily_prices_from_cache(ts_code, start_date, end_date)

    def get_daily_basic_by_date(
        self,
        trade_date: str,
        fields: str = (
            "ts_code,trade_date,pe_ttm,pb,ps,total_mv,"
            "turnover_rate,circ_mv,free_share"
        ),
    ) -> pd.DataFrame:
        """全市场某日 daily_basic 快照(按日直拉, 无按票缓存).

        供日度采集场景(daily_scan Wave 0); 走 _call 获得限流+重试。
        取代调用方直接伸手私有属性 ``client._pro.daily_basic`` 的写法
        (2026-08-25 收敛).
        """
        return self._call(
            "daily_basic",
            self._pro.daily_basic,
            {"trade_date": trade_date, "fields": fields},
        )

    def get_dividend(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Return 分红送股 (dividend) rows for the requested date range.

        Cached per ``(ts_code, end_date)``. NOTE: Tushare's dividend endpoint
        silently returns EMPTY results when ``start_date``/``end_date`` are
        passed (a documented quirk — it only honours ``ts_code``). So we fetch
        the full history once per stock on a 7-day refresh cycle, then filter
        to the requested range locally.

        Fields: ts_code, end_date, ann_date, div_proc, cash_div, stk_div,
        ex_date. The cached row per end_date is the 实施 (executed) payout
        when available (see _dedupe_financial_rows).
        """
        # Refresh once per 7 days; dividend history is slow-moving.
        row = self._cache_conn.execute(
            "SELECT MAX(fetched_at) FROM financial WHERE ts_code=? AND endpoint='dividend'",
            (ts_code,),
        ).fetchone()
        latest_fetched = row[0] if row else None
        now = time.time()
        fresh = (
            latest_fetched is not None
            and (now - latest_fetched) < _TTL_DIVIDEND
        )

        if not fresh:
            # Full-history fetch (NO date params — the endpoint ignores them).
            logger.info("Refreshing dividend history for {}", ts_code)
            df = self._call(
                "dividend",
                self._pro.dividend,
                {
                    "ts_code": ts_code,
                    "fields": "ts_code,end_date,ann_date,div_proc,cash_div,stk_div,ex_date",
                },
            )
            self._financial_insert("dividend", ts_code, df)

        # Serve from cache, filtered to the requested range locally.
        df = self._financial_from_cache("dividend", ts_code, start_date, end_date)
        return df

    def get_forecast(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Return 业绩预告 (earnings pre-announcement) rows.

        Cached per ``(ts_code, end_date)`` via the same incremental path as
        financial data. Fields: ts_code, ann_date, end_date, type,
        p_change_min, p_change_max.
        """
        return self._get_financial(
            "forecast",
            self._pro.forecast,
            "ts_code,ann_date,end_date,type,p_change_min,p_change_max",
            ts_code,
            start_date,
            end_date,
        )

    def get_stk_holdernumber(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Return 股东户数 (shareholder count) rows for chip-concentration.

        Cached per ``(ts_code, end_date)``. Fields: ts_code, ann_date,
        end_date, holder_num.
        """
        return self._get_financial(
            "stk_holdernumber",
            self._pro.stk_holdernumber,
            "ts_code,ann_date,end_date,holder_num",
            ts_code,
            start_date,
            end_date,
        )

    def get_hk_hold(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Return northbound (HK Stock Connect) holding data for ``ts_code``.

        Uses Tushare ``hk_hold`` — returns vol (shares held), ratio (% of float),
        and amount.  Cached per (ts_code, trade_date) in ``hk_hold``.

        Fields: ts_code, trade_date, name, vol, ratio.
        """
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hk_hold (
                    ts_code    TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    vol        REAL,
                    ratio      REAL,
                    fetched_at REAL NOT NULL,
                    PRIMARY KEY (ts_code, trade_date)
                )
            """)
            # Check cache coverage
            row = conn.execute(
                "SELECT MAX(trade_date), MAX(fetched_at) FROM hk_hold WHERE ts_code=?",
                (ts_code,),
            ).fetchone()
            max_date = row[0] if row else None
            latest_fetched = row[1] if row else None
            fetched_today = (
                latest_fetched is not None
                and datetime.fromtimestamp(latest_fetched).date() == date.today()
            )

            if max_date is not None and max_date >= end_date:
                rows = conn.execute(
                    """SELECT ts_code, trade_date, vol, ratio FROM hk_hold
                       WHERE ts_code=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date""",
                    (ts_code, start_date, end_date),
                ).fetchall()
                return pd.DataFrame(rows, columns=["ts_code", "trade_date", "vol", "ratio"])
            if fetched_today:
                rows = conn.execute(
                    """SELECT ts_code, trade_date, vol, ratio FROM hk_hold
                       WHERE ts_code=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date""",
                    (ts_code, start_date, end_date),
                ).fetchall()
                return pd.DataFrame(rows, columns=["ts_code", "trade_date", "vol", "ratio"])

        # Fetch from API
        fetch_start = _next_date_str(max_date) if max_date else start_date
        if fetch_start < start_date:
            fetch_start = start_date
        if fetch_start <= end_date:
            df = self._call(
                "hk_hold",
                self._pro.hk_hold,
                {
                    "ts_code": ts_code,
                    "start_date": fetch_start,
                    "end_date": end_date,
                    "fields": "ts_code,trade_date,vol,ratio",
                },
            )
            if df is not None and not df.empty:
                now = time.time()
                records = [
                    (r.get("ts_code", ts_code), str(r.get("trade_date", "")),
                     r.get("vol"), r.get("ratio"), now)
                    for r in df.to_dict("records")
                ]
                with sqlite3.connect(str(_CACHE_DB)) as conn:
                    conn.executemany(
                        """INSERT OR REPLACE INTO hk_hold
                           (ts_code, trade_date, vol, ratio, fetched_at) VALUES (?,?,?,?,?)""",
                        records,
                    )
                    conn.commit()

        with sqlite3.connect(str(_CACHE_DB)) as conn:
            rows = conn.execute(
                """SELECT ts_code, trade_date, vol, ratio FROM hk_hold
                   WHERE ts_code=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date""",
                (ts_code, start_date, end_date),
            ).fetchall()
        return pd.DataFrame(rows, columns=["ts_code", "trade_date", "vol", "ratio"])

    def get_research_reports(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Return analyst research reports for ``ts_code``.

        Uses Tushare ``report_rc`` — returns report_date, rating, target_price,
        org_name, author_name.  **Rate-limited to 10 calls/hour by Tushare.**

        Cached per (ts_code, report_date) in ``research``.
        """
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research (
                    ts_code      TEXT NOT NULL,
                    report_date  TEXT NOT NULL,
                    rating       TEXT,
                    target_price REAL,
                    org_name     TEXT,
                    fetched_at   REAL NOT NULL,
                    PRIMARY KEY (ts_code, report_date, org_name)
                )
            """)
            # Check if we already have data for this range
            row = conn.execute(
                "SELECT COUNT(*) FROM research WHERE ts_code=? AND report_date>=? AND report_date<=?",
                (ts_code, start_date, end_date),
            ).fetchone()
            cached_count = row[0] if row else 0

            if cached_count > 0:
                rows = conn.execute(
                    """SELECT ts_code, report_date, rating, target_price, org_name FROM research
                       WHERE ts_code=? AND report_date>=? AND report_date<=? ORDER BY report_date""",
                    (ts_code, start_date, end_date),
                ).fetchall()
                return pd.DataFrame(rows, columns=["ts_code", "report_date", "rating", "target_price", "org_name"])

        # Fetch from API (caller must respect 10/hour rate limit)
        df = self._call(
            "report_rc",
            self._pro.report_rc,
            {
                "ts_code": ts_code,
                "start_date": start_date,
                "end_date": end_date,
                "fields": "ts_code,report_date,rating,tp,org_name",
            },
        )
        if df is not None and not df.empty:
            now = time.time()
            records = [
                (r.get("ts_code", ts_code), str(r.get("report_date", "")),
                 r.get("rating"), r.get("tp"), r.get("org_name", ""), now)
                for r in df.to_dict("records")
            ]
            with sqlite3.connect(str(_CACHE_DB)) as conn:
                conn.executemany(
                    """INSERT OR REPLACE INTO research
                       (ts_code, report_date, rating, target_price, org_name, fetched_at) VALUES (?,?,?,?,?,?)""",
                    records,
                )
                conn.commit()

        return df if df is not None else pd.DataFrame()

    # ── structured-cache read/write helpers ──

    def _stock_basic_from_cache(self) -> pd.DataFrame:
        rows = self._cache_conn.execute(
                "SELECT ts_code, name, industry, list_status FROM stock_basic"
            ).fetchall()
        return pd.DataFrame(rows, columns=["ts_code", "name", "industry", "list_status"])

    def _stock_basic_replace(self, df: pd.DataFrame) -> None:
        now = time.time()
        records = [
            (r["ts_code"], r["name"], r["industry"], r.get("list_status", "L"),
             str(r.get("list_date", "")) if r.get("list_date") else "", now)
            for r in df.to_dict("records")
        ]
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO stock_basic
                    (ts_code, name, industry, list_status, list_date, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                records,
            )
            conn.commit()

    def _daily_basic_from_cache(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        rows = self._cache_conn.execute(
                """
                SELECT ts_code, trade_date, pe_ttm, pb, ps, total_mv
                FROM daily_basic
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
                INSERT OR REPLACE INTO daily_basic
                    (ts_code, trade_date, pe_ttm, pb, ps, total_mv, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )
            conn.commit()

    def backfill_daily_basic_by_date(
        self, start_date: str, end_date: str
    ) -> dict[str, int]:
        """Backfill daily_basic by trade_date (whole-market per call).

        Unlike ``get_daily_basic`` (per-ts_code incremental), this method
        fetches the entire market for each trade date in one API call.
        Much faster for bulk historical fill: ~1356 calls for 5.6 years
        vs ~5534 calls per-ts_code.

        Args:
            start_date: YYYYMMDD string.
            end_date:   YYYYMMDD string.

        Returns:
            Dict with ``days_fetched``, ``rows_inserted``, ``days_skipped``.
        """
        # Resolve trade calendar from cached daily_price (the authoritative
        # set of dates that actually have market data).
        cal_rows = self._cache_conn.execute(
            "SELECT DISTINCT trade_date FROM daily_price "
            "WHERE trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
            (start_date, end_date),
        ).fetchall()
        all_dates = [r[0] for r in cal_rows]

        # Resume from the latest trade_date already in daily_basic.
        resume_row = self._cache_conn.execute(
            "SELECT MAX(trade_date) FROM daily_basic "
            "WHERE trade_date >= ? AND trade_date <= ?",
            (start_date, end_date),
        ).fetchone()
        resume_from = resume_row[0] if resume_row else None
        if resume_from:
            fetch_dates = [d for d in all_dates if d > resume_from]
            logger.info(
                "daily_basic backfill: resuming after {} (already cached), "
                "{} dates remaining of {} total",
                resume_from, len(fetch_dates), len(all_dates),
            )
        else:
            fetch_dates = list(all_dates)
            logger.info(
                "daily_basic backfill: {} trade dates [{} → {}]",
                len(fetch_dates), fetch_dates[0] if fetch_dates else "?",
                fetch_dates[-1] if fetch_dates else "?",
            )

        days_fetched = 0
        rows_inserted = 0
        days_skipped = 0
        empty_dates: list[str] = []

        for i, d in enumerate(fetch_dates):
            try:
                df = self._call(
                    "daily_basic",
                    self._pro.daily_basic,
                    {
                        "trade_date": d,
                        "fields": (
                            "ts_code,trade_date,pe_ttm,pb,ps,total_mv,"
                            "turnover_rate,circ_mv,free_share"
                        ),
                    },
                )
            except Exception as exc:
                logger.warning("daily_basic backfill: {} failed: {} — skip", d, exc)
                days_skipped += 1
                continue

            if df is None or df.empty:
                empty_dates.append(d)
                days_skipped += 1
                continue

            # Bulk insert via OR REPLACE (pk dedup on ts_code+trade_date).
            self._daily_basic_bulk_insert(df)
            days_fetched += 1
            rows_inserted += len(df)

            if (i + 1) % 50 == 0 or (i + 1) == len(fetch_dates):
                logger.info(
                    "daily_basic backfill progress: {}/{} ({}%), "
                    "rows={} latest={}",
                    i + 1, len(fetch_dates),
                    round((i + 1) / max(len(fetch_dates), 1) * 100, 1),
                    rows_inserted, d,
                )

        if empty_dates:
            logger.warning(
                "daily_basic backfill: {} dates returned empty (first 5: {})",
                len(empty_dates), empty_dates[:5],
            )

        logger.info(
            "daily_basic backfill done: fetched={} days, rows_inserted={}, "
            "skipped={}",
            days_fetched, rows_inserted, days_skipped,
        )
        return {
            "days_fetched": days_fetched,
            "rows_inserted": rows_inserted,
            "days_skipped": days_skipped,
        }

    @staticmethod
    def _daily_basic_bulk_insert(df: pd.DataFrame) -> None:
        """Bulk-insert daily_basic rows (whole-market df, OR REPLACE)."""
        if df is None or df.empty:
            return
        now = time.time()
        records = []
        for r in df.to_dict("records"):
            records.append(
                (
                    r.get("ts_code", ""),
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
                INSERT OR REPLACE INTO daily_basic
                    (ts_code, trade_date, pe_ttm, pb, ps, total_mv, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )
            conn.commit()

    def _daily_prices_from_cache(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        rows = self._cache_conn.execute(
                """
                SELECT ts_code, trade_date, open, close, adj_factor
                FROM daily_price
                WHERE ts_code = ? AND trade_date >= ? AND trade_date <= ?
                ORDER BY trade_date ASC
                """,
                (ts_code, start_date, end_date),
            ).fetchall()
        return pd.DataFrame(
            rows, columns=["ts_code", "trade_date", "open", "close", "adj_factor"]
        )

    @staticmethod
    def _daily_prices_insert(
        ts_code: str, daily_df: pd.DataFrame, adj_df: pd.DataFrame
    ) -> None:
        if daily_df is None or daily_df.empty:
            return
        now = time.time()
        # Build an adj_factor lookup keyed by trade_date.
        adj_map: dict[str, float] = {}
        if adj_df is not None and not adj_df.empty:
            for r in adj_df.to_dict("records"):
                td = str(r.get("trade_date", ""))
                af = r.get("adj_factor")
                if td and af is not None:
                    adj_map[td] = float(af)

        records = []
        for r in daily_df.to_dict("records"):
            td = str(r.get("trade_date", ""))
            records.append(
                (
                    r.get("ts_code", ts_code),
                    td,
                    r.get("open"),
                    r.get("close"),
                    adj_map.get(td),
                    now,
                )
            )
        with sqlite3.connect(str(_CACHE_DB)) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO daily_price
                    (ts_code, trade_date, open, close, adj_factor, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
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
        # Use persistent read connection (24x faster than per-call connect).
        row = self._cache_conn.execute(
            "SELECT MAX(end_date), MAX(fetched_at) FROM financial "
            "WHERE ts_code=? AND endpoint=?",
            (ts_code, endpoint),
        ).fetchone()

        max_end = row[0] if row else None
        latest_fetched = row[1] if row else None
        fetched_today = (
            latest_fetched is not None
            and datetime.fromtimestamp(latest_fetched).date() == date.today()
        )

        # forecast (业绩预告) is sparse — most quarters have no announcement.
        # When the cache is empty, _financial_insert writes nothing, so
        # fetched_at stays NULL forever, making fetched_today always False.
        # This caused 200 stocks × 1351 days = 270k futile API calls (8.6h
        # of pure overhead in 5-year backtests). Track checked-today in memory.
        today_str = date.today().isoformat()
        cache_key = (endpoint, ts_code, today_str)
        if cache_key in _forecast_checked_today:
            return self._financial_from_cache(endpoint, ts_code, start_date, end_date)

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
            logger.info("Incremental {}: {} [{} → {}]", endpoint, ts_code, fetch_start, end_date)
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
            # Mark as checked today even if df was empty (e.g. forecast has
            # no announcement for this period). Prevents futile re-fetching.
            _forecast_checked_today.add(cache_key)

        return self._financial_from_cache(endpoint, ts_code, start_date, end_date)

    @staticmethod
    def _financial_insert(endpoint: str, ts_code: str, df: pd.DataFrame) -> None:
        """Insert financial rows, one JSON payload per (ts_code, end_date).

        The dividend endpoint returns multiple rows per end_date (预案 / 股东大会
        通过 / 实施). For dividend specifically we prefer the 实施 (executed)
        row — plans get cancelled, and only 实施 has the real cash_div. Other
        endpoints have one row per end_date and dedupe trivially.
        """
        if df is None or df.empty:
            return
        now = time.time()
        deduped = _dedupe_financial_rows(df, endpoint)
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
                INSERT OR REPLACE INTO financial
                    (ts_code, end_date, endpoint, payload, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                records,
            )
            conn.commit()

    def _financial_from_cache(
        self,
        endpoint: str, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        rows = self._cache_conn.execute(
                """
                SELECT payload
                FROM financial
                WHERE ts_code = ? AND endpoint = ? AND end_date >= ? AND end_date <= ?
                ORDER BY end_date DESC
                """,
                (ts_code, endpoint, start_date, end_date),
            ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([json.loads(r[0]) for r in rows])
