# davis_analyzer/tests/test_cardgen_materialize.py
"""$fact 递归物化与 spec digest。"""
import hashlib
import json
from decimal import Decimal

from davis_analyzer.cardgen.materialize import materialize_spec, spec_digest
from davis_analyzer.cardgen.types import Fact


def _f(fid: str, display: str) -> Fact:
    return Fact(id=fid, value=Decimal("1"), unit="", display=display,
                as_of="2026-08-28", source_kind="report", source_ref="docs/x.md")


class TestMaterialize:
    def test_nested_replacement(self):
        spec = {"cards": [{"type": "bars", "bars": [{"label": "🟢 沐曦",
                    "value": {"$fact": "ps"}, "pct": 100}]}]}
        out, errs = materialize_spec(spec, {"ps": _f("ps", "143x")})
        assert errs == []
        assert out["cards"][0]["bars"][0]["value"] == "143x"

    def test_multiple_refs(self):
        spec = {"a": {"$fact": "x"}, "b": [{"$fact": "y"}, "plain"]}
        out, errs = materialize_spec(spec, {"x": _f("x", "1"), "y": _f("y", "2")})
        assert out == {"a": "1", "b": ["2", "plain"]} and errs == []

    def test_unknown_id_fails_with_path(self):
        spec = {"cards": [{"name": "04_估值断层", "bars": [{"value": {"$fact": "nope"}}]}]}
        out, errs = materialize_spec(spec, {})
        assert out is None and len(errs) == 1
        assert errs[0].gate == "materialize"
        assert "cards[0]" in errs[0].field and "nope" in errs[0].detail

    def test_input_not_mutated(self):
        spec = {"v": {"$fact": "ps"}}
        materialize_spec(spec, {"ps": _f("ps", "143x")})
        assert spec == {"v": {"$fact": "ps"}}

    def test_fact_dict_with_extra_keys_not_replaced(self):
        # 仅当 dict 的键集恰为 {"$fact"} 才替换;混入其他键原样保留
        spec = {"v": {"$fact": "ps", "note": "x"}}
        out, errs = materialize_spec(spec, {"ps": _f("ps", "143x")})
        assert out == spec and errs == []


class TestSpecDigest:
    def test_digest_changes_with_content(self):
        a = spec_digest({"x": 1})
        b = spec_digest({"x": 2})
        assert a != b and a.startswith("sha256:")
