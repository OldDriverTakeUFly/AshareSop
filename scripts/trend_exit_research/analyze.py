"""实验0008 汇总报告:结局基率/特征判别表(三口径)/规则排行榜/分层/敏感性/稳健性。"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from common import OUT_DIR, log

MAIN_START = 20210104
COST = 0.0013          # 双边 13bps(计在卖出侧)


def _quartile_table(pb: pd.DataFrame, feature: str) -> pd.DataFrame:
    d = pb[pb["outcome"].isin(["continue", "terminate"])].dropna(subset=[feature]).copy()
    if len(d) < 100:
        return pd.DataFrame()
    try:
        d["q"] = pd.qcut(d[feature], 4, labels=["Q1", "Q2", "Q3", "Q4"], duplicates="drop")
    except ValueError:
        return pd.DataFrame()
    g = d.groupby("q", observed=True).agg(
        n=("outcome", "size"), p_continue=("outcome", lambda x: (x == "continue").mean()))
    return g


def main(out_dir: str = OUT_DIR) -> str:
    eps = pd.read_csv(f"{out_dir}/episodes.csv")
    pb = pd.read_csv(f"{out_dir}/pullbacks.csv")
    ex = pd.read_csv(f"{out_dir}/exits.csv")
    pb["window"] = np.where(pb["ep_entry_date"] >= MAIN_START, "main", "exante")
    ex["window"] = np.where(ex["ep_entry_date"] >= MAIN_START, "main", "exante")

    lines: list[str] = []
    A = lines.append
    A("=" * 72)
    A("实验0008 趋势回调与退出规则 — analysis_report")
    A("=" * 72)
    A(f"\nepisodes={len(eps)}  pullbacks={len(pb)}  exits={len(ex)}")

    A("\n## 1. 回调结局基率(全量/主窗口/外推,分年)")
    for w, d in pb.groupby("window"):
        A(f"\n[{w}] n={len(d)}  " +
          "  ".join(f"{k}={v:.3f}" for k, v in
                    d["outcome"].value_counts(normalize=True).items()))
    pb["year"] = (pb["peak_date"] // 10000).astype(int)
    by_year = pb.groupby(["year", "outcome"]).size().unstack(fill_value=0)
    A("\n" + by_year.to_string())

    A("\n## 2. 判别特征四分位表(continue 率,主窗口;谷底口径+事前口径)")
    pbm = pb[pb["window"] == "main"]
    for f in ("pb_days", "pb_depth", "vol_ratio", "close_vs_ma20", "mf_wash",
              "pos_pct_entry", "ep_gain_at_peak", "ex_dd3", "ex_vol3", "ex_ma20_3"):
        t = _quartile_table(pbm, f)
        if not t.empty:
            A(f"\n### {f}\n{t.to_string()}")

    A("\n## 3. 退出规则排行榜(主窗口,成本 0 与 13bps)")
    exm = ex[ex["window"] == "main"].copy()
    exm["capture_net"] = exm["capture"] * (1 - COST)
    lb = exm.groupby("rule").agg(
        n=("capture", "size"), capture_med=("capture", "median"),
        capture_net_med=("capture_net", "median"),
        sellfly20=("sellfly20", "mean"), gain20_med=("gain20", "median"),
        maxdd_med=("maxdd", "median"), hold_med=("hold_days", "median"))
    A("\n" + lb.sort_values("capture_med", ascending=False).to_string())

    A("\n## 3b. 排行榜(外推窗口 2015-2020)")
    exe = ex[ex["window"] == "exante"]
    if len(exe):
        lbe = exe.groupby("rule").agg(
            n=("capture", "size"), capture_med=("capture", "median"),
            sellfly20=("sellfly20", "mean"), maxdd_med=("maxdd", "median"))
        A("\n" + lbe.sort_values("capture_med", ascending=False).to_string())

    A("\n## 4. 分层切片(主窗口):位置三分位 × 规则 捕获率中位数")
    if "pos_pct_entry" in exm.columns:
        exm2 = exm.dropna(subset=["pos_pct_entry"]).copy()
        exm2["pos_tercile"] = pd.qcut(exm2["pos_pct_entry"], 3, labels=["low", "mid", "high"])
        t = exm2[exm2["rule"].isin(["bench_machine", "trail10", "ma20", "ma60",
                                    "split_ma10_ma20"])].pivot_table(
            index="rule", columns="pos_tercile", values="capture",
            aggfunc="median")
        A("\n" + t.to_string())

    A("\n## 5. 参数敏感性(趋势机/标注器)")
    for name in ("sensitivity.json", "sensitivity_labeler.json"):
        fp = f"{out_dir}/{name}"
        if os.path.exists(fp):
            with open(fp) as f:
                A(f"\n### {name}\n" + pd.DataFrame(json.load(f)).to_string(index=False))

    A("\n## 6. 稳健性(方向一致率)")
    fp = f"{out_dir}/robustness_summary.csv"
    if os.path.exists(fp):
        A("\n" + pd.read_csv(fp).to_string(index=False))
    else:
        A("\n(bootstrap 被跳过)")

    report = "\n".join(lines)
    with open(f"{out_dir}/analysis_report.txt", "w") as f:
        f.write(report)
    log(f"analysis_report.txt 写出({len(lines)} 段)")
    return report


if __name__ == "__main__":
    main()
