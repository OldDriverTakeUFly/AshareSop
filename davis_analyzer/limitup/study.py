"""事件研究：晋级率矩阵、打板收益分布、特征有效性、环境切片（规格 §8）."""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.limitup import robustness

# 封单强度分档标签（与 cli.py 封档有效性小节一致：弱/中/强）
SEAL_BUCKETS = ("弱", "中", "强")


def _dist(g: pd.DataFrame) -> pd.Series:
    r = g["ret_open_1"].dropna()
    pos, neg = r[r > 0], r[r <= 0]
    return pd.Series({
        "mean": r.mean(),
        "median": r.median(),
        "win_rate": (r > 0).mean() if len(r) else float("nan"),
        "payoff": pos.mean() / abs(neg.mean()) if len(pos) and len(neg) else float("nan"),
        "n": len(r),
    })


def return_distribution(
    events: pd.DataFrame, by: list[str] | None = None
) -> pd.DataFrame:
    keys = by or []
    if not keys:
        out = _dist(events).to_frame().T
    else:
        rows = []
        for kvals, g in events.groupby(keys, dropna=False, sort=False):
            if not isinstance(kvals, tuple):
                kvals = (kvals,)
            rows.append({**dict(zip(keys, kvals)), **_dist(g).to_dict()})
        out = pd.DataFrame(rows)
    out["enough_sample"] = robustness.sufficient(out["n"], "return")
    return out


def promotion_matrix(
    events: pd.DataFrame, by: list[str] | None = None
) -> pd.DataFrame:
    keys = ["consecutive_boards", *(by or [])]
    out = (
        events.groupby(keys, dropna=False)
        .agg(promo_rate=("promoted", "mean"), n=("promoted", "size"))
        .reset_index()
        .set_index("consecutive_boards" if not by else keys)
    )
    out["enough_sample"] = robustness.sufficient(out["n"], "promotion")
    return out


def feature_effectiveness(events: pd.DataFrame, feature: str) -> pd.DataFrame:
    return return_distribution(events, by=[feature])


def seal_bucket_perturbation(
    events: pd.DataFrame, base: tuple[float, float] = (0.02, 0.05)
) -> pd.DataFrame:
    """封单分档 ±20% 阈值扰动稳定性（规格 §7.4 核心阈值扰动重跑）.

    阈值 (lo, hi) 各乘 0.8/1.2 重算弱/中/强三档 ret_open_1 均值，与基准
    并列成表；dir_stable 用 robustness.direction_stable 标注三场景方向
    是否一致——空档均值 NaN 视为无方向，判不稳定（宁缺毋错）。
    """
    f_lo, f_hi = robustness.perturb_factors()
    scenarios: dict[str, tuple[float, float]] = {
        "base": (base[0], base[1]),
        "0.8x": (base[0] * f_lo, base[1] * f_lo),
        "1.2x": (base[0] * f_hi, base[1] * f_hi),
    }
    means: dict[str, dict[str, float]] = {}
    for key, (lo, hi) in scenarios.items():
        bucket = pd.cut(events["seal_ratio"], [-np.inf, lo, hi, np.inf],
                        labels=list(SEAL_BUCKETS))
        m = events.assign(_b=bucket).groupby("_b", observed=False)[
            "ret_open_1"].mean()
        means[key] = {k: float(m.get(k, np.nan)) for k in SEAL_BUCKETS}
    rows = []
    for k in SEAL_BUCKETS:
        b, m8, m12 = means["base"][k], means["0.8x"][k], means["1.2x"][k]
        stable = (
            all(pd.notna(v) for v in (b, m8, m12))
            and robustness.direction_stable(b, m8, m12)
        )
        rows.append({"封档": k, "mean_base": b, "mean_0.8x": m8,
                     "mean_1.2x": m12, "dir_stable": stable})
    return pd.DataFrame(rows)


def regime_slices(events: pd.DataFrame, regime: pd.DataFrame) -> pd.DataFrame:
    merged = events.merge(regime[["trade_date", "regime_label"]],
                          on="trade_date", how="inner")
    if merged.empty:
        logger.warning("regime_slices: 无可合并事件")
        return pd.DataFrame()
    out = return_distribution(merged, by=["regime_label"])
    order = [x for x in ("冰点", "回暖", "高潮", "退潮") if x in set(out["regime_label"])]
    return out.set_index("regime_label").reindex(order).reset_index()
