"""evolution 随机段（CPCV-lite）与变异测试。"""

from __future__ import annotations

import random
from datetime import date, timedelta

from davis_analyzer.tournament.evolution import draw_segments, mutate, split_finals
from davis_analyzer.tournament.genome import Genome, ParamSpec


def _cal(n: int = 200) -> list[date]:
    d0 = date(2020, 1, 2)
    return [d0 + timedelta(days=i) for i in range(n)]


def test_segments_disjoint_with_embargo() -> None:
    cal = _cal(200)
    splits = draw_segments(cal, n_segments=10, k_validation=3, embargo_days=5,
                           n_draws=10, seed=42)
    assert len(splits) == 10
    for split in splits:
        sel_days = {d for s, e in split.selection for d in _dates_between(cal, s, e)}
        val_days = {d for s, e in split.validation for d in _dates_between(cal, s, e)}
        assert not (sel_days & val_days), "selection/validation must be disjoint"
        # 对称隔离：验证段两端各剔除 embargo，长度 = block − 2×embargo
        for s, e in split.validation:
            assert len(_dates_between(cal, s, e)) == 20 - 2 * 5  # block=200/10, embargo=5


def test_split_finals_tail() -> None:
    cal = _cal(500)
    evolve_cal, finals_cal = split_finals(cal, finals_days=100)
    assert len(finals_cal) == 100
    assert finals_cal[-1] == cal[-1]
    assert evolve_cal[-1] < finals_cal[0]


def test_mutate_respects_bounds_and_choices() -> None:
    genome = Genome([
        ParamSpec("momentum_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("top_n", 5, 20, kind="choice", choices=[5, 10, 15, 20]),
    ])
    rng = random.Random(7)
    params = {"momentum_weight": 0.5, "top_n": 10}
    for _ in range(50):
        mutated = mutate(params, genome, rng)
        assert 0.0 <= mutated["momentum_weight"] <= 1.0
        assert mutated["top_n"] in (5, 10, 15, 20)


def _dates_between(cal: list[date], s: date, e: date) -> list[date]:
    return [d for d in cal if s <= d <= e]
