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
