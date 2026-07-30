"""IC + quintile analysis for 6 intraday features.

Tests whether each feature predicts forward returns (5d/10d/20d).
"""
import os, sys, json, math
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)

import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from stockhot.data_layer.market_db import get_connection, init_db

init_db()

FORWARD_HORIZONS = [1, 5, 10, 20]
FEATURES = ["gap", "amplitude", "close_position", "upper_shadow", "lower_shadow", "body_ratio"]


def load_data():
    print("Loading intraday_feature + daily_price...", flush=True)
    with get_connection() as conn:
        features = pd.read_sql_query(
            "SELECT ts_code, trade_date, gap, amplitude, close_position, "
            "upper_shadow, lower_shadow, body_ratio "
            "FROM intraday_feature ORDER BY ts_code, trade_date",
            conn,
        )
        prices = pd.read_sql_query(
            "SELECT ts_code, trade_date, close FROM daily_price "
            "WHERE close IS NOT NULL AND close > 0 ORDER BY ts_code, trade_date",
            conn,
        )
    return features, prices


def compute_forward_returns(prices: pd.DataFrame) -> pd.DataFrame:
    prices = prices.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    prices = prices.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    for n in FORWARD_HORIZONS:
        prices[f"fwd_ret_{n}"] = (
            prices.groupby("ts_code")["close"].shift(-n) / prices["close"] - 1
        )
    return prices


def compute_ic(features: pd.DataFrame, prices_with_ret: pd.DataFrame) -> dict:
    merged = features.merge(
        prices_with_ret[["ts_code", "trade_date"] + [f"fwd_ret_{n}" for n in FORWARD_HORIZONS]],
        on=["ts_code", "trade_date"], how="inner",
    )
    print(f"  Merged: {len(merged):,} rows", flush=True)

    results = {}
    for feat in FEATURES:
        results[feat] = {}
        for n in FORWARD_HORIZONS:
            ret_col = f"fwd_ret_{n}"
            ic_per_date = []
            for td, group in merged.groupby("trade_date"):
                sub = group[[feat, ret_col]].dropna()
                if len(sub) < 30 or sub[feat].nunique() < 2:
                    continue
                ic, _ = spearmanr(sub[feat], sub[ret_col])
                if not math.isnan(ic):
                    ic_per_date.append(ic)
            if not ic_per_date:
                results[feat][f"fwd_{n}"] = None
                continue
            arr = np.array(ic_per_date)
            results[feat][f"fwd_{n}"] = {
                "n_dates": len(arr),
                "mean_ic": round(float(arr.mean()), 4),
                "std_ic": round(float(arr.std(ddof=1)), 4),
                "icir": round(float(arr.mean() / arr.std(ddof=1)) if arr.std(ddof=1) > 0 else 0, 2),
                "hit_rate": round(float((arr > 0).mean()), 2),
            }
    return results


def quintile_analysis(features: pd.DataFrame, prices_with_ret: pd.DataFrame) -> dict:
    merged = features.merge(
        prices_with_ret[["ts_code", "trade_date"] + [f"fwd_ret_{n}" for n in FORWARD_HORIZONS]],
        on=["ts_code", "trade_date"], how="inner",
    )
    results = {}
    N_Q = 5
    for feat in FEATURES:
        results[feat] = {}
        for n in FORWARD_HORIZONS:
            ret_col = f"fwd_ret_{n}"
            q_rows = []
            for td, group in merged.groupby("trade_date"):
                sub = group[[feat, ret_col]].dropna()
                if len(sub) < 50 or sub[feat].nunique() < N_Q:
                    continue
                try:
                    sub["q"] = pd.qcut(sub[feat], N_Q, labels=False, duplicates="drop")
                except Exception:
                    continue
                if sub["q"].nunique() < N_Q:
                    continue
                for q, qsub in sub.groupby("q"):
                    q_rows.append({"trade_date": td, "q": int(q),
                                   "mean_ret": float(qsub[ret_col].mean())})
            if not q_rows:
                results[feat][f"fwd_{n}"] = None
                continue
            qdf = pd.DataFrame(q_rows)
            summary = {}
            for q in range(N_Q):
                qsub = qdf[qdf["q"] == q]
                if len(qsub) < 5:
                    summary[f"Q{q+1}"] = None
                    continue
                rets = qsub["mean_ret"].values * 100
                summary[f"Q{q+1}"] = round(float(rets.mean()), 3)
            # Q5-Q1 spread
            q5 = qdf[qdf["q"] == N_Q - 1]
            q1 = qdf[qdf["q"] == 0]
            if len(q5) > 0 and len(q1) > 0:
                merged_q = q5.set_index("trade_date")["mean_ret"].rename("q5").to_frame().join(
                    q1.set_index("trade_date")["mean_ret"].rename("q1")).dropna()
                if len(merged_q) > 5:
                    spread = (merged_q["q5"] - merged_q["q1"]) * 100
                    summary["Q5_Q1_spread"] = round(float(spread.mean()), 3)
                    from scipy import stats as scistats
                    t, p = scistats.ttest_1samp(spread, 0)
                    summary["spread_t"] = round(float(t), 1)
                    summary["spread_sig"] = abs(float(t)) >= 2
            results[feat][f"fwd_{n}"] = summary
    return results


def print_ic(results):
    print(f"\n{'='*100}")
    print(f"  INTRADAY FEATURE IC ANALYSIS")
    print(f"{'='*100}")
    print(f"{'Feature':<18} {'Horizon':>8} {'N':>5} {'MeanIC':>8} {'ICIR':>7} {'HitRate':>8} {'Signal':>10}")
    print(f"{'-'*18} {'-'*8} {'-'*5} {'-'*8} {'-'*7} {'-'*8} {'-'*10}")
    for feat, horizons in results.items():
        for h_key, stats in horizons.items():
            if stats is None: continue
            sig = "STRONG" if abs(stats["mean_ic"]) > 0.05 and stats["icir"] > 0.5 else \
                  "GOOD" if abs(stats["mean_ic"]) > 0.03 and stats["icir"] > 0.3 else "WEAK"
            print(f"{feat:<18} {h_key:>8} {stats['n_dates']:>5} {stats['mean_ic']:>+8.4f} "
                  f"{stats['icir']:>+7.2f} {stats['hit_rate']:>8.2f} {sig:>10}")


def print_quintile(results):
    for feat, horizons in results.items():
        for h_key, summary in horizons.items():
            if summary is None: continue
            spread = summary.get("Q5_Q1_spread", 0)
            sig = "***" if summary.get("spread_sig") else ""
            print(f"\n  {feat} {h_key}: Q1={summary.get('Q1','?')} Q3={summary.get('Q3','?')} "
                  f"Q5={summary.get('Q5','?')} spread={spread:+.3f}% {sig}")


def main():
    features, prices = load_data()
    prices_with_ret = compute_forward_returns(prices)

    print("\nComputing IC...", flush=True)
    ic_results = compute_ic(features, prices_with_ret)
    print_ic(ic_results)

    print("\n\nComputing quintile analysis...", flush=True)
    q_results = quintile_analysis(features, prices_with_ret)
    print_quintile(q_results)

    output = {"ic": ic_results, "quintile": q_results}
    with open("logs/intraday_feature_results.json", "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nSaved to logs/intraday_feature_results.json")


if __name__ == "__main__":
    main()
