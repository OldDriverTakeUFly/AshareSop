"""Diagnostic: trace volume signal computation for specific cases."""
import os, sys
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")

import pandas as pd
from stockhot.data_layer.market_db import get_connection as get_market_conn

# Replicate the internals of _compute_volume_signals to print diagnostic info
_VOL_LOOKBACK_DAYS = 130
_VOL_MA_WINDOW = 20
_VOL_MIN_HISTORY = 60
_BOX_WINDOW = 20
_BOX_MAX_AMPLITUDE = 0.15
_BOX_BREAKOUT_BUFFER = 1.01
_PLATFORM_VOL_RATIO = 1.5
_EXTREME_VOL_RATIO = 2.0
_LOW_POS_PCT = 20.0
_HIGH_POS_PCT = 80.0
_LOW_VOL_PCTILE = 80.0
_HIGH_VOL_PCTILE = 90.0


def diagnose(code, trade_date):
    with get_market_conn() as conn:
        date_rows = conn.execute(
            "SELECT DISTINCT trade_date FROM daily_price "
            "WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT ?",
            (trade_date, _VOL_LOOKBACK_DAYS),
        ).fetchall()
    dates = [r[0] for r in date_rows]
    start_str = dates[-1]

    with get_market_conn() as conn:
        rows = conn.execute(
            "SELECT trade_date, high, low, close, vol FROM daily_price "
            "WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
            "AND close IS NOT NULL ORDER BY trade_date",
            (code, start_str, trade_date),
        ).fetchall()
    df = pd.DataFrame(rows, columns=["trade_date", "high", "low", "close", "vol"])
    for col in ["high", "low", "close", "vol"]:
        df[col] = df[col].astype(float)
    print(f"\n  [{code} @ {trade_date}] rows={len(df)}")

    today_close = df["close"].iloc[-1]
    today_vol = df["vol"].iloc[-1]
    position_pct = (df["close"] <= today_close).sum() / len(df) * 100
    vol_ma = df["vol"].iloc[-_VOL_MA_WINDOW - 1 : -1].mean()
    vol_ratio = today_vol / vol_ma
    vol_pctile = (df["vol"] <= today_vol).sum() / len(df) * 100

    box = df.iloc[-_BOX_WINDOW:]
    box_high = box["high"].max()
    box_low = box["low"].min()
    box_amplitude = (box_high - box_low) / box_low
    is_breakout = today_close >= box_high * _BOX_BREAKOUT_BUFFER
    print(f"    today: close={today_close:.2f} vol={today_vol:.0f}")
    print(f"    pos_pct={position_pct:.1f}% vol_ratio={vol_ratio:.2f} vol_pctile={vol_pctile:.1f}%")
    print(f"    box20: high={box_high:.2f} low={box_low:.2f} amp={box_amplitude*100:.1f}%")
    print(f"    close vs box_high*1.01 = {box_high*1.01:.2f} → is_breakout={is_breakout}")
    print(f"    box_amp<=15%? {box_amplitude <= _BOX_MAX_AMPLITUDE}")
    print(f"    vol_ratio>=1.5? {vol_ratio >= _PLATFORM_VOL_RATIO}")

# Investigate specific cases
print("=" * 78)
print("  Why high_vol triggered for 300750.SZ @ 20260321 (vol_ratio only 1.68)?")
print("=" * 78)
diagnose("300750.SZ", "20260321")

print("\n" + "=" * 78)
print("  Why no platform_breakout triggered? Check 000001.SZ @ 20260123 (box_amp 7.7%)")
print("=" * 78)
diagnose("000001.SZ", "20260123")

print("\n" + "=" * 78)
print("  Check 600519.SH @ 20260123 (low_vol triggered despite vol_ratio 1.58)")
print("=" * 78)
diagnose("600519.SH", "20260123")
