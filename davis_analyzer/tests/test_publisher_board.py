"""board.py 发稿池管理台 API 测试(TestClient;写操作经 queue.py CLI 走真实闸门)。"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
BOARD = REPO_ROOT / "scripts" / "content_publisher" / "board.py"
QUEUE = REPO_ROOT / "scripts" / "content_publisher" / "queue.py"

TITLE = "管理台测试"
BODY_OK = "数据来源:Tushare · 仅供研究参考,不构成投资建议"


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PUBLISHER_DB", str(tmp_path / "pub.db"))
    r = subprocess.run([sys.executable, str(QUEUE), "init"], capture_output=True, text=True,
                       cwd=REPO_ROOT, env={**os.environ, "PUBLISHER_DB": str(tmp_path / "pub.db")})
    assert r.returncode == 0, r.stderr
    spec = importlib.util.spec_from_file_location("publisher_board", BOARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return TestClient(mod.app)


def _enqueue(db_env: dict, tmp: Path, expires: str) -> int:
    proj = tmp / "p"
    (proj / "output").mkdir(parents=True, exist_ok=True)
    (proj / "output" / "c.png").write_bytes(b"png")
    (proj / "output" / "RELEASE.json").write_text(
        json.dumps({"topic": "t", "expires_at": expires}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(QUEUE), "enqueue", "--title", TITLE, "--body", BODY_OK,
                        "--images", str(proj / "output" / "c.png"), "--source", str(proj)],
                       capture_output=True, text=True, cwd=REPO_ROOT, env=db_env)
    assert r.returncode == 0, r.stderr
    return int(r.stdout.split()[1].lstrip("#"))


class TestTokenAuth:
    def test_token_gate(self, tmp_path: Path, monkeypatch):
        dbp = tmp_path / "pub.db"
        monkeypatch.setenv("PUBLISHER_DB", str(dbp))
        monkeypatch.setenv("BOARD_TOKEN", "s3cret")
        r = subprocess.run([sys.executable, str(QUEUE), "init"], capture_output=True, text=True,
                           cwd=REPO_ROOT, env={**os.environ})
        assert r.returncode == 0, r.stderr
        spec = importlib.util.spec_from_file_location("publisher_board_t", BOARD)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        c = TestClient(mod.app)
        assert c.get("/api/queue").status_code == 401
        assert c.get("/api/queue?token=wrong").status_code == 401
        assert c.get("/api/queue?token=s3cret").status_code == 200
        assert c.get("/?token=s3cret").status_code == 200


class TestRead:
    def test_empty_queue(self, client: TestClient, tmp_path: Path):
        assert client.get("/api/queue").json() == []
        assert client.get("/api/due").json() == []

    def test_index_html(self, client: TestClient):
        r = client.get("/")
        assert r.status_code == 200 and "发稿池管理台" in r.text


class TestWriteFlow:
    def test_review_schedule_reject_expired(self, client: TestClient, tmp_path: Path, monkeypatch):
        env = {**os.environ, "PUBLISHER_DB": str(tmp_path / "pub.db")}
        qid = _enqueue(env, tmp_path, "2099-01-01")  # 入池时未过期
        # 模拟入池后数据变陈旧:直接把库里的 release_expires 改到过去(不重启 board,读时查库)
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "pub.db"))
        conn.execute("UPDATE publish_queue SET release_expires='2020-01-01' WHERE id=?", (qid,))
        conn.commit()
        conn.close()
        r = subprocess.run([sys.executable, str(QUEUE), "review", str(qid)],
                           capture_output=True, text=True, cwd=REPO_ROOT, env=env)
        assert r.returncode == 0, r.stderr
        assert any(x["id"] == qid for x in client.get("/api/queue").json())
        resp = client.post(f"/api/queue/{qid}/schedule", json={"at": "2099-01-01 20:00"})
        assert resp.status_code == 400 and "有效期" in resp.json()["stderr"]

    def test_review_and_schedule_ok(self, client: TestClient, tmp_path: Path):
        env = {**os.environ, "PUBLISHER_DB": str(tmp_path / "pub.db")}
        qid = _enqueue(env, tmp_path, "2099-01-01")
        assert client.post(f"/api/queue/{qid}/review").json()["ok"]
        resp = client.post(f"/api/queue/{qid}/schedule", json={"at": "2099-01-01 20:00"})
        assert resp.json()["ok"]
        row = next(x for x in client.get("/api/queue").json() if x["id"] == qid)
        assert row["status"] == "scheduled" and row["scheduled_at"] == "2099-01-01 20:00"

    def test_bad_schedule_format(self, client: TestClient):
        assert client.post("/api/queue/1/schedule", json={"at": "明天"}).status_code == 400

    def test_mark_published(self, client: TestClient, tmp_path: Path):
        env = {**os.environ, "PUBLISHER_DB": str(tmp_path / "pub.db")}
        qid = _enqueue(env, tmp_path, "2099-01-01")
        client.post(f"/api/queue/{qid}/review")
        client.post(f"/api/queue/{qid}/schedule", json={"at": "2099-01-01 20:00"})
        assert client.post(f"/api/queue/{qid}/mark", json={"status": "published"}).json()["ok"]
        row = next(x for x in client.get("/api/queue").json() if x["id"] == qid)
        assert row["status"] == "published" and row["published_at"]
