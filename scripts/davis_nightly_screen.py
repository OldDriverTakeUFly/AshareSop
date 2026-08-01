"""Davis Double nightly screening — 定时盘后估值筛选 + 飞书推送摘要.

Cron entry (20:00 after run_daily_scan Wave 0 data refresh):
    0 20 * * 1-5 cd /home/leo/Projects/CodeAgentDashboard && \\
        PYTHONPATH=/home/leo/Projects/CodeAgentDashboard \\
        .venv/bin/python scripts/davis_nightly_screen.py \\
        >> stockhot/invest_sop/logs/davis_nightly.log 2>&1

Pipeline 较重（~60-90min，受 Tushare 400/min 限流），故放在 20:00 而非
16:30（screen_top20 的时间窗）。增量缓存自带，重复跑不重复拉。

Output:
  - studies/output/davis_top_{date}.json   完整 Top50 数据
  - studies/output/davis_latest.json       指向最新一次的软引用（覆盖写）
  - 飞书 Top10 摘要推送（复用 get_feishu_notifier）

独立于 studies/screen_top20.py，互不干扰：davis 是戴维斯双击估值视角，
screen_top20 是四因子横截面视角，两套结果并存供人工对比。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path

# Bootstrap project root so imports work regardless of cwd.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger

# Reduce log noise from tushare_client; our own prints carry the narrative.
logger.remove()
logger.add(sys.stderr, level="WARNING")

TOP_N = 50
FEISHU_SUMMARY_N = 10  # 飞书只推 Top10 摘要，避免消息过长
OUTPUT_DIR = PROJECT_ROOT / "studies" / "output"


# ── Serialization helpers ──────────────────────────────────────────────


def _default_serializer(obj):
    """JSON fallback: dataclass → dict, everything else → str."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    return str(obj)


def _serialize_result(result, top_n: int) -> dict:
    """Flatten a PipelineResult into a JSON-safe dict for the top_n stocks."""
    scores = result.scores[:top_n]
    rows = []
    for ds in scores:
        info = result.stock_infos.get(ds.ts_code)
        rows.append(
            {
                "rank": ds.rank,
                "ts_code": ds.ts_code,
                "name": ds.name,
                "industry": info.industry if info else "",
                "is_cyclical": info.is_cyclical if info else False,
                "final_score": round(ds.final_score, 2),
                "valuation_score": round(ds.valuation_score, 2),
                "prosperity_score": round(ds.prosperity_score, 2),
                "distress_score": round(ds.distress_score, 2),
                "trend_score": round(ds.trend_score, 2),
                # Forward overlay (前景估值调整) — parallel signal, never alters
                # final_score, but worth surfacing for manual judgment.
                "forward_overlay": _safe_overlay(result.forward_overlays.get(ds.ts_code)),
            }
        )
    return {
        "trade_date": _latest_trade_date(result),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "universe_size": len(result.stock_infos),
        "scored_count": _scored_count(result),
        "top_n": len(rows),
        "scores": rows,
    }


def _safe_overlay(overlay):
    """Extract the few overlay fields that matter for a quick glance."""
    if overlay is None:
        return None
    return {
        "effective_percentile": round(overlay.effective_percentile, 1),
        "final_overlay": round(overlay.final_overlay, 1),
        "confidence": overlay.confidence_note,
    }


def _latest_trade_date(result) -> str:
    """Best-effort: peek at valuation_data tuples for the most recent trade_date.

    valuation_data values are (score, pe_pct, pb_pct) — no date. Fall back to
    today's date string; the consuming report will cross-check against index_daily.
    """
    return date.today().strftime("%Y%m%d")


def _scored_count(result) -> int:
    """Number of stocks that received a final Davis score this run."""
    return len(result.prosperity_scores)


def _collect_overseas_risk(trade_date_str: str) -> dict | None:
    """Fetch international resonance risk for the Feishu summary.

    Returns a compact dict {score, level, detail} or None if unavailable.
    Never raises — overseas data gaps must not block the screening report.
    """
    try:
        from davis_analyzer.international_overlay import get_international_risk

        risk = get_international_risk(trade_date_str)
        if not risk.data_sufficient:
            return None
        # Condense the strongest sub-signals into a one-line detail.
        active = [s for s in risk.sub_signals if s.raw_value is not None and s.score > 0]
        active.sort(key=lambda s: s.score, reverse=True)
        detail = " | ".join(s.detail for s in active[:2]) if active else "全信号平静"
        return {"score": risk.composite_score, "level": risk.level, "detail": detail}
    except Exception as exc:
        print(f"[davis_nightly] overseas risk skipped — {type(exc).__name__}: {exc}")
        return None


# ── Output ─────────────────────────────────────────────────────────────


def save_json(payload: dict, trade_date_str: str) -> Path:
    """Persist full Top-N to dated file + overwrite latest pointer."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dated_path = OUTPUT_DIR / f"davis_top_{trade_date_str}.json"
    latest_path = OUTPUT_DIR / "davis_latest.json"

    dated_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return dated_path


# ── Feishu summary ─────────────────────────────────────────────────────


def _build_feishu_text(payload: dict, n: int) -> str:
    """Compact Top-N summary for Feishu (plain text, no markdown cards)."""
    rows = payload["scores"][:n]
    lines = [
        f"📊 Davis双击盘后筛选 Top{len(rows)} ({payload['trade_date']})",
        f"全市场 {payload['universe_size']} 只 → 打分 {payload['scored_count']} 只",
    ]

    # International resonance risk line (from overseas overlay).
    overseas = payload.get("overseas_risk")
    if overseas:
        emoji = {"极端": "🔴", "偏高": "🟠", "中等": "🟡", "偏低": "🟢"}.get(
            overseas.get("level", ""), ""
        )
        lines.append(
            f"🌍 国际共振风险 {overseas['score']:.0f}/100 {emoji}{overseas['level']} "
            f"— {overseas['detail']}"
        )

    lines += [
        f"{'排名':>3} {'代码':<10} {'名称':<8} {'总分':>5} {'估值':>5} {'景气':>5} {'困境':>5} 行业",
        "─" * 50,
    ]
    for r in rows:
        lines.append(
            f"{r['rank']:>3} {r['ts_code']:<10} {r['name']:<8} "
            f"{r['final_score']:>5.1f} {r['valuation_score']:>5.1f} "
            f"{r['prosperity_score']:>5.1f} {r['distress_score']:>5.1f} {r['industry']}"
        )
    lines.append("")
    lines.append(f"完整数据: studies/output/davis_top_{payload['trade_date']}.json")
    return "\n".join(lines)


def push_feishu(text: str) -> bool:
    """Push summary via existing notifier. Returns False if not configured/fails."""
    try:
        from stockhot.notification.feishu_bot import get_feishu_notifier

        notifier = get_feishu_notifier()
        if notifier is None:
            print("[davis_nightly] Feishu not configured — skipping push")
            return False
        asyncio.run(notifier.send_text(text))
        print("[davis_nightly] Feishu push ✓")
        return True
    except Exception as exc:
        print(f"[davis_nightly] Feishu push FAILED — {type(exc).__name__}: {exc}")
        return False


# ── Main ───────────────────────────────────────────────────────────────


def main() -> int:
    t0 = time.time()
    trade_date_str = date.today().strftime("%Y%m%d")
    print(f"[davis_nightly] === {trade_date_str} start, top_n={TOP_N} ===")

    # Lazy import so the bootstrap/logger config takes effect first, and so a
    # failed import surfaces as a clear error rather than a traceback mid-run.
    try:
        from davis_analyzer.pipeline import run_screening_pipeline
    except Exception as exc:
        print(f"[davis_nightly] FATAL — cannot import pipeline: {exc}")
        return 1

    # ── Run pipeline ──
    try:
        result = run_screening_pipeline(dry_run=False, top_n=TOP_N)
    except Exception as exc:
        print(f"[davis_nightly] Pipeline FAILED — {type(exc).__name__}: {exc}")
        return 2

    if not result.scores:
        print("[davis_nightly] Pipeline returned no scores — aborting")
        return 3

    elapsed_pipeline = time.time() - t0
    print(
        f"[davis_nightly] Pipeline done in {elapsed_pipeline/60:.1f}min — "
        f"{len(result.scores)} scored, top {len(result.scores[:TOP_N])} returned"
    )

    # ── Serialize & persist ──
    try:
        payload = _serialize_result(result, TOP_N)
        # Attach international resonance risk (never blocks the pipeline).
        payload["overseas_risk"] = _collect_overseas_risk(trade_date_str)
        dated_path = save_json(payload, trade_date_str)
        print(f"[davis_nightly] JSON saved → {dated_path}")
    except Exception as exc:
        print(f"[davis_nightly] JSON save FAILED — {type(exc).__name__}: {exc}")
        return 4

    # ── Feishu summary ──
    summary = _build_feishu_text(payload, FEISHU_SUMMARY_N)
    push_feishu(summary)

    total = time.time() - t0
    print(f"[davis_nightly] === done in {total/60:.1f}min ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
