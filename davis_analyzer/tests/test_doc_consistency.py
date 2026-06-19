"""Consistency checker: validates README.md and SOP.md numbers against constants.py.

The registry below is the MAINTENANCE POINT — when a constant is newly documented
in README/SOP, add it here so future drift is automatically caught. Constants that
do NOT appear in docs should NOT be added (opt-in design prevents false failures).

Run: python -m pytest davis_analyzer/tests/test_doc_consistency.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from davis_analyzer import constants

# ── Doc locations ────────────────────────────────────────────────────────────
_DAVIS_DIR = Path(__file__).resolve().parent.parent
README_PATH = _DAVIS_DIR / "README.md"
SOP_PATH = _DAVIS_DIR / "SOP.md"

# Capture one numeric literal (integer or decimal) as a single group.
_NUM = r"([0-9]+(?:\.[0-9]+)?)"


@pytest.fixture(scope="module")
def docs() -> dict[str, str]:
    """Load the two documentation files once for the whole module."""
    return {
        "readme": README_PATH.read_text(encoding="utf-8"),
        "sop": SOP_PATH.read_text(encoding="utf-8"),
    }


# ── Anchor-regex extraction helpers ──────────────────────────────────────────
def _scalar_assignments(text: str, name: str) -> list[float]:
    """All numeric occurrences of the ``NAME = value`` form in *text*."""
    pattern = rf"{name}\s*=\s*{_NUM}"
    return [float(m) for m in re.findall(pattern, text)]


def _scalar_paren(text: str, name: str) -> list[float]:
    """All numeric occurrences of the ``NAME（value)`` form in *text*.

    A trailing backtick (markdown inline-code close) is tolerated between the
    name and the parenthesis, e.g. `` `EPS_NEAR_ZERO_THRESHOLD`（0.01） ``.
    """
    pattern = rf"{name}`?\s*[（(]\s*{_NUM}"
    return [float(m) for m in re.findall(pattern, text)]


def _scalar_occurrences(text: str, name: str) -> list[float]:
    """Every numeric occurrence documented next to *name* (``=`` or paren form)."""
    return _scalar_assignments(text, name) + _scalar_paren(text, name)


def _weight_values(text: str, key: str) -> list[float]:
    """Every numeric value documented for a weight *key*.

    Matches both prose/code forms (``key: value`` / ``key：value``) and the SOP
    table form (``key） | value``). Empty when the key is absent from *text*.
    """
    values: list[float] = []
    values.extend(float(m) for m in re.findall(rf"{key}\s*[:：]\s*{_NUM}", text))
    values.extend(float(m) for m in re.findall(rf"{key}[）)]\s*\|\s*{_NUM}", text))
    return values


# ── Opt-in registry: scalar constants documented by name ─────────────────────
# (doc, name, expected). Only constants that appear BY NAME in a doc are listed;
# README documents TUSHARE_RATE_LIMIT / REPORT_MAX_WORDS as bare values only and
# are validated separately in TestReadmeBareValueConstants.
SCALAR_DOC_CASES = [
    pytest.param("readme", "PERCENTILE_DAYS",
                 float(constants.PERCENTILE_DAYS), id="readme-PERCENTILE_DAYS"),
    pytest.param("sop", "PERCENTILE_DAYS",
                 float(constants.PERCENTILE_DAYS), id="sop-PERCENTILE_DAYS"),
    pytest.param("sop", "TUSHARE_RATE_LIMIT",
                 float(constants.TUSHARE_RATE_LIMIT), id="sop-TUSHARE_RATE_LIMIT"),
    pytest.param("sop", "REPORT_MAX_WORDS",
                 float(constants.REPORT_MAX_WORDS), id="sop-REPORT_MAX_WORDS"),
    pytest.param("sop", "PE_PB_TREND_MONTHS",
                 float(constants.PE_PB_TREND_MONTHS), id="sop-PE_PB_TREND_MONTHS"),
    pytest.param("sop", "MIN_TREND_MONTHS",
                 float(constants.MIN_TREND_MONTHS), id="sop-MIN_TREND_MONTHS"),
    pytest.param("sop", "EPS_NEAR_ZERO_THRESHOLD",
                 float(constants.EPS_NEAR_ZERO_THRESHOLD),
                 id="sop-EPS_NEAR_ZERO_THRESHOLD"),
]


@pytest.mark.parametrize("doc_name,name,expected", SCALAR_DOC_CASES)
def test_scalar_constant_by_name(
    docs: dict[str, str], doc_name: str, name: str, expected: float
) -> None:
    """Every documented occurrence of a named scalar equals constants.py."""
    text = docs[doc_name]
    occurrences = _scalar_occurrences(text, name)
    assert occurrences, f"{name} is not documented by name in {doc_name}"
    for value in occurrences:
        assert abs(value - expected) < 1e-9, (
            f"{name} in {doc_name} documented as {value}, expected {expected}"
        )


# ── Opt-in registry: weight dictionaries ─────────────────────────────────────
WEIGHT_DICT_CASES = [
    pytest.param("DAVIS_DOUBLE_WEIGHTS", "readme",
                 constants.DAVIS_DOUBLE_WEIGHTS, id="readme-davis"),
    pytest.param("DAVIS_DOUBLE_WEIGHTS", "sop",
                 constants.DAVIS_DOUBLE_WEIGHTS, id="sop-davis"),
    pytest.param("PROSPERITY_WEIGHTS", "readme",
                 constants.PROSPERITY_WEIGHTS, id="readme-prosperity"),
    pytest.param("PROSPERITY_WEIGHTS", "sop",
                 constants.PROSPERITY_WEIGHTS, id="sop-prosperity"),
]


@pytest.mark.parametrize("label,doc_name,expected", WEIGHT_DICT_CASES)
def test_weight_dictionary(
    docs: dict[str, str], label: str, doc_name: str, expected: dict[str, float]
) -> None:
    """Each documented weight dict exposes the full key set with correct values.

    Asserting the key *set* is the primary guard against dimension regressions
    (e.g. the old 3-dim → 4-dim distress change silently reverting).
    """
    text = docs[doc_name]
    found: dict[str, list[float]] = {}
    for key in expected:
        values = _weight_values(text, key)
        assert values, (
            f"weight key '{key}' of {label} is not documented in {doc_name}"
        )
        found[key] = values

    assert set(found) == set(expected), (
        f"{label} in {doc_name}: documented keys {sorted(found)} "
        f"!= expected {sorted(expected)}"
    )
    for key, values in found.items():
        for value in values:
            assert abs(value - expected[key]) < 1e-9, (
                f"{label}[{key}] in {doc_name} documented as {value}, "
                f"expected {expected[key]}"
            )


class TestReadmeBareValueConstants:
    """README documents two constants as bare values (no constant name)."""

    def test_tushare_rate_limit_value(self, docs: dict[str, str]) -> None:
        expected = int(constants.TUSHARE_RATE_LIMIT)
        assert re.search(rf"{expected}\s*次\s*/\s*分钟", docs["readme"]), (
            f"README should document the Tushare rate limit as "
            f"'{expected}次/分钟'"
        )

    def test_report_max_words_value(self, docs: dict[str, str]) -> None:
        expected = int(constants.REPORT_MAX_WORDS)
        assert re.search(rf"{expected}\s*词", docs["readme"]), (
            f"README should document the report word cap as '{expected} 词'"
        )


@pytest.mark.parametrize("doc_name", ["readme", "sop"])
def test_distress_scoring_described_as_continuous(
    docs: dict[str, str], doc_name: str
) -> None:
    """Docs must describe distress scoring as continuous (not binary hit/miss)."""
    assert re.search(r"连续", docs[doc_name]), (
        f"{doc_name} must describe distress scoring as continuous "
        f"(expected '连续得分' / '连续值')"
    )


class TestConstantsInvariants:
    """Sanity checks on the source-of-truth itself (the docs rely on these)."""

    def test_davis_double_weights_sum_to_one(self) -> None:
        total = sum(constants.DAVIS_DOUBLE_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"DAVIS_DOUBLE_WEIGHTS sum to {total}"

    def test_prosperity_weights_sum_to_one(self) -> None:
        total = sum(constants.PROSPERITY_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"PROSPERITY_WEIGHTS sum to {total}"
