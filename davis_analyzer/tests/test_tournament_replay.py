"""replay 回放/meta 序列/前向曲线测试。"""

from __future__ import annotations

from datetime import date, timedelta

from davis_analyzer.backtest_report import PerformanceStats
from davis_analyzer.tournament.judge import WindowReport
from davis_analyzer.tournament.replay import replay


def _stats(sharpe: float) -> PerformanceStats:
    return PerformanceStats(
        total_return_pct=sharpe * 10, annualized_return_pct=sharpe * 10,
        sharpe_ratio=sharpe, max_drawdown_pct=0.0, win_rate_pct=60.0,
        turnover_per_rebalance=1.0, num_trades=20, num_rebalances=12,
        avg_holding_count=10.0, total_cost=100.0,
    )


def _reports(windows, sharpe_a: float, sharpe_b: float):
    out = {}
    for s, e in windows:
        out[(s, e)] = {
            "A": WindowReport("A", s, e, stats=_stats(sharpe_a), regime="risk_on", na_reason=None),
            "B": WindowReport("B", s, e, stats=_stats(sharpe_b), regime="risk_on", na_reason=None),
        }
    return out


def _windows(n: int = 6):
    d0 = date(2023, 1, 2)
    return [(d0 + timedelta(days=90 * i), d0 + timedelta(days=90 * i + 88)) for i in range(n)]


def test_replay_no_lookahead_and_rows() -> None:
    windows = _windows(6)
    result = replay(windows, _reports(windows, 1.5, 0.5))
    eval_points = sorted({r["as_of"] for r in result.meta_rows})
    # 首个评估点必须已有 ≥2 个已实现窗口（score_participant 需要）
    assert len(eval_points) >= 3
    # 前向曲线从 100 万起步、单调覆盖每个可分配窗口
    assert result.forward_rows[0]["replay_equity"] == 1_000_000.0
    assert len(result.forward_rows) >= 3
    # 分配权重逐点和为 1
    by_asof: dict[str, float] = {}
    for row in result.meta_rows:
        by_asof[row["as_of"]] = by_asof.get(row["as_of"], 0.0) + row["weight"]
    assert all(abs(v - 1.0) < 1e-6 for v in by_asof.values())


def test_replay_prefers_strong_participant() -> None:
    windows = _windows(6)
    result = replay(windows, _reports(windows, 2.0, 0.0))
    last = [r for r in result.meta_rows if r["participant"] == "A"]
    assert last[-1]["weight"] > 0.5  # 强者权重显著更高
