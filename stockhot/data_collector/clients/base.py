"""Base client for stock data sources."""

import abc
from typing import Any


class BaseClient(abc.ABC):
    """Abstract base class for stock data clients."""

    @abc.abstractmethod
    def get_gainers(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取涨跌幅排行"""
        pass

    @abc.abstractmethod
    def get_losers(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取跌幅排行"""
        pass

    @abc.abstractmethod
    def get_sectors(self, limit: int = 15) -> list[dict[str, Any]]:
        """获取板块排行"""
        pass

    @abc.abstractmethod
    def get_fund_flow(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取资金流向"""
        pass
