"""Tests for board-aware minimum buy lots and small-account affordability.

背景 (2026-08-19): mini_100k 十万小账户接入 inject 链路后暴露两个漏洞——
1) 股价高于等权槽位资金(权益/top_n)的标的按 100 股买不起 → 被静默跳过且
   不顺位递补, 小账户系统性欠仓;
2) 科创板(688/689)限价单笔下限 200 股, 旧代码统一 100 整手, 会产生真实
   市场不可执行的 100 股科创板买单。
"""

import os
import tempfile

import pytest

os.environ.setdefault("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@pytest.fixture(scope="module")
def temp_db():
    """Use a temporary stockhot.db for account tests."""
    tmpdir = tempfile.mkdtemp()
    tmp_db = os.path.join(tmpdir, "stockhot.db")
    from stockhot.core import config as stockhot_config
    from stockhot.storage import database as db_module

    old_path = stockhot_config.DB_PATH
    stockhot_config.DB_PATH = type(old_path)(tmp_db)
    db_module.DB_PATH = stockhot_config.DB_PATH
    db_module.init_database()
    yield tmp_db
    stockhot_config.DB_PATH = old_path
    db_module.DB_PATH = old_path


# ── min_buy_lots 纯函数 ────────────────────────────────────────────────


class TestMinBuyLots:
    def test_star_market_200_shares(self):
        from davis_analyzer.paper_trading.account import min_buy_lots

        assert min_buy_lots("688002.SH") == 200
        assert min_buy_lots("689009.SH") == 200

    def test_other_boards_100_shares(self):
        from davis_analyzer.paper_trading.account import min_buy_lots

        assert min_buy_lots("600519.SH") == 100
        assert min_buy_lots("603893.SH") == 100
        assert min_buy_lots("300558.SZ") == 100
        assert min_buy_lots("000001.SZ") == 100
        assert min_buy_lots("920107.BJ") == 100

    def test_empty_code(self):
        from davis_analyzer.paper_trading.account import min_buy_lots

        assert min_buy_lots("") == 100
        assert min_buy_lots(None) == 100


# ── account.buy 板块下限强制 ───────────────────────────────────────────


class TestBuyBoardMinimum:
    def test_star_buy_below_200_rejected(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount

        account = PaperAccount.create("lot_star_reject", "davis_double", 1_000_000)
        # 150 股整手取整为 100 < 科创板 200 下限 → 拒单
        assert account.buy("688002.SH", "睿创微纳", 150, 150.0, "20260101") is None

    def test_star_buy_250_rounds_to_200(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount

        account = PaperAccount.create("lot_star_round", "davis_double", 1_000_000)
        trade = account.buy("688002.SH", "睿创微纳", 250, 150.0, "20260101")
        assert trade is not None
        assert trade.shares == 200

    def test_main_board_100_lot_still_works(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount

        account = PaperAccount.create("lot_main", "davis_double", 1_000_000)
        trade = account.buy("000001.SZ", "平安银行", 100, 10.0, "20260101")
        assert trade is not None
        assert trade.shares == 100

    def test_star_cash_trim_below_200_rejected(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount

        # 现金 1.8 万, 科创板 @95 元: 整手取整后 100 股 < 200 → 拒单
        account = PaperAccount.create("lot_star_trim", "davis_double", 18_000)
        assert account.buy("688125.SH", "安恒信息", 1000, 95.0, "20260101") is None

    def test_main_board_cash_trim_to_affordable(self, temp_db):
        from davis_analyzer.paper_trading.account import PaperAccount

        # 现金 1.8 万, 主板 @85 元: 目标 1000 股买不起 → 缩减到 200 股
        account = PaperAccount.create("lot_trim_ok", "davis_double", 18_000)
        trade = account.buy("600000.SH", "浦发银行", 1000, 85.0, "20260101")
        assert trade is not None
        assert trade.shares == 200


# ── DavisDoubleStrategy 小资金可买性递补 ──────────────────────────────


def _make_snapshot(prices: dict, davis_scores: dict) -> object:
    from davis_analyzer.paper_trading.strategy import MarketSnapshot

    return MarketSnapshot(
        trade_date="20260819",
        prices=prices,
        davis_scores=davis_scores,
        stock_names={c: f"股票{i}" for i, c in enumerate(davis_scores)},
    )


class TestDavisDoubleAffordability:
    def _strategy(self):
        from davis_analyzer.paper_trading.strategy import DavisDoubleStrategy

        return DavisDoubleStrategy(top_n=5, frequency=1, min_score=60.0)

    def test_high_price_stock_substituted(self):
        """槽位资金(100k/5=2万)买不起一手的标的被跳过, 顺位递补第 6 名."""
        strat = self._strategy()
        scores = {
            # #1 主板 300 元 → 一手 3 万 > 2 万, 跳过
            "600001.SH": {"final_score": 80.0, "name": "高价股"},
            # #2-#5 均可买 (一手 ≤ 2 万)
            "600002.SH": {"final_score": 78.0, "name": "B"},
            "600003.SH": {"final_score": 76.0, "name": "C"},
            "600004.SH": {"final_score": 74.0, "name": "D"},
            "600005.SH": {"final_score": 72.0, "name": "E"},
            # #6 递补进入
            "600006.SH": {"final_score": 70.0, "name": "F"},
        }
        prices = {
            "600001.SH": 300.0,
            "600002.SH": 100.0,
            "600003.SH": 50.0,
            "600004.SH": 20.0,
            "600005.SH": 30.0,
            "600006.SH": 40.0,
        }
        signals = strat.evaluate([], _make_snapshot(prices, scores), total_equity=100_000)
        buys = {s.ts_code for s in signals if s.action == "BUY"}
        assert "600001.SH" not in buys
        assert buys == {"600002.SH", "600003.SH", "600004.SH", "600005.SH", "600006.SH"}

    def test_star_200_lot_gate(self):
        """科创板 @150 元一手(200股)=3万 > 2万槽位 → 跳过递补, 尽管 100 股买得起."""
        strat = self._strategy()
        scores = {
            "688001.SH": {"final_score": 90.0, "name": "科创板"},
            "600002.SH": {"final_score": 80.0, "name": "B"},
            "600003.SH": {"final_score": 70.0, "name": "C"},
        }
        prices = {"688001.SH": 150.0, "600002.SH": 50.0, "600003.SH": 60.0}
        signals = strat.evaluate([], _make_snapshot(prices, scores), total_equity=100_000)
        buys = {s.ts_code for s in signals if s.action == "BUY"}
        assert "688001.SH" not in buys
        assert "600002.SH" in buys

    def test_large_account_unchanged(self):
        """100 万主仓: 300 元高价股一手 3 万 ≤ 20 万槽位, 正常入选."""
        strat = self._strategy()
        scores = {
            "600001.SH": {"final_score": 80.0, "name": "高价股"},
            "600002.SH": {"final_score": 70.0, "name": "B"},
        }
        prices = {"600001.SH": 300.0, "600002.SH": 50.0}
        signals = strat.evaluate([], _make_snapshot(prices, scores), total_equity=1_000_000)
        buys = {s.ts_code for s in signals if s.action == "BUY"}
        assert "600001.SH" in buys

    def test_fewer_affordable_targets_lower_weight(self):
        """只有 2 只可买时权重按 1/2 分配(既有语义), 不强行凑满."""
        strat = self._strategy()
        scores = {
            "600001.SH": {"final_score": 80.0, "name": "高价"},
            "600002.SH": {"final_score": 70.0, "name": "B"},
        }
        prices = {"600001.SH": 300.0, "600002.SH": 50.0}
        signals = strat.evaluate([], _make_snapshot(prices, scores), total_equity=100_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert len(buys) == 1
        assert buys[0].ts_code == "600002.SH"
        assert buys[0].target_weight == pytest.approx(1.0)

    def test_min_score_floor_still_enforced(self):
        """递补不突破 min_score 下限."""
        strat = self._strategy()
        scores = {
            "600001.SH": {"final_score": 80.0, "name": "高价"},
            "600002.SH": {"final_score": 59.9, "name": "低分"},
        }
        prices = {"600001.SH": 300.0, "600002.SH": 50.0}
        signals = strat.evaluate([], _make_snapshot(prices, scores), total_equity=100_000)
        buys = [s for s in signals if s.action == "BUY"]
        assert buys == []
