"""Conflict arbitration — hardcoded precedence ladder.

The LLM NEVER arbitrates signal conflicts. All conflict resolution is
done by hardcoded rules here, preventing LLM hallucination from making
wrong trading decisions.

Precedence (highest → lowest): hard_stop > thesis_broken > target_reached > agreement
"""

from __future__ import annotations

from dataclasses import dataclass

from stockhot.advisor.signal_aggregator import AggregatedSignals

PRECEDENCE_LADDER = ["hard_stop", "thesis_broken", "target_reached", "agreement"]

_TECH_STRONG = 60.0
_TECH_WEAK = 40.0
_DAVIS_STRONG = 70.0


@dataclass
class ScenarioClassification:
    scenario: str
    description: str
    is_conflict: bool


@dataclass
class ArbitrationResult:
    primary_signal: str
    action: str
    reasoning: str
    conflict_notes: str
    scenario: str


def _is_triggered(sell_signals: list[dict], signal_type: str) -> bool:
    for s in sell_signals:
        if s.get("signal_type") == signal_type:
            return bool(s.get("triggered", False))
    return False


def _any_triggered(sell_signals: list[dict]) -> list[str]:
    return [
        s["signal_type"]
        for s in sell_signals
        if s.get("triggered", False)
    ]


def classify_scenario(aggregated: AggregatedSignals) -> ScenarioClassification:
    tech_score = float(aggregated.technical.value) if aggregated.technical.value != "" else 50.0
    davis_score = float(aggregated.davis.value) if aggregated.davis.value != "" else 50.0

    hard_stop = _is_triggered(aggregated.sell_signals, "hard_stop")
    thesis_broken = _is_triggered(aggregated.sell_signals, "thesis_broken")
    target_reached = _is_triggered(aggregated.sell_signals, "target_reached")

    if target_reached and thesis_broken:
        return ScenarioClassification(
            scenario="FULL_EXIT",
            description="Both target reached and thesis broken — full exit",
            is_conflict=True,
        )

    if hard_stop and not thesis_broken:
        return ScenarioClassification(
            scenario="RISK_EXIT",
            description="Hard stop hit but thesis intact — risk management exit, mark for re-entry",
            is_conflict=True,
        )

    if davis_score >= _DAVIS_STRONG and target_reached:
        return ScenarioClassification(
            scenario="TRIM_TAKE_PROFIT",
            description="Strong fundamentals but target reached — take profit",
            is_conflict=True,
        )

    if tech_score >= _TECH_STRONG and thesis_broken:
        return ScenarioClassification(
            scenario="EXIT_THESIS_INVALIDATED",
            description="Technical still strong but thesis broken — exit before further decline",
            is_conflict=True,
        )

    if tech_score < _TECH_WEAK and davis_score >= _DAVIS_STRONG and not _any_triggered(aggregated.sell_signals):
        return ScenarioClassification(
            scenario="VALUE_ENTRY",
            description="Technical weakness + fundamental strength = value entry opportunity",
            is_conflict=False,
        )

    return ScenarioClassification(
        scenario="NEUTRAL",
        description="No significant signals — hold or no action",
        is_conflict=False,
    )


def arbitrate(aggregated: AggregatedSignals) -> ArbitrationResult:
    scenario = classify_scenario(aggregated)
    triggered_types = _any_triggered(aggregated.sell_signals)

    hard_stop = _is_triggered(aggregated.sell_signals, "hard_stop")
    thesis_broken = _is_triggered(aggregated.sell_signals, "thesis_broken")
    target_reached = _is_triggered(aggregated.sell_signals, "target_reached")

    overridden: list[str] = []

    if hard_stop:
        for t in triggered_types:
            if t != "hard_stop":
                overridden.append(t)
        return ArbitrationResult(
            primary_signal="hard_stop",
            action="EXIT",
            reasoning=f"Hard stop-loss triggered — immediate risk-management exit",
            conflict_notes=f"Overridden signals: {', '.join(overridden)}" if overridden else "",
            scenario=scenario.scenario,
        )

    if thesis_broken:
        for t in triggered_types:
            if t != "thesis_broken":
                overridden.append(t)
        return ArbitrationResult(
            primary_signal="thesis_broken",
            action="EXIT",
            reasoning=f"Investment thesis broken — exit before further decline",
            conflict_notes=f"Overridden signals: {', '.join(overridden)}" if overridden else "",
            scenario=scenario.scenario,
        )

    if target_reached:
        for t in triggered_types:
            if t != "target_reached":
                overridden.append(t)
        return ArbitrationResult(
            primary_signal="target_reached",
            action="TRIM",
            reasoning=f"Target price reached — take partial profit",
            conflict_notes=f"Overridden signals: {', '.join(overridden)}" if overridden else "",
            scenario=scenario.scenario,
        )

    tech_score = float(aggregated.technical.value) if aggregated.technical.value != "" else 50.0
    davis_score = float(aggregated.davis.value) if aggregated.davis.value != "" else 50.0

    if scenario.scenario == "VALUE_ENTRY":
        return ArbitrationResult(
            primary_signal="agreement",
            action="BUY",
            reasoning=f"Value entry: technical weak ({tech_score:.1f}) + davis strong ({davis_score:.1f})",
            conflict_notes="",
            scenario=scenario.scenario,
        )

    if tech_score >= _TECH_STRONG and davis_score >= _DAVIS_STRONG and not triggered_types:
        has_holding = len(aggregated.sell_signals) > 0
        return ArbitrationResult(
            primary_signal="agreement",
            action="HOLD" if has_holding else "BUY",
            reasoning=f"Technical strong ({tech_score:.1f}) + davis strong ({davis_score:.1f}) — all aligned",
            conflict_notes="",
            scenario=scenario.scenario,
        )

    return ArbitrationResult(
        primary_signal="none",
        action="HOLD",
        reasoning=f"No actionable signals — technical={tech_score:.1f}, davis={davis_score:.1f}",
        conflict_notes="",
        scenario=scenario.scenario,
    )
