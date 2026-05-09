"""Tonghuashun public-web client.

This client remains read-only and is now used by the main pipeline for
fund-flow samples while still supporting supplemental THS lookups.
"""

from io import StringIO
import importlib
import math
from typing import Any

import requests

from stockhot.core.exceptions import DataParseError, DataSourceError
from stockhot.core.utils import safe_text


class THSClient:
    """THS client for public board and fund-flow pages."""

    INDUSTRY_BOARD_URL = "https://q.10jqka.com.cn/thshy/"
    INDUSTRY_FUND_FLOW_URL = "https://data.10jqka.com.cn/funds/hyzjl/"
    CONCEPT_FUND_FLOW_URL = "https://data.10jqka.com.cn/funds/gnzjl/"
    BOARD_SCHEMA = {
        "name": ["板块", "行业", "概念名称", "名称"],
        "change_pct": ["涨跌幅(%)", "涨跌幅", "涨幅"],
        "amount": ["总成交额（亿元）", "总成交额", "成交额", "成交金额"],
        "company_count": ["公司家数", "成分股数量", "家数"],
        "rising_count": ["上涨家数"],
        "falling_count": ["下跌家数"],
        "flat_count": ["平盘家数", "平家数", "持平家数", "平盘数"],
        "leader_stock": ["领涨股", "股票名称", "龙头股"],
    }
    FUND_FLOW_SCHEMA = {
        "name": ["行业", "概念", "板块", "名称"],
        "change_pct": ["涨跌幅", "阶段涨跌幅", "今日涨跌幅"],
        "inflow": ["流入资金(亿)", "流入资金", "主力流入净额", "流入", "净流入"],
        "outflow": ["流出资金(亿)", "流出资金", "主力流出净额", "流出"],
        "net_inflow": ["净额(亿)", "净额", "净流入(亿)", "净流入"],
        "leader_stock": ["领涨股", "领涨股票", "股票名称"],
        "leader_change_pct": ["涨跌幅.1", "领涨股涨跌幅", "领涨股票-涨跌幅", "个股涨跌幅"],
    }

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://q.10jqka.com.cn/",
            }
        )

    def get_industry_boards(self, limit: int = 20) -> list[dict[str, Any]]:
        df = self._read_table(
            self.INDUSTRY_BOARD_URL,
            self.BOARD_SCHEMA,
            required_keys=[
                "name",
                "change_pct",
                "amount",
                "rising_count",
                "falling_count",
                "leader_stock",
            ],
        )
        return self._normalize_board_rows(df, limit=limit)

    def get_industry_fund_flow(self, limit: int = 20) -> list[dict[str, Any]]:
        df = self._read_table(
            self.INDUSTRY_FUND_FLOW_URL,
            self.FUND_FLOW_SCHEMA,
            required_keys=["name", "change_pct", "net_inflow", "leader_stock"],
        )
        return self._normalize_fund_flow_rows(df, limit=limit)

    def get_concept_fund_flow(self, limit: int = 20) -> list[dict[str, Any]]:
        df = self._read_table(
            self.CONCEPT_FUND_FLOW_URL,
            self.FUND_FLOW_SCHEMA,
            required_keys=["name", "change_pct", "net_inflow", "leader_stock"],
        )
        return self._normalize_fund_flow_rows(df, limit=limit)

    def _read_table(self, url: str, schema: dict[str, list[str]], required_keys: list[str]) -> Any:
        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise DataSourceError(f"THS request failed: {exc}") from exc

        if not response.text:
            raise DataSourceError("THS response was empty")

        try:
            pd = importlib.import_module("pandas")
            tables = pd.read_html(StringIO(response.text))
        except (ModuleNotFoundError, ImportError, ValueError) as exc:
            raise DataParseError(f"THS page contained no parseable tables: {exc}") from exc
        except Exception as exc:
            raise DataParseError(f"THS table parsing failed: {exc}") from exc

        if not tables:
            raise DataParseError("THS page contained zero tables")

        for table in tables:
            columns = self._normalized_columns(table)
            missing = self._missing_required_columns(columns, schema, required_keys)
            if not missing:
                return table

        available = [list(self._normalized_columns(table).keys()) for table in tables[:3]]
        raise DataParseError(
            f"THS page did not contain a table with required columns {required_keys}; sample columns: {available}"
        )

    def _normalize_board_rows(self, df: Any, limit: int) -> list[dict[str, Any]]:
        resolved = self._resolve_columns(
            df, self.BOARD_SCHEMA, required_keys=["name", "change_pct", "amount", "leader_stock"]
        )

        has_total_count = resolved.get("company_count") is not None
        has_split_count = (
            resolved.get("rising_count") is not None and resolved.get("falling_count") is not None
        )
        if not has_total_count and not has_split_count:
            raise DataParseError(
                "THS board table missing company count information; expected company_count or rising/falling counts"
            )

        rows: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            name = safe_text(row.get(resolved["name"]))
            if not name:
                continue
            rising_count = (
                self._parse_int(row.get(resolved["rising_count"]), "rising_count")
                if resolved.get("rising_count") is not None
                else 0
            )
            falling_count = (
                self._parse_int(row.get(resolved["falling_count"]), "falling_count")
                if resolved.get("falling_count") is not None
                else 0
            )
            flat_count = (
                self._parse_int(row.get(resolved["flat_count"]), "flat_count")
                if resolved.get("flat_count") is not None
                else 0
            )
            company_count = (
                self._parse_int(row.get(resolved["company_count"]), "company_count")
                if resolved.get("company_count") is not None
                else rising_count + falling_count + flat_count
            )
            rows.append(
                {
                    "name": name,
                    "change_pct": self._parse_float(row.get(resolved["change_pct"]), "change_pct"),
                    "amount": self._parse_float(row.get(resolved["amount"]), "amount"),
                    "company_count": company_count,
                    "rising_count": rising_count,
                    "falling_count": falling_count,
                    "flat_count": flat_count,
                    "leader_stock": safe_text(row.get(resolved["leader_stock"])),
                }
            )

        rows.sort(key=lambda item: item.get("change_pct", 0), reverse=True)
        return rows[:limit]

    def _normalize_fund_flow_rows(self, df: Any, limit: int) -> list[dict[str, Any]]:
        resolved = self._resolve_columns(
            df,
            self.FUND_FLOW_SCHEMA,
            required_keys=["name", "change_pct", "net_inflow", "leader_stock"],
        )

        rows: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            name = safe_text(row.get(resolved["name"]))
            if not name:
                continue
            rows.append(
                {
                    "name": name,
                    "change_pct": self._parse_float(row.get(resolved["change_pct"]), "change_pct"),
                    "net_inflow": self._parse_float(row.get(resolved["net_inflow"]), "net_inflow"),
                    "inflow": self._parse_float(row.get(resolved.get("inflow")), "inflow")
                    if resolved.get("inflow") is not None
                    else 0.0,
                    "outflow": self._parse_float(row.get(resolved.get("outflow")), "outflow")
                    if resolved.get("outflow") is not None
                    else 0.0,
                    "leader_stock": safe_text(row.get(resolved["leader_stock"])),
                    "leader_change_pct": self._parse_float(
                        row.get(resolved.get("leader_change_pct")), "leader_change_pct"
                    )
                    if resolved.get("leader_change_pct") is not None
                    else 0.0,
                }
            )

        rows.sort(key=lambda item: item.get("net_inflow", 0), reverse=True)
        return rows[:limit]

    @staticmethod
    def _normalized_columns(df: Any) -> dict[str, Any]:
        return {str(col).strip(): col for col in df.columns}

    def _resolve_columns(
        self,
        df: Any,
        schema: dict[str, list[str]],
        required_keys: list[str],
    ) -> dict[str, Any]:
        columns = self._normalized_columns(df)
        resolved: dict[str, Any] = {}
        missing: list[str] = []
        for key, candidates in schema.items():
            actual = next(
                (columns[candidate] for candidate in candidates if candidate in columns), None
            )
            if actual is None and key in required_keys:
                missing.append(key)
            resolved[key] = actual
        if missing:
            raise DataParseError(
                f"THS table missing required columns: {missing}; available={list(columns.keys())}"
            )
        return resolved

    def _missing_required_columns(
        self,
        columns: dict[str, Any],
        schema: dict[str, list[str]],
        required_keys: list[str],
    ) -> list[str]:
        missing: list[str] = []
        for key in required_keys:
            candidates = schema.get(key, [])
            if not any(candidate in columns for candidate in candidates):
                missing.append(key)
        return missing

    @staticmethod
    def _parse_float(value: Any, field_name: str) -> float:
        if isinstance(value, (int, float)):
            if isinstance(value, float) and math.isnan(value):
                return 0.0
            return float(value)
        if value in (None, "", "-", "--"):
            return 0.0
        text = str(value).strip().replace(",", "")
        if not text or text.lower() == "nan":
            return 0.0

        scale = 1.0
        if "万" in text and "亿" not in text:
            scale = 1 / 10000

        text = text.replace("亿", "").replace("万", "")
        text = text.replace("%", "")
        try:
            return float(text) * scale
        except ValueError:
            raise DataParseError(f"THS invalid numeric value for {field_name}: {value!r}")

    def _parse_int(self, value: Any, field_name: str) -> int:
        if isinstance(value, bool):
            raise DataParseError(f"THS invalid integer value for {field_name}: {value!r}")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if math.isnan(value):
                return 0
            if not value.is_integer():
                raise DataParseError(f"THS invalid integer value for {field_name}: {value!r}")
            return int(value)
        if value in (None, "", "-", "--"):
            return 0
        text = str(value).strip().replace(",", "")
        if not text or text.lower() == "nan":
            return 0
        if any(unit in text for unit in ("亿", "万", "%")):
            raise DataParseError(f"THS invalid integer value for {field_name}: {value!r}")
        try:
            numeric = float(text)
        except ValueError as exc:
            raise DataParseError(f"THS invalid integer value for {field_name}: {value!r}") from exc
        if not numeric.is_integer():
            raise DataParseError(f"THS invalid integer value for {field_name}: {value!r}")
        return int(numeric)
