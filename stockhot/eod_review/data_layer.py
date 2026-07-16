"""eod_review 数据拉取层 — 统一从 Tushare 拉取全维度盘后数据.

设计原则：
- 所有拉取走 ``data_layer.get_gateway()`` 统一入口（复用限频/重试）
- 每个维度独立 try/except，失败返回空，不阻断整体
- 复用现有模块（limit_up / fund_flow / dragon_tiger）的 fetch 函数，不重写
- 新增三个接口（moneyflow_hsgt / margin_detail / block_trade）直接走 gateway

单位换算约定（Tushare 原始 → 本模块输出）：
- ``daily.amount``：千元 → 亿元（/1e4）
- ``daily.vol``：手（保留原值，Tushare daily vol 单位即手）
- ``moneyflow_hsgt.north_money``：万元 → 亿元（/1e4）
- ``margin_detail.rzye``：元 → 亿元（/1e8）
- ``block_trade.amount``：万元（保留，Tushare block_trade amount 单位为万元）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from stockhot.core.logging import logger
from stockhot.data_layer import get_gateway, get_repository
from stockhot.data_layer.tushare_gateway import TushareGateway
from stockhot.data_layer.repository import MarketDataRepository

_TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

# ── 单位换算常量 ──────────────────────────────────────────────────────
_WAN_TO_YI = 1e-4   # 万元 → 亿元
_YUAN_TO_YI = 1e-8  # 元 → 亿元


@dataclass
class MarketSnapshot:
    """某交易日全市场数据快照.

    每个字段都是一个独立的 DataFrame（或 list[dict]），某维度拉取失败时为空。
    调用方应检查 ``.empty`` 或 ``len()==0`` 来判断数据是否可用。
    """

    trade_date: str
    # ── 基础行情（DataFrame）──
    daily: pd.DataFrame = field(default_factory=pd.DataFrame)
    daily_basic: pd.DataFrame = field(default_factory=pd.DataFrame)
    daily_with_industry: pd.DataFrame = field(default_factory=pd.DataFrame)
    # ── 涨跌停（list[dict]，复用 limit_up 模块）──
    limit_up: list[dict] = field(default_factory=list)
    broken: list[dict] = field(default_factory=list)
    limit_down: list[dict] = field(default_factory=list)
    # ── 资金流（list[dict]，复用 fund_flow 模块）──
    moneyflow_sector: list[dict] = field(default_factory=list)
    # ── 龙虎榜（list[dict]，复用 dragon_tiger 模块）──
    dragon_tiger: list[dict] = field(default_factory=list)
    # ── 新增维度（DataFrame）──
    north_flow: pd.DataFrame = field(default_factory=pd.DataFrame)
    margin: pd.DataFrame = field(default_factory=pd.DataFrame)
    block_trade: pd.DataFrame = field(default_factory=pd.DataFrame)
    # ── 拉取失败的维度名列表 ──
    errors: list[str] = field(default_factory=list)

    @property
    def has_daily(self) -> bool:
        return not self.daily.empty

    @property
    def has_limit_data(self) -> bool:
        return bool(self.limit_up or self.broken or self.limit_down)


# ── 内部缓存（同一进程内同日期快照不重复拉取）──────────────────────────
_snapshot_cache: dict[str, MarketSnapshot] = {}
_snapshot_ts: dict[str, float] = {}
_CACHE_TTL = 300  # 5 分钟


def get_market_snapshot(
    date: str,
    *,
    use_cache: bool = True,
    gateway: TushareGateway | None = None,
    repo: MarketDataRepository | None = None,
) -> MarketSnapshot:
    """某交易日全市场数据快照（一次性拉齐所有维度）.

    每个子拉取独立 try/except，单维度失败不影响其他维度，
    失败的维度名记入 ``snapshot.errors``。

    Parameters
    ----------
    date : str
        交易日期，YYYYMMDD 格式（如 "20260715"）。
    use_cache : bool
        是否使用进程内缓存（5 分钟 TTL，同一日期不重复拉取）。
    """
    # 缓存命中
    if use_cache and date in _snapshot_cache:
        if time.time() - _snapshot_ts[date] < _CACHE_TTL:
            return _snapshot_cache[date]

    gw = gateway or get_gateway()
    repository = repo or get_repository()

    snap = MarketSnapshot(trade_date=date)

    # ── 1. 全市场日线（直接走 gateway，绕过可能部分写入的 DAL 缓存）──
    # NOTE: repository.get_daily_by_date 会落库缓存，但某些日期的缓存写入
    # 可能产生大量 NaN（已知现象）。复盘引擎要求完整 OHLCV，因此直接拉 Tushare。
    try:
        snap.daily = _fetch_daily(gw, date)
        if snap.daily.empty:
            snap.errors.append("daily")
        else:
            logger.info(f"[EOD] daily: {len(snap.daily)} rows")
    except Exception as e:
        logger.warning(f"[EOD] daily 拉取失败: {e}")
        snap.errors.append("daily")

    # ── 2. 日线 + 行业（用于板块聚合，复用已拉的 daily）──
    try:
        snap.daily_with_industry = _get_daily_with_industry(
            gw, repository, date, daily=snap.daily
        )
    except Exception as e:
        logger.warning(f"[EOD] daily_with_industry 失败: {e}")
        snap.errors.append("daily_with_industry")

    # ── 3. daily_basic（估值+换手率）──
    try:
        snap.daily_basic = _fetch_daily_basic(gw, date)
        logger.info(f"[EOD] daily_basic: {len(snap.daily_basic)} rows")
    except Exception as e:
        logger.warning(f"[EOD] daily_basic 失败: {e}")
        snap.errors.append("daily_basic")

    # ── 4. 涨跌停（复用 limit_up 模块）──
    try:
        from stockhot.limit_up import (
            fetch_limit_up_pool,
            fetch_broken_pool,
            fetch_limit_down_pool,
        )

        snap.limit_up = fetch_limit_up_pool(date) or []
        snap.broken = fetch_broken_pool(date) or []
        snap.limit_down = fetch_limit_down_pool(date) or []
        logger.info(
            f"[EOD] limit: up={len(snap.limit_up)} broken={len(snap.broken)} "
            f"down={len(snap.limit_down)}"
        )
    except Exception as e:
        logger.warning(f"[EOD] limit_up 模块失败: {e}")
        snap.errors.append("limit_up")

    # ── 5. 板块资金流（复用 fund_flow 模块）──
    try:
        from stockhot.fund_flow import fetch_sector_fund_flow

        snap.moneyflow_sector = fetch_sector_fund_flow() or []
        logger.info(f"[EOD] moneyflow_sector: {len(snap.moneyflow_sector)} sectors")
    except Exception as e:
        logger.warning(f"[EOD] fund_flow 模块失败: {e}")
        snap.errors.append("moneyflow_sector")

    # ── 6. 龙虎榜（复用 dragon_tiger 模块）──
    try:
        from stockhot.dragon_tiger import fetch_lhb_detail

        snap.dragon_tiger = fetch_lhb_detail(date, date) or []
        logger.info(f"[EOD] dragon_tiger: {len(snap.dragon_tiger)} rows")
    except Exception as e:
        logger.warning(f"[EOD] dragon_tiger 模块失败: {e}")
        snap.errors.append("dragon_tiger")

    # ── 7. 北向资金（新增接口）──
    try:
        snap.north_flow = _fetch_north_flow(gw, date)
    except Exception as e:
        logger.warning(f"[EOD] north_flow 失败: {e}")
        snap.errors.append("north_flow")

    # ── 8. 融资融券（新增接口）──
    try:
        snap.margin = _fetch_margin(gw, date)
    except Exception as e:
        logger.warning(f"[EOD] margin 失败: {e}")
        snap.errors.append("margin")

    # ── 9. 大宗交易（新增接口）──
    try:
        snap.block_trade = _fetch_block_trade(gw, date)
    except Exception as e:
        logger.warning(f"[EOD] block_trade 失败: {e}")
        snap.errors.append("block_trade")

    # 缓存
    _snapshot_cache[date] = snap
    _snapshot_ts[date] = time.time()

    return snap


# ── 内部拉取函数 ──────────────────────────────────────────────────────


def _fetch_daily(gw: TushareGateway, date: str) -> pd.DataFrame:
    """全市场日线 OHLCV（直接走 gateway 分页拉取，不经 DAL 缓存）.

    Tushare ``daily`` 接口，分页拉全市场。
    返回列：ts_code/trade_date/open/high/low/close/pre_close/pct_chg/vol/amount。
    """
    df = gw.call(
        "daily",
        trade_date=date,
        paginate=True,
        fields=(
            "ts_code,trade_date,open,high,low,close,pre_close,"
            "change,pct_chg,vol,amount"
        ),
    )
    return df if df is not None else pd.DataFrame()


def _get_daily_with_industry(
    gw: TushareGateway, repo: MarketDataRepository, date: str, daily: pd.DataFrame = None
) -> pd.DataFrame:
    """全市场日线 + 行业标签（merge stock_basic.industry）.

    用于板块涨幅聚合——按 Tushare industry 字段 groupby。
    复用 davis_analyzer 的行业口径（stock_basic.industry，非申万）。

    Parameters
    ----------
    daily : pd.DataFrame, optional
        已拉取的全市场日线（传入则不重复拉取）。
    """
    if daily is None or daily.empty:
        daily = _fetch_daily(gw, date)
    if daily is None or daily.empty:
        return pd.DataFrame()

    stock_list = repo.get_stock_list()
    if stock_list is None or stock_list.empty:
        return daily

    # stock_list 含 ts_code / name / industry
    merged = daily.merge(
        stock_list[["ts_code", "name", "industry"]], on="ts_code", how="left"
    )
    # 过滤掉无行业标签的（如指数、退市股）
    merged = merged[merged["industry"].notna() & (merged["industry"] != "")]
    return merged


def _fetch_daily_basic(gw: TushareGateway, date: str) -> pd.DataFrame:
    """全市场每日估值（PE/PB/PS/市值/换手率）."""
    df = gw.call(
        "daily_basic",
        trade_date=date,
        fields=(
            "ts_code,trade_date,pe_ttm,pb,ps,total_mv,"
            "circ_mv,turnover_rate,dv_ratio,free_share"
        ),
    )
    return df if df is not None else pd.DataFrame()


def _fetch_north_flow(gw: TushareGateway, date: str) -> pd.DataFrame:
    """北向资金（moneyflow_hsgt）.

    返回近 N 日（含 date）的北向资金净流入序列，用于 5 日均值。
    north_money 单位：万元 → 输出保持万元（调用方按需转亿）。
    """
    df = gw.call(
        "moneyflow_hsgt",
        trade_date=date,
        fields="trade_date,north_money,south_money,ggt_ss,ggt_sz,hgt,sgt",
    )
    return df if df is not None else pd.DataFrame()


def _fetch_margin(gw: TushareGateway, date: str) -> pd.DataFrame:
    """融资融券明细（margin_detail）.

    rzye（融资余额）单位：元。调用方按需转亿。
    返回全市场个股明细，用于汇总全市场融资余额。
    """
    df = gw.call(
        "margin_detail",
        trade_date=date,
        fields="ts_code,trade_date,rzye,rqye,rzrqye,rzmre,rzche,rqyl",
    )
    return df if df is not None else pd.DataFrame()


def _fetch_block_trade(gw: TushareGateway, date: str) -> pd.DataFrame:
    """大宗交易（block_trade）.

    price/vol/amount/buyer/seller。
    amount 单位：万元。折价率需调用方自算：(price - close) / close。
    """
    df = gw.call(
        "block_trade",
        trade_date=date,
        fields="ts_code,trade_date,price,vol,amount,buyer,seller",
    )
    return df if df is not None else pd.DataFrame()


# ── 个股/指数历史（供技术分析/突破检测/归因用）──────────────────────────


def get_history(
    ts_code: str,
    days: int = 250,
    repo: MarketDataRepository | None = None,
) -> pd.DataFrame:
    """个股历史日线（含 adj_factor，前复权由调用方按需算）.

    复用 repository.get_daily_prices 的增量缓存。
    返回列：ts_code/trade_date/open/high/low/close/vol/amount/adj_factor。
    """
    repository = repo or get_repository()
    today = datetime.now(_TZ_SHANGHAI).strftime("%Y%m%d")
    start = (datetime.now(_TZ_SHANGHAI) - timedelta(days=int(days * 1.6))).strftime(
        "%Y%m%d"
    )
    try:
        df = repository.get_daily_prices(ts_code, start, today)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.warning(f"[EOD] get_history({ts_code}) 失败: {e}")
        return pd.DataFrame()


def get_index_history(
    ts_code: str,
    days: int = 250,
    repo: MarketDataRepository | None = None,
) -> pd.DataFrame:
    """指数历史日线，复用 repository.get_index_daily."""
    repository = repo or get_repository()
    today = datetime.now(_TZ_SHANGHAI).strftime("%Y%m%d")
    start = (datetime.now(_TZ_SHANGHAI) - timedelta(days=int(days * 1.6))).strftime(
        "%Y%m%d"
    )
    try:
        df = repository.get_index_daily(ts_code, start, today)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.warning(f"[EOD] get_index_history({ts_code}) 失败: {e}")
        return pd.DataFrame()


def clear_snapshot_cache() -> None:
    """清空进程内快照缓存（测试用）."""
    _snapshot_cache.clear()
    _snapshot_ts.clear()
