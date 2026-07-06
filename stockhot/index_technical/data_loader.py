"""Index OHLCV data loader — fetches index daily data via AKShare (primary) + Tushare (fallback).

与 technical_analyzer/data_loader.py（个股）平行的指数版本。
输出格式与 indicators.py 完全兼容：English columns (open/high/low/close/volume)
+ DatetimeIndex 升序。

AKShare 指数接口（主源，免费、无需 token）：
    ak.stock_zh_index_daily(symbol="sh000001")
    返回列：date/open/high/low/close/volume，volume 单位为手

Tushare 指数接口（fallback，需 token）：
    pro.index_daily(ts_code="000001.SH")
    返回列：trade_date/open/high/low/close/vol/pct_chg，vol 单位为手

ts_code 转换规则（统一输入为 Tushare 格式 XXXXXX.SS/SZ）：
    - 输入 "000001.SH" → AKShare symbol "sh000001"
    - 输入 "399001.SZ" → AKShare symbol "sz399001"
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from stockhot.core.logging import logger


def _to_akshare_symbol(ts_code: str) -> str:
    """Tushare ts_code (000001.SH) → AKShare symbol (sh000001)."""
    if "." not in ts_code:
        return ts_code.lower()
    code, market = ts_code.split(".")
    return f"{market.lower()}{code}"


def _fetch_via_akshare(ts_code: str) -> pd.DataFrame:
    """AKShare 主源：返回升序 OHLCV DataFrame（index=date）。"""
    import akshare as ak

    symbol = _to_akshare_symbol(ts_code)
    raw = ak.stock_zh_index_daily(symbol=symbol)
    if raw is None or raw.empty:
        return pd.DataFrame()

    df = raw.rename(columns={"date": "date"}).copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index(ascending=True)
    keep = ["open", "high", "low", "close", "volume"]
    for col in keep:
        if col not in df.columns:
            return pd.DataFrame()
    return df[keep].astype(float)


def _fetch_via_tushare(ts_code: str, days: int) -> pd.DataFrame:
    """Tushare fallback：返回升序 OHLCV DataFrame（index=date）。"""
    from stockhot.tushare_config import get_pro_api

    pro = get_pro_api(timeout=30)
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=int(days * 1.6))).strftime("%Y%m%d")
    raw = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
    if raw is None or raw.empty:
        return pd.DataFrame()

    df = raw.rename(columns={"trade_date": "date", "vol": "volume"}).copy()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.set_index("date").sort_index(ascending=True)
    keep = ["open", "high", "low", "close", "volume"]
    for col in keep:
        if col not in df.columns:
            return pd.DataFrame()
    return df[keep].astype(float)


def fetch_index_ohlcv(ts_code: str, days: int = 120) -> pd.DataFrame:
    """采集指数 OHLCV，双源 fallback（AKShare → Tushare）。

    参数：
        ts_code: Tushare 格式代码，如 "000001.SH" / "399006.SZ" / "000688.SH"
        days: 取最近 N 个交易日（默认 120，约半年，够算 MA60/MACD/RSI/KDJ/布林）

    返回：
        DataFrame[index=date, columns=[open, high, low, close, volume]]，
        升序，与 stockhot.technical_analyzer.indicators 完全兼容。
        数据不可用时返回空 DataFrame。
    """
    # 主源 AKShare
    try:
        df = _fetch_via_akshare(ts_code)
        if not df.empty:
            # AKShare 返回全部历史，截取最近 days 行
            df = df.tail(days)
            logger.info(f"fetch_index_ohlcv({ts_code}) via AKShare: {len(df)} rows")
            return df
    except Exception as e:
        logger.warning(f"fetch_index_ohlcv({ts_code}) AKShare failed: {type(e).__name__}: {e}")

    # Fallback Tushare
    try:
        df = _fetch_via_tushare(ts_code, days)
        if not df.empty:
            logger.info(f"fetch_index_ohlcv({ts_code}) via Tushare: {len(df)} rows")
            return df
    except Exception as e:
        logger.warning(f"fetch_index_ohlcv({ts_code}) Tushare failed: {type(e).__name__}: {e}")

    logger.error(f"fetch_index_ohlcv({ts_code}): all sources failed")
    return pd.DataFrame()
