"""M3 自动发稿:护栏纯函数 + CLI 接线(dry-run/confirm/登录态前置检查),不触真实浏览器。"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts" / "content_publisher"
QUEUE = SCRIPTS / "queue.py"

TITLE = "M3测试"
BODY_OK = "数据来源:Tushare · 仅供研究参考,不构成投资建议"
NOW = "2026-08-29T12:00:00"


def _publisher():
    spec = importlib.util.spec_from_file_location("xhs_publisher", SCRIPTS / "publisher.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["xhs_publisher"] = mod  # dataclass 需要 sys.modules 可查
    spec.loader.exec_module(mod)
    return mod


def _run(db: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(QUEUE), *args], capture_output=True,
                          text=True, cwd=REPO_ROOT, env={**os.environ, "PUBLISHER_DB": str(db)})


def _seed_due_row(db: Path, qid_expires: str = "2099-01-01") -> int:
    """造一条 scheduled 且已到点的行(直接插库,绕开 enqueue 以便控制字段)。"""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "INSERT INTO publish_queue(created_at,source,title,body,tags,images,status,"
        "scheduled_at,release_expires,scan_result) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (NOW, "", TITLE, BODY_OK, "#t", "[\"x.png\"]", "scheduled",
         "2026-08-29T08:00", qid_expires, "{}"))
    qid = cur.lastrowid
    conn.commit()
    conn.close()
    return qid


@pytest.fixture
def db(tmp_path: Path) -> Path:
    dbp = tmp_path / "pub.db"
    assert _run(dbp, "init").returncode == 0
    return dbp


class TestGuardrails:
    """select_publishable 纯函数:单日上限/最小间隔/过期跳过。"""

    def test_normal_pass(self):
        pub = _publisher()
        todo, skipped = pub.select_publishable(
            [{"id": 1, "release_expires": "2099-01-01"}], 0, None, NOW)
        assert [r["id"] for r in todo] == [1] and skipped == []

    def test_daily_limit(self):
        pub = _publisher()
        todo, skipped = pub.select_publishable([{"id": 1}], pub.GUARD_DAILY_LIMIT, None, NOW)
        assert todo == [] and "上限" in skipped[0]

    def test_min_interval(self):
        pub = _publisher()
        recent = (datetime.fromisoformat(NOW) - timedelta(minutes=5)).isoformat()
        todo, skipped = pub.select_publishable([{"id": 1}], 0, recent, NOW)
        assert todo == [] and "不足" in skipped[0]

    def test_interval_ok_after_wait(self):
        pub = _publisher()
        old = (datetime.fromisoformat(NOW) - timedelta(minutes=pub.GUARD_MIN_INTERVAL_MIN + 1)).isoformat()
        todo, _ = pub.select_publishable([{"id": 1}], 0, old, NOW)
        assert len(todo) == 1

    def test_stale_row_skipped(self):
        pub = _publisher()
        todo, skipped = pub.select_publishable(
            [{"id": 1, "release_expires": "2020-01-01"}, {"id": 2, "release_expires": "2099-01-01"}],
            0, None, NOW)
        assert [r["id"] for r in todo] == [2] and "过期" in skipped[0]


class TestCli:
    def test_publish_requires_confirm(self, db: Path):
        qid = _seed_due_row(db)
        r = _run(db, "publish", str(qid))
        assert r.returncode != 0 and "--confirm" in (r.stdout + r.stderr)

    def test_publish_no_login_state_fails_clean(self, db: Path, monkeypatch):
        monkeypatch.setenv("PUBLISHER_PROFILE_DIR", "/tmp/xhs_no_profile_dir")
        qid = _seed_due_row(db)
        r = _run(db, "publish", str(qid), "--confirm")
        out = r.stdout + r.stderr
        assert "login" in out  # 干净失败于登录态检查,不触 playwright

    def test_publish_wrong_status(self, db: Path):
        r = _run(db, "publish", "999", "--confirm")
        assert r.returncode != 0 and "scheduled" in (r.stdout + r.stderr)

    def test_publish_due_dry_run_plan(self, db: Path):
        _seed_due_row(db, "2099-01-01")
        _seed_due_row(db, "2020-01-01")  # 过期行应进跳过清单
        r = _run(db, "publish-due", "--dry-run")
        assert r.returncode == 0
        assert "将发布 #1" in r.stdout and "将发布 #2" not in r.stdout
        assert "过期" in r.stdout

    def test_publish_due_daily_limit_respected(self, db: Path):
        pub = _publisher()
        for _ in range(pub.GUARD_DAILY_LIMIT):  # 打满当日上限
            qid = _seed_due_row(db)
            conn = sqlite3.connect(db)
            conn.execute("UPDATE publish_queue SET status='published', published_at=? WHERE id=?",
                         (NOW, qid))
            conn.commit()
            conn.close()
        _seed_due_row(db)
        r = _run(db, "publish-due", "--dry-run")
        assert "将发布" not in r.stdout and "上限" in r.stdout

    def test_publish_due_empty(self, db: Path):
        r = _run(db, "publish-due", "--dry-run")
        assert "无到点项" in r.stdout
