"""report 渲染与落盘测试。"""

from __future__ import annotations

from datetime import date

from davis_analyzer.tournament.judge import WindowReport
from davis_analyzer.tournament.report import HONESTY_NOTE, render_report, write_report
from davis_analyzer.tournament.scorecard import CompositeScore


def _snapshot():
    w = (date(2024, 1, 2), date(2024, 4, 8))
    return {w: {"davis_balanced": WindowReport(
        "davis_balanced", w[0], w[1], stats=None, regime="risk_on",
        na_reason="窗口成交笔数 5 < 10")}}


def test_render_contains_sections() -> None:
    text = render_report(
        _snapshot(),
        {"davis_balanced": CompositeScore(None, None, None, 0)},
        current_regime="risk_on",
    )
    assert "策略锦标赛报告" in text
    assert "表现矩阵" in text
    assert "N/A" in text
    assert "参考性结论" in text  # N/A 参赛者触发标注
    assert HONESTY_NOTE in text


def test_write_report(tmp_path) -> None:
    p = write_report("# t\n", date(2025, 6, 30), reports_dir=tmp_path)
    assert p.exists() and p.name == "2025-06-30_tournament.md"
