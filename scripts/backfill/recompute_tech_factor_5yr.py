"""Recompute tech_factor for 5-year window using cached daily_price.

Reads daily_price (5585 stocks × 1351 days), computes 6 technical indicators
per stock, writes to tech_factor table (REPLACE).

Indicators (reusing stockhot.technical_analyzer.indicators):
    tech_score       composite_technical_score (0-100)
    ma_align         'bullish'/'bearish'/'mixed'
    ma_align_score   0-100
    rsi              0-100
    macd_hist        MACD histogram
    kdj_j            KDJ J value
    boll_position    0-1

Usage:
    python davis_analyzer/scripts/recompute_tech_factor_5yr.py
"""
import os
import sys
import time
import sqlite3
from dotenv import load_dotenv

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_REPO_ROOT, ".env"), override=True)
os.environ.setdefault("PROJECT_ROOT", _REPO_ROOT)

import loguru
loguru.logger.remove()
loguru.logger.add(sys.stderr, level="WARNING")

import numpy as np
import pandas as pd

from davis_analyzer.tushare_client import _CACHE_DB
from stockhot.technical_analyzer.scoring import composite_technical_score
from stockhot.technical_analyzer.indicators import ma, macd, rsi, kdj, bollinger


def load_daily_prices(conn, ts_code):
    """Load OHLCV for one stock from daily_price (uses close + adj_factor)."""
    rows = conn.execute(
        "SELECT trade_date, open, high, low, close, vol, adj_factor "
        "FROM daily_price WHERE ts_code=? ORDER BY trade_date",
        (ts_code,),
    ).fetchall()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["trade_date","open","high","low","close","vol","adj_factor"])
    # Use adjusted close for indicator calculation
    df["adj_close"] = df["close"].astype(float) * df["adj_factor"].fillna(1).astype(float)
    df["adj_open"] = df["open"].astype(float) * df["adj_factor"].fillna(1).astype(float)
    df["adj_high"] = df["high"].astype(float) * df["adj_factor"].fillna(1).astype(float)
    df["adj_low"] = df["low"].astype(float) * df["adj_factor"].fillna(1).astype(float)
    df["volume"] = df["vol"].astype(float)
    return df


def compute_indicators(df):
    """Compute all 6 indicators. Returns list of dicts for DB insert."""
    if df is None or len(df) < 30:
        return []

    close = df["adj_close"]
    high = df["adj_high"]
    low = df["adj_low"]
    vol = df["volume"]

    results = []

    # Pre-compute indicators on full series
    try:
        rsi_s = rsi(df[["adj_close"]].rename(columns={"adj_close":"close"}))
    except:
        rsi_s = None
    try:
        macd_df = macd(df[["adj_close"]].rename(columns={"adj_close":"close"}))
    except:
        macd_df = None
    try:
        kdj_df = kdj(df[["adj_high","adj_low","adj_close"]].rename(
            columns={"adj_high":"high","adj_low":"low","adj_close":"close"}))
    except:
        kdj_df = None
    try:
        boll_df = bollinger(df[["adj_close"]].rename(columns={"adj_close":"close"}))
    except:
        boll_df = None

    # MA alignment
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()

    dates = df["trade_date"].tolist()

    for i in range(len(dates)):
        idx = i
        td = dates[i]

        # Skip if insufficient data for meaningful indicators
        if i < 25:
            continue

        # RSI
        rsi_val = float(rsi_s.iloc[idx]) if rsi_s is not None and idx < len(rsi_s) and not np.isnan(rsi_s.iloc[idx]) else None

        # MACD hist
        macd_h = float(macd_df["macd_hist"].iloc[idx]) if macd_df is not None and idx < len(macd_df) and "macd_hist" in macd_df.columns and not np.isnan(macd_df["macd_hist"].iloc[idx]) else None

        # KDJ J
        kdj_j = float(kdj_df["j"].iloc[idx]) if kdj_df is not None and idx < len(kdj_df) and "j" in kdj_df.columns and not np.isnan(kdj_df["j"].iloc[idx]) else None

        # Boll position
        boll_pos = None
        if boll_df is not None and idx < len(boll_df):
            upper = boll_df["boll_upper"].iloc[idx] if "boll_upper" in boll_df.columns else None
            lower = boll_df["boll_lower"].iloc[idx] if "boll_lower" in boll_df.columns else None
            c = close.iloc[idx]
            if upper is not None and lower is not None and not np.isnan(upper) and not np.isnan(lower) and upper > lower:
                boll_pos = float(max(0.0, min(1.0, (c - lower) / (upper - lower))))

        # MA alignment
        ma5_v = float(ma5.iloc[idx]) if not np.isnan(ma5.iloc[idx]) else None
        ma10_v = float(ma10.iloc[idx]) if not np.isnan(ma10.iloc[idx]) else None
        ma20_v = float(ma20.iloc[idx]) if not np.isnan(ma20.iloc[idx]) else None
        ma_align = None
        ma_align_score = None
        if ma5_v and ma10_v and ma20_v:
            if ma5_v > ma10_v > ma20_v:
                ma_align = "bullish"
                ma_align_score = 100.0
            elif ma5_v < ma10_v < ma20_v:
                ma_align = "bearish"
                ma_align_score = 0.0
            else:
                ma_align = "mixed"
                ma_align_score = 50.0

        # tech_score (composite) — simplified: weighted average of available sub-scores
        scores = []
        if rsi_val is not None:
            scores.append(("rsi", rsi_val, 0.15))
        if ma_align_score is not None:
            scores.append(("ma", ma_align_score, 0.30))
        if macd_h is not None:
            macd_s = 100.0 if macd_h > 0 else 0.0
            scores.append(("macd", macd_s, 0.20))
        if kdj_j is not None:
            kdj_s = max(0, min(100, kdj_j))
            scores.append(("kdj", kdj_s, 0.15))
        if boll_pos is not None:
            boll_s = (1 - boll_pos) * 100  # lower position = higher score (contrarian)
            scores.append(("boll", boll_s, 0.10))
        # Volume-price (placeholder)
        scores.append(("vp", 50.0, 0.10))

        tech_score = sum(s * w for _, s, w in scores) if scores else None

        results.append((
            df["ts_code"].iloc[0] if "ts_code" in df.columns else "",
            td, tech_score, ma_align, ma_align_score,
            rsi_val, macd_h, kdj_j, boll_pos,
            time.time(),
        ))

    return results


def main():
    print("=" * 80)
    print("Recompute tech_factor: 5-year window (2021-01 ~ 2026-07)")
    print("=" * 80)

    with sqlite3.connect(str(_CACHE_DB)) as conn:
        all_codes = [r[0] for r in conn.execute(
            "SELECT DISTINCT ts_code FROM daily_price WHERE ts_code LIKE '0%' OR ts_code LIKE '3%' OR ts_code LIKE '6%'"
        ).fetchall()]
        # Filter to main board only
        all_codes = [c for c in all_codes if c.startswith(("00","30","60","68"))]

    print(f"Stocks: {len(all_codes)}")

    t0 = time.time()
    total_rows = 0
    batch = []
    BATCH_SIZE = 50000

    with sqlite3.connect(str(_CACHE_DB)) as conn:
        for i, code in enumerate(all_codes):
            df = load_daily_prices(conn, code)
            if df is None:
                continue
            df["ts_code"] = code
            records = compute_indicators(df)
            batch.extend(records)
            total_rows += len(records)

            # Flush batch
            if len(batch) >= BATCH_SIZE:
                conn.executemany("""
                    INSERT OR REPLACE INTO tech_factor
                    (ts_code,trade_date,tech_score,ma_align,ma_align_score,
                     rsi,macd_hist,kdj_j,boll_position,fetched_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, batch)
                conn.commit()
                batch = []

            if (i+1) % 500 == 0 or (i+1) == len(all_codes):
                elapsed = time.time() - t0
                rate = (i+1) / elapsed if elapsed > 0 else 0
                eta = (len(all_codes) - i - 1) / rate if rate > 0 else 0
                print(f"  [{i+1}/{len(all_codes)}] {code} rows={total_rows:,} "
                      f"({rate:.1f}/s ETA {eta:.0f}s)", flush=True)

        # Final flush
        if batch:
            conn.executemany("""
                INSERT OR REPLACE INTO tech_factor
                (ts_code,trade_date,tech_score,ma_align,ma_align_score,
                 rsi,macd_hist,kdj_j,boll_position,fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, batch)
            conn.commit()

    elapsed = time.time() - t0
    print(f"\nDone: {len(all_codes)} stocks, {total_rows:,} rows, {elapsed:.0f}s")

    with sqlite3.connect(str(_CACHE_DB)) as conn:
        r = conn.execute("SELECT COUNT(*), COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date) FROM tech_factor").fetchone()
        print(f"DB: rows={r[0]:,} days={r[1]} range={r[2]}~{r[3]}")


if __name__ == "__main__":
    main()
