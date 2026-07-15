"""Volatility data loader — fetches index OHLCV + iVIX (China 50ETF QVIX) history.

复用 ``index_technical/data_loader.py`` 的双源 fallback 模式拉指数日线，
另用 AKShare ``index_option_50etf_qvix`` 拉取 iVIX 完整时序（A 股官方恐慌指数，
2018.6 后停发，AKShare 仍持续更新第三方重建版本）。

输出格式：DatetimeIndex 升序 DataFrame / Series，与 analyzer 计算函数兼容。

数据源：
    指数日线 — AKShare ``stock_zh_index_daily`` 主 / Tushare ``index_daily`` 备
    iVIX     — AKShare ``index_option_50etf_qvix``（返回 date/open/high/low/close，2758+ 行）

ts_code 转换规则（统一输入为 Tushare 格式 XXXXXX.SS/SZ）：
    - 输入 "000001.SH" → AKShare symbol "sh000001"
    - 输入 "399001.SZ" → AKShare symbol "sz399001"

与方法论研报（``docs/方法论/A股波动率观察框架方法论深度研报.md``）Layer 1/5 对应。
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


def _fetch_index_via_akshare(ts_code: str) -> pd.DataFrame:
    """AKShare 主源：返回升序 OHLCV DataFrame（index=date）。"""
    import akshare as ak

    symbol = _to_akshare_symbol(ts_code)
    raw = ak.stock_zh_index_daily(symbol=symbol)
    if raw is None or raw.empty:
        return pd.DataFrame()

    df = raw.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index(ascending=True)
    if "close" not in df.columns:
        return pd.DataFrame()
    return df[["close"]].astype(float)


def _fetch_index_via_tushare(ts_code: str, days: int) -> pd.DataFrame:
    """Tushare fallback：返回升序 close DataFrame（index=date）。"""
    from stockhot.tushare_config import get_pro_api

    pro = get_pro_api(timeout=30)
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=int(days * 1.6))).strftime("%Y%m%d")
    raw = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
    if raw is None or raw.empty:
        return pd.DataFrame()

    df = raw.rename(columns={"trade_date": "date"}).copy()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.set_index("date").sort_index(ascending=True)
    return df[["close"]].astype(float)


def fetch_index_history(ts_code: str, days: int = 1300) -> pd.DataFrame:
    """采集指数日线收盘价序列，DAL 缓存优先，Tushare/AKShare 兜底。

    参数：
        ts_code: Tushare 格式代码，如 "000001.SH" / "399006.SZ" / "000688.SH"
        days: 取最近 N 个交易日（默认 1300 ≈ 5 年 + buffer）

    返回：
        DataFrame[index=date, columns=[close]]，升序。
        三级都失败返回空 DataFrame。

    优先级：DAL ``index_daily``（缓存，与 index_technical 共享）→ Tushare → AKShare
    """
    # 第一优先：DAL 缓存（与 index_technical 模块共享，避免重复拉取 index_daily）
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
            return df[["close"]].astype(float).tail(days)
    except Exception:
        pass  # DAL 失败则回退

    # 回退：Tushare → AKShare（原逻辑）
    from stockhot.core.datasource import fetch_with_fallback

    def _akshare_cropped() -> pd.DataFrame:
        df_ak = _fetch_index_via_akshare(ts_code)
        return df_ak.tail(days) if not df_ak.empty else pd.DataFrame()

    return fetch_with_fallback(
        primary_fn=lambda: _fetch_index_via_tushare(ts_code, days),
        fallback_fn=_akshare_cropped,
        label=f"index_close({ts_code})",
    )


def fetch_ivix_history(days: int = 1300) -> pd.Series:
    """采集上证 50ETF 波动率指数（iVIX / QVIX）历史 close 序列。

    AKShare ``index_option_50etf_qvix`` 返回 2015-02 至今的完整日频数据，
    覆盖 iVIX 官方停发（2018.6）后的第三方重建版本——这是 Layer 5 的核心数据源，
    比方法论研报里的 Tushare 期权链 + BS 反算（仅快照可用）更优。

    ⚠️ **数据源缺口**（参考 ``.agents/skills/data-source-convention.md``）：
    Tushare **无 QVIX/iVIX 等价接口**，本函数为 **AKShare 单源**。
    数据来自期权论坛（optbbs.com）第三方重建版，非官方发布，算法可能与
    原 iVIX（Carr-Madan 无模型 IV）有偏差。建议定期与 50ETF 期权 BS 反算
    IV 交叉校验（方法论文档 §3.3）。

    参数：
        days: 取最近 N 个交易日（默认 1300 ≈ 5 年）

    返回：
        Series[index=date, name='ivix']，升序，float。
        失败返回空 Series。
    """
    import akshare as ak

    try:
        raw = ak.index_option_50etf_qvix()
    except Exception as e:
        logger.warning(f"[iVIX] AKShare error: {type(e).__name__}: {e}")
        return pd.Series(dtype=float, name="ivix")

    if raw is None or raw.empty:
        logger.warning("[iVIX] AKShare empty")
        return pd.Series(dtype=float, name="ivix")

    df = raw.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index(ascending=True)
    series = df["close"].astype(float).rename("ivix").tail(days)
    logger.info(f"[iVIX] via AKShare: {len(series)} rows, latest={series.iloc[-1]:.2f}")
    return series
