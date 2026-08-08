"""Unit tests for negative_factors — 5 veto rules + concentration helper.

Each rule is tested in both the "fires" (pattern present → vetoed) and
"passes" (clean stock → not vetoed) directions, plus edge cases (exemption,
missing data, threshold boundaries).
"""

from __future__ import annotations

from types import SimpleNamespace

from davis_analyzer.paper_trading.account import Position
from davis_analyzer.paper_trading.negative_factors import (
    check_buy,
    check_holding,
    compute_concentration,
)


def _snap(**kw):
    """Build a minimal MarketSnapshot-like object for check_buy."""
    defaults = dict(
        prices={},
        pe_percentile={},
        industries={},
        industry_trend={},
        volume_signal={},
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# ── Rule ①: 风口追高 (topchase) ───────────────────────────────────────


class TestTopchaseRule:
    def test_high_pe_high_momentum_vetoed(self):
        snap = _snap(pe_percentile={"X": 90})
        r = check_buy("X", {"momentum": 85}, snap)
        assert r.vetoed and r.rule == "topchase"

    def test_low_pe_not_vetoed(self):
        snap = _snap(pe_percentile={"X": 50})
        r = check_buy("X", {"momentum": 90}, snap)
        assert not r.vetoed  # PE low — not a top-chase

    def test_low_momentum_not_vetoed(self):
        snap = _snap(pe_percentile={"X": 90})
        r = check_buy("X", {"momentum": 50}, snap)
        assert not r.vetoed  # momentum below threshold

    def test_exempted_by_volume_breakout(self):
        snap = _snap(
            pe_percentile={"X": 90},
            volume_signal={"X": {"signal_type": "platform_breakout"}},
        )
        r = check_buy("X", {"momentum": 90}, snap)
        assert not r.vetoed  # platform breakout exempts

    def test_not_exempted_by_weak_volume(self):
        snap = _snap(
            pe_percentile={"X": 90},
            volume_signal={"X": {"signal_type": "neutral"}},
        )
        r = check_buy("X", {"momentum": 90}, snap)
        assert r.vetoed


# ── Rule ②: 套牢装死 (zombie) ─────────────────────────────────────────


class TestZombieRule:
    def test_old_and_deep_loss_forces_sell(self):
        pos = Position(ts_code="X", name="t", shares=100, avg_cost=100, entry_date="20210101")
        r = check_holding(pos, current_price=50, today="20260101")  # -50%, 5yr
        assert r.vetoed and r.action == "FORCE_SELL"

    def test_old_but_small_loss_not_zombie(self):
        pos = Position(ts_code="X", name="t", shares=100, avg_cost=100, entry_date="20210101")
        r = check_holding(pos, current_price=95, today="20260101")  # -5%, old but not deep
        assert not r.vetoed  # only age isn't enough — need loss too

    def test_deep_loss_but_recent_not_zombie(self):
        pos = Position(ts_code="X", name="t", shares=100, avg_cost=100, entry_date="20260101")
        # -35%, but only ~recent → may still recover. NOTE: rule ④ may fire.
        r = check_holding(pos, current_price=65, today="20260601")
        # -35% is not < -50% absolute stop, and holding < 365 days → not zombie
        assert not r.vetoed


# ── Rule ③: 单一赛道集中 (concentration) ──────────────────────────────


class TestConcentrationRule:
    def test_over_concentrated_vetoed(self):
        snap = _snap()
        conc = {"半导体": 0.6}  # 60% in one industry
        r = check_buy("X", {"momentum": 50}, snap, portfolio_concentration=conc)
        # Need industry match — check_buy uses snapshot.industries[code]
        snap2 = _snap(industries={"X": "半导体"})
        r2 = check_buy("X", {"momentum": 50}, snap2, portfolio_concentration=conc)
        assert r2.vetoed and r2.rule == "concentration"

    def test_balanced_portfolio_not_vetoed(self):
        snap = _snap(industries={"X": "半导体"})
        conc = {"半导体": 0.3, "化工": 0.3, "医药": 0.4}
        r = check_buy("X", {"momentum": 50}, snap, portfolio_concentration=conc)
        assert not r.vetoed

    def test_no_concentration_data_skips_rule(self):
        snap = _snap()
        r = check_buy("X", {"momentum": 50}, snap, portfolio_concentration=None)
        assert not r.vetoed  # no data → skip


# ── Rule ④: 深套绝对止损 (absolute_stop) ──────────────────────────────


class TestAbsoluteStopRule:
    def test_loss_over_50pct_forces_sell(self):
        pos = Position(ts_code="X", name="t", shares=100, avg_cost=100, entry_date="20260101")
        r = check_holding(pos, current_price=40, today="20260601")  # -60%
        assert r.vetoed and r.rule == "absolute_stop" and r.action == "FORCE_SELL"

    def test_loss_under_50pct_not_triggered(self):
        pos = Position(ts_code="X", name="t", shares=100, avg_cost=100, entry_date="20260101")
        r = check_holding(pos, current_price=55, today="20260601")  # -45%
        assert not r.vetoed

    def test_boundary_exactly_50(self):
        pos = Position(ts_code="X", name="t", shares=100, avg_cost=100, entry_date="20260101")
        r = check_holding(pos, current_price=50, today="20260601")  # exactly -50%
        assert r.vetoed  # ≤ threshold fires


# ── Rule ⑤: 周期股 PE 陷阱 (cyclical_pe_trap) ────────────────────────


class TestCyclicalPETrapRule:
    def test_cyclical_low_pe_high_pb_vetoed(self):
        snap = _snap()
        factors = {"momentum": 50, "is_cyclical": True, "pb_percentile": 90}
        # Need pe_percentile in snapshot
        snap2 = _snap(pe_percentile={"X": 20})  # PE low
        r = check_buy("X", {**factors}, snap2)
        assert r.vetoed and r.rule == "cyclical_pe_trap"

    def test_non_cyclical_not_vetoed(self):
        snap = _snap(pe_percentile={"X": 20})
        factors = {"momentum": 50, "is_cyclical": False, "pb_percentile": 90}
        r = check_buy("X", factors, snap)
        assert not r.vetoed

    def test_cyclical_but_pb_low_not_vetoed(self):
        snap = _snap(pe_percentile={"X": 20})
        factors = {"momentum": 50, "is_cyclical": True, "pb_percentile": 40}
        r = check_buy("X", factors, snap)
        assert not r.vetoed  # PB low too → genuinely cheap


# ── compute_concentration helper ──────────────────────────────────────


class TestComputeConcentration:
    def test_single_industry(self):
        positions = [
            Position(ts_code="A", name="a", shares=100, avg_cost=10, entry_date="20260101"),
            Position(ts_code="B", name="b", shares=100, avg_cost=10, entry_date="20260101"),
        ]
        # Attach industry attribute
        positions[0].industry = "半导体"
        positions[1].industry = "半导体"
        prices = {"A": 10, "B": 10}
        conc = compute_concentration(positions, prices)
        assert conc["半导体"] == 1.0  # 100% concentrated

    def test_balanced(self):
        positions = [
            Position(ts_code="A", name="a", shares=100, avg_cost=10, entry_date="20260101"),
            Position(ts_code="B", name="b", shares=100, avg_cost=10, entry_date="20260101"),
        ]
        positions[0].industry = "半导体"
        positions[1].industry = "化工"
        prices = {"A": 10, "B": 10}
        conc = compute_concentration(positions, prices)
        assert conc["半导体"] == 0.5 and conc["化工"] == 0.5

    def test_empty(self):
        assert compute_concentration([], {}) == {}
