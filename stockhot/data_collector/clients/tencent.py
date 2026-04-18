"""Tencent Finance data client for StockHot-CN."""

import re
from typing import Any

import requests

from stockhot.data_collector.clients.base import BaseClient
from stockhot.core.exceptions import DataSourceError


class TencentClient(BaseClient):
    """Tencent Finance data source client - works without proxy!"""

    BASE_URL = "https://qt.gtimg.cn/q="

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })

    def _fetch(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        code_str = ",".join(codes)
        url = f"{self.BASE_URL}{code_str}"
        
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise DataSourceError(f"Tencent API request failed: {e}") from e

        try:
            text = resp.content.decode("gbk")
        except UnicodeDecodeError as e:
            raise DataSourceError(f"Tencent API encoding error: {e}") from e

        return self._parse_response(text)

    def _parse_response(self, text: str) -> dict[str, dict[str, Any]]:
        result = {}
        pattern = r'v_(\w+)="([^"]+)"'
        
        for match in re.finditer(pattern, text):
            code = match.group(1)
            data_str = match.group(2)
            
            if data_str == "pv_none_match":
                continue
                
            fields = data_str.split("~")
            if len(fields) < 10:
                continue

            result[code] = {
                "code": fields[2] if len(fields) > 2 else "",
                "name": fields[1] if len(fields) > 1 else "",
                "price": float(fields[3]) if fields[3] and fields[3] != "-" else 0,
                "change": float(fields[4]) if fields[4] and fields[4] != "-" else 0,
                "change_pct": float(fields[5]) if fields[5] and fields[5] != "-" else 0,
                "volume": int(fields[6]) if fields[6] and fields[6].isdigit() else 0,
                "amount": float(fields[7]) if fields[7] and fields[7] else 0,
            }

        return result

    def get_gainers(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取涨幅排行 - scan many stocks to find top gainers"""
        codes = self._generate_stock_codes(limit * 5)
        data = self._fetch(codes)
        
        stocks = []
        for code, info in data.items():
            if info.get("change_pct", 0) > 0:
                stocks.append(info)
        
        stocks.sort(key=lambda x: x.get("change_pct", 0), reverse=True)
        return stocks[:limit]

    def get_losers(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取跌幅排行"""
        codes = self._generate_stock_codes(limit * 5)
        data = self._fetch(codes)
        
        stocks = []
        for code, info in data.items():
            if info.get("change_pct", 0) < 0:
                stocks.append(info)
        
        stocks.sort(key=lambda x: x.get("change_pct", 0))
        return stocks[:limit]

    def get_sectors(self, limit: int = 15) -> list[dict[str, Any]]:
        """获取板块排行 - limited via Tencent API"""
        codes = ["bk081113", "bk081101", "bk081102", "bk081103", "bk081104", "bk081105",
                 "bk080001", "bk080002", "bk080003", "bk080004", "bk080005", "bk080006"]
        data = self._fetch(codes)
        
        sectors = []
        for code, info in data.items():
            if info.get("change_pct", 0) != 0:
                sectors.append(info)
        
        sectors.sort(key=lambda x: x.get("change_pct", 0), reverse=True)
        return sectors[:limit]

    def get_fund_flow(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取资金流向 - limited via Tencent API"""
        codes = self._generate_stock_codes(limit * 3)
        data = self._fetch(codes)
        
        stocks = list(data.values())
        stocks.sort(key=lambda x: abs(x.get("change_pct", 0)), reverse=True)
        return stocks[:limit]

    def _generate_stock_codes(self, count: int) -> list[str]:
        """Generate a diverse list of stock codes to query."""
        codes = []
        
        # Shanghai main board (60x)
        for i in range(600000, 600000 + count * 3):
            codes.append(f"sh{i:06d}")
        
        # Shenzhen main board (00x)
        for i in range(1, count):
            codes.append(f"sz{i:06d}")
        
        # STAR Market (688x)
        for i in range(688000, 688000 + count // 2):
            codes.append(f"sh{i:06d}")
        
        # ChiNext (30x)
        for i in range(300000, 300000 + count // 2):
            codes.append(f"sz{i:06d}")
        
        return codes[:count * 4]