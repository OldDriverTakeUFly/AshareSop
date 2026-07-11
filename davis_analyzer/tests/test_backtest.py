"""Tests for the backtest engine — portfolio math, matching, and stats.

All tests use mock price data; no live API calls are made.  The tests
verify:
  * Portfolio cash/position accounting under buy/sell
  * Rebalance diff logic (sell-dropped → buy-new)
  * Trade cost (commission + stamp duty) correctness
  * Performance stats (return, drawdown, win rate, turnover)
  * Edge cases: suspended stock (no exec price), board lots, first build
"""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from davis_analyzer.backtest import (
    BacktestConfig,
    BacktestResult,
    EquitySnapshot,
    Portfolio,
    Position,
    _trade_cost,
)
from davis_analyzer.backtest_report import (
    PerformanceStats,
    compute_performance,
    export_equity_curve,
    export_trades,
    format_stats_report,
)


# ──────────────────────────── _trade_cost ────────────────────────────


class TestTradeCost:
    def test_buy_only_commission_no_stamp(self):
        # 100k gross, 2.5bps commission → 25.0; no stamp on buy.
        cost = _trade_cost(100_000.0, commission_bps=2.5, stamp_tax_bps=10.0, is_sell=False)
        assert cost == pytest.approx(25.0)

    def test_sell_commission_plus_stamp(self):
        # 100k gross, 2.5bps commission + 10bps stamp → 25 + 100 = 125.
        cost = _trade_cost(100_000.0, commission_bps=2.5, stamp_tax_bps=10.0, is_sell=True)
        assert cost == pytest.approx(125.0)

    def test_zero_gross(self):
        assert _trade_cost(0.0, 2.5, 10.0, is_sell=False) == 0.0


# ──────────────────────────── Portfolio basics ────────────────────────────


class TestPortfolioInit:
    def test_initial_cash_and_equity(self):
        p = Portfolio(1_000_000)
        assert p.cash == 1_000_000
        assert p.positions == {}
        assert p.market_value({}) == 1_000_000

    def test_market_value_with_positions(self):
        p = Portfolio(1_000_000)
        p.positions["A.SZ"] = Position(shares=1000, cost_basis=10.0)
        p.positions["B.SH"] = Position(shares=500, cost_basis=20.0)
        mv = p.market_value({"A.SZ": 12.0, "B.SH": 18.0})
        # 1_000_000 + 1000*12 + 500*18 = 1_021_000
        assert mv == pytest.approx(1_021_000)


# ──────────────────────────── Rebalance matching ────────────────────────────


class TestRebalance:
    """The core matching logic: given a target set, verify sells then buys."""

    def test_first_build_buys_top_n_equal_weight(self):
        """Empty portfolio → rebalance into 2 stocks equal-weight."""
        p = Portfolio(100_000)
        prices = {"A.SZ": 10.0, "B.SH": 20.0}
        trades = p.rebalance(
            target_codes=["A.SZ", "B.SH"],
            exec_prices=prices,
            signal_date=date(2026, 1, 5),
            exec_date=date(2026, 1, 6),
            commission_bps=2.5,
            stamp_tax_bps=10.0,
        )
        # Should have bought 2 stocks.
        buys = [t for t in trades if t.action == "BUY"]
        assert len(buys) == 2
        # Equal-weight target: 100k / 2 = 50k per slot.
        # A at 10 → 50k/10 = 5000 shares (board lot aligned).
        a_trade = next(t for t in buys if t.ts_code == "A.SZ")
        assert a_trade.shares == 5000
        assert a_trade.price == 10.0
        # B at 20 → target 2500 shares, but A's commission was deducted first,
        # so B gets 2400 (the affordable amount after cash shortfall check).
        b_trade = next(t for t in buys if t.ts_code == "B.SH")
        assert b_trade.shares == 2400
        assert b_trade.shares % 100 == 0  # board lot aligned

    def test_sell_then_buy_on_rotation(self):
        """Hold A+B → target B+C → should sell A, keep B, buy C."""
        p = Portfolio(100_000)
        # Pre-populate as if we already hold A and B.
        p.positions["A.SZ"] = Position(shares=3000, cost_basis=10.0)
        p.positions["B.SH"] = Position(shares=2000, cost_basis=20.0)
        # Simulate that cash was already spent: 3000*10 + 2000*20 = 70k → cash = 30k.
        p.cash = 30_000

        prices = {"A.SZ": 11.0, "B.SH": 20.0, "C.SZ": 5.0}
        trades = p.rebalance(
            target_codes=["B.SH", "C.SZ"],
            exec_prices=prices,
            signal_date=date(2026, 1, 5),
            exec_date=date(2026, 1, 6),
            commission_bps=2.5,
            stamp_tax_bps=10.0,
        )
        sells = [t for t in trades if t.action == "SELL"]
        buys = [t for t in trades if t.action == "BUY"]
        # A dropped out → sold. B stays. C is new → bought.
        assert len(sells) == 1
        assert sells[0].ts_code == "A.SZ"
        assert sells[0].shares == 3000
        assert "A.SZ" not in p.positions
        assert "B.SH" in p.positions  # B retained
        assert "C.SZ" in p.positions  # C newly bought
        assert len(buys) == 1

    def test_suspended_stock_cannot_be_sold(self):
        """Stock with no exec price (halted) should be carried forward."""
        p = Portfolio(100_000)
        p.positions["HALT.SZ"] = Position(shares=1000, cost_basis=10.0)
        p.cash = 90_000
        # No price for HALT.SZ → cannot sell.
        prices = {"NEW.SZ": 20.0}
        trades = p.rebalance(
            target_codes=["NEW.SZ"],
            exec_prices=prices,
            signal_date=date(2026, 1, 5),
            exec_date=date(2026, 1, 6),
            commission_bps=2.5,
            stamp_tax_bps=10.0,
        )
        # HALT not sold (no price), carried forward.
        assert "HALT.SZ" in p.positions
        sells = [t for t in trades if t.action == "SELL"]
        assert len(sells) == 0

    def test_board_lot_alignment(self):
        """Shares must be multiples of 100 (A-share board lot)."""
        p = Portfolio(100_000)
        # Single target: per_slot = 100k / 1 = 100k.
        # Price 33.0: 100k/33 = 3030.3 → floor to 3000 (30 lots).
        prices = {"X.SZ": 33.0}
        trades = p.rebalance(
            target_codes=["X.SZ"],
            exec_prices=prices,
            signal_date=date(2026, 1, 5),
            exec_date=date(2026, 1, 6),
            commission_bps=2.5,
            stamp_tax_bps=10.0,
        )
        buys = [t for t in trades if t.action == "BUY"]
        assert len(buys) == 1
        assert buys[0].shares % 100 == 0
        assert buys[0].shares == 3000

    def test_no_redundant_trades_when_target_unchanged(self):
        """If target set == held set, no trades should fire."""
        p = Portfolio(100_000)
        p.positions["A.SZ"] = Position(shares=5000, cost_basis=10.0)
        p.positions["B.SH"] = Position(shares=2500, cost_basis=20.0)
        p.cash = 0
        prices = {"A.SZ": 10.0, "B.SH": 20.0}
        trades = p.rebalance(
            target_codes=["A.SZ", "B.SH"],
            exec_prices=prices,
            signal_date=date(2026, 1, 5),
            exec_date=date(2026, 1, 6),
            commission_bps=2.5,
            stamp_tax_bps=10.0,
        )
        assert len(trades) == 0


# ──────────────────────────── Performance stats ────────────────────────────


class TestPerformanceStats:
    def _snapshots(self, equities: list[float], start: str = "2026-01-05") -> list[EquitySnapshot]:
        dates = pd.bdate_range(start=start, periods=len(equities))
        return [
            EquitySnapshot(date=d.date(), equity=e, cash=0.0, positions_value=e)
            for d, e in zip(dates, equities)
        ]

    def test_total_return_positive(self):
        result = BacktestResult(
            config=BacktestConfig(
                start_date=date(2026, 1, 5),
                end_date=date(2026, 3, 5),
                initial_capital=100_000,
            ),
            equity_curve=self._snapshots([100_000, 110_000]),
        )
        stats = compute_performance(result)
        assert stats.total_return_pct == pytest.approx(10.0, abs=0.01)

    def test_total_return_negative(self):
        result = BacktestResult(
            config=BacktestConfig(
                start_date=date(2026, 1, 5),
                end_date=date(2026, 3, 5),
                initial_capital=100_000,
            ),
            equity_curve=self._snapshots([100_000, 95_000]),
        )
        stats = compute_performance(result)
        assert stats.total_return_pct == pytest.approx(-5.0, abs=0.01)

    def test_max_drawdown(self):
        # 100k → 120k → 90k → 110k → max DD = (90-120)/120 = -25%.
        snaps = self._snapshots([100_000, 120_000, 90_000, 110_000])
        result = BacktestResult(
            config=BacktestConfig(
                start_date=date(2026, 1, 5),
                end_date=date(2026, 3, 5),
                initial_capital=100_000,
            ),
            equity_curve=snaps,
        )
        stats = compute_performance(result)
        assert stats.max_drawdown_pct == pytest.approx(-25.0, abs=0.5)

    def test_win_rate_from_trades(self):
        """Buy at 10, sell at 12 (win); buy at 20, sell at 18 (loss)."""
        from davis_analyzer.backtest import Trade

        trades = [
            Trade(date(2026, 1, 5), date(2026, 1, 6), "A.SZ", "BUY", 10.0, 100, 1000, 0),
            Trade(date(2026, 2, 5), date(2026, 2, 6), "A.SZ", "SELL", 12.0, 100, 1200, 0),
            Trade(date(2026, 1, 5), date(2026, 1, 6), "B.SH", "BUY", 20.0, 50, 1000, 0),
            Trade(date(2026, 2, 5), date(2026, 2, 6), "B.SH", "SELL", 18.0, 50, 900, 0),
        ]
        result = BacktestResult(
            config=BacktestConfig(
                start_date=date(2026, 1, 5),
                end_date=date(2026, 3, 5),
                initial_capital=100_000,
            ),
            equity_curve=self._snapshots([100_000, 100_000]),
            trades=trades,
        )
        stats = compute_performance(result)
        # 1 win / 2 round-trips = 50%.
        assert stats.win_rate_pct == pytest.approx(50.0, abs=0.1)

    def test_turnover_calculation(self):
        """4 trades / 2 rebalances → turnover = 4/2/2 = 1.0."""
        from davis_analyzer.backtest import Trade

        trades = [
            Trade(date(2026, 1, 5), date(2026, 1, 6), "A.SZ", "BUY", 10.0, 100, 1000, 0),
            Trade(date(2026, 2, 5), date(2026, 2, 6), "A.SZ", "SELL", 12.0, 100, 1200, 0),
            Trade(date(2026, 2, 5), date(2026, 2, 6), "B.SH", "BUY", 20.0, 50, 1000, 0),
            Trade(date(2026, 3, 5), date(2026, 3, 6), "B.SH", "SELL", 18.0, 50, 900, 0),
        ]
        result = BacktestResult(
            config=BacktestConfig(
                start_date=date(2026, 1, 5),
                end_date=date(2026, 3, 5),
                initial_capital=100_000,
            ),
            equity_curve=self._snapshots([100_000, 100_000]),
            trades=trades,
            rebalance_dates=[date(2026, 1, 5), date(2026, 2, 5)],
        )
        stats = compute_performance(result)
        assert stats.turnover_per_rebalance == pytest.approx(1.0, abs=0.01)


# ──────────────────────────── CSV export ────────────────────────────


class TestCsvExport:
    def test_export_trades_writes_file(self, tmp_path: Path):
        from davis_analyzer.backtest import Trade

        trades = [
            Trade(date(2026, 1, 5), date(2026, 1, 6), "A.SZ", "BUY", 10.0, 100, 1000, 2.5),
        ]
        result = BacktestResult(
            config=BacktestConfig(
                start_date=date(2026, 1, 5),
                end_date=date(2026, 3, 5),
                initial_capital=100_000,
            ),
            trades=trades,
        )
        out = export_trades(result, tmp_path / "trades.csv")
        assert out.exists()
        df = pd.read_csv(out)
        assert len(df) == 1
        assert df.iloc[0]["ts_code"] == "A.SZ"
        assert df.iloc[0]["action"] == "BUY"

    def test_export_equity_curve_writes_file(self, tmp_path: Path):
        snaps = [
            EquitySnapshot(date(2026, 1, 5), 100_000, 50_000, 50_000),
            EquitySnapshot(date(2026, 1, 6), 105_000, 50_000, 55_000),
        ]
        result = BacktestResult(
            config=BacktestConfig(
                start_date=date(2026, 1, 5),
                end_date=date(2026, 3, 5),
                initial_capital=100_000,
            ),
            equity_curve=snaps,
        )
        out = export_equity_curve(result, tmp_path / "equity.csv")
        assert out.exists()
        df = pd.read_csv(out)
        assert len(df) == 2
        assert df.iloc[0]["equity"] == 100_000.0


# ──────────────────────────── Report formatting ────────────────────────────


class TestFormatReport:
    def test_report_contains_key_metrics(self):
        stats = PerformanceStats(
            total_return_pct=15.5,
            annualized_return_pct=30.0,
            sharpe_ratio=1.234,
            max_drawdown_pct=-8.0,
            win_rate_pct=60.0,
            turnover_per_rebalance=1.5,
            num_trades=20,
            num_rebalances=10,
            avg_holding_count=5.0,
            total_cost=500.0,
        )
        report = format_stats_report(stats, "test-config")
        assert "15.50%" in report
        assert "1.234" in report
        assert "test-config" in report
