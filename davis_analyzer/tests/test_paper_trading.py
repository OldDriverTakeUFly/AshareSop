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
        snapshot = MarketSnapshot(trade_date="20260102", prices={"A.SZ": 10.0})

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
        positions = [Position("A.SZ", "A", 100, 10, "20260101")]
        snapshot = MarketSnapshot(
            trade_date="20260102",
            prices={"B.SZ": 10.0, "C.SZ": 10.0, "D.SZ": 10.0},
            factor_scores={
                "B.SZ": {"momentum": 80, "holder": 60},
                "C.SZ": {"momentum": 75, "holder": 50},
                "D.SZ": {"momentum": 90, "holder": 70},
            },
        )
        signals = strategy.evaluate(positions, snapshot, 1_000_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 1  # only 1 slot available (max 2 - 1 held)


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
