"""AkShare Sina-backed data client for StockHot-CN."""

import importlib
import os
from typing import Any

from stockhot.core.exceptions import DataSourceError
from stockhot.core.utils import safe_float, safe_optional_float, safe_text
from stockhot.data_collector.clients.base import BaseClient
from stockhot.data_collector.clients.tencent import TencentClient
from stockhot.data_collector.clients.ths import THSClient


class AkshareSinaClient(BaseClient):
    """AkShare client using Sina-backed interfaces for spot and sector data."""

    def __init__(self):
        self.tencent_fallback = TencentClient()
        self.ths_supplement = THSClient()

    def get_gainers(self, limit: int = 20) -> list[dict[str, Any]]:
        try:
            stocks = self._get_stock_spot()
            stocks.sort(key=lambda item: item.get("change_pct", 0), reverse=True)
            return stocks[:limit]
        except DataSourceError:
            return self.tencent_fallback.get_gainers(limit)

    def get_losers(self, limit: int = 20) -> list[dict[str, Any]]:
        try:
            stocks = self._get_stock_spot()
            stocks.sort(key=lambda item: item.get("change_pct", 0))
            return stocks[:limit]
        except DataSourceError:
            return self.tencent_fallback.get_losers(limit)

    def get_sectors(self, limit: int = 15) -> list[dict[str, Any]]:
        try:
            df = self._call_akshare("stock_sector_spot", indicator="行业")
        except Exception as exc:
            raise DataSourceError(f"AkShare sector spot failed: {exc}") from exc

        sectors: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            leader_stock = safe_text(row.get("股票名称"))
            sectors.append(
                {
                    "name": str(row.get("板块", "")),
                    "change_pct": safe_float(row.get("涨跌幅")),
                    "company_count": int(safe_float(row.get("公司家数"))),
                    "amount": safe_float(row.get("总成交额")),
                    "leader_stock": leader_stock,
                }
            )

        sectors.sort(key=lambda item: item.get("change_pct", 0), reverse=True)
        return sectors[:limit]

    def get_fund_flow(self, limit: int = 10) -> list[dict[str, Any]]:
        """Use THS industry fund-flow first, then concept fund-flow as fallback."""
        try:
            rows = self.ths_supplement.get_industry_fund_flow(limit)
            return [self._normalize_ths_fund_flow(item, category="industry") for item in rows]
        except Exception:
            try:
                rows = self.ths_supplement.get_concept_fund_flow(limit)
                return [self._normalize_ths_fund_flow(item, category="concept") for item in rows]
            except Exception:
                return []

    def _get_stock_spot(self) -> list[dict[str, Any]]:
        try:
            df = self._call_akshare("stock_zh_a_spot", extended=True)
        except Exception as exc:
            raise DataSourceError(f"AkShare stock spot failed: {exc}") from exc

        stocks: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            code = safe_text(self._get_row_value(row, "代码"))
            name = safe_text(self._get_row_value(row, "名称"))
            if not code or not name:
                continue
            stocks.append(
                {
                    "code": code,
                    "name": name,
                    "price": safe_float(self._get_row_value(row, "最新价")),
                    "change_pct": safe_float(self._get_row_value(row, "涨跌幅")),
                    "volume": safe_float(self._get_row_value(row, "成交量")),
                    "amount": safe_float(self._get_row_value(row, "成交额")),
                    "turnover_rate": safe_optional_float(self._get_row_value(row, "换手率")),
                    "total_market_value": safe_optional_float(self._get_row_value(row, "总市值")),
                    "circulating_market_value": safe_optional_float(
                        self._get_row_value(row, "流通市值")
                    ),
                }
            )
        return stocks

    @staticmethod
    def _get_row_value(row: Any, *keys: str) -> Any:
        for key in keys:
            value = row.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if str(value).strip().lower() == "nan":
                continue
            return value
        return None

    @staticmethod
    def _akshare():
        return importlib.import_module("akshare")

    def _call_akshare(self, method_name: str, **kwargs):
        removed: dict[str, str] = {}
        proxy_keys = [
            "http_proxy",
            "https_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "all_proxy",
        ]
        for key in proxy_keys:
            if key in os.environ:
                removed[key] = os.environ.pop(key)

        try:
            ak = self._akshare()
            method = getattr(ak, method_name)
            return method(**kwargs)
        finally:
            os.environ.update(removed)

    @staticmethod
    def _normalize_ths_fund_flow(item: dict[str, Any], category: str) -> dict[str, Any]:
        return {
            "code": "",
            "name": item.get("name", ""),
            "net_inflow": item.get("net_inflow"),
            "board_change_pct": item.get("change_pct"),
            "inflow": item.get("inflow"),
            "outflow": item.get("outflow"),
            "leader_stock": item.get("leader_stock", ""),
            "leader_change_pct": item.get("leader_change_pct"),
            "category": category,
            "source": "ths",
        }
