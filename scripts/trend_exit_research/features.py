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
