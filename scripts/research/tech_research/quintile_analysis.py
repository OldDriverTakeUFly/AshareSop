"""Quintile (5-group) backtest for technical factors.

For each trade_date and each factor:
  1. Rank all stocks by factor value, split into 5 equal groups (Q1=lowest, Q5=highest)
  2. Compute mean forward return per group
  3. Compute Q5-Q1 spread (long-short return)

Aggregates across dates:
  - Mean return per quintile (Q1 → Q5)
  - Monotonicity check (is Q5 > Q4 > Q3 > Q2 > Q1?)
  - Q5-Q1 spread mean + t-stat
"""
import os, sys, json, math
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)

import pandas as pd
import numpy as np
from scipy import stats
from stockhot.data_layer.market_db import get_connection as get_market_conn, init_db
from stockhot.storage.database import init_database

init_db()
init_database()

FORWARD_HORIZONS = [5, 10, 20]
FACTOR_COLUMNS = ["tech_score", "ma_align_score", "rsi", "macd_hist", "kdj_j", "boll_position"]
N_QUANTILES = 5


def load_factor_and_returns():
    with get_market_conn() as conn:
        factors = pd.read_sql_query(
            "SELECT ts_code, trade_date, tech_score, ma_align_score, rsi, macd_hist, kdj_j, boll_position "
            "FROM tech_factor ORDER BY ts_code, trade_date",
            conn,
        )
        prices = pd.read_sql_query(
            "SELECT ts_code, trade_date, close FROM daily_price "
            "WHERE close IS NOT NULL AND close > 0 ORDER BY ts_code, trade_date",
            conn,
        )
    return factors, prices


def compute_forward_returns(prices: pd.DataFrame) -> pd.DataFrame:
    prices = prices.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    prices = prices.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    for n in FORWARD_HORIZONS:
        prices[f"fwd_ret_{n}"] = (
            prices.groupby("ts_code")["close"].shift(-n) / prices["close"] - 1
        )
    return prices


def quintile_analysis(factors: pd.DataFrame, prices_with_ret: pd.DataFrame) -> dict:
    """Per-date quintile backtest per factor per horizon."""
    merged = factors.merge(
        prices_with_ret[["ts_code", "trade_date"] + [f"fwd_ret_{n}" for n in FORWARD_HORIZONS]],
        on=["ts_code", "trade_date"], how="inner",
    )
    print(f"  Merged: {len(merged):,} rows", flush=True)

    results = {}
    for factor in FACTOR_COLUMNS:
        results[factor] = {}
        for n in FORWARD_HORIZONS:
            ret_col = f"fwd_ret_{n}"
            # Per-date, assign quantile membership
            quantile_rows = []
            for trade_date, group in merged.groupby("trade_date"):
                sub = group[[factor, ret_col]].dropna()
                if len(sub) < 50:
                    continue
                if sub[factor].nunique() < N_QUANTILES:
                    continue
                try:
                    sub["q"] = pd.qcut(sub[factor], N_QUANTILES, labels=False, duplicates="drop")
                except Exception:
                    continue
                if sub["q"].nunique() < N_QUANTILES:
                    continue
                # Mean forward return per quantile
                for q, qsub in sub.groupby("q"):
                    quantile_rows.append({
                        "trade_date": trade_date,
                        "q": int(q),
                        "mean_ret": float(qsub[ret_col].mean()),
                    })

            if not quantile_rows:
                results[factor][f"fwd_{n}"] = None
                continue

            qdf = pd.DataFrame(quantile_rows)
            # Aggregate across dates
            summary = {}
            for q in range(N_QUANTILES):
                qsub = qdf[qdf["q"] == q]
                if len(qsub) < 5:
                    summary[f"Q{q+1}"] = None
                    continue
                # Convert returns to % and t-test against 0
                rets = qsub["mean_ret"].values * 100  # in %
                t_stat, p_val = stats.ttest_1samp(rets, 0)
                summary[f"Q{q+1}"] = {
                    "n_dates": len(qsub),
                    "mean_ret_pct": float(rets.mean()),
                    "median_ret_pct": float(np.median(rets)),
                    "std_pct": float(rets.std(ddof=1)),
                    "t_stat": float(t_stat),
                    "p_value": float(p_val),
                    "significant": abs(float(t_stat)) >= 2,
                }

            # Q5-Q1 spread
            q5 = qdf[qdf["q"] == N_QUANTILES - 1]
            q1 = qdf[qdf["q"] == 0]
            if len(q5) > 0 and len(q1) > 0:
                # Match on trade_date
                merged_q = q5.set_index("trade_date")["mean_ret"].rename("q5").to_frame().join(
                    q1.set_index("trade_date")["mean_ret"].rename("q1")
                ).dropna()
                if len(merged_q) > 5:
                    spread = (merged_q["q5"] - merged_q["q1"]) * 100  # in %
                    spread_t, spread_p = stats.ttest_1samp(spread, 0)
                    summary["Q5_Q1_spread"] = {
                        "n_dates": len(merged_q),
                        "mean_spread_pct": float(spread.mean()),
                        "std_pct": float(spread.std(ddof=1)),
                        "t_stat": float(spread_t),
                        "p_value": float(spread_p),
                        "significant": abs(float(spread_t)) >= 2,
                    }
                    # Monotonicity check: is Q5 > Q4 > Q3 > Q2 > Q1?
                    q_means = [
                        (qdf[qdf["q"] == q]["mean_ret"].mean() if len(qdf[qdf["q"] == q]) > 0 else None)
                        for q in range(N_QUANTILES)
                    ]
                    summary["monotonic"] = all(
                        q_means[i] is not None and q_means[i+1] is not None and q_means[i+1] > q_means[i]
                        for i in range(len(q_means) - 1)
                    )

            results[factor][f"fwd_{n}"] = summary

    return results


def print_quintile_results(results: dict):
    print(f"\n{'='*100}")
    print(f"  QUINTILE BACKTEST — mean forward return % by quantile (Q1=lowest factor, Q5=highest)")
    print(f"{'='*100}")
    for factor, horizons in results.items():
        for h_key, summary in horizons.items():
            if summary is None:
                continue
            print(f"\n  {factor}  {h_key}:")
            for q in [f"Q{i+1}" for i in range(N_QUANTILES)]:
                s = summary.get(q)
                if s is None:
                    continue
                sig = "***" if s["significant"] else ""
                print(f"    {q}: {s['mean_ret_pct']:>+7.3f}%  t={s['t_stat']:>+5.1f}  {sig}")

            spread = summary.get("Q5_Q1_spread")
            if spread:
                sig = "***" if spread["significant"] else ""
                mono = "MONOTONIC ✓" if summary.get("monotonic") else "non-monotonic"
                print(f"    Q5-Q1 spread: {spread['mean_spread_pct']:>+7.3f}%  t={spread['t_stat']:>+5.1f}  {sig}  ({mono})")


def main():
    factors, prices = load_factor_and_returns()
    prices_with_ret = compute_forward_returns(prices)
    results = quintile_analysis(factors, prices_with_ret)
    print_quintile_results(results)

    out_path = "logs/tech_quintile_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
