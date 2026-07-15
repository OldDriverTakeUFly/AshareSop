"""Sector data loader — 申万一级列表 + 成分股 + 个股日线（含缓存）。

数据源策略（遵循 data-source-convention.md，Tushare 优先）：
- 申万一级列表：AKShare ``sw_index_first_info``（唯一源，Tushare 的 index_basic market=SW 口径混乱）
- 成分股明细：Tushare ``index_member``（is_new=Y 过滤当前在册）
- 个股日线：Tushare ``pro_bar(adj=qfq)``（前复权）

缓存策略（避免每日重复拉取 ~4650 只个股全历史）：
- 成分股名单：缓存到 JSON，月度更新（申万季度调整）
- 个股 RV 时序：缓存到 CSV（storage/sector_rv_cache/），每日增量追加最新一日
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from stockhot.core.config import STORAGE_DIR
from stockhot.core.logging import logger

# 绝对路径（基于 PROJECT_ROOT），消除 cwd 依赖
CACHE_DIR = STORAGE_DIR / "sector_rv_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
MEMBERS_CACHE = CACHE_DIR / "sw_members.json"


def fetch_sw_l1_sectors() -> list[dict]:
    """获取申万一级行业列表（31 个）。

    返回：[{"sw_code": "801010.SI", "name": "农林牧渔", "member_count": 104}, ...]
    数据源：AKShare sw_index_first_info（唯一干净来源）。
    """
    import akshare as ak

    df = ak.sw_index_first_info()
    sectors: list[dict] = []
    for _, row in df.iterrows():
        sectors.append({
            "sw_code": str(row["行业代码"]),
            "name": str(row["行业名称"]),
            "member_count": int(row["成份个数"]),
        })
    logger.info(f"fetch_sw_l1_sectors: {len(sectors)} 个申万一级")
    return sectors


def fetch_sector_members(sw_code: str, use_cache: bool = True) -> list[str]:
    """获取某板块的当前在册成分股代码列表。

    Tushare ``index_member(is_new=Y)`` 过滤当前在册。
    缓存到 JSON，月度失效（申万季度调整，月度刷新够用）。

    参数：
        sw_code: 申万指数代码，如 "801010.SI"
        use_cache: 是否用缓存（默认 True）

    返回：["000001.SZ", "000002.SZ", ...]
    """
    # 检查缓存
    cache: dict = {}
    if use_cache and MEMBERS_CACHE.exists():
        mtime = pd.Timestamp(MEMBERS_CACHE.stat().st_mtime, unit="s")
        age_days = (pd.Timestamp.now() - mtime).days
        if age_days < 30:  # 30 天内缓存有效
            cache = json.loads(MEMBERS_CACHE.read_text())
            if sw_code in cache:
                members = cache[sw_code]
                logger.info(f"fetch_sector_members({sw_code}): cache hit, {len(members)} 只")
                return members

    # Tushare 拉取
    from stockhot.core.tushare_client_safe import safe_tushare_call

    df = safe_tushare_call("index_member", index_code=sw_code, fields="con_code,is_new")
    if df is None or df.empty:
        logger.warning(f"fetch_sector_members({sw_code}): empty")
        return []

    # 过滤当前在册
    active = df[df["is_new"] == "Y"]["con_code"].tolist() if "is_new" in df.columns else df["con_code"].tolist()
    logger.info(f"fetch_sector_members({sw_code}): {len(active)} 只当前在册")

    # 更新缓存
    cache[sw_code] = active
    MEMBERS_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    return active


def _member_rv_cache_path(sw_code: str) -> Path:
    """个股 RV 时序缓存文件路径（按板块分文件）。"""
    safe_code = sw_code.replace(".", "_")
    return CACHE_DIR / f"{safe_code}_member_rv.csv"


def fetch_member_rv_history(
    sw_code: str,
    members: list[str],
    days: int = 1300,
    use_cache: bool = True,
) -> dict[str, pd.Series]:
    """批量获取板块内所有成分股的 RV20 时序（含缓存 + 增量）。

    首次：对每只成分股拉 1300 日日线，算 RV20，缓存。
    增量：读缓存，只拉每只个股最新 5 日（更新滚动窗口）。

    返回：{ts_code: pd.Series(name='rv20', index=date)}
    """
    cache_path = _member_rv_cache_path(sw_code)

    # 尝试读缓存
    cached: pd.DataFrame | None = None
    if use_cache and cache_path.exists():
        try:
            cached = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            logger.info(f"fetch_member_rv_history({sw_code}): cache {len(cached.columns)} stocks")
        except Exception as e:
            logger.warning(f"cache read failed: {e}")
            cached = None

    # 判断是否需要增量更新（缓存最新日期 < 今天 - 3 天）
    need_incremental = False
    if cached is not None and len(cached) > 0:
        last_cached_date = cached.index.max()
        today = pd.Timestamp.now().normalize()
        if (today - last_cached_date).days > 3:
            need_incremental = True
            logger.info(f"fetch_member_rv_history({sw_code}): incremental update from {last_cached_date.date()}")
        else:
            # 缓存够新，直接返回（转成 Series dict）
            return {col: cached[col].dropna() for col in cached.columns if cached[col].notna().any()}

    # 需要拉数据（首次或增量）
    from stockhot.volatility.analyzer import realized_vol
    from stockhot.technical_analyzer.data_loader import fetch_ohlcv

    rv_dict: dict[str, pd.Series] = {}
    total = len(members)

    if cached is not None:
        # 增量：保留已有 RV，只追加新数据（简单策略：重新拉最近 60 日重算末段）
        # 实际上为简单起见，增量也重拉全量（pro_bar 快，~2 分钟）
        pass

    # 首次全量 或 增量（统一全量重拉，简单可靠）
    start = (date.today() - timedelta(days=int(days * 1.8))).strftime("%Y-%m-%d")
    end = date.today().strftime("%Y-%m-%d")

    for i, ts_code in enumerate(members):
        try:
            df = fetch_ohlcv(ts_code, start, end, adjust="qfq")
            if df.empty or len(df) < 30:
                continue
            rv = realized_vol(df["close"], window=20)
            rv.name = ts_code
            rv_dict[ts_code] = rv.dropna()
        except Exception:
            continue  # 单只失败不影响其他

        if (i + 1) % 200 == 0:
            logger.info(f"  {sw_code} progress: {i+1}/{total}")

    # 缓存（合并成 DataFrame 便于存取）
    if rv_dict:
        rv_df = pd.DataFrame(rv_dict)
        rv_df.to_csv(cache_path)
        logger.info(f"fetch_member_rv_history({sw_code}): cached {len(rv_dict)} stocks × {len(rv_df)} days")

    return rv_dict
