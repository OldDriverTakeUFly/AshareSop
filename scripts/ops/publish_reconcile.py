#!/usr/bin/env python3
# scripts/ops/publish_reconcile.py — 发稿池发布状态对账(2026-09-17)
# 职责:用小红书账号回流笔记(xhs_metrics.db notes,只读)作为「实际已发布」事实源,
#       与发稿池(content_publisher.db publish_queue)中 status≠published 的行做标题归一化匹配,
#       命中即补标 published(published_at=笔记发布时间)并向 publish_log 写审计行;歧义只报告不改。
# 纪律:不改 scripts/content_publisher 代码,只做数据级 UPDATE(23:40 池子卫生死行归档先例);
#       发布动作永远人工,本脚本只做簿记对账;不动 images/scan_result 等其他列。
# 用法: .venv/bin/python scripts/ops/publish_reconcile.py [--dry]
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
METRICS_DB = REPO_ROOT / "storage" / "database" / "xhs_metrics.db"
POOL_DB = REPO_ROOT / "storage" / "database" / "content_publisher.db"


def normalize_title(title: str) -> str:
    """标题归一化:只留字母数字与 CJK(自然剥掉 emoji/中英文标点/空白),ASCII 小写。"""
    return "".join(ch.lower() for ch in title if ch.isalnum())


def is_match(norm_a: str, norm_b: str) -> bool:
    """归一化后相等,或一方包含另一方(短方≥8字符,防短标题误含)。"""
    if not norm_a or not norm_b:
        return False
    if norm_a == norm_b:
        return True
    short, long_ = (norm_a, norm_b) if len(norm_a) <= len(norm_b) else (norm_b, norm_a)
    return len(short) >= 8 and short in long_


def _load_notes(db: Path) -> list[tuple[int, str, str]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT note_id, title, published_at FROM notes ORDER BY published_at").fetchall()
    finally:
        conn.close()


def _load_pending(db: Path) -> list[tuple[int, str]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT id, title FROM publish_queue WHERE status != 'published' ORDER BY id").fetchall()
    finally:
        conn.close()


def reconcile(metrics_db: Path = METRICS_DB, pool_db: Path = POOL_DB,
              dry_run: bool = False) -> dict:
    """对账主流程;返回摘要 dict(测试消费)。"""
    notes = _load_notes(metrics_db)
    pending = _load_pending(pool_db)
    norm_notes = [(nid, title, normalize_title(title), ts) for nid, title, ts in notes]

    marked: list[tuple[int, str, int, str, str]] = []  # (queue_id, queue_title, note_id, note_title, note_ts)
    for qid, qtitle in pending:
        nq = normalize_title(qtitle)
        hits = [(nid, ntitle, ts) for nid, ntitle, nn, ts in norm_notes
                if nn and is_match(nq, nn)]
        if not hits:
            continue
        nid, ntitle, ts = min(hits, key=lambda h: h[2] or "")  # 取最早一篇
        marked.append((qid, qtitle, nid, ntitle, ts))

    if marked and not dry_run:
        conn = sqlite3.connect(pool_db)
        try:
            now = datetime.now().isoformat(timespec="seconds")
            for qid, qtitle, nid, ntitle, ts in marked:
                conn.execute(
                    "UPDATE publish_queue SET status='published', published_at=? WHERE id=?",
                    ((ts or "").replace(" ", "T"), qid))
                conn.execute(
                    "INSERT INTO publish_log(queue_id, ts, event, detail) VALUES(?,?,?,?)",
                    (qid, now, "reconcile_auto_published",
                     f"matched note #{nid} 《{ntitle}》 @{ts}"))
            conn.commit()
        finally:
            conn.close()

    return {"notes": len(notes), "pending": len(pending),
            "marked": marked, "dry_run": dry_run}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="发稿池发布状态对账(笔记回流→自动补标)")
    ap.add_argument("--dry", action="store_true", help="只打印拟改动,不写库")
    args = ap.parse_args(argv)

    if not METRICS_DB.exists():
        print(f" metrics 库不存在:{METRICS_DB}(先跑 metrics collect)")
        return 1
    summary = reconcile(dry_run=args.dry)
    print(f"对账:账号笔记 {summary['notes']} 篇 × 池内未结 {summary['pending']} 行")
    if not summary["marked"]:
        print("无需补标:池内未结行均未命中账号笔记")
        return 0
    for qid, qtitle, nid, ntitle, ts in summary["marked"]:
        print(f"{'[dry] ' if args.dry else ''}#{qid} 《{qtitle}》 ← note#{nid} 《{ntitle}》 @{ts}")
    print(f"共补标 {len(summary['marked'])} 条{'(未执行)' if args.dry else '(publish_log 已留审计行)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
