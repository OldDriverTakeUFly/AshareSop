"""统计稳健性规范（规格 §7）：样本门槛、IS/OOS 切分、参数扰动、方向稳定性."""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_SAMPLE_RETURN = 30
MIN_SAMPLE_PROMOTION = 50


def split_is_oos(
    events: pd.DataFrame, oos_start: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    is_ev = events[events["trade_date"] < oos_start]
    oos_ev = events[events["trade_date"] >= oos_start]
    return is_ev.copy(), oos_ev.copy()


def perturb_factors() -> tuple[float, float]:
    return (0.8, 1.2)


def sufficient(counts: pd.Series, kind: str) -> pd.Series:
    floor = MIN_SAMPLE_RETURN if kind == "return" else MIN_SAMPLE_PROMOTION
    return counts.fillna(0) >= floor


def direction_stable(baseline: float, *perturbed: float) -> bool:
    signs = {np.sign(v) for v in (baseline, *perturbed)}
    return len(signs) == 1 and 0 not in signs
