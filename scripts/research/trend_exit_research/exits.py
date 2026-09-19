"""退出规则族回放(实验0008,spec §4.4/4.5)。持有者视角:进入日满仓,收盘决策,卖出不回补。

指标口径:capture = exit_px / episode最高收盘(窗口 [entry, max(状态机退出日, 规则退出日)]);
sellfly10/20 = 卖出后 10/20 交易日(完整序列)内收盘创卖出前 episode 新高;
maxdd = 持有期收盘对累计峰值最大回撤;分批卖价 = 两段卖价各半加权。
"""
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
        return result(s["end"], "split_open_half",
                      px=0.5 * split_px + 0.5 * float(c[s["end"]]))
    return result(s["end"], "open")
