#!/usr/bin/env python3
# content_publisher/prep_push.py — 自动备料 + 飞书群推送(2026-09-13)
# 流程:扫发稿池到点项 → queue.py prep 备料(状态机/时效闸唯一真相源) → 当日新备料逐条推送
#       封面图 + 标题/正文文案到运维群(企业机器人,复用 stockhot feishu_bot)。
# 纪律:推送≠发布,发布动作永远人工(2026-08-30 平台判定后口径);幂等锁 logs/.prep_push/{date}_{qid}.ok。
# 用法: .venv/bin/python scripts/content_publisher/prep_push.py [--dry] [--force]
# systemd: prep-push.timer 每日 19:30 触发(20:00 黄金档前完成备料与群通知)。
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent.parent
sys.path.insert(0, str(REPO_ROOT))  # stockhot 包可达

DB = REPO_ROOT / "storage" / "database" / "content_publisher.db"
PREP_DIR = REPO_ROOT / "storage" / "publish_prep"
LOCK_DIR = REPO_ROOT / "logs" / ".prep_push"


def _run_prep() -> str:
    """调 queue.py prep 备料全部到点项(CLI 闸门唯一真相源)。"""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "queue.py"), "prep"],
        capture_output=True, text=True, cwd=REPO_ROOT)
    if proc.returncode != 0:
        print(proc.stdout + proc.stderr, file=sys.stderr)
        raise SystemExit(f"queue.py prep 失败(exit={proc.returncode})")
    return proc.stdout


def _due_today() -> list[sqlite3.Row]:
    """当日完成备料(prepped + 今日 prep 日志)且未推送过的行。"""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT q.id, q.title, q.body, q.tags, q.scheduled_at, q.release_expires, "
        "MAX(CASE WHEN l.event='prep' THEN l.ts END) AS prep_ts "
        "FROM publish_queue q JOIN publish_log l ON l.queue_id=q.id "
        "WHERE q.status='prepped' GROUP BY q.id HAVING prep_ts LIKE ? ORDER BY q.id",
        (today + "%",)).fetchall()
    conn.close()
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for r in rows:
        lock = LOCK_DIR / f"{today}_{r['id']}.ok"
        if lock.exists():
            continue
        out.append(r)
    return out


async def _push(rows: list[sqlite3.Row], dry: bool) -> None:
    """推送到「红薯财经博主运营」专用群(FEISHU_XHS_CHAT_ID,与盯盘/雷达群隔离);缺省回退默认群。"""
    import os
    from stockhot.notification.feishu_bot import EnterpriseFeishuNotifier, get_feishu_notifier
    xhs_chat = os.environ.get("FEISHU_XHS_CHAT_ID", "")
    if xhs_chat and os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET"):
        notifier = EnterpriseFeishuNotifier(
            os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"], xhs_chat)
    else:
        notifier = get_feishu_notifier()
    if notifier is None:
        raise SystemExit("未配置飞书通知(缺 FEISHU_APP_ID/SECRET/CHAT_ID 或 WEBHOOK_URL)")
    today = datetime.now().strftime("%Y-%m-%d")
    for r in rows:
        preps = sorted(PREP_DIR.glob(f"*_{r['id']}_*"), key=lambda p: p.stat().st_mtime)
        if not preps:
            print(f"#{r['id']} 无备料目录,跳过推送", file=sys.stderr)
            continue
        imgs = sorted(preps[-1].glob("*.png"))
        if not imgs:
            print(f"#{r['id']} 备料目录无 PNG", file=sys.stderr)
            continue
        text = (f"【今日备料 #{r['id']}】{r['title']}\n"
                f"排期 {r['scheduled_at']} | 数据有效至 {r['release_expires'] or '无'}\n\n"
                f"{(r['body'] or '').strip()}\n\n{r['tags'] or ''}\n"
                "— 发布请在手机App人工完成,发后回管理台标记")
        if dry:
            print(f"[dry] #{r['id']} {imgs[0].name} + {len(text)}字文案")
            continue
        await notifier.send_image(str(imgs[0]))
        await notifier.send_text(text)
        (LOCK_DIR / f"{today}_{r['id']}.ok").write_text(datetime.now().isoformat(timespec="seconds"))
        print(f"#{r['id']} 已推送({imgs[0].name})")


def main() -> None:
    ap = argparse.ArgumentParser(description="自动备料+飞书推送(发布仍人工)")
    ap.add_argument("--dry", action="store_true", help="只列计划不推送不备料")
    ap.add_argument("--force", action="store_true", help="忽略当日幂等锁重推")
    a = ap.parse_args()
    if not a.dry:
        print(_run_prep())
    if a.force:
        for p in LOCK_DIR.glob(f"{datetime.now().strftime('%Y-%m-%d')}_*.ok"):
            p.unlink()
    rows = _due_today()
    if not rows:
        print("无待推送的当日新备料项")
        return
    asyncio.run(_push(rows, dry=a.dry))


if __name__ == "__main__":
    main()
