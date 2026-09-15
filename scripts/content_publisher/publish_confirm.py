#!/usr/bin/env python3
# content_publisher/publish_confirm.py — 发布自动确认对账(2026-09-13;2026-09-15 修)
# 依据:每晚 21:10 小红书数据回流(metrics collect)落库的笔记清单(xhs_metrics.db.notes)。
# 逻辑:发稿池 draft/reviewed/prepped/到点 scheduled 行 × 已发布笔记做标题归一化前缀匹配——
#   命中(且笔记发布时间 ≥ 行入池时间,防复活重发的同标题旧卡误对账)→ 自动标记 published
#   (留 publish_log 'auto_confirm',note_id 记真实笔记 id)并推送确认到红薯运营群;
#   未命中的 prepped → 推送「待发布/未检出」提醒,人工核对。
# 2026-09-15 修:9/13 起卡片由 cron 直接入池停在 draft、人工直发,旧扫描集
#   (prepped/到点 scheduled)导致已发布行永远不被对账(每晚播报「无待对账项」)。
# 不新增任何爬取;只消费已有回流。幂等:状态迁移本身幂等,推送按日锁。
# 用法: .venv/bin/python scripts/content_publisher/publish_confirm.py [--dry]
# systemd: publish-confirm.timer 每日 21:25(21:10 回流采集之后)。
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent.parent
# 防自遮蔽(同 queue.py 头注):本目录 queue.py 会挡掉 stdlib queue,anyio 导入即炸——
# 本脚本不 import 发稿池模块,直接把本目录移出 sys.path
sys.path = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(REPO_ROOT))

QUEUE_DB = REPO_ROOT / "storage" / "database" / "content_publisher.db"
METRICS_DB = REPO_ROOT / "storage" / "database" / "xhs_metrics.db"
LOCK_DIR = REPO_ROOT / "logs" / ".publish_confirm"


def _norm(title: str) -> str:
    """标题归一化:去 emoji/标点/空白,小写——笔记列表标题会被截断,取前缀匹配。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", title).lower()


def _parse(ts: str | None) -> datetime | None:
    """回流 published_at('2026-09-14 18:08')与池 created_at(ISO带T)统一解析,失败返回 None。"""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def reconcile(dry: bool = False) -> tuple[list, list]:
    """返回 (自动确认行, 未检出超时行)。"""
    now = datetime.now().isoformat(timespec="minutes")
    q = sqlite3.connect(QUEUE_DB)
    q.row_factory = sqlite3.Row
    rows = q.execute(
        "SELECT * FROM publish_queue WHERE status IN ('draft','reviewed','prepped') "
        "OR (status='scheduled' AND scheduled_at<=?) ORDER BY id", (now,)).fetchall()
    m = sqlite3.connect(METRICS_DB)
    m.row_factory = sqlite3.Row
    notes = m.execute(
        "SELECT note_id,title,published_at FROM notes ORDER BY published_at DESC").fetchall()
    notes_n = [(_norm(r["title"]), r["title"], r["published_at"], r["note_id"],
                _parse(r["published_at"])) for r in notes]

    confirmed, pending = [], []
    for r in rows:
        key = _norm(r["title"])[:10]
        created = _parse(r["created_at"]) or datetime.min
        hit = next((n for n in notes_n
                    if n[0][:10] == key and key and n[4] and n[4] >= created), None)
        if hit:
            confirmed.append((r["id"], r["title"], hit[2]))
            if not dry:
                q.execute("UPDATE publish_queue SET status='published', published_at=?, note_id=? "
                          "WHERE id=?", (hit[2], hit[3], r["id"]))
                q.execute("INSERT INTO publish_log(queue_id,ts,event,detail) VALUES(?,?,?,?)",
                          (r["id"], datetime.now().isoformat(timespec="seconds"),
                           "auto_confirm", f"回流对账命中: 《{hit[1]}》@{hit[2]}"))
        elif r["status"] == "prepped":
            pending.append((r["id"], r["title"]))
    if not dry:
        q.commit()
    q.close()
    m.close()
    return confirmed, pending


async def _report(confirmed: list, pending: list) -> None:
    from dotenv import load_dotenv
    from stockhot.notification.feishu_bot import EnterpriseFeishuNotifier
    load_dotenv(REPO_ROOT / ".env")
    chat = os.environ.get("FEISHU_XHS_CHAT_ID", "")
    if not chat:
        print("未配置 FEISHU_XHS_CHAT_ID,跳过群播报(对账照常)")
        return
    lines = [f"📖 发布对账 {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    if confirmed:
        lines.append(f"✅ 自动确认已发布 {len(confirmed)} 条:")
        lines += [f"  #{i} 《{t[:24]}》@{ts[:16]}" for i, t, ts in confirmed]
    if pending:
        lines.append(f"⏳ 已备料但回流未检出 {len(pending)} 条(请人工核对是否已发):")
        lines += [f"  #{i} 《{t[:24]}》" for i, t in pending]
    if not confirmed and not pending:
        lines.append("无待对账项")
    n = EnterpriseFeishuNotifier(os.environ["FEISHU_APP_ID"],
                                 os.environ["FEISHU_APP_SECRET"], chat)
    await n.send_text("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description="发布自动确认对账(消费 metrics 回流,不新增爬取)")
    ap.add_argument("--dry", action="store_true", help="只打印对账结果,不改状态不推送")
    a = ap.parse_args()
    confirmed, pending = reconcile(dry=a.dry)
    for i, t, ts in confirmed:
        print(f"✅ #{i} {t[:30]} → published @{ts}")
    for i, t in pending:
        print(f"⏳ #{i} {t[:30]} 未检出")
    if a.dry:
        return
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock = LOCK_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.ok"
    if lock.exists():
        print("今日已播报过,跳过群推送")
        return
    asyncio.run(_report(confirmed, pending))
    lock.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")


if __name__ == "__main__":
    main()
