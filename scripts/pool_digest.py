#!/usr/bin/env python3
# scripts/pool_digest.py — 发稿池待办摘要推送(2026-09-13)
# 职责:只读 content_publisher.db,把「待排期(draft/reviewed)」清单推到运维群(飞书 XHS 专用群),
#       与 prep_push(已备料项的图文推送)互补:本脚本管"入池可见",prep_push 管"发布前备料"。
# 纪律:只读池库,不改 content_publisher 任何代码;推送≠发布,发布永远人工。
# 用法: .venv/bin/python scripts/pool_digest.py [--dry]
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB = REPO_ROOT / "storage" / "database" / "content_publisher.db"


def _pool_rows() -> list[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, title, status, created_at, release_expires, images, scan_result "
        "FROM publish_queue WHERE status IN ('draft','reviewed') ORDER BY id").fetchall()
    n_sched = conn.execute(
        "SELECT COUNT(*) FROM publish_queue WHERE status IN ('scheduled','prepped')").fetchone()[0]
    n_pub_today = conn.execute(
        "SELECT COUNT(*) FROM publish_queue WHERE status='published' "
        "AND date(published_at)=date('now','localtime')").fetchone()[0]
    conn.close()
    return rows, n_sched, n_pub_today


def _fmt(rows: list[sqlite3.Row], n_sched: int, n_pub_today: int) -> str:
    today = date.today().isoformat()
    lines = [f"📋 发稿池待办 · {datetime.now().strftime('%m-%d %H:%M')}",
             f"今日已发布 {n_pub_today} 篇 | 已排期/已备料 {n_sched} 篇(备料后由 prep_push 图文推送)",
             f"待排期 {len(rows)} 篇:"]
    for r in rows:
        created = (r["created_at"] or "")[:10]
        n_img = len([x for x in (r["images"] or "").split(",") if x])
        exp = r["release_expires"] or ""
        warn = ""
        if exp and exp <= today:
            warn = " ⚠已过有效期"
        elif exp and (date.fromisoformat(exp) - date.today()).days <= 2:
            warn = " ⚠有效期临近"
        img = f" · 带图{n_img}张" if n_img else ""
        lines.append(f"· #{r['id']} [{r['status']}] {r['title']}{img}"
                     f"（{created}入池{('，有效至' + exp) if exp else ''}）{warn}")
    lines += ["", "操作链: review <id> → schedule <id> \"YYYY-MM-DD 20:00\" → (19:30 prep_push 自动备料并推图) → App 人工发布 → mark <id> published",
              "节奏护栏: ≤2篇/日 · 同晚≤3篇 · 间隔30分钟"]
    return "\n".join(lines)


async def _push(text: str, dry: bool) -> None:
    if dry:
        print("[dry]\n" + text)
        return
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")  # 否则读不到 FEISHU_XHS_CHAT_ID,会误落默认盯盘群(首推实测踩坑)
    from stockhot.notification.feishu_bot import EnterpriseFeishuNotifier, get_feishu_notifier
    xhs_chat = os.environ.get("FEISHU_XHS_CHAT_ID", "")
    if xhs_chat and os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET"):
        notifier = EnterpriseFeishuNotifier(
            os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"], xhs_chat)
    else:
        notifier = get_feishu_notifier()
    if notifier is None:
        raise SystemExit("未配置飞书通知(缺 FEISHU_APP_ID/SECRET 或 XHS_CHAT_ID)")
    await notifier.send_text(text)
    print("已推送发稿池摘要 →", xhs_chat or "默认群")


def main() -> None:
    ap = argparse.ArgumentParser(description="发稿池待办摘要推送(只读,发布仍人工)")
    ap.add_argument("--dry", action="store_true", help="只打印不推送")
    a = ap.parse_args()
    sys.path.insert(0, str(REPO_ROOT))
    rows, n_sched, n_pub = _pool_rows()
    if not rows:
        print("池内无待排期项")
        return
    asyncio.run(_push(_fmt(rows, n_sched, n_pub), dry=a.dry))


if __name__ == "__main__":
    main()
