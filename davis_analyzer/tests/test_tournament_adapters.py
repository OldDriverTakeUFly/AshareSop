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


# ═══════════════════════════════════════════════════════════════════
# universe 支持（--universe 工程修复：全市场单日打分 >280s 不可行）
# ═══════════════════════════════════════════════════════════════════


def _amount_conn():
    """内存库：22 个交易日 × 3 只股票，成交额 A > B > C（中位数可分序）."""
    import sqlite3
    from datetime import date, timedelta

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE daily_price (ts_code TEXT, trade_date TEXT, "
        "close REAL, amount REAL, PRIMARY KEY (ts_code, trade_date))"
    )
    d0 = date(2026, 7, 1)
    amounts = {"A.SZ": 100.0, "B.SZ": 50.0, "C.SZ": 10.0}
    for i in range(22):
        d = (d0 + timedelta(days=i)).strftime("%Y%m%d")
        for code, amt in amounts.items():
            conn.execute(
                "INSERT INTO daily_price VALUES (?,?,?,?)", (code, d, 1.0, amt)
            )
    conn.commit()
    return conn


def test_liquidity_universe_ranks_by_median_amount() -> None:
    from davis_analyzer.tournament.adapters import liquidity_universe

    conn = _amount_conn()
    try:
        assert liquidity_universe(2, conn) == ["A.SZ", "B.SZ"]
        assert liquidity_universe(5, conn) == ["A.SZ", "B.SZ", "C.SZ"]
    finally:
        conn.close()


def test_resolve_universe_spec_forms(tmp_path) -> None:
    from davis_analyzer.tournament.adapters import resolve_universe

    assert resolve_universe("all") is None  # 全缓存宇宙
    conn = _amount_conn()
    try:
        assert resolve_universe("u2", conn=conn) == ["A.SZ", "B.SZ"]
    finally:
        conn.close()
    f = tmp_path / "uni.txt"
    f.write_text("600519.SH\n000858.SZ\n", encoding="utf-8")
    assert resolve_universe(str(f)) == ["600519.SH", "000858.SZ"]


def test_default_participants_universe_passthrough() -> None:
    davis = [p for p in default_participants(universe=["600519.SH"])
             if isinstance(p, DavisPresetAdapter)]
    assert davis and all(p._universe == ["600519.SH"] for p in davis)
    # 不传 universe → 默认全缓存（回归不变）
    assert all(p._universe is None
               for p in default_participants() if isinstance(p, DavisPresetAdapter))


# ═══════════════════════════════════════════════════════════════════
# board-chasing TradeRecord → Trade 归一化（回归：曾用 code=/缺 amount、cost 直接 TypeError）
# ═══════════════════════════════════════════════════════════════════


def test_board_chasing_adapter_converts_trades(monkeypatch, mock_client) -> None:
    from davis_analyzer.limitup.engine import TradeRecord
    from davis_analyzer.tournament.adapters import BoardChasingAdapter

    class _FakeConn:
        def close(self) -> None:
            pass

    record = TradeRecord(
        ts_code="600000.SH", entry_date="20240105", entry_price=10.0, shares=1000,
        exit_date="20240108", exit_price=11.0, exit_reason="T+1_open",
        fill_scenario="pessimistic", gross_pnl=983.75, fees=16.25, ret_pct=9.8375,
    )
    nav = pd.DataFrame({
        "date": ["20240102", "20240103", "20240104", "20240105"],
        "equity": [1_000_000.0, 1_000_000.0, 1_000_500.0, 1_001_000.0],
        "cash": [1_000_000.0, 1_000_000.0, 999_500.0, 999_000.0],
    })
    monkeypatch.setattr(
        "davis_analyzer.tournament.adapters._limitup_db.connect", lambda: _FakeConn())
    monkeypatch.setattr(
        "davis_analyzer.tournament.adapters._build_ev",
        lambda conn, s, e: pd.DataFrame({"ts_code": ["600000.SH"]}))
    monkeypatch.setattr(
        "davis_analyzer.tournament.adapters._attach_pat", lambda ev, conn, s, e: ev)
    monkeypatch.setattr(
        "davis_analyzer.tournament.adapters._build_regime",
        lambda conn, s, e: pd.DataFrame())
    monkeypatch.setattr(
        "davis_analyzer.tournament.adapters._lap",
        lambda ev, preset, regime=None: pd.DataFrame({"ts_code": ["600000.SH"]}))
    monkeypatch.setattr(
        "davis_analyzer.tournament.adapters._limitup_db.read_daily_prices",
        lambda conn, codes, s, e: pd.DataFrame({"ts_code": ["600000.SH"]}))
    monkeypatch.setattr(
        "davis_analyzer.tournament.adapters._lrun",
        lambda cands, prices, preset, cfg, scenario: ([record], nav))

    run = BoardChasingAdapter().run_window(mock_client, date(2024, 1, 2), date(2024, 1, 31))

    assert run is not None
    assert len(run.trades) == 2
    buy, sell = run.trades
    assert buy.action == "BUY" and buy.ts_code == "600000.SH"
    assert buy.amount == pytest.approx(10_000.0)
    assert buy.cost == pytest.approx(10_000.0 * 2.5e-4)
    assert sell.action == "SELL"
    assert sell.amount == pytest.approx(11_000.0)
    assert sell.cost == pytest.approx(11_000.0 * 12.5e-4)
    # 单腿 cost 复算之和与引擎记账的往返 fees 一致（同式同参）
    assert buy.cost + sell.cost == pytest.approx(record.fees)
    assert len(run.equity_curve) == len(nav)
    assert run.equity_curve[-1].equity == pytest.approx(1_001_000.0)
