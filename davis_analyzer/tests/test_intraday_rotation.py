"""Tests for 尾盘调仓 (intraday_rotation) 的纯逻辑部分.

盘中轮动的执行路径依赖实时行情与 DB, 由 --dry-run 手工验证; 此处覆盖
可纯测的部分: 板块涨跌停幅度、执行时间窗、TradeRecord → 推送 dict 转换。
"""

import sys
from datetime import datetime

from stockhot.invest_sop.scripts.intraday_rotation import (  # noqa: F401  (sys 用于被测模块的路径引导)
    _in_run_window,
    _limit_pct,
    _trade_dict,
)


class TestLimitPct:
    def test_beijing_30(self):
        assert _limit_pct("920107.BJ") == 30.0
        assert _limit_pct("920021.BJ") == 30.0
        assert _limit_pct("835185.BJ") == 30.0

    def test_star_chinext_20(self):
        assert _limit_pct("688002.SH") == 20.0
        assert _limit_pct("689009.SH") == 20.0
        assert _limit_pct("300558.SZ") == 20.0
        assert _limit_pct("301001.SZ") == 20.0

    def test_main_board_10(self):
        assert _limit_pct("600000.SH") == 10.0
        assert _limit_pct("603893.SH") == 10.0
        assert _limit_pct("000001.SZ") == 10.0
        assert _limit_pct("001389.SZ") == 10.0

    def test_empty(self):
        assert _limit_pct("") == 10.0


class TestRunWindow:
    def test_weekday_in_window(self):
        # 2026-08-20 是周四；触发窗口 14:40-15:00
        assert _in_run_window(datetime(2026, 8, 20, 14, 40)) is True
        assert _in_run_window(datetime(2026, 8, 20, 15, 0)) is True

    def test_outside_window(self):
        assert _in_run_window(datetime(2026, 8, 20, 9, 30)) is False
        assert _in_run_window(datetime(2026, 8, 20, 15, 1)) is False
        assert _in_run_window(datetime(2026, 8, 20, 14, 39)) is False
        assert _in_run_window(datetime(2026, 8, 20, 14, 30)) is False  # 触发前

    def test_weekend_rejected(self):
        # 2026-08-22 周六
        assert _in_run_window(datetime(2026, 8, 22, 14, 40)) is False


class TestTradeDict:
    def test_trade_record_to_dict(self):
        from davis_analyzer.paper_trading.account import TradeRecord

        trade = TradeRecord(
            trade_date="20260820",
            ts_code="600989.SH",
            name="宝丰能源",
            action="BUY",
            shares=800,
            price=23.4,
            amount=18720.0,
            cost=4.68,
            signal_reason="final_score=66.4 top5",
        )
        d = _trade_dict(trade)
        assert d["ts_code"] == "600989.SH"
        assert d["name"] == "宝丰能源"
        assert d["price"] == 23.4
        assert d["shares"] == 800
        assert "top5" in d["signal_reason"]
