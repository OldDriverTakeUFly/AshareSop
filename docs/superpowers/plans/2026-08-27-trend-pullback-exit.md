# 实验 0008:趋势回调判别与退出规则研究 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现趋势状态机→回调事件→判别特征→退出规则族回放→稳健性重采样的完整事件研究管线,产出 0008 实验报告。

**Architecture:** 纯函数内核(`trend_machine/pullback/features/exits/bootstrap` 操作 numpy 数组,单测用合成 K 线)+ 数据加载外壳(`common.py` 直连 SQLite) + 编排(`run_all.py`),输出层 `analyze.py` 汇总成报告。复用 `scripts/washout_research/detect_washout.py` 的数据模式但不 import 它(避免跨目录副作用耦合;三个小助手函数复制进本项目并注明)。

**Tech Stack:** Python 3.11(`from __future__ import annotations`)、numpy、pandas、sqlite3(只读)、pytest。

**Spec:** `docs/superpowers/specs/2026-08-27-trend-pullback-exit-design.md`(参数与验收线以 spec 为准,本计划不重复论证)。

## Global Constraints

- 解释器:`/home/leo/Projects/CodeAgentDashboard/.venv/bin/python`,一切命令从仓库根目录 `/home/leo/Projects/CodeAgentDashboard/` 运行。
- 测试命令:`.venv/bin/python -m pytest davis_analyzer/tests/test_trend_exit_research.py -v`(从仓库根)。
- 代码风格:`from __future__ import annotations` 开头;完整类型注解;管线脚本用 `log()`(print 带时间戳,沿 washout 约定),不用 stdlib logging;`print` 在 scripts/ 管线允许。
- 数据库只读:`storage/database/market_data.db`,不得写入。
- 不修改 `scripts/washout_research/` 现有脚本(holdings_check cron 在用)、不改 `constants.py`。
- 提交规范:Conventional Commits 中文 scope,如 `feat(实验0008): 实现趋势状态机`。
- 全量运行预估 <30 分钟(未触发长回测脱离会话规范),但必须先过 20 只股票烟测。
- 关键设计不变量(来自 spec,实现时不得违反):
  - 回调结局观察窗 = 峰值后 25 交易日,**独立于 episode 生命周期**(状态机退出后仍继续观察结局);
  - 结局标注器参数(term_dd=-0.25/40日新低)与状态机退出(exit_dd=-0.20/破MA60×2)必须不同源;
  - bootstrap 抽样单元 = 股票(非事件),事件窗口归属:回调按峰值日、episode 按进入日。

---

### Task 1: 趋势状态机 `trend_machine.py`

**Files:**
- Create: `scripts/trend_exit_research/__init__.py`(空文件)
- Create: `scripts/trend_exit_research/trend_machine.py`
- Test: `davis_analyzer/tests/test_trend_exit_research.py`

**Interfaces:**
- Produces: `TrendParams(newhigh_win=60, exit_dd=-0.20, exit_ma60_days=2, hist_min=120)`;`Episode(ts_code, entry_pos, exit_pos, exit_reason, peak_close)`;`find_episodes(code: str, s: dict, p: TrendParams) -> list[Episode]`;`rolling_ma(c, win) -> np.ndarray`;`rolling_prior_max(c, win) -> np.ndarray`;`rolling_prior_min(c, win) -> np.ndarray`。`s` 是 washout 约定的数组 dict:`{"o","h","l","c","v","a": float32[n], "end": int}`(下同)。

- [ ] **Step 1: 写失败测试(文件头 + 合成K线助手 + 4 个状态机测试)**

```python
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
        # 长缓涨 10→15(120日)→ 顶部横盘 10 日 → 滚落跌破 MA60 两日,回撤仅 ~13%
        up = [10.0 + 5.0 * i / 120 for i in range(120)]    # 线性涨到 15
        closes = up + [15.0] * 10 + [14.0, 13.4, 13.3]
        s = mk_stock(closes)
        eps = find_episodes("T.SZ", s, P)
        assert len(eps) == 1 and eps[0].exit_reason == "ma60"
```

注:`test_exit_on_ma60_streak` 的滚落价(14.0/13.4/13.3)是按"顶部横盘后 MA60≈14.15"手算的边界;若浮点边界导致不过,允许微调测试序列(约束:回撤 <20% 且恰好两日破线),不得改实现参数迎合测试。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_trend_exit_research.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trend_machine'`

- [ ] **Step 3: 实现 `trend_machine.py`**

```python
"""上涨趋势状态机:episode 进入/退出(实验0008,预注册参数见 spec §4.1)。"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class TrendParams:
    newhigh_win: int = 60
    exit_dd: float = -0.20
    exit_ma60_days: int = 2
    hist_min: int = 120


@dataclass
class Episode:
    ts_code: str
    entry_pos: int
    exit_pos: int        # 退出日(该日收盘卖出);数据尽头未退出 reason="open"
    exit_reason: str     # "dd" | "ma60" | "open"
    peak_close: float    # episode 期间最高收盘(至退出日)


def rolling_ma(c: np.ndarray, win: int) -> np.ndarray:
    # min_periods=win//2:停牌 NaN 日不整窗作废(A股停牌常见)
    return pd.Series(c).rolling(win, min_periods=win // 2).mean().to_numpy(np.float64)


def _rolling_shift_agg(c: np.ndarray, win: int, agg: str) -> np.ndarray:
    return (pd.Series(c).rolling(win, min_periods=win // 2)
            .agg(agg).shift(1).to_numpy(np.float64))


def rolling_prior_max(c: np.ndarray, win: int) -> np.ndarray:
    return _rolling_shift_agg(c, win, "max")


def rolling_prior_min(c: np.ndarray, win: int) -> np.ndarray:
    return _rolling_shift_agg(c, win, "min")


def find_episodes(code: str, s: dict, p: TrendParams) -> list[Episode]:
    c = s["c"].astype(np.float64)
    n = s["end"] + 1
    cum_finite = np.cumsum(np.isfinite(c))
    prior_finite = np.concatenate(([0], cum_finite[:-1]))  # i 之前有限收盘数
    pmax = rolling_prior_max(c, p.newhigh_win)
    ma20 = rolling_ma(c, 20)
    ma60 = rolling_ma(c, 60)

    eps: list[Episode] = []
    in_ep = False
    entry = -1
    peak = -np.inf
    below60 = 0
    for i in range(n):
        if not np.isfinite(c[i]):
            continue
        if not in_ep:
            ok_hist = prior_finite[i] >= p.hist_min
            ok_newhigh = np.isfinite(pmax[i]) and c[i] > pmax[i]
            ok_ma = (np.isfinite(ma20[i]) and np.isfinite(ma60[i]) and i >= 1
                     and np.isfinite(ma20[i - 1])
                     and ma20[i] > ma60[i] and ma20[i] > ma20[i - 1])
            if ok_hist and ok_newhigh and ok_ma:
                in_ep, entry, peak, below60 = True, i, float(c[i]), 0
        else:
            if c[i] > peak:
                peak = float(c[i])
            below60 = below60 + 1 if c[i] < ma60[i] else 0
            if c[i] / peak - 1.0 <= p.exit_dd:
                eps.append(Episode(code, entry, i, "dd", peak))
                in_ep = False
            elif below60 >= p.exit_ma60_days:
                eps.append(Episode(code, entry, i, "ma60", peak))
                in_ep = False
    if in_ep:
        eps.append(Episode(code, entry, n - 1, "open", peak))
    return eps
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_trend_exit_research.py -v`
Expected: 5 passed。若 `test_exit_on_ma60_streak` 数值边界不过(横盘末端 MA60 估计 ~14.1,13.4/13.3 应在下方),允许微调测试序列的滚落价(保持回撤 <20% 与两日破线的约束),不得改实现参数来迎合测试。

- [ ] **Step 5: Commit**

```bash
git add scripts/trend_exit_research/__init__.py scripts/trend_exit_research/trend_machine.py davis_analyzer/tests/test_trend_exit_research.py
git commit -m "feat(实验0008): 趋势状态机——60日新高+MA结构进入/深回撤或连破MA60退出"
```

---

### Task 2: 回调抽取与结局标注器 `pullback.py`

**Files:**
- Create: `scripts/trend_exit_research/pullback.py`
- Test: `davis_analyzer/tests/test_trend_exit_research.py`(追加)

**Interfaces:**
- Consumes: `trend_machine.Episode`、`rolling_prior_min`。
- Produces: `LabelerParams(trigger_dd=-0.05, term_dd=-0.25, term_low_win=40, timeout_days=25, max_ep_days=250)`;`Pullback(ts_code, ep_entry_pos, ep_exit_pos, idx, peak_pos, peak_px, trough_pos, trough_px, outcome, end_pos)`;`find_pullbacks(code: str, s: dict, ep: Episode, lp: LabelerParams) -> list[Pullback]`。

- [ ] **Step 1: 追加失败测试**

```python
from pullback import LabelerParams, find_pullbacks  # noqa: E402

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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_trend_exit_research.py -v -k Pullback`
Expected: FAIL — `ModuleNotFoundError: No module named 'pullback'`

- [ ] **Step 3: 实现 `pullback.py`**

```python
"""回调事件抽取 + 结局标注器(实验0008,spec §4.2)。

关键不变量:结局观察窗(峰值后 timeout_days 日)在完整价格序列上进行,
不截断于状态机退出日;标注器参数与状态机/退出规则不同源。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from trend_machine import Episode, rolling_prior_min


@dataclass
class LabelerParams:
    trigger_dd: float = -0.05
    term_dd: float = -0.25
    term_low_win: int = 40
    timeout_days: int = 25
    max_ep_days: int = 250


@dataclass
class Pullback:
    ts_code: str
    ep_entry_pos: int
    ep_exit_pos: int
    idx: int            # episode 内第几个回调(0-based)
    peak_pos: int
    peak_px: float
    trough_pos: int
    trough_px: float
    outcome: str        # "continue" | "terminate" | "timeout"
    end_pos: int        # 结局日


def find_pullbacks(code: str, s: dict, ep: Episode, lp: LabelerParams) -> list[Pullback]:
    c = s["c"].astype(np.float64)
    low_prior = rolling_prior_min(c, lp.term_low_win)
    out: list[Pullback] = []

    def emit(pb_peak_pos, pb_peak, trough_pos, trough, outcome, end_pos, idx):
        out.append(Pullback(code, ep.entry_pos, ep.exit_pos, idx,
                            pb_peak_pos, float(pb_peak), trough_pos, float(trough),
                            outcome, end_pos))

    peak_pos = ep.entry_pos
    peak = float(c[ep.entry_pos])
    in_pb = False
    need_new_high = False        # timeout 后须先创新高才登记下一回调(防同峰重复触发)
    pb_peak_pos = pb_peak = trough_pos = trough = None
    idx = 0
    hi_scan = min(ep.entry_pos + lp.max_ep_days, s["end"])

    for i in range(ep.entry_pos + 1, hi_scan + 1):
        if not np.isfinite(c[i]):
            continue
        if not in_pb:
            if i > ep.exit_pos:
                return out       # episode 生命周期结束;开放回调已在循环内解决
            if c[i] > peak:
                peak, peak_pos = float(c[i]), i
                need_new_high = False
            elif not need_new_high and c[i] / peak - 1.0 <= lp.trigger_dd:
                in_pb = True
                pb_peak_pos, pb_peak = peak_pos, peak
                trough_pos, trough = i, float(c[i])
            else:
                continue
        if in_pb:
            if c[i] > pb_peak:   # continue:收盘创前高(结局日K线不算谷底)
                emit(pb_peak_pos, pb_peak, trough_pos, trough, "continue", i, idx)
                idx += 1
                in_pb = False
                peak, peak_pos = float(c[i]), i
                continue
            if c[i] < trough:
                trough, trough_pos = float(c[i]), i
            if (c[i] / pb_peak - 1.0 <= lp.term_dd
                    or (np.isfinite(low_prior[i]) and c[i] < low_prior[i])):
                emit(pb_peak_pos, pb_peak, trough_pos, trough, "terminate", i, idx)
                return out       # 判死 → episode 观察结束
            if i - pb_peak_pos > lp.timeout_days:
                emit(pb_peak_pos, pb_peak, trough_pos, trough, "timeout", i, idx)
                idx += 1
                in_pb = False
                need_new_high = True
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_trend_exit_research.py -v`
Expected: 全部 passed(含 Task 1 的 5 个)。

- [ ] **Step 5: Commit**

```bash
git add scripts/trend_exit_research/pullback.py davis_analyzer/tests/test_trend_exit_research.py
git commit -m "feat(实验0008): 回调抽取+结局标注器——观察窗独立于episode生命周期,timeout后须创新高再登记"
```

---

### Task 3: 判别特征 `features.py`

**Files:**
- Create: `scripts/trend_exit_research/features.py`
- Test: `davis_analyzer/tests/test_trend_exit_research.py`(追加)

**Interfaces:**
- Consumes: `trend_machine.Episode`、`pullback.Pullback`。
- Produces: `pullback_features(code, s, ep, pb, aux_inf, aux_mf) -> dict`(谷底口径特征,列名以 `pb_`/`mf_`/`chip` 前缀见下);`exante_features(code, s, ep, pb) -> dict`(峰值+3日口径,列名 `ex_` 前缀)。aux dict 为 washout 约定:`{"pos": int32[], col: float32[]}` 或缺股票时 `None`。

特征列(实现与测试以此为准):

- `pullback_features`:`pb_days`(trough-peak)、`pb_depth`(trough/peak-1)、`vol_ratio`(回调段均量/趋势段均量,趋势段=entry..peak)、`close_vs_ma20`(谷底收盘/MA20-1)、`upper_shadow_mean`、`close_position_mean`(回调段 intraday_feature 均值)、`mf_wash`(回调段主力净流入强度=(大单+超大单净额)/成交额,无 moneyflow 返回 NaN)、`pos_pct_entry`(进入日收盘在先前 ≤250 个有限收盘中的百分位,不足 120 个返回 NaN)、`ep_gain_at_peak`(peak/entry收盘-1)。
- `exante_features`:`ex_dd3`(峰值+3日收盘/峰值-1)、`ex_vol3`(峰值后3日均量/趋势段均量)、`ex_ma20_3`(峰值+3日收盘/MA20-1)。

- [ ] **Step 1: 追加失败测试**

```python
from features import exante_features, pullback_features  # noqa: E402


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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_trend_exit_research.py -v -k Features`
Expected: FAIL — `ModuleNotFoundError: No module named 'features'`

- [ ] **Step 3: 实现 `features.py`**

```python
"""回调判别特征(实验0008,spec §4.3)。全部因果:谷底口径只看 entry..trough,事前口径只看峰值+3日。

aux_window/mf_net_intensity 复制自 washout_research/detect_washout.py(不跨目录 import,
避免其模块级 os.chdir 副作用),约定一致。
"""
from __future__ import annotations

import numpy as np

from trend_machine import Episode, rolling_ma
from pullback import Pullback


def pct_rank(v: float, arr: np.ndarray) -> float:
    if arr.size == 0:
        return np.nan
    return float((arr < v).sum() / arr.size * 100.0)


def aux_window(aux: dict | None, code: str, i0: int, i1: int, cols: list[str]) -> dict:
    if aux is None or aux.get(code) is None:
        return {f"{c}_mean": np.nan for c in cols}
    d = aux[code]
    m = (d["pos"] >= i0) & (d["pos"] <= i1)
    out = {}
    for c in cols:
        vals = d[c][m]
        out[f"{c}_mean"] = float(np.nanmean(vals)) if m.sum() else np.nan
    return out


def mf_net_intensity(aux: dict | None, code: str, i0: int, i1: int,
                     amount: np.ndarray) -> float:
    if aux is None or aux.get(code) is None:
        return np.nan
    d = aux[code]
    m = (d["pos"] >= i0) & (d["pos"] <= i1)
    if m.sum() == 0:
        return np.nan
    net = (d["buy_lg_amount"][m] + d["buy_elg_amount"][m]
           - d["sell_lg_amount"][m] - d["sell_elg_amount"][m]).sum()
    amt = np.nansum(amount[i0:i1 + 1])
    if not np.isfinite(amt) or amt <= 0:
        return np.nan
    return float(net / amt)


def _mean_finite(a: np.ndarray) -> float:
    a = a[np.isfinite(a)]
    return float(a.mean()) if a.size else np.nan


def pullback_features(code: str, s: dict, ep: Episode, pb: Pullback,
                      aux_inf: dict | None, aux_mf: dict | None) -> dict:
    c, v = s["c"].astype(np.float64), s["v"].astype(np.float64)
    leg_v = _mean_finite(v[ep.entry_pos: pb.peak_pos + 1])
    pb_v = _mean_finite(v[pb.peak_pos + 1: pb.trough_pos + 1])
    vol_ratio = pb_v / leg_v if np.isfinite(leg_v) and leg_v > 0 and np.isfinite(pb_v) else np.nan
    ma20 = rolling_ma(c, 20)[pb.trough_pos]
    infd = aux_window(aux_inf, code, pb.peak_pos + 1, pb.trough_pos,
                      ["upper_shadow", "close_position"])
    prior = c[max(0, ep.entry_pos - 250): ep.entry_pos]
    prior = prior[np.isfinite(prior)]
    pos_pct = pct_rank(float(c[ep.entry_pos]), prior) if prior.size >= 120 else np.nan
    return {
        "pb_days": pb.trough_pos - pb.peak_pos,
        "pb_depth": pb.trough_px / pb.peak_px - 1.0,
        "vol_ratio": vol_ratio,
        "close_vs_ma20": (float(c[pb.trough_pos]) / ma20 - 1.0
                          if np.isfinite(ma20) and ma20 > 0 else np.nan),
        "upper_shadow_mean": infd["upper_shadow_mean"],
        "close_position_mean": infd["close_position_mean"],
        "mf_wash": mf_net_intensity(aux_mf, code, pb.peak_pos + 1, pb.trough_pos, s["a"]),
        "pos_pct_entry": pos_pct,
        "ep_gain_at_peak": pb.peak_px / float(c[ep.entry_pos]) - 1.0,
    }


def exante_features(code: str, s: dict, ep: Episode, pb: Pullback) -> dict:
    c, v = s["c"].astype(np.float64), s["v"].astype(np.float64)
    j = pb.peak_pos + 3
    if j > s["end"] or not np.isfinite(c[j]):
        return {"ex_dd3": np.nan, "ex_vol3": np.nan, "ex_ma20_3": np.nan}
    leg_v = _mean_finite(v[ep.entry_pos: pb.peak_pos + 1])
    w3 = _mean_finite(v[pb.peak_pos + 1: j + 1])
    ma20 = rolling_ma(c, 20)[j]
    return {
        "ex_dd3": float(c[j] / pb.peak_px - 1.0),
        "ex_vol3": (w3 / leg_v if np.isfinite(leg_v) and leg_v > 0 and np.isfinite(w3)
                    else np.nan),
        "ex_ma20_3": (float(c[j] / ma20 - 1.0) if np.isfinite(ma20) and ma20 > 0
                      else np.nan),
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_trend_exit_research.py -v`
Expected: 全部 passed。

- [ ] **Step 5: Commit**

```bash
git add scripts/trend_exit_research/features.py davis_analyzer/tests/test_trend_exit_research.py
git commit -m "feat(实验0008): 回调判别特征——谷底口径+峰值+3日事前口径双轨"
```

---

### Task 4: 退出规则族回放 `exits.py`

**Files:**
- Create: `scripts/trend_exit_research/exits.py`
- Test: `davis_analyzer/tests/test_trend_exit_research.py`(追加)

**Interfaces:**
- Consumes: `trend_machine.Episode/rolling_ma`、`pullback.find_pullbacks/LabelerParams`。
- Produces: `ExitRule(name, kind, param=0.0, exempt_recover=False)`;`default_rules() -> list[ExitRule]`(trail8/10/12/15、ma10/20/30/60 及 r 豁免变体、split_ma10_ma20、bench_machine、bench_label);`run_exit_rule(code, s, ep, rule, lp) -> dict`(字段:`rule, exit_pos, exit_px, reason, capture, hold_days, maxdd, sellfly10, sellfly20, gain10, gain20`);`exit_metrics(s, ep, exit_pos, exit_px) -> dict`(指标部分,供 run_exit_rule 内部与测试直用)。

指标定义(spec §4.5,实现与测试以此为准):`capture = exit_px / episode最高收盘`,最高收盘窗口 = `[entry_pos, max(ep.exit_pos, exit_pos)]` 内有限收盘最大值(规则晚于状态机退出时窗口延伸到规则退出日);`sellfly10/20` = 卖出后 10/20 交易日(完整序列,不截断)内有限收盘最大值 > 卖出前 `[entry_pos, exit_pos]` 最高收盘 → 1 否则 0;`gain10/gain20` = 该窗口最高收盘/exit_px-1(仅卖飞时非 NaN);`maxdd` = 持有期 `[entry_pos, exit_pos]` 收盘对累计峰值最大回撤。

- [ ] **Step 1: 追加失败测试**

```python
from exits import ExitRule, default_rules, exit_metrics, run_exit_rule  # noqa: E402


class TestExits:
    def _mk_ep(self):
        # entry 附近 10 → 峰 12.0 → 回落到 11.0(-8.3%) → 反弹到 12.5
        base = rising(150)
        closes = base + [11.0, 12.0, 11.0, 11.5, 12.5]
        s = mk_stock(closes)
        ep = Episode("T.SZ", 300 + 148, s["end"], "open", 12.5)
        return s, ep

    def test_trailing_8_exits_at_11(self):
        s, ep = self._mk_ep()
        r = run_exit_rule("T.SZ", s, ep, ExitRule("trail8", "trail", 0.08), LP)
        assert r["exit_px"] == pytest.approx(11.0)
        assert r["reason"] == "trail"

    def test_capture_and_sellfly_hand_numbers(self):
        s, ep = self._mk_ep()
        m = exit_metrics(s, ep, s["end"] - 1, 11.0)     # 在 11.0 那天卖出
        # episode最高收盘 = 12.5(含状态机退出日=数据尽头)→ capture = 11/12.5
        assert m["capture"] == pytest.approx(11.0 / 12.5, rel=1e-6)
        assert m["sellfly20"] == 1                       # 之后 12.5 > 卖前高点 12.0
        assert m["gain20"] == pytest.approx(12.5 / 11.0 - 1, rel=1e-6)

    def test_ma_rule_sells_on_break(self):
        base = rising(150)
        closes = base + [11.0, 12.0, 11.5, 11.0, 10.5, 10.0, 9.5]
        s = mk_stock(closes)
        ep = Episode("T.SZ", 300 + 148, s["end"], "open", 12.0)
        r = run_exit_rule("T.SZ", s, ep, ExitRule("ma10", "ma", 10.0), LP)
        assert r["reason"] == "ma_break"
        assert r["exit_px"] < 11.5                       # 深度跌破才触发

    def test_ma_recover_exempt_needs_two_days(self):
        base = rising(150)
        # 单日下破次日收回 → 不卖;随后连续两日下破 → 第二日卖
        closes = base + [11.0, 12.0, 11.0, 11.6, 10.9, 10.8]
        s = mk_stock(closes)
        ep = Episode("T.SZ", 300 + 148, s["end"], "open", 12.0)
        r = run_exit_rule("T.SZ", s, ep, ExitRule("ma10r", "ma", 10.0, True), LP)
        assert r["reason"] == "ma_break2"
        assert r["exit_px"] == pytest.approx(10.8)

    def test_split_blended_price(self):
        base = rising(150)
        closes = base + [11.0, 12.0, 11.0, 10.6, 10.2, 9.9, 9.6]
        s = mk_stock(closes)
        ep = Episode("T.SZ", 300 + 148, s["end"], "open", 12.0)
        r = run_exit_rule("T.SZ", s, ep, ExitRule("split", "split"), LP)
        assert r["reason"] == "split_full"
        # 卖价 = 0.5×破MA10日收盘 + 0.5×破MA20日收盘(两日由实现确定,按恒等式验证)
        c_at = {300 + i: v for i, v in enumerate(closes)}
        assert r["exit_px"] == pytest.approx(
            0.5 * 11.0 + 0.5 * c_at[r["exit_pos"]], rel=1e-6)

    def test_default_rules_count(self):
        names = [r.name for r in default_rules()]
        assert names[:4] == ["trail8", "trail10", "trail12", "trail15"]
        assert "bench_machine" in names and "bench_label" in names
        assert len(names) == 4 + 8 + 1 + 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_trend_exit_research.py -v -k Exits`
Expected: FAIL — `ModuleNotFoundError: No module named 'exits'`

- [ ] **Step 3: 实现 `exits.py`**

```python
"""退出规则族回放(实验0008,spec §4.4/4.5)。持有者视角:进入日满仓,收盘决策,卖出不回补。"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from trend_machine import Episode, rolling_ma
from pullback import LabelerParams, find_pullbacks


@dataclass(frozen=True)
class ExitRule:
    name: str
    kind: str                 # "trail" | "ma" | "split" | "bench_machine" | "bench_label"
    param: float = 0.0        # trail: 回落比例(正数);ma: 窗口
    exempt_recover: bool = False


def default_rules() -> list[ExitRule]:
    rules = [ExitRule(f"trail{int(x * 100)}", "trail", x) for x in (0.08, 0.10, 0.12, 0.15)]
    for n in (10, 20, 30, 60):
        rules.append(ExitRule(f"ma{n}", "ma", float(n)))
        rules.append(ExitRule(f"ma{n}r", "ma", float(n), True))
    rules.append(ExitRule("split_ma10_ma20", "split"))
    rules.append(ExitRule("bench_machine", "bench_machine"))
    rules.append(ExitRule("bench_label", "bench_label"))
    return rules


def exit_metrics(s: dict, ep: Episode, exit_pos: int, exit_px: float) -> dict:
    c = s["c"].astype(np.float64)
    end_w = max(ep.exit_pos, exit_pos)
    seg = c[ep.entry_pos: end_w + 1]
    ep_max = float(seg[np.isfinite(seg)].max())
    pre = c[ep.entry_pos: exit_pos + 1]
    pre = pre[np.isfinite(pre)]
    pre_high = float(pre.max())
    run = np.maximum.accumulate(pre)
    maxdd = float((pre / run - 1.0).min())
    out: dict = {"exit_px": float(exit_px), "capture": float(exit_px / ep_max),
                 "hold_days": int(exit_pos - ep.entry_pos), "maxdd": maxdd,
                 "sellfly10": 0, "sellfly20": 0, "gain10": np.nan, "gain20": np.nan}
    for hz, kflag, kgain in ((10, "sellfly10", "gain10"), (20, "sellfly20", "gain20")):
        w = c[exit_pos + 1: exit_pos + 1 + hz]
        w = w[np.isfinite(w)]
        if w.size and float(w.max()) > pre_high:
            out[kflag] = 1
            out[kgain] = float(w.max() / exit_px - 1.0)
    return out


def run_exit_rule(code: str, s: dict, ep: Episode, rule: ExitRule,
                  lp: LabelerParams) -> dict:
    c = s["c"].astype(np.float64)

    def result(pos: int, reason: str, px: float | None = None) -> dict:
        m = exit_metrics(s, ep, pos, float(c[pos]) if px is None else px)
        m.update({"rule": rule.name, "exit_pos": int(pos), "reason": reason})
        return m

    if rule.kind == "bench_machine":
        return result(ep.exit_pos, "machine")
    if rule.kind == "bench_label":
        term = next((p for p in find_pullbacks(code, s, ep, lp)
                     if p.outcome == "terminate"), None)
        pos = term.end_pos if term is not None else ep.exit_pos
        return result(pos, "label_term" if term is not None else "machine_fallback")

    ma_arr = rolling_ma(c, int(rule.param)) if rule.kind == "ma" else None
    ma10 = rolling_ma(c, 10) if rule.kind == "split" else None
    ma20 = rolling_ma(c, 20) if rule.kind == "split" else None
    hold_high = float(c[ep.entry_pos])
    below_prev = False
    split_px: float | None = None        # split: 破MA10日的半仓卖价
    for i in range(ep.entry_pos + 1, s["end"] + 1):
        if not np.isfinite(c[i]):
            continue
        hold_high = max(hold_high, float(c[i]))
        if rule.kind == "trail":
            if c[i] / hold_high - 1.0 <= -rule.param:
                return result(i, "trail")
        elif rule.kind == "ma":
            ma = ma_arr[i]
            if np.isfinite(ma) and c[i] < ma:
                if not rule.exempt_recover:
                    return result(i, "ma_break")
                if below_prev:                    # 连续两日收在线下才卖
                    return result(i, "ma_break2")
                below_prev = True
            else:
                below_prev = False
        elif rule.kind == "split":
            if split_px is None and np.isfinite(ma10[i]) and c[i] < ma10[i]:
                split_px = float(c[i])           # 减半
            if np.isfinite(ma20[i]) and c[i] < ma20[i]:
                if split_px is None:
                    return result(i, "split_direct")   # 未及减半直接破 MA20
                return result(i, "split_full", px=0.5 * split_px + 0.5 * float(c[i]))
    # 持有到数据尽头仍未触发
    if rule.kind == "split" and split_px is not None:
        return result(s["end"], "split_open_half", px=0.5 * split_px + 0.5 * float(c[s["end"]]))
    return result(s["end"], "open")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_trend_exit_research.py -v`
Expected: 全部 passed。`test_ma_rule_sells_on_break`/`test_split_blended_price` 若因 MA 数值边界不过,允许微调测试序列(保持"规则按定义触发"的语义),不得改规则定义。

- [ ] **Step 5: Commit**

```bash
git add scripts/trend_exit_research/exits.py davis_analyzer/tests/test_trend_exit_research.py
git commit -m "feat(实验0008): 退出规则族回放——trail/MA(含收回豁免)/分批/双基准+捕获率卖飞率指标"
```

---

### Task 5: 数据加载 `common.py` + 小样本 DB 冒烟

**Files:**
- Create: `scripts/trend_exit_research/common.py`

**Interfaces:**
- Produces: `MarketData(cal, cal_pos, stocks, valid_pos, aux_inf, aux_mf)` dataclass;`load_market(start_date=20140601, end_date=20260826, codes_limit=0) -> MarketData`;`log(msg)`。`stocks[code]` 即前述数组 dict;`cal` 为 int 日期(yyyymmdd)升序数组,`cal_pos` 为日期→下标 dict。

- [ ] **Step 1: 实现 `common.py`(无单测——薄加载层靠 Task 7 烟测覆盖;数据坑处理从 washout 复制)**

```python
"""数据加载:market_data.db → 前复权数组(实验0008)。

改编自 scripts/washout_research/detect_washout.py 的 build_arrays(不跨目录 import,
其模块级 os.chdir 有副作用);数据坑沉淀见该文件注释:adj_factor 缺失按股 ffill/bfill、
日历用全市场 daily_price 日期并集、universe 剔除现名含 ST/退 与北交所。
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

DB = "storage/database/market_data.db"
OUT_DIR = "studies/output/trend_exit"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


@dataclass
class MarketData:
    cal: np.ndarray                       # int yyyymmdd 升序
    cal_pos: dict[int, int]
    stocks: dict[str, dict]
    valid_pos: dict[str, np.ndarray]
    aux_inf: dict[str, dict] | None       # 2021-01 起才有;start_date 早于此时由 SQL 结果自然为空
    aux_mf: dict[str, dict] | None


def load_market(start_date: int = 20140601, end_date: int = 20260826,
                codes_limit: int = 0) -> MarketData:
    con = sqlite3.connect(DB)
    log("加载 stock_basic ...")
    sb = pd.read_sql("SELECT ts_code, name FROM stock_basic", con)
    log("加载 daily_price ...")
    dp = pd.read_sql(
        f"SELECT ts_code, trade_date, open, high, low, close, vol, amount, adj_factor "
        f"FROM daily_price WHERE trade_date>={start_date} AND trade_date<={end_date} "
        f"ORDER BY ts_code, trade_date", con)
    dp["trade_date"] = dp["trade_date"].astype(int)
    log("加载 intraday_feature ...")
    inf = pd.read_sql(
        "SELECT ts_code, trade_date, upper_shadow, close_position, amplitude "
        "FROM intraday_feature", con)
    inf["trade_date"] = inf["trade_date"].astype(int)
    log("加载 moneyflow ...")
    mf = pd.read_sql(
        "SELECT ts_code, trade_date, buy_lg_amount, sell_lg_amount, "
        "buy_elg_amount, sell_elg_amount FROM moneyflow", con)
    mf["trade_date"] = mf["trade_date"].astype(int)
    con.close()

    cal = np.sort(dp["trade_date"].unique())
    cal_pos = {int(d): i for i, d in enumerate(cal)}
    n_cal = len(cal)
    log(f"交易日历 {cal[0]}~{cal[-1]} 共 {n_cal} 天(全市场日期并集)")

    valid_prefix = ("60", "00", "30", "68")
    sb = sb[sb["ts_code"].str[:2].isin(valid_prefix)]
    bad = sb["name"].astype(str).str.contains("ST|退", na=False)
    universe = set(sb.loc[~bad, "ts_code"])
    if codes_limit:
        universe = set(sorted(universe)[:codes_limit])
    log(f"股票池 {len(universe)} 只(现名无 ST/退,含已退市,北交所剔除)")

    dp = dp[dp["ts_code"].isin(universe)]
    dp["adj_factor"] = dp.groupby("ts_code")["adj_factor"].ffill()
    dp["adj_factor"] = dp.groupby("ts_code")["adj_factor"].bfill()
    dp["adj_factor"] = dp["adj_factor"].fillna(1.0)
    last_adj = dp.groupby("ts_code")["adj_factor"].last()
    dp["k"] = dp["ts_code"].map(last_adj)
    for col in ("open", "high", "low", "close"):
        dp[col] = (dp[col] * dp["adj_factor"] / dp["k"]).astype(np.float32)
    dp["vol"] = dp["vol"].astype(np.float32)
    dp["amount"] = dp["amount"].astype(np.float32)

    stocks: dict[str, dict] = {}
    valid_pos: dict[str, np.ndarray] = {}
    for code, g in dp.groupby("ts_code", sort=False):
        pos = np.searchsorted(cal, g["trade_date"].to_numpy())
        arrs = {}
        for key, col in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"),
                         ("v", "vol"), ("a", "amount")):
            a = np.full(n_cal, np.nan, np.float32)
            a[pos] = g[col].to_numpy()
            arrs[key] = a
        arrs["end"] = int(pos[-1])
        stocks[code] = arrs
        valid_pos[code] = np.flatnonzero(np.isfinite(arrs["c"]))
    log(f"构建每股数组 {len(stocks)} 只")

    def build_aux(df: pd.DataFrame, cols: list[str]) -> dict[str, dict]:
        df = df[df["ts_code"].isin(universe)].copy()
        df["pos"] = df["trade_date"].map(cal_pos)
        df = df.dropna(subset=["pos"])
        df["pos"] = df["pos"].astype(np.int32)
        df = df.sort_values(["ts_code", "pos"])
        out: dict[str, dict] = {}
        for c_, g in df.groupby("ts_code", sort=False):
            d = {"pos": g["pos"].to_numpy(np.int32)}
            for col in cols:
                d[col] = g[col].to_numpy(np.float32)
            out[c_] = d
        return out

    aux_inf = build_aux(inf, ["upper_shadow", "close_position", "amplitude"])
    aux_mf = build_aux(mf, ["buy_lg_amount", "sell_lg_amount",
                            "buy_elg_amount", "sell_elg_amount"])
    log("数据准备完成")
    return MarketData(cal, cal_pos, stocks, valid_pos, aux_inf, aux_mf)
```

- [ ] **Step 2: 冒烟——20 只股票加载并跑状态机**

Run(仓库根目录,一次性验证,不留文件):
```bash
.venv/bin/python -c "
import sys; sys.path.insert(0, 'scripts/trend_exit_research')
from common import load_market, log
from trend_machine import TrendParams, find_episodes
md = load_market(codes_limit=20)
n_ep = sum(len(find_episodes(c, s, TrendParams())) for c, s in md.stocks.items())
log(f'20只股票 episodes 总数 = {n_ep}')
assert n_ep > 0, '状态机未切出任何 episode'
"
```
Expected: 打印加载日志,episodes 总数 > 0(5 年半日线、20 只活跃股,至少几十段)。若 adj_factor 或日历异常,对照 washout 注释排查,不要绕过。

- [ ] **Step 3: Commit**

```bash
git add scripts/trend_exit_research/common.py
git commit -m "feat(实验0008): market_data.db 加载层——2015起全A前复权数组,复用washout数据坑处理"
```

---

### Task 6: 稳健性测试台 `bootstrap.py`

**Files:**
- Create: `scripts/trend_exit_research/bootstrap.py`
- Test: `davis_analyzer/tests/test_trend_exit_research.py`(追加)

**Interfaces:**
- Consumes: 事件明细 DataFrame(`pullbacks_df`:含 `ts_code, ep_entry_date, peak_date, outcome, pb_depth, pb_days, vol_ratio, ex_dd3, ex_vol3`;`exits_df`:含 `ts_code, ep_entry_date, rule, capture, sellfly20`)。
- Produces: `sample_window(rng, cal) -> tuple[int, int]`(60~180 交易日随机窗,返回日期 int);`sample_pool(rng, universe, frac) -> set[str]`;`filter_events(df, window, pool, date_col) -> pd.DataFrame`;`run_bootstrap(pullbacks_df, exits_df, cal, trials=16, seed=2026, min_events=30) -> pd.DataFrame`(长表:`mode, trial, kind, name, metric, diff, n, n_dropped, sign_match`);`main(out_dir=OUT_DIR) -> pd.DataFrame`(读 CSV 入口,写 robustness.csv)。
- 纯函数设计:测试直接喂构造 DataFrame,不碰文件。

- [ ] **Step 1: 追加失败测试**

```python
from bootstrap import (filter_events, run_bootstrap,  # noqa: E402
                       sample_pool, sample_window)

CAL = np.arange(1, 401) * 10          # 假日期 10..4000,窗口运算按数值即可


def _mk_pullbacks(n_stocks: int = 30) -> pd.DataFrame:
    rows = []
    for k in range(n_stocks):
        for j in range(6):
            rows.append({
                "ts_code": f"S{k:03d}.SZ", "ep_entry_date": 500 + 30 * j,
                "peak_date": 510 + 30 * j,
                "outcome": "continue" if (k + j) % 2 == 0 else "terminate",
                "pb_depth": -0.05 - 0.002 * k, "pb_days": j % 5,
                "vol_ratio": 0.5 + 0.02 * k, "ex_dd3": -0.04, "ex_vol3": 0.9,
            })
    return pd.DataFrame(rows)


def _mk_exits(pb: pd.DataFrame) -> pd.DataFrame:
    eps = pb[["ts_code", "ep_entry_date"]].drop_duplicates()
    offset = {"bench_machine": 0, "trail8": 1, "ma20": 2}   # 确定性偏移,不用 hash()
    rows = []
    for rule, cap in (("bench_machine", 0.90), ("trail8", 0.85), ("ma20", 0.80)):
        for _, r in eps.iterrows():
            rows.append({"ts_code": r["ts_code"], "ep_entry_date": r["ep_entry_date"],
                         "rule": rule, "capture": cap + offset[rule] * 0.001,
                         "sellfly20": int(rule != "bench_machine")})
    return pd.DataFrame(rows)


class TestBootstrap:
    def test_seeded_reproducible(self):
        pb, ex = _mk_pullbacks(), _mk_exits(_mk_pullbacks())
        a = run_bootstrap(pb, ex, CAL, trials=4)
        b = run_bootstrap(pb, ex, CAL, trials=4)
        pd.testing.assert_frame_equal(a, b)

    def test_pool_sampling_is_stock_level(self):
        rng = np.random.default_rng(7)
        pool = sample_pool(rng, set(f"S{k:03d}.SZ" for k in range(100)), 0.5)
        sub = filter_events(_mk_pullbacks(100), (0, 10**9), pool, "peak_date")
        assert set(sub["ts_code"]) <= pool
        assert 40 <= len(pool) <= 60                     # 100×50%

    def test_window_membership_by_peak_date(self):
        pb = _mk_pullbacks()
        sub = filter_events(pb, (530, 560), None, "peak_date")
        assert ((sub["peak_date"] >= 530) & (sub["peak_date"] <= 560)).all()

    def test_small_samples_dropped_and_counted(self):
        pb = _mk_pullbacks(2)                            # 12 事件 < 30
        ex = _mk_exits(pb)
        out = run_bootstrap(pb, ex, CAL, trials=2, min_events=30)
        assert (out["n_dropped"] >= 1).any() or len(out) == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_trend_exit_research.py -v -k Bootstrap`
Expected: FAIL — `ModuleNotFoundError: No module named 'bootstrap'`

- [ ] **Step 3: 实现 `bootstrap.py`**

```python
"""稳健性测试台:随机窗口×随机票池重采样(实验0008,spec §4.6,0003 纪律事件研究适配)。

重聚合而非重跑:基于 episodes/pullbacks/exits 明细重新汇总;抽样单元=股票;
配对设计:规则与基准在同子样本内差值;方向一致率 = 子样本差值符号与全量一致。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import OUT_DIR, log

SEED = 2026
TRIALS = 16
WINDOW_RANGE = (60, 181)      # uniform(60,180] 交易日
POOL_FRAC_RANGE = (0.60, 1.00)
MIN_EVENTS = 30
FEATURES = ["pb_depth", "pb_days", "vol_ratio", "ex_dd3", "ex_vol3"]


def sample_window(rng: np.random.Generator, cal: np.ndarray) -> tuple[int, int]:
    length = int(rng.integers(WINDOW_RANGE[0], WINDOW_RANGE[1]))
    i0 = int(rng.integers(0, max(1, len(cal) - length)))
    return int(cal[i0]), int(cal[i0 + length - 1])


def sample_pool(rng: np.random.Generator, universe: set[str], frac: float) -> set[str]:
    size = max(1, int(round(len(universe) * frac)))
    return set(rng.choice(sorted(universe), size=size, replace=False).tolist())


def filter_events(df: pd.DataFrame, window: tuple[int, int] | None,
                  pool: set[str] | None, date_col: str) -> pd.DataFrame:
    out = df
    if window is not None:
        out = out[(out[date_col] >= window[0]) & (out[date_col] <= window[1])]
    if pool is not None:
        out = out[out["ts_code"].isin(pool)]
    return out


def _rule_diffs(ex_sub: pd.DataFrame) -> dict[tuple[str, str], float]:
    """各规则对 bench_machine 的同子样本配对差值(中位数口径)。"""
    base = ex_sub[ex_sub["rule"] == "bench_machine"]
    if base.empty:
        return {}
    med_cap = base["capture"].median()
    med_sf = base["sellfly20"].mean()
    out: dict[tuple[str, str], float] = {}
    for rule in ex_sub["rule"].unique():
        if rule == "bench_machine":
            continue
        sub = ex_sub[ex_sub["rule"] == rule]
        out[(rule, "capture")] = float(sub["capture"].median() - med_cap)
        out[(rule, "sellfly20")] = float(sub["sellfly20"].mean() - med_sf)
    return out


def _feature_signs(pb_sub: pd.DataFrame) -> dict[str, float]:
    """判别特征四分位首尾差符号(continue 比例 Q4-Q1,二分样本)。"""
    df = pb_sub[pb_sub["outcome"].isin(["continue", "terminate"])].dropna(
        subset=["pb_depth"])
    out: dict[str, float] = {}
    for f in FEATURES:
        d = df.dropna(subset=[f])
        if len(d) < MIN_EVENTS:
            continue
        try:
            q = pd.qcut(d[f], 4, labels=False, duplicates="drop")
        except ValueError:
            continue
        if q.nunique() < 4:
            continue
        rate = d.assign(q=q).groupby("q")["outcome"].apply(
            lambda x: (x == "continue").mean())
        out[f] = float(rate.max() - rate.min())
    return out


def run_bootstrap(pullbacks_df: pd.DataFrame, exits_df: pd.DataFrame, cal: np.ndarray,
                  trials: int = TRIALS, seed: int = SEED,
                  min_events: int = MIN_EVENTS) -> pd.DataFrame:
    universe = set(pullbacks_df["ts_code"].unique())
    # 全量口径(方向一致率的参照)
    full_rules = _rule_diffs(exits_df)
    full_feats = _feature_signs(pullbacks_df)

    rows: list[dict] = []
    mode_offset = {"window": 0, "pool": 1, "both": 2}   # 确定性(勿用 hash,跨进程不稳定)
    for mode in ("window", "pool", "both"):
        rng = np.random.default_rng(seed + mode_offset[mode])
        for trial in range(trials):
            window = sample_window(rng, cal) if mode in ("window", "both") else None
            pool = (sample_pool(rng, universe, rng.uniform(*POOL_FRAC_RANGE))
                    if mode in ("pool", "both") else None)
            pb_sub = filter_events(pullbacks_df, window, pool, "peak_date")
            n_ev = len(pb_sub)
            if n_ev < min_events:
                rows.append({"mode": mode, "trial": trial, "kind": "dropped",
                             "name": "", "metric": "", "diff": np.nan,
                             "n": n_ev, "n_dropped": 1, "sign_match": np.nan})
                continue
            ex_sub = filter_events(exits_df, window, pool, "ep_entry_date")
            for (name, metric), diff in _rule_diffs(ex_sub).items():
                full = full_rules.get((name, metric), np.nan)
                rows.append({"mode": mode, "trial": trial, "kind": "rule",
                             "name": name, "metric": metric, "diff": diff,
                             "n": n_ev, "n_dropped": 0,
                             "sign_match": int(np.sign(diff) == np.sign(full))
                             if np.isfinite(diff) and np.isfinite(full) else np.nan})
            for name, diff in _feature_signs(pb_sub).items():
                full = full_feats.get(name, np.nan)
                rows.append({"mode": mode, "trial": trial, "kind": "feature",
                             "name": name, "metric": "q4_q1_continue", "diff": diff,
                             "n": n_ev, "n_dropped": 0,
                             "sign_match": int(np.sign(diff) == np.sign(full))
                             if np.isfinite(diff) and np.isfinite(full) else np.nan})
    df = pd.DataFrame(rows)
    summary = (df[df["kind"] != "dropped"]
               .groupby(["kind", "name", "metric", "mode"], as_index=False)
               .agg(consistency=("sign_match", "mean"),
                    mean_diff=("diff", "mean"), n_trials=("sign_match", "count")))
    df.attrs["summary"] = summary
    return df


def main(out_dir: str = OUT_DIR) -> pd.DataFrame:
    import os
    pb = pd.read_csv(f"{out_dir}/pullbacks.csv")
    ex = pd.read_csv(f"{out_dir}/exits.csv")
    cal = np.loadtxt(f"{out_dir}/calendar.txt", dtype=int)
    df = run_bootstrap(pb, ex, cal)
    df.to_csv(f"{out_dir}/robustness.csv", index=False)
    df.attrs["summary"].to_csv(f"{out_dir}/robustness_summary.csv", index=False)
    log(f"bootstrap 完成:{len(df)} 行 → robustness.csv / robustness_summary.csv")
    return df


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_trend_exit_research.py -v`
Expected: 全部 passed。

- [ ] **Step 5: Commit**

```bash
git add scripts/trend_exit_research/bootstrap.py davis_analyzer/tests/test_trend_exit_research.py
git commit -m "feat(实验0008): 稳健性测试台——三模式重采样/股票级抽样/配对差值方向一致率"
```

---

### Task 7: 编排 `run_all.py` + 汇总 `analyze.py` + 20 只烟测

**Files:**
- Create: `scripts/trend_exit_research/run_all.py`
- Create: `scripts/trend_exit_research/analyze.py`

**Interfaces:**
- Consumes: 前述全部模块;`common.MarketData`。
- Produces: `studies/output/trend_exit/` 下 `episodes.csv`(ts_code, entry_date, exit_date, entry_pos, exit_pos, exit_reason, peak_close, pos_pct_entry)、`pullbacks.csv`(Pullback 字段 + peak_date/trough_date/end_date + features + exante 列)、`exits.csv`(run_exit_rule 结果 + ts_code/ep_entry_pos/ep_entry_date + cost 口位列 capture_net = exit_px×0.9987/episode最高收盘——由 analyze 重算,CSV 只存 exit_px/capture 原值)、`calendar.txt`、`universe.txt`、`sensitivity.json`、`sensitivity_labeler.json`;`analyze.py main(out_dir) -> str` 写 `analysis_report.txt`。
- `run_all.py` argparse:`--start 20150105 --end 20260826 --codes-limit 0 --sensitivity/--no-sensitivity --skip-bootstrap`。episode 窗口过滤:entry_date ∈ [start, end];主窗口(2021-01-04 起)与外推窗口(2015~2020)由 analyze 按 entry_date 拆分,不重复跑。

- [ ] **Step 1: 实现 `run_all.py`**

```python
"""实验0008 编排:加载→状态机→回调→特征→退出规则→bootstrap→汇总。

全量预估 <30 分钟;先 --codes-limit 20 烟测再全量。敏感性网格 9+9 组合逐组落盘。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bootstrap import run_bootstrap                          # noqa: E402
from common import OUT_DIR, load_market, log                  # noqa: E402
from exits import default_rules, run_exit_rule                # noqa: E402
from features import exante_features, pullback_features       # noqa: E402
from pullback import LabelerParams, find_pullbacks            # noqa: E402
from trend_machine import TrendParams, find_episodes          # noqa: E402

MAIN_WINDOW_START = 20210104     # 主窗口/外推窗口拆分线(analyze 用)


def scan(md, p: TrendParams, lp: LabelerParams, start: int, end: int,
         with_features: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """全市场扫描,返回 (episodes_df, pullbacks_df)。with_features=False 供敏感性复用。"""
    ep_rows, pb_rows = [], []
    t0 = time.time()
    date_of = {i: int(d) for d, i in md.cal_pos.items()}
    for k, (code, s) in enumerate(md.stocks.items(), 1):
        if k % 500 == 0:
            log(f"  扫描 {k}/{len(md.stocks)} 只, episodes={len(ep_rows)}, pullbacks={len(pb_rows)}")
        for ep in find_episodes(code, s, p):
            d0 = date_of[ep.entry_pos]
            if d0 < start or d0 > end:
                continue
            hist = md.valid_pos[code]
            hist = hist[hist < ep.entry_pos][-250:]
            prior = s["c"][hist]
            prior = prior[np.isfinite(prior)]
            pos_pct = (float((prior < s["c"][ep.entry_pos]).sum()) / prior.size * 100.0
                       if prior.size >= 120 else np.nan)
            ep_rows.append({"ts_code": code, "entry_date": d0,
                            "exit_date": date_of[min(ep.exit_pos, s["end"])],
                            "entry_pos": ep.entry_pos, "exit_pos": ep.exit_pos,
                            "exit_reason": ep.exit_reason, "peak_close": ep.peak_close,
                            "pos_pct_entry": pos_pct})
            for pb in find_pullbacks(code, s, ep, lp):
                row = {"ts_code": code, "ep_entry_pos": ep.entry_pos,
                       "ep_entry_date": d0, "idx": pb.idx,
                       "peak_pos": pb.peak_pos, "peak_date": date_of[pb.peak_pos],
                       "trough_pos": pb.trough_pos, "trough_date": date_of[pb.trough_pos],
                       "end_pos": pb.end_pos, "end_date": date_of[min(pb.end_pos, s["end"])],
                       "peak_px": pb.peak_px, "trough_px": pb.trough_px,
                       "outcome": pb.outcome}
                if with_features:
                    row.update(pullback_features(code, s, ep, pb,
                                                 md.aux_inf, md.aux_mf))
                    row.update(exante_features(code, s, ep, pb))
                pb_rows.append(row)
    log(f"扫描完成 episodes={len(ep_rows)} pullbacks={len(pb_rows)} 耗时 {(time.time()-t0)/60:.1f} 分钟")
    return pd.DataFrame(ep_rows), pd.DataFrame(pb_rows)


def replay_exits(md, eps_df: pd.DataFrame, lp: LabelerParams) -> pd.DataFrame:
    """对每个 episode 回放全部规则(按 ts_code 分桶,避免 O(股票×episode))。"""
    from trend_machine import Episode
    rules = default_rules()
    eps_by_code: dict[str, list] = {}
    for r in eps_df.itertuples():
        eps_by_code.setdefault(r.ts_code, []).append(r)
    rows = []
    t0 = time.time()
    for code, ep_list in eps_by_code.items():
        s = md.stocks.get(code)
        if s is None:
            continue
        for r in ep_list:
            ep = Episode(code, int(r.entry_pos), int(r.exit_pos),
                         r.exit_reason, r.peak_close)
            for rule in rules:
                m = run_exit_rule(code, s, ep, rule, lp)
                m.update({"ts_code": code, "ep_entry_pos": int(r.entry_pos),
                          "ep_entry_date": int(r.entry_date),
                          "pos_pct_entry": getattr(r, "pos_pct_entry", np.nan),
                          "exit_date": int(md.cal[m["exit_pos"]])})
                rows.append(m)
        if len(rows) % 50000 == 0 and rows:
            log(f"  规则回放已产出 {len(rows)} 行")
    log(f"规则回放完成 rows={len(rows)} 耗时 {(time.time()-t0)/60:.1f} 分钟")
    return pd.DataFrame(rows)


def sensitivity(md, start: int, end: int, out_dir: str) -> None:
    """参数敏感性网格:趋势机 9 组 + 标注器 9 组,逐组落盘。"""
    trend_grid = [(nh, dd) for nh in (40, 60, 120) for dd in (0.15, 0.20, 0.25)]
    for nh, dd in trend_grid:
        _, pb = scan(md, TrendParams(newhigh_win=nh, exit_dd=-dd),
                     LabelerParams(), start, end, with_features=False)
        vc = pb["outcome"].value_counts(normalize=True).to_dict()
        rec = {"newhigh_win": nh, "exit_dd": -dd, "n": len(pb),
               "continue": vc.get("continue"), "terminate": vc.get("terminate"),
               "timeout": vc.get("timeout")}
        _append_json(f"{out_dir}/sensitivity.json", rec)
        log(f"敏感性 趋势机 {nh}/{dd}: n={len(pb)} continue={vc.get('continue', 0):.3f}")
    lab_grid = [(td, lw) for td in (0.20, 0.25, 0.30) for lw in (30, 40, 60)]
    for td, lw in lab_grid:
        _, pb = scan(md, TrendParams(),
                     LabelerParams(term_dd=-td, term_low_win=lw), start, end,
                     with_features=False)
        vc = pb["outcome"].value_counts(normalize=True).to_dict()
        rec = {"term_dd": -td, "term_low_win": lw, "n": len(pb),
               "continue": vc.get("continue"), "terminate": vc.get("terminate")}
        _append_json(f"{out_dir}/sensitivity_labeler.json", rec)


def _append_json(path: str, rec: dict) -> None:
    items = []
    if os.path.exists(path):
        with open(path) as f:
            items = json.load(f)
    items.append(rec)
    with open(path, "w") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=20150105)
    ap.add_argument("--end", type=int, default=20260826)
    ap.add_argument("--codes-limit", type=int, default=0)
    ap.add_argument("--sensitivity", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--skip-bootstrap", action="store_true")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()
    md = load_market(codes_limit=args.codes_limit)
    eps_df, pb_df = scan(md, TrendParams(), LabelerParams(), args.start, args.end)
    ex_df = replay_exits(md, eps_df, LabelerParams())

    eps_df.to_csv(f"{OUT_DIR}/episodes.csv", index=False)
    pb_df.to_csv(f"{OUT_DIR}/pullbacks.csv", index=False)
    ex_df.to_csv(f"{OUT_DIR}/exits.csv", index=False)
    np.savetxt(f"{OUT_DIR}/calendar.txt", md.cal, fmt="%d")
    with open(f"{OUT_DIR}/universe.txt", "w") as f:
        f.write("\n".join(sorted(md.stocks)))
    log(f"明细落盘完成 耗时 {(time.time()-t0)/60:.1f} 分钟")

    if args.sensitivity and not args.codes_limit:
        for p in ("sensitivity.json", "sensitivity_labeler.json"):
            fp = f"{OUT_DIR}/{p}"
            if os.path.exists(fp):
                os.remove(fp)          # 重跑覆盖
        sensitivity(md, args.start, args.end, OUT_DIR)

    if not args.skip_bootstrap and len(pb_df):
        bdf = run_bootstrap(pb_df, ex_df, md.cal)
        bdf.to_csv(f"{OUT_DIR}/robustness.csv", index=False)
        bdf.attrs["summary"].to_csv(f"{OUT_DIR}/robustness_summary.csv", index=False)
        log("bootstrap 完成")

    import analyze
    analyze.main(OUT_DIR)
    log(f"run_all 总耗时 {(time.time()-t0)/60:.1f} 分钟")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 实现 `analyze.py`**

```python
"""实验0008 汇总报告:结局基率/特征判别表(三口径)/规则排行榜/分层/敏感性/稳健性。"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from common import OUT_DIR, log

MAIN_START = 20210104
COST = 0.0013          # 双边 13bps(计在卖出侧)


def _quartile_table(pb: pd.DataFrame, feature: str) -> pd.DataFrame:
    d = pb[pb["outcome"].isin(["continue", "terminate"])].dropna(subset=[feature]).copy()
    if len(d) < 100:
        return pd.DataFrame()
    try:
        d["q"] = pd.qcut(d[feature], 4, labels=["Q1", "Q2", "Q3", "Q4"], duplicates="drop")
    except ValueError:
        return pd.DataFrame()
    g = d.groupby("q", observed=True).agg(
        n=("outcome", "size"), p_continue=("outcome", lambda x: (x == "continue").mean()))
    return g


def main(out_dir: str = OUT_DIR) -> str:
    eps = pd.read_csv(f"{out_dir}/episodes.csv")
    pb = pd.read_csv(f"{out_dir}/pullbacks.csv")
    ex = pd.read_csv(f"{out_dir}/exits.csv")
    pb["window"] = np.where(pb["ep_entry_date"] >= MAIN_START, "main", "exante")
    ex["window"] = np.where(ex["ep_entry_date"] >= MAIN_START, "main", "exante")

    lines: list[str] = []
    A = lines.append
    A("=" * 72)
    A("实验0008 趋势回调与退出规则 — analysis_report")
    A("=" * 72)
    A(f"\nepisodes={len(eps)}  pullbacks={len(pb)}  exits={len(ex)}")

    A("\n## 1. 回调结局基率(全量/主窗口/外推,分年)")
    for w, d in pb.groupby("window"):
        A(f"\n[{w}] n={len(d)}  " +
          "  ".join(f"{k}={v:.3f}" for k, v in
                    d["outcome"].value_counts(normalize=True).items()))
    pb["year"] = (pb["peak_date"] // 10000).astype(int)
    by_year = pb.groupby(["year", "outcome"]).size().unstack(fill_value=0)
    A("\n" + by_year.to_string())

    A("\n## 2. 判别特征四分位表(continue 率,主窗口;谷底口径+事前口径)")
    pbm = pb[pb["window"] == "main"]
    for f in ("pb_days", "pb_depth", "vol_ratio", "close_vs_ma20", "mf_wash",
              "pos_pct_entry", "ep_gain_at_peak", "ex_dd3", "ex_vol3", "ex_ma20_3"):
        t = _quartile_table(pbm, f)
        if not t.empty:
            A(f"\n### {f}\n{t.to_string()}")

    A("\n## 3. 退出规则排行榜(主窗口,成本 0 与 13bps)")
    exm = ex[ex["window"] == "main"].copy()
    exm["capture_net"] = exm["capture"] * (1 - COST)
    lb = exm.groupby("rule").agg(
        n=("capture", "size"), capture_med=("capture", "median"),
        capture_net_med=("capture_net", "median"),
        sellfly20=("sellfly20", "mean"), gain20_med=("gain20", "median"),
        maxdd_med=("maxdd", "median"), hold_med=("hold_days", "median"))
    A("\n" + lb.sort_values("capture_med", ascending=False).to_string())

    A("\n## 3b. 排行榜(外推窗口 2015-2020)")
    exe = ex[ex["window"] == "exante"]
    if len(exe):
        lbe = exe.groupby("rule").agg(
            n=("capture", "size"), capture_med=("capture", "median"),
            sellfly20=("sellfly20", "mean"), maxdd_med=("maxdd", "median"))
        A("\n" + lbe.sort_values("capture_med", ascending=False).to_string())

    A("\n## 4. 分层切片(主窗口):位置三分位 × 规则 捕获率中位数")
    if "pos_pct_entry" in exm.columns:
        exm2 = exm.dropna(subset=["pos_pct_entry"]).copy()
        exm2["pos_tercile"] = pd.qcut(exm2["pos_pct_entry"], 3, labels=["low", "mid", "high"])
        t = exm2[exm2["rule"].isin(["bench_machine", "trail10", "ma20", "ma60",
                                    "split_ma10_ma20"])].pivot_table(
            index="rule", columns="pos_tercile", values="capture",
            aggfunc="median")
        A("\n" + t.to_string())

    A("\n## 5. 参数敏感性(趋势机/标注器)")
    for name in ("sensitivity.json", "sensitivity_labeler.json"):
        fp = f"{out_dir}/{name}"
        if os.path.exists(fp):
            with open(fp) as f:
                A(f"\n### {name}\n" + pd.DataFrame(json.load(f)).to_string(index=False))

    A("\n## 6. 稳健性(方向一致率)")
    fp = f"{out_dir}/robustness_summary.csv"
    if os.path.exists(fp):
        A("\n" + pd.read_csv(fp).to_string(index=False))
    else:
        A("\n(bootstrap 被跳过)")

    report = "\n".join(lines)
    with open(f"{out_dir}/analysis_report.txt", "w") as f:
        f.write(report)
    log(f"analysis_report.txt 写出({len(lines)} 段)")
    return report


if __name__ == "__main__":
    main()
```

注:analyze 第 4 节依赖 exits 表带 `pos_pct_entry` 列——已由上面的 `replay_exits` 从 eps_df 映射补上。

- [ ] **Step 3: 烟测(20 只股票全链路)**

Run: `.venv/bin/python scripts/trend_exit_research/run_all.py --codes-limit 20 --no-sensitivity --skip-bootstrap`
Expected: 退出码 0;`studies/output/trend_exit/` 下生成 6 个文件;`analysis_report.txt` 有 6 节内容;episodes/pullbacks/exits 行数 > 0。随后单独跑 bootstrap 冒烟:
```bash
.venv/bin/python scripts/trend_exit_research/bootstrap.py
```
Expected: robustness.csv 生成(20 只样本量小,大量 trial 可能 dropped——正常,只验证机制跑通)。

- [ ] **Step 4: 跑全部单测**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_trend_exit_research.py -v`
Expected: 全部 passed(确认 Task 5-7 未破坏内核)。

- [ ] **Step 5: Commit**

```bash
git add scripts/trend_exit_research/run_all.py scripts/trend_exit_research/analyze.py
git commit -m "feat(实验0008): run_all编排+analyze汇总——六节报告,主窗口/外推双拆分"
```

---

### Task 8: 全量运行(主窗口 + 外推 + 敏感性 + bootstrap)

**Files:**
- 无新代码;产出 `studies/output/trend_exit/*` 与运行日志。

- [ ] **Step 1: 会话内全量运行(预估 <30 分钟,先确认单测全绿)**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_trend_exit_research.py -q && .venv/bin/python scripts/trend_exit_research/run_all.py 2>&1 | tee logs/trend_exit_run.log`
Expected: 加载 ~2-4 分钟 → 扫描/回放 ~10-20 分钟 → 敏感性 9+9 组合 → bootstrap 48 trial → analysis_report.txt。若中途预估将超 30 分钟(观察前 5 分钟的扫描速率外推),中止并改按 AGENTS.md 长回测规范(setsid nohup 脱离会话 + 收尾 cron),重跑覆盖。

- [ ] **Step 2: 产物体检(机械检查,不做解读)**

检查项(全过才算完成):episodes.csv ≥ 5,000 行、pullbacks.csv ≥ 10,000 行、exits.csv = episodes × 15 规则;pullbacks 的 outcome 三值都有;analysis_report.txt 六节齐全;robustness_summary.csv 的 mode 列含 window/pool/both;sensitivity.json 恰 9 条、sensitivity_labeler.json 恰 9 条。任何一项不满足 → 回到对应 Task 修,不得带病进 Task 9。

- [ ] **Step 3: Commit(数据产物不入库,只留日志)**

```bash
echo "studies/output/trend_exit/" >> .gitignore
git add .gitignore logs/trend_exit_run.log 2>/dev/null || git add .gitignore
git commit -m "feat(实验0008): 全量运行完成——episodes/pullbacks/exits/robustness落盘"
```
注:`studies/` 若已在 .gitignore 则跳过追加;logs/ 视项目 .gitignore 情况,不入库就只 commit .gitignore。

---

### Task 9: 研究报告 + 实验日志 0008 + 提交

**Files:**
- Create: `docs/回测记录/趋势回调与退出研究_<完成日>.md`
- Create: `docs/回测记录/实验日志/0008_2026-08-27_趋势回调与退出规则.md`
- Modify: `docs/回测记录/实验日志/README.md`(条目索引表加 0008 行)

- [ ] **Step 1: 读分析产物并撰写研究报告**

通读 `analysis_report.txt`(原生命令完整读取,不用 rtk——数字要精读),按 0006 报告的体例写 `docs/回测记录/趋势回调与退出研究_<完成日>.md`:预注册定义复述、结局基率、判别特征表(重点:vol_ratio 放量/缩量方向是否复现 0006;pos_pct_entry 位置先于形态是否成立)、退出规则排行榜(捕获率/卖飞率/回撤,成本双口径)、外推窗口对比、敏感性方向稳定性、bootstrap 方向一致率、局限性(含 ST 名近似、聚集效应)。所有数字必须能在 analysis_report.txt 或 CSV 中找到出处;**按 spec §6 判定线机械下结论,负结果照写**。

- [ ] **Step 2: 写实验日志 0008(六节模板)**

按 README 模板:思路来源(用户提出"避免卖飞/精确止盈",0006/0004 的增量定位)/假设(可证伪四问 Q1-Q4)/实验设计(引用 spec,不再重复)/结果(报告核心表)/决策(按 §6 判定线:判别表与退出候选各自 采纳/否决/条件采纳)/遗留(Phase 2 分钟级、holdings_check 接入条件)。

- [ ] **Step 3: 更新实验日志 README 索引 + 提交**

```bash
git add "docs/回测记录/趋势回调与退出研究_<完成日>.md" "docs/回测记录/实验日志/0008_2026-08-27_趋势回调与退出规则.md" "docs/回测记录/实验日志/README.md"
git commit -m "docs(实验): 0008 趋势回调判别与退出规则——<一句话核心结论>"
```
(文件名占位 `<完成日>` 由执行者替换为实际日期;提交信息的一句话结论以报告 §决策 为准。)
