# davis_analyzer/tests/test_cardgen_builder.py
"""builder:物化落盘→card_factory 渲染→张数检查→RELEASE。渲染子进程真实执行(node 缺失时跳过)。"""
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from davis_analyzer.cardgen import ledger
from davis_analyzer.cardgen.builder import load_release, render
from davis_analyzer.cardgen.validator import run_validation

NODE = shutil.which("node")

GOOD_FOOT = "数据来源:Tushare · 仅供研究参考,不构成投资建议"


def _fact(fid: str, value: str, unit: str, display: str) -> dict:
    return {"id": fid, "value": value, "unit": unit, "display": display,
            "as_of": "2026-08-28", "source": {"kind": "report", "ref": "docs/x.md#a"}}


SPEC = {"cards": [
    {"type": "cover", "name": "01_封面", "title": "验收烟测", "tag_top": "验收",
     "stats": [{"v": {"$fact": "v1"}, "k": "指标"}], "foot": GOOD_FOOT},
    {"type": "summary", "name": "02_结论", "title": "结论", "tag_top": "验收",
     "rows": [{"desc": "指标 17.36亿"}], "foot": GOOD_FOOT},
]}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "facts.json").write_text(
        json.dumps({"facts": [_fact("v1", "17.36", "亿", "17.36亿")]}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "cards.spec.json").write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
    return tmp_path


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = ledger.connect(tmp_path / "cards.db")
    yield c
    c.close()


class TestRenderFailFast:
    def test_validation_gate_blocks_render(self, project: Path, conn, capsys):
        (project / "cards.spec.json").write_text(
            json.dumps({"cards": [{"type": "cover", "name": "01", "title": "x",
                                   "rows": [], "foot": ""}]}, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(SystemExit):
            render(project, "T", conn)
        assert run_validation(project).passed is False


@pytest.mark.skipif(NODE is None, reason="需要 node(playwright)环境")
class TestRenderIntegration:
    def test_full_render(self, project: Path, conn):
        release = render(project, "烟测卡片", conn)
        out = project / "output"
        assert (out / "cards.html").exists()
        assert (out / "spec.materialized.json").exists()
        assert _png_count_static(out) == 2
        assert release["topic"] == "烟测卡片"
        assert release["expires_at"] == "2026-09-04"
        assert release["validate"]["passed"] is True
        assert len(release["images"]) == 2
        assert release["images"][0].startswith("output/")  # 契约:images 为相对 project_dir 的路径
        assert ledger.get_card(conn, "烟测卡片")["status"] == "rendered"

    def test_change_requires_bump_reason(self, project: Path, conn):
        render(project, "烟测卡片", conn)
        (project / "facts.json").write_text(
            json.dumps({"facts": [_fact("v1", "18.0", "亿", "18.0亿")]}, ensure_ascii=False), encoding="utf-8")
        (project / "cards.spec.json").write_text(
            json.dumps(SPEC, ensure_ascii=False).replace("17.36亿", "18.0亿"), encoding="utf-8")
        with pytest.raises(SystemExit, match="bump"):
            render(project, "烟测卡片", conn)
        render(project, "烟测卡片", conn, bump=True, reason="数据更新")
        assert ledger.get_card(conn, "烟测卡片")["current_version"] == 2


def _png_count_static(out: Path) -> int:
    return len(list(out.glob("*.png")))


class TestLoadRelease:
    def test_roundtrip(self, project: Path, conn):
        if NODE is None:
            pytest.skip("需要 node")
        render(project, "T", conn)
        assert load_release(project)["topic"] == "T"
