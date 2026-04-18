"""Data collection module for StockHot-CN."""

from datetime import datetime
from typing import Any

from stockhot.data_collector.clients.mock import MockClient
from stockhot.data_collector.clients.tencent import TencentClient
from stockhot.data_collector.clients.eastmoney import EastMoneyClient
from stockhot.core.config import TOP_N_STOCKS, TOP_N_SECTORS, TOP_N_FUNDS
from stockhot.storage.database import save_daily_data

USE_MOCK = False
USE_TENCENT = True


def _get_client():
    if USE_MOCK:
        return MockClient()
    if USE_TENCENT:
        return TencentClient()
    return EastMoneyClient()


def run_collection(date: str | None = None) -> dict:
    """Run data collection for specified date."""
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    print(f"[DataCollector] 采集日期: {target_date}")

    client = _get_client()

    gainers = client.get_gainers(TOP_N_STOCKS)
    losers = client.get_losers(TOP_N_STOCKS)
    sectors = client.get_sectors(TOP_N_SECTORS)
    fund_flows = client.get_fund_flow(TOP_N_FUNDS)

    data = {
        "date": target_date,
        "gainers": gainers,
        "losers": losers,
        "sectors": sectors,
        "fund_flows": fund_flows,
    }

    print(f"[DataCollector] 采集完成: {len(gainers)} 涨幅, {len(losers)} 跌幅, {len(sectors)} 板块")

    save_daily_data(data)

    return {"date": target_date, "status": "success", "counts": {
        "gainers": len(gainers),
        "losers": len(losers),
        "sectors": len(sectors),
        "fund_flows": len(fund_flows),
    }}


def get_gainers(limit: int = 20) -> list[dict[str, Any]]:
    """获取涨跌幅排行"""
    return _get_client().get_gainers(limit)


def get_losers(limit: int = 20) -> list[dict[str, Any]]:
    """获取跌幅排行"""
    return _get_client().get_losers(limit)


def get_sector_performance() -> list[dict[str, Any]]:
    """获取板块表现"""
    return _get_client().get_sectors(TOP_N_SECTORS)


def get_fund_flow() -> list[dict[str, Any]]:
    """获取资金流向"""
    return _get_client().get_fund_flow(TOP_N_FUNDS)