"""事件研究：晋级率矩阵、打板收益分布、特征有效性、环境切片（规格 §8）."""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.limitup import patterns, robustness, sentiment

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


# ── threshold perturbation（形态与 regime 阈值 ±20%，规格 §4 补课）──

# 结论1「突破型晋级率 − 其他晋级率」的扰动组：突破判定双阈值
_PATTERN_PERTURB_KEYS = ("breakout_close", "breakout_box")
# 结论2「高潮档 ret_open_1 均值 − 其他档均值」的扰动组：高潮判定三阈值
_REGIME_PERTURB_KEYS = ("freeze_premium", "hot_count", "hot_boards")

_CONCLUSION_PATTERN = "突破型晋级率 − 其他晋级率"
_CONCLUSION_REGIME = "高潮档 ret_open_1 均值 − 其他档均值"


def _pattern_promo_diffs(
    events: pd.DataFrame, prices: pd.DataFrame, f_lo: float, f_hi: float
) -> dict[str, float]:
    """结论1：扰动突破双阈值 → classify_from_prices 重分类 → 分组晋级率差.

    晋级率 = promoted 均值（ret_open_1.notna() 过滤，跨除权标签已置 NaN）；
    任一组为空 → 差值 NaN（无方向可言，宁缺毋错）。
    """
    base = {k: patterns.PATTERN_THRESHOLDS[k] for k in _PATTERN_PERTURB_KEYS}
    scenarios: dict[str, dict[str, float] | None] = {
        "base": None,
        "0.8x": {k: v * f_lo for k, v in base.items()},
        "1.2x": {k: v * f_hi for k, v in base.items()},
    }
    if events.empty or prices.empty:
        return {k: float("nan") for k in scenarios}
    # 事件表可能已带形态列（attach_pattern_features 产物）：剥离后再分类，
    # 否则 merge 产生 *_x/*_y 后缀列，pattern_label 取不到
    ev = events.drop(columns=patterns.PATTERN_FEATURE_COLS, errors="ignore")
    out: dict[str, float] = {}
    for key, thr in scenarios.items():
        labeled = patterns.classify_from_prices(ev, prices, thresholds=thr)
        valid = labeled[labeled["ret_open_1"].notna()]
        brk = valid.loc[valid["pattern_label"] == "突破型", "promoted"]
        oth = valid.loc[valid["pattern_label"].notna()
                        & (valid["pattern_label"] != "突破型"), "promoted"]
        out[key] = (
            float(brk.mean() - oth.mean()) if len(brk) and len(oth) else float("nan")
        )
    return out


def _regime_return_diffs(
    events: pd.DataFrame, regime: pd.DataFrame, f_lo: float, f_hi: float
) -> dict[str, float]:
    """结论2：扰动高潮三阈值 → classify_regime 逐日重算档位 → 收益均值差."""
    base = {
        "freeze_premium": sentiment.REGIME_FREEZE,
        "hot_count": sentiment.REGIME_HOT_COUNT,
        "hot_boards": sentiment.REGIME_HOT_BOARDS,
    }
    scenarios: dict[str, dict[str, float] | None] = {
        "base": None,
        "0.8x": {k: v * f_lo for k, v in base.items()},
        "1.2x": {k: v * f_hi for k, v in base.items()},
    }
    if events.empty or regime.empty:
        return {k: float("nan") for k in scenarios}
    ev = events.drop(columns=["regime_label"], errors="ignore")
    out: dict[str, float] = {}
    for key, thr in scenarios.items():
        reg = regime.copy()
        reg["regime_label"] = reg.apply(
            lambda r: sentiment.classify_regime(r, thresholds=thr), axis=1
        )
        merged = ev.merge(reg[["trade_date", "regime_label"]],
                          on="trade_date", how="inner")
        valid = merged[merged["ret_open_1"].notna()]
        hot = valid.loc[valid["regime_label"] == "高潮", "ret_open_1"]
        oth = valid.loc[valid["regime_label"] != "高潮", "ret_open_1"]
        out[key] = (
            float(hot.mean() - oth.mean()) if len(hot) and len(oth) else float("nan")
        )
    return out


def threshold_perturbation(
    events: pd.DataFrame, prices: pd.DataFrame, regime: pd.DataFrame
) -> pd.DataFrame:
    """形态与 regime 阈值 ±20% 扰动稳定性（规格 §4 补课，冻结先验不因扰动改变）.

    结论1 扰动 {breakout_close, breakout_box}；结论2 扰动 {freeze_premium,
    hot_count, hot_boards}。差值任一场景为 NaN（空组/样本缺标签）→ 无方向
    → dir_stable=False（宁缺毋错）；方向一致性用 robustness.direction_stable。
    """
    f_lo, f_hi = robustness.perturb_factors()
    diffs = [
        (_CONCLUSION_PATTERN, _pattern_promo_diffs(events, prices, f_lo, f_hi)),
        (_CONCLUSION_REGIME, _regime_return_diffs(events, regime, f_lo, f_hi)),
    ]
    rows = []
    for name, d in diffs:
        b, m8, m12 = d["base"], d["0.8x"], d["1.2x"]
        stable = (
            all(pd.notna(v) for v in (b, m8, m12))
            and robustness.direction_stable(b, m8, m12)
        )
        rows.append({"结论": name, "基准差": b, "扰动0.8x差": m8,
                     "扰动1.2x差": m12, "dir_stable": stable})
    return pd.DataFrame(rows)
