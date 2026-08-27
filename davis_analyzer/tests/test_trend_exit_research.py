"""实验0008 趋势状态机/回调抽取/退出规则 单元测试(合成K线,不依赖数据库)。"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(
    "/home/leo/Projects/CodeAgentDashboard", "scripts", "trend_exit_research"))

from trend_machine import Episode, TrendParams, find_episodes  # noqa: E402


def mk_stock(closes: list[float], start: int = 300) -> dict:
    """合成K线:o=h=l=c,v=1.0;start 前全 NaN(模拟上市前)。"""
    n = start + len(closes) + 10
    c = np.full(n, np.nan, np.float32)
    arr = np.asarray(closes, np.float64)
    c[start:start + len(arr)] = arr.astype(np.float32)
    v = np.where(np.isfinite(c), 1.0, np.nan).astype(np.float32)
    return {"o": c.copy(), "h": c.copy(), "l": c.copy(), "c": c,
            "v": v, "a": v.copy(), "end": start + len(arr) - 1}


def rising(n: int, p0: float = 10.0, drift: float = 0.001) -> list[float]:
    return [p0 * ((1.0 + drift) ** i) for i in range(n)]


P = TrendParams()


class TestTrendMachine:
    def test_entry_on_breakout_with_ma_structure(self):
        closes = rising(150) + [10.0 * 1.05]        # 150日缓涨 + 突破日
        s = mk_stock(closes)
        eps = find_episodes("T.SZ", s, P)
        assert len(eps) >= 1
        # 上市历史=150+,MA20>MA60 上行,收盘创60日新高 → 应在缓涨途中就进入
        assert eps[0].exit_reason == "open"          # 之后无破位,episode 保持到数据尽头
        assert eps[0].entry_pos >= 300

    def test_no_reentry_without_new_high(self):
        # 150日缓涨后 30 日横盘(收盘=缓涨末值,不再创60日新高)→ 不触发新进入
        closes = rising(150) + [rising(150)[-1]] * 30
        s = mk_stock(closes)
        eps = find_episodes("T.SZ", s, P)
        assert len(eps) == 1 and eps[0].exit_reason == "open"

    def test_no_entry_without_ma_structure(self):
        # 200日阴跌后单日 +45% 反弹:创60日新高但 MA20<MA60 → 不得进入
        dec = [20.0 * (0.995 ** i) for i in range(200)]   # 20 → ~7.4,前60日高点 ~10.0
        closes = dec + [dec[-1] * 1.45]                    # 反弹日 ~10.7 > 10.0 创新高
        s = mk_stock(closes)
        eps = find_episodes("T.SZ", s, P)
        assert len(eps) == 0

    def test_exit_on_drawdown(self):
        up = rising(160)                                   # 10 → ~12.7
        peak = up[-1] * 1.10                               # 冲高
        closes = up + [peak, peak * 0.99, peak * 0.78]     # 自峰值 -22% ≤ -20%
        s = mk_stock(closes)
        eps = find_episodes("T.SZ", s, P)
        assert len(eps) == 1 and eps[0].exit_reason == "dd"
        assert eps[0].exit_pos == s["end"]

    def test_exit_on_ma60_streak(self):
        # 长缓涨 10→15(120日)→ 顶部横盘 10 日 → 滚落跌破 MA60 两日,回撤仅 ~11%
        up = [10.0 + 5.0 * i / 120 for i in range(120)]    # 线性涨到 15
        closes = up + [15.0] * 10 + [14.0, 13.4, 13.3]
        s = mk_stock(closes)
        eps = find_episodes("T.SZ", s, P)
        assert len(eps) == 1 and eps[0].exit_reason == "ma60"
