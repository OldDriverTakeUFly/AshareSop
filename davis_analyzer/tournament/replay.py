"""Walk-forward replay — meta series + forward simulated equity (§7 Phase 2)."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from davis_analyzer.tournament.allocator import allocate
from davis_analyzer.tournament.judge import WindowReport
from davis_analyzer.tournament.scorecard import score_participant


@dataclass
class ReplayResult:
    meta_rows: list[dict] = field(default_factory=list)
    forward_rows: list[dict] = field(default_factory=list)


def _window_return(report: WindowReport) -> float:
    """Total return of one window, derived from annualised return & length."""
    if report.stats is None:
        return 0.0
    days = max((report.end - report.start).days, 1)
    years = days / 365.0
    ann = report.stats.annualized_return_pct / 100.0
    return (1.0 + ann) ** years - 1.0


def replay(
    windows: list[tuple[date, date]],
    reports_by_window: dict[tuple[date, date], dict[str, WindowReport]],
) -> ReplayResult:
    """At each window end (as_of), score with past windows, allocate, and
    apply that allocation to the NEXT window's realised returns."""
    result = ReplayResult()
    equity = 1_000_000.0
    for i, (start, end) in enumerate(windows):
        past = windows[:i]
        if not past:
            # 首窗口无历史可评分：持有现金，仅记录前向曲线起点（100 万）
            result.forward_rows.append({
                "start": start.isoformat(), "end": end.isoformat(),
                "replay_equity": round(equity, 2),
            })
            continue
        # 决策时点可观测的最新 regime = 上一已实现窗口的 regime（防前视）
        observed = [
            r.regime for r in reports_by_window.get(past[-1], {}).values()
            if r.regime is not None
        ]
        current_regime = observed[0] if observed else ""  # regime 匹配由 WindowReport.regime 提供
        reports_by_p: dict[str, list[WindowReport]] = {}
        for w in past:
            for name, r in reports_by_window[w].items():
                reports_by_p.setdefault(name, []).append(r)
        scores = {n: score_participant(rs, current_regime) for n, rs in reports_by_p.items()}
        weights = allocate({n: s.total for n, s in scores.items()})
        for name, score in scores.items():
            # 决策时点 = 本窗口起点（仅使用此前已实现窗口，防前视）
            result.meta_rows.append({
                "as_of": start.isoformat(), "participant": name,
                "composite": score.total, "weight": round(weights[name], 6),
            })
        nxt = windows[i]  # weights decided from past windows, applied to this one
        realised = {
            name: _window_return(r)
            for name, r in reports_by_window[nxt].items()
        }
        port_ret = sum(weights.get(n, 0.0) * ret for n, ret in realised.items())
        equity *= 1.0 + port_ret
        result.forward_rows.append({
            "start": nxt[0].isoformat(), "end": nxt[1].isoformat(),
            "replay_equity": round(equity, 2),
        })
    return result


def export_replay(result: ReplayResult, out_dir: Path) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "meta_series.csv"
    forward_path = out_dir / "forward_curve.csv"
    with meta_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["as_of", "participant", "composite", "weight"])
        writer.writeheader()
        writer.writerows(result.meta_rows)
    with forward_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["start", "end", "replay_equity"])
        writer.writeheader()
        writer.writerows(result.forward_rows)
    return meta_path, forward_path
