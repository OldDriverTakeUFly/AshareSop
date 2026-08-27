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
from pullback import LabelerParams, find_pullbacks  # noqa: E402
from features import exante_features, pullback_features  # noqa: E402


LP = LabelerParams()


def episode_of(s: dict, entry_pos: int, exit_pos: int | None = None) -> Episode:
    return Episode("T.SZ", entry_pos, s["end"] if exit_pos is None else exit_pos,
                   "open", float(np.nanmax(s["c"][entry_pos:])))


class TestPullback:
    def test_labeler_params_disjoint_from_machine(self):
        # 结构断言(spec §8):标注器判死线严于状态机退出线,参数不同源
        assert LabelerParams().term_dd < TrendParams().exit_dd   # -0.25 < -0.20
        assert LabelerParams().term_low_win != TrendParams().exit_ma60_days

    def test_multiple_pullbacks_continue(self):
        # 打底缓涨到 ~10.2(保证 40 日低点 ~10.0 足够低),动作段远离 40 日低点
        base = rising(150, 9.5, 0.0005)
        closes = base + [11.5, 10.8, 11.7, 11.0, 11.9]   # 峰11.5 回调10.8(-6.1%) 新高11.7;峰11.7 回调11.0(-6.0%) 新高11.9
        s = mk_stock(closes)
        ep = episode_of(s, 300 + 145)                   # 缓涨尾段进入
        pbs = find_pullbacks("T.SZ", s, ep, LP)
        assert [p.outcome for p in pbs] == ["continue", "continue"]
        assert pbs[1].peak_px > pbs[0].peak_px          # 峰值重置
        assert pbs[0].idx == 0 and pbs[1].idx == 1

    def test_terminate_on_deep_drawdown(self):
        up = rising(160)
        peak = up[-1] * 1.10
        closes = up + [peak, peak * 0.90, peak * 0.72]  # -28% ≤ -25% → terminate
        s = mk_stock(closes)
        ep = episode_of(s, 300 + 155)
        pbs = find_pullbacks("T.SZ", s, ep, LP)
        assert len(pbs) == 1 and pbs[0].outcome == "terminate"
        assert pbs[0].trough_px == pytest.approx(peak * 0.72, rel=1e-6)

    def test_timeout_flat_pullback(self):
        up = rising(160)
        peak = up[-1] * 1.08
        flat = [peak * 0.94] * 26                        # -6% 触发后横盘 26 日
        closes = up + [peak] + flat
        s = mk_stock(closes)
        ep = episode_of(s, 300 + 155)
        pbs = find_pullbacks("T.SZ", s, ep, LP)
        assert len(pbs) == 1 and pbs[0].outcome == "timeout"

    def test_outcome_observed_beyond_episode_exit(self):
        # 状态机在 -21% 处退出,但 25 日内收盘创前高 → 回调结局是 continue(观察窗独立)。
        # 构造要点:前期必须长期横盘在 10(否则缓涨尾巴抬高 40 日低点,跌 -21% 会先触发
        # 「创40日新低」的 terminate 判死,而不是走到 V 型修复)。
        flat = [10.0] * 140                              # 300..439
        run = [10.0 * (13.4 / 10.0) ** (i / 19) for i in range(20)]   # 440..459 冲到 13.4
        peak = 13.4
        closes = flat + run + [peak * 0.93, peak * 0.79,   # 460 触发回调;461 -21% 状态机dd退出
                               peak * 0.86, peak * 0.95, peak * 1.02]  # 464 创前高 → continue
        s = mk_stock(closes)
        ep = Episode("T.SZ", 455, 461, "dd", peak)
        pbs = find_pullbacks("T.SZ", s, ep, LP)
        assert len(pbs) == 1 and pbs[0].outcome == "continue"
        assert pbs[0].end_pos == 464 and pbs[0].end_pos > ep.exit_pos


class TestFeatures:
    def _mk(self):
        base = rising(150, 9.5, 0.0005)
        closes = base + [11.5, 10.8, 10.75, 10.9, 11.7]   # 峰11.5(450) 谷10.75(452) +3日10.9(453) 新高11.7(454)
        s = mk_stock(closes)
        ep = Episode("T.SZ", 300 + 145, s["end"], "open", 11.7)
        pb = find_pullbacks("T.SZ", s, ep, LP)[0]
        return s, ep, pb

    def test_pullback_features_price_cols(self):
        s, ep, pb = self._mk()
        f = pullback_features("T.SZ", s, ep, pb, None, None)
        assert f["pb_days"] == 2                        # 10.8 → 10.75 两天
        assert f["pb_depth"] == pytest.approx(10.75 / 11.5 - 1, rel=1e-6)
        assert f["vol_ratio"] == pytest.approx(1.0, rel=1e-6)   # v 全 1.0
        assert np.isnan(f["mf_wash"])                   # aux_mf=None
        assert 0.0 < f["pos_pct_entry"] <= 100.0

    def test_exante_features(self):
        s, ep, pb = self._mk()
        f = exante_features("T.SZ", s, ep, pb)
        assert f["ex_dd3"] == pytest.approx(10.9 / 11.5 - 1, rel=1e-6)
        assert f["ex_vol3"] == pytest.approx(1.0, rel=1e-6)


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
