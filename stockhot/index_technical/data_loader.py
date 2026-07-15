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
    """采集指数 OHLCV，DAL 缓存优先，Tushare/AKShare 兜底（2026-07-15 统一架构调整）。

    参数：
        ts_code: Tushare 格式代码，如 "000001.SH" / "399006.SZ" / "000688.SH"
        days: 取最近 N 个交易日（默认 120，约半年，够算 MA60/MACD/RSI/KDJ/布林）

    返回：
        DataFrame[index=date, columns=[open, high, low, close, volume]]，
        升序，与 stockhot.technical_analyzer.indicators 完全兼容。
        三级都失败返回空 DataFrame。

    优先级：DAL ``index_daily``（缓存，与 volatility 共享）→ Tushare → AKShare
    """
    # 第一优先：DAL 缓存（与 volatility 模块共享，避免重复拉取 index_daily）
    try:
        from stockhot.data_layer import get_repository

        repo = get_repository()
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=int(days * 1.6))).strftime("%Y%m%d")
        df_dal = repo.get_index_daily(ts_code, start, end)
        if not df_dal.empty:
            df = df_dal.copy()
            df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
            df = df.set_index("date").sort_index(ascending=True)
            df = df.rename(columns={"vol": "volume"})
            keep = ["open", "high", "low", "close", "volume"]
            if all(c in df.columns for c in keep):
                return df[keep].astype(float).tail(days)
    except Exception:
        pass  # DAL 失败则回退

    # 回退：Tushare → AKShare（原逻辑）
    from stockhot.core.datasource import fetch_with_fallback

    def _akshare_cropped() -> pd.DataFrame:
        df_ak = _fetch_via_akshare(ts_code)
        return df_ak.tail(days) if not df_ak.empty else pd.DataFrame()

    return fetch_with_fallback(
        primary_fn=lambda: _fetch_via_tushare(ts_code, days),
        fallback_fn=_akshare_cropped,
        label=f"index_ohlcv({ts_code})",
    )
