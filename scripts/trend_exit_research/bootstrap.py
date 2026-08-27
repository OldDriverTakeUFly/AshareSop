"""稳健性测试台:随机窗口×随机票池重采样(实验0008,spec §4.6,0003 纪律事件研究适配)。

重聚合而非重跑:基于 episodes/pullbacks/exits 明细重新汇总;抽样单元=股票;
配对设计:规则与基准在同子样本内差值;方向一致率 = 子样本差值符号与全量一致。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import OUT_DIR, log

SEED = 2026
TRIALS = 16
WINDOW_RANGE = (60, 181)      # uniform[60,180] 交易日
POOL_FRAC_RANGE = (0.60, 1.00)
MIN_EVENTS = 30
FEATURES = ["pb_depth", "pb_days", "vol_ratio", "ex_dd3", "ex_vol3"]


def sample_window(rng: np.random.Generator, cal: np.ndarray) -> tuple[int, int]:
    length = int(rng.integers(WINDOW_RANGE[0], WINDOW_RANGE[1]))
    i0 = int(rng.integers(0, max(1, len(cal) - length)))
    return int(cal[i0]), int(cal[i0 + length - 1])


def sample_pool(rng: np.random.Generator, universe: set[str], frac: float) -> set[str]:
    size = max(1, int(round(len(universe) * frac)))
    return set(rng.choice(sorted(universe), size=size, replace=False).tolist())


def filter_events(df: pd.DataFrame, window: tuple[int, int] | None,
                  pool: set[str] | None, date_col: str) -> pd.DataFrame:
    out = df
    if window is not None:
        out = out[(out[date_col] >= window[0]) & (out[date_col] <= window[1])]
    if pool is not None:
        out = out[out["ts_code"].isin(pool)]
    return out


def _rule_diffs(ex_sub: pd.DataFrame) -> dict[tuple[str, str], float]:
    """各规则对 bench_machine 的同子样本配对差值(中位数口径)。"""
    base = ex_sub[ex_sub["rule"] == "bench_machine"]
    if base.empty:
        return {}
    med_cap = base["capture"].median()
    med_sf = base["sellfly20"].mean()
    out: dict[tuple[str, str], float] = {}
    for rule in ex_sub["rule"].unique():
        if rule == "bench_machine":
            continue
        sub = ex_sub[ex_sub["rule"] == rule]
        out[(rule, "capture")] = float(sub["capture"].median() - med_cap)
        out[(rule, "sellfly20")] = float(sub["sellfly20"].mean() - med_sf)
    return out


def _feature_signs(pb_sub: pd.DataFrame) -> dict[str, float]:
    """判别特征四分位首尾差(continue 比例 Q_max - Q_min,二分样本)。"""
    df = pb_sub[pb_sub["outcome"].isin(["continue", "terminate"])].dropna(
        subset=["pb_depth"])
    out: dict[str, float] = {}
    for f in FEATURES:
        d = df.dropna(subset=[f])
        if len(d) < MIN_EVENTS:
            continue
        try:
            q = pd.qcut(d[f], 4, labels=False, duplicates="drop")
        except ValueError:
            continue
        if pd.Series(q).nunique() < 4:
            continue
        rate = d.assign(q=q).groupby("q")["outcome"].apply(
            lambda x: (x == "continue").mean())
        out[f] = float(rate.max() - rate.min())
    return out


def run_bootstrap(pullbacks_df: pd.DataFrame, exits_df: pd.DataFrame, cal: np.ndarray,
                  trials: int = TRIALS, seed: int = SEED,
                  min_events: int = MIN_EVENTS) -> pd.DataFrame:
    universe = set(pullbacks_df["ts_code"].unique())
    # 全量口径(方向一致率的参照)
    full_rules = _rule_diffs(exits_df)
    full_feats = _feature_signs(pullbacks_df)

    rows: list[dict] = []
    mode_offset = {"window": 0, "pool": 1, "both": 2}   # 确定性(勿用 hash,跨进程不稳定)
    for mode in ("window", "pool", "both"):
        rng = np.random.default_rng(seed + mode_offset[mode])
        for trial in range(trials):
            window = sample_window(rng, cal) if mode in ("window", "both") else None
            pool = (sample_pool(rng, universe, rng.uniform(*POOL_FRAC_RANGE))
                    if mode in ("pool", "both") else None)
            pb_sub = filter_events(pullbacks_df, window, pool, "peak_date")
            n_ev = len(pb_sub)
            if n_ev < min_events:
                rows.append({"mode": mode, "trial": trial, "kind": "dropped",
                             "name": "", "metric": "", "diff": np.nan,
                             "n": n_ev, "n_dropped": 1, "sign_match": np.nan})
                continue
            ex_sub = filter_events(exits_df, window, pool, "ep_entry_date")
            for (name, metric), diff in _rule_diffs(ex_sub).items():
                full = full_rules.get((name, metric), np.nan)
                rows.append({"mode": mode, "trial": trial, "kind": "rule",
                             "name": name, "metric": metric, "diff": diff,
                             "n": n_ev, "n_dropped": 0,
                             "sign_match": int(np.sign(diff) == np.sign(full))
                             if np.isfinite(diff) and np.isfinite(full) else np.nan})
            for name, diff in _feature_signs(pb_sub).items():
                full = full_feats.get(name, np.nan)
                rows.append({"mode": mode, "trial": trial, "kind": "feature",
                             "name": name, "metric": "q4_q1_continue", "diff": diff,
                             "n": n_ev, "n_dropped": 0,
                             "sign_match": int(np.sign(diff) == np.sign(full))
                             if np.isfinite(diff) and np.isfinite(full) else np.nan})
    df = pd.DataFrame(rows)
    summary = (df[df["kind"] != "dropped"]
               .groupby(["kind", "name", "metric", "mode"], as_index=False)
               .agg(consistency=("sign_match", "mean"),
                    mean_diff=("diff", "mean"), n_trials=("sign_match", "count")))
    df.attrs["summary"] = summary
    return df


def main(out_dir: str = OUT_DIR) -> pd.DataFrame:
    import os
    pb = pd.read_csv(f"{out_dir}/pullbacks.csv")
    ex = pd.read_csv(f"{out_dir}/exits.csv")
    cal = np.loadtxt(f"{out_dir}/calendar.txt", dtype=int)
    df = run_bootstrap(pb, ex, cal)
    df.to_csv(f"{out_dir}/robustness.csv", index=False)
    df.attrs["summary"].to_csv(f"{out_dir}/robustness_summary.csv", index=False)
    log(f"bootstrap 完成:{len(df)} 行 → robustness.csv / robustness_summary.csv")
    return df


if __name__ == "__main__":
    main()
