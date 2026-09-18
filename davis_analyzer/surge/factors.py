"""九维指标纯函数计算（DataFrame in/out，不触网不触库）.

口径冻结于 spec §5：位置/均线用后复权价；压力支撑用未复权现价口径
（与 cyq_perf 成本价同口径）；资金流金额单位万元、daily amount 千元。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_MIN_HISTORY = 120
_NAN = float("nan")


# ── 5.2 相对位置（后复权）──

def _adj(px: pd.DataFrame) -> pd.DataFrame:
    out = px.copy()
    for col in ("open", "high", "low", "close"):
        out[col + "_adj"] = out[col] * out["adj_factor"]
    return out


def compute_position(px: pd.DataFrame) -> dict[str, float]:
    a = _adj(px)
    close = float(a["close_adj"].iloc[-1])
    if len(a) < _MIN_HISTORY:
        return {k: _NAN for k in
                ("pos_250d", "dist_ma20", "dist_ma60", "dist_ma120",
                 "dist_ma250", "dd_high_250")}
    h250 = a["high_adj"].tail(250)
    l250 = a["low_adj"].tail(250)
    rng = float(h250.max() - l250.min())
    pos = (close - float(l250.min())) / rng if rng > 0 else _NAN
    dists: dict[str, float] = {}
    for n in (20, 60, 120, 250):
        ma = float(a["close_adj"].tail(n).mean())
        dists[f"dist_ma{n}"] = close / ma - 1 if ma > 0 else _NAN
    return {"pos_250d": pos, **dists,
            "dd_high_250": close / float(h250.max()) - 1}


# ── 5.3 资金流入（moneyflow 万元;daily amount 千元,×10 对齐）──

def compute_moneyflow(mf_hist: pd.DataFrame, amount_today_k: float) -> dict[str, float]:
    nan = {k: _NAN for k in
           ("elg_net_d0", "lg_net_5d", "net_ratio_d0", "consec_net_days")}
    if mf_hist is None or mf_hist.empty:
        return nan
    m = mf_hist.tail(5)
    elg_net = (m["buy_elg_amount"].fillna(0) - m["sell_elg_amount"].fillna(0))
    lg_net = ((m["buy_lg_amount"].fillna(0) + m["buy_elg_amount"].fillna(0))
              - (m["sell_lg_amount"].fillna(0) + m["sell_elg_amount"].fillna(0)))
    last = m.iloc[-1]
    elg_d0 = float(last["buy_elg_amount"] or 0) - float(last["sell_elg_amount"] or 0) \
        if pd.notna(last["buy_elg_amount"]) or pd.notna(last["sell_elg_amount"]) else _NAN
    streak = 0
    for v in elg_net[::-1]:
        if v > 0:
            streak += 1
        else:
            break
    ratio = _NAN
    if amount_today_k and amount_today_k > 0 and pd.notna(last["net_mf_amount"]):
        ratio = float(last["net_mf_amount"]) / (amount_today_k * 10)
    return {"elg_net_d0": elg_d0, "lg_net_5d": float(lg_net.sum()),
            "net_ratio_d0": ratio, "consec_net_days": float(streak)}


# ── 5.6/5.7 压力支撑(未复权现价口径;均线后复权计算后换算对齐) ──

def _ma_adj(a: pd.DataFrame, n: int) -> float:
    tail = a["close_adj"].tail(n)
    return float(tail.mean()) if len(tail) == n else _NAN


def compute_resistance_support(
    px: pd.DataFrame, cyq: pd.Series | None
) -> dict[str, float | str]:
    """候选链: 筹码分位/成本线(失守转压)/均线/缺口/滚动高低/历史高(spec §5.6/5.7)."""
    close = float(px["close"].iloc[-1])
    res: list[tuple[str, float]] = []
    sup: list[tuple[str, float]] = []

    def add(label: str, value: float, *, above: bool) -> None:
        if value != value:  # NaN 跳过
            return
        (res if above else sup).append((label, value))

    if cyq is not None:
        for label in ("cost_85pct", "cost_95pct"):
            if pd.notna(cyq.get(label)):
                add(label, float(cyq[label]), above=True)
        for label in ("cost_15pct", "cost_5pct"):
            if pd.notna(cyq.get(label)):
                add(label, float(cyq[label]), above=False)
        wa = cyq.get("weight_avg")
        if pd.notna(wa):
            add("weight_avg", float(wa), above=close < float(wa))
        if pd.notna(cyq.get("his_high")):
            add("his_high", float(cyq["his_high"]), above=True)

    win120 = px.tail(120)
    add("high_120d", float(win120["high"].max()), above=True)
    add("low_120d", float(win120["low"].min()), above=False)

    # 均线(后复权均值 / 当日adj_factor → 未复权口径)
    a = _adj(px)
    adj_today = float(px["adj_factor"].iloc[-1])
    for n in (60, 120, 250, 20):
        ma_raw = _ma_adj(a, n) / adj_today
        add(f"MA{n}", ma_raw, above=close < ma_raw)

    # 缺口(120日窗口内最近一个;向上缺口=支撑,向下缺口=阻力)
    lo = max(0, len(px) - 120)
    for i in range(len(px) - 1, lo, -1):
        prev_high = float(px["high"].iloc[i - 1])
        prev_low = float(px["low"].iloc[i - 1])
        cur_high = float(px["high"].iloc[i])
        cur_low = float(px["low"].iloc[i])
        if cur_low > prev_high:
            add("gap_up", prev_high, above=False)
            break
        if cur_high < prev_low:
            add("gap_down", prev_low, above=True)
            break

    res_ok = sorted([t for t in res if t[1] > close * 1.005], key=lambda t: t[1])
    sup_ok = sorted([t for t in sup if t[1] < close * 0.995], key=lambda t: -t[1])
    rp = float(res_ok[0][1]) if res_ok else _NAN
    sp = float(sup_ok[0][1]) if sup_ok else _NAN
    ladder = " | ".join(f"{k}@{v:.2f}({v / close - 1:+.1%})" for k, v in res_ok)
    return {"resistance_price": rp,
            "resistance_dist": rp / close - 1 if res_ok else _NAN,
            "support_price": sp,
            "support_dist": sp / close - 1 if sup_ok else _NAN,
            "resistance_ladder": ladder}
