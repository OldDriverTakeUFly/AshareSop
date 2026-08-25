"""paper_trading 卖出冷却持久化测试（2026-08-25 技术债修复）.

背景: strategy._cooldown 是实例内存 dict(ts_code→卖出日), 进程重启即丢,
重启后会违反 5 日回购纪律。修复: rebuild_cooldown_from_trades 从
paper_trades 表(单一真相源)以最新 trade_date 为"今天"重建。
"""

from __future__ import annotations

from davis_analyzer.paper_trading.account import TradeRecord
from davis_analyzer.paper_trading.strategy import create_strategy


def _sell(code: str, d: str) -> TradeRecord:
    return TradeRecord(
        trade_date=d, ts_code=code, name="x", action="SELL",
        shares=100, price=10.0, amount=1000.0, cost=1.0,
    )


def _buy(code: str, d: str) -> TradeRecord:
    return TradeRecord(
        trade_date=d, ts_code=code, name="x", action="BUY",
        shares=100, price=10.0, amount=1000.0, cost=1.0,
    )


def test_rebuild_from_recent_sells_only() -> None:
    """5 日内 SELL 进入冷却; 更早的 SELL 与 BUY 不进."""
    strategy = create_strategy("factor_threshold", {})
    n = strategy.rebuild_cooldown_from_trades([
        _sell("000001.SZ", "20260825"),  # 最新交易日(当天卖出)
        _sell("600000.SH", "20260822"),  # 3 日前 → 冷却内
        _sell("300750.SZ", "20260815"),  # 10 日前 → 过期
        _buy("688141.SH", "20260824"),   # BUY 不进冷却
    ])
    assert n == 2
    assert strategy._cooldown["000001.SZ"] == "20260825"
    assert strategy._cooldown["600000.SH"] == "20260822"
    assert "300750.SZ" not in strategy._cooldown
    assert "688141.SH" not in strategy._cooldown


def test_rebuild_keeps_latest_sell_per_code() -> None:
    """同票多次卖出保留最近日期(冷却保守取长)."""
    strategy = create_strategy("factor_threshold", {})
    strategy.rebuild_cooldown_from_trades([
        _sell("000001.SZ", "20260821"),
        _sell("000001.SZ", "20260824"),
    ])
    assert strategy._cooldown["000001.SZ"] == "20260824"


def test_rebuild_empty_trades() -> None:
    strategy = create_strategy("factor_threshold", {})
    assert strategy.rebuild_cooldown_from_trades([]) == 0
    assert strategy._cooldown == {}


def test_rebuilt_cooldown_blocks_rebuy_semantics() -> None:
    """重建结果与运行时写入的冷却同构: 命中候选排除检查(1226/1278 语义)."""
    strategy = create_strategy("factor_threshold", {})
    strategy.rebuild_cooldown_from_trades([_sell("000001.SZ", "20260824")])
    # 与 strategy.generate_signals 运行时的检查同式: code in self._cooldown
    assert "000001.SZ" in strategy._cooldown
