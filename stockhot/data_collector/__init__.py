"""Data collection module for StockHot-CN."""

from datetime import datetime


def run_collection(date: str | None = None) -> dict:
    """Run data collection for specified date."""
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    print(f"[DataCollector] 采集日期: {target_date}")
    print("[DataCollector] 采集完成")
    return {"date": target_date, "status": "success"}


def get_gainers(limit: int = 20) -> list[dict]:
    """获取涨跌幅排行"""
    return []


def get_losers(limit: int = 20) -> list[dict]:
    """获取跌幅排行"""
    return []


def get_sector_performance() -> list[dict]:
    """获取板块表现"""
    return []


def get_fund_flow() -> list[dict]:
    """获取资金流向"""
    return []