"""replay 回放/meta 序列/前向曲线测试。"""

from __future__ import annotations

from datetime import date, timedelta

from davis_analyzer.backtest_report import PerformanceStats
from davis_analyzer.tournament.judge import WindowReport
from davis_analyzer.tournament.replay import _window_return, export_replay, replay


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


def test_window_return_is_exact_total_return() -> None:
    # I1：直接取引擎 total_return_pct，不再按 365 日历日反年化推导
    stats = PerformanceStats(
        total_return_pct=18.0, annualized_return_pct=15.0, sharpe_ratio=1.0,
        max_drawdown_pct=-8.0, win_rate_pct=55.0, turnover_per_rebalance=0.3,
        num_trades=24, num_rebalances=8, avg_holding_count=5.0, total_cost=0.01,
    )
    long_window = (date(2023, 1, 2), date(2024, 1, 2))  # 365 日历日
    short_window = (date(2023, 1, 2), date(2023, 4, 3))  # 91 日历日
    for w in (long_window, short_window):
        report = WindowReport("A", w[0], w[1], stats=stats, regime="risk_on", na_reason=None)
        assert _window_return(report) == 0.18  # 与窗口长度、年化值无关
    na = WindowReport("A", *long_window, stats=None, regime=None, na_reason="数据不足")
    assert _window_return(na) == 0.0


def test_export_replay_filenames_carry_date_prefix(tmp_path) -> None:
    # M4：CSV 文件名带 {YYYY-MM-DD}_ 前缀；缺省 run_date 用当天
    result = replay(_windows(6), _reports(_windows(6), 1.5, 0.5))
    meta_path, forward_path = export_replay(
        result, tmp_path, run_date=date(2025, 6, 30),
    )
    assert meta_path.name == "2025-06-30_meta_series.csv"
    assert forward_path.name == "2025-06-30_forward_curve.csv"
    assert meta_path.exists() and forward_path.exists()
    header = meta_path.read_text(encoding="utf-8").splitlines()[0]
    assert header == "as_of,participant,composite,weight"
