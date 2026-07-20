"""Fetch corporate events into corp_event table for CAR analysis.

Fetches 4 event types from 2023-01-01 to 2026-07-15:
  1. share_float   — 限售解禁 (Tushare, by ann_date)
  2. holder_trade  — 股东增减持 (Tushare, by trade_date window)
  3. repurchase    — 回购 (Tushare, by month)
  4. pledge        — 股权质押 (akshare, quarterly snapshots)

Each event is normalized to: (ts_code, ann_date, event_type, direction,
magnitude, details_json). Direction: negative/positive/neutral.

Idempotent: re-running skips dates already fetched (uses existence check).
"""
import os, sys, json, time
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")

import sqlite3
from datetime import datetime, timedelta
from stockhot.data_layer.market_db import get_connection as get_market_conn, init_db
from stockhot.storage.database import init_database
from davis_analyzer.tushare_client import TushareClient

init_db()
init_database()
client = TushareClient()

START = "20230101"
END = "20260715"


def _ts() -> float:
    return time.time()


def _normalize_ts_code(code: str) -> str:
    """akshare returns '600370' (no suffix); Tushare uses '600370.SH'."""
    if "." in code:
        return code
    if not code or len(code) != 6:
        return code
    if code.startswith(("60", "68", "11", "13")):
        return f"{code}.SH"
    return f"{code}.SZ"


# ═══════════════════════════════════════════════════════════════
# Event 1: share_float — 限售解禁
# ═══════════════════════════════════════════════════════════════
def fetch_share_float():
    """限售解禁 — 按周滚动（API 单次限 6000 条，按周分批避免截断）."""
    print("\n" + "=" * 70)
    print("  [1/4] share_float 限售解禁")
    print("=" * 70)

    with get_market_conn() as c:
        row = c.execute(
            "SELECT COUNT(*), MIN(ann_date), MAX(ann_date) FROM corp_event "
            "WHERE event_type='share_float'"
        ).fetchone()
    print(f"  Already in DB: {row[0]:,} events ({row[1]} → {row[2]})")

    # Find latest fetched date to resume from
    resume_from = row[2] if row[2] else START
    print(f"  Resuming from: {resume_from}")

    # Roll by 7-day windows (each call returns ≤6000 rows, ~3-5 days coverage)
    start_dt = datetime.strptime(resume_from, "%Y%m%d")
    end_dt = datetime.strptime(END, "%Y%m%d")
    windows = []
    cur = start_dt
    while cur <= end_dt:
        nxt = cur + timedelta(days=7)
        windows.append((cur.strftime("%Y%m%d"), min(nxt, end_dt).strftime("%Y%m%d")))
        cur = nxt + timedelta(days=1)

    total_inserted = 0
    for i, (s, e) in enumerate(windows):
        try:
            df = client._pro.share_float(start_date=s, end_date=e)
            if df is None or df.empty:
                continue
            # Filter to window (API may include some out-of-range)
            df = df[(df["ann_date"] >= s) & (df["ann_date"] <= e)]
            if df.empty:
                continue

            records = []
            for r in df.to_dict("records"):
                if not r.get("ts_code") or not r.get("ann_date"):
                    continue
                mag = float(r["float_ratio"]) if r.get("float_ratio") else None
                details = {
                    "float_date": str(r.get("float_date", "")),
                    "float_share": float(r["float_share"]) if r.get("float_share") else None,
                    "holder_name": str(r.get("holder_name", "")),
                    "share_type": str(r.get("share_type", "")),
                }
                records.append((
                    r["ts_code"], str(r["ann_date"]), "share_float",
                    "negative", mag,
                    json.dumps(details, ensure_ascii=False),
                    "tushare", _ts(),
                ))

            if records:
                with get_market_conn() as c:
                    c.executemany(
                        "INSERT OR IGNORE INTO corp_event "
                        "(ts_code, ann_date, event_type, direction, magnitude, details_json, source, fetched_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        records,
                    )
                    c.commit()
                total_inserted += len(records)

            if (i + 1) % 13 == 0:  # ~quarterly progress
                print(f"  [{i+1}/{len(windows)}] {s}→{e}: +{len(records)} (cumulative {total_inserted:,})")
        except Exception as e:
            print(f"  {s}→{e} ERROR: {e}")

    print(f"  Done: {total_inserted:,} share_float events")


# ═══════════════════════════════════════════════════════════════
# Event 2: stk_holdertrade — 股东增减持
# ═══════════════════════════════════════════════════════════════
def fetch_holder_trade():
    """股东增减持 — 按 10 天窗口滚动."""
    print("\n" + "=" * 70)
    print("  [2/4] stk_holdertrade 股东增减持")
    print("=" * 70)

    with get_market_conn() as c:
        row = c.execute(
            "SELECT COUNT(*), MIN(ann_date), MAX(ann_date) FROM corp_event "
            "WHERE event_type='holder_trade'"
        ).fetchone()
    print(f"  Already in DB: {row[0]:,} events ({row[1]} → {row[2]})")

    # Roll by 10-day windows
    start_dt = datetime.strptime(START, "%Y%m%d")
    end_dt = datetime.strptime(END, "%Y%m%d")
    windows = []
    cur = start_dt
    while cur <= end_dt:
        nxt = cur + timedelta(days=10)
        windows.append((cur.strftime("%Y%m%d"), min(nxt, end_dt).strftime("%Y%m%d")))
        cur = nxt + timedelta(days=1)

    total_inserted = 0
    for i, (s, e) in enumerate(windows):
        try:
            df = client._pro.stk_holdertrade(start_date=s, end_date=e)
            if df is None or df.empty:
                continue

            records = []
            for r in df.to_dict("records"):
                if not r.get("ts_code") or not r.get("ann_date"):
                    continue
                in_de = str(r.get("in_de", "")).upper()
                # in_de: IN=增持(positive), DE=减持(negative)
                direction = "positive" if in_de == "IN" else "negative" if in_de == "DE" else "neutral"
                mag = float(r["change_ratio"]) if r.get("change_ratio") else None
                details = {
                    "holder_name": str(r.get("holder_name", "")),
                    "holder_type": str(r.get("holder_type", "")),  # C=公司, P=个人
                    "in_de": in_de,
                    "change_vol": float(r["change_vol"]) if r.get("change_vol") else None,
                    "after_share": float(r["after_share"]) if r.get("after_share") else None,
                    "after_ratio": float(r["after_ratio"]) if r.get("after_ratio") else None,
                }
                records.append((
                    r["ts_code"], str(r["ann_date"]), "holder_trade",
                    direction, mag, json.dumps(details, ensure_ascii=False),
                    "tushare", _ts(),
                ))

            if records:
                with get_market_conn() as c:
                    c.executemany(
                        "INSERT OR IGNORE INTO corp_event "
                        "(ts_code, ann_date, event_type, direction, magnitude, details_json, source, fetched_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        records,
                    )
                    c.commit()
                total_inserted += len(records)

            if (i + 1) % 12 == 0:
                print(f"  [{i+1}/{len(windows)}] {s}→{e}: +{len(records)} (cumulative {total_inserted:,})")
        except Exception as e:
            print(f"  {s}→{e} ERROR: {e}")

    print(f"  Done: {total_inserted:,} holder_trade events")


# ═══════════════════════════════════════════════════════════════
# Event 3: repurchase — 回购
# ═══════════════════════════════════════════════════════════════
def fetch_repurchase():
    """回购 — 按 ts_code 拉历史所有回购记录（period 接口只返回当前活跃状态）.

    5500 stocks × 1 call each ≈ 14 min at 400/min rate limit.
    """
    print("\n" + "=" * 70)
    print("  [3/4] repurchase 回购")
    print("=" * 70)

    with get_market_conn() as c:
        row = c.execute(
            "SELECT COUNT(*), MIN(ann_date), MAX(ann_date) FROM corp_event "
            "WHERE event_type='repurchase'"
        ).fetchone()
    print(f"  Already in DB: {row[0]:,} events ({row[1]} → {row[2]})")

    # Get list of all active stocks
    with get_market_conn() as c:
        rows = c.execute(
            "SELECT ts_code FROM stock_basic WHERE list_status='L' ORDER BY ts_code"
        ).fetchall()
    all_codes = [r[0] for r in rows]
    print(f"  Total stocks to fetch: {len(all_codes)}")

    # Find which codes we've already fetched (resume support)
    with get_market_conn() as c:
        done = set(r[0] for r in c.execute(
            "SELECT DISTINCT ts_code FROM corp_event WHERE event_type='repurchase'"
        ).fetchall())
    todo = [c for c in all_codes if c not in done]
    print(f"  Already fetched: {len(done)}, remaining: {len(todo)}")

    total_inserted = 0
    for i, code in enumerate(todo):
        try:
            df = client._pro.repurchase(ts_code=code)
            if df is None or df.empty:
                continue
            df = df[(df["ann_date"] >= START) & (df["ann_date"] <= END)]
            if df.empty:
                continue

            records = []
            for r in df.to_dict("records"):
                if not r.get("ts_code") or not r.get("ann_date"):
                    continue
                mag = float(r["amount"]) if r.get("amount") else None
                details = {
                    "end_date": str(r.get("end_date", "")),
                    "proc": str(r.get("proc", "")),
                    "exp_date": str(r.get("exp_date", "")),
                    "vol": float(r["vol"]) if r.get("vol") else None,
                    "high_limit": float(r["high_limit"]) if r.get("high_limit") else None,
                    "low_limit": float(r["low_limit"]) if r.get("low_limit") else None,
                }
                records.append((
                    code, str(r["ann_date"]), "repurchase",
                    "positive", mag,
                    json.dumps(details, ensure_ascii=False),
                    "tushare", _ts(),
                ))

            if records:
                with get_market_conn() as c:
                    c.executemany(
                        "INSERT OR IGNORE INTO corp_event "
                        "(ts_code, ann_date, event_type, direction, magnitude, details_json, source, fetched_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        records,
                    )
                    c.commit()
                total_inserted += len(records)

            if (i + 1) % 500 == 0:
                print(f"  [{i+1}/{len(todo)}] {code}: cumulative {total_inserted:,}")
        except Exception as e:
            if "频率" in str(e) or "limit" in str(e).lower():
                time.sleep(2)
            # Silent fail for individual stocks

    print(f"  Done: {total_inserted:,} repurchase events")


# ═══════════════════════════════════════════════════════════════
# Event 4: pledge — 股权质押 (akshare)
# ═══════════════════════════════════════════════════════════════
def fetch_pledge():
    """股权质押 — 季度截面 (akshare).

    akshare returns the latest snapshot for a given date; we use end-of-quarter
    dates to track how质押率 changes over time.
    """
    print("\n" + "=" * 70)
    print("  [4/4] pledge 股权质押 (akshare)")
    print("=" * 70)

    with get_market_conn() as c:
        row = c.execute(
            "SELECT COUNT(*), MIN(ann_date), MAX(ann_date) FROM corp_event "
            "WHERE event_type='pledge'"
        ).fetchone()
    print(f"  Already in DB: {row[0]:,} events ({row[1]} → {row[2]})")

    import akshare as ak

    # End-of-quarter dates
    quarter_ends = []
    for y in (2023, 2024, 2025, 2026):
        for m, d in [(3, 31), (6, 30), (9, 30), (12, 31)]:
            qdate = f"{y}{m:02d}{d:02d}"
            if qdate <= END:
                quarter_ends.append(qdate)

    total_inserted = 0
    for qdate in quarter_ends:
        try:
            df = ak.stock_gpzy_pledge_ratio_em(date=qdate)
            if df is None or df.empty:
                continue

            records = []
            for r in df.to_dict("records"):
                code = str(r.get("股票代码", "")).strip()
                if not code or len(code) != 6:
                    continue
                ts_code = _normalize_ts_code(code)
                pledge_ratio = r.get("质押比例")
                try:
                    pledge_ratio = float(pledge_ratio)
                except (ValueError, TypeError):
                    pledge_ratio = None
                if pledge_ratio is None:
                    continue

                # direction: >50% negative, 30-50% neutral-warning, <30% neutral
                direction = "negative" if pledge_ratio > 50 else "neutral"

                details = {
                    "name": str(r.get("股票简称", "")),
                    "industry": str(r.get("所属行业", "")),
                    "pledge_shares": float(r["质押股数"]) if r.get("质押股数") else None,
                    "pledge_mv": float(r["质押市值"]) if r.get("质押市值") else None,
                    "pledge_count": int(r["质押笔数"]) if r.get("质押笔数") else None,
                    "unlimited_pledge": float(r["无限售股质押数"]) if r.get("无限售股质押数") else None,
                    "limited_pledge": float(r["限售股质押数"]) if r.get("限售股质押数") else None,
                }
                records.append((
                    ts_code, qdate, "pledge", direction,
                    pledge_ratio, json.dumps(details, ensure_ascii=False),
                    "akshare", _ts(),
                ))

            if records:
                with get_market_conn() as c:
                    c.executemany(
                        "INSERT OR IGNORE INTO corp_event "
                        "(ts_code, ann_date, event_type, direction, magnitude, details_json, source, fetched_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        records,
                    )
                    c.commit()
                total_inserted += len(records)
            print(f"  {qdate}: +{len(records)} (cumulative {total_inserted:,})")
        except Exception as e:
            print(f"  {qdate} ERROR: {e}")

    print(f"  Done: {total_inserted:,} pledge events")


def main():
    print(f"Fetching events from {START} to {END}")
    print(f"  DB: {get_market_conn.__wrapped__ if hasattr(get_market_conn, '__wrapped__') else 'market_data.db'}")

    # Pick which events to fetch via CLI args
    args = set(sys.argv[1:])
    fetch_all = not args or "all" in args

    if fetch_all or "share_float" in args:
        fetch_share_float()
    if fetch_all or "holder_trade" in args:
        fetch_holder_trade()
    if fetch_all or "repurchase" in args:
        fetch_repurchase()
    if fetch_all or "pledge" in args:
        fetch_pledge()

    # Final summary
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    with get_market_conn() as c:
        for et in ("share_float", "holder_trade", "repurchase", "pledge"):
            row = c.execute(
                "SELECT COUNT(*), COUNT(DISTINCT ts_code), MIN(ann_date), MAX(ann_date) "
                "FROM corp_event WHERE event_type=?", (et,)
            ).fetchone()
            print(f"  {et:<15} {row[0]:>7,} events  {row[1]:>5} stocks  ({row[2]} → {row[3]})")


if __name__ == "__main__":
    main()
