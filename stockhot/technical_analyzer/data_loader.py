"""OHLCV data loader — Tushare primary, AKShare fallback.

数据源策略（2026-07-07 调整）：Tushare 第一，AKShare 兜底。
- 主源：Tushare ``pro_bar(adj=qfq)``（前复权日线，走新端点 api.tushare.pro/dataapi）
- 兜底：AKShare ``stock_zh_a_hist``（前复权，东财源）

对外接口 ``fetch_ohlcv`` 签名与返回 schema 不变：
DataFrame[index=date, columns=[open, high, low, close, volume]]，升序。

历史上此模块曾仅用 AKShare，但 AKShare 个股接口（东财 stock_zh_a_hist）经常
RemoteDisconnected，故改为 Tushare 优先。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from stockhot.core.datasource import fetch_with_fallback
from stockhot.core.logging import logger
from stockhot.core.rate_limiter import safe_akshare_call
from stockhot.core.tushare_client_safe import safe_tushare_call

_COLUMN_MAP_AK = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
}

_KEEP_COLUMNS = ["open", "high", "low", "close", "volume"]


def _normalize_symbol(symbol: str) -> str:
    """Tushare ts_code 或纯代码 → AKShare 6 位代码（去市场后缀）。"""
    if "." in symbol:
        return symbol.split(".")[0]
    if symbol.lower().startswith(("sh", "sz", "bj")):
        return symbol[2:]
    return symbol


def _fetch_via_tushare(symbol: str, start_date: str, end_date: str, adjust: str) -> pd.DataFrame:
    """Tushare pro_bar 取前/后复权 OHLCV。

    pro_bar 是 Tushare 的便捷接口，内部组合 daily + adj_factor 做复权。
    adjust 参数：qfq(前复权)/hfq(后复权)/None(不复权)。
    """
    # pro_bar 通过 ts.pro_bar() 调用，不是 pro.xxx，需用 tushare 库
    import tushare as ts

    # pro_bar 需要 ts_code 格式（带 .SZ/.SH），而本模块输入可能是不带后缀的代码
    ts_code = symbol if "." in symbol else _to_ts_code(symbol)
    # 日期格式转换：YYYY-MM-DD → YYYYMMDD
    s = start_date.replace("-", "")
    e = end_date.replace("-", "")

    df = ts.pro_bar(ts_code=ts_code, adj=adjust, start_date=s, end_date=e)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(columns={"trade_date": "date", "vol": "volume"}).copy()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.set_index("date").sort_index(ascending=True)
    for col in _KEEP_COLUMNS:
        if col not in df.columns:
            return pd.DataFrame()
    return df[_KEEP_COLUMNS].astype(float)


def _to_ts_code(symbol: str) -> str:
    """纯 6 位代码 → Tushare ts_code（根据规则推断交易所）。"""
    code = _normalize_symbol(symbol)
    if code.startswith(("60", "68", "90", "11", "13")):  # 沪市
        return f"{code}.SH"
    elif code.startswith(("00", "30", "20")):  # 深市
        return f"{code}.SZ"
    elif code.startswith(("43", "83", "87", "88")):  # 北交所
        return f"{code}.BJ"
    return f"{code}.SZ"  # 默认深市


def _fetch_via_akshare(symbol: str, start_date: str, end_date: str, adjust: str) -> pd.DataFrame:
    """AKShare stock_zh_a_hist 取复权 OHLCV（兜底）。"""
    import akshare as ak

    from stockhot.core.utils import to_akshare_date

    ak_adjust = {"qfq": "qfq", "hfq": "hfq"}.get(adjust, "")
    raw = safe_akshare_call(
        ak.stock_zh_a_hist,
        symbol=_normalize_symbol(symbol),
        period="daily",
        start_date=to_akshare_date(start_date),
        end_date=to_akshare_date(end_date),
        adjust=ak_adjust,
    )
    if raw is None or raw.empty:
        return pd.DataFrame()

    renamed = raw.rename(columns=_COLUMN_MAP_AK)
    for col in _KEEP_COLUMNS + ["date"]:
        if col not in renamed.columns:
            return pd.DataFrame()
    renamed["date"] = pd.to_datetime(renamed["date"])
    renamed = renamed.set_index("date").sort_index(ascending=True)
    return renamed[_KEEP_COLUMNS].astype(float)


def fetch_ohlcv(
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str = "qfq",
) -> pd.DataFrame:
    """采集个股 OHLCV，Tushare 优先，AKShare 兜底。

    参数：
        symbol: 股票代码，支持 "300398.SZ"（Tushare 格式）或 "300398"（纯代码）
        start_date: 起始日期 "YYYY-MM-DD"
        end_date: 结束日期 "YYYY-MM-DD"
        adjust: 复权方式 "qfq"(前复权, 默认) / "hfq"(后复权) / "" (不复权)

    返回：
        DataFrame[index=date, columns=[open, high, low, close, volume]]，
        升序，与 indicators.py 完全兼容。两源都失败返回空 DataFrame。
    """
    return fetch_with_fallback(
        primary_fn=lambda: _fetch_via_tushare(symbol, start_date, end_date, adjust),
        fallback_fn=lambda: _fetch_via_akshare(symbol, start_date, end_date, adjust),
        label=f"OHLCV({symbol})",
    )
