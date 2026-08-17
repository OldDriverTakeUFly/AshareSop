"""Parameter evolution engine (spec §5.6) — logic frozen, params only.

CPCV-lite validation: split history into N sequential blocks, randomly
hold out K as validation with an embargo gap at every boundary, repeat.
Mutation never leaves declared genome bounds (D8).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date

from davis_analyzer.constants import (
    TOURNAMENT_EMBARGO_DAYS,
    TOURNAMENT_FINALS_WINDOW_DAYS,
    TOURNAMENT_MUTATION_SIGMA,
    TOURNAMENT_SEGMENTS_K,
    TOURNAMENT_SEGMENTS_N,
    TOURNAMENT_SEGMENT_DRAWS,
)
from davis_analyzer.tournament.genome import Genome

# 进化种子默认值：与 FactorConfig / BacktestConfig 的字段默认一致（backtest_factors.py
# 权重 6 键 + backtest.py 的 top_n=10 / frequency=5）。空预设参与者（如 davis_balanced）
# 以此补全 incumbent，否则 mutate 跳过缺失键 → 可进化集合为空、进化完全惰性。
DAVIS_SEED_DEFAULTS: dict[str, float] = {
    "momentum_weight": 0.20,
    "valuation_weight": 0.20,
    "prosperity_weight": 0.25,
    "distress_weight": 0.15,
    "northbound_weight": 0.10,
    "research_weight": 0.10,
    "top_n": 10,
    "frequency": 5,
}


@dataclass
class SegmentSplit:
    selection: list[tuple[date, date]] = field(default_factory=list)
    validation: list[tuple[date, date]] = field(default_factory=list)


def _blocks(calendar: list[date], n_segments: int) -> list[list[date]]:
    n = len(calendar)
    size = n // n_segments
    return [calendar[i * size:(i + 1) * size] for i in range(n_segments)]


def draw_segments(
    calendar: list[date],
    n_segments: int = TOURNAMENT_SEGMENTS_N,
    k_validation: int = TOURNAMENT_SEGMENTS_K,
    embargo_days: int = TOURNAMENT_EMBARGO_DAYS,
    n_draws: int = TOURNAMENT_SEGMENT_DRAWS,
    seed: int | None = None,
) -> list[SegmentSplit]:
    rng = random.Random(seed)
    blocks = _blocks(calendar, n_segments)
    splits: list[SegmentSplit] = []
    for _ in range(n_draws):
        val_idx = sorted(rng.sample(range(n_segments), k_validation))
        val_set = set(val_idx)
        validation: list[tuple[date, date]] = []
        for i in val_idx:
            block = blocks[i]
            # 对称 embargo：验证段两端各剔除隔离带（结尾贴近的后续选择段同样需要隔离）
            kept = block[embargo_days:-embargo_days]
            if kept:
                validation.append((kept[0], kept[-1]))
        selection = [
            (blocks[i][0], blocks[i][-1]) for i in range(n_segments) if i not in val_set
        ]
        splits.append(SegmentSplit(selection=selection, validation=validation))
    return splits


def split_finals(
    calendar: list[date], finals_days: int = TOURNAMENT_FINALS_WINDOW_DAYS
) -> tuple[list[date], list[date]]:
    """Reserve the trailing trading days as the one-shot finals window."""
    if len(calendar) <= finals_days:
        raise ValueError("calendar too short for a finals window")
    return calendar[:-finals_days], calendar[-finals_days:]


def mutate(
    params: dict[str, float | int], genome: Genome, rng: random.Random
) -> dict[str, float | int]:
    """Gaussian perturbation, σ = 15% of range; choices snap back."""
    out: dict[str, float | int] = dict(params)
    for name in genome.names():
        if name not in out:
            continue
        spec = genome.spec(name)
        lo, hi = spec.lo, spec.hi
        if spec.kind == "choice":
            if rng.random() < 0.3:  # occasional discrete jump
                picks = [c for c in (spec.choices or []) if c != out[name]]
                if picks:
                    out[name] = rng.choice(picks)
            continue
        sigma = TOURNAMENT_MUTATION_SIGMA * (hi - lo)
        value = float(out[name]) + rng.gauss(0.0, sigma)
        out[name] = min(max(value, lo), hi)
    return out


# ── campaign & promotion gates (spec §5.6) ──

from typing import Callable

from loguru import logger

from davis_analyzer.constants import (
    TOURNAMENT_GENERATIONS,
    TOURNAMENT_PERTURB_MAX_DECAY,
    TOURNAMENT_POPULATION,
    TOURNAMENT_PROMO_MEDIAN_MIN,
    TOURNAMENT_PROMO_P25_MIN,
    TOURNAMENT_PROMO_WIN_RATE,
    TOURNAMENT_SURVIVAL_FRAC,
)

ScoreFn = Callable[[dict[str, float | int], list], float]
MutateFn = Callable[[dict, random.Random], dict]


def run_campaign(
    incumbent: dict[str, float | int],
    mutate_fn: MutateFn,
    score_fn: ScoreFn,
    selection_ranges: list,
    population: int = TOURNAMENT_POPULATION,
    generations: int = TOURNAMENT_GENERATIONS,
    survival_frac: float = TOURNAMENT_SURVIVAL_FRAC,
    seed: int | None = None,
) -> tuple[dict[str, float | int], float]:
    """Mutation-selection loop scored ONLY on the selection set (§5.6)."""
    rng = random.Random(seed)
    pool = [dict(incumbent)]
    best, best_score = dict(incumbent), score_fn(incumbent, selection_ranges)
    for _ in range(generations):
        candidates = list(pool)
        while len(candidates) < population:
            candidates.append(mutate_fn(rng.choice(pool), rng))
        scored = sorted(
            ((score_fn(c, selection_ranges), c) for c in candidates),
            key=lambda x: x[0], reverse=True,
        )
        if scored[0][0] > best_score:
            best_score, best = scored[0][0], dict(scored[0][1])
        keep = max(int(len(scored) * survival_frac), 1)
        pool = [dict(c) for _, c in scored[:keep]]
    return best, best_score


def improvement_distribution(
    score_fn: ScoreFn,
    incumbent: dict[str, float | int],
    challenger: dict[str, float | int],
    validation_ranges_per_split: list[list],
) -> list[float]:
    """(challenger − incumbent) per split, on validation ranges only."""
    out: list[float] = []
    for ranges in validation_ranges_per_split:
        out.append(score_fn(challenger, ranges) - score_fn(incumbent, ranges))
    return out


def perturb_decay(challenger_score: float, perturbed_scores: list[float]) -> float:
    """Performance decay ratio after independent per-parameter perturbation.

    decay = max(0, (base − mean(perturbed)) / (|base| + ε)); the |base|
    denominator keeps the ratio meaningful for negative base scores (base=-1,
    perturbed=-2 → decay=1.0, i.e. a real 100% relative drop, not a pass).
    Any non-finite input (or no perturbation samples) fails closed to +inf.
    """
    values = [challenger_score, *perturbed_scores]
    if not perturbed_scores or not all(math.isfinite(v) for v in values):
        return float("inf")
    mean_perturbed = sum(perturbed_scores) / len(perturbed_scores)
    return max(0.0, (challenger_score - mean_perturbed) / (abs(challenger_score) + 1e-9))


@dataclass
class PromotionDecision:
    ok: bool
    reasons: list[str]


def check_promotion(improvements: list[float], decay: float, finals_pass: bool) -> PromotionDecision:
    """All four gates must hold (frozen thresholds in constants)."""
    import statistics

    reasons: list[str] = []
    if not improvements:
        return PromotionDecision(False, ["无随机段改进样本"])
    # fail-closed：非有限评分（如 score_fn 窗口样本不足返回 -inf）直接拒绝，
    # 不能让 NaN 比较静默通过任何门槛
    if not all(math.isfinite(x) for x in improvements) or not math.isfinite(decay):
        return PromotionDecision(False, ["非有限评分（窗口样本不足）"])
    win_rate = sum(1 for x in improvements if x > 0) / len(improvements)
    if win_rate < TOURNAMENT_PROMO_WIN_RATE:
        reasons.append(f"随机段胜率 {win_rate:.0%} < {TOURNAMENT_PROMO_WIN_RATE:.0%}")
    median = statistics.median(improvements)
    if median <= TOURNAMENT_PROMO_MEDIAN_MIN:
        reasons.append(f"中位改进 {median:.3f} ≤ {TOURNAMENT_PROMO_MEDIAN_MIN}")
    p25 = _percentile(improvements, 25)
    if p25 <= TOURNAMENT_PROMO_P25_MIN:
        reasons.append(f"25 分位改进 {p25:.3f} ≤ {TOURNAMENT_PROMO_P25_MIN}")
    if decay > TOURNAMENT_PERTURB_MAX_DECAY:
        reasons.append(f"扰动衰减 {decay:.0%} > {TOURNAMENT_PERTURB_MAX_DECAY:.0%}")
    if not finals_pass:
        reasons.append("决赛窗口未通过（或已烧尽，需 paper_trading 前向证据）")
    ok = not reasons
    if ok:
        logger.info("promotion gates passed: win_rate={:.0%} median={:.3f} p25={:.3f} decay={:.0%}",
                    win_rate, median, p25, decay)
    return PromotionDecision(ok, reasons)


def _percentile(values: list[float], pct: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * pct / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def build_score_fn(judge, participant: str) -> ScoreFn:
    """Score a parameter set as the mean window_performance over ranges,
    evaluated through the SAME JudgeHarness (spec §5.2 rule 6)."""
    from davis_analyzer.tournament.scorecard import window_performance

    def score_fn(params: dict, ranges: list) -> float:
        perfs: list[float] = []
        for start, end in ranges:
            reports = judge.evaluate_window(start, end, {participant: params})
            r = reports.get(participant)
            if r is not None and r.stats is not None:
                perfs.append(window_performance(r.stats))
        if not perfs:
            return float("-inf")
        return sum(perfs) / len(perfs)

    return score_fn
