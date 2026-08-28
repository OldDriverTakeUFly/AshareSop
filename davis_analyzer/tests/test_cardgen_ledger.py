# davis_analyzer/tests/test_cardgen_ledger.py
"""content_cards.db 台账:登记/版本/状态/校验日志。"""
import sqlite3
from pathlib import Path

import pytest

from davis_analyzer.cardgen import ledger
from davis_analyzer.cardgen.types import Failure


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = ledger.connect(tmp_path / "cards.db")
    yield c
    c.close()


class TestRegister:
    def test_first_register_version_1(self, conn):
        v = ledger.register_card(conn, "GPU四小龙", "docs/小红书卡片/GPU四小龙/cards.spec.json")
        assert v == 1
        row = ledger.get_card(conn, "GPU四小龙")
        assert row["status"] == "drafting" and row["current_version"] == 1

    def test_reregister_returns_existing(self, conn):
        ledger.register_card(conn, "T", "p")
        assert ledger.register_card(conn, "T", "p") == 1


class TestVersioning:
    def test_record_render(self, conn):
        ledger.register_card(conn, "T", "p")
        ledger.record_render(conn, "T", 1, reason="初版", facts_digest="sha256:a", spec_digest="sha256:b")
        row = ledger.get_card(conn, "T")
        assert row["status"] == "rendered"
        revs = conn.execute("SELECT version, reason FROM revisions WHERE topic='T'").fetchall()
        assert revs[0]["version"] == 1 and revs[0]["reason"] == "初版"

    def test_bump_requires_reason(self, conn):
        ledger.register_card(conn, "T", "p")
        with pytest.raises(ValueError, match="reason"):
            ledger.bump_version(conn, "T", reason="", facts_digest="d", spec_digest="s")

    def test_bump_increments(self, conn):
        ledger.register_card(conn, "T", "p")
        v2 = ledger.bump_version(conn, "T", reason="估值更新", facts_digest="d2", spec_digest="s2")
        assert v2 == 2 and ledger.get_card(conn, "T")["current_version"] == 2


class TestValidateLog:
    def test_log_and_query(self, conn):
        ledger.register_card(conn, "T", "p")
        ledger.log_validate(conn, "T", 1, passed=False,
                            failures=[Failure("numbers", "03", "cards[2].rows[0]", "未溯源: 30.4%")])
        row = conn.execute("SELECT passed, failures_json FROM validate_log WHERE topic='T'").fetchone()
        assert row["passed"] == 0 and "30.4" in row["failures_json"]


class TestStatus:
    def test_set_and_rows(self, conn):
        ledger.register_card(conn, "T", "p")
        ledger.set_status(conn, "T", "queued")
        rows = ledger.status_rows(conn)
        assert rows[0]["status"] == "queued"
        assert ledger.status_rows(conn, topic="T")[0]["topic"] == "T"
