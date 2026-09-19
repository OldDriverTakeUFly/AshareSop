"""Genome declarations — the ONLY channel evolution can touch (spec D8).

Parameters not declared in a Genome are structurally unreachable by the
parameter channel: the adapter validates every incoming key against its
Genome and raises on anything undeclared (logic can never ride along).
"""

from __future__ import annotations

from dataclasses import dataclass


# ── parameter specification ──


@dataclass(frozen=True)
class ParamSpec:
    """One tunable parameter: name, bounds and kind.

    kind: "weight" (0-1 factor weight, normalised by the engine later),
    "float" (bounded continuous), "choice" (discrete allowed values).
    """

    name: str
    lo: float
    hi: float
    kind: str = "float"
    choices: list[float] | None = None  # required when kind == "choice"


class Genome:
    """Immutable set of declared tunable parameters for one participant."""

    def __init__(self, specs: list[ParamSpec]) -> None:
        self._specs = {s.name: s for s in specs}
        if len(self._specs) != len(specs):
            raise ValueError("duplicate ParamSpec names")

    def names(self) -> list[str]:
        return list(self._specs.keys())

    def bounds(self) -> dict[str, tuple[float, float]]:
        return {n: (s.lo, s.hi) for n, s in self._specs.items()}

    def spec(self, name: str) -> ParamSpec:
        return self._specs[name]

    def validate(self, params: dict[str, float | int]) -> None:
        """Raise KeyError for undeclared keys, ValueError for bad values."""
        for name, value in params.items():
            if name not in self._specs:
                raise KeyError(
                    f"undeclared parameter {name!r} — logic structure is "
                    f"frozen; declare it in the Genome first (spec D8)"
                )
            spec = self._specs[name]
            v = float(value)
            if spec.kind == "choice":
                if spec.choices is None or v not in [float(c) for c in spec.choices]:
                    raise ValueError(f"{name}={value} not in choices {spec.choices}")
            elif not (spec.lo <= v <= spec.hi):
                raise ValueError(f"{name}={value} outside [{spec.lo}, {spec.hi}]")


# ── davis participant genome (frozen v1) ──


DAVIS_GENOME = Genome(
    [
        ParamSpec("momentum_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("valuation_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("prosperity_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("distress_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("northbound_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("research_weight", 0.0, 1.0, kind="weight"),
        ParamSpec("top_n", 5, 20, kind="choice", choices=[5, 10, 15, 20]),
        ParamSpec("frequency", 5, 20, kind="choice", choices=[5, 10, 20]),
    ]
)


# ── board-chasing genome (frozen v1, spec Phase 4) ──
# 仅开放 max_positions；形态/regime/成交概率阈值全部冻结（limitup 先验）

BOARD_CHASING_GENOME = Genome(
    [
        ParamSpec("max_positions", 1, 5, kind="choice", choices=[1, 2, 3, 4, 5]),
    ]
)


# ── six-vein genome (frozen v1) ──
# 仅开放 max_positions；六脉信号参数（3/5/8/13 斐波那契族）全部冻结——
# 原教旨复刻同花顺「浩坚六脉神剑」，防止进化把指标结构洗成另一个策略

SIX_VEIN_GENOME = Genome(
    [
        ParamSpec("max_positions", 1, 5, kind="choice", choices=[1, 2, 3, 4, 5]),
    ]
)
