"""BoardChasingStrategy 双名注册测试（limitup Phase 3 Task 6）.

Spec: docs/superpowers/specs/2026-08-17-limitup-phase3-design.md §3.1/§3.3.
- 持仓 → SELL 电平型（持有期间每日重发，sell_at_open=True）——顺延漏卖防线
- 未持仓候选 → BUY（target_weight=1/max_positions，reason 含形态/封档/enhanced）
- 候选来源 limitup.candidates.build_candidates（monkeypatch，不连真实库）
- 双名注册：board_chasing（False）/ board_chasing_enhanced（True）
"""

import os

import pandas as pd
import pytest

# Ensure PROJECT_ROOT before any stockhot import
os.environ.setdefault(
    "PROJECT_ROOT",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)


# ── helpers ─────────────────────────────────────────────────────────────


class _FakeConn:
    """Sentinel connection: counts close() calls (try/finally 验证)."""

    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def _mk_cands(rows: list[dict]) -> pd.DataFrame:
    """合成候选帧（对齐 candidates.CANDIDATE_COLUMNS 契约列）."""
    from davis_analyzer.limitup.candidates import CANDIDATE_COLUMNS

    df = pd.DataFrame(rows)
    for col in CANDIDATE_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[CANDIDATE_COLUMNS]


def _cand(code: str, name: str = "测试股", pattern: str = "突破型",
          seal_ratio: float = 0.08, band: str = "强",
          enhanced: bool = False) -> dict:
    return {
        "ts_code": code, "name": name, "sector": "电子",
        "pattern_label": pattern, "seal_ratio": seal_ratio, "封档": band,
        "first_seal_band": band, "broken_count": 0, "lg_sell_share": 0.6,
        "enhanced": enhanced, "fill_prob": 0.2,
    }


def _patch_candidates(monkeypatch, frame_or_exc):
    """Patch build_candidates + db.connect；返回 (calls, fake_conn).

    calls: [{"conn":..., "date":..., "enhanced_filter":...}, ...]
    frame_or_exc: 返回的候选帧，或要抛出的 Exception 实例.
    """
    calls: list[dict] = []
    fake_conn = _FakeConn()

    def _fake_build_candidates(conn, date, *, enhanced_filter=False, lookback_days=60):
        calls.append({"conn": conn, "date": date,
                      "enhanced_filter": enhanced_filter})
        if isinstance(frame_or_exc, Exception):
            raise frame_or_exc
        return frame_or_exc

    monkeypatch.setattr(
        "davis_analyzer.limitup.candidates.build_candidates", _fake_build_candidates
    )
    monkeypatch.setattr("davis_analyzer.limitup.db.connect", lambda: fake_conn)
    return calls, fake_conn


def _snapshot(date: str = "20260818"):
    from davis_analyzer.paper_trading.strategy import MarketSnapshot

    return MarketSnapshot(trade_date=date, prices={})


def _position(code: str = "600001.SH", name: str = "持仓A"):
    from davis_analyzer.paper_trading.account import Position

    return Position(code, name, 100, 10.0, "20260817")


def _buy_map(signals: list) -> dict:
    return {s.ts_code: s for s in signals if s.action == "BUY"}


def _sell_map(signals: list) -> dict:
    return {s.ts_code: s for s in signals if s.action == "SELL"}


# ── 工厂双名注册 ────────────────────────────────────────────────────────


class TestFactoryRegistration:
    def test_board_chasing_base(self):
        from davis_analyzer.paper_trading.strategy import (
            BoardChasingStrategy, create_strategy,
        )

        strategy = create_strategy("board_chasing", {})
        assert isinstance(strategy, BoardChasingStrategy)
        assert strategy._enhanced is False
        assert strategy.name == "board_chasing"

    def test_board_chasing_enhanced(self):
        from davis_analyzer.paper_trading.strategy import (
            BoardChasingStrategy, create_strategy,
        )

        strategy = create_strategy("board_chasing_enhanced", {})
        assert isinstance(strategy, BoardChasingStrategy)
        assert strategy._enhanced is True
        assert strategy.name == "board_chasing_enhanced"

    def test_existing_strategies_unchanged(self):
        from davis_analyzer.paper_trading.strategy import (
            STRATEGY_REGISTRY, DavisDoubleStrategy, FactorThresholdStrategy,
        )

        assert STRATEGY_REGISTRY["davis_double"] is DavisDoubleStrategy
        assert STRATEGY_REGISTRY["factor_threshold"] is FactorThresholdStrategy


# ── 卖出：电平型 + sell_at_open ────────────────────────────────────────


class TestLevelSell:
    def test_position_gets_sell_with_sell_at_open(self, monkeypatch):
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        calls, _ = _patch_candidates(
            monkeypatch, _mk_cands([_cand("600002.SH", enhanced=True)])
        )
        strategy = BoardChasingStrategy()
        signals = strategy.evaluate([_position()], _snapshot(), 1_000_000.0)

        sells = _sell_map(signals)
        assert "600001.SH" in sells
        sig = sells["600001.SH"]
        assert sig.sell_at_open is True
        assert sig.signal_reason == "T+1开盘卖(打板)"

    def test_sell_is_level_triggered_not_edge(self, monkeypatch):
        """同一持仓连续两次 evaluate 都发 SELL（顺延漏卖防线）."""
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        _patch_candidates(monkeypatch, _mk_cands([_cand("600002.SH")]))
        strategy = BoardChasingStrategy()
        positions = [_position()]

        for _ in range(2):
            signals = strategy.evaluate(positions, _snapshot(), 1_000_000.0)
            sells = _sell_map(signals)
            assert "600001.SH" in sells
            assert sells["600001.SH"].sell_at_open is True


# ── 买入：权重 / reason / 名额 ─────────────────────────────────────────


class TestBuy:
    def test_buy_weight_is_one_third(self, monkeypatch):
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        _patch_candidates(monkeypatch, _mk_cands([
            _cand("600002.SH"), _cand("600003.SH"),
        ]))
        strategy = BoardChasingStrategy()
        signals = strategy.evaluate([], _snapshot(), 1_000_000.0)

        buys = _buy_map(signals)
        assert set(buys) == {"600002.SH", "600003.SH"}
        for sig in buys.values():
            assert sig.target_weight == pytest.approx(1 / 3)

    def test_buy_reason_contains_key_fields(self, monkeypatch):
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        _patch_candidates(monkeypatch, _mk_cands([
            _cand("600002.SH", name="突破股", pattern="突破型",
                  band="强", enhanced=False),
        ]))
        strategy = BoardChasingStrategy()
        signals = strategy.evaluate([], _snapshot(), 1_000_000.0)

        buys = _buy_map(signals)
        reason = buys["600002.SH"].signal_reason
        assert "首板打板" in reason
        assert "突破型" in reason
        assert "封档=强" in reason
        assert "enhanced=否" in reason
        assert buys["600002.SH"].name == "突破股"

    def test_buy_capped_at_max_positions_in_frame_order(self, monkeypatch):
        """候选多于名额时按帧序（封单比降序）取前 max_positions 个."""
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        codes = [f"60000{i}.SH" for i in range(2, 7)]
        _patch_candidates(monkeypatch, _mk_cands([_cand(c) for c in codes]))
        strategy = BoardChasingStrategy()
        signals = strategy.evaluate([], _snapshot(), 1_000_000.0)

        buys = _buy_map(signals)
        assert len(buys) == 3
        assert set(buys) == set(codes[:3])

    def test_held_code_not_rebought(self, monkeypatch):
        """持仓 code 出现在候选里也不发 BUY（当日已 SELL）."""
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        _patch_candidates(monkeypatch, _mk_cands([
            _cand("600001.SH"), _cand("600002.SH"),
        ]))
        strategy = BoardChasingStrategy()
        signals = strategy.evaluate([_position("600001.SH")], _snapshot(), 1_000_000.0)

        buys = _buy_map(signals)
        assert "600001.SH" not in buys
        assert "600002.SH" in buys


# ── 双臂差异：enhanced 过滤 ────────────────────────────────────────────


class TestEnhancedArm:
    def test_base_arm_buys_both_enhanced_and_not(self, monkeypatch):
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        calls, _ = _patch_candidates(monkeypatch, _mk_cands([
            _cand("600002.SH", enhanced=False),
            _cand("600003.SH", enhanced=True),
        ]))
        strategy = BoardChasingStrategy()  # enhanced_filter=False
        signals = strategy.evaluate([], _snapshot(), 1_000_000.0)

        buys = _buy_map(signals)
        assert set(buys) == {"600002.SH", "600003.SH"}
        assert calls[0]["enhanced_filter"] is False

    def test_enhanced_arm_buys_only_enhanced_subset(self, monkeypatch):
        """enhanced 版只对 enhanced=True 子集发 BUY（本地双保险过滤）."""
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        calls, _ = _patch_candidates(monkeypatch, _mk_cands([
            _cand("600002.SH", enhanced=False),
            _cand("600003.SH", enhanced=True),
        ]))
        strategy = BoardChasingStrategy(enhanced_filter=True)
        signals = strategy.evaluate([], _snapshot(), 1_000_000.0)

        buys = _buy_map(signals)
        assert set(buys) == {"600003.SH"}
        assert calls[0]["enhanced_filter"] is True

    def test_enhanced_arm_reason_marks_flag(self, monkeypatch):
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        _patch_candidates(monkeypatch, _mk_cands([
            _cand("600003.SH", enhanced=True),
        ]))
        strategy = BoardChasingStrategy(enhanced_filter=True)
        signals = strategy.evaluate([], _snapshot(), 1_000_000.0)

        assert "enhanced=是" in _buy_map(signals)["600003.SH"].signal_reason


# ── 数据缺失防线 ───────────────────────────────────────────────────────


class TestDataGuards:
    def test_build_candidates_raises_returns_empty_no_raise(self, monkeypatch):
        """build_candidates 抛异常 → evaluate 返回 [] 不抛（不炸 run_day）."""
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        _patch_candidates(monkeypatch, RuntimeError("limit_pool 读取失败"))
        strategy = BoardChasingStrategy()
        signals = strategy.evaluate([], _snapshot(), 1_000_000.0)
        assert signals == []

    def test_build_candidates_raises_with_position_still_no_raise(self, monkeypatch):
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        _patch_candidates(monkeypatch, RuntimeError("db locked"))
        strategy = BoardChasingStrategy()
        signals = strategy.evaluate([_position()], _snapshot(), 1_000_000.0)
        assert signals == []

    def test_empty_candidates_sells_positions_only(self, monkeypatch):
        """空帧 → 仅持仓 SELL，无 BUY."""
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        _patch_candidates(monkeypatch, _mk_cands([]))
        strategy = BoardChasingStrategy()
        signals = strategy.evaluate([_position()], _snapshot(), 1_000_000.0)

        sells = _sell_map(signals)
        assert set(sells) == {"600001.SH"}
        assert sells["600001.SH"].sell_at_open is True
        assert _buy_map(signals) == {}

    def test_empty_candidates_no_positions_no_signals(self, monkeypatch):
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        _patch_candidates(monkeypatch, _mk_cands([]))
        strategy = BoardChasingStrategy()
        assert strategy.evaluate([], _snapshot(), 1_000_000.0) == []


# ── 参数传递：date / conn / 短生命周期 ─────────────────────────────────


class TestWiring:
    def test_date_and_conn_passed_to_build_candidates(self, monkeypatch):
        """snapshot.trade_date 透传 + conn 来自 limitup.db.connect()."""
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        calls, fake_conn = _patch_candidates(
            monkeypatch, _mk_cands([_cand("600002.SH")])
        )
        strategy = BoardChasingStrategy()
        strategy.evaluate([], _snapshot("20260818"), 1_000_000.0)

        assert len(calls) == 1
        assert calls[0]["date"] == "20260818"
        assert calls[0]["conn"] is fake_conn

    def test_conn_closed_after_evaluate(self, monkeypatch):
        """conn 短生命周期：evaluate 结束后恰好关闭一次."""
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        _, fake_conn = _patch_candidates(
            monkeypatch, _mk_cands([_cand("600002.SH")])
        )
        strategy = BoardChasingStrategy()
        strategy.evaluate([], _snapshot(), 1_000_000.0)
        assert fake_conn.closed == 1

    def test_conn_closed_even_when_build_raises(self, monkeypatch):
        """异常路径也走 try/finally close."""
        from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

        _, fake_conn = _patch_candidates(monkeypatch, RuntimeError("boom"))
        strategy = BoardChasingStrategy()
        strategy.evaluate([], _snapshot(), 1_000_000.0)
        assert fake_conn.closed == 1


def test_required_codes_hook_and_cache(monkeypatch) -> None:
    """required_codes 申报候选代码 + 当日缓存（evaluate 复用不再重算）."""
    calls: list[str] = []

    def fake_build(conn, date, *, enhanced_filter=False, lookback_days=60):
        calls.append(date)
        return pd.DataFrame([{
            "ts_code": "600572.SH", "name": "康恩贝", "sector": "中药",
            "pattern_label": "突破型", "seal_ratio": 0.009, "封档": "弱",
            "first_seal_band": "尾盘", "broken_count": 1, "lg_sell_share": 0.34,
            "enhanced": False, "fill_prob": 0.70,
        }])

    monkeypatch.setattr(
        "davis_analyzer.limitup.candidates.build_candidates", fake_build)
    monkeypatch.setattr(
        "davis_analyzer.limitup.db.connect",
        lambda: __import__("sqlite3").connect(":memory:"))

    from davis_analyzer.paper_trading.strategy import BoardChasingStrategy

    strat = BoardChasingStrategy()
    assert strat.required_codes("20260812") == ["600572.SH"]
    assert strat.required_codes("20260812") == ["600572.SH"]
    assert len(calls) == 1  # 当日缓存
    # 异常安全
    strat2 = BoardChasingStrategy()
    monkeypatch.setattr(
        "davis_analyzer.limitup.candidates.build_candidates",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    assert strat2.required_codes("20260813") == []
