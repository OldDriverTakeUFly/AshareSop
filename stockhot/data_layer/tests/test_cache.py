"""Tests for cache.py — 缓存判定策略.

重点覆盖 decide_by_date_range 的"自我锁死"修复：旧版用 is_fetched_today
（自然日判定）做防刷闸门，盘中查到 Tushare 尚未发布的旧数据后会锁死当天
补救窗口。新版改为秒级冷却期（is_in_cooldown + COOLDOWN_SECONDS）。
"""

from __future__ import annotations

import time
from unittest.mock import patch

from stockhot.data_layer import cache
from stockhot.data_layer.cache import (
    COOLDOWN_SECONDS,
    CacheDecision,
    decide_by_date_range,
    is_fetched_today,
    is_in_cooldown,
    now_ts,
)


# ── is_in_cooldown ──────────────────────────────────────────────────────


def test_is_in_cooldown_within_window():
    """fetched_at 在冷却期内 → True."""
    assert is_in_cooldown(now_ts() - 10, COOLDOWN_SECONDS) is True


def test_is_in_cooldown_expired():
    """fetched_at 超过冷却期 → False."""
    assert is_in_cooldown(now_ts() - COOLDOWN_SECONDS - 1, COOLDOWN_SECONDS) is False


def test_is_in_cooldown_none():
    """fetched_at=None（从未拉取）→ False（不在冷却期，允许拉取）."""
    assert is_in_cooldown(None, COOLDOWN_SECONDS) is False


# ── decide_by_date_range: 场景 A — 自我锁死修复（核心回归测试）───────


def test_decide_stale_cache_not_locked_when_cooldown_expired():
    """场景 A（复现并验证修复）：max_date 落后于 end_date 且冷却期已过 → 必须允许刷新.

    这是修复的核心场景：07-28 实测 bug——上证 max_date=0727、fetched_at 在
    当天 10:05（盘中过早查到旧数据），旧逻辑因 is_fetched_today 锁死，导致
    18:00 盘后扫描也拿不到 0728。修复后冷却期（60s）早过期，应允许增量拉取。
    """
    # 模拟：缓存到 0727，1 分钟前查过（远超 60s 冷却期），请求到 0728
    stale_fetched = now_ts() - 120  # 120 秒前，冷却期已过
    decision = decide_by_date_range(
        max_cached_date="20260727",
        latest_fetched_at=stale_fetched,
        start_date="20260101",
        end_date="20260728",
    )
    assert decision.use_cache is False
    assert decision.fetch_start == "20260728"  # 增量起点 = max_date + 1


def test_decide_stale_cache_still_locked_within_cooldown():
    """场景 B（冷却期生效）：max_date 落后但 10 秒前刚查过 → 用缓存防刷."""
    decision = decide_by_date_range(
        max_cached_date="20260727",
        latest_fetched_at=now_ts() - 10,  # 冷却期内
        start_date="20260101",
        end_date="20260728",
    )
    assert decision.use_cache is True
    assert decision.fetch_start is None


def test_decide_history_complete_uses_cache():
    """场景 C：max_date >= end_date（历史已完整覆盖）→ 直接用缓存."""
    decision = decide_by_date_range(
        max_cached_date="20260728",
        latest_fetched_at=now_ts(),  # 即便刚查过也不重要，历史不可变
        start_date="20260101",
        end_date="20260728",
    )
    assert decision.use_cache is True
    assert decision.fetch_start is None


def test_decide_no_cache_full_fetch():
    """场景 D：max_date=None（首次无缓存）→ 全量拉取."""
    decision = decide_by_date_range(
        max_cached_date=None,
        latest_fetched_at=None,
        start_date="20260101",
        end_date="20260728",
    )
    assert decision.use_cache is False
    assert decision.fetch_start == "20260101"  # 全量，从 start_date 起


def test_decide_cooldown_then_refresh_after_expiry():
    """场景 E：同一组参数，冷却期内锁定、过期后放行（验证时间推进效果）."""
    end = "20260728"
    # t0：刚查过，冷却期内
    fetched_at = now_ts()
    d1 = decide_by_date_range("20260727", fetched_at, "20260101", end)
    assert d1.use_cache is True

    # 模拟时间推进到冷却期之后：用更早的 fetched_at 代表"已过了一段时间"
    d2 = decide_by_date_range(
        "20260727", fetched_at - COOLDOWN_SECONDS - 5, "20260101", end
    )
    assert d2.use_cache is False
    assert d2.fetch_start == "20260728"


# ── 边界与回归 ────────────────────────────────────────────────────────


def test_decide_fetch_start_never_before_request_start():
    """增量起点不应早于请求的 start_date（即便 max_date 更早）."""
    # max_date=2020，但请求 start_date=20260101 → fetch_start 应被钳到 20260101
    decision = decide_by_date_range(
        max_cached_date="20200101",
        latest_fetched_at=now_ts() - 120,
        start_date="20260101",
        end_date="20260728",
    )
    assert decision.use_cache is False
    assert decision.fetch_start == "20260101"


def test_decide_fetched_today_but_stale_still_refreshes():
    """回归保护：即使 fetched_at 是"今天"，只要冷却期过且数据未覆盖，仍刷新.

    这正是旧 is_fetched_today 逻辑的错误行为——它只看"是否今天"而不看
    "数据是否新鲜"。新逻辑修复后，今天查过但冷却期已过且数据落后时仍会重查。
    """
    # fetched_at 设为今天，但 2 小时前（冷却期早已过）
    stale_today = now_ts() - 7200
    decision = decide_by_date_range(
        max_cached_date="20260727",
        latest_fetched_at=stale_today,
        start_date="20260101",
        end_date="20260728",
    )
    assert decision.use_cache is False


def test_cooldown_seconds_constant_is_reasonable():
    """冷却期常量合理性：>0 且 <=5 分钟（防止被误改成离谱值）."""
    assert 0 < COOLDOWN_SECONDS <= 300


# ── is_fetched_today 仍可用（未被删除，其他模块可能依赖）────────────


def test_is_fetched_today_still_works():
    """is_fetched_today 函数保留，语义不变（仅不再被 decide 使用）."""
    assert is_fetched_today(now_ts()) is True
    assert is_fetched_today(None) is False
