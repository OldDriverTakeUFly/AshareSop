"""IC (Information Coefficient) computation for technical factors.

For each trade_date and each factor, computes:
  - Forward return at horizon N (5, 10, 20 days)
  - Spearman rank correlation between factor value and forward return

IC > 0 means factor predicts positive forward return (bullish signal).
IC < 0 means factor predicts negative forward return (contrarian).

Aggregate stats per factor per horizon:
  - Mean IC (should be > 0.05 to be useful)
  - IC std (volatility of signal)
  - ICIR = Mean / Std (signal-to-noise; >0.5 is good, >1.0 is strong)
  - Hit rate = % of dates with IC > 0 (direction accuracy)

Also computes per-date IC series for stability analysis.
"""
import os, sys, json, math
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)

import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from stockhot.data_layer.market_db import get_connection as get_market_conn, init_db
from stockhot.storage.database import init_database

init_db()
init_database()

FORWARD_HORIZONS = [5, 10, 20]
FACTOR_COLUMNS = ["tech_score", "ma_align_score", "rsi", "macd_hist", "kdj_j", "boll_position"]


def load_factor_and_returns():
    """Load tech_factor + future returns into a single DataFrame."""
    print("Loading tech_factor + daily_price...", flush=True)
    with get_market_conn() as conn:
        # Load factors
        factors = pd.read_sql_query(
            "SELECT ts_code, trade_date, tech_score, ma_align_score, rsi, macd_hist, kdj_j, boll_position "
            "FROM tech_factor ORDER BY ts_code, trade_date",
            conn,
        )
        print(f"  factors: {len(factors):,} rows, {factors['ts_code'].nunique()} stocks", flush=True)

        # Load close prices for forward return computation
        prices = pd.read_sql_query(
            "SELECT ts_code, trade_date, close FROM daily_price "
            "WHERE close IS NOT NULL AND close > 0 ORDER BY ts_code, trade_date",
            conn,
        )
        print(f"  prices: {len(prices):,} rows", flush=True)

    return factors, prices


def compute_forward_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Compute forward N-day returns for each (ts_code, trade_date).

    Forward return[t, N] = close[t+N] / close[t] - 1
    """
    prices = prices.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    prices = prices.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")

    for n in FORWARD_HORIZONS:
        prices[f"fwd_ret_{n}"] = (
            prices.groupby("ts_code")["close"].shift(-n) / prices["close"] - 1
        )
    return prices


def compute_ic_series(factors: pd.DataFrame, prices_with_ret: pd.DataFrame) -> dict:
    """Compute per-date IC series for each (factor, horizon) pair."""
    # Merge factors with forward returns
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
            # Per-date IC
            ic_per_date = []
            for trade_date, group in merged.groupby("trade_date"):
                sub = group[[factor, ret_col]].dropna()
                if len(sub) < 30:  # need at least 30 stocks for meaningful IC
                    continue
                if sub[factor].nunique() < 2:
                    continue
                ic, _ = spearmanr(sub[factor], sub[ret_col])
                if not math.isnan(ic):
                    ic_per_date.append({"trade_date": trade_date, "ic": ic})

            if not ic_per_date:
                results[factor][f"fwd_{n}"] = None
                continue

            ic_arr = np.array([x["ic"] for x in ic_per_date])
            results[factor][f"fwd_{n}"] = {
                "n_dates": len(ic_per_date),
                "mean_ic": float(ic_arr.mean()),
                "median_ic": float(np.median(ic_arr)),
                "std_ic": float(ic_arr.std(ddof=1)),
                "icir": float(ic_arr.mean() / ic_arr.std(ddof=1)) if ic_arr.std(ddof=1) > 0 else 0,
                "hit_rate": float((ic_arr > 0).mean()),
                "ic_series": ic_per_date[:50],  # first 50 dates for stability view
            }

    return results


def print_ic_results(results: dict):
    """Pretty-print IC summary per factor."""
    print(f"\n{'='*100}")
    print(f"  IC ANALYSIS RESULTS — Spearman rank correlation with forward returns")
    print(f"{'='*100}")
    print(f"{'Factor':<18} {'Horizon':>9} {'N':>5} {'MeanIC':>8} {'StdIC':>7} {'ICIR':>7} {'HitRate':>9} {'Signal':>10}")
    print(f"{'-'*18} {'-'*9} {'-'*5} {'-'*8} {'-'*7} {'-'*7} {'-'*9} {'-'*10}")
    for factor, horizons in results.items():
        for h_key, stats in horizons.items():
            if stats is None:
                continue
            signal = (
                "STRONG" if abs(stats["mean_ic"]) > 0.1 and stats["icir"] > 1.0
                else "GOOD" if abs(stats["mean_ic"]) > 0.05 and stats["icir"] > 0.5
                else "WEAK" if abs(stats["mean_ic"]) > 0.03
                else "NOISE"
            )
            print(f"{factor:<18} {h_key:>9} {stats['n_dates']:>5} "
                  f"{stats['mean_ic']:>+8.4f} {stats['std_ic']:>7.4f} "
                  f"{stats['icir']:>+7.2f} {stats['hit_rate']:>9.2f} {signal:>10}")


def main():
    factors, prices = load_factor_and_returns()
    prices_with_ret = compute_forward_returns(prices)
    results = compute_ic_series(factors, prices_with_ret)
    print_ic_results(results)

    # Save JSON
    out_path = "logs/tech_ic_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
