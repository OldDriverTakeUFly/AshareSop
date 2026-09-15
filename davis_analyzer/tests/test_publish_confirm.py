"""publish_confirm 对账回归:扫描范围与时间防误判(2026-09-15 修复)。

事故:9/13 起卡片由 cron 直接入池停在 draft,人工直发小红书后,21:25 对账只扫
prepped/到点 scheduled——已发布的 #92/#94/#95 永远停在 draft,每晚播报「无待对账项」。
修复锁定三点:①扫描扩到 draft/reviewed;②命中笔记 published_at 必须 ≥ 行 created_at
(防复活重发的同标题旧卡对账到旧笔记);③note_id 列写真实笔记 id(旧代码误写标题)。
"""
import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts" / "content_publisher"
QUEUE = SCRIPTS / "queue.py"

T_DRAFT = "09-14 龙虎榜 | 每日数据复盘"
T_GUARD = "光通信产业地图勘误与复活测试"
T_REVIEWED = "已审核待发卡研究标的对比表"
T_FUTURE = "未来排期卡不应被提前对账"
T_PREPPED = "已备料未检出卡等待人工核对"


def _load():
    spec = importlib.util.spec_from_file_location("publish_confirm", SCRIPTS / "publish_confirm.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["publish_confirm"] = mod
    spec.loader.exec_module(mod)
    return mod


def _metrics_db(path: Path, notes: list[tuple[str, str]]) -> dict[str, int]:
    """按 metrics/db.py 的 accounts+notes 子集建临时回流库,返回 title→note_id。"""
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE accounts(account_id TEXT PRIMARY KEY, platform TEXT NOT NULL DEFAULT 'xhs',"
        " name TEXT, created_at TEXT NOT NULL);"
        "CREATE TABLE notes(note_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " account_id TEXT NOT NULL REFERENCES accounts(account_id), topic TEXT, grp TEXT,"
        " published_at TEXT, title TEXT NOT NULL, url TEXT, UNIQUE(account_id, title));")
    conn.execute("INSERT INTO accounts VALUES('t1','xhs','t','2026-01-01T00:00:00')")
    ids = {}
    for title, pub in notes:
        cur = conn.execute("INSERT INTO notes(account_id,title,published_at) VALUES('t1',?,?)",
                           (title, pub))
        ids[title] = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return ids


def _seed(db: Path, status: str, title: str, created: str, scheduled: str | None = None) -> int:
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO publish_queue(created_at,source,title,body,tags,images,status,"
        "scheduled_at,scan_result) VALUES(?,?,?,?,?,?,?,?,?)",
        (created, "", title, "仅供研究参考,不构成投资建议", "#t", "[]", status, scheduled, "{}"))
    qid = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return qid


def test_reconcile_scan_set_time_guard_and_note_id(tmp_path: Path):
    queue_db = tmp_path / "pub.db"
    assert subprocess.run([sys.executable, str(QUEUE), "init"], capture_output=True, text=True,
                          cwd=REPO_ROOT,
                          env={**os.environ, "PUBLISHER_DB": str(queue_db)}).returncode == 0
    metrics_db = tmp_path / "metrics.db"
    note_ids = _metrics_db(metrics_db, [
        (T_DRAFT, "2026-09-14 18:08"),      # 入池(17:51)之后发布 → 应确认
        (T_GUARD, "2026-08-01 09:00"),      # 早于入池(09-10)的旧笔记 → 防误判拒收
        (T_REVIEWED, "2026-09-15 19:00"),
        (T_FUTURE, "2026-09-15 20:00"),
    ])

    qid_draft = _seed(queue_db, "draft", T_DRAFT, "2026-09-14T17:51:00")
    qid_guard = _seed(queue_db, "draft", T_GUARD, "2026-09-10T10:00:00")
    qid_reviewed = _seed(queue_db, "reviewed", T_REVIEWED, "2026-09-15T09:00:00")
    qid_future = _seed(queue_db, "scheduled", T_FUTURE, "2026-09-15T09:00:00",
                       scheduled="2099-01-01 09:00")
    qid_prepped = _seed(queue_db, "prepped", T_PREPPED, "2026-09-14T09:00:00")

    mod = _load()
    mod.QUEUE_DB = queue_db
    mod.METRICS_DB = metrics_db
    confirmed, pending = mod.reconcile(dry=False)

    assert [i for i, _, _ in confirmed] == [qid_draft, qid_reviewed], \
        "draft/reviewed 行入池后直发也必须被对账到(修复前 draft 不在扫描集)"
    assert [i for i, _ in pending] == [qid_prepped]

    conn = sqlite3.connect(queue_db)
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT status,note_id,published_at FROM publish_queue WHERE id=?",
                     (qid_draft,)).fetchone()
    assert r["status"] == "published"
    assert int(r["note_id"]) == note_ids[T_DRAFT], \
        "note_id 列必须是真实笔记 id(修复前误写标题,int() 即炸)"
    assert r["published_at"] == "2026-09-14 18:08"
    assert conn.execute("SELECT status FROM publish_queue WHERE id=?",
                        (qid_guard,)).fetchone()[0] == "draft", "旧笔记不得确认新入池的同标题行"
    assert conn.execute("SELECT status FROM publish_queue WHERE id=?",
                        (qid_future,)).fetchone()[0] == "scheduled", "未到点 scheduled 不扫"
    assert conn.execute(
        "SELECT COUNT(*) FROM publish_log WHERE event='auto_confirm'").fetchone()[0] == 2
    conn.close()
