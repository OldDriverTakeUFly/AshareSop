"""adapters 归一化层测试。"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from davis_analyzer.backtest_report import PerformanceStats
from davis_analyzer.tournament.adapters import (
    DavisPresetAdapter,
    IndexBenchmarkAdapter,
    RunResult,
    default_participants,
    stats_from_run,
)


def _daily_df(days: int = 50, base: float = 10.0) -> pd.DataFrame:
    d0 = date(2024, 1, 2)
    return pd.DataFrame({
        "trade_date": [(d0 + timedelta(days=i)).strftime("%Y%m%d") for i in range(days)],
        "close": [base + i * 0.1 for i in range(days)],
    })


def test_index_benchmark_builds_curve(mock_client) -> None:
    mock_client.get_daily_prices.return_value = _daily_df()
    adapter = IndexBenchmarkAdapter()
    run = adapter.run_window(mock_client, date(2024, 1, 2), date(2024, 3, 15))
    assert run is not None
    assert len(run.equity_curve) == 50
    assert run.trades == []
    assert run.assumptions["cost_model"] == "buy_and_hold_no_cost"


def test_index_benchmark_none_when_no_data(mock_client) -> None:
    mock_client.get_daily_prices.return_value = pd.DataFrame()
    assert IndexBenchmarkAdapter().run_window(mock_client, date(2024, 1, 2), date(2024, 3, 15)) is None


def test_stats_from_run_roundtrip() -> None:
    from davis_analyzer.backtest import EquitySnapshot
    curve = [EquitySnapshot(date=date(2024, 1, i + 1), equity=1_000_000.0 * (1 + 0.001 * i),
                            cash=0.0, positions_value=1_000_000.0) for i in range(30)]
    stats = stats_from_run(RunResult(curve, [], {}), date(2024, 1, 1), date(2024, 1, 30))
    assert isinstance(stats, PerformanceStats)
    assert stats.total_return_pct == pytest.approx((1.029 * 1_000_000 / 1_000_000 - 1) * 100, abs=0.5)


def test_davis_adapter_maps_params(monkeypatch, mock_client) -> None:
    captured: dict = {}
    def fake_run_backtest(cfg, client):
        captured["cfg"] = cfg
        from davis_analyzer.backtest import BacktestResult, EquitySnapshot
        curve = [EquitySnapshot(date=date(2024, 1, 2) + timedelta(days=i), equity=1_000_000.0,
                                cash=1_000_000.0, positions_value=0.0) for i in range(45)]
        return BacktestResult(config=cfg, equity_curve=curve)

    monkeypatch.setattr("davis_analyzer.tournament.adapters.run_backtest", fake_run_backtest)
    adapter = DavisPresetAdapter("davis_momentum_tilt", {"momentum_weight": 0.45, "top_n": 15})
    run = adapter.run_window(mock_client, date(2024, 1, 2), date(2024, 4, 1))
    assert run is not None
    fc = captured["cfg"].factor_config
    assert fc.momentum_weight == 0.45
    assert captured["cfg"].top_n == 15


def test_davis_adapter_rejects_undeclared_param(monkeypatch, mock_client) -> None:
    adapter = DavisPresetAdapter("davis_balanced", {})
    with pytest.raises(KeyError):
        adapter.run_window(mock_client, date(2024, 1, 2), date(2024, 4, 1),
                           params={"hold_days": 3})


def test_davis_adapter_none_on_empty_curve(monkeypatch, mock_client) -> None:
    from davis_analyzer.backtest import BacktestConfig, BacktestResult
    monkeypatch.setattr(
        "davis_analyzer.tournament.adapters.run_backtest",
        lambda cfg, client: BacktestResult(config=BacktestConfig(
            start_date=cfg.start_date, end_date=cfg.end_date)),
    )
    adapter = DavisPresetAdapter("davis_balanced", {})
    assert adapter.run_window(mock_client, date(2024, 1, 2), date(2024, 4, 1)) is None


def test_default_participants_registry() -> None:
    names = [a.name for a in default_participants()]
    assert "davis_balanced" in names and "davis_valuation_tilt" in names
    assert "benchmark_sse" in names
