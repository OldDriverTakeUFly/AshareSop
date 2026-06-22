"""EastMoney data client for StockHot-CN."""

import json
import urllib.request
import urllib.parse
from typing import Any
from stockhot.data_collector.clients.base import BaseClient
from stockhot.core.exceptions import DataSourceError


class EastMoneyClient(BaseClient):
    """EastMoney data source client."""

    BASE_URL = "https://push2.eastmoney.com"

    def __init__(self):
        import os

        for k in [
            "http_proxy",
            "https_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "all_proxy",
            "no_proxy",
            "NO_PROXY",
        ]:
            os.environ.pop(k, None)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.opener.addheaders = [
            (
                "User-Agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            ),
            ("Referer", "https://quote.eastmoney.com/"),
        ]

    def _fetch(self, url: str, params: dict) -> dict:
        full_url = f"{url}?{urllib.parse.urlencode(params)}"
        resp = self.opener.open(full_url, timeout=10)
        return json.loads(resp.read().decode("utf-8"))

    def get_gainers(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取涨幅排行"""
        try:
            url = f"{self.BASE_URL}/api/qt/clist/get"
            params = {
                "pn": 1,
                "pz": limit * 3,
                "po": 1,
                "np": 1,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2",
                "fields": "f2,f3,f4,f5,f6,f12,f14",
            }
            data = self._fetch(url, params)
            results = self._parse_stock_list(data.get("data", {}).get("diff", []))
            return [s for s in results if not s.get("name", "").startswith("N")][:limit]
        except Exception as e:
            raise DataSourceError(f"获取涨幅排行失败: {e}")

    def get_losers(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取跌幅排行"""
        try:
            url = f"{self.BASE_URL}/api/qt/clist/get"
            params = {
                "pn": 1,
                "pz": limit * 3,
                "po": 0,
                "np": 1,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2",
                "fields": "f2,f3,f4,f5,f6,f12,f14",
            }
            data = self._fetch(url, params)
            results = self._parse_stock_list(data.get("data", {}).get("diff", []))
            return [s for s in results if not s.get("name", "").startswith("N")][:limit]
        except Exception as e:
            raise DataSourceError(f"获取跌幅排行失败: {e}")

    def get_sectors(self, limit: int = 15) -> list[dict[str, Any]]:
        """获取板块排行"""
        try:
            url = f"{self.BASE_URL}/api/qt/clist/get"
            params = {
                "pn": 1,
                "pz": limit,
                "po": 1,
                "np": 1,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": "b:MK0021",
                "fields": "f2,f3,f4,f5,f12,f14",
            }
            data = self._fetch(url, params)
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
                "po": 1,
                "np": 1,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2,
                "invt": 2,
                "fid": "f10",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2",
                "fields": "f2,f3,f10,f12,f14",
            }
            data = self._fetch(url, params)
            return self._parse_fund_flow(data.get("data", {}).get("diff", []))
        except Exception as e:
            raise DataSourceError(f"获取资金流向失败: {e}")

    def _parse_stock_list(self, items: list) -> list[dict[str, Any]]:
        result = []
        for item in items:
            result.append(
                {
                    "code": item.get("f12", ""),
                    "name": item.get("f14", ""),
                    "price": item.get("f2", 0),
                    "change_pct": item.get("f3", 0),
                    "volume": item.get("f5", 0),
                    "amount": item.get("f6", 0),
                }
            )
        return result

    def _parse_sector_list(self, items: list) -> list[dict[str, Any]]:
        result = []
        for item in items:
            result.append(
                {
                    "name": item.get("f14", ""),
                    "change_pct": item.get("f3", 0),
                    "volume": item.get("f5", 0),
                    "turnover_rate": item.get("f2", 0),
                }
            )
        return result

    def _parse_fund_flow(self, items: list) -> list[dict[str, Any]]:
        result = []
        for item in items:
            result.append(
                {
                    "code": item.get("f12", ""),
                    "name": item.get("f14", ""),
                    "net_inflow": item.get("f10", 0),
                    "inflow_rate": item.get("f3", 0),
                }
            )
        return result
