"""统一市场数据访问层（DAL）— stockhot 与 davis_analyzer 共享的存储入口.

本包是项目中**所有市场行情数据的唯一入口**，消除三库隔离（stockhot.db /
tushare_cache.db / daily_data JSON blob）和四套 Tushare 客户端并存的混乱。

架构::

    ┌─────────────────────────────────────────────┐
    │  消费方：stockhot 采集模块 / davis_analyzer  │
    │         / 各 skill / advisor                 │
    └──────────────────┬──────────────────────────┘
                       │ 统一 API
              ┌────────▼────────┐
              │  repository.py  │  MarketDataRepository（读写 + 缓存判定）
              └────────┬────────┘
                       │
         ┌─────────────┼─────────────┐
         │             │             │
  ┌──────▼─────┐ ┌────▼────┐ ┌──────▼──────┐
  │ gateway.py │ │ cache.py│ │ market_db.py│
  │ Tushare网关│ │ 缓存策略 │ │  schema     │
  │ (传输+限频) │ │(三段式) │ │  (14张表)   │
  └────────────┘ └─────────┘ └──────┬──────┘
                                     │
                              ┌──────▼──────┐
                              │market_data.db│  唯一市场库
                              └─────────────┘

快速开始::

    from stockhot.data_layer import get_repository

    repo = get_repository()

    # 个股日线（增量缓存，stockhot 和 davis 共享）
    df = repo.get_daily_prices("000001.SZ", "20260101", "20260715")

    # 全市场某日行情（分页）
    df = repo.get_daily_by_date("20260715")

    # 指数日线（index_technical 和 volatility 共享）
    df = repo.get_index_daily("000001.SH", "20260101", "20260715")

    # 采集日志
    repo.log_scan("2026-07-15", "limit_up", "success", rows_affected=72)

详见 ``docs/方法论/统一市场数据架构.md``。
"""

from stockhot.data_layer.market_db import (
    MARKET_DB_PATH,
    get_connection,
    init_db,
    table_info,
)
from stockhot.data_layer.tushare_gateway import (
    TushareGateway,
    get_gateway,
    reset_gateway,
)
from stockhot.data_layer.repository import (
    MarketDataRepository,
    get_repository,
)

__all__ = [
    "MARKET_DB_PATH",
    "get_connection",
    "init_db",
    "table_info",
    "TushareGateway",
    "get_gateway",
    "reset_gateway",
    "MarketDataRepository",
    "get_repository",
]
