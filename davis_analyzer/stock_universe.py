"""Stock universe builder — fetch, pre-filter, and classify A-share stocks."""

import pandas as pd
from loguru import logger

from davis_analyzer.constants import CYCLICAL_INDUSTRIES, EXCLUSION_PATTERNS
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import StockInfo


def build_stock_universe(client: TushareClient) -> list[StockInfo]:
    """Fetch listed A-shares, exclude ST/delisted, set cyclical flag."""
    df: pd.DataFrame = client.get_stock_list()
    if df.empty:
        logger.warning("Stock list returned empty")
        return []

    total = len(df)

    mask = pd.Series([False] * len(df))
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
    logger.info(
        "Built universe: {} stocks ({} cyclical)", len(stocks), cyclical_count
    )
    return stocks
