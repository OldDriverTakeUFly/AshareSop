"""OHLCV data loader — fetches daily stock data via AKShare.

Implements ``fetch_ohlcv`` from the frozen contract
(:mod:`stockhot.technical_analyzer.contract`).  All AKShare calls go
through ``safe_akshare_call`` (rate-limited + empty-safe).
"""

from __future__ import annotations

import akshare as ak
import pandas as pd

from stockhot.core.rate_limiter import safe_akshare_call
from stockhot.core.utils import to_akshare_date

_COLUMN_MAP = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
}

_KEEP_COLUMNS = ["open", "high", "low", "close", "volume"]


def _normalize_symbol(symbol: str) -> str:
    if "." in symbol:
        return symbol.split(".")[0]
    if symbol.lower().startswith(("sh", "sz", "bj")):
        return symbol[2:]
    return symbol


def fetch_ohlcv(
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str = "qfq",
) -> pd.DataFrame:
    raw = safe_akshare_call(
        ak.stock_zh_a_hist,
        symbol=_normalize_symbol(symbol),
        period="daily",
        start_date=to_akshare_date(start_date),
        end_date=to_akshare_date(end_date),
        adjust=adjust,
    )

    if raw is None or raw.empty:
        return pd.DataFrame()

    renamed = raw.rename(columns=_COLUMN_MAP)

    for col in _KEEP_COLUMNS + ["date"]:
        if col not in renamed.columns:
            return pd.DataFrame()

    renamed["date"] = pd.to_datetime(renamed["date"])
    renamed = renamed.set_index("date")
    renamed = renamed.sort_index(ascending=True)

    return renamed[_KEEP_COLUMNS]
