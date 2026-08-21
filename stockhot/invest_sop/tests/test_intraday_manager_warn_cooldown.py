"""Tests for 预警推送冷却 (intraday_manager warn cooldown).

背景 (2026-08-21): warn_stop/warn_target 是电平型信号，价格贴线时每 2 分钟
一轮都触发——单标的单日可刷 30+ 条推送。新增同 (账户, 标的, 类型) 30 分钟
冷却，冷却期内静默不推不计入统计。
"""

import time

from stockhot.invest_sop.scripts.intraday_manager import (
    WARN_COOLDOWN_SEC,
    _warn_push_at,
    _warn_push_allowed,
)


class TestWarnCooldown:
    def setup_method(self):
        _warn_push_at.clear()

    def test_first_push_allowed(self):
        assert _warn_push_allowed(("mini_100k", "603893", "warn_stop"), now=1000.0) is True

    def test_same_key_suppressed_within_cooldown(self):
        assert _warn_push_allowed(("mini_100k", "603893", "warn_stop"), now=1000.0) is True
        assert _warn_push_allowed(("mini_100k", "603893", "warn_stop"), now=1000.0 + 60) is False
        assert _warn_push_allowed(("mini_100k", "603893", "warn_stop"), now=1000.0 + WARN_COOLDOWN_SEC - 1) is False

    def test_allowed_again_after_cooldown(self):
        assert _warn_push_allowed(("mini_100k", "603893", "warn_stop"), now=1000.0) is True
        assert _warn_push_allowed(("mini_100k", "603893", "warn_stop"), now=1000.0 + WARN_COOLDOWN_SEC) is True

    def test_different_type_independent(self):
        assert _warn_push_allowed(("mini_100k", "603893", "warn_stop"), now=1000.0) is True
        assert _warn_push_allowed(("mini_100k", "603893", "warn_target"), now=1000.0 + 60) is True

    def test_different_stock_independent(self):
        assert _warn_push_allowed(("mini_100k", "603893", "warn_stop"), now=1000.0) is True
        assert _warn_push_allowed(("mini_100k", "600183", "warn_stop"), now=1000.0 + 60) is True

    def test_different_account_independent(self):
        assert _warn_push_allowed(("mini_100k", "603893", "warn_stop"), now=1000.0) is True
        assert _warn_push_allowed(("live_factor_test", "603893", "warn_stop"), now=1000.0 + 60) is True

    def test_cooldown_anchors_at_last_push_not_last_signal(self):
        # 冷却从"上次推送"起算：被静默的信号不刷新计时
        assert _warn_push_allowed(("a", "1", "warn_stop"), now=1000.0) is True
        assert _warn_push_allowed(("a", "1", "warn_stop"), now=1200.0) is False  # 静默，不刷新
        assert _warn_push_allowed(("a", "1", "warn_stop"), now=1000.0 + WARN_COOLDOWN_SEC) is True

    def test_default_now_uses_wall_clock(self):
        before = time.time()
        assert _warn_push_allowed(("b", "2", "warn_target")) is True
        assert _warn_push_at[("b", "2", "warn_target")] >= before
