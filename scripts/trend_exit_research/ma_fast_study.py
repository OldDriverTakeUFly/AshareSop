"""0008 补录:MA5/MA10 在趋势回调判别与退出中的量化作用。

预注册问题(2026-08-28,跑数前定稿):
1. 事前判别:峰值+3日收盘/MA5、/MA10 的四分位判别力 vs MA20(37.9pp);
2. 0 轴阈值:+3日收盘在快线上方 vs 下方(最可执行的二分口径);
3. 收复信号:回调中首个收盘收复 MA5/MA10 当日 → P(continue) 与该日 fwd10/20;
4. 谷底口径:谷底收盘/MA5、/MA10 + 回踩触及 MA10±2% 桶(结构理解,非实时);
5. 退出:ma5/ma5r 补全交换率曲线最快端。
验证:2015-2020 外推 + 三模式重采样(种子 2026,抽样单元=股票,沿 0003 纪律)。
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import OUT_DIR, load_market, log                    # noqa: E402
from exits import ExitRule, run_exit_rule                       # noqa: E402
from pullback import LabelerParams, find_pullbacks              # noqa: E402
from trend_machine import TrendParams, find_episodes, rolling_ma  # noqa: E402
from bootstrap import sample_pool, sample_window               # noqa: E402

MAIN_START = 20210104
EX_FEATURES = ["ex_ma5_3", "ex_ma10_3", "ex_ma20_3"]
TROUGH_FEATURES = ["close_vs_ma5", "close_vs_ma10", "close_vs_ma20"]
SEED = 2026
TRIALS = 16


def _fwd(c: np.ndarray, i: int, horizon: int, end: int) -> float:
    """信号日 i 后 horizon 个交易日的收盘收益(取 ≤i+hz 的最近有限收盘)。"""
    j = min(i + horizon, end)
    seg = c[i + 1: j + 1]
    seg = seg[np.isfinite(seg)]
    if seg.size == 0 or not np.isfinite(c[i]):
        return np.nan
    return float(seg[-1] / c[i] - 1.0)


def scan_fast(md, start: int, end: int) -> pd.DataFrame:
    """全市场重扫(与主 run 同参数保证事件可比),计算 MA 快线特征与收复信号。"""
    tp, lp = TrendParams(), LabelerParams()
    rows: list[dict] = []
    t0 = time.time()
    date_of = {i: int(d) for d, i in md.cal_pos.items()}
    for k, (code, s) in enumerate(md.stocks.items(), 1):
        if k % 500 == 0:
            log(f"  扫描 {k}/{len(md.stocks)} 只, 事件={len(rows)}")
        c = s["c"].astype(np.float64)
        ma5, ma10, ma20 = rolling_ma(c, 5), rolling_ma(c, 10), rolling_ma(c, 20)
        for ep in find_episodes(code, s, tp):
            d0 = date_of[ep.entry_pos]
            if d0 < start or d0 > end:
                continue
            for pb in find_pullbacks(code, s, ep, lp):
                j = pb.peak_pos + 3
                row = {
                    "ts_code": code, "ep_entry_pos": ep.entry_pos,
                    "ep_entry_date": d0, "peak_date": date_of[pb.peak_pos],
                    "outcome": pb.outcome, "peak_pos": pb.peak_pos,
                    "trough_pos": pb.trough_pos, "end_pos": pb.end_pos,
                }
                if j <= s["end"] and np.isfinite(c[j]):
                    row["ex_ma5_3"] = float(c[j] / ma5[j] - 1.0) if np.isfinite(ma5[j]) else np.nan
                    row["ex_ma10_3"] = float(c[j] / ma10[j] - 1.0) if np.isfinite(ma10[j]) else np.nan
                    row["ex_ma20_3"] = float(c[j] / ma20[j] - 1.0) if np.isfinite(ma20[j]) else np.nan
                else:
                    row.update({f: np.nan for f in EX_FEATURES})
                tr = pb.trough_pos
                for name, arr in (("close_vs_ma5", ma5), ("close_vs_ma10", ma10),
                                  ("close_vs_ma20", ma20)):
                    row[name] = (float(c[tr] / arr[tr] - 1.0)
                                 if np.isfinite(arr[tr]) and arr[tr] > 0 else np.nan)
                # 收复信号:峰值后 [peak+1, end_pos] 内首个收盘收复 MA5/MA10 的日子
                for tag, arr in (("5", ma5), ("10", ma10)):
                    rec = -1
                    for i in range(pb.peak_pos + 1, pb.end_pos + 1):
                        if np.isfinite(c[i]) and np.isfinite(arr[i]) and c[i] > arr[i]:
                            rec = i
                            break
                    row[f"reclaim{tag}_pos"] = rec
                    row[f"reclaim{tag}"] = int(rec > 0)
                    if rec > 0:
                        row[f"reclaim{tag}_days"] = rec - pb.peak_pos
                        row[f"reclaim{tag}_fwd10"] = _fwd(c, rec, 10, s["end"])
                        row[f"reclaim{tag}_fwd20"] = _fwd(c, rec, 20, s["end"])
                    else:
                        row[f"reclaim{tag}_days"] = np.nan
                        row[f"reclaim{tag}_fwd10"] = np.nan
                        row[f"reclaim{tag}_fwd20"] = np.nan
                rows.append(row)
    log(f"扫描完成 事件={len(rows)} 耗时 {(time.time() - t0) / 60:.1f} 分钟")
    return pd.DataFrame(rows)


def replay_fast_exits(md, eps_df: pd.DataFrame) -> pd.DataFrame:
    """ma5/ma5r 两条规则回放(episode 集与主 run 一致)。"""
    lp = LabelerParams()
    rules = [ExitRule("ma5", "ma", 5.0), ExitRule("ma5r", "ma", 5.0, True)]
    eps_by_code: dict[str, list] = {}
    for r in eps_df.itertuples():
        eps_by_code.setdefault(r.ts_code, []).append(r)
    from trend_machine import Episode
    rows = []
    t0 = time.time()
    for code, lst in eps_by_code.items():
        s = md.stocks.get(code)
        if s is None:
            continue
        for r in lst:
            ep = Episode(code, int(r.entry_pos), int(r.exit_pos),
                         r.exit_reason, r.peak_close)
            for rule in rules:
                m = run_exit_rule(code, s, ep, rule, lp)
                m.update({"ts_code": code, "ep_entry_pos": int(r.entry_pos),
                          "ep_entry_date": int(r.entry_date)})
                rows.append(m)
    log(f"ma5/ma5r 回放完成 rows={len(rows)} 耗时 {(time.time() - t0) / 60:.1f} 分钟")
    return pd.DataFrame(rows)


# ── 汇总表 ──

def quartile_table(df: pd.DataFrame, f: str) -> pd.DataFrame:
    d = df[df["outcome"].isin(["continue", "terminate"])].dropna(subset=[f]).copy()
    if len(d) < 100:
        return pd.DataFrame()
    try:
        d["q"] = pd.qcut(d[f], 4, labels=["Q1", "Q2", "Q3", "Q4"], duplicates="drop")
    except ValueError:
        return pd.DataFrame()
    return d.groupby("q", observed=True).agg(
        n=("outcome", "size"), p_continue=("outcome", lambda x: (x == "continue").mean()))


def zero_cross_table(df: pd.DataFrame, f: str) -> pd.DataFrame:
    d = df[df["outcome"].isin(["continue", "terminate"])].dropna(subset=[f]).copy()
    d["side"] = np.where(d[f] > 0, "above0", "below0")
    return d.groupby("side").agg(
        n=("outcome", "size"), p_continue=("outcome", lambda x: (x == "continue").mean()))


def reclaim_table(df: pd.DataFrame, tag: str) -> pd.DataFrame:
    d = df[df["outcome"].isin(["continue", "terminate"])].copy()
    g = d.groupby(f"reclaim{tag}").agg(
        n=("outcome", "size"), p_continue=("outcome", lambda x: (x == "continue").mean()))
    rec = d[d[f"reclaim{tag}"] == 1]
    timing = rec.groupby(pd.cut(rec[f"reclaim{tag}_days"], [0, 2, 5, 10, 100],
                                labels=["≤2d", "3-5d", "6-10d", ">10d"]),
                          observed=True).agg(
        n=("outcome", "size"), p_continue=("outcome", lambda x: (x == "continue").mean()),
        fwd20_med=(f"reclaim{tag}_fwd20", "median"),
        fwd10_med=(f"reclaim{tag}_fwd10", "median"))
    return g, timing


def touch_ma10_table(df: pd.DataFrame) -> pd.DataFrame:
    d = df[df["outcome"].isin(["continue", "terminate"])].dropna(subset=["close_vs_ma10"])
    d["bucket"] = pd.cut(d["close_vs_ma10"], [-9, -0.05, -0.02, 0.02, 0.10, 9],
                         labels=["<-5%", "-5~-2%", "±2%(踩线)", "+2~10%", ">10%"])
    return d.groupby("bucket", observed=True).agg(
        n=("outcome", "size"), p_continue=("outcome", lambda x: (x == "continue").mean()))


# ── 重采样验证 ──

def _signs(df_sub: pd.DataFrame) -> dict[str, float]:
    """全量/子样本通用的方向统计:连续特征四分位首尾差 + 0轴差 + 收复率差。"""
    d = df_sub[df_sub["outcome"].isin(["continue", "terminate"])]
    out: dict[str, float] = {}
    for f in EX_FEATURES + TROUGH_FEATURES:
        dd = d.dropna(subset=[f])
        if len(dd) < 60:
            continue
        try:
            q = pd.qcut(dd[f], 4, labels=False, duplicates="drop")
        except ValueError:
            continue
        if pd.Series(q).nunique() < 4:
            continue
        rate = dd.assign(q=q).groupby("q")["outcome"].apply(lambda x: (x == "continue").mean())
        out[f] = float(rate.max() - rate.min())
    for f in EX_FEATURES:
        dd = d.dropna(subset=[f])
        if len(dd) < 60:
            continue
        a = (dd.loc[dd[f] > 0, "outcome"] == "continue").mean()
        b = (dd.loc[dd[f] <= 0, "outcome"] == "continue").mean()
        out[f + "|zero"] = float(a - b)
    for tag in ("5", "10"):
        if len(d) < 60:
            continue
        a = (d.loc[d[f"reclaim{tag}"] == 1, "outcome"] == "continue").mean()
        b = (d.loc[d[f"reclaim{tag}"] == 0, "outcome"] == "continue").mean()
        out[f"reclaim{tag}"] = float(a - b)
    return out


def resample_validation(df: pd.DataFrame, cal: np.ndarray) -> pd.DataFrame:
    universe = set(df["ts_code"].unique())
    full = _signs(df)
    rows = []
    for mode, off in (("window", 0), ("pool", 1), ("both", 2)):
        rng = np.random.default_rng(SEED + off)
        for trial in range(TRIALS):
            window = sample_window(rng, cal) if mode in ("window", "both") else None
            pool = (sample_pool(rng, universe, rng.uniform(0.6, 1.0))
                    if mode in ("pool", "both") else None)
            sub = df
            if window is not None:
                sub = sub[(sub["peak_date"] >= window[0]) & (sub["peak_date"] <= window[1])]
            if pool is not None:
                sub = sub[sub["ts_code"].isin(pool)]
            if len(sub) < 30:
                rows.append({"mode": mode, "trial": trial, "name": "dropped",
                             "diff": np.nan, "sign_match": np.nan})
                continue
            for name, diff in _signs(sub).items():
                f0 = full.get(name, np.nan)
                rows.append({"mode": mode, "trial": trial, "name": name, "diff": diff,
                             "sign_match": int(np.sign(diff) == np.sign(f0))
                             if np.isfinite(diff) and np.isfinite(f0) else np.nan})
    dd = pd.DataFrame(rows)
    return dd[dd["name"] != "dropped"].groupby(["name", "mode"], as_index=False).agg(
        consistency=("sign_match", "mean"), mean_diff=("diff", "mean"),
        n_trials=("sign_match", "count"))


def main() -> None:
    md = load_market()
    df = scan_fast(md, 20150105, 20260826)
    eps = pd.read_csv(f"{OUT_DIR}/episodes.csv")     # 主 run 的 episode 集(保证一致)
    fast = replay_fast_exits(md, eps)
    bench = pd.read_csv(f"{OUT_DIR}/exits.csv",
                        usecols=["ts_code", "ep_entry_pos", "rule", "capture",
                                 "sellfly20", "maxdd", "hold_days"])
    bench = bench[bench["rule"] == "bench_machine"]
    ex_all = pd.concat([bench, fast], ignore_index=True)

    df.to_csv(f"{OUT_DIR}/ma_fast_events.csv", index=False)
    fast.to_csv(f"{OUT_DIR}/ma_fast_exits.csv", index=False)

    df["window"] = np.where(df["ep_entry_date"] >= MAIN_START, "main", "exante")
    L: list[str] = []
    A = L.append
    A("=" * 72)
    A("0008 补录:MA5/MA10 — analysis")
    A(f"事件 n={len(df)}(与主 run 同参数重扫)")

    for w in ("main", "exante"):
        dw = df[df["window"] == w]
        A(f"\n{'=' * 30} 窗口 {w} (n={len(dw)}) {'=' * 30}")
        A("\n## 1. 事前口径四分位(+3日收盘/快线)")
        for f in EX_FEATURES:
            t = quartile_table(dw, f)
            if not t.empty:
                A(f"\n### {f}\n{t.to_string()}")
        A("\n## 2. 0 轴二分(上方 vs 下方)")
        for f in EX_FEATURES:
            A(f"\n### {f}\n{zero_cross_table(dw, f).to_string()}")
        A("\n## 3. 谷底口径四分位")
        for f in TROUGH_FEATURES:
            t = quartile_table(dw, f)
            if not t.empty:
                A(f"\n### {f}\n{t.to_string()}")
        A(f"\n## 4. 回踩触及 MA10 桶\n{touch_ma10_table(dw).to_string()}")
        for tag in ("5", "10"):
            g, timing = reclaim_table(dw, tag)
            A(f"\n## 5. 收复 MA{tag} 信号\n{g.to_string()}")
            A(f"\n收复时点分层:\n{timing.to_string()}")
        A("\n## 6. 退出规则:ma5/ma5r(vs bench_machine)")
        ew = ex_all.copy()
        ew["window"] = np.where(ew["ep_entry_date"] >= MAIN_START, "main", "exante")
        ew = ew[ew["window"] == w]
        lb = ew.groupby("rule").agg(
            n=("capture", "size"), capture_med=("capture", "median"),
            sellfly20=("sellfly20", "mean"), maxdd_med=("maxdd", "median"),
            hold_med=("hold_days", "median"))
        A("\n" + lb.sort_values("capture_med", ascending=False).to_string())

    A("\n## 7. 重采样方向一致率(三模式)")
    A(resample_validation(df, md.cal).to_string(index=False))

    report = "\n".join(L)
    with open(f"{OUT_DIR}/ma_fast_report.txt", "w") as f:
        f.write(report)
    log(f"ma_fast_report.txt 写出({len(L)} 段)")


if __name__ == "__main__":
    main()
