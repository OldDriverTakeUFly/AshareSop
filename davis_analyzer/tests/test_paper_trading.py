"""Tests for davis_analyzer.paper_trading — account, strategy, executor."""

import os
import tempfile

import pytest

# Ensure PROJECT_ROOT before any stockhot import
os.environ.setdefault("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@pytest.fixture(scope="module")
def temp_db():
    """Use a temporary stockhot.db for paper-trading tests."""
    tmpdir = tempfile.mkdtemp()
    tmp_db = os.path.join(tmpdir, "stockhot.db")
    # Monkey-patch DB_PATH before init
    from stockhot.core import config as stockhot_config

    old_path = stockhot_config.DB_PATH
    stockhot_config.DB_PATH = type(old_path)(tmp_db)
    # Also patch the import in database module
    from stockhot.storage import database as db_module

    db_module.DB_PATH = stockhot_config.DB_PATH
    db_module.init_database()
    yield tmp_db
    stockhot_config.DB_PATH = old_path
    db_module.DB_PATH = old_path


# ── Account tests ──────────────────────────────────────────────────────


class TestPaperAccount:
    def test_create_and_load(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount

        account = PaperAccount.create("test_v1", "davis_double", 1_000_000)
        assert account.name == "test_v1"
        assert account.cash == 1_000_000
        assert account.initial_capital == 1_000_000

        # Reload
        loaded = PaperAccount.load("test_v1")
        assert loaded.account_id == account.account_id
        loaded.close()

    def test_create_duplicate_raises(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount

        PaperAccount.create("dup_test", "davis_double", 500_000)
        with pytest.raises(ValueError, match="already exists"):
            PaperAccount.create("dup_test", "davis_double", 500_000)

    def test_buy_creates_position(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount

        account = PaperAccount.create("buy_test", "davis_double", 1_000_000)
        trade = account.buy("000001.SZ", "平安银行", 1000, 10.0, "20260101", signal_reason="test")
        assert trade is not None
        assert trade.action == "BUY"
        assert trade.shares == 1000
        assert trade.price == 10.0

        positions = account.get_positions()
        assert len(positions) == 1
        assert positions[0].ts_code == "000001.SZ"
        assert positions[0].shares == 1000
        # Cash reduced (1000 * 10 + commission)
        assert account.cash < 1_000_000
        account.close()

    def test_buy_board_lot_enforced(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount

        account = PaperAccount.create("lot_test", "davis_double", 1_000_000)
        # Request 150 shares → should round to 100
        trade = account.buy("600000.SH", "浦发银行", 150, 10.0, "20260101")
        assert trade is not None
        assert trade.shares == 100  # rounded to board lot
        account.close()

    def test_sell_reduces_position(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount

        account = PaperAccount.create("sell_test", "davis_double", 1_000_000)
        account.buy("000002.SZ", "万科A", 1000, 20.0, "20260101")

        cash_before = account.cash
        trade = account.sell("000002.SZ", "万科A", 500, 25.0, "20260102", signal_reason="止盈")
        assert trade is not None
        assert trade.action == "SELL"
        assert trade.shares == 500
        assert trade.price == 25.0

        # Cash increased (500 * 25 - commission - stamp)
        assert account.cash > cash_before

        positions = account.get_positions()
        assert len(positions) == 1
        assert positions[0].shares == 500
        account.close()

    def test_sell_all_closes_position(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount

        account = PaperAccount.create("sellall_test", "davis_double", 1_000_000)
        account.buy("000003.SZ", "测试C", 500, 15.0, "20260101")
        trade = account.sell_all("000003.SZ", "测试C", 16.0, "20260102", signal_reason="清仓")
        assert trade is not None
        positions = account.get_positions()
        assert len(positions) == 0
        account.close()

    def test_market_value(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount

        account = PaperAccount.create("mv_test", "davis_double", 1_000_000)
        account.buy("000004.SZ", "测试D", 1000, 10.0, "20260101")
        # Cash ≈ 990,000 (1000*10=10000 minus commission)
        mv = account.market_value({"000004.SZ": 12.0})
        # Should be cash + 1000*12 = ~990k + 12k
        assert mv > 1_000_000  # position gained value
        account.close()

    def test_record_nav(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount

        account = PaperAccount.create("nav_test", "davis_double", 1_000_000)
        account.buy("000005.SZ", "测试E", 500, 20.0, "20260101")
        nav = account.record_nav("20260101", {"000005.SZ": 22.0})
        assert nav.trade_date == "20260101"
        assert nav.total_equity > 0

        # Second day — daily return should be calculable
        nav2 = account.record_nav("20260102", {"000005.SZ": 21.0})
        assert nav2.daily_return is not None
        account.close()

    def test_has_run_on(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount

        account = PaperAccount.create("hasrun_test", "davis_double", 1_000_000)
        assert not account.has_run_on("20260101")
        account.record_nav("20260101", {})
        assert account.has_run_on("20260101")
        account.close()


# ── Strategy tests ─────────────────────────────────────────────────────


class TestDavisDoubleStrategy:
    def test_rebalance_day_buys_top_n(self):
        from davis_analyzer.paper_trading.strategy import (
            DavisDoubleStrategy,
            MarketSnapshot,
        )

        strategy = DavisDoubleStrategy(top_n=3, frequency=1, min_score=50.0)
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"A.SZ": 10.0, "B.SZ": 20.0, "C.SZ": 30.0},
            davis_scores={
                "A.SZ": {"final_score": 80, "name": "A公司"},
                "B.SZ": {"final_score": 70, "name": "B公司"},
                "C.SZ": {"final_score": 60, "name": "C公司"},
                "D.SZ": {"final_score": 40, "name": "D公司"},  # below min_score
            },
            market_regime="bull",
        )
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 3  # top 3 above min_score
        assert "D.SZ" not in [s.ts_code for s in buys]

    def test_non_rebalance_day_holds(self):
        from davis_analyzer.paper_trading.strategy import (
            DavisDoubleStrategy,
            MarketSnapshot,
        )
        from davis_analyzer.paper_trading.account import Position

        strategy = DavisDoubleStrategy(top_n=3, frequency=5, min_score=50.0)
        positions = [Position("A.SZ", "A公司", 100, 10.0, "20260101")]
        snapshot = MarketSnapshot(trade_date="20260102", prices={"A.SZ": 10.0}, market_regime="bull")

        # Day 2 of 5 → not rebalance day
        signals = strategy.evaluate(positions, snapshot, 1_000_000)
        assert all(s.action == "HOLD" for s in signals)

    def test_sells_dropped_positions(self):
        from davis_analyzer.paper_trading.strategy import (
            DavisDoubleStrategy,
            MarketSnapshot,
        )
        from davis_analyzer.paper_trading.account import Position

        strategy = DavisDoubleStrategy(top_n=2, frequency=1, min_score=50.0)
        positions = [
            Position("OLD.SZ", "旧公司", 100, 10.0, "20260101"),
        ]
        snapshot = MarketSnapshot(
            trade_date="20260102",
            prices={"NEW.SZ": 20.0, "OLD.SZ": 10.0},
            davis_scores={"NEW.SZ": {"final_score": 80, "name": "新公司"}},
            market_regime="bull",
        )
        signals = strategy.evaluate(positions, snapshot, 1_000_000)
        sells = [s for s in signals if s.action == "SELL"]
        buys = [s for s in signals if s.action == "BUY"]
        assert any(s.ts_code == "OLD.SZ" for s in sells)
        assert any(s.ts_code == "NEW.SZ" for s in buys)


class TestFactorThresholdStrategy:
    def test_buy_signal_strong_momentum_holder(self):
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy,
            MarketSnapshot,
        )

        strategy = FactorThresholdStrategy(buy_momentum=70, buy_holder_min=40)
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"X.SZ": 10.0},
            factor_scores={"X.SZ": {"momentum": 85, "holder": 60}},
            market_regime="bull",
        )
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 1
        assert buys[0].ts_code == "X.SZ"

    def test_no_buy_weak_holder(self):
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy,
            MarketSnapshot,
        )

        strategy = FactorThresholdStrategy(buy_momentum=70, buy_holder_min=40)
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"X.SZ": 10.0},
            factor_scores={"X.SZ": {"momentum": 85, "holder": 20}},  # weak holder
            market_regime="bull",
        )
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 0

    def test_sell_signal_momentum_collapse(self):
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy,
            MarketSnapshot,
        )
        from davis_analyzer.paper_trading.account import Position

        strategy = FactorThresholdStrategy(sell_momentum=40)
        positions = [Position("Y.SZ", "Y公司", 100, 10.0, "20260101")]
        snapshot = MarketSnapshot(
            trade_date="20260102",
            prices={"Y.SZ": 8.0},
            factor_scores={"Y.SZ": {"momentum": 25, "holder": 50, "holder_trend": "集中"}},
            market_regime="bull",
        )
        signals = strategy.evaluate(positions, snapshot, 1_000_000)
        sells = [s for s in signals if s.action == "SELL"]
        assert len(sells) == 1

    def test_sell_signal_holder_distribution(self):
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy,
            MarketSnapshot,
        )
        from davis_analyzer.paper_trading.account import Position

        strategy = FactorThresholdStrategy()
        positions = [Position("Z.SZ", "Z公司", 100, 10.0, "20260101")]
        snapshot = MarketSnapshot(
            trade_date="20260102",
            prices={"Z.SZ": 12.0},
            factor_scores={"Z.SZ": {"momentum": 60, "holder": 0, "holder_trend": "分散"}},
            market_regime="bull",
        )
        signals = strategy.evaluate(positions, snapshot, 1_000_000)
        sells = [s for s in signals if s.action == "SELL"]
        assert len(sells) == 1

    def test_respects_max_positions(self):
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy,
            MarketSnapshot,
        )
        from davis_analyzer.paper_trading.account import Position

        strategy = FactorThresholdStrategy(max_positions=2)
        # A.SZ is held AND qualified (in factor_scores)
        positions = [Position("A.SZ", "A", 100, 10, "20260101")]
        snapshot = MarketSnapshot(
            trade_date="20260102",
            prices={"A.SZ": 10.0, "B.SZ": 10.0, "C.SZ": 10.0, "D.SZ": 10.0},
            factor_scores={
                "A.SZ": {"momentum": 85, "holder": 55},  # held, qualified
                "B.SZ": {"momentum": 80, "holder": 60},
                "C.SZ": {"momentum": 75, "holder": 50},
                "D.SZ": {"momentum": 90, "holder": 70},
            },
            market_regime="bull",  # full max_positions in bull market
        )
        signals = strategy.evaluate(positions, snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        # effective_max=2, A.SZ held+qualified (ranked #2), D.SZ is #1.
        # Top-2 = [D.SZ(90), A.SZ(85)] → A.SZ stays, D.SZ bought = 1 buy
        assert len(buys) == 1
        assert buys[0].ts_code == "D.SZ"  # highest momentum


# ── Smart Strategy tests (market gate + dynamic stop + sector rotation) ─


class TestMarketRegimeGate:
    def test_bear_market_blocks_buys(self):
        """In bear market, no buys even if momentum/holder are strong."""
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy,
            MarketSnapshot,
        )

        strategy = FactorThresholdStrategy(buy_momentum=65, buy_holder_min=35)
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"X.SZ": 10.0},
            factor_scores={"X.SZ": {"momentum": 90, "holder": 80}},
            market_regime="bear",
        )
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 0  # bear market: no new buys

    def test_bull_market_allows_buys(self):
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy,
            MarketSnapshot,
        )

        strategy = FactorThresholdStrategy(buy_momentum=65, buy_holder_min=35)
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"X.SZ": 10.0},
            factor_scores={"X.SZ": {"momentum": 90, "holder": 80}},
            market_regime="bull",
        )
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 1

    def test_mixed_market_halves_positions(self):
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy,
            MarketSnapshot,
        )

        strategy = FactorThresholdStrategy(max_positions=10, buy_momentum=65, buy_holder_min=35)
        # 8 candidates all qualify
        factor_scores = {f"S{i}.SZ": {"momentum": 80, "holder": 60} for i in range(8)}
        prices = {f"S{i}.SZ": 10.0 for i in range(8)}
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices=prices,
            factor_scores=factor_scores,
            market_regime="mixed",  # max_positions halved: 10→5
        )
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 5  # mixed: only 5 slots (10/2)


class TestSectorRotation:
    def test_buy_skips_declining_sector(self):
        """Buy candidates in declining sectors are filtered out."""
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy,
            MarketSnapshot,
        )

        strategy = FactorThresholdStrategy(buy_momentum=65, buy_holder_min=35)
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"UP.SZ": 10.0, "DOWN.SZ": 10.0},
            factor_scores={
                "UP.SZ": {"momentum": 80, "holder": 60},
                "DOWN.SZ": {"momentum": 85, "holder": 65},  # stronger but...
            },
            market_regime="bull",
            industries={"UP.SZ": "半导体", "DOWN.SZ": "房地产"},
            industry_trend={"半导体": "up", "房地产": "down"},
        )
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 1
        assert buys[0].ts_code == "UP.SZ"  # only the up-trending sector

    def test_sell_on_sector_decline(self):
        """Holding in a declining sector triggers proactive sell."""
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy,
            MarketSnapshot,
        )
        from davis_analyzer.paper_trading.account import Position

        strategy = FactorThresholdStrategy()
        positions = [Position("HELD.SZ", "持仓股", 100, 10.0, "20260101")]
        snapshot = MarketSnapshot(
            trade_date="20260102",
            prices={"HELD.SZ": 11.0},
            factor_scores={"HELD.SZ": {"momentum": 60, "holder": 50, "holder_trend": "集中"}},
            market_regime="bull",
            industries={"HELD.SZ": "化工"},
            industry_trend={"化工": "down"},
        )
        signals = strategy.evaluate(positions, snapshot, 1_000_000)
        sells = [s for s in signals if s.action == "SELL"]
        assert len(sells) == 1
        assert "切换赛道" in sells[0].signal_reason


class TestDynamicRiskThresholds:
    def test_bear_down_sector_tight_stop(self, temp_db):
        """Bear market + declining sector → 7% stop (tightest)."""
        from davis_analyzer.paper_trading.account import PaperAccount
        from davis_analyzer.paper_trading.strategy import create_strategy
        from davis_analyzer.paper_trading.executor import DailyExecutor
        from unittest.mock import patch

        account = PaperAccount.create("risk_bear_down", "factor_threshold", 100_000)
        account.buy("000050.SZ", "测试", 1000, 10.0, "20260101")
        strategy = create_strategy("factor_threshold", account.config)
        executor = DailyExecutor(account, strategy)

        # Price at 9.2 → loss of 8% → should trigger 7% stop in bear+down
        with patch("davis_analyzer.paper_trading.executor._get_close_prices",
                   return_value={"000050.SZ": 9.2}):
            risk_signals = executor._check_risk_signals(
                account.get_positions(), {"000050.SZ": 9.2}, "20260102",
                market_regime="bear",
                industries={"000050.SZ": "化工"},
                industry_trend={"化工": "down"},
            )
        assert len(risk_signals) == 1
        assert "止损" in risk_signals[0].signal_reason
        account.close()

    def test_bull_up_sector_wide_stop(self, temp_db):
        """Bull market + rising sector → 12% stop (widest), survives small dip.

        Note: uses risk_stop_multiplier=1.0 explicitly to test the BASE rule
        (bull/up → 12%); the default multiplier is now 0.70 (Sharpe-optimized).
        """
        from davis_analyzer.paper_trading.account import PaperAccount
        from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
        from davis_analyzer.paper_trading.executor import DailyExecutor

        account = PaperAccount.create("risk_bull_up", "factor_threshold", 100_000)
        account.buy("000051.SZ", "测试", 1000, 10.0, "20260101")
        strategy = FactorThresholdStrategy(risk_stop_multiplier=1.0)
        executor = DailyExecutor(account, strategy)

        # Price at 9.1 → loss of 9% → should NOT trigger 12% stop in bull+up
        risk_signals = executor._check_risk_signals(
            account.get_positions(), {"000051.SZ": 9.1}, "20260102",
            market_regime="bull",
            industries={"000051.SZ": "半导体"},
            industry_trend={"半导体": "up"},
        )
        assert len(risk_signals) == 0  # 9% < 12% stop (base rule, multiplier=1.0)
        account.close()

    def test_bull_up_sector_high_take_profit(self, temp_db):
        """Bull market + rising sector → 30% take-profit (doesn't trigger at 20%).

        Note: uses risk_stop_multiplier=1.0 explicitly to test the BASE rule
        (bull/up → 30% take-profit); default multiplier is now 0.70.
        """
        from davis_analyzer.paper_trading.account import PaperAccount
        from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
        from davis_analyzer.paper_trading.executor import DailyExecutor

        account = PaperAccount.create("risk_tp", "factor_threshold", 100_000)
        account.buy("000052.SZ", "测试", 1000, 10.0, "20260101")
        strategy = FactorThresholdStrategy(risk_stop_multiplier=1.0)
        executor = DailyExecutor(account, strategy)

        # Price at 12.0 → gain of 20% → should NOT trigger 30% take-profit in bull+up
        risk_signals = executor._check_risk_signals(
            account.get_positions(), {"000052.SZ": 12.0}, "20260102",
            market_regime="bull",
            industries={"000052.SZ": "半导体"},
            industry_trend={"半导体": "up"},
        )
        assert len(risk_signals) == 0  # 20% < 30% take-profit (base rule, multiplier=1.0)
        account.close()


# ── Report tests ───────────────────────────────────────────────────────


class TestReport:
    def test_empty_account_report(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount
        from davis_analyzer.paper_trading.report import generate_report

        account = PaperAccount.create("report_empty", "davis_double", 500_000)
        report = generate_report(account)
        assert "无历史数据" in report
        account.close()

    def test_report_with_data(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount
        from davis_analyzer.paper_trading.report import generate_report

        account = PaperAccount.create("report_data", "davis_double", 1_000_000)
        account.buy("000010.SZ", "测试", 1000, 10.0, "20260101")
        account.record_nav("20260101", {"000010.SZ": 11.0})
        account.record_nav("20260102", {"000010.SZ": 12.0})

        report = generate_report(account, current_prices={"000010.SZ": 12.0})
        assert "绩效概览" in report
        assert "净值曲线" in report
        assert "当前持仓" in report
        assert "1,000,000" in report or "1000000" in report  # initial capital
        account.close()


# ── Live Monitor tests ─────────────────────────────────────────────────


class TestLiveMonitor:
    def test_is_market_open_weekday_morning(self):
        from davis_analyzer.paper_trading.live_monitor import is_market_open
        from datetime import datetime
        from zoneinfo import ZoneInfo

        # Wednesday 10:00 CST → market open
        wed = datetime(2026, 7, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert is_market_open(wed) is True

    def test_is_market_closed_weekend(self):
        from davis_analyzer.paper_trading.live_monitor import is_market_open
        from datetime import datetime
        from zoneinfo import ZoneInfo

        # Saturday 10:00 → closed
        sat = datetime(2026, 7, 18, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert is_market_open(sat) is False

    def test_is_market_closed_lunch_break(self):
        from davis_analyzer.paper_trading.live_monitor import is_market_open
        from datetime import datetime
        from zoneinfo import ZoneInfo

        # Wednesday 12:00 → lunch break, closed
        noon = datetime(2026, 7, 15, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert is_market_open(noon) is False

    def test_is_market_closed_after_hours(self):
        from davis_analyzer.paper_trading.live_monitor import is_market_open
        from datetime import datetime
        from zoneinfo import ZoneInfo

        # Wednesday 16:00 → after hours
        late = datetime(2026, 7, 15, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert is_market_open(late) is False

    def test_sell_signal_hard_stop(self, temp_db):
        """Hard stop triggers sell when price drops below cost × (1 - 12%)."""
        from davis_analyzer.paper_trading.account import PaperAccount
        from davis_analyzer.paper_trading.strategy import create_strategy
        from davis_analyzer.paper_trading.live_monitor import LiveMonitor
        from unittest.mock import patch

        account = PaperAccount.create("live_stop_test", "factor_threshold", 100_000)
        # Buy at 10.0 → hard stop at 8.8 (10 × 0.88)
        account.buy("000099.SZ", "测试", 1000, 10.0, "20260101")
        assert len(account.get_positions()) == 1

        strategy = create_strategy("factor_threshold", account.config)
        monitor = LiveMonitor(account, strategy, interval_seconds=1)

        # Mock get_realtime_price to return 8.5 (below 8.8 stop)
        with patch("davis_analyzer.paper_trading.live_monitor.get_realtime_price", return_value=8.5):
            monitor._check_sell_signals("20260102")

        # Position should be sold
        assert len(account.get_positions()) == 0
        trades = account.get_trades()
        assert any(t.action == "SELL" and "止损" in t.signal_reason for t in trades)
        account.close()

    def test_sell_signal_target_reached(self, temp_db):
        """Take-profit triggers sell when price rises above cost × (1 + 20%)."""
        from davis_analyzer.paper_trading.account import PaperAccount
        from davis_analyzer.paper_trading.strategy import create_strategy
        from davis_analyzer.paper_trading.live_monitor import LiveMonitor
        from unittest.mock import patch

        account = PaperAccount.create("live_target_test", "factor_threshold", 100_000)
        # Buy at 10.0 → target at 12.0 (10 × 1.20)
        account.buy("000098.SZ", "测试", 1000, 10.0, "20260101")

        strategy = create_strategy("factor_threshold", account.config)
        monitor = LiveMonitor(account, strategy, interval_seconds=1)

        # Mock price at 12.5 (above 12.0 target)
        with patch("davis_analyzer.paper_trading.live_monitor.get_realtime_price", return_value=12.5):
            monitor._check_sell_signals("20260102")

        assert len(account.get_positions()) == 0
        trades = account.get_trades()
        assert any(t.action == "SELL" and "止盈" in t.signal_reason for t in trades)
        account.close()

    def test_no_sell_when_price_normal(self, temp_db):
        """No sell signal when price is within normal range."""
        from davis_analyzer.paper_trading.account import PaperAccount
        from davis_analyzer.paper_trading.strategy import create_strategy
        from davis_analyzer.paper_trading.live_monitor import LiveMonitor
        from unittest.mock import patch

        account = PaperAccount.create("live_normal_test", "factor_threshold", 100_000)
        account.buy("000097.SZ", "测试", 1000, 10.0, "20260101")

        strategy = create_strategy("factor_threshold", account.config)
        monitor = LiveMonitor(account, strategy, interval_seconds=1)

        # Price at 10.5 — within range (not below 8.8, not above 12.0)
        with patch("davis_analyzer.paper_trading.live_monitor.get_realtime_price", return_value=10.5):
            monitor._check_sell_signals("20260102")

        # Position should still be held
        assert len(account.get_positions()) == 1
        account.close()


# ── Volume-price signal tests ──────────────────────────────────────────


class TestVolumePriceRiskSell:
    """Tests for the high-position high-volume (高位放量) risk sell."""

    def test_high_vol_with_profit_triggers_sell(self, temp_db):
        """High-position volume + ≥10% profit → SELL signal."""
        from davis_analyzer.paper_trading.account import PaperAccount, Position
        from davis_analyzer.paper_trading.strategy import create_strategy
        from davis_analyzer.paper_trading.executor import DailyExecutor

        account = PaperAccount.create("vol_high_test", "factor_threshold", 100_000)
        strategy = create_strategy("factor_threshold", account.config)
        executor = DailyExecutor(account, strategy)

        # Bought at 10.0, now at 12.0 → +20% profit
        positions = [Position("HVOL.SZ", "测试", 1000, 10.0, "20260101")]
        vol_signals = {
            "HVOL.SZ": {
                "score": 28.0,
                "signal_type": "high_vol",
                "vol_ratio": 2.5,
                "position_pct": 95.0,
                "box_amplitude": 18.0,
            }
        }
        signals = executor._check_risk_signals(
            positions, {"HVOL.SZ": 12.0}, "20260102",
            market_regime="bull",
            industries={"HVOL.SZ": "半导体"},
            industry_trend={"半导体": "up"},
            volume_signals=vol_signals,
        )
        assert len(signals) == 1
        assert signals[0].action == "SELL"
        assert "高位放量" in signals[0].signal_reason
        account.close()

    def test_high_vol_without_profit_no_trigger(self, temp_db):
        """High-position volume but profit < 10% → no SELL (avoid killing fresh buys)."""
        from davis_analyzer.paper_trading.account import PaperAccount, Position
        from davis_analyzer.paper_trading.strategy import create_strategy
        from davis_analyzer.paper_trading.executor import DailyExecutor

        account = PaperAccount.create("vol_low_pnl_test", "factor_threshold", 100_000)
        strategy = create_strategy("factor_threshold", account.config)
        executor = DailyExecutor(account, strategy)

        # Bought at 10.0, now at 10.5 → +5% profit (below 10% threshold)
        positions = [Position("HVOL2.SZ", "测试", 1000, 10.0, "20260101")]
        vol_signals = {
            "HVOL2.SZ": {
                "score": 28.0,
                "signal_type": "high_vol",
                "vol_ratio": 2.5,
                "position_pct": 95.0,
                "box_amplitude": 18.0,
            }
        }
        signals = executor._check_risk_signals(
            positions, {"HVOL2.SZ": 10.5}, "20260102",
            market_regime="bull",
            industries={"HVOL2.SZ": "半导体"},
            industry_trend={"半导体": "up"},
            volume_signals=vol_signals,
        )
        assert len(signals) == 0  # profit too low → no trigger
        account.close()

    def test_high_vol_with_enable_volume_risk_off(self, temp_db):
        """When enable_volume_risk=False, high-vol signal is ignored."""
        from davis_analyzer.paper_trading.account import PaperAccount, Position
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy,
        )
        from davis_analyzer.paper_trading.executor import DailyExecutor

        account = PaperAccount.create("vol_disabled_test", "factor_threshold", 100_000)
        # Disable volume-risk sell explicitly
        strategy = FactorThresholdStrategy(enable_volume_risk=False)
        executor = DailyExecutor(account, strategy)

        positions = [Position("HVOL3.SZ", "测试", 1000, 10.0, "20260101")]
        vol_signals = {
            "HVOL3.SZ": {
                "score": 28.0,
                "signal_type": "high_vol",
                "vol_ratio": 2.5,
                "position_pct": 95.0,
                "box_amplitude": 18.0,
            }
        }
        signals = executor._check_risk_signals(
            positions, {"HVOL3.SZ": 12.0}, "20260102",
            market_regime="bull",
            industries={"HVOL3.SZ": "半导体"},
            industry_trend={"半导体": "up"},
            volume_signals=vol_signals,
        )
        assert len(signals) == 0  # disabled → no trigger
        account.close()

    def test_neutral_volume_no_trigger(self, temp_db):
        """Neutral volume signal (no high_vol) → no risk sell."""
        from davis_analyzer.paper_trading.account import PaperAccount, Position
        from davis_analyzer.paper_trading.strategy import create_strategy
        from davis_analyzer.paper_trading.executor import DailyExecutor

        account = PaperAccount.create("vol_neutral_test", "factor_threshold", 100_000)
        strategy = create_strategy("factor_threshold", account.config)
        executor = DailyExecutor(account, strategy)

        positions = [Position("NEUT.SZ", "测试", 1000, 10.0, "20260101")]
        vol_signals = {
            "NEUT.SZ": {
                "score": 50.0,
                "signal_type": "neutral",
                "vol_ratio": 1.1,
                "position_pct": 50.0,
                "box_amplitude": 10.0,
            }
        }
        signals = executor._check_risk_signals(
            positions, {"NEUT.SZ": 12.0}, "20260102",
            market_regime="bull",
            industries={"NEUT.SZ": "半导体"},
            industry_trend={"半导体": "up"},
            volume_signals=vol_signals,
        )
        assert len(signals) == 0  # neutral volume → no extra risk sell
        account.close()


class TestVolumeCompositeScore:
    """Tests for volume-price score in the composite rating."""

    def test_volume_score_boosts_composite(self):
        """A high volume score (low_vol/platform_breakout) raises composite rating."""
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy,
            MarketSnapshot,
        )

        # Same factors, but different volume scores
        base_factors = {"momentum": 75, "holder": 60, "prosperity": 55}
        # Stock A: neutral volume (score 50)
        # Stock B: low_vol signal (score 80)
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"A.SZ": 10.0, "B.SZ": 10.0},
            factor_scores={"A.SZ": base_factors, "B.SZ": base_factors},
            market_regime="bull",
            volume_signal={
                "A.SZ": {"score": 50.0, "signal_type": "neutral"},
                "B.SZ": {"score": 80.0, "signal_type": "low_vol"},
            },
        )
        strategy = FactorThresholdStrategy(volume_weight=0.10)
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        # Both qualify, but B should rank higher due to higher volume score
        assert len(buys) == 2
        # B.SZ should be first (higher composite due to volume bonus)
        assert buys[0].ts_code == "B.SZ"

    def test_volume_weight_zero_legacy_behavior(self):
        """When volume_weight=0, volume signal doesn't affect ranking."""
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy,
            MarketSnapshot,
        )

        base_factors = {"momentum": 75, "holder": 60, "prosperity": 55}
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"A.SZ": 10.0, "B.SZ": 10.0},
            factor_scores={"A.SZ": base_factors, "B.SZ": base_factors},
            market_regime="bull",
            volume_signal={
                "A.SZ": {"score": 50.0, "signal_type": "neutral"},
                "B.SZ": {"score": 90.0, "signal_type": "platform_breakout"},
            },
        )
        # With volume_weight=0, both stocks should have identical composite
        strategy = FactorThresholdStrategy(volume_weight=0.0)
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        # Both still bought (factors qualify), but the ranking order doesn't
        # depend on volume. We just verify both qualify.
        assert len(buys) == 2

    def test_volume_signal_field_in_snapshot(self):
        """MarketSnapshot accepts a volume_signal field."""
        from davis_analyzer.paper_trading.strategy import MarketSnapshot

        snap = MarketSnapshot(
            trade_date="20260101",
            prices={},
            volume_signal={"X.SZ": {"score": 70.0, "signal_type": "low_vol"}},
        )
        assert "X.SZ" in snap.volume_signal
        assert snap.volume_signal["X.SZ"]["signal_type"] == "low_vol"


# ── Event filter tests ─────────────────────────────────────────────────


class TestEventFilter:
    """Tests for the event hard-filter (减持/解禁)."""

    def test_blocked_stock_excluded_from_buys(self):
        """A stock with blocked=True event_signal should not be bought."""
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy, MarketSnapshot,
        )

        strategy = FactorThresholdStrategy(buy_momentum=65, buy_holder_min=35,
                                           enable_event_filter=True)
        # A.SZ has strong factors but is event-blocked; B.SZ same factors, not blocked
        base_factors = {"momentum": 80, "holder": 60}
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"A.SZ": 10.0, "B.SZ": 10.0},
            factor_scores={"A.SZ": base_factors, "B.SZ": base_factors},
            market_regime="bull",
            event_signal={
                "A.SZ": {"blocked": True, "reason": "减持"},
                "B.SZ": {"blocked": False, "reason": ""},
            },
        )
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 1
        assert buys[0].ts_code == "B.SZ"  # only unblocked stock

    def test_filter_disabled_allows_blocked_stock(self):
        """When enable_event_filter=False, blocked stocks can still be bought."""
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy, MarketSnapshot,
        )

        strategy = FactorThresholdStrategy(buy_momentum=65, buy_holder_min=35,
                                           enable_event_filter=False)
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"A.SZ": 10.0},
            factor_scores={"A.SZ": {"momentum": 80, "holder": 60}},
            market_regime="bull",
            event_signal={"A.SZ": {"blocked": True, "reason": "减持"}},
        )
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 1
        assert buys[0].ts_code == "A.SZ"  # filter off, can buy

    def test_missing_event_signal_does_not_block(self):
        """Stock not in event_signal dict should NOT be blocked (graceful degradation)."""
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy, MarketSnapshot,
        )

        strategy = FactorThresholdStrategy(buy_momentum=65, buy_holder_min=35,
                                           enable_event_filter=True)
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"X.SZ": 10.0},
            factor_scores={"X.SZ": {"momentum": 80, "holder": 60}},
            market_regime="bull",
            event_signal={},  # X.SZ not present → no event data
        )
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 1  # missing event data = no block


class TestEventSoftPenalty:
    """Tests for the event soft-penalty (composite deduction, not hard-gate)."""

    def test_penalized_stock_still_buys_but_ranks_lower(self):
        """Stock with event penalty should still be buyable, just rank below peers."""
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy, MarketSnapshot,
        )

        # A.SZ and B.SZ same factors; A has event penalty, B doesn't
        base_factors = {"momentum": 75, "holder": 60, "prosperity": 55}
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"A.SZ": 10.0, "B.SZ": 10.0},
            factor_scores={"A.SZ": base_factors, "B.SZ": base_factors},
            market_regime="bull",
            event_signal={
                "A.SZ": {"blocked": True, "penalty": 20.0, "reason": "减持"},
                "B.SZ": {"blocked": False, "penalty": 0.0, "reason": ""},
            },
        )
        # Soft penalty: weight=1.0 → 20-point deduction; hard filter OFF
        strategy = FactorThresholdStrategy(
            enable_event_filter=False,  # hard filter off
            event_penalty_weight=1.0,   # soft penalty on
        )
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        # Both qualify (no hard filter), but B ranks first (no penalty)
        assert len(buys) == 2
        assert buys[0].ts_code == "B.SZ"  # B ranks higher

    def test_zero_penalty_weight_no_effect(self):
        """When event_penalty_weight=0, penalty has no effect."""
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy, MarketSnapshot,
        )

        base_factors = {"momentum": 75, "holder": 60, "prosperity": 55}
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"A.SZ": 10.0, "B.SZ": 10.0},
            factor_scores={"A.SZ": base_factors, "B.SZ": base_factors},
            market_regime="bull",
            event_signal={
                "A.SZ": {"blocked": True, "penalty": 30.0, "reason": "减持"},
                "B.SZ": {"blocked": False, "penalty": 0.0, "reason": ""},
            },
        )
        # Both penalty weight and hard filter OFF → fully legacy behavior
        strategy = FactorThresholdStrategy(
            enable_event_filter=False, event_penalty_weight=0.0,
        )
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 2
        # Same factors, no penalty effect → composite equal, ranking arbitrary but both qualify

    def test_hard_filter_overrides_soft_penalty(self):
        """When both hard filter and soft penalty are on, hard filter wins (skip)."""
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy, MarketSnapshot,
        )

        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"A.SZ": 10.0},
            factor_scores={"A.SZ": {"momentum": 80, "holder": 60}},
            market_regime="bull",
            event_signal={"A.SZ": {"blocked": True, "penalty": 30.0, "reason": "减持"}},
        )
        strategy = FactorThresholdStrategy(
            enable_event_filter=True,   # hard filter on → skip
            event_penalty_weight=1.0,   # soft penalty also on (but hard wins)
        )
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 0  # hard filter blocked the buy


# ── Sharpe-optimized defaults tests ────────────────────────────────────


class TestSharpeOptimizedDefaults:
    """Tests verifying the Sharpe-optimized default config (2026-07-21 sweep)."""

    def test_default_max_positions_is_5(self):
        """Sharpe sweep showed pos=5 beats pos=10/12 in all stop_mult settings."""
        from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
        s = FactorThresholdStrategy()
        assert s.max_positions == 5  # was 10 before Sharpe optimization

    def test_default_risk_stop_multiplier_is_0_70(self):
        """Sharpe sweep showed stop_mult=0.70 + pos=5 = best Sharpe (-0.133)."""
        from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
        s = FactorThresholdStrategy()
        assert s.risk_stop_multiplier == 0.70  # was 1.0 before

    def test_tighter_stop_actually_reduces_threshold(self, temp_db):
        """Verify risk_stop_multiplier=0.70 gives 8.4% stop (not 12%) in bull/up."""
        from davis_analyzer.paper_trading.account import PaperAccount
        from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
        from davis_analyzer.paper_trading.executor import DailyExecutor

        account = PaperAccount.create("sharpe_default_test", "factor_threshold", 100_000)
        account.buy("000060.SZ", "测试", 1000, 10.0, "20260101")
        # Default strategy: stop_mult=0.70 → bull/up base 12% × 0.70 = 8.4%
        strategy = FactorThresholdStrategy()  # default config
        executor = DailyExecutor(account, strategy)

        # Price at 9.0 → loss of 10% → should trigger 8.4% stop
        risk_signals = executor._check_risk_signals(
            account.get_positions(), {"000060.SZ": 9.0}, "20260102",
            market_regime="bull",
            industries={"000060.SZ": "半导体"},
            industry_trend={"半导体": "up"},
        )
        assert len(risk_signals) == 1
        assert "止损" in risk_signals[0].signal_reason
        account.close()


# ── Technical factor composite tests ───────────────────────────────────


class TestTechScoreComposite:
    """Tests for tech_score in the composite rating."""

    def test_high_tech_score_ranks_higher(self):
        """Stock with higher tech_score ranks above same-factor peer."""
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy, MarketSnapshot,
        )

        base_factors = {"momentum": 75, "holder": 60, "prosperity": 55}
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"A.SZ": 10.0, "B.SZ": 10.0},
            factor_scores={"A.SZ": base_factors, "B.SZ": base_factors},
            market_regime="bull",
            tech_score={"A.SZ": 30.0, "B.SZ": 80.0},  # B is stronger technically
        )
        strategy = FactorThresholdStrategy(tech_weight=0.10)
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 2
        # B.SZ should rank first due to higher tech score
        assert buys[0].ts_code == "B.SZ"

    def test_tech_weight_zero_ignores_score(self):
        """When tech_weight=0, tech_score doesn't affect ranking."""
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy, MarketSnapshot,
        )

        base_factors = {"momentum": 75, "holder": 60, "prosperity": 55}
        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"A.SZ": 10.0, "B.SZ": 10.0},
            factor_scores={"A.SZ": base_factors, "B.SZ": base_factors},
            market_regime="bull",
            tech_score={"A.SZ": 10.0, "B.SZ": 90.0},  # huge tech difference
        )
        strategy = FactorThresholdStrategy(tech_weight=0.0)
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        # Both qualify; tech_weight=0 means tech_score doesn't change ranking
        assert len(buys) == 2

    def test_tech_score_defaults_to_neutral_when_missing(self):
        """Missing tech_score should default to neutral (50), not crash."""
        from davis_analyzer.paper_trading.strategy import (
            FactorThresholdStrategy, MarketSnapshot,
        )

        snapshot = MarketSnapshot(
            trade_date="20260101",
            prices={"X.SZ": 10.0},
            factor_scores={"X.SZ": {"momentum": 80, "holder": 60}},
            market_regime="bull",
            tech_score={},  # no tech data
        )
        strategy = FactorThresholdStrategy(tech_weight=0.10)
        signals = strategy.evaluate([], snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 1  # missing data → neutral score, still qualifies
