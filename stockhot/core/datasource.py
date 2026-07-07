"""Unified dual-source data fetcher — Tushare primary, AKShare fallback.

提供统一的"Tushare 优先 → AKShare 兜底"双源抽象，消除各模块重复的双源模板代码。
所有需要双源的数据获取应通过 ``fetch_with_fallback``。

设计原则：
- Tushare 是第一数据源（稳定、结构化、新端点 api.tushare.pro/dataapi）
- AKShare 是 fallback（覆盖 Tushare 缺口：停牌池、活跃营业部龙虎榜等）
- 日志统一格式：``[label] via Tushare: N rows`` 或 ``[label] Tushare empty, via AKShare: N rows``
- 永不抛异常：两源都失败返回空 DataFrame
"""

from __future__ import annotations

from typing import Callable

import pandas as pd

from stockhot.core.logging import logger


def fetch_with_fallback(
    primary_fn: Callable[[], pd.DataFrame],
    fallback_fn: Callable[[], pd.DataFrame] | None,
    label: str,
    primary_name: str = "Tushare",
    fallback_name: str = "AKShare",
) -> pd.DataFrame:
    """Tushare 优先，失败/空则 AKShare fallback。

    参数：
        primary_fn: 无参 callable，调用 Tushare（通常用 lambda 包装 safe_tushare_call）
        fallback_fn: 无参 callable，调用 AKShare；None 表示无 fallback（Tushare only）
        label: 可读标签，如 "涨停池"、"龙虎榜明细"，用于日志
        primary_name: 主源名（默认 "Tushare"）
        fallback_name: 兜底源名（默认 "AKShare"）

    返回：
        成功返回 DataFrame；两源都失败/空则返回空 DataFrame。

    用法示例：
        df = fetch_with_fallback(
            primary_fn=lambda: safe_tushare_call("limit_list_d", trade_date=date, limit="U"),
            fallback_fn=lambda: safe_akshare_call(ak.stock_zt_pool_em, date=date),
            label="涨停池",
        )
    """
    # 1. 尝试主源（Tushare）
    try:
        df = primary_fn()
        if df is not None and not df.empty:
            logger.info(f"[{label}] via {primary_name}: {len(df)} rows")
            return df
        logger.info(f"[{label}] {primary_name} empty, trying {fallback_name}")
    except Exception as e:
        logger.warning(f"[{label}] {primary_name} error: {type(e).__name__}: {e}, trying {fallback_name}")

    # 2. 尝试 fallback（AKShare）
    if fallback_fn is None:
        logger.warning(f"[{label}] no fallback configured, returning empty")
        return pd.DataFrame()

    try:
        df = fallback_fn()
        if df is not None and not df.empty:
            logger.info(f"[{label}] via {fallback_name}: {len(df)} rows")
            return df
        logger.warning(f"[{label}] {fallback_name} also empty")
    except Exception as e:
        logger.warning(f"[{label}] {fallback_name} error: {type(e).__name__}: {e}")

    return pd.DataFrame()


def fetch_tushare_only(api_name: str, label: str, fields: str = "", **params) -> pd.DataFrame:
    """便捷封装：仅 Tushare（无 fallback）场景。

    用于宏观、财务等 Tushare 独占的数据。
    """
    from stockhot.core.tushare_client_safe import safe_tushare_call

    df = safe_tushare_call(api_name, fields=fields, **params)
    if not df.empty:
        logger.info(f"[{label}] via Tushare: {len(df)} rows")
    return df
