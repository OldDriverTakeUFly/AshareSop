# davis_analyzer/tests/test_cardgen_compliance.py
"""合规扫描:敏感词、必备免责、来源标注、waivers 豁免。"""
import json
from pathlib import Path

from davis_analyzer.cardgen.compliance import (
    REQUIRED_PHRASES, iter_content_strings, load_waivers, scan_compliance,
)


def _card(name: str, typ: str, foot: str, **extra) -> dict:
    c = {"type": typ, "name": name, "title": f"{name}标题", "foot": foot}
    c.update(extra)
    return c


GOOD_FOOT = "数据来源:Tushare · 仅供研究参考,不构成投资建议"


class TestIterContentStrings:
    def test_skips_non_content_keys(self):
        card = {"type": "cover", "name": "01", "theme": "cream", "tag_color": "#ff2442",
                "title": "标题", "stats": [{"v": "4家", "k": "四家"}]}
        pairs = [s for s, _ in iter_content_strings(card, "cards[0]")]
        assert "标题" in pairs and "4家" in pairs
        assert not any("ff2442" in s or "cream" in s for s in pairs)

    def test_paths_reported(self):
        card = {"type": "cover", "name": "01", "title": "T"}
        paths = [p for _, p in iter_content_strings(card, "cards[0]")]
        assert "cards[0].title" in paths


class TestScanCompliance:
    def test_clean_deck_passes(self):
        cards = [_card("01_封面", "cover", GOOD_FOOT), _card("06_结论", "summary", GOOD_FOOT)]
        assert scan_compliance(cards, waivers=[]) == []

    def test_sensitive_word_hit(self):
        cards = [_card("01", "cover", GOOD_FOOT), _card("06", "summary", GOOD_FOOT)]
        cards[0]["title"] = "现在可以抄底了吗"
        failures = scan_compliance(cards, waivers=[])
        assert any(f.gate == "compliance" and "抄底" in f.detail for f in failures)

    def test_negation_context_via_waiver(self):
        cards = [_card("01", "cover", GOOD_FOOT),
                 _card("06", "summary", GOOD_FOOT, title="非目标价声明")]
        waivers = [{"word": "目标价", "card": "06", "reason": "否定语境「非目标价」"}]
        assert scan_compliance(cards, waivers=waivers) == []

    def test_waiver_wrong_card_still_fails(self):
        cards = [_card("01", "cover", GOOD_FOOT),
                 _card("06", "summary", GOOD_FOOT, title="非目标价声明")]
        waivers = [{"word": "目标价", "card": "03", "reason": "登记错卡片"}]
        assert scan_compliance(cards, waivers=waivers) != []

    def test_missing_disclaimer_on_last_card(self):
        cards = [_card("01", "cover", GOOD_FOOT),
                 _card("06", "summary", "数据来源:Tushare")]  # 缺免责
        failures = scan_compliance(cards, waivers=[])
        assert any("不构成投资建议" in f.detail for f in failures)

    def test_missing_source_in_foot(self):
        cards = [_card("01", "cover", "仅供参考,不构成投资建议"),
                 _card("06", "summary", GOOD_FOOT)]
        failures = scan_compliance(cards, waivers=[])
        assert any("数据来源" in f.detail or "来源" in f.detail for f in failures)


class TestLoaders:
    def test_load_waivers_missing_file(self, tmp_path: Path):
        assert load_waivers(tmp_path) == []

    def test_load_waivers_roundtrip(self, tmp_path: Path):
        (tmp_path / "compliance_waivers.json").write_text(
            json.dumps([{"word": "目标价", "card": "06", "reason": "否定语境"}]), encoding="utf-8")
        assert load_waivers(tmp_path)[0]["word"] == "目标价"

    def test_required_constant(self):
        assert REQUIRED_PHRASES == ("不构成投资建议",)
