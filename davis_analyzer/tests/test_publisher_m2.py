"""content_publisher M2 时效硬闸/RELEASE 入池/due/PUBLISHER_DB 注入(subprocess 级 CLI 测试)。"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
QUEUE = REPO_ROOT / "scripts" / "content_publisher" / "queue.py"

TITLE = "测试卡片"
BODY_OK = "数据来源:Tushare · 仅供研究参考,不构成投资建议"


def _run(env_db: Path, tmp: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PUBLISHER_DB": str(env_db)}
    return subprocess.run([sys.executable, str(QUEUE), *args], capture_output=True,
                          text=True, cwd=REPO_ROOT, env=env)


_proj_seq = 0


def _make_project(tmp: Path, expires: str) -> Path:
    """cardgen 形态工程目录:RELEASE.json + 一张图(每次调用独立目录)。"""
    global _proj_seq
    _proj_seq += 1
    proj = tmp / f"proj{_proj_seq}"
    (proj / "output").mkdir(parents=True)
    (proj / "output" / "card.png").write_bytes(b"png")
    (proj / "output" / "RELEASE.json").write_text(
        json.dumps({"topic": "t", "version": 1, "as_of": expires[:10] or "2026-08-01",
                    "expires_at": expires}), encoding="utf-8")
    return proj


@pytest.fixture
def db(tmp_path: Path) -> Path:
    dbp = tmp_path / "pub.db"
    r = _run(dbp, tmp_path, "init")
    assert r.returncode == 0, r.stderr
    return dbp


def _enqueue_ok(db: Path, tmp: Path, source: str = "") -> int:
    img = str(_make_project(tmp, "2099-01-01") / "output" / "card.png")
    r = _run(db, tmp, "enqueue", "--title", TITLE, "--body", BODY_OK,
             "--images", img, "--source", source)
    assert r.returncode == 0, r.stderr
    return int(r.stdout.split()[1].lstrip("#"))


class TestEnvInjection:
    def test_init_creates_injected_db(self, db: Path):
        assert db.exists()


class TestReleaseIntake:
    def test_source_with_release_records_expires(self, db: Path, tmp_path: Path):
        proj = _make_project(tmp_path, "2099-01-01")
        qid = _enqueue_ok(db, tmp_path, source=str(proj))
        rows = json.loads(_run(db, tmp_path, "list", "--json").stdout)
        row = next(r for r in rows if r["id"] == qid)
        assert row["release_expires"] == "2099-01-01"

    def test_plain_source_no_release(self, db: Path, tmp_path: Path):
        qid = _enqueue_ok(db, tmp_path, source="")
        row = next(r for r in json.loads(_run(db, tmp_path, "list", "--json").stdout)
                   if r["id"] == qid)
        assert row["release_expires"] in ("", None)

    def test_expired_release_rejected_at_enqueue(self, db: Path, tmp_path: Path):
        proj = _make_project(tmp_path, "2020-01-01")
        img = str(proj / "output" / "card.png")
        r = _run(db, tmp_path, "enqueue", "--title", TITLE, "--body", BODY_OK,
                 "--images", img, "--source", str(proj))
        assert r.returncode != 0 and "过期" in (r.stdout + r.stderr)


class TestScheduleGate:
    def _review(self, db: Path, tmp: Path, qid: int):
        assert _run(db, tmp, "review", str(qid)).returncode == 0

    def test_schedule_beyond_expiry_rejected(self, db: Path, tmp_path: Path):
        proj = _make_project(tmp_path, "2099-01-01")
        qid = _enqueue_ok(db, tmp_path, source=str(proj))
        self._review(db, tmp_path, qid)
        r = _run(db, tmp_path, "schedule", str(qid), "--at", "9999-01-01 20:00")
        assert r.returncode != 0 and "有效期" in (r.stdout + r.stderr)
        row = next(x for x in json.loads(_run(db, tmp_path, "list", "--json").stdout) if x["id"] == qid)
        assert row["status"] == "reviewed"  # 状态不被消耗

    def test_schedule_within_expiry_ok(self, db: Path, tmp_path: Path):
        proj = _make_project(tmp_path, "2099-01-01")
        qid = _enqueue_ok(db, tmp_path, source=str(proj))
        self._review(db, tmp_path, qid)
        r = _run(db, tmp_path, "schedule", str(qid), "--at", "2099-01-01 20:00")
        assert r.returncode == 0, r.stderr  # 排期日=过期日当天,仍有效

    def test_no_release_backward_compat(self, db: Path, tmp_path: Path):
        qid = _enqueue_ok(db, tmp_path)
        self._review(db, tmp_path, qid)
        r = _run(db, tmp_path, "schedule", str(qid), "--at", "9999-01-01 20:00")
        assert r.returncode == 0, r.stderr  # 无契约的存量行不受闸


class TestDue:
    def test_due_lists_arrived_only(self, db: Path, tmp_path: Path):
        qid = _enqueue_ok(db, tmp_path)
        assert _run(db, tmp_path, "review", str(qid)).returncode == 0
        assert _run(db, tmp_path, "schedule", str(qid), "--at", "2000-01-01 08:00").returncode == 0
        out = _run(db, tmp_path, "due").stdout
        assert f"#{qid}" in out and "共 1 项" in out

    def test_due_empty(self, db: Path, tmp_path: Path):
        assert "无到点" in _run(db, tmp_path, "due").stdout


class TestGroupPassthrough:
    def test_enqueue_reads_group_from_release(self, db: Path, tmp_path: Path):
        proj = _make_project(tmp_path, "2099-01-01")
        (proj / "output" / "RELEASE.json").write_text(
            json.dumps({"expires_at": "2099-01-01", "group": "产业链调研"}), encoding="utf-8")
        img = str(proj / "output" / "card.png")
        r = _run(db, tmp_path, "enqueue", "--title", TITLE, "--body", BODY_OK,
                 "--images", img, "--source", str(proj))
        assert r.returncode == 0 and "分组[产业链调研]" in r.stdout
        row = json.loads(_run(db, tmp_path, "list", "--json").stdout)[0]
        assert row["group"] == "产业链调研"
