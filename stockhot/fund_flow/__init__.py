"""Fund flow trends module for StockHot-CN.

Fetches market-wide, sector, and individual fund-flow data from AkShare,
analyses multi-day trends (direction, momentum, large-vs-retail divergence),
and persists results via the storage layer.
"""

from __future__ import annotations

import os
import re

import akshare as ak

from stockhot.core.logging import logger
from stockhot.core.rate_limiter import akshare_limiter, safe_akshare_call
from stockhot.core.utils import (
    from_akshare_date,
    safe_float,
    safe_text,
)
from stockhot.storage.database import save_analysis_result, save_daily_data


def _parse_pct(text: str) -> float:
    """Parse a percentage string like '3.14%' into 3.14. Returns 0.0 on failure."""
    return safe_float(re.sub(r"[^\d.\-]", "", text))


def _fetch_market_fund_flow_tushare(days: int = 30) -> list[dict]:
    """Fallback: fetch market-wide fund flow history from Tushare.

    Uses ``pro.moneyflow_mkt_dc`` (东方财富分类市场资金流) which returns
    one row per trade date. We fetch the most recent ``days`` trade dates.

    Fields mapped to the same schema as ``fetch_market_fund_flow``:
    date, main_net (亿), main_pct, huge_net, large_net, medium_net, small_net.
    """
    try:
        from datetime import datetime, timedelta
        from stockhot.data_layer import get_gateway

        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        df = get_gateway().call("moneyflow_mkt_dc", start_date=start, end_date=end)
        if df is None or df.empty:
            logger.info(f"fetch_market_fund_flow (Tushare): no data {start}-{end}")
            return []

        # moneyflow_mkt_dc 字段(单位元): trade_date, close_sh, pct_change_sh,
        # close_sz, pct_change_sz, net_amount, net_amount_rate,
        # buy_elg_amount, buy_elg_amount_rate, buy_lg_amount, ...,
        # buy_md_amount, ..., buy_sm_amount, ...
        rows: list[dict] = []
        for _, r in df.iterrows():
            rows.append({
                "date": str(r.get("trade_date", "")),
                "main_net": safe_float(r.get("net_amount")) / 1e8,  # 元→亿
                "main_pct": safe_float(r.get("net_amount_rate")),
                "huge_net": safe_float(r.get("buy_elg_amount")) / 1e8,
                "large_net": safe_float(r.get("buy_lg_amount")) / 1e8,
                "medium_net": safe_float(r.get("buy_md_amount")) / 1e8,
                "small_net": safe_float(r.get("buy_sm_amount")) / 1e8,
            })
        rows.sort(key=lambda x: x["date"])  # 升序(旧→新), 与akshare一致
        logger.info(f"fetch_market_fund_flow (Tushare): {len(rows)} rows")
        return rows
    except Exception as e:
        logger.warning(f"fetch_market_fund_flow (Tushare) failed: {e}")
        return []


def _fetch_sector_fund_flow_ths() -> list[dict]:
    """Fallback: scrape Tonghuashun (同花顺) industry fund-flow HTML page.

    Used when the primary East Money API (``push2.eastmoney.com``) is
    unreachable or rate-limited. Tonghuashun serves a simple HTML table at
    ``http://data.10jqka.com.cn/funds/hyzjl/`` that is robust to IP-level
    blocks affecting East Money.

    Returns rows in the same schema as ``fetch_sector_fund_flow``:
    name, change_pct, main_net, huge_net, large_net, medium_net, small_net.
    (main_pct is set to 0.0 since Tonghuashun does not report it.)
    """
    import os

    import requests
    from lxml import etree

    # Tonghuashun is a domestic site; clear proxy to avoid the same
    # ProxyError that affects East Money.
    saved = {}
    for key in (
        "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
        "ALL_PROXY", "all_proxy",
    ):
        if key in os.environ:
            saved[key] = os.environ.pop(key)
    try:
        url = "http://data.10jqka.com.cn/funds/hyzjl/"
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
            proxies={"http": None, "https": None},
        )
        r.raise_for_status()
        tree = etree.HTML(r.text)
        trs = tree.xpath('//table//tr')
        rows: list[dict] = []
        for tr in trs[1:]:  # skip header row
            cells = [c.strip() for c in tr.xpath('.//td//text()') if c.strip()]
            # Expected: [序号, 行业, 行业指数, 涨跌幅, 流入资金(亿),
            #            流出资金(亿), 净额(亿), 公司家数, 领涨股, 涨跌幅]
            if len(cells) < 7:
                continue
            name = cells[1]
            if not name:
                continue
            main_net = safe_float(cells[6])  # 净额(亿), already in 亿
            # ⚠️ 同花顺只提供单一净额，无法区分主力/散户。
            # main_net 在此路径是"全部资金净流入"（含散户），与 Tushare/东财路径
            # 的"主力=超大单+大单净流入"语义不一致。此为兜底路径，优先级最低。
            rows.append({
                "name": name,
                "change_pct": _parse_pct(cells[3]),
                "main_net": main_net,
                "main_pct": 0.0,  # not reported by THS
                "huge_net": 0.0,
                "large_net": main_net,  # THS 无法拆分，近似填入全部净额
                "medium_net": 0.0,
                "small_net": -main_net,  # 零和近似（兜底用，非真实分档）
            })
        logger.info(f"fetch_sector_fund_flow (THS fallback): {len(rows)} rows")
        return rows
    except Exception as e:
        logger.warning(f"THS fallback failed: {e}")
        return []
    finally:
        os.environ.update(saved)


def fetch_market_fund_flow() -> list[dict]:
    """Fetch recent market-wide fund flow history via AkShare.

    Uses ``ak.stock_market_fund_flow()`` which takes no date parameter and
    returns a history table.  Fields extracted (AkShare Chinese column names):

    - 日期  → date
    - 主力净流入-净额 → main_net
    - 主力净流入-净流入占比 → main_pct
    - 超大单净流入-净额 → huge_net
    - 大单净流入-净额   → large_net
    - 中单净流入-净额   → medium_net
    - 小单净流入-净额   → small_net

    Returns a list of dicts with normalised field names, sorted ascending
    (oldest → newest). The **last** element is the most recent trading day.

    ⚠️ **消费方注意**：本函数返回的是**时间序列**（最近 N 日），不是单点。
    取"当日值"请用 ``rows[-1]`` 或筛选 ``date == 期望交易日``，**不要取 ``rows[0]``**
    （那是 N 天前的最早一条）。``mark_latest_market_flow()`` 会在最新一条加
    ``is_latest=True`` 标记，``run_fund_flow_analysis`` 也会在返回里单独提供
    ``latest_market_flow`` 字段方便消费方直接取。
    """
    df = safe_akshare_call(ak.stock_market_fund_flow)
    # 2026-07-07 调整：Tushare 优先，AKShare 兜底（反转原顺序）
    rows = _fetch_market_fund_flow_tushare()
    if rows:
        return _mark_latest_market_flow(rows)
    logger.info("fetch_market_fund_flow: Tushare empty, trying AKShare fallback")

    if df is None or df.empty:
        return []

    rows = []
    for _, row in df.iterrows():
        raw_date = safe_text(row.get("日期"))
        if not raw_date:
            continue
        # AkShare returns 元, convert to 亿
        rows.append(
            {
                "date": from_akshare_date(raw_date) if len(raw_date) == 8 else raw_date,
                "main_net": safe_float(row.get("主力净流入-净额")) / 1e8,
                "main_pct": safe_float(row.get("主力净流入-净流入占比")),
                "huge_net": safe_float(row.get("超大单净流入-净额")) / 1e8,
                "large_net": safe_float(row.get("大单净流入-净额")) / 1e8,
                "medium_net": safe_float(row.get("中单净流入-净额")) / 1e8,
                "small_net": safe_float(row.get("小单净流入-净额")) / 1e8,
            }
        )
    logger.info(f"fetch_market_fund_flow (AKShare fallback): {len(rows)} rows")
    return _mark_latest_market_flow(rows)


def _mark_latest_market_flow(rows: list[dict]) -> list[dict]:
    """为大盘资金流时序打 ``is_latest`` 标记（最新一条 = True）。

    解决"消费方误取 ``rows[0]`` 当当日"的隐患——
    ``rows[0]`` 是最早一天，``rows[-1]`` 才是最新。

    幂等：重复调用不会累积 ``is_latest=True``（先全清再标最后一行）。
    """
    if not rows:
        return rows
    for r in rows:
        r["is_latest"] = False
    rows[-1]["is_latest"] = True
    return rows


def _fetch_sector_fund_flow_tushare() -> list[dict]:
    """Primary source: aggregate sector fund flow from Tushare.

    Tushare has no single "sector fund flow" endpoint, but we can aggregate
    from per-stock ``moneyflow`` (东方财富分类) + ``stock_basic`` industry.
    This is the most reliable source — token-authenticated, no IP blocking.

    Returns rows with: name, change_pct (0.0, not from Tushare),
    main_net (亿元), huge_net, large_net, medium_net, small_net.
    """
    try:
        # 统一架构（2026-07-15）：改用 stockhot.data_layer 统一网关，
        # 替代 ts.set_token + ts.pro_api 裸调。
        from stockhot.data_layer import get_gateway

        pro = get_gateway()

        trade_date = _today_trade_date_str()  # YYYYMMDD

        # Per-stock fund flow (单位: 万元)
        mf = pro.moneyflow(
            trade_date=trade_date,
            fields=(
                "ts_code,net_mf_amount,buy_sm_amount,sell_sm_amount,"
                "buy_md_amount,sell_md_amount,buy_lg_amount,sell_lg_amount,"
                "buy_elg_amount,sell_elg_amount"
            ),
        )
        if mf is None or mf.empty:
            logger.info(f"fetch_sector_fund_flow (Tushare): no moneyflow for {trade_date}")
            return []

        # Industry classification from stock_basic
        basic = pro.stock_basic(fields="ts_code,name,industry")
        merged = mf.merge(basic[["ts_code", "industry"]], on="ts_code", how="left")

        # 各档净额 = 买入 - 卖出（修复：之前只取 buy 单边值）
        # Tushare moneyflow 单位万元，四档划分：小单<5万 / 中单5-20万 / 大单20-100万 / 超大单>100万
        import pandas as _pd
        merged = merged.copy()
        merged["huge_net"] = merged["buy_elg_amount"].fillna(0) - merged["sell_elg_amount"].fillna(0)
        merged["large_net"] = merged["buy_lg_amount"].fillna(0) - merged["sell_lg_amount"].fillna(0)
        merged["medium_net"] = merged["buy_md_amount"].fillna(0) - merged["sell_md_amount"].fillna(0)
        merged["small_net"] = merged["buy_sm_amount"].fillna(0) - merged["sell_sm_amount"].fillna(0)

        # main_net = 主力净流入 = 超大单 + 大单（与个股路径 _fetch_individual_fund_flow_tushare
        # 及东财口径对齐；net_mf_amount 是全部净流入含散户，不再用作 main_net）
        merged["main_net_raw"] = merged["huge_net"] + merged["large_net"]

        agg = merged.groupby("industry").agg(
            main_net=("main_net_raw", "sum"),
            huge_net=("huge_net", "sum"),
            large_net=("large_net", "sum"),
            medium_net=("medium_net", "sum"),
            small_net=("small_net", "sum"),
            count=("ts_code", "count"),
        ).reset_index()
        # 万元 → 亿元
        rows: list[dict] = []
        for _, r in agg.iterrows():
            if not r["industry"] or _pd.isna(r["industry"]):
                continue
            rows.append({
                "name": r["industry"],
                "change_pct": 0.0,  # Tushare moneyflow 不含涨跌幅
                "main_net": r["main_net"] / 1e4,  # 万元→亿元（主力=超大单+大单净额）
                "main_pct": 0.0,
                "huge_net": r["huge_net"] / 1e4,
                "large_net": r["large_net"] / 1e4,
                "medium_net": r["medium_net"] / 1e4,
                "small_net": r["small_net"] / 1e4,
            })
        rows.sort(key=lambda x: x["main_net"], reverse=True)
        logger.info(f"fetch_sector_fund_flow (Tushare): {len(rows)} sectors")
        return rows
    except Exception as e:
        logger.warning(f"fetch_sector_fund_flow (Tushare) failed: {e}")
        return []


def _today_trade_date_str() -> str:
    """Return today's date as YYYYMMDD string."""
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d")


def fetch_sector_fund_flow(
    indicator: str = "今日",
    sector_type: str = "行业资金流",
) -> list[dict]:
    """Fetch sector-level fund flow ranking.

    Data source priority (each is a fallback for the previous):
    1. **Tushare** (primary) — aggregate per-stock ``moneyflow`` by industry.
       Token-authenticated, no IP blocking, 110 industries.
    2. **AkShare** (secondary) — ``ak.stock_sector_fund_flow_rank`` (东方财富).
       Subject to IP-level rate limiting.
    3. **同花顺 HTML** (tertiary) — ``_fetch_sector_fund_flow_ths``.
       Last resort; 50 industries, no main_pct.

    Fields extracted (all sources):
    - name, change_pct, main_net (亿元), main_pct,
    - huge_net, large_net, medium_net, small_net
    """
    # 1. Tushare primary (reliable, token-authenticated)
    tushare_rows = _fetch_sector_fund_flow_tushare()

    # 2. AkShare secondary (has change_pct — Tushare does not)
    # AkShare's stock_sector_fund_flow_rank makes 5 paginated requests to
    # push2.eastmoney.com internally. If a proxy is configured, later pages
    # may fail with ProxyError, truncating the result to ~1 row.
    # We must clear proxy BEFORE the call so all 5 pages connect directly.
    from stockhot.core.rate_limiter import _call_without_proxy

    logger.info("fetch_sector_fund_flow: trying AkShare (东财) for change_pct")
    try:
        akshare_limiter.acquire()
        df = _call_without_proxy(
            ak.stock_sector_fund_flow_rank,
            indicator=indicator,
            sector_type=sector_type,
        )
    except Exception as e:
        logger.warning(f"fetch_sector_fund_flow: AkShare failed: {e}")
        df = None

    if df is not None and not df.empty:
        rows: list[dict] = []
        for _, row in df.iterrows():
            name = safe_text(row.get("名称"))
            if not name:
                continue
            # AkShare returns 元, convert to 亿
            rows.append(
                {
                    "name": name,
                    "change_pct": safe_float(row.get("今日涨跌幅")),
                    "main_net": safe_float(row.get("主力净流入-净额")) / 1e8,
                    "main_pct": safe_float(row.get("主力净流入-净流入占比")),
                    "huge_net": safe_float(row.get("超大单净流入-净额")) / 1e8,
                    "large_net": safe_float(row.get("大单净流入-净额")) / 1e8,
                    "medium_net": safe_float(row.get("中单净流入-净额")) / 1e8,
                    "small_net": safe_float(row.get("小单净流入-净额")) / 1e8,
                }
            )
        logger.info(f"fetch_sector_fund_flow: AkShare {len(rows)} rows (with change_pct)")

        # If Tushare also succeeded, merge Tushare fund-flow data for
        # sectors not in AkShare (different industry classification).
        # AkShare change_pct is preferred over Tushare's 0.0.
        if tushare_rows:
            ak_names = {r["name"] for r in rows}
            for tr in tushare_rows:
                if tr["name"] not in ak_names:
                    rows.append(tr)

        return rows

    # 3. AkShare empty — use Tushare if available, then enrich change_pct from sw_daily
    if tushare_rows:
        _enrich_change_pct_from_sw_daily(tushare_rows)
        nonzero = sum(1 for r in tushare_rows if r.get("change_pct", 0) != 0)
        logger.info(
            f"fetch_sector_fund_flow: using Tushare {len(tushare_rows)} rows "
            f"(change_pct enriched from sw_daily: {nonzero}/{len(tushare_rows)})"
        )
        return tushare_rows

    # 4. THS fallback (has change_pct)
    logger.warning("fetch_sector_fund_flow: AkShare+Tushare empty, trying THS fallback")
    return _fetch_sector_fund_flow_ths()


def _enrich_change_pct_from_sw_daily(rows: list[dict]) -> None:
    """用 Tushare sw_daily 板块涨跌幅补全 fund_flow 的 change_pct（AKShare 东财源失败时）.

    Tushare moneyflow 不含涨跌幅（change_pct=0.0）。当 AKShare 东财源
    不可用时（push2 被屏蔽），用 sw_daily（申万一级，Tushare token 认证）
    的 pct_change 字段补全。板块名通过 sector_mapping 归一化后匹配。

    直接修改 rows（原地补全 change_pct 字段）。
    """
    import pandas as _pd
    from stockhot.data_layer import get_gateway
    from stockhot.alert.sector_mapping import normalize_sector_name

    try:
        gw = get_gateway()
        # 找最近可得交易日的 sw_daily 数据
        from datetime import date as _date, timedelta as _timedelta
        pct_map: dict[str, float] = {}
        for back in range(0, 6):
            d = (_date.today() - _timedelta(days=back)).strftime("%Y%m%d")
            df = gw.call("sw_daily", trade_date=d)
            if df is None or df.empty:
                continue
            # 只取申万一级（ts_code 格式 801xx0.SI）
            df_l1 = df[df["ts_code"].str.match(r"^801\d{2}0\.SI$")]
            if df_l1.empty:
                continue
            for _, row in df_l1.iterrows():
                name = str(row.get("name", "")).strip()
                pct = row.get("pct_change")
                if name and _pd.notna(pct):
                    pct_map[normalize_sector_name(name)] = float(pct)
            if pct_map:
                break

        if not pct_map:
            logger.info("fetch_sector_fund_flow: sw_daily 无数据，change_pct 保持 0.0")
            return

        # 补全 rows（按归一化名匹配）
        enriched = 0
        for r in rows:
            raw_name = r.get("name", "")
            norm = normalize_sector_name(raw_name)
            if norm in pct_map:
                r["change_pct"] = pct_map[norm]
                enriched += 1
        logger.info(f"fetch_sector_fund_flow: sw_daily 补全 change_pct {enriched}/{len(rows)} 个板块")

    except Exception as e:
        logger.warning(f"fetch_sector_fund_flow: sw_daily 补全失败: {e}")


def _fetch_individual_fund_flow_tushare(stock: str, market: str = "sh") -> list[dict]:
    """Tushare moneyflow 取个股资金流（主源）。

    moneyflow 字段（单位万元）：trade_date, ts_code, buy_sm_amount/sm_vol_amount(小单买/卖),
    buy_md_amount/md_vol_amount(中单), buy_lg_amount/lg_vol_amount(大单),
    buy_elg_amount/elg_vol_amount(超大单), net_mf_amount(主力净流入)。
    """
    from datetime import datetime, timedelta
    from stockhot.data_layer import get_gateway

    # 构造 ts_code
    ts_code = f"{stock}.{market.upper()}" if "." not in stock else stock
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    df = get_gateway().call("moneyflow", ts_code=ts_code, start_date=start, end_date=end)
    if df is None or df.empty:
        return []

    rows: list[dict] = []
    for _, r in df.iterrows():
        # moneyflow 单位是万元，转亿元
        buy_elg = safe_float(r.get("buy_elg_amount"))
        sell_elg = safe_float(r.get("elg_vol_amount"))
        buy_lg = safe_float(r.get("buy_lg_amount"))
        sell_lg = safe_float(r.get("lg_vol_amount"))
        buy_md = safe_float(r.get("buy_md_amount"))
        sell_md = safe_float(r.get("md_vol_amount"))
        buy_sm = safe_float(r.get("buy_sm_amount"))
        sell_sm = safe_float(r.get("sm_vol_amount"))
        huge_net = (buy_elg - sell_elg) / 1e4 if buy_elg is not None and sell_elg is not None else 0.0
        large_net = (buy_lg - sell_lg) / 1e4 if buy_lg is not None and sell_lg is not None else 0.0
        medium_net = (buy_md - sell_md) / 1e4 if buy_md is not None and sell_md is not None else 0.0
        small_net = (buy_sm - sell_sm) / 1e4 if buy_sm is not None and sell_sm is not None else 0.0
        rows.append({
            "date": safe_text(r.get("trade_date")),
            "close_price": 0.0,  # moneyflow 不含 close，由上层补
            "change_pct": safe_float(r.get("pct_change")),
            "main_net": huge_net + large_net,
            "huge_net": huge_net,
            "large_net": large_net,
            "medium_net": medium_net,
            "small_net": small_net,
        })
    rows.sort(key=lambda x: x["date"])
    logger.info(f"fetch_individual_fund_flow (Tushare, {ts_code}): {len(rows)} rows")
    return rows


def fetch_individual_fund_flow(
    stock: str,
    market: str = "sh",
) -> list[dict]:
    """Fetch individual stock fund flow history via AkShare.

    Uses ``ak.stock_individual_fund_flow(stock, market)``.

    Fields extracted:
    - 日期   → date
    - 收盘价 → close_price
    - 涨跌幅 → change_pct
    - 主力净流入-净额 → main_net
    - 超大单净流入-净额 → huge_net
    - 大单净流入-净额   → large_net
    - 中单净流入-净额   → medium_net
    - 小单净流入-净额   → small_net

    2026-07-07 调整：Tushare ``moneyflow`` 优先，AKShare 兜底。
    """
    # Tushare 优先路径
    ts_rows = _fetch_individual_fund_flow_tushare(stock, market)
    if ts_rows:
        return ts_rows

    df = safe_akshare_call(
        ak.stock_individual_fund_flow,
        stock=stock,
        market=market,
    )
    if df is None or df.empty:
        logger.warning("fetch_individual_fund_flow: empty result")
        return []

    rows: list[dict] = []
    for _, row in df.iterrows():
        raw_date = safe_text(row.get("日期"))
        if not raw_date:
            continue
        # AkShare returns 元, convert to 亿
        rows.append(
            {
                "date": from_akshare_date(raw_date) if len(raw_date) == 8 else raw_date,
                "close_price": safe_float(row.get("收盘价")),
                "change_pct": safe_float(row.get("涨跌幅")),
                "main_net": safe_float(row.get("主力净流入-净额")) / 1e8,
                "huge_net": safe_float(row.get("超大单净流入-净额")) / 1e8,
                "large_net": safe_float(row.get("大单净流入-净额")) / 1e8,
                "medium_net": safe_float(row.get("中单净流入-净额")) / 1e8,
                "small_net": safe_float(row.get("小单净流入-净额")) / 1e8,
            }
        )
    logger.info(f"fetch_individual_fund_flow({stock}): {len(rows)} rows")
    return rows


def analyze_fund_flow_trend(
    market_flow: list[dict],
    lookback: int = 5,
) -> dict:
    """Analyse multi-day fund flow trend from market-wide data.

    Looks at the most recent *lookback* rows of ``market_flow`` and computes:

    - **direction**: 持续流入 / 持续流出 / 震荡
      (all positive → inflow, all negative → outflow, else oscillation)
    - **momentum**: 加速 / 减速 / 稳定
      (increasing absolute magnitude → accelerating, decreasing → decelerating)
    - **large_vs_retail_divergence**: bool
      True when large orders (huge + large) and retail orders (medium + small)
      have opposite signs in the latest row.

    Returns a dict with keys: direction, momentum, large_vs_retail_divergence,
    lookback_rows, avg_main_net.
    """
    if not market_flow:
        return {
            "direction": "无数据",
            "momentum": "无数据",
            "large_vs_retail_divergence": False,
            "lookback_rows": 0,
            "avg_main_net": 0.0,
        }

    recent = market_flow[-lookback:]
    main_nets = [safe_float(r.get("main_net")) for r in recent]

    if all(v > 0 for v in main_nets):
        direction = "持续流入"
    elif all(v < 0 for v in main_nets):
        direction = "持续流出"
    else:
        direction = "震荡"

    abs_nets = [abs(v) for v in main_nets]
    if len(abs_nets) >= 2:
        diffs = [abs_nets[i + 1] - abs_nets[i] for i in range(len(abs_nets) - 1)]
        if all(d > 0 for d in diffs):
            momentum = "加速"
        elif all(d < 0 for d in diffs):
            momentum = "减速"
        else:
            momentum = "稳定"
    else:
        momentum = "稳定"

    latest = recent[-1]
    large_total = safe_float(latest.get("huge_net")) + safe_float(latest.get("large_net"))
    retail_total = safe_float(latest.get("medium_net")) + safe_float(latest.get("small_net"))
    divergence = (large_total * retail_total) < 0

    avg_main = sum(main_nets) / len(main_nets) if main_nets else 0.0

    return {
        "direction": direction,
        "momentum": momentum,
        "large_vs_retail_divergence": divergence,
        "lookback_rows": len(recent),
        "avg_main_net": round(avg_main, 4),
    }


def generate_summary(
    market_flow: list[dict],
    sector_flow: list[dict],
    trend: dict,
) -> str:
    """Generate a human-readable statistical summary string.

    Pure text — no AI/LLM involved.
    """
    if not market_flow:
        return "暂无市场资金流向数据。"

    latest = market_flow[-1]
    main_net = safe_float(latest.get("main_net"))
    direction_label = "净流入" if main_net >= 0 else "净流出"

    parts = [
        f"最近一日主力{direction_label}{abs(main_net):.2f}亿。",
        f"趋势判断：{trend.get('direction', '未知')}，{trend.get('momentum', '未知')}。",
    ]

    if trend.get("large_vs_retail_divergence"):
        parts.append("大单与小单方向背离。")

    if sector_flow:
        top = sector_flow[0]
        name = safe_text(top.get("name"))
        top_net = safe_float(top.get("main_net"))
        parts.append(f"行业资金流入首位：{name}，主力净流入{top_net:.2f}亿。")

    return "".join(parts)


def run_fund_flow_analysis(date: str) -> dict:
    """Main entry point for the fund flow trends module.

    1. Fetches market-wide and sector fund flow data.
    2. Analyses trends.
    3. Saves raw data and analysis result to DB.
    4. Returns ``{date, status, data, latest_market_flow, data_stale}``.

    **日期校验**（2026-07-27 加）：``fund_flow_market`` 是 30 日时序，
    最新一条的 ``date`` 应等于传入 ``date``。不等则说明 Tushare/AKShare
    当日数据未上线（如盘后 17:30 拉取时数据源延迟），返回 ``data_stale=True``
    并把实际数据日期记入 ``actual_data_date``，消费方据此决定是否降级处理。
    """
    logger.info(f"run_fund_flow_analysis: date={date}")

    market_flow = fetch_market_fund_flow()
    sector_flow = fetch_sector_fund_flow()

    if not market_flow and not sector_flow:
        logger.warning("run_fund_flow_analysis: no data available")
        return {"date": date, "status": "no_data", "data": {}}

    # 日期一致性校验：market_flow[-1].date 应等于传入 date
    expected_date_norm = date.replace("-", "")  # YYYY-MM-DD → YYYYMMDD
    latest_market = market_flow[-1] if market_flow else None
    actual_date = latest_market.get("date", "") if latest_market else ""
    actual_date_norm = actual_date.replace("-", "")
    data_stale = bool(actual_date_norm) and actual_date_norm != expected_date_norm
    if data_stale:
        logger.warning(
            f"run_fund_flow_analysis: DATA STALE — expected {expected_date_norm}, "
            f"got {actual_date_norm} (fund_flow_market[-1].date). "
            f"消费方应标注'数据日期待更新'。"
        )

    trend = analyze_fund_flow_trend(market_flow)
    summary = generate_summary(market_flow, sector_flow, trend)

    data = {
        "market_flow": market_flow,
        "sector_flow": sector_flow,
        "trend": trend,
        "summary": summary,
        # 单点字段，方便消费方直接取当日值，无需自行从时序里挑
        "latest_market_flow": latest_market,
        "data_stale": data_stale,
        "actual_data_date": actual_date,
    }

    save_daily_data(
        {"date": date, "fund_flow_market": market_flow, "fund_flow_sector": sector_flow}
    )

    save_analysis_result(date, "fund_flow_trend", {"trend": trend, "summary": summary})

    status = "data_stale" if data_stale else "success"
    logger.info(f"run_fund_flow_analysis: done — {status} / {trend['direction']} / {trend['momentum']}")
    return {
        "date": date,
        "status": status,
        "data": data,
        "latest_market_flow": latest_market,
        "data_stale": data_stale,
        "actual_data_date": actual_date,
    }
