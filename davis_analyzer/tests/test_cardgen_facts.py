# davis_analyzer/tests/test_cardgen_facts.py
"""facts.json 读写、自检与时效计算。"""
from pathlib import Path

import pytest

from davis_analyzer.cardgen.facts import (
    DEFAULT_TTL_DAYS, check_facts, earliest_expires, effective_expires,
    facts_digest, latest_as_of, load_facts, save_facts,
)
from davis_analyzer.cardgen.types import Fact


def _fact(fid: str = "muxi_ps", value: str = "143", unit: str = "x",
          display: str = "143x", as_of: str = "2026-08-28", **kw) -> Fact:
    d = {"id": fid, "value": value, "unit": unit, "display": display,
         "as_of": as_of, "source_kind": kw.get("source_kind", "report"),
         "source_ref": kw.get("source_ref", "docs/x.md#估值")}
    d.update({k: v for k, v in kw.items() if k not in d})
    return Fact.from_dict(d)


class TestFactDataclass:
    def test_value_is_decimal(self):
        f = _fact(value="17.36", unit="亿", display="17.36亿")
        assert f.value == 17.36 or str(f.value) == "17.36"  # Decimal 数值相等

    def test_roundtrip_dict(self):
        f = _fact()
        assert Fact.from_dict(f.to_dict()) == f


class TestQuotePassthrough:
    """C1 回归:quote 必须透传,防 ingest(load→append→save)静默抹掉溯源引句。"""

    def test_nested_quote_roundtrip_preserved(self):
        d = {"id": "muxi_ps", "value": "143", "unit": "x", "display": "143x",
             "as_of": "2026-08-28",
             "source": {"kind": "report", "ref": "docs/x.md#估值",
                        "quote": "沐曦 PS(TTM) 143x,参照英伟达"}}
        f = Fact.from_dict(d)
        assert f.quote == "沐曦 PS(TTM) 143x,参照英伟达"
        assert Fact.from_dict(f.to_dict()).quote == f.quote  # 二次 round-trip 仍保留

    def test_flat_quote_keys_fallback(self):
        for key in ("quote", "source_quote"):
            d = {"id": "f1", "value": "143", "unit": "x", "display": "143x",
                 "as_of": "2026-08-28",
                 "source": {"kind": "report", "ref": "docs/x.md#a"},
                 key: "平铺回退引句"}
            assert Fact.from_dict(d).quote == "平铺回退引句"

    def test_nested_quote_wins_over_flat(self):
        d = {"id": "f1", "value": "143", "unit": "x", "display": "143x",
             "as_of": "2026-08-28",
             "source": {"kind": "report", "ref": "docs/x.md#a", "quote": "嵌套优先"},
             "quote": "平铺应被忽略"}
        assert Fact.from_dict(d).quote == "嵌套优先"

    def test_no_quote_omits_key(self):
        f = _fact()
        assert "quote" not in f.to_dict()["source"]

    def test_ingest_chain_preserves_quote(self, tmp_path: Path):
        # 模拟 cli cmd_ingest 的 load→append→save 链路:既有 quote 不得丢
        p = tmp_path / "facts.json"
        save_facts(p, [_fact(quote="原文引句:PS 143x")])
        existing = load_facts(p)
        appended = existing + [_fact(fid="b", value="75", unit="%", display="75%",
                                     quote="新增事实引句:75%")]
        save_facts(p, appended)
        reloaded = load_facts(p)
        assert [f.quote for f in reloaded] == ["原文引句:PS 143x", "新增事实引句:75%"]
        assert existing[0].quote == "原文引句:PS 143x"


class TestCheckFacts:
    def test_all_good(self):
        errors = check_facts([_fact(), _fact(fid="a2", value="75", unit="%", display="75%")])
        assert errors == []

    def test_duplicate_id(self):
        errors = check_facts([_fact(), _fact()])
        assert any("重复" in e for e in errors)

    def test_missing_source_ref(self):
        errors = check_facts([_fact(source_ref="")])
        assert any("source" in e for e in errors)

    def test_bad_source_kind(self):
        errors = check_facts([_fact(source_kind="wild")])
        assert any("source_kind" in e for e in errors)

    def test_expires_before_as_of(self):
        errors = check_facts([_fact(expires="2026-08-01")])
        assert any("expires" in e for e in errors)

    def test_display_value_mismatch(self):
        # display "143x" 但 value 登记为 200 → display 与 value 不自洽
        errors = check_facts([_fact(value="200")])
        assert any("display" in e for e in errors)

    def test_display_missing_unit(self):
        # value=143 unit=x 但 display 写成 "143"(缺单位)→ 不自洽
        errors = check_facts([_fact(display="143")])
        assert any("display" in e for e in errors)


class TestRoundtripJson:
    def test_save_load(self, tmp_path: Path):
        p = tmp_path / "facts.json"
        save_facts(p, [_fact()])
        assert load_facts(p) == [_fact()]


class TestExpiry:
    def test_default_ttl(self):
        assert effective_expires(_fact(as_of="2026-08-28")) == "2026-09-04"
        assert DEFAULT_TTL_DAYS == 7

    def test_explicit_expires_kept(self):
        assert effective_expires(_fact(expires="2026-08-31")) == "2026-08-31"

    def test_earliest_expires_takes_min(self):
        facts = [_fact(expires="2026-09-10"),
                 _fact(fid="b", value="75", unit="%", display="75%", expires="2026-08-30")]
        assert earliest_expires(facts) == "2026-08-30"

    def test_latest_as_of(self):
        facts = [_fact(as_of="2026-08-26"),
                 _fact(fid="b", value="75", unit="%", display="75%", as_of="2026-08-28")]
        assert latest_as_of(facts) == "2026-08-28"


class TestDigest:
    def test_digest_stable_regardless_of_order(self):
        a = facts_digest([_fact(), _fact(fid="b", value="75", unit="%", display="75%")])
        b = facts_digest([_fact(fid="b", value="75", unit="%", display="75%"), _fact()])
        assert a == b and a.startswith("sha256:")
