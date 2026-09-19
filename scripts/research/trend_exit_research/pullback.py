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

    if not np.isfinite(c[ep.entry_pos]):
        return out
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
