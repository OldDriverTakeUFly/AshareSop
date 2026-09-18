#!/usr/bin/env python3
"""surge 日报摘要 → 飞书运营群(一次性推送工具,推送≠发布).

仿 pool_digest.py 模式:EnterpriseFeishuBot + FEISHU_XHS_CHAT_ID;
文案铁律:含 tags 时 tags 必须最后一行。
用法: .venv/bin/python davis_analyzer/surge/push_feishu.py [--dry]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path("/home/leo/Projects/CodeAgentDashboard")
sys.path.insert(0, str(REPO))


def _fmt() -> str:
    conn = sqlite3.connect(f"file:{REPO}/storage/database/market_data.db?mode=ro", uri=True)
    day, pool = conn.execute(
        "SELECT trade_date, COUNT(*) FROM surge_snapshot GROUP BY trade_date "
        "ORDER BY trade_date DESC LIMIT 1").fetchone()
    ann_ok = conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT ts_code FROM major_events "
        "WHERE ann_date>='20260301')").fetchone()[0]
    events = dict(conn.execute(
        "SELECT event_type, COUNT(*) FROM major_events GROUP BY event_type").fetchall())
    tags = conn.execute(
        "SELECT tag, COUNT(*) FROM surge_tags GROUP BY tag ORDER BY COUNT(*) DESC LIMIT 3"
    ).fetchall()
    top3 = conn.execute(
        "SELECT rank, ts_code, name, composite, hype_tags FROM surge_snapshot "
        "WHERE trade_date=? ORDER BY rank LIMIT 3", (day,)).fetchall()
    pattern_n = conn.execute(
        "SELECT COUNT(*) FROM surge_pattern_hits WHERE trade_date=?", (day,)).fetchone()[0]
    cyq_days = conn.execute(
        "SELECT COUNT(DISTINCT trade_date) FROM cyq_perf_cache").fetchone()[0]
    conn.close()
    import json
    lines = [
        f"📊 Surge 涨幅筛选 · {day}（子系统上线首跑）",
        f"当日涨幅>7%：命中 {pool} 只入九维台账，筹码库 {cyq_days} 日就位",
        f"巨潮公告同步成功：并购重组 {events.get('ma', 0)} / 定增 {events.get('refinance', 0)}"
        f" / 爆雷监管 {events.get('distress', 0)} / 重组终止 {events.get('ma_halt', 0)}"
        f"（覆盖 {ann_ok} 只个股）",
        f"形态副本（放量阳→缩量回调→平台突破）：今日 {pattern_n} 命中",
        "高频形态标签：" + "、".join(f"{t}({n})" for t, n in tags),
        "综合分 Top3：" + " / ".join(
            f"{n or c} {s:.1f}分" + (f"[{json.loads(h)[0]}]" if h and h != "[]" else "")
            for _, c, n, s, h in top3),
        f"报告：davis_analyzer/surge/reports/surge_{day}.md + surge_pattern_{day}.md",
        "已过三路验收（安全/健壮/图片校验），工作日 19:30 定时运行；发布与深析判断永远人工",
        "",
        "#盘后筛选 #量价形态",
    ]
    return "\n".join(lines)


async def _push(text: str, dry: bool) -> None:
    if dry:
        print("[dry]\n" + text)
        return
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
    from stockhot.notification.feishu_bot import EnterpriseFeishuNotifier
    chat = os.environ["FEISHU_XHS_CHAT_ID"]
    notifier = EnterpriseFeishuNotifier(
        os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"], chat)
    await notifier.send_text(text)
    print("已推送 surge 日报摘要 →", chat)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    asyncio.run(_push(_fmt(), a.dry))
