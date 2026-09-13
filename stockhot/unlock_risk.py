"""解禁风险闸(2026-09-13)——实证背书见 docs/分析笔记/未来一年解禁日历。

结论驱动参数:高占比解禁的伤害集中在解禁前 20 日(占比≥20% 样本前窗跑输率 77%、
超额中位 -5.4%),解禁后基本随机——所以:
- 持仓警报:未来 90 天解禁占比 > 30% → 警报(washout holdings_check 消费)
- 买入剔除:未来 30 天解禁占比 > 50% → 不入候选池(intraday_rotation 消费)

数据:Tushare share_float——注意该接口按「股东明细行」返回,float_ratio 是单股东
占总股本比,必须按 (ts_code, float_date) 聚合求和才是该次解禁总占比。
缓存:storage/database/unlock_cache.json,周级 TTL + 缺股增量补(127 只全量刷约 60-90s,
不能每次调用都打 API)。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = REPO_ROOT / "storage" / "database" / "unlock_cache.json"
CACHE_TTL_DAYS = 7


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            pass
    return {"fetched_on": "", "events": {}}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False))
    tmp.replace(CACHE_PATH)


def _fetch_forward_events(ts_code: str, pro) -> list[dict]:
    """单股全量解禁记录 → 仅保留未来事件(按 float_date 聚合占比),占比降序前 5。"""
    sf = pro.share_float(ts_code=ts_code, fields="float_date,float_ratio")
    if sf is None or sf.empty:
        return []
    today = _today()
    sf = sf[sf.float_date.astype(str) >= today].dropna(subset=["float_ratio"])
    if sf.empty:
        return []
    agg = sf.assign(fd=sf.float_date.astype(str)).groupby("fd").float_ratio.sum()
    events = [
        {"float_date": d, "ratio": round(float(r), 2)}
        for d, r in agg.items()
        if r >= 10  # 聚合后 <10% 的事件无警报价值,不占缓存
    ]
    events.sort(key=lambda e: (-e["ratio"], e["float_date"]))
    return events[:5]


def ensure_cache(ts_codes: list[str], force: bool = False) -> dict[str, list[dict]]:
    """返回 events 映射;TTL 过期或新增股票时增量刷新(失败时返回旧缓存并容错)。"""
    cache = _load_cache()
    today = datetime.now()
    fetched_on = cache.get("fetched_on", "")
    stale = True
    if fetched_on:
        try:
            age = (today - datetime.strptime(fetched_on, "%Y%m%d")).days
            stale = age >= CACHE_TTL_DAYS
        except ValueError:
            stale = True
    missing = [c for c in ts_codes if c not in cache.get("events", {})]
    if force or (stale and ts_codes):
        refresh = ts_codes if stale else missing
    elif missing:
        refresh = missing
    else:
        return cache["events"]
    try:
        from dotenv import load_dotenv

        load_dotenv(str(REPO_ROOT / ".env"))
        import os

        import tushare as ts

        pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
        events = cache.setdefault("events", {})
        for i, code in enumerate(refresh):
            try:
                ev = _fetch_forward_events(code, pro)
                events[code] = ev  # 无未来事件也落缓存(空列表),避免反复打 API
            except Exception:
                pass
            time.sleep(0.12)
        cache["fetched_on"] = _today()
        _save_cache(cache)
    except Exception:
        pass  # API 不可用时返回现有缓存(可能为空),调用方按"无警报"处理
    return cache.get("events", {})


def forward_unlock(
    ts_codes: list[str], days: int = 90, threshold: float = 30
) -> dict[str, dict]:
    """窗口内占比达阈值的最早一次解禁:{code: {"date","ratio","days_left"}}。"""
    events = ensure_cache(ts_codes)
    today = datetime.strptime(_today(), "%Y%m%d")
    out: dict[str, dict] = {}
    for code in ts_codes:
        best = None
        for e in events.get(code, []):
            try:
                fd = datetime.strptime(e["float_date"], "%Y%m%d")
            except ValueError:
                continue
            left = (fd - today).days
            if 0 <= left <= days and e["ratio"] >= threshold:
                if best is None or e["ratio"] > best["ratio"]:
                    best = {
                        "date": e["float_date"],
                        "ratio": e["ratio"],
                        "days_left": left,
                    }
        if best:
            out[code] = best
    return out


def filter_buyable(
    ts_codes: list[str], days: int = 30, threshold: float = 50
) -> tuple[list[str], dict[str, dict]]:
    """买入闸:返回 (保留列表, 剔除映射)。窗口内占比≥阈值的标的剔除。"""
    hits = forward_unlock(ts_codes, days=days, threshold=threshold)
    kept = [c for c in ts_codes if c not in hits]
    return kept, hits
