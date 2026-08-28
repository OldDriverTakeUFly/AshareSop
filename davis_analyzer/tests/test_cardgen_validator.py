# davis_analyzer/tests/test_cardgen_validator.py
"""四闸编排:集成级,用 tmp_path 组装最小工程。"""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from davis_analyzer.cardgen.validator import run_validation


def _write(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _fact(fid: str, value: str, unit: str, display: str) -> dict:
    return {"id": fid, "value": value, "unit": unit, "display": display,
            "as_of": "2026-08-28", "source": {"kind": "report", "ref": "docs/x.md#a"}}


GOOD_FOOT = "数据来源:Tushare · 仅供研究参考,不构成投资建议"


def _spec(fact_ref: bool = True, narrative: str = "营收17.36亿") -> dict:
    return {"cards": [
        {"type": "cover", "name": "01_封面", "title": "测试封面",
         "stats": [{"v": {"$fact": "rev"} if fact_ref else "17.36亿", "k": "H1营收"}],
         "foot": GOOD_FOOT},
        {"type": "summary", "name": "02_结论", "title": "结论",
         "rows": [{"desc": narrative}], "foot": GOOD_FOOT},
    ]}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _write(tmp_path / "facts.json", {"facts": [_fact("rev", "17.36", "亿", "17.36亿")]})
    return tmp_path


class TestHappyPath:
    def test_pass(self, project: Path):
        _write(project / "cards.spec.json", _spec())
        r = run_validation(project, topic="T")
        assert r.passed, r.failures
        assert r.as_of == "2026-08-28" and r.expires_at == "2026-09-04"
        assert r.facts_digest.startswith("sha256:")


class TestGateFailures:
    def test_facts_selfcheck(self, project: Path):
        _write(project / "facts.json", {"facts": [_fact("rev", "99", "亿", "17.36亿")]})
        _write(project / "cards.spec.json", _spec())
        r = run_validation(project)
        assert not r.passed and any(f.gate == "facts" for f in r.failures)

    def test_materialize_unknown_id(self, project: Path):
        _write(project / "cards.spec.json", _spec())
        (project / "facts.json").write_text(json.dumps({"facts": []}, ensure_ascii=False), encoding="utf-8")
        r = run_validation(project)
        assert any(f.gate == "materialize" for f in r.failures)

    def test_numbers_unregistered_in_narrative(self, project: Path):
        _write(project / "cards.spec.json", _spec(narrative="毛利率30.4%垫底"))
        r = run_validation(project)
        bad = [f for f in r.failures if f.gate == "numbers"]
        assert bad and "30.4" in bad[0].detail and bad[0].card == "02_结论"

    def test_compliance_sensitive_word(self, project: Path):
        spec = _spec(narrative="现在可以抄底了")
        _write(project / "cards.spec.json", spec)
        r = run_validation(project)
        assert any(f.gate == "compliance" for f in r.failures)

    def test_completeness_no_cover(self, project: Path):
        spec = _spec()
        spec["cards"][0]["type"] = "table"
        _write(project / "cards.spec.json", spec)
        r = run_validation(project)
        assert any(f.gate == "completeness" and "封面" in f.detail for f in r.failures)

    def test_empty_facts_fails(self, project: Path):
        (project / "facts.json").write_text(json.dumps({"facts": []}, ensure_ascii=False), encoding="utf-8")
        _write(project / "cards.spec.json", _spec(fact_ref=False, narrative="无数字"))
        r = run_validation(project)
        assert not r.passed and any("事实" in f.detail for f in r.failures)
