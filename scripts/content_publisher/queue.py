#!/usr/bin/env python3
# content_publisher/queue.py — 发稿池台账(SQLite)+ 敏感词扫描 + 状态机 CLI
# M1:入池(draft)→ 人工审核(reviewed)→ 排期(scheduled)
# M2(2026-08-29,cardgen session 经用户授权实现):
#   - enqueue 时若 --source 指向 cardgen 工程目录(含 output/RELEASE.json),自动读 expires_at 入库
#   - schedule 时效硬闸:排期日超过 expires_at → 拒绝(当天仍有效)
#   - PUBLISHER_DB 环境变量注入数据库路径(pytest 用)
#   - due 子命令:扫已到点的 scheduled 行(半自动发布提醒);list --json 供管理台消费
# M4(2026-08-30):小红书判定账号使用自动化浏览/发布 → 自动发帖(publish/publish-due/login)停用,
#   改为 prep 备料(机器整理图片+文案+CHECKLIST 到本地目录)→ 人工在官方 App 发布 → mark published
# 用法:
#   .venv/bin/python scripts/content_publisher/queue.py init
#   .venv/bin/python scripts/content_publisher/queue.py enqueue --title "..." --images a.png,b.png --tags "#国产GPU" --body "..." --source docs/小红书卡片/GPU四小龙
#   .venv/bin/python scripts/content_publisher/queue.py scan          # 扫描全部 draft
#   .venv/bin/python scripts/content_publisher/queue.py list [--status draft] [--json]
#   .venv/bin/python scripts/content_publisher/queue.py review <id>   # draft→reviewed(人工确认)
#   .venv/bin/python scripts/content_publisher/queue.py schedule <id> --at "2026-08-30 20:00"
#   .venv/bin/python scripts/content_publisher/queue.py due           # 已到点待发布清单
#   .venv/bin/python scripts/content_publisher/queue.py prep [id]     # 备料(无 id=全部到点项)→ 人工发布 → mark <id> published
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("PUBLISHER_DB", ROOT.parent.parent / "storage" / "database" / "content_publisher.db"))
WORDS_FILE = ROOT.parent / "card_factory" / "sensitive_words.txt"

# 防自遮蔽:本目录 queue.py 与 stdlib queue 同名,脚本方式启动时本目录在 sys.path[0],
# publisher→playwright 内部 `import queue` 会解析到本文件——先以 stdlib 路径锁定 queue
# 模块,再恢复目录供 publisher 导入
_here = str(ROOT)
sys.path = [p for p in sys.path if p != _here]
import queue as _stdlib_queue  # noqa: F401
sys.modules["queue"] = _stdlib_queue
sys.path.insert(0, _here)

REQUIRED = ["不构成投资建议"]  # 合规必备话术(标题+正文+图片foot合并检查)
STATUSES = ("draft", "reviewed", "scheduled", "prepped", "published", "failed")

# 2026-08-30 平台判定后自动发帖停用;publish/publish-due/login 只留提示
_AUTO_PUBLISH_DISABLED = (
    "自动发帖已停用(2026-08-30 小红书判定账号使用自动化浏览/发布,继续自动化会加重处罚)。\n"
    "改用: queue.py prep [id] 备料 → 人工在官方 App 发布 → queue.py mark <id> published")
PREP_DIR = ROOT.parent.parent / "storage" / "publish_prep"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    _ensure_cols(conn)
    return conn


def _ensure_cols(conn: sqlite3.Connection) -> None:
    """M2 列迁移 + M4 CHECK 迁移(补 prepped 状态,SQLite 改 CHECK 须重建表)。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(publish_queue)")}
    if "release_expires" not in cols and cols:
        conn.execute("ALTER TABLE publish_queue ADD COLUMN release_expires TEXT")
    if "group" not in cols and cols:
        conn.execute('ALTER TABLE publish_queue ADD COLUMN "group" TEXT')
    if cols and "prepped" not in (conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='publish_queue'").fetchone()[0] or ""):
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("ALTER TABLE publish_queue RENAME TO publish_queue_old")
        conn.execute("""CREATE TABLE publish_queue(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            source TEXT, title TEXT NOT NULL, body TEXT, tags TEXT,
            images TEXT, platform TEXT DEFAULT 'xiaohongshu',
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','reviewed','scheduled','prepped','published','failed')),
            scheduled_at TEXT, published_at TEXT, note_id TEXT,
            release_expires TEXT, "group" TEXT,
            scan_result TEXT)""")
        conn.execute("INSERT INTO publish_queue SELECT id,created_at,source,title,body,tags,images,"
                     'platform,status,scheduled_at,published_at,note_id,release_expires,"group",scan_result '
                     "FROM publish_queue_old")
        conn.execute("DROP TABLE publish_queue_old")
    conn.commit()


def init_db(_args=None) -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS publish_queue(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            source TEXT, title TEXT NOT NULL, body TEXT, tags TEXT,
            images TEXT, platform TEXT DEFAULT 'xiaohongshu',
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','reviewed','scheduled','prepped','published','failed')),
            scheduled_at TEXT, published_at TEXT, note_id TEXT,
            release_expires TEXT, "group" TEXT,
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


def _load_release(source: str | None) -> dict:
    """--source 指向 cardgen 工程目录时读 output/RELEASE.json(expires_at/group);无契约返回空。"""
    if not source:
        return {}
    p = Path(source) / "output" / "RELEASE.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _load_release_expires(source: str | None) -> str:
    return str(_load_release(source).get("expires_at", ""))


def enqueue(args: argparse.Namespace) -> None:
    images = [p for p in (args.images or "").split(",") if p]
    for p in images:
        if not Path(p).exists():
            sys.exit(f"图片不存在: {p}")
    release = _load_release(args.source)
    expires = str(release.get("expires_at", ""))
    group = str(release.get("group", ""))
    if expires and expires < datetime.now().strftime("%Y-%m-%d"):
        sys.exit(f"卡片数据已过期(expires_at={expires}):须回 cardgen 更新事实后 --bump 重新 build")
    scan = scan_text(args.title + (args.body or "") + (args.tags or ""))
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO publish_queue(created_at, source, title, body, tags, images, release_expires, \"group\", scan_result) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), args.source, args.title,
             args.body or "", args.tags or "", json.dumps(images, ensure_ascii=False), expires, group,
             json.dumps(scan, ensure_ascii=False)))
        _log(c, cur.lastrowid, "enqueue",
             f"scan_passed={scan['passed']}" + (f" release_expires={expires}" if expires else "")
             + (f" group={group}" if group else ""))
        print(f"入池 #{cur.lastrowid} [{args.title}] 扫描: {'通过' if scan['passed'] else scan}"
              + (f" | 数据有效期至 {expires}" if expires else "")
              + (f" | 分组[{group}]" if group else ""))


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
        r = c.execute("SELECT status, release_expires FROM publish_queue WHERE id=?", (args.id,)).fetchone()
        if not r or r["status"] != "reviewed":
            sys.exit(f"#{args.id} 非 reviewed 状态,不能排期")
        expires = r["release_expires"] or ""
        sched_day = args.at[:10]
        if expires and sched_day > expires:
            sys.exit(f"#{args.id} 排期日 {sched_day} 超过数据有效期 {expires}:数据将过期,"
                     f"须回 cardgen 更新事实 --bump 后重新入池")
        c.execute("UPDATE publish_queue SET status='scheduled', scheduled_at=? WHERE id=?",
                  (args.at, args.id))
        _log(c, args.id, "schedule", args.at + (f" (数据有效至 {expires})" if expires else ""))
        print(f"#{args.id} → scheduled @ {args.at}"
              + (f"(数据有效至 {expires})" if expires else "") + "(发布动作人工/M3 自动化)")


def due(_args: argparse.Namespace) -> None:
    """已到点的 scheduled 行清单——半自动发布提醒(cron 可挂)。"""
    now = datetime.now().isoformat(timespec="minutes")
    with _conn() as c:
        rows = c.execute(
            "SELECT id,title,scheduled_at,release_expires FROM publish_queue "
            "WHERE status='scheduled' AND scheduled_at<=? ORDER BY scheduled_at", (now,)).fetchall()
    if not rows:
        print(f"无到点待发布项(as of {now})")
        return
    today = now[:10]
    for r in rows:
        stale = " ⚠️数据已过期" if (r["release_expires"] and r["release_expires"] < today) else ""
        print(f"#{r['id']:<3} @ {r['scheduled_at']} {r['title'][:40]}"
              + (f" (有效至 {r['release_expires']})" if r["release_expires"] else "") + stale)
    print(f"共 {len(rows)} 项到点,发布动作人工执行")


def mark(args: argparse.Namespace) -> None:
    if args.status not in STATUSES:
        sys.exit(f"非法状态: {args.status}")
    with _conn() as c:
        c.execute("UPDATE publish_queue SET status=?, published_at=CASE WHEN ?='published' THEN ? ELSE published_at END WHERE id=?",
                  (args.status, args.status, datetime.now().isoformat(timespec="seconds"), args.id))
        _log(c, args.id, "mark", args.status)
        print(f"#{args.id} → {args.status}")


# ── M4 备料(机器备料、人工发布;2026-08-30 平台判定后 M3 自动发稿停用)──
def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _checklist_md(r: sqlite3.Row, imgs: list[str]) -> str:
    return f"""# 人工发布清单 #{r['id']} {r['title']}

- 排期: {r['scheduled_at'] or '(未排期)'} | 数据有效期至: {r['release_expires'] or '无'}
- 合集(如有): {r['group'] or '无'}

## 步骤(全部在手机 App 或本人手开的浏览器里操作,勿用脚本驱动)

1. 上传图片,按顺序: {', '.join(imgs) if imgs else '(无图片)'}
2. 标题 ← 复制 `标题.txt`
3. 正文+话题标签 ← 复制 `正文.txt`(末段已含 tags)
4. 若图片为 AI 生成,勾选平台的「AI 生成内容」声明
5. 加入合集「{r['group']}」(如有)
6. 发布前核对图片 foot 处免责话术仍在
7. 发布成功后回填台账: `.venv/bin/python scripts/content_publisher/queue.py mark {r['id']} published`
"""


def prep(args: argparse.Namespace) -> None:
    """备料:scheduled 且到点的行(或指定 id)落盘图片+文案+CHECKLIST,状态 → prepped。"""
    now = datetime.now().isoformat(timespec="minutes")
    with _conn() as c:
        if args.id:
            rows = c.execute("SELECT * FROM publish_queue WHERE id=?", (args.id,)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM publish_queue WHERE status='scheduled' AND scheduled_at<=? "
                "ORDER BY scheduled_at", (now,)).fetchall()
    if not rows:
        print(f"无可备料项(不带 id 时取已到点的 scheduled,as of {now})")
        return
    today = _today()
    for r in rows:
        if r["status"] != "scheduled":
            sys.exit(f"#{r['id']} 状态为 {r['status']},仅 scheduled 可备料")
        if r["release_expires"] and r["release_expires"] < today:
            print(f"跳过 #{r['id']}: 数据已过期(有效至 {r['release_expires']}),须回 cardgen bump 重发")
            continue
        safe = re.sub(r"[^\w\u4e00-\u9fff-]", "_", r["title"])[:24]
        out = PREP_DIR / f"{today}_{r['id']}_{safe}"
        out.mkdir(parents=True, exist_ok=True)
        imgs: list[str] = []
        for p in json.loads(r["images"] or "[]"):
            src = Path(p)
            if not src.exists():
                sys.exit(f"#{r['id']} 图片不存在: {p}(源被移动/清理,重新 enqueue)")
            shutil.copy2(src, out / src.name)
            imgs.append(src.name)
        (out / "标题.txt").write_text(r["title"], encoding="utf-8")
        (out / "正文.txt").write_text((r["body"] or "").strip() + "\n\n" + (r["tags"] or ""),
                                      encoding="utf-8")
        (out / "CHECKLIST.md").write_text(_checklist_md(r, imgs), encoding="utf-8")
        with _conn() as c:
            c.execute("UPDATE publish_queue SET status='prepped' WHERE id=?", (r["id"],))
            _log(c, r["id"], "prep", str(out))
        print(f"#{r['id']} 备料完成 → {out}")


def cmd_login(_args: argparse.Namespace) -> None:
    sys.exit(_AUTO_PUBLISH_DISABLED)


def publish(args: argparse.Namespace) -> None:  # noqa: ARG001
    sys.exit(_AUTO_PUBLISH_DISABLED)


def publish_due(args: argparse.Namespace) -> None:  # noqa: ARG001
    sys.exit(_AUTO_PUBLISH_DISABLED)


def list_queue(args: argparse.Namespace) -> None:
    q = ("SELECT id,created_at,source,title,tags,images,platform,status,scheduled_at,"
         "published_at,release_expires,\"group\",scan_result FROM publish_queue")
    params: tuple = ()
    if args.status:
        q += " WHERE status=?"
        params = (args.status,)
    with _conn() as c:
        rows = c.execute(q + " ORDER BY id", params).fetchall()
        if getattr(args, "json", False):
            print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
            return
        for r in rows:
            print(f"#{r['id']:<3} [{r['status']:<9}] {r['title'][:38]:<40} {(r['tags'] or '')[:30]} "
                  f"{r['scheduled_at'] or ''} {('有效至' + r['release_expires']) if r['release_expires'] else ''}")


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
    l = sub.add_parser("list"); l.add_argument("--status"); l.add_argument("--json", action="store_true")
    l.set_defaults(func=list_queue)
    d = sub.add_parser("due"); d.set_defaults(func=due)
    sub.add_parser("login").set_defaults(func=cmd_login)
    pr = sub.add_parser("prep"); pr.add_argument("id", type=int, nargs="?", default=None)
    pr.set_defaults(func=prep)
    p = sub.add_parser("publish"); p.add_argument("id", type=int, nargs="?", default=None)
    p.set_defaults(func=publish)
    pd = sub.add_parser("publish-due"); pd.add_argument("--dry-run", action="store_true")
    pd.set_defaults(func=publish_due)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
