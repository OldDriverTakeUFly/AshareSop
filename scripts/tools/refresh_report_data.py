"""Batch-refresh quantitative data snapshots for existing research reports.

Scans docs/个股研报/*.md, extracts each report's ts_code, re-runs the
davis_analyzer valuation + prosperity engines against the latest cached
data, and writes a comparison table (old snapshot from the report text vs
new engine output) to studies/output/report_refresh_{date}.csv.

This is the data-only half of the report refresh — it does NOT rewrite
report prose. Use the output to identify which reports need a full
narrative update (rating change, big valuation shift) vs which are still
current.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/refresh_report_data.py
"""

from __future__ import annotations

import csv
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger

logger.remove()
logger.add(sys.stderr, level="WARNING")

REPORTS_DIR = PROJECT_ROOT / "docs" / "个股研报"
OUTPUT_CSV = PROJECT_ROOT / "studies" / "output" / f"report_refresh_{date.today().strftime('%Y%m%d')}.csv"

_TS_CODE_RE = re.compile(r"(\d{6}\.[A-Z]{2})")
# Old snapshot fields embedded in report headers.
_PE_RE = re.compile(r"PE-TTM \*\*([\d.]+)\*\*（([\d.]+)%")
_PRICE_RE = re.compile(r"股价快照[：:]\s*([\d.]+)\s*元")


def extract_reports() -> list[tuple[str, str]]:
    """Return [(ts_code, report_path), ...] for all individual stock reports."""
    results = []
    for md in sorted(REPORTS_DIR.rglob("*.md")):
        text = md.read_text(encoding="utf-8", errors="ignore")
        m = _TS_CODE_RE.search(text)
        if m:
            results.append((m.group(1), str(md)))
    return results


def extract_old_snapshot(report_path: str) -> dict:
    """Parse old PE/price/percentile from the report's header block."""
    text = Path(report_path).read_text(encoding="utf-8", errors="ignore")
    # Only look at the first 2000 chars (header摘要 block).
    head = text[:2000]
    pe_match = _PE_RE.search(head)
    price_match = _PRICE_RE.search(head)
    return {
        "old_pe": float(pe_match.group(1)) if pe_match else None,
        "old_pe_pct": float(pe_match.group(2)) if pe_match else None,
        "old_price": float(price_match.group(1)) if price_match else None,
    }


def fetch_latest_valuation(ts_code: str) -> dict:
    """Re-run valuation engine for one stock. Failures return empty dict."""
    out = {}
    try:
        from davis_analyzer.tushare_client import TushareClient
        from davis_analyzer.valuation import fetch_valuation_history, calculate_valuation_score
        from davis_analyzer.stock_universe import build_stock_universe

        client = TushareClient()
        history = fetch_valuation_history(client, ts_code)
        if not history:
            return out
        # fetch_valuation_history may return stale data (cache gap); force a
        # fresh daily_basic pull for the latest snapshot.
        from datetime import date as _d, timedelta as _td
        end = _d.today().strftime("%Y%m%d")
        start = (_d.today() - _td(days=1095)).strftime("%Y%m%d")
        db = client.get_daily_basic(ts_code, start, end)
        latest_pe = latest_pb = latest_close = None
        trade_date = ""
        if db is not None and len(db) > 0:
            db = db.sort_values("trade_date")
            last_row = db.iloc[-1]
            latest_pe = float(last_row.get("pe_ttm", 0)) or None
            latest_pb = float(last_row.get("pb", 0)) or None
            latest_close = float(last_row.get("close", 0)) or None
            trade_date = str(last_row.get("trade_date", ""))

        # Determine is_cyclical from stock universe (needed by calculate_valuation_score).
        try:
            universe = build_stock_universe(client)
            is_cyc = any(s.ts_code == ts_code and s.is_cyclical for s in universe)
        except Exception:
            is_cyc = False

        score, pe_pct, pb_pct = calculate_valuation_score(history, is_cyclical=is_cyc)
        out = {
            "new_price": round(latest_close, 2) if latest_close else None,
            "new_pe": round(latest_pe, 2) if latest_pe else None,
            "new_pe_pct": round(pe_pct * 100, 1),
            "new_pb_pct": round(pb_pct * 100, 1),
            "valuation_score": round(score, 1),
            "trade_date": trade_date,
        }
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def fetch_latest_prosperity(ts_code: str) -> dict:
    """Re-run prosperity engine for one stock."""
    try:
        from davis_analyzer.tushare_client import TushareClient
        from davis_analyzer.financial_fetcher import fetch_financial_data
        from davis_analyzer.prosperity import calculate_prosperity_score

        client = TushareClient()
        fin = fetch_financial_data(client, ts_code, periods=8)
        if not fin:
            return {}
        prosp = calculate_prosperity_score(fin)
        return {
            "prosperity_score": round(prosp.composite_score, 1),
            "delta_g": round(prosp.delta_g, 1),
        }
    except Exception:
        return {}


def main() -> int:
    reports = extract_reports()
    print(f"[refresh] 发现 {len(reports)} 篇个股研报")

    rows = []
    for i, (ts_code, path) in enumerate(reports, 1):
        name = Path(path).stem.replace("深度研报", "").replace("AI应用深度研报", "")
        old = extract_old_snapshot(path)
        new_val = fetch_latest_valuation(ts_code)
        new_pro = fetch_latest_prosperity(ts_code)

        # Detect material changes
        flags = []
        if old["old_pe_pct"] and new_val.get("new_pe_pct"):
            shift = new_val["new_pe_pct"] - old["old_pe_pct"]
            if abs(shift) >= 20:
                flags.append(f"估值分位{'↑' if shift>0 else '↓'}{abs(shift):.0f}pp")
        if new_pro.get("delta_g") is not None and new_pro["delta_g"] < 0:
            flags.append("ΔG转负(减速)")

        row = {
            "ts_code": ts_code,
            "name": name,
            "report_path": path,
            **old,
            **new_val,
            **new_pro,
            "flags": " | ".join(flags),
        }
        rows.append(row)
        if i % 10 == 0:
            print(f"  进度 {i}/{len(reports)}")

    # Write CSV
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fields = ["ts_code", "name", "trade_date", "old_price", "new_price",
              "old_pe", "new_pe", "old_pe_pct", "new_pe_pct", "new_pb_pct",
              "valuation_score", "prosperity_score", "delta_g", "flags", "report_path"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    flagged = [r for r in rows if r["flags"]]
    print(f"\n[refresh] 完成! {len(rows)} 只写入 {OUTPUT_CSV.name}")
    print(f"[refresh] 其中 {len(flagged)} 只有显著变化(需重点更新研报):")
    for r in flagged[:20]:
        print(f"  {r['ts_code']} {r['name']:<10} {r['flags']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
