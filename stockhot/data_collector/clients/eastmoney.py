"""EastMoney data client for StockHot-CN."""

import re
import json
from datetime import datetime
from typing import Any
import requests
from stockhot.data_collector.clients.base import BaseClient
from stockhot.core.exceptions import DataSourceError


class EastMoneyClient(BaseClient):
    """EastMoney data source client."""

    BASE_URL = "https://push2.eastmoney.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/",
        })

    def get_gainers(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取涨幅排行"""
        try:
            url = f"{self.BASE_URL}/api/qt/clist/get"
            params = {
                "pn": 1,
                "pz": limit,
                "po": 0,
                "np": 1,
                "ut": "bd1d3dd5b7e0d041c6c2fa054a3c0a9d",
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": "m:0+t:80,m:0+t:81,m:1+t:80,m:1+t:81",
                "fields": "f1,f2,f3,f4,f5,f6,f12,f13,f14",
            }
            resp = self.session.get(url, params=params, timeout=10)
            data = resp.json()
            return self._parse_stock_list(data.get("data", {}).get("diff", []))
        except Exception as e:
            raise DataSourceError(f"获取涨幅排行失败: {e}")

    def get_losers(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取跌幅排行"""
        try:
            url = f"{self.BASE_URL}/api/qt/clist/get"
            params = {
                "pn": 1,
                "pz": limit,
                "po": 1,
                "np": 1,
                "ut": "bd1d3dd5b7e0d041c6c2fa054a3c0a9d",
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": "m:0+t:80,m:0+t:81,m:1+t:80,m:1+t:81",
                "fields": "f1,f2,f3,f4,f5,f6,f12,f13,f14",
            }
            resp = self.session.get(url, params=params, timeout=10)
            data = resp.json()
            return self._parse_stock_list(data.get("data", {}).get("diff", []))
        except Exception as e:
            raise DataSourceError(f"获取跌幅排行失败: {e}")

    def get_sectors(self, limit: int = 15) -> list[dict[str, Any]]:
        """获取板块排行"""
        try:
            url = f"{self.BASE_URL}/api/qt/clist/get"
            params = {
                "pn": 1,
                "pz": limit,
                "po": 0,
                "np": 1,
                "ut": "bd1d3dd5b7e0d041c6c2fa054a3c0a9d",
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": "b:MK0021",
                "fields": "f1,f2,f3,f4,f5,f12,f14",
            }
            resp = self.session.get(url, params=params, timeout=10)
            data = resp.json()
            return self._parse_sector_list(data.get("data", {}).get("diff", []))
        except Exception as e:
            raise DataSourceError(f"获取板块排行失败: {e}")

    def get_fund_flow(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取资金流向"""
        try:
            url = f"{self.BASE_URL}/api/qt/clist/get"
            params = {
                "pn": 1,
                "pz": limit,
                "po": 0,
                "np": 1,
                "ut": "bd1d3dd5b7e0d041c6c2fa054a3c0a9d",
                "fltt": 2,
                "invt": 2,
                "fid": "f10",
                "fs": "m:0+t:80,m:0+t:81,m:1+t:80,m:1+t:81",
                "fields": "f2,f3,f10,f12,f14",
            }
            resp = self.session.get(url, params=params, timeout=10)
            data = resp.json()
            return self._parse_fund_flow(data.get("data", {}).get("diff", []))
        except Exception as e:
            raise DataSourceError(f"获取资金流向失败: {e}")

    def _parse_stock_list(self, items: list) -> list[dict[str, Any]]:
        result = []
        for item in items:
            result.append({
                "code": item.get("f12", ""),
                "name": item.get("f14", ""),
                "price": item.get("f2", 0),
                "change_pct": item.get("f3", 0),
                "volume": item.get("f5", 0),
                "amount": item.get("f6", 0),
            })
        return result

    def _parse_sector_list(self, items: list) -> list[dict[str, Any]]:
        result = []
        for item in items:
            result.append({
                "name": item.get("f14", ""),
                "change_pct": item.get("f3", 0),
                "volume": item.get("f5", 0),
                "turnover_rate": item.get("f2", 0),
            })
        return result

    def _parse_fund_flow(self, items: list) -> list[dict[str, Any]]:
        result = []
        for item in items:
            result.append({
                "code": item.get("f12", ""),
                "name": item.get("f14", ""),
                "net_inflow": item.get("f10", 0),
                "inflow_rate": item.get("f3", 0),
            })
        return result