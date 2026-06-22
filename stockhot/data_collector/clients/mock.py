"""Mock client for testing and development."""

from typing import Any
from stockhot.data_collector.clients.base import BaseClient


class MockClient(BaseClient):
    """Mock client that returns sample data for testing."""

    def get_gainers(self, limit: int = 20) -> list[dict[str, Any]]:
        sample_data = [
            {
                "code": "000001",
                "name": "平安银行",
                "price": 12.58,
                "change_pct": 10.02,
                "volume": 156800000,
                "amount": 19.54,
            },
            {
                "code": "600519",
                "name": "贵州茅台",
                "price": 1688.00,
                "change_pct": 8.32,
                "volume": 23450000,
                "amount": 39.61,
            },
            {
                "code": "000002",
                "name": "万科A",
                "price": 8.45,
                "change_pct": 7.85,
                "volume": 89700000,
                "amount": 7.52,
            },
            {
                "code": "600036",
                "name": "招商银行",
                "price": 35.67,
                "change_pct": 6.54,
                "volume": 45600000,
                "amount": 16.23,
            },
            {
                "code": "601318",
                "name": "中国平安",
                "price": 48.23,
                "change_pct": 5.92,
                "volume": 67800000,
                "amount": 32.67,
            },
            {
                "code": "000858",
                "name": "五粮液",
                "price": 156.78,
                "change_pct": 5.67,
                "volume": 34500000,
                "amount": 53.98,
            },
            {
                "code": "002594",
                "name": "比亚迪",
                "price": 267.89,
                "change_pct": 5.34,
                "volume": 28900000,
                "amount": 77.45,
            },
            {
                "code": "600900",
                "name": "长江电力",
                "price": 23.45,
                "change_pct": 4.89,
                "volume": 56700000,
                "amount": 13.28,
            },
            {
                "code": "601888",
                "name": "中国中免",
                "price": 198.56,
                "change_pct": 4.56,
                "volume": 12300000,
                "amount": 24.43,
            },
            {
                "code": "000333",
                "name": "美的集团",
                "price": 67.89,
                "change_pct": 4.23,
                "volume": 34500000,
                "amount": 23.42,
            },
        ]
        return sample_data[:limit]

    def get_losers(self, limit: int = 20) -> list[dict[str, Any]]:
        sample_data = [
            {
                "code": "300750",
                "name": "宁德时代",
                "price": 198.45,
                "change_pct": -10.02,
                "volume": 89700000,
                "amount": 178.23,
            },
            {
                "code": "002475",
                "name": "立讯精密",
                "price": 32.56,
                "change_pct": -9.87,
                "volume": 56700000,
                "amount": 18.45,
            },
            {
                "code": "688981",
                "name": "中芯国际",
                "price": 45.67,
                "change_pct": -8.76,
                "volume": 78900000,
                "amount": 36.02,
            },
            {
                "code": "002230",
                "name": "科大讯飞",
                "price": 56.78,
                "change_pct": -8.34,
                "volume": 45600000,
                "amount": 25.89,
            },
            {
                "code": "300059",
                "name": "东方财富",
                "price": 18.90,
                "change_pct": -7.89,
                "volume": 67800000,
                "amount": 12.81,
            },
            {
                "code": "600276",
                "name": "恒瑞医药",
                "price": 45.23,
                "change_pct": -7.45,
                "volume": 34500000,
                "amount": 15.60,
            },
            {
                "code": "002412",
                "name": "汉森制药",
                "price": 12.34,
                "change_pct": -6.98,
                "volume": 23400000,
                "amount": 2.89,
            },
            {
                "code": "600522",
                "name": "中天科技",
                "price": 23.45,
                "change_pct": -6.56,
                "volume": 45600000,
                "amount": 10.69,
            },
            {
                "code": "601012",
                "name": "隆基绿能",
                "price": 28.90,
                "change_pct": -6.23,
                "volume": 56700000,
                "amount": 16.39,
            },
            {
                "code": "300274",
                "name": "阳光电源",
                "price": 89.45,
                "change_pct": -5.98,
                "volume": 34500000,
                "amount": 30.86,
            },
        ]
        return sample_data[:limit]

    def get_sectors(self, limit: int = 15) -> list[dict[str, Any]]:
        sample_data = [
            {"name": "银行", "change_pct": 5.67, "volume": 4567890000, "turnover_rate": 4.56},
            {"name": "白酒", "change_pct": 4.32, "volume": 2345678000, "turnover_rate": 3.89},
            {"name": "保险", "change_pct": 3.89, "volume": 1789012000, "turnover_rate": 3.45},
            {"name": "房地产", "change_pct": 3.45, "volume": 3456789000, "turnover_rate": 5.67},
            {"name": "券商", "change_pct": 2.98, "volume": 2345678000, "turnover_rate": 4.12},
            {"name": "新能源车", "change_pct": 2.56, "volume": 1890765000, "turnover_rate": 3.78},
            {"name": "光伏", "change_pct": 2.34, "volume": 1567890000, "turnover_rate": 3.21},
            {"name": "医药", "change_pct": 1.89, "volume": 2134567000, "turnover_rate": 2.89},
            {"name": "半导体", "change_pct": 1.56, "volume": 1678900000, "turnover_rate": 3.45},
            {"name": "军工", "change_pct": 1.23, "volume": 1234567000, "turnover_rate": 2.78},
        ]
        return sample_data[:limit]

    def get_fund_flow(self, limit: int = 10) -> list[dict[str, Any]]:
        sample_data = [
            {"code": "600519", "name": "贵州茅台", "net_inflow": 8.56, "inflow_rate": 15.67},
            {"code": "000001", "name": "平安银行", "net_inflow": 5.43, "inflow_rate": 12.34},
            {"code": "601318", "name": "中国平安", "net_inflow": 4.56, "inflow_rate": 10.89},
            {"code": "600036", "name": "招商银行", "net_inflow": 3.78, "inflow_rate": 9.45},
            {"code": "000002", "name": "万科A", "net_inflow": 3.21, "inflow_rate": 8.67},
            {"code": "002594", "name": "比亚迪", "net_inflow": 2.89, "inflow_rate": 7.89},
            {"code": "000858", "name": "五粮液", "net_inflow": 2.45, "inflow_rate": 6.78},
            {"code": "601888", "name": "中国中免", "net_inflow": 2.12, "inflow_rate": 5.89},
            {"code": "000333", "name": "美的集团", "net_inflow": 1.89, "inflow_rate": 5.12},
            {"code": "600900", "name": "长江电力", "net_inflow": 1.56, "inflow_rate": 4.56},
        ]
        return sample_data[:limit]
