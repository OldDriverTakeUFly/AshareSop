#!/usr/bin/env python3
"""AKShare endpoint verification script for invest_sop data collection.

Tests all AKShare endpoints needed for the invest_sop project,
outputting PASS/FAIL for each along with return field information.

Usage:
    PYTHONPATH=/home/leo/Projects/CodeAgentDashboard python3 stockhot/invest_sop/scripts/akshare_poc.py
"""

import json
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Proxy stripping – mirrors stockhot/data_collector/clients/akshare_sina.py
# ---------------------------------------------------------------------------
PROXY_KEYS = [
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "all_proxy",
]


def _strip_proxies() -> dict[str, str]:
    removed: dict[str, str] = {}
    for key in PROXY_KEYS:
        if key in os.environ:
            removed[key] = os.environ.pop(key)
    return removed


def _restore_proxies(removed: dict[str, str]) -> None:
    os.environ.update(removed)


def call_akshare(method_name: str, **kwargs: Any) -> Any:
    """Call an akshare method with proxy env vars stripped."""
    removed = _strip_proxies()
    try:
        import akshare as ak

        method = getattr(ak, method_name)
        return method(**kwargs)
    finally:
        _restore_proxies(removed)


# ---------------------------------------------------------------------------
# Result data class
# ---------------------------------------------------------------------------
@dataclass
class EndpointResult:
    category: str
    endpoint_name: str
    function_name: str
    params: str
    status: str  # PASS / FAIL / SKIP
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    sample_row: Optional[str] = None
    error: Optional[str] = None
    notes: str = ""
    latency_ms: int = 0


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
def _safe_str(val: Any, max_len: int = 200) -> str:
    s = str(val)
    return s[:max_len] + "..." if len(s) > max_len else s


def test_endpoint(
    category: str,
    endpoint_name: str,
    function_name: str,
    params: dict[str, Any] | None = None,
    notes: str = "",
) -> EndpointResult:
    """Test a single AKShare endpoint and record results."""
    params = params or {}
    params_str = ", ".join(f"{k}={v!r}" for k, v in params.items())

    print(f"\n  Testing: {function_name}({params_str}) ... ", end="", flush=True)
    result = EndpointResult(
        category=category,
        endpoint_name=endpoint_name,
        function_name=function_name,
        params=params_str,
        status="FAIL",
        notes=notes,
    )

    t0 = time.monotonic()
    try:
        df = call_akshare(function_name, **params)
        elapsed = int((time.monotonic() - t0) * 1000)
        result.latency_ms = elapsed

        if df is None:
            result.status = "FAIL"
            result.error = "Returned None"
            print(f"FAIL (returned None) [{elapsed}ms]")
            return result

        if not hasattr(df, "columns"):
            result.status = "PASS"
            result.row_count = 0
            result.sample_row = _safe_str(df)
            result.columns = ["<non-DataFrame>"]
            print(f"PASS (non-DataFrame, type={type(df).__name__}) [{elapsed}ms]")
            return result

        result.columns = list(df.columns)
        result.row_count = len(df)
        if len(df) > 0:
            result.sample_row = _safe_str(df.iloc[0].to_dict(), max_len=300)

        result.status = "PASS"
        print(f"PASS ({result.row_count} rows) [{elapsed}ms]")

    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        result.latency_ms = elapsed
        result.error = f"{type(exc).__name__}: {exc}"
        print(f"FAIL ({type(exc).__name__}: {exc}) [{elapsed}ms]")

    return result


# ---------------------------------------------------------------------------
# Helper: recent date strings
# ---------------------------------------------------------------------------
def _recent_date(fmt: str = "%Y%m%d", days_back: int = 7) -> str:
    return (datetime.now() - timedelta(days=days_back)).strftime(fmt)


def _today(fmt: str = "%Y%m%d") -> str:
    return datetime.now().strftime(fmt)


# ---------------------------------------------------------------------------
# Endpoint definitions by category
# ---------------------------------------------------------------------------
def get_endpoints() -> list[dict[str, Any]]:
    """Return all endpoint test definitions."""
    endpoints: list[dict[str, Any]] = []

    # =======================================================================
    # Category 1: 海外市场 (Overseas Market)
    # =======================================================================
    cat = "1_海外市场_Overseas_Market"

    endpoints.append(dict(
        category=cat, endpoint_name="US Stock Indices (S&P500, Nasdaq, Dow)",
        function_name="index_us_stock_sina", params={},
        notes="Returns US stock index data from Sina",
    ))

    endpoints.append(dict(
        category=cat, endpoint_name="Global Indices Spot (US/EU/Asia)",
        function_name="index_global_spot_em", params={},
        notes="East Money global index real-time quotes, includes S&P500/Nasdaq/Dow",
    ))

    endpoints.append(dict(
        category=cat, endpoint_name="Global Indices Hist (S&P500)",
        function_name="index_global_hist_em",
        params={"symbol": "道琼斯"},
        notes="Historical global index from East Money",
    ))

    # VIX – use China VIX as proxy (US VIX not directly available)
    endpoints.append(dict(
        category=cat, endpoint_name="China VIX (50ETF QVIX)",
        function_name="index_option_50etf_qvix", params={},
        notes="Chinese option implied volatility index, similar to VIX concept",
    ))

    # A50 futures (symbol code from Sina)
    endpoints.append(dict(
        category=cat, endpoint_name="A50 Futures (foreign)",
        function_name="futures_foreign_hist",
        params={"symbol": "CHA50CFD"},
        notes="SGX A50 futures CFD historical data. Symbol: CHA50CFD",
    ))

    # USD/CNY exchange rate
    endpoints.append(dict(
        category=cat, endpoint_name="Forex Spot (USD/CNY)",
        function_name="forex_spot_em", params={},
        notes="East Money forex spot rates, includes USD/CNY",
    ))

    endpoints.append(dict(
        category=cat, endpoint_name="Forex Hist (USDCNH)",
        function_name="forex_hist_em",
        params={"symbol": "USDCNH"},
        notes="Historical USD/CNH from East Money (signature: only takes symbol param)",
    ))

    endpoints.append(dict(
        category=cat, endpoint_name="BOC Exchange Rate",
        function_name="currency_boc_sina",
        params={"symbol": "美元", "start_date": _recent_date("%Y%m%d", 30), "end_date": _today("%Y%m%d")},
        notes="PBOC reference rate for USD from Sina",
    ))

    # US 10Y treasury yield
    endpoints.append(dict(
        category=cat, endpoint_name="US Treasury Yield (bond_zh_us_rate)",
        function_name="bond_zh_us_rate",
        params={"start_date": _recent_date("%Y%m%d", 30)},
        notes="China-US bond yield comparison, includes US 10Y",
    ))

    # US stock spot data
    endpoints.append(dict(
        category=cat, endpoint_name="US Stock Spot",
        function_name="stock_us_spot_em", params={},
        notes="East Money US stock real-time quotes",
    ))

    # =======================================================================
    # Category 2: 期货数据 (Futures)
    # =======================================================================
    cat = "2_期货数据_Futures"

    # IF index futures
    endpoints.append(dict(
        category=cat, endpoint_name="Futures Main Sina (IF)",
        function_name="futures_main_sina",
        params={"symbol": "IF0"},
        notes="IF (CSI300) main contract continuous from Sina. Use IF0/IC0/IM0",
    ))

    endpoints.append(dict(
        category=cat, endpoint_name="Futures Main Sina (IC)",
        function_name="futures_main_sina",
        params={"symbol": "IC0"},
        notes="IC (CSI500) main contract continuous",
    ))

    endpoints.append(dict(
        category=cat, endpoint_name="Futures Main Sina (IM)",
        function_name="futures_main_sina",
        params={"symbol": "IM0"},
        notes="IM (CSI1000) main contract continuous",
    ))

    endpoints.append(dict(
        category=cat, endpoint_name="Futures Spot Price (IF/IC/IM)",
        function_name="futures_spot_price",
        params={"date": _recent_date("%Y%m%d", 3), "vars_list": ["IF", "IC", "IM", "IH"]},
        notes="Futures spot price with basis info for index futures",
    ))

    endpoints.append(dict(
        category=cat, endpoint_name="CSI300 Index Daily",
        function_name="stock_zh_index_daily_em",
        params={"symbol": "sh000300"},
        notes="CSI300 index daily data from East Money",
    ))

    endpoints.append(dict(
        category=cat, endpoint_name="Futures ZH Realtime",
        function_name="futures_zh_realtime", params={},
        notes="Chinese futures realtime quotes",
    ))

    # =======================================================================
    # Category 3: 资金面 (Capital Flow)
    # =======================================================================
    cat = "3_资金面_Capital_Flow"

    # Northbound capital
    endpoints.append(dict(
        category=cat, endpoint_name="Northbound Capital Hist",
        function_name="stock_hsgt_hist_em",
        params={"symbol": "北向资金"},
        notes="Northbound capital flow history from East Money",
    ))

    endpoints.append(dict(
        category=cat, endpoint_name="HSGT Fund Flow Summary",
        function_name="stock_hsgt_fund_flow_summary_em", params={},
        notes="Summary of northbound/southbound fund flows",
    ))

    # Margin balance
    endpoints.append(dict(
        category=cat, endpoint_name="SSE Margin Detail",
        function_name="stock_margin_detail_sse",
        params={"date": _recent_date("%Y%m%d", 5)},
        notes="Shanghai margin trading detail",
    ))

    endpoints.append(dict(
        category=cat, endpoint_name="SSE Margin Summary",
        function_name="stock_margin_sse",
        params={"start_date": _recent_date("%Y%m%d", 10), "end_date": _today("%Y%m%d")},
        notes="Shanghai margin balance summary",
    ))

    endpoints.append(dict(
        category=cat, endpoint_name="SZSE Margin Underlying Info",
        function_name="stock_margin_underlying_info_szse",
        params={"date": _recent_date("%Y%m%d", 5)},
        notes="Shenzhen margin underlying securities info",
    ))

    # =======================================================================
    # Category 4: 产业链 (Supply Chain / Commodities)
    # =======================================================================
    cat = "4_产业链_Supply_Chain"

    # LME metals (Sina symbol codes: CAD=伦敦铜, AHD=伦敦铝, ZSD=伦敦锌)
    for metal_name, symbol in [("LME Copper", "CAD"), ("LME Aluminum", "AHD"), ("LME Zinc", "ZSD")]:
        endpoints.append(dict(
            category=cat, endpoint_name=f"Foreign Futures Hist ({metal_name})",
            function_name="futures_foreign_hist",
            params={"symbol": symbol},
            notes=f"LME {metal_name} CFD historical data (Sina symbol: {symbol})",
        ))

    endpoints.append(dict(
        category=cat, endpoint_name="Foreign Commodity Realtime (LME metals)",
        function_name="futures_foreign_commodity_realtime",
        params={"symbol": "CAD"},
        notes="Realtime LME Copper quote (single symbol, list has parsing bug)",
    ))

    # Coal (spot_goods only accepts: 波罗的海干散货指数, 钢坯价格指数, 澳大利亚粉矿价格)
    endpoints.append(dict(
        category=cat, endpoint_name="Spot Goods (BDI Index)",
        function_name="spot_goods",
        params={"symbol": "波罗的海干散货指数"},
        notes="Baltic Dry Index – only 3 symbols accepted by spot_goods",
    ))

    # CFLP price index (commodity price indices)
    endpoints.append(dict(
        category=cat, endpoint_name="CFLP Commodity Price Index",
        function_name="index_price_cflp",
        params={"symbol": "周指数"},
        notes="China Federation of Logistics commodity price index, may include lithium/solar materials",
    ))

    # Futures spot price for commodities
    endpoints.append(dict(
        category=cat, endpoint_name="Futures Spot Price (commodities)",
        function_name="futures_spot_price",
        params={"date": _recent_date("%Y%m%d", 3), "vars_list": ["CU", "AL", "ZN", "J", "JM", "I", "RB"]},
        notes="Commodity futures spot prices: Cu/Al/Zn/coal/iron/steel",
    ))

    # Spot goods - lithium NOT available via spot_goods, note the limitation
    endpoints.append(dict(
        category=cat, endpoint_name="Spot Goods (钢坯/HRC)",
        function_name="spot_goods",
        params={"symbol": "钢坯价格指数"},
        notes="Steel billet price index. Lithium/solar NOT available via spot_goods",
    ))

    # Energy
    endpoints.append(dict(
        category=cat, endpoint_name="Energy Oil Hist",
        function_name="energy_oil_hist", params={},
        notes="Historical oil price data",
    ))

    # =======================================================================
    # Category 5: 日历 (Calendar)
    # =======================================================================
    cat = "5_日历_Calendar"

    # Trade calendar – ALREADY CONFIRMED
    endpoints.append(dict(
        category=cat, endpoint_name="Trade Date History (Sina)",
        function_name="tool_trade_date_hist_sina", params={},
        notes="CONFIRMED WORKING – A-share trade calendar from Sina",
    ))

    # Restricted shares release
    endpoints.append(dict(
        category=cat, endpoint_name="Restricted Release Queue (Sina)",
        function_name="stock_restricted_release_queue_sina",
        params={"symbol": "600000"},
        notes="Restricted shares release schedule for a sample stock",
    ))

    endpoints.append(dict(
        category=cat, endpoint_name="Restricted Release Queue (EM)",
        function_name="stock_restricted_release_queue_em",
        params={"symbol": "600000"},
        notes="Restricted shares release schedule from East Money",
    ))

    endpoints.append(dict(
        category=cat, endpoint_name="Restricted Release Summary (EM)",
        function_name="stock_restricted_release_summary_em", params={},
        notes="Aggregate restricted shares release summary",
    ))

    # Macro calendar – use specific macro functions
    endpoints.append(dict(
        category=cat, endpoint_name="LPR Rate",
        function_name="macro_china_lpr", params={},
        notes="Loan Prime Rate – key economic indicator calendar item",
    ))

    # Global news / events
    endpoints.append(dict(
        category=cat, endpoint_name="Global Market Info (CLS)",
        function_name="stock_info_global_cls", params={},
        notes="CLS global financial news/events feed",
    ))

    return endpoints


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------
def main() -> None:
    removed = _strip_proxies()
    try:
        import akshare as ak

        version = ak.__version__
    finally:
        _restore_proxies(removed)

    print("=" * 80)
    print(f"AKShare Endpoint Verification Script")
    print(f"AKShare version: {version}")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Python: {sys.version.split()[0]}")
    print("=" * 80)

    endpoints = get_endpoints()
    results: list[EndpointResult] = []

    categories_seen: list[str] = []
    for ep in endpoints:
        if ep["category"] not in categories_seen:
            categories_seen.append(ep["category"])
            print(f"\n{'─' * 80}")
            print(f"  {ep['category']}")
            print(f"{'─' * 80}")

        result = test_endpoint(
            category=ep["category"],
            endpoint_name=ep["endpoint_name"],
            function_name=ep["function_name"],
            params=ep.get("params", {}),
            notes=ep.get("notes", ""),
        )
        results.append(result)

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")

    print(f"\n{'=' * 80}")
    print(f"SUMMARY: {passed}/{total} passed, {failed} failed")
    print("=" * 80)

    # Print failed endpoints
    if failed > 0:
        print("\nFAILED ENDPOINTS:")
        for r in results:
            if r.status == "FAIL":
                print(f"  ✗ {r.endpoint_name}: {r.function_name}({r.params})")
                if r.error:
                    print(f"    Error: {r.error}")

    # Output JSON results for markdown generation
    json_path = os.path.join(os.path.dirname(__file__), "..", "akshare_poc_results.json")
    json_path = os.path.abspath(json_path)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {"version": version, "run_date": datetime.now().isoformat(), "results": [asdict(r) for r in results]},
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\nResults saved to: {json_path}")


if __name__ == "__main__":
    main()
