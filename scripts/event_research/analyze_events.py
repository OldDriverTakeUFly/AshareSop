"""Statistical analysis of event CAR results.

For each event type, computes:
1. Overall CAR distribution (mean, median, std, t-stat)
2. CAR by magnitude bucket (does scale amplify effect?)
3. CAR by direction (positive vs negative events)
4. Significance: t-test against 0

Outputs both console tables and JSON for the report generator.
"""
from __future__ import annotations

import os, sys, json, math
from dataclasses import dataclass, asdict
from typing import Iterable

PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ.setdefault("PROJECT_ROOT", PROJECT_ROOT)

import pandas as pd
from scipy import stats

from compute_car import (
    EventCAR, compute_all_cars, save_cars_to_db, load_events,
)


@dataclass
class CarStat:
    """Statistical summary of a CAR distribution."""
    n: int
    mean: float
    median: float
    std: float
    p25: float
    p75: float
    t_stat: float
    p_value: float
    significant: bool   # |t_stat| > 2 (5% level approx)


def describe_cars(car_values: list[float]) -> CarStat | None:
    """Compute statistics on a list of CAR values."""
    car_values = [v for v in car_values if v is not None and not math.isnan(v)]
    if len(car_values) < 5:
        return None
    s = pd.Series(car_values)
    mean = float(s.mean())
    std = float(s.std(ddof=1))
    n = len(s)
    if std == 0:
        return None
    t_stat = mean / (std / math.sqrt(n))
    p_value = 2 * (1 - stats.t.cdf(abs(t_stat), df=n - 1))
    return CarStat(
        n=n, mean=round(mean, 2), median=round(float(s.median()), 2),
        std=round(std, 2),
        p25=round(float(s.quantile(0.25)), 2),
        p75=round(float(s.quantile(0.75)), 2),
        t_stat=round(t_stat, 2),
        p_value=round(p_value, 4),
        significant=abs(t_stat) >= 2,
    )


def analyze_event_type(
    event_type: str,
    cars: list[EventCAR],
    magnitude_buckets: list[tuple[float, float, str]] | None = None,
) -> dict:
    """Analyze CAR distribution for one event type.

    magnitude_buckets: list of (lo, hi, label). If None, uses defaults per type.
    """
    if not cars:
        return {"event_type": event_type, "error": "no events"}

    # Deduplicate: same (ts_code, ann_date) with multiple shareholders counts once
    seen = set()
    dedup: list[EventCAR] = []
    for c in cars:
        key = (c.ts_code, c.ann_date)
        if key in seen:
            # Keep the one with larger magnitude
            existing_idx = next((i for i, e in enumerate(dedup)
                                if (e.ts_code, e.ann_date) == key), None)
            if existing_idx is not None:
                if (c.magnitude or 0) > (dedup[existing_idx].magnitude or 0):
                    dedup[existing_idx] = c
            continue
        seen.add(key)
        dedup.append(c)
    print(f"  [dedup] {event_type}: {len(cars):,} → {len(dedup):,} unique events")

    # Default buckets per event type
    if magnitude_buckets is None:
        if event_type == "share_float":
            # magnitude = 解禁比例 (占总股本%)
            magnitude_buckets = [
                (0, 1, "<1%"), (1, 5, "1-5%"),
                (5, 10, "5-10%"), (10, 20, "10-20%"), (20, 1e9, ">20%"),
            ]
        elif event_type == "holder_trade":
            # magnitude = change_ratio (%)
            magnitude_buckets = [
                (0, 0.5, "<0.5%"), (0.5, 1, "0.5-1%"),
                (1, 3, "1-3%"), (3, 1e9, ">3%"),
            ]
        elif event_type == "repurchase":
            # magnitude = amount in 元
            magnitude_buckets = [
                (0, 1e8, "<1亿"), (1e8, 5e8, "1-5亿"),
                (5e8, 1e9, "5-10亿"), (1e9, 1e14, ">10亿"),
            ]
        elif event_type == "pledge":
            # magnitude = pledge_ratio %
            magnitude_buckets = [
                (0, 10, "<10%"), (10, 30, "10-30%"),
                (30, 50, "30-50%"), (50, 1e9, ">50%"),
            ]
        else:
            magnitude_buckets = []

    result = {
        "event_type": event_type,
        "total_events": len(dedup),
        "overall": {},
        "by_direction": {},
        "by_magnitude": [],
    }

    # Overall stats
    overall_neg30_0 = describe_cars([c.car_neg30_0 for c in dedup])
    overall_0_pos30 = describe_cars([c.car_0_pos30 for c in dedup])
    overall_0_pos60 = describe_cars([c.car_0_pos60 for c in dedup])
    overall_neg5_pos5 = describe_cars([c.car_neg5_pos5 for c in dedup])
    result["overall"] = {
        "car_neg20_0": asdict(overall_neg30_0) if overall_neg30_0 else None,
        "car_0_pos30": asdict(overall_0_pos30) if overall_0_pos30 else None,
        "car_0_pos60": asdict(overall_0_pos60) if overall_0_pos60 else None,
        "car_neg5_pos5": asdict(overall_neg5_pos5) if overall_neg5_pos5 else None,
    }

    # By direction (only meaningful for holder_trade which has positive/negative)
    by_dir: dict[str, list[EventCAR]] = {}
    for c in dedup:
        d = c.direction or "neutral"
        by_dir.setdefault(d, []).append(c)
    for d, group in by_dir.items():
        s_neg = describe_cars([c.car_neg30_0 for c in group])
        s_pos = describe_cars([c.car_0_pos30 for c in group])
        result["by_direction"][d] = {
            "n": len(group),
            "car_neg20_0": asdict(s_neg) if s_neg else None,
            "car_0_pos30": asdict(s_pos) if s_pos else None,
        }

    # By magnitude bucket
    for lo, hi, label in magnitude_buckets:
        bucket_cars = [c for c in dedup
                       if c.magnitude is not None and lo <= c.magnitude < hi]
        if len(bucket_cars) < 5:
            result["by_magnitude"].append({
                "bucket": label, "n": len(bucket_cars),
                "note": "insufficient samples (<5)",
            })
            continue
        s_neg = describe_cars([c.car_neg30_0 for c in bucket_cars])
        s_pos30 = describe_cars([c.car_0_pos30 for c in bucket_cars])
        s_pos60 = describe_cars([c.car_0_pos60 for c in bucket_cars])
        result["by_magnitude"].append({
            "bucket": label,
            "n": len(bucket_cars),
            "car_neg20_0": asdict(s_neg) if s_neg else None,
            "car_0_pos30": asdict(s_pos30) if s_pos30 else None,
            "car_0_pos60": asdict(s_pos60) if s_pos60 else None,
        })

    return result


def print_analysis(result: dict) -> None:
    """Pretty-print one event type's analysis."""
    et = result["event_type"]
    print(f"\n{'='*78}")
    print(f"  {et}  (total events: {result.get('total_events', 0):,})")
    print(f"{'='*78}")

    if "error" in result:
        print(f"  ERROR: {result['error']}")
        return

    # Overall
    print(f"\n  Overall CAR distribution:")
    print(f"    {'Window':<18} {'N':>6} {'Mean%':>8} {'Median%':>9} {'Std%':>7} {'t-stat':>8} {'Sig':>5}")
    print(f"    {'-'*18} {'-'*6} {'-'*8} {'-'*9} {'-'*7} {'-'*8} {'-'*5}")
    for label, key in [("[-20, 0]", "car_neg20_0"),
                       ("[0, +30]", "car_0_pos30"),
                       ("[0, +60]", "car_0_pos60"),
                       ("[-5, +5]", "car_neg5_pos5")]:
        s = result["overall"].get(key)
        if s:
            sig = "***" if s["significant"] else ""
            print(f"    {label:<18} {s['n']:>6} {s['mean']:>+8.2f} {s['median']:>+9.2f} "
                  f"{s['std']:>7.2f} {s['t_stat']:>+8.2f} {sig:>5}")

    # By direction
    if result.get("by_direction") and len(result["by_direction"]) > 1:
        print(f"\n  By direction:")
        print(f"    {'Direction':<12} {'N':>6} {'CAR[-20,0]%':>14} {'CAR[0,+30]%':>14}")
        print(f"    {'-'*12} {'-'*6} {'-'*14} {'-'*14}")
        for d, dr in result["by_direction"].items():
            neg = dr.get("car_neg20_0") or {}
            pos = dr.get("car_0_pos30") or {}
            print(f"    {d:<12} {dr['n']:>6} "
                  f"{neg.get('mean', 0):>+14.2f} {pos.get('mean', 0):>+14.2f}")

    # By magnitude
    if result.get("by_magnitude"):
        print(f"\n  By magnitude bucket:")
        print(f"    {'Bucket':<10} {'N':>6} {'CAR[-20,0]%':>14} {'t':>6} {'CAR[0,+30]%':>14} {'t':>6} {'CAR[0,+60]%':>14} {'t':>6}")
        print(f"    {'-'*10} {'-'*6} {'-'*14} {'-'*6} {'-'*14} {'-'*6} {'-'*14} {'-'*6}")
        for b in result["by_magnitude"]:
            if "note" in b:
                print(f"    {b['bucket']:<10} {b['n']:>6} {b['note']}")
                continue
            n0 = b.get("car_neg20_0") or {}
            p30 = b.get("car_0_pos30") or {}
            p60 = b.get("car_0_pos60") or {}
            print(f"    {b['bucket']:<10} {b['n']:>6} "
                  f"{n0.get('mean', 0):>+14.2f} {n0.get('t_stat', 0):>+6.1f} "
                  f"{p30.get('mean', 0):>+14.2f} {p30.get('t_stat', 0):>+6.1f} "
                  f"{p60.get('mean', 0):>+14.2f} {p60.get('t_stat', 0):>+6.1f}")


def analyze_all(event_types: list[str] | None = None) -> dict:
    """Compute and analyze all event types. Returns dict keyed by event_type."""
    # Default min_magnitude per event type to skip noise (<1% small non-tradable unlocks)
    min_mag_defaults = {
        "share_float": 5.0,    # only consider ≥5% unlocks (大非)
        "holder_trade": None,  # all trades matter, even small
        "repurchase": None,
        "pledge": None,        # pledge uses different scale (already filtered at fetch)
    }

    if event_types is None:
        event_types = ["share_float", "holder_trade", "repurchase", "pledge"]

    all_results = {}
    for et in event_types:
        min_mag = min_mag_defaults.get(et)
        print(f"\n{'#' * 78}")
        print(f"# Computing CARs for {et}" + (f" (min_magnitude={min_mag})" if min_mag else ""))
        print(f"{'#' * 78}")

        # Use date range matching our daily_price coverage (2025-01 onwards)
        cars = compute_all_cars(
            et, min_magnitude=min_mag,
            start_date="20250101", end_date="20260701",
        )
        save_cars_to_db(cars, table_name=f"car_{et}")

        result = analyze_event_type(et, cars)
        print_analysis(result)
        all_results[et] = result

    # Save JSON for report generator
    out_path = os.path.join(PROJECT_ROOT, "logs/event_analysis.json")
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str, ensure_ascii=False)
    print(f"\n\nAnalysis saved to {out_path}")

    return all_results


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Analyze specific event types passed as CLI args
        analyze_all(sys.argv[1:])
    else:
        analyze_all()
