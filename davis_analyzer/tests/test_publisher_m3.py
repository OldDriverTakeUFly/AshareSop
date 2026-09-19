"""publisher CLI 测试:M3 护栏纯函数 + M4 停用契约(publish/publish-due/login 只留引导,不触浏览器)。

M4(2026-08-30)小红书判定账号自动化后,自动发帖三条命令改为 sys.exit(引导文案):
退出码 1、stderr 含「自动发帖已停用」与 prep→人工→mark 引导;--confirm 参数已移除。
"""
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
    """M4 停用契约:自动发帖三命令一律退出并给 prep 引导;publish 行状态不受影响。"""

    BANNER_KEYWORDS = ("自动发帖已停用", "prep", "mark")

    def _assert_disabled(self, r: subprocess.CompletedProcess) -> None:
        out = r.stdout + r.stderr
        assert r.returncode != 0
        for kw in self.BANNER_KEYWORDS:
            assert kw in out, f"缺少引导关键词 {kw!r}:{out!r}"

    def test_publish_disabled_with_banner(self, db: Path):
        qid = _seed_due_row(db)
        r = _run(db, "publish", str(qid))
        self._assert_disabled(r)
        # 行不得被自动发布改动
        conn = sqlite3.connect(db)
        status = conn.execute("SELECT status FROM publish_queue WHERE id=?", (qid,)).fetchone()[0]
        conn.close()
        assert status == "scheduled"

    def test_publish_confirm_flag_removed(self, db: Path):
        # M4 移除 --confirm;传了应吃 argparse usage 错误(rc=2),而非静默接受
        qid = _seed_due_row(db)
        r = _run(db, "publish", str(qid), "--confirm")
        assert r.returncode != 0 and "invalid choice" in (r.stderr + r.stdout) or r.returncode == 2

    def test_publish_due_disabled_even_dry_run(self, db: Path):
        _seed_due_row(db, "2099-01-01")
        r = _run(db, "publish-due", "--dry-run")
        self._assert_disabled(r)

    def test_publish_due_no_side_effect_on_rows(self, db: Path):
        qid = _seed_due_row(db)
        _run(db, "publish-due", "--dry-run")
        conn = sqlite3.connect(db)
        status, published_at = conn.execute(
            "SELECT status, published_at FROM publish_queue WHERE id=?", (qid,)).fetchone()
        conn.close()
        assert status == "scheduled" and published_at is None

    def test_login_disabled(self, db: Path):
        r = _run(db, "login")
        self._assert_disabled(r)
