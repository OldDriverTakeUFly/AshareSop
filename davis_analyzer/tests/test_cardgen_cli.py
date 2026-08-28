# davis_analyzer/tests/test_cardgen_cli.py
"""ingest 事实拉取 + CLI 冒烟(init/validate/status/enqueue 命令行)。

台账注入:测试经 CARDGEN_LEDGER_DB 把 content_cards.db 重定向到 tmp,
不触碰真实 storage/database/content_cards.db;工程目录经 CARDGEN_PROJECT_ROOT 重定向。
"""
import json
import sqlite3
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from davis_analyzer.cardgen import ledger
from davis_analyzer.cardgen.cli import main
from davis_analyzer.cardgen.ingest import fetch_daily_basic
from davis_analyzer.cardgen.types import Fact

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(tmp_path / "market_data.db")
    c.execute("CREATE TABLE daily_basic(ts_code TEXT, trade_date TEXT, pe_ttm REAL, pb REAL, ps REAL, total_mv REAL)")
    c.execute("INSERT INTO daily_basic VALUES('688802.SH','20260828',NULL,NULL,NULL,27000274.0)")
    c.execute("INSERT INTO daily_basic VALUES('688802.SH','20260827',NULL,NULL,168.1111,27638908.0)")
    c.commit()
    yield c
    c.close()


class TestIngest:
    def test_ps_latest_nonnull(self, db):
        f = fetch_daily_basic("688802.SH", "ps", conn=db)
        assert f.display == "168.11x" and f.value == Decimal("168.11")
        assert f.source_kind == "tushare"
        assert f.source_ref == "daily_basic:688802.SH@20260827:ps"
        assert f.as_of == "2026-08-27"

    def test_total_mv_wan_to_yi(self, db):
        f = fetch_daily_basic("688802.SH", "total_mv", conn=db)
        assert f.display == "≈2700亿" and f.value == Decimal("2700")
        assert f.unit == "亿"

    def test_unknown_metric(self, db):
        with pytest.raises(ValueError, match="metric"):
            fetch_daily_basic("688802.SH", "zzz", conn=db)

    def test_no_rows(self, db):
        with pytest.raises(LookupError):
            fetch_daily_basic("688801.SH", "ps", conn=db)


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "davis_analyzer.cardgen", *args],
        capture_output=True, text=True, cwd=REPO_ROOT)


@pytest.fixture
def redirected(tmp_path: Path, monkeypatch) -> Path:
    """工程目录 + 台账均重定向到 tmp,返回工程根(小红书卡片/)。"""
    monkeypatch.setenv("CARDGEN_PROJECT_ROOT", str(tmp_path / "小红书卡片"))
    monkeypatch.setenv("CARDGEN_LEDGER_DB", str(tmp_path / "cards.db"))
    return tmp_path / "小红书卡片"


class TestCliSmoke:
    def test_init_and_validate(self, tmp_path: Path, redirected: Path):
        proj = redirected / "烟测"
        r = _run_cli("init", "--topic", "烟测")
        assert r.returncode == 0, r.stderr
        assert (proj / "facts.json").exists() and (proj / "cards.spec.json").exists()
        assert (proj / "output").exists()
        assert (tmp_path / "cards.db").exists()  # 台账落在重定向库,真实 content_cards.db 不被触碰
        r2 = _run_cli("validate", "--topic", "烟测")
        assert r2.returncode != 0  # 空工程必不过(事实清单为空)
        assert "事实清单为空" in r2.stdout + r2.stderr
        r3 = _run_cli("status")
        assert r3.returncode == 0 and "烟测" in r3.stdout and "drafting" in r3.stdout
        r4 = _run_cli("build", "--topic", "烟测")
        assert r4.returncode != 0 and "validate" in (r4.stdout + r4.stderr)  # 闸门拦下空工程

    def test_enqueue_prints_command_and_marks_queued(self, tmp_path: Path, redirected: Path):
        proj = redirected / "烟测"
        assert _run_cli("init", "--topic", "烟测").returncode == 0
        r = _run_cli("enqueue", "--topic", "烟测")
        assert r.returncode != 0 and "RELEASE.json" in (r.stdout + r.stderr)  # 未 build 先拒绝

        (proj / "cards.spec.json").write_text(json.dumps({"cards": [
            {"type": "cover", "name": "01_封面", "title": "烟测<em>卡片</em>", "tags": "测试,卡片"}]},
            ensure_ascii=False), encoding="utf-8")
        (proj / "output").mkdir(exist_ok=True)
        (proj / "output" / "RELEASE.json").write_text(json.dumps({
            "topic": "烟测", "version": 1, "as_of": "2026-08-27",
            "expires_at": (date.today() + timedelta(days=1)).isoformat(),
            "images": ["output/烟测_01_封面.png"]}, ensure_ascii=False), encoding="utf-8")
        r2 = _run_cli("enqueue", "--topic", "烟测")
        assert r2.returncode == 0, r2.stderr
        assert "queue.py enqueue" in r2.stdout
        # 契约:RELEASE.images 是相对 project_dir 的路径,打印前用 proj/img 拼接为绝对路径
        assert str(proj / "output" / "烟测_01_封面.png") in r2.stdout
        assert "<em>" not in r2.stdout  # 标题去 HTML 标签
        conn = ledger.connect(tmp_path / "cards.db")
        assert ledger.get_card(conn, "烟测")["status"] == "queued"
        conn.close()

    def test_enqueue_expired_blocks(self, tmp_path: Path, redirected: Path):
        proj = redirected / "过期"
        assert _run_cli("init", "--topic", "过期").returncode == 0
        (proj / "output").mkdir(exist_ok=True)
        (proj / "output" / "RELEASE.json").write_text(json.dumps({
            "topic": "过期", "version": 1, "as_of": "2026-08-01",
            "expires_at": (date.today() - timedelta(days=1)).isoformat(),
            "images": ["output/过期_01_封面.png"]}, ensure_ascii=False), encoding="utf-8")
        r = _run_cli("enqueue", "--topic", "过期")
        assert r.returncode != 0 and "已过期" in (r.stdout + r.stderr)


class TestCliIngestCommand:
    def test_ingest_writes_facts_json(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CARDGEN_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setenv("CARDGEN_LEDGER_DB", str(tmp_path / "cards.db"))
        fake = Fact(id="", value=Decimal("168.11"), unit="x", display="168.11x",
                    as_of="2026-08-27", source_kind="tushare",
                    source_ref="daily_basic:688802.SH@20260827:ps")
        monkeypatch.setattr("davis_analyzer.cardgen.cli.fetch_daily_basic",
                            lambda code, metric: fake)
        main(["init", "--topic", "烟测"])
        main(["ingest", "--topic", "烟测", "--code", "688802.SH", "--metric", "ps"])
        facts = json.loads((tmp_path / "烟测" / "facts.json").read_text(encoding="utf-8"))["facts"]
        assert facts[0]["id"] == "ps_688802"  # 缺省 id = {metric}_{代码前段}
        assert facts[0]["display"] == "168.11x"
        assert facts[0]["source"] == {"kind": "tushare", "ref": "daily_basic:688802.SH@20260827:ps"}

    def test_ingest_replaces_same_id(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CARDGEN_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setenv("CARDGEN_LEDGER_DB", str(tmp_path / "cards.db"))
        values = ["168.11x", "170.00x"]

        def fake(code: str, metric: str) -> Fact:
            v = values.pop(0)
            return Fact(id="", value=Decimal(v[:-1]), unit="x", display=v,
                        as_of="2026-08-27", source_kind="tushare", source_ref="r")

        monkeypatch.setattr("davis_analyzer.cardgen.cli.fetch_daily_basic", fake)
        main(["init", "--topic", "烟测"])
        main(["ingest", "--topic", "烟测", "--code", "688802.SH", "--metric", "ps"])
        main(["ingest", "--topic", "烟测", "--code", "688802.SH", "--metric", "ps",
              "--id", "自定义"])
        facts = json.loads((tmp_path / "烟测" / "facts.json").read_text(encoding="utf-8"))["facts"]
        assert len(facts) == 2 and {f["id"] for f in facts} == {"ps_688802", "自定义"}
