#!/usr/bin/env python3
"""重算双标的 3 年估值分位(直连 pro.daily_basic,绕过 22 天增量缓存)."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

import pandas as pd  # noqa: E402
from loguru import logger  # noqa: E402

from stockhot.tushare_config import get_pro_api  # noqa: E402

TARGETS = [("300498.SZ", "温氏股份"), ("000876.SZ", "新希望")]
OUT = "davis_analyzer/studies/pigpair_valuation_full.json"


def pct(s: pd.Series, cur: float) -> float:
    s = s.dropna()
    return round((s < cur).sum() / len(s) * 100, 1)


def main() -> None:
    pro = get_pro_api(timeout=60)
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
    results = {}
    for ts_code, name in TARGETS:
        frames = []
        # 分 3 段拉取,避免单次行数限制
        cur = start
        while cur < end:
            seg_end = (pd.to_datetime(cur) + pd.Timedelta(days=400)).strftime("%Y%m%d")
            if seg_end > end:
                seg_end = end
            df = pro.daily_basic(ts_code=ts_code, start_date=cur, end_date=seg_end,
                                 fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv,circ_mv")
            frames.append(df)
            cur = (pd.to_datetime(seg_end) + pd.Timedelta(days=1)).strftime("%Y%m%d")
        db = pd.concat(frames).drop_duplicates("trade_date").sort_values("trade_date")
        pe = pd.to_numeric(db["pe_ttm"], errors="coerce")
        pb = pd.to_numeric(db["pb"], errors="coerce")
        ps = pd.to_numeric(db["ps"], errors="coerce")
        mv = pd.to_numeric(db["total_mv"], errors="coerce")
        last = db.iloc[-1]
        rec = {
            "snapshot": last["trade_date"], "close": float(last["close"]),
            "total_mv_yi": round(float(last["total_mv"]) / 1e4, 1),
            "n_days": len(db),
            "pe_ttm": float(last["pe_ttm"]) if pd.notna(last["pe_ttm"]) else None,
            "pe_pct": pct(pe, float(pe.iloc[-1])) if pd.notna(pe.iloc[-1]) else None,
            "pb": float(last["pb"]), "pb_pct": pct(pb, float(pb.iloc[-1])),
            "ps": float(last["ps"]), "ps_pct": pct(ps, float(ps.iloc[-1])),
            "mv_pct": pct(mv, float(mv.iloc[-1])),
            "pb_q": {f"p{p}": round(float(pb.quantile(p / 100)), 2) for p in [5, 10, 25, 50, 75, 90, 95]},
            "pe_q": {f"p{p}": (round(float(pe.quantile(p / 100)), 2) if pe.notna().any() else None) for p in [5, 10, 25, 50, 75, 90, 95]},
            "ps_q": {f"p{p}": round(float(ps.quantile(p / 100)), 3) for p in [5, 10, 25, 50, 75, 90, 95]},
            "mv_q": {f"p{p}": round(float(mv.quantile(p / 100)) / 1e4, 1) for p in [5, 10, 25, 50, 75, 90, 95]},
            "pb_min": round(float(pb.min()), 2), "pb_max": round(float(pb.max()), 2),
            "ps_min": round(float(ps.min()), 3), "ps_max": round(float(ps.max()), 3),
        }
        results[ts_code] = {"name": name, **rec}
        logger.success("{} {}: {} 天 | PB {:.2f}({}%分位) PS {:.2f}({}%分位) PE {}",
                       ts_code, name, len(db), rec["pb"], rec["pb_pct"], rec["ps"], rec["ps_pct"], rec["pe_ttm"])
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.success("写入 {}", OUT)


if __name__ == "__main__":
    main()
