"""OHLCV data loader — 统一数据层（DAL）优先，Tushare/AKShare 兜底.

数据源策略（2026-07-15 统一架构调整）：
- **主源：统一数据层 DAL**（``stockhot.data_layer``），复用 market_data.db 的
  daily_price 缓存，避免 stockhot 与 davis_analyzer 重复拉取同一个个股日线。
  DAL 内部按三段式增量缓存，命中时不调 Tushare。
- 兜底 1：Tushare ``pro_bar(adj=qfq)``（DAL 无缓存时的实时拉取）
- 兜底 2：AKShare ``stock_zh_a_hist``（Tushare 失败时）

前复权处理：DAL 存原始 close + adj_factor，读取后在本地用 ``close * adj_factor / latest_adj``
做前复权（与 pro_bar 的 qfq 语义一致），避免存储层固化复权基准。

对外接口 ``fetch_ohlcv`` 签名与返回 schema 不变：
DataFrame[index=date, columns=[open, high, low, close, volume]]，升序。
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


def _fetch_via_dal(symbol: str, start_date: str, end_date: str, adjust: str) -> pd.DataFrame:
    """统一数据层取 OHLCV（增量缓存，stockhot 与 davis 共享）.

    DAL 存原始 close + adj_factor，这里按 adjust 参数在本地做复权计算：
    - qfq（前复权）：price * adj_factor / latest_adj_factor
    - hfq（后复权）：price * adj_factor
    - 不复权：price 原值
    """
    from stockhot.data_layer import get_repository

    ts_code = symbol if "." in symbol else _to_ts_code(symbol)
    s = start_date.replace("-", "")
    e = end_date.replace("-", "")

    try:
        repo = get_repository()
        df = repo.get_daily_prices(ts_code, s, e)
    except Exception as exc:
        logger.warning(f"DAL get_daily_prices({ts_code}) failed: {exc}")
        return pd.DataFrame()

    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df = df.set_index("date").sort_index(ascending=True)

    # 前复权 / 后复权
    if adjust == "qfq" and "adj_factor" in df.columns:
        latest_adj = df["adj_factor"].iloc[-1]
        if latest_adj and latest_adj > 0:
            ratio = df["adj_factor"] / latest_adj
            for col in ["open", "high", "low", "close"]:
                df[col] = df[col] * ratio
    elif adjust == "hfq" and "adj_factor" in df.columns:
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col] * df["adj_factor"]

    # volume 列名映射（DAL 用 vol，对外用 volume）
    df = df.rename(columns={"vol": "volume"})
    for col in _KEEP_COLUMNS:
        if col not in df.columns:
            return pd.DataFrame()
    return df[_KEEP_COLUMNS].astype(float)


def fetch_ohlcv(
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str = "qfq",
) -> pd.DataFrame:
    """采集个股 OHLCV，统一数据层（DAL）优先，Tushare/AKShare 兜底。

    三级回退：
    1. **DAL**（market_data.db 缓存）—— 首选，与 davis_analyzer 共享缓存
    2. **Tushare pro_bar** —— DAL 无数据时的实时拉取（会写入 DAL 缓存）
    3. **AKShare stock_zh_a_hist** —— Tushare 失败时的兜底

    参数：
        symbol: 股票代码，支持 "300398.SZ"（Tushare 格式）或 "300398"（纯代码）
        start_date: 起始日期 "YYYY-MM-DD"
        end_date: 结束日期 "YYYY-MM-DD"
        adjust: 复权方式 "qfq"(前复权, 默认) / "hfq"(后复权) / "" (不复权)

    返回：
        DataFrame[index=date, columns=[open, high, low, close, volume]]，
        升序，与 indicators.py 完全兼容。三级都失败返回空 DataFrame。
    """
    # 第一优先：DAL 缓存
    dal_df = _fetch_via_dal(symbol, start_date, end_date, adjust)
    if not dal_df.empty:
        return dal_df

    # DAL miss → 触发 DAL 增量拉取（内部会调 Tushare daily+adj_factor 并写入缓存），
    # 这样下次命中缓存，避免重复走 pro_bar 无缓存路径。
    try:
        from stockhot.data_layer import get_repository

        ts_code = symbol if "." in symbol else _to_ts_code(symbol)
        s = start_date.replace("-", "")
        e = end_date.replace("-", "")
        repo = get_repository()
        repo.get_daily_prices(ts_code, s, e)  # 增量拉取 + 写缓存
        # 再次从 DAL 读取（此时应命中刚写入的缓存）
        dal_df = _fetch_via_dal(symbol, start_date, end_date, adjust)
        if not dal_df.empty:
            return dal_df
    except Exception:
        pass  # DAL 增量拉取失败则走原始 fallback

    # 最终回退：Tushare pro_bar → AKShare（原逻辑，不写缓存）
    return fetch_with_fallback(
        primary_fn=lambda: _fetch_via_tushare(symbol, start_date, end_date, adjust),
        fallback_fn=lambda: _fetch_via_akshare(symbol, start_date, end_date, adjust),
        label=f"OHLCV({symbol})",
    )
