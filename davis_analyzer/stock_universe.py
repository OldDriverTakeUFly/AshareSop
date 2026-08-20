"""Stock universe builder — fetch, pre-filter, and classify A-share stocks."""

import pandas as pd
from loguru import logger

from davis_analyzer.constants import CYCLICAL_INDUSTRIES, EXCLUSION_PATTERNS
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo


def build_stock_universe(
    client: TushareClient, active_only: bool = True
) -> list[StockInfo]:
    """Fetch listed A-shares, exclude ST/delisted, set cyclical flag.

    Args:
        client: Tushare 数据客户端。
        active_only: True（默认）只保留可交易标的——list_status=='L' 且
            名称不含退市整理期标记（"退"）。get_stock_list 为回测反幸存者
            偏差故意包含 D/P 状态股票（2026-08-02 起），实盘选股宇宙必须
            在此剔除：退市股的暴跌残影会被因子读成"困境反转+极低估值
            分位"，以失真高分污染 top 篮子（2026-08-19 实例：紫天退
            composite=99.4 占榜首）。回测等需要退市股的场景显式传
            active_only=False。
    """
    df: pd.DataFrame = client.get_stock_list()
    if df.empty:
        logger.warning("Stock list returned empty")
        return []

    total = len(df)

    if active_only:
        if "list_status" in df.columns:
            df = df[df["list_status"] == "L"].copy()
        # 退市整理期股票 list_status 仍为 L，但名称带"退"标记——名称兜底
        delist_mask = df["name"].str.contains("退", case=False, na=False, regex=False)
        df = df[~delist_mask].copy()
        logger.info(
            "Stock universe: {} → {} after dropping delisted/suspended",
            total,
            len(df),
        )

    mask = pd.Series(False, index=df.index)
    for pattern in EXCLUSION_PATTERNS:
        mask |= df["name"].str.contains(pattern, case=False, na=False, regex=False)
    df = df[~mask].copy()

    logger.info(
        "Stock universe: {} → {} after filtering ST/patterns",
        total,
        len(df),
    )

    cyclical_set = set(CYCLICAL_INDUSTRIES)
    stocks: list[StockInfo] = []
    for _, row in df.iterrows():
        industry = row.get("industry", "")
        stocks.append(
            StockInfo(
                ts_code=row["ts_code"],
                name=row["name"],
                industry=industry if pd.notna(industry) else "",
                list_status=row.get("list_status", "L"),
                is_cyclical=industry in cyclical_set,
            )
        )

    cyclical_count = sum(1 for s in stocks if s.is_cyclical)
    logger.info("Built universe: {} stocks ({} cyclical)", len(stocks), cyclical_count)
    return stocks
