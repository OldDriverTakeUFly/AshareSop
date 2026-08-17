"""genome 基因声明与参数验证测试。"""

from __future__ import annotations

import pytest

from davis_analyzer.tournament.genome import DAVIS_GENOME, Genome, ParamSpec


def _g() -> Genome:
    return Genome([
        ParamSpec("momentum_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("top_n", 5, 20, kind="choice", choices=[5, 10, 15, 20]),
    ])


def test_validate_accepts_declared() -> None:
    _g().validate({"momentum_weight": 0.4, "top_n": 10})  # no raise


def test_validate_rejects_undeclared_key() -> None:
    with pytest.raises(KeyError, match="logic_change"):
        _g().validate({"momentum_weight": 0.4, "logic_change": 1})


def test_validate_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="momentum_weight"):
        _g().validate({"momentum_weight": 1.5, "top_n": 10})


def test_validate_rejects_bad_choice() -> None:
    with pytest.raises(ValueError, match="top_n"):
        _g().validate({"momentum_weight": 0.4, "top_n": 7})


def test_davis_genome_covers_factor_and_engine_knobs() -> None:
    names = set(DAVIS_GENOME.names())
    assert {"momentum_weight", "valuation_weight", "prosperity_weight",
            "distress_weight", "northbound_weight", "research_weight",
            "top_n", "frequency"} <= names
