"""上涨趋势状态机:episode 进入/退出(实验0008,预注册参数见 spec §4.1)。

进入(三条件全满足,日收盘、前复权):收盘创 60 日新高;MA20>MA60 且 MA20 上行;
上市历史 ≥120 个有限收盘(universe 过滤在 common.py,状态机不关心)。
退出(先到先得):收盘自 episode 最高收盘回撤 ≤ -20%;或收盘连续 2 日跌破 MA60。
"""
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
