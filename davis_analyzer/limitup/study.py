"""事件研究：晋级率矩阵、打板收益分布、特征有效性、环境切片（规格 §8）."""

from __future__ import annotations

import pandas as pd
from loguru import logger

from davis_analyzer.limitup import robustness


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


def regime_slices(events: pd.DataFrame, regime: pd.DataFrame) -> pd.DataFrame:
    merged = events.merge(regime[["trade_date", "regime_label"]],
                          on="trade_date", how="inner")
    if merged.empty:
        logger.warning("regime_slices: 无可合并事件")
        return pd.DataFrame()
    out = return_distribution(merged, by=["regime_label"])
    order = [x for x in ("冰点", "回暖", "高潮", "退潮") if x in set(out["regime_label"])]
    return out.set_index("regime_label").reindex(order).reset_index()
