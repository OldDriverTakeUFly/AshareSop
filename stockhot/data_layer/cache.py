"""缓存策略 — 继承 davis tushare_client.py 的三段式增量骨架.

davis 的缓存判定逻辑简洁且正确，本模块将其提炼为可复用的策略类，
供 repository.py 的各数据类型按需使用。

三段式增量骨架（以 daily_basic 为例）::

    1) max_date >= end_date → 历史不可变，直接返回缓存
    2) 距上次查询 < COOLDOWN_SECONDS → 冷却期内用缓存防刷
    3) 否则增量拉取 max_date 之后的数据

本模块修复了 davis 的几个隐患：
- **线程安全**：所有判定和写入在调用方的 connection 上下文内串行
- **时区**：用 Asia/Shanghai 的"今天"（davis 用本地时区）
- **自我锁死**：旧版第 2 条用 is_fetched_today（自然日判定），盘中查到
  Tushare 未发布的旧数据后会锁死当天补救窗口；改为秒级冷却期（COOLDOWN_SECONDS）

缓存判定不直接做 API 调用——它只返回"是否需要刷新"的决策，
实际拉取由 repository 层委托 gateway 完成（职责分离）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

_TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

# TTL 常量（秒）
TTL_STOCK_BASIC = 7 * 86400  # 7 天
TTL_DAILY = 86400  # 24 小时（但主要靠 fetched_today 短路）
TTL_FINANCIAL = float("inf")  # 永久（季报不可变）

# 增量拉取的冷却期：距上次查询 < 此值则用缓存，避免高频轮询狂打 Tushare。
# 替代旧的 is_fetched_today 粗粒度短路——后者会"自我锁死"（盘中查到 Tushare
# 尚未发布的旧数据后，当天再也无法补救）。60s 远小于盘中最激进 cron 间隔
# （panic_alert ≥60min），又远大于 Tushare 网关 0.2s 限频，平衡补及时性
# 与防刷。详见 decide_by_date_range 第 2 条规则。
COOLDOWN_SECONDS = 60


def now_ts() -> float:
    """当前 Unix 时间戳."""
    return time.time()


def is_fetched_today(fetched_at: float | None) -> bool:
    """判断 fetched_at 是否是"今天"（Asia/Shanghai 时区）.

    修复 davis date.today() 本地时区隐患：A 股按北京时间收盘，
    "今天"应以 Asia/Shanghai 为准。
    """
    if fetched_at is None:
        return False
    fetched_date = datetime.fromtimestamp(fetched_at, tz=_TZ_SHANGHAI).date()
    today = datetime.now(_TZ_SHANGHAI).date()
    return fetched_date == today


def is_in_cooldown(fetched_at: float | None, cooldown_seconds: float) -> bool:
    """判断距上次查询是否仍在冷却期内.

    替代 is_fetched_today 作为增量拉取的防刷闸门——后者按"自然日"判定，
    会导致"自我锁死"（盘中查到 Tushare 未发布的旧数据后，当天数据发布后
    也无法补救）。冷却期按秒级判定，过期即允许重新查增量，既防高频刷接口
    又不锁死当日补救窗口。
    """
    if fetched_at is None:
        return False
    return (now_ts() - fetched_at) < cooldown_seconds


def is_expired(fetched_at: float | None, ttl: float) -> bool:
    """判断是否超过绝对 TTL."""
    if fetched_at is None:
        return True
    if ttl == float("inf"):
        return False
    return (now_ts() - fetched_at) > ttl


def next_date_str(date_str: str) -> str:
    """YYYYMMDD + 1 天（用于增量拉取，避免重复拉 max_date 当天）."""
    d = datetime.strptime(date_str, "%Y%m%d")
    from datetime import timedelta
    return (d + timedelta(days=1)).strftime("%Y%m%d")


@dataclass
class CacheDecision:
    """缓存判定结果 — 告诉 repository 层"接下来该做什么"."""

    use_cache: bool  # True=直接用缓存，False=需要刷新
    fetch_start: str | None  # 需要刷新时的起始日期（YYYYMMDD），None=全量


def decide_by_date_range(
    max_cached_date: str | None,
    latest_fetched_at: float | None,
    start_date: str,
    end_date: str,
) -> CacheDecision:
    """按日期范围的缓存判定（三段式骨架）.

    参数：
        max_cached_date: 缓存中该 ts_code 的最大 trade_date（YYYYMMDD），None=无缓存
        latest_fetched_at: 最近一次拉取的 fetched_at，None=从未拉取
        start_date: 请求的起始日期
        end_date: 请求的结束日期

    返回：
        CacheDecision(use_cache, fetch_start)

    三段式逻辑（短路求值）：

    1) max_cached_date >= end_date → 历史不可变，直接返回缓存
    2) 距上次查询仍在冷却期内（< COOLDOWN_SECONDS）→ 用缓存防刷
    3) 否则增量拉取 max_date 之后的数据

    ⚠️ 第 2 条历史教训：曾用 is_fetched_today（按自然日判定）做防刷闸门，
    结果"自我锁死"——当盘中查到 Tushare 尚未发布的旧数据时，fetched_at 被
    刷成"今天"，导致当天数据真正发布后也无法补救（实证：07-28 三个宽基指数
    卡在 07-27 一整天）。改为秒级冷却期后，过期即允许重新查，既防高频刷接口
    又保留当日补救能力。
    """
    # 1) 已覆盖到 end_date → 历史不可变，直接返回
    if max_cached_date is not None and max_cached_date >= end_date:
        return CacheDecision(use_cache=True, fetch_start=None)

    # 2) 距上次查询仍在冷却期内 → 用缓存防刷（替代旧的 is_fetched_today 锁死）
    if is_in_cooldown(latest_fetched_at, COOLDOWN_SECONDS):
        return CacheDecision(use_cache=True, fetch_start=None)

    # 3) 增量拉取：只取 max_date 之后
    if max_cached_date is not None:
        fetch_start = next_date_str(max_cached_date)
        if fetch_start < start_date:
            fetch_start = start_date
    else:
        fetch_start = start_date  # 无缓存，全量

    return CacheDecision(use_cache=False, fetch_start=fetch_start)
