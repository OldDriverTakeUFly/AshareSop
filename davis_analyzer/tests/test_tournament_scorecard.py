"""scorecard 评分公式测试（冻结初值）。"""

from __future__ import annotations

import pytest

from davis_analyzer.backtest_report import PerformanceStats
from davis_analyzer.tournament.scorecard import (
    composite,
    regime_match_score,
    score_participant,
    trailing_score,
    window_performance,
)


def _stats(sharpe: float = 1.5, drawdown: float = -12.0) -> PerformanceStats:
    return PerformanceStats(
        total_return_pct=10.0, annualized_return_pct=10.0, sharpe_ratio=sharpe,
        max_drawdown_pct=drawdown, win_rate_pct=50.0, turnover_per_rebalance=1.0,
        num_trades=20, num_rebalances=12, avg_holding_count=10.0, total_cost=100.0,
    )


def test_window_performance_formula() -> None:
    # 夏普 1.5 − 0.1 × |−12| = 0.3
    assert window_performance(_stats()) == pytest.approx(0.3)


def test_trailing_half_life_weights() -> None:
    # [2.0, 1.0, 0.5, 0.25] 半衰期 2 加权（新近窗口权重最高）→ 0.7071
    assert trailing_score([2.0, 1.0, 0.5, 0.25]) == pytest.approx(0.7072, abs=1e-4)


def test_trailing_insufficient_windows_is_none() -> None:
    assert trailing_score([1.0]) is None
    assert trailing_score([]) is None


def test_regime_match_mean_of_matching_history() -> None:
    hist = {"risk_on": [1.0, 3.0], "risk_off": [0.0]}
    assert regime_match_score(hist, "risk_on") == pytest.approx(2.0)
    assert regime_match_score(hist, "unknown_regime") is None


def test_composite_weights() -> None:
    assert composite(1.2, 0.8) == pytest.approx(0.6 * 1.2 + 0.4 * 0.8)
    assert composite(None, 0.8) is None
    assert composite(1.2, None) is None


def test_score_participant_end_to_end() -> None:
    from datetime import date, timedelta
    from davis_analyzer.tournament.judge import WindowReport
    reports = [
        WindowReport("p", date(2024, 1, 1) + timedelta(days=63 * i),
                     date(2024, 3, 1) + timedelta(days=63 * i),
                     stats=_stats(sharpe=1.0 + 0.1 * i), regime="risk_on", na_reason=None)
        for i in range(3)
    ] + [WindowReport("p", date(2025, 1, 1), date(2025, 3, 1), stats=None,
                      regime="risk_off", na_reason="窗口成交笔数 5 < 10")]
    result = score_participant(reports, current_regime="risk_on")
    assert result.valid_windows == 3
    assert result.trailing is not None and result.regime_match is not None
    assert result.total is not None
