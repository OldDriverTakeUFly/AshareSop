"""surge 日报推送 → 飞书「红薯财经博主运营」群(2026-09-19 拍板随 run 自动推).

纪律: 推送≠发布,发布永远人工;含 tags 的文案 tags 必须最后一行。
用法: python -m davis_analyzer.systems.surge.push_feishu [--dry] [--no-images] [--day YYYYMMDD]
     或 from davis_analyzer.systems.surge.push_feishu import push_day
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DB = REPO / "storage" / "database" / "market_data.db"
REPORTS = REPO / "davis_analyzer" / "surge" / "reports"
sys.path.insert(0, str(REPO))


def latest_day() -> str:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    day = conn.execute(
        "SELECT MAX(trade_date) FROM surge_snapshot").fetchone()[0]
    conn.close()
    return day


def build_text(day: str) -> str:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    pool = conn.execute(
        "SELECT COUNT(*) FROM surge_snapshot WHERE trade_date=?", (day,)).fetchone()[0]
    events = dict(conn.execute(
        "SELECT event_type, COUNT(*) FROM major_events WHERE ann_date>=? "
        "GROUP BY event_type", (day[:4] + "0101",)).fetchall())
    tags = conn.execute(
        "SELECT tag, COUNT(*) FROM surge_tags WHERE trade_date=? "
        "GROUP BY tag ORDER BY COUNT(*) DESC LIMIT 3", (day,)).fetchall()
    top3 = conn.execute(
        "SELECT ts_code, name, composite, hype_tags FROM surge_snapshot "
        "WHERE trade_date=? ORDER BY rank LIMIT 3", (day,)).fetchall()
    pattern_n = conn.execute(
        "SELECT COUNT(*) FROM surge_pattern_hits WHERE trade_date=?",
        (day,)).fetchone()[0]
    conn.close()
    lines = [
        f"📊 Surge 涨幅筛选 · {day}",
        f"当日涨幅>7%：{pool} 只入九维台账（长图见下）",
        f"巨潮事件：并购重组 {events.get('ma', 0)} / 定增 {events.get('refinance', 0)}"
        f" / 爆雷监管 {events.get('distress', 0)}",
        f"形态副本（放量阳→缩量回调→平台突破）：{pattern_n} 命中",
        "高频标签：" + "、".join(f"{t}({n})" for t, n in tags),
        "综合分 Top3：" + " / ".join(
            f"{n or c} {s:.1f}分" + (f"[{json.loads(h)[0]}]" if h and h != "[]" else "")
            for c, n, s, h in top3),
        f"全量产物：davis_analyzer/surge/reports/{day}/（md 两份+长图两张，数字闸把关）",
        "深析判断与发布永远人工",
        "",
        "#盘后筛选 #量价形态",
    ]
    return "\n".join(lines)


async def _push(text: str, images: list[Path], dry: bool) -> None:
    if dry:
        print("[dry]\n" + text)
        for im in images:
            print("[dry][image]", im.name)
        return
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
    from stockhot.notification.feishu_bot import EnterpriseFeishuNotifier
    chat = os.environ["FEISHU_XHS_CHAT_ID"]
    notifier = EnterpriseFeishuNotifier(
        os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"], chat)
    await notifier.send_text(text)
    for im in images:
        await notifier.send_image(str(im))
    print(f"已推送 surge 日报(文本+{len(images)}图) → 财经博主运营群 {chat[:14]}...")


def push_day(day: str | None = None, *, dry: bool = False,
             with_images: bool = True) -> None:
    day = day or latest_day()
    day = day.replace("-", "")
    text = build_text(day)
    images: list[Path] = []
    if with_images:
        day_dir = REPORTS / day
        images = sorted(
            p for name in ("长图_全量", "长图_副本")
            if (p := day_dir / f"{name}_{day}.png").exists())
    asyncio.run(_push(text, images, dry))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--day", default=None)
    a = ap.parse_args()
    push_day(a.day, dry=a.dry, with_images=not a.no_images)
