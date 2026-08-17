"""Markdown tournament report rendering (Phase 1: display-only)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from davis_analyzer.config import TOURNAMENT_REPORTS_DIR
from davis_analyzer.tournament.judge import WindowReport
from davis_analyzer.tournament.scorecard import CompositeScore

HONESTY_NOTE = (
    "> 诚实边界：随机段验证能消解单一路径运气，但不能凭空制造新 regime；"
    "A 股历史独立 regime 情节有限，所有结论为必要非充分证据，不构成实盘依据。"
)


def _window_table(snapshot: dict[tuple[date, date], dict[str, WindowReport]]) -> str:
    lines = ["| 窗口 | 参赛者 | 夏普 | 最大回撤 | 年化 | regime | N/A 原因 |",
             "|---|---|---|---|---|---|---|"]
    for (start, end), reports in sorted(snapshot.items()):
        for name, r in sorted(reports.items()):
            if r.stats is None:
                lines.append(f"| {start}→{end} | {name} | - | - | - | {r.regime} | {r.na_reason} |")
            else:
                lines.append(
                    f"| {start}→{end} | {name} | {r.stats.sharpe_ratio} | "
                    f"{r.stats.max_drawdown_pct}% | {r.stats.annualized_return_pct}% | "
                    f"{r.regime} | - |"
                )
    return "\n".join(lines)


def _score_table(scores: dict[str, CompositeScore]) -> str:
    lines = ["| 参赛者 | 合成总分 | trailing | regime 匹配 | 有效窗口 |", "|---|---|---|---|---|"]
    for name, s in sorted(scores.items()):
        fmt = lambda v: "-" if v is None else f"{v:.3f}"  # noqa: E731
        lines.append(f"| {name} | {fmt(s.total)} | {fmt(s.trailing)} | {fmt(s.regime_match)} | {s.valid_windows} |")
    return "\n".join(lines)


def render_report(
    snapshot: dict[tuple[date, date], dict[str, WindowReport]],
    scores: dict[str, CompositeScore],
    current_regime: str,
) -> str:
    any_na = any(
        r.stats is None for reports in snapshot.values() for r in reports.values()
    )
    parts = [
        "# 策略锦标赛报告",
        f"\n当前 regime：**{current_regime}**",
        "\n## 表现矩阵\n", _window_table(snapshot),
        "\n## 评分\n", _score_table(scores),
    ]
    if any_na:
        parts.append("\n**参考性结论**：存在 N/A 参赛者（样本门槛未过），本期排名仅供参考。")
    parts.append(f"\n{HONESTY_NOTE}\n")
    return "\n".join(parts)


def write_report(text: str, run_date: date, reports_dir: Path | None = None) -> Path:
    out_dir = Path(reports_dir) if reports_dir else TOURNAMENT_REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_date.isoformat()}_tournament.md"
    path.write_text(text, encoding="utf-8")
    return path
