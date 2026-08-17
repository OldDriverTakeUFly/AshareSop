"""evolution 战役循环与晋升门槛测试。"""

from __future__ import annotations

import pytest

from davis_analyzer.tournament.evolution import (
    check_promotion,
    improvement_distribution,
    perturb_decay,
    run_campaign,
)


def _mutate(params, rng):
    return {k: v + rng.gauss(0, 0.05) for k, v in params.items()}


def test_campaign_converges_toward_optimum() -> None:
    # 适应度只认 momentum_weight→0.8；初始 0.2，进化应显著逼近
    score_fn = lambda params, ranges: 1.0 - abs(params["momentum_weight"] - 0.8)  # noqa: E731
    best, best_score = run_campaign(
        {"momentum_weight": 0.2}, _mutate, score_fn,
        selection_ranges=[("s1", "e1")], seed=3,
    )
    assert best["momentum_weight"] > 0.5
    assert best_score > 0.7


def test_improvement_distribution_signs() -> None:
    score_fn = lambda params, ranges: params["momentum_weight"]  # noqa: E731
    inc = {"momentum_weight": 0.5}
    chall = {"momentum_weight": 0.7}
    splits = [[("v1", "v2")], [("v3", "v4")]]
    diffs = improvement_distribution(score_fn, inc, chall, splits)
    assert diffs == [pytest.approx(0.2), pytest.approx(0.2)]


def test_perturb_decay_ratio() -> None:
    decay = perturb_decay(challenger_score=1.0, perturbed_scores=[0.9, 0.8])
    assert decay == pytest.approx(0.15)  # 1 − mean(0.85)


def test_promotion_gates_truth_table() -> None:
    ok_all = check_promotion([0.5] * 20, decay=0.1, finals_pass=True)
    assert ok_all.ok and not ok_all.reasons
    low_win_rate = check_promotion([1.0] * 10 + [-1.0] * 10, decay=0.1, finals_pass=True)
    assert not low_win_rate.ok and any("胜率" in r for r in low_win_rate.reasons)
    # 坏尾样本须让 ≥25% 的改进落在 P25_MIN=-1.0 之下才能触发该门槛：
    # 原稿 17+3 仅 15% 负值，线性插值 p25=2.0 无法触发，故改为 14+6（胜率
    # 0.70、中位 2.0 仍通过，仅 25 分位 = -3.0 触发，单门独中）
    bad_tail = check_promotion([2.0] * 14 + [-3.0] * 6, decay=0.1, finals_pass=True)
    assert not bad_tail.ok and any("25 分位" in r for r in bad_tail.reasons)
    bad_decay = check_promotion([0.5] * 20, decay=0.5, finals_pass=True)
    assert not bad_decay.ok and any("扰动" in r for r in bad_decay.reasons)
    no_finals = check_promotion([0.5] * 20, decay=0.1, finals_pass=False)
    assert not no_finals.ok and any("决赛" in r for r in no_finals.reasons)
