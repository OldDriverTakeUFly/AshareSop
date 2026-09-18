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
