# davis_analyzer/cardgen/validator.py
"""四闸编排:facts 自检 → $fact 物化 → 数字全量核对 → 合规 → 完整性。"""
from __future__ import annotations

import json
from pathlib import Path

from davis_analyzer.cardgen.compliance import iter_content_strings, load_waivers, scan_compliance
from davis_analyzer.cardgen.facts import (
    check_facts, earliest_expires, facts_digest, latest_as_of, load_facts,
)
from davis_analyzer.cardgen.materialize import materialize_spec
from davis_analyzer.cardgen.numbers import unmatched_tokens
from davis_analyzer.cardgen.types import Failure, ValidateReport


def load_spec(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_validation(project_dir: Path, topic: str = "") -> ValidateReport:
    report = ValidateReport(topic=topic, passed=False)

    # ── 闸1:事实清单自检 ──
    facts = load_facts(project_dir / "facts.json")
    fact_errors = check_facts(facts)
    for e in fact_errors:
        report.failures.append(Failure("facts", "", "", e))
    if not facts:
        report.failures.append(Failure("facts", "", "facts.json", "事实清单为空:卡片数字必须有溯源"))
    report.facts_digest = facts_digest(facts)
    report.as_of = latest_as_of(facts)
    report.expires_at = earliest_expires(facts)

    # ── 闸2:$fact 物化 ──
    spec = load_spec(project_dir / "cards.spec.json")
    by_id = {f.id: f for f in facts}
    mat, mat_errors = materialize_spec(spec, by_id)
    report.failures.extend(mat_errors)
    if mat is None:
        return report

    cards = mat.get("cards", [])

    # ── 闸3:数字全量核对(逐卡、逐内容字段)──
    for idx, card in enumerate(cards):
        name = str(card.get("name", f"cards[{idx}]"))
        for text, path in iter_content_strings(card, f"cards[{idx}]"):
            for tok in unmatched_tokens(text, facts):
                report.failures.append(Failure(
                    "numbers", name, path, f"未溯源数字: '{tok.raw}'(值 {tok.value} 单位 '{tok.unit}')"))

    # ── 闸4a:合规 ──
    report.failures.extend(scan_compliance(cards, waivers=load_waivers(project_dir)))

    # ── 闸4b:完整性 ──
    if not cards:
        report.failures.append(Failure("completeness", "", "cards", "spec 无卡片"))
    else:
        if cards[0].get("type") != "cover":
            report.failures.append(Failure("completeness", "", "cards[0]", "首卡须为封面(cover)"))
        if cards[-1].get("type") != "summary":
            report.failures.append(Failure("completeness", "", f"cards[{len(cards) - 1}]",
                                           "尾卡须为结论(summary)"))

    report.passed = not report.failures
    return report
