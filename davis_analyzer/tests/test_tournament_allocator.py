"""allocator 权重分配测试（冻结初值 τ=0.5, bounds [0.05,0.50]）。"""

from __future__ import annotations

import pytest

from davis_analyzer.tournament.allocator import allocate


def test_three_valid_participants() -> None:
    w = allocate({"A": 3.0, "B": 1.0, "C": 0.0})
    assert w["A"] == pytest.approx(0.8333, abs=1e-3)
    assert w["B"] == pytest.approx(0.0833, abs=1e-3)
    assert w["C"] == pytest.approx(0.0833, abs=1e-3)
    assert sum(w.values()) == pytest.approx(1.0)


def test_na_participant_gets_floor() -> None:
    w = allocate({"A": 3.0, "B": 1.0, "C": 0.0, "D": None})
    assert w["D"] == pytest.approx(0.05)
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["A"] == pytest.approx(0.7917, abs=1e-3)


def test_all_na_uniform() -> None:
    w = allocate({"A": None, "B": None})
    assert w["A"] == pytest.approx(0.5)
