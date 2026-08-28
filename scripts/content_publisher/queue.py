#!/usr/bin/env python3
# content_publisher/queue.py — 发稿池台账(SQLite)+ 敏感词扫描 + 状态机 CLI
# M1 范围:入池(draft)→ 人工审核(reviewed)→ 排期(scheduled);发布动作留人工/未来M2
# 用法:
#   .venv/bin/python scripts/content_publisher/queue.py init
#   .venv/bin/python scripts/content_publisher/queue.py enqueue --title "..." --images a.png,b.png --tags "#国产GPU" --body "..." --source docs/小红书卡片/GPU四小龙
#   .venv/bin/python scripts/content_publisher/queue.py scan          # 扫描全部 draft
#   .venv/bin/python scripts/content_publisher/queue.py list [--status draft]
#   .venv/bin/python scripts/content_publisher/queue.py review <id>   # draft→reviewed(人工确认)
#   .venv/bin/python scripts/content_publisher/queue.py schedule <id> --at "2026-08-30 20:00"
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT.parent.parent / "storage" / "database" / "content_publisher.db"
WORDS_FILE = ROOT.parent / "card_factory" / "sensitive_words.txt"

REQUIRED = ["不构成投资建议"]  # 合规必备话术(标题+正文+图片foot合并检查)
STATUSES = ("draft", "reviewed", "scheduled", "published", "failed")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(_args=None) -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS publish_queue(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            source TEXT, title TEXT NOT NULL, body TEXT, tags TEXT,
            images TEXT, platform TEXT DEFAULT 'xiaohongshu',
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','reviewed','scheduled','published','failed')),
            scheduled_at TEXT, published_at TEXT, note_id TEXT,
            scan_result TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS publish_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_id INTEGER, ts TEXT NOT NULL, event TEXT NOT NULL, detail TEXT)""")
    print(f"OK: {DB}")


def _log(c: sqlite3.Connection, qid: int, event: str, detail: str = "") -> None:
    c.execute("INSERT INTO publish_log(queue_id, ts, event, detail) VALUES(?,?,?,?)",
              (qid, datetime.now().isoformat(timespec="seconds"), event, detail))


def load_words() -> list[str]:
    words = [w.strip() for w in WORDS_FILE.read_text(encoding="utf-8").splitlines()
             if w.strip() and not w.strip().startswith("#")]
    return words


def scan_text(text: str) -> dict:
    words = load_words()
    hits = sorted({w for w in words if w in text})
    missing_required = [r for r in REQUIRED if r not in text]
    passed = not hits and not missing_required
    return {"passed": passed, "hits": hits, "missing_required": missing_required,
            "scanned_at": datetime.now().isoformat(timespec="seconds")}


def enqueue(args: argparse.Namespace) -> None:
    images = [p for p in (args.images or "").split(",") if p]
    for p in images:
        if not Path(p).exists():
            sys.exit(f"图片不存在: {p}")
    scan = scan_text(args.title + (args.body or "") + (args.tags or ""))
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO publish_queue(created_at, source, title, body, tags, images, scan_result) "
            "VALUES(?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), args.source, args.title,
             args.body or "", args.tags or "", json.dumps(images, ensure_ascii=False),
             json.dumps(scan, ensure_ascii=False)))
        _log(c, cur.lastrowid, "enqueue", f"scan_passed={scan['passed']}")
        print(f"入池 #{cur.lastrowid} [{args.title}] 扫描: {'通过' if scan['passed'] else scan}")


def rescan(args: argparse.Namespace) -> None:
    with _conn() as c:
        rows = c.execute("SELECT id,title,body,tags FROM publish_queue WHERE status='draft'").fetchall()
        for r in rows:
            scan = scan_text(r["title"] + r["body"] + r["tags"])
            c.execute("UPDATE publish_queue SET scan_result=? WHERE id=?",
                      (json.dumps(scan, ensure_ascii=False), r["id"]))
            print(f"#{r['id']} {'✓' if scan['passed'] else '✗ ' + str(scan)} {r['title']}")


def review(args: argparse.Namespace) -> None:
    with _conn() as c:
        r = c.execute("SELECT status, scan_result FROM publish_queue WHERE id=?", (args.id,)).fetchone()
        if not r:
            sys.exit(f"无 #{args.id}")
        if r["status"] != "draft":
            sys.exit(f"#{args.id} 状态为 {r['status']},仅 draft 可审核")
        scan = json.loads(r["scan_result"] or "{}")
        if not scan.get("passed"):
            sys.exit(f"#{args.id} 扫描未通过: {scan}\n如确认无风险,先人工修正文案或更新敏感词表")
        c.execute("UPDATE publish_queue SET status='reviewed' WHERE id=?", (args.id,))
        _log(c, args.id, "review", "人工审核通过")
        print(f"#{args.id} → reviewed(可 schedule)")


def schedule(args: argparse.Namespace) -> None:
    with _conn() as c:
        r = c.execute("SELECT status FROM publish_queue WHERE id=?", (args.id,)).fetchone()
        if not r or r["status"] != "reviewed":
            sys.exit(f"#{args.id} 非 reviewed 状态,不能排期")
        c.execute("UPDATE publish_queue SET status='scheduled', scheduled_at=? WHERE id=?",
                  (args.at, args.id))
        _log(c, args.id, "schedule", args.at)
        print(f"#{args.id} → scheduled @ {args.at}(M1 到此为止,发布动作人工/未来M2)")


def mark(args: argparse.Namespace) -> None:
    if args.status not in STATUSES:
        sys.exit(f"非法状态: {args.status}")
    with _conn() as c:
        c.execute("UPDATE publish_queue SET status=?, published_at=CASE WHEN ?='published' THEN ? ELSE published_at END WHERE id=?",
                  (args.status, args.status, datetime.now().isoformat(timespec="seconds"), args.id))
        _log(c, args.id, "mark", args.status)
        print(f"#{args.id} → {args.status}")


def list_queue(args: argparse.Namespace) -> None:
    q = "SELECT id,status,title,substr(tags,1,30) tags,scheduled_at FROM publish_queue"
    params: tuple = ()
    if args.status:
        q += " WHERE status=?"
        params = (args.status,)
    with _conn() as c:
        for r in c.execute(q + " ORDER BY id", params):
            print(f"#{r['id']:<3} [{r['status']:<9}] {r['title'][:38]:<40} {r['tags']} {r['scheduled_at'] or ''}")


def main() -> None:
    ap = argparse.ArgumentParser(description="内容发稿池(M1)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(func=init_db)
    e = sub.add_parser("enqueue"); e.add_argument("--title", required=True); e.add_argument("--body", default="")
    e.add_argument("--tags", default=""); e.add_argument("--images", default=""); e.add_argument("--source", default="")
    e.set_defaults(func=enqueue)
    sub.add_parser("scan").set_defaults(func=rescan)
    r = sub.add_parser("review"); r.add_argument("id", type=int); r.set_defaults(func=review)
    s = sub.add_parser("schedule"); s.add_argument("id", type=int); s.add_argument("--at", required=True)
    s.set_defaults(func=schedule)
    m = sub.add_parser("mark"); m.add_argument("id", type=int); m.add_argument("status"); m.set_defaults(func=mark)
    l = sub.add_parser("list"); l.add_argument("--status"); l.set_defaults(func=list_queue)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
