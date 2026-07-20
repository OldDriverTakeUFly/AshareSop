"""CAR (Cumulative Abnormal Return) computation engine for event studies.

For each event (ts_code, ann_date), computes abnormal returns over
[T-pre_window, T+post_window] using a baseline mean-daily-return from
[T-baseline_start, T-pre_window].

The baseline uses the stock's own mean daily return over a 120-day window
ending 30 days before the event (market-model-free, simpler than beta-adjusted
and works for our small-cap universe where index beta may be unstable).

Usage:
    from compute_car import compute_event_car, compute_all_cars
    # Single event
    car = compute_event_car('300750.SZ', '20260321')
    # All events of a type
    results = compute_all_cars('share_float', min_magnitude=5.0)
"""
from __future__ import annotations

import os, sys, json, time
from dataclasses import dataclass, asdict
from typing import Iterable

PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ.setdefault("PROJECT_ROOT", PROJECT_ROOT)

import pandas as pd
from stockhot.data_layer.market_db import get_connection as get_market_conn


# ── Window parameters ──────────────────────────────────────────────────
# Adjusted for our data: daily_price only has 2025-01-02 onwards for most stocks.
# Using shorter windows (60-day baseline, 20-day pre, 30-day post) to maximize
# sample size while still capturing the event effect.
BASELINE_DAYS = 60        # baseline mean return computed over 60 trading days
PRE_GAP_DAYS = 10         # gap between baseline end and event day
PRE_WINDOW = 20           # CAR window before event: [-20, 0]
POST_WINDOW = 30          # CAR window after event: [0, +30]
MIN_PRICES_FOR_CAR = 60   # need ≥60 daily prices total


@dataclass
class EventCAR:
    """CAR result for a single event."""
    ts_code: str
    ann_date: str
    event_type: str
    direction: str
    magnitude: float | None

    # Core CAR metrics (in %)
    car_neg30_0: float | None     # [-30, 0] cumulative abnormal return (pre-event drift)
    car_0_pos30: float | None     # [0, +30] immediate reaction
    car_0_pos60: float | None     # [0, +60] sustained impact
    car_neg5_pos5: float | None   # [-5, +5] short-term shock

    # Diagnostic
    baseline_mean_daily_ret: float | None  # baseline avg daily return (bp)
    n_prices: int                  # how many prices were used
    has_full_window: bool          # did we have prices covering full [T-30, T+60]?


def _load_price_series(ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Load daily prices for ts_code between dates. Returns DataFrame indexed by trade_date."""
    with get_market_conn() as conn:
        rows = conn.execute(
            "SELECT trade_date, close FROM daily_price "
            "WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
            "AND close IS NOT NULL AND close > 0 ORDER BY trade_date",
            (ts_code, start_date, end_date),
        ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["trade_date", "close"])
    df["close"] = df["close"].astype(float)
    return df


def compute_event_car(
    ts_code: str,
    ann_date: str,
    event_type: str = "",
    direction: str = "",
    magnitude: float | None = None,
) -> EventCAR | None:
    """Compute CAR for one event.

    Returns None if insufficient price data.
    """
    # We need prices from ann_date - (BASELINE_DAYS + PRE_GAP_DAYS + PRE_WINDOW + buffer) trading days
    # to ann_date + POST_WINDOW + buffer trading days. Approximate trading-day offset
    # with calendar days (1.4x to be safe but not waste query bandwidth).
    pre_cal_days = int((BASELINE_DAYS + PRE_GAP_DAYS + PRE_WINDOW + 20) * 1.4)
    post_cal_days = int((POST_WINDOW + 20) * 1.4)
    start_cal = pd.Timestamp(ann_date) - pd.Timedelta(days=pre_cal_days)
    end_cal = pd.Timestamp(ann_date) + pd.Timedelta(days=post_cal_days)
    start_str = start_cal.strftime("%Y%m%d")
    end_str = end_cal.strftime("%Y%m%d")

    df = _load_price_series(ts_code, start_str, end_str)
    if len(df) < MIN_PRICES_FOR_CAR:
        return None

    # Find the index of the event date (or nearest trading day on/before it)
    df["ts"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df = df.set_index("ts").sort_index()
    event_ts = pd.Timestamp(ann_date)
    # On or before event date
    at_or_before = df[df.index <= event_ts]
    if at_or_before.empty:
        return None
    event_idx_pos = len(at_or_before) - 1  # position in df

    # Need at least BASELINE_DAYS + PRE_GAP_DAYS + PRE_WINDOW positions before event
    min_pos_needed = BASELINE_DAYS + PRE_GAP_DAYS + PRE_WINDOW
    if event_idx_pos < min_pos_needed:
        return None
    # Need at least POST_WINDOW positions after event
    if len(df) - event_idx_pos - 1 < POST_WINDOW:
        return None

    # Compute daily returns
    df["ret"] = df["close"].pct_change()

    # Baseline window: [event_idx_pos - BASELINE_DAYS - PRE_GAP_DAYS, event_idx_pos - PRE_GAP_DAYS]
    baseline_start = event_idx_pos - BASELINE_DAYS - PRE_GAP_DAYS
    baseline_end = event_idx_pos - PRE_GAP_DAYS
    baseline_rets = df["ret"].iloc[baseline_start:baseline_end].dropna()
    if len(baseline_rets) < 40:  # require 2/3 of baseline window
        return None
    baseline_mean = float(baseline_rets.mean())

    # Abnormal returns: ar_t = ret_t - baseline_mean
    df["ar"] = df["ret"] - baseline_mean

    # Compute CAR windows
    def car_from_to(start_offset: int, end_offset: int) -> float | None:
        s = event_idx_pos + start_offset
        e = event_idx_pos + end_offset
        if s < 0 or e >= len(df):
            return None
        window = df["ar"].iloc[s:e + 1].dropna()
        if len(window) < (end_offset - start_offset) * 0.7:  # require 70% coverage
            return None
        return float(window.sum() * 100)  # in %

    car_neg30_0 = car_from_to(-PRE_WINDOW, 0)
    car_0_pos30 = car_from_to(0, POST_WINDOW // 2)
    car_0_pos60 = car_from_to(0, POST_WINDOW)
    car_neg5_pos5 = car_from_to(-5, 5)

    has_full_window = (
        car_neg30_0 is not None
        and car_0_pos60 is not None
    )

    return EventCAR(
        ts_code=ts_code, ann_date=ann_date,
        event_type=event_type, direction=direction, magnitude=magnitude,
        car_neg30_0=car_neg30_0, car_0_pos30=car_0_pos30,
        car_0_pos60=car_0_pos60, car_neg5_pos5=car_neg5_pos5,
        baseline_mean_daily_ret=round(baseline_mean * 10000, 1),  # in bp
        n_prices=len(df),
        has_full_window=has_full_window,
    )


def load_events(
    event_type: str,
    start_date: str = "20230101",
    end_date: str = "20260715",
    min_magnitude: float | None = None,
    max_magnitude: float | None = None,
    direction: str | None = None,
) -> list[dict]:
    """Load events of a given type from corp_event table."""
    query = (
        "SELECT ts_code, ann_date, event_type, direction, magnitude, details_json "
        "FROM corp_event WHERE event_type=? AND ann_date>=? AND ann_date<=?"
    )
    params: list = [event_type, start_date, end_date]
    if min_magnitude is not None:
        query += " AND magnitude >= ?"
        params.append(min_magnitude)
    if max_magnitude is not None:
        query += " AND magnitude <= ?"
        params.append(max_magnitude)
    if direction is not None:
        query += " AND direction = ?"
        params.append(direction)

    with get_market_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {"ts_code": r[0], "ann_date": r[1], "event_type": r[2],
         "direction": r[3], "magnitude": r[4], "details_json": r[5]}
        for r in rows
    ]


def compute_all_cars(
    event_type: str,
    min_magnitude: float | None = None,
    max_magnitude: float | None = None,
    direction: str | None = None,
    start_date: str = "20230101",
    end_date: str = "20260715",
    progress_every: int = 100,
) -> list[EventCAR]:
    """Compute CAR for all events of a type. Returns list of EventCAR."""
    events = load_events(
        event_type, start_date=start_date, end_date=end_date,
        min_magnitude=min_magnitude, max_magnitude=max_magnitude,
        direction=direction,
    )
    print(f"Computing CAR for {len(events):,} {event_type} events...", flush=True)

    results: list[EventCAR] = []
    skipped = 0
    t0 = time.time()
    for i, ev in enumerate(events):
        car = compute_event_car(
            ev["ts_code"], ev["ann_date"],
            event_type=ev["event_type"], direction=ev["direction"] or "",
            magnitude=ev["magnitude"],
        )
        if car is None:
            skipped += 1
        else:
            results.append(car)

        if (i + 1) % progress_every == 0:
            elapsed = time.time() - t0
            eta = elapsed / (i + 1) * (len(events) - i - 1)
            print(f"  [{i+1}/{len(events)}] CAR computed: {len(results)}, "
                  f"skipped: {skipped} ({elapsed:.0f}s, ETA {eta:.0f}s)", flush=True)

    print(f"Done: {len(results):,} CARs computed, {skipped:,} skipped "
          f"(insufficient price data)", flush=True)
    return results


def save_cars_to_db(cars: list[EventCAR], table_name: str = "event_car_result") -> None:
    """Persist CAR results for fast re-analysis without re-computing."""
    with get_market_conn() as conn:
        conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        conn.execute(f"""
            CREATE TABLE {table_name} (
                ts_code TEXT, ann_date TEXT, event_type TEXT,
                direction TEXT, magnitude REAL,
                car_neg30_0 REAL, car_0_pos30 REAL, car_0_pos60 REAL, car_neg5_pos5 REAL,
                baseline_mean_bp REAL, n_prices INTEGER, has_full_window INTEGER
            )
        """)
        conn.executemany(
            f"INSERT INTO {table_name} VALUES ({','.join(['?']*12)})",
            [(c.ts_code, c.ann_date, c.event_type, c.direction, c.magnitude,
              c.car_neg30_0, c.car_0_pos30, c.car_0_pos60, c.car_neg5_pos5,
              c.baseline_mean_daily_ret, c.n_prices, int(c.has_full_window))
             for c in cars],
        )
        conn.commit()
    print(f"Saved {len(cars):,} CAR results to {table_name}", flush=True)


if __name__ == "__main__":
    # CLI: compute CAR for one event type and save
    if len(sys.argv) < 2:
        print("Usage: python compute_car.py <event_type> [min_magnitude]")
        sys.exit(1)
    et = sys.argv[1]
    min_mag = float(sys.argv[2]) if len(sys.argv) >= 3 else None
    cars = compute_all_cars(et, min_magnitude=min_mag)
    save_cars_to_db(cars, table_name=f"car_{et}")
