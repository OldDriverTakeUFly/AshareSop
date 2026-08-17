"""tournament 包骨架、配置与冻结常量测试。"""

from __future__ import annotations

from davis_analyzer import config, constants
from davis_analyzer.tournament.cli import main


def test_package_importable() -> None:
    import davis_analyzer.tournament  # noqa: F401


def test_reports_dir_created() -> None:
    assert config.TOURNAMENT_REPORTS_DIR.exists()
    assert config.TOURNAMENT_REPORTS_DIR.is_dir()


def test_frozen_constants_present() -> None:
    assert constants.TOURNAMENT_EVAL_STEP_DAYS == 63
    assert constants.TOURNAMENT_MIN_WINDOW_DAYS == 40
    assert constants.TOURNAMENT_MIN_TRADES == 10
    assert constants.TOURNAMENT_TRAILING_WINDOWS == 4
    assert constants.TOURNAMENT_TRAILING_HALF_LIFE == 2.0
    assert constants.TOURNAMENT_DRAWDOWN_PENALTY == 0.1
    assert constants.TOURNAMENT_COMPOSITE_WEIGHTS == {"trailing": 0.6, "regime_match": 0.4}
    assert constants.TOURNAMENT_ALLOCATOR_TAU == 0.5
    assert constants.TOURNAMENT_WEIGHT_BOUNDS == (0.05, 0.50)
    assert constants.TOURNAMENT_SEGMENTS_N == 10
    assert constants.TOURNAMENT_SEGMENTS_K == 3
    assert constants.TOURNAMENT_EMBARGO_DAYS == 5
    assert constants.TOURNAMENT_SEGMENT_DRAWS == 20
    assert constants.TOURNAMENT_PROMO_WIN_RATE == 0.65
    assert constants.TOURNAMENT_PROMO_MEDIAN_MIN == 0.0
    assert constants.TOURNAMENT_PROMO_P25_MIN == -1.0
    assert constants.TOURNAMENT_PERTURB_PCT == 0.20
    assert constants.TOURNAMENT_PERTURB_MAX_DECAY == 0.30
    assert constants.TOURNAMENT_POPULATION == 16
    assert constants.TOURNAMENT_GENERATIONS == 10
    assert constants.TOURNAMENT_MUTATION_SIGMA == 0.15
    assert constants.TOURNAMENT_SURVIVAL_FRAC == 0.25
    assert constants.TOURNAMENT_CAMPAIGNS_PER_YEAR == 4
    assert constants.TOURNAMENT_FINALS_WINDOW_DAYS == 378
    assert constants.TOURNAMENT_CHAMPION_SLOTS == 2
    assert constants.TOURNAMENT_DAVIS_PRESETS["davis_momentum_tilt"]["momentum_weight"] == 0.45
    assert constants.CHAMPION_PRESETS == {}


def test_cli_list_smoke(capsys) -> None:
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "参赛者" in out
