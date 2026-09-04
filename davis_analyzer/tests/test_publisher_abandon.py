"""M5 abandon 回归:归档合成审计行不得占用活库日志 id 段。

事故(2026-09-04):abandon #69 的合成行经自增取 id 363,abandon #70 回插其日志保留行
(原 id 恰为 363)时 UNIQUE 冲突,第二次 abandon 必炸。修复=合成行显式取 ≥9 亿保留段。
本测试用可复现构造锁定:第一次 abandon 的合成行 id 必须与第二次的保留行 id 错开。
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
QUEUE = REPO_ROOT / "scripts" / "content_publisher" / "queue.py"
TITLE = "abandon回归"
BODY_OK = "数据来源:测试 · 仅供研究参考,不构成投资建议"
# 显式高位队列 id:abandon 会 glob 真实 PREP_DIR(*_<qid>_*),高位 id 保证测试不误删真实备料
QID_A, QID_B = 97001, 97002


def _run(db: Path, arc: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(QUEUE), *args], capture_output=True, text=True,
                          cwd=REPO_ROOT, env={**os.environ, "PUBLISHER_DB": str(db),
                                              "PUBLISHER_ARCHIVE_DB": str(arc)})


def _seed(db: Path, qid: int, log_id: int) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO publish_queue(id,created_at,source,title,body,tags,images,status,scan_result)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (qid, "2026-09-05T12:00:00", "", TITLE, BODY_OK, "#t", "[]", "draft", "{}"))
    conn.execute("INSERT INTO publish_log(id,queue_id,ts,event) VALUES(?,?,?,?)",
                 (log_id, qid, "2026-09-05T12:00:01", "enqueue"))
    conn.commit()
    conn.close()


@pytest.fixture
def dbs(tmp_path: Path):
    db, arc = tmp_path / "pub.db", tmp_path / "arc.db"
    assert _run(db, arc, "init").returncode == 0
    return db, arc


def test_consecutive_abandon_no_id_collision(dbs):
    """/abandon A(其日志 id=1)→ 合成行若自增会占 id=2 → abandon B(其日志 id=2)回插必撞。"""
    db, arc = dbs
    _seed(db, QID_A, log_id=1)
    r1 = _run(db, arc, "abandon", str(QID_A))
    assert r1.returncode == 0, r1.stderr

    _seed(db, QID_B, log_id=2)
    r2 = _run(db, arc, "abandon", str(QID_B))
    assert r2.returncode == 0, r2.stderr  # 修复前此处 UNIQUE constraint failed: publish_log.id

    conn = sqlite3.connect(arc)
    conn.row_factory = sqlite3.Row
    preserved = {r["id"] for r in conn.execute(
        "SELECT id FROM publish_log WHERE event!='abandon'")}
    synth = {r["id"] for r in conn.execute(
        "SELECT id FROM publish_log WHERE event='abandon'")}
    assert preserved == {1, 2}, "保留行必须按活库原 id 完整回插"
    assert len(synth) == 2 and all(i >= 900_000_000 for i in synth), \
        "合成行必须全部落在保留 id 段,不与活库 id 段重叠"
    assert conn.execute("SELECT COUNT(*) FROM publish_queue").fetchone()[0] == 2
    conn.close()

    live = sqlite3.connect(db)
    assert live.execute("SELECT COUNT(*) FROM publish_queue").fetchone()[0] == 0
    assert live.execute("SELECT COUNT(*) FROM publish_log").fetchone()[0] == 0
    live.close()
