"""report 渲染与落盘测试。"""

from __future__ import annotations

from datetime import date

from davis_analyzer.backtest_report import PerformanceStats
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


def test_window_table_renders_real_stats() -> None:
    w = (date(2024, 1, 2), date(2024, 4, 8))
    stats = PerformanceStats(
        total_return_pct=18.0,
        annualized_return_pct=15.0,
        sharpe_ratio=1.234,
        max_drawdown_pct=-8.0,
        win_rate_pct=55.0,
        turnover_per_rebalance=0.3,
        num_trades=24,
        num_rebalances=8,
        avg_holding_count=5.0,
        total_cost=0.012,
    )
    snapshot = {w: {"davis_balanced": WindowReport(
        "davis_balanced", w[0], w[1], stats=stats, regime="risk_on",
        na_reason=None)}}
    text = render_report(
        snapshot,
        {"davis_balanced": CompositeScore(1.234, 1.234, None, 1)},
        current_regime="risk_on",
        allocation=None,
    )
    assert "1.234" in text
    assert "-8.0%" in text
    assert "15.0%" in text


def test_write_report(tmp_path) -> None:
    p = write_report("# t\n", date(2025, 6, 30), reports_dir=tmp_path)
    assert p.exists() and p.name == "2025-06-30_tournament.md"
