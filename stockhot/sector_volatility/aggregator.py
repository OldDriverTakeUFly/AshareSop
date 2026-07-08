"""Sector RV aggregator — 成分股 RV 等权聚合为板块 RV。

核心算法：对每个交易日，取所有成分股当日 RV20 的等权平均（忽略 NaN）。
这是"板块等权 RV"——不按市值加权，避免单只龙头股主导。

与指数 RV 的差异：
- 指数 RV：用指数收盘价直接算（自带市值加权）
- 板块等权 RV：成分股 RV 平均（小盘股权重更高，绝对值略高）
两者方向一致，分位可比。
"""

from __future__ import annotations

import pandas as pd

from stockhot.core.logging import logger


def aggregate_sector_rv(member_rv_dict: dict[str, pd.Series]) -> pd.Series:
    """成分股 RV 等权聚合为板块 RV。

    参数：
        member_rv_dict: {ts_code: pd.Series(name='rv20', index=date)}

    返回：
        pd.Series(name='sector_rv20', index=date)，等权平均，忽略 NaN。
    """
    if not member_rv_dict:
        return pd.Series(dtype=float, name="sector_rv20")

    # 合并所有成分股 RV 为 DataFrame（列=个股，行=日期）
    df = pd.DataFrame(member_rv_dict)
    # 等权平均：每行（每日）跨列（个股）取均值，skipna
    sector_rv = df.mean(axis=1, skipna=True).round(2)
    sector_rv.name = "sector_rv20"

    # 至少需要 5 只成分股有数据才算有效（避免数据稀疏期的噪声）
    valid_count = df.notna().sum(axis=1)
    sector_rv[valid_count < 5] = float("nan")

    logger.info(
        f"aggregate_sector_rv: {len(member_rv_dict)} stocks → {sector_rv.notna().sum()} valid days"
    )
    return sector_rv.dropna()
