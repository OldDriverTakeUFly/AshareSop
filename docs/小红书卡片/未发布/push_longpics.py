#!/usr/bin/env python3
# push_longpics.py —— 剪刀差系列三张长图卡 → 飞书红薯运营群推送(2026-09-18 08:00 job)
# 纪律:推送≠发布,发布永远人工;tags 必须是消息最后一行(2026-09-15 铁律);幂等锁;时效闸。
# 用法: .venv/bin/python docs/小红书卡片/未发布/push_longpics.py [--dry] [--force]
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]  # 仓库根(docs/小红书卡片/未发布 → 上三级)
sys.path.insert(0, str(ROOT))
for _env in [ROOT / ".env"]:
    if _env.exists():
        for _ln in _env.read_text().splitlines():
            if "=" in _ln and not _ln.strip().startswith("#"):
                _k, _, _v = _ln.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
LOCK_DIR = BASE / ".push_locks"
# 时效闸:图上数据为 9-15/9-16 口径,5 天发布窗口,超窗禁止推送
EXPIRE_DATE = date(2026, 9, 20)
PROJECTS = [
    ("长文图卡_镍出海链", 1),
    ("长文图卡_碳价CBAM", 2),
    ("长文图卡_电解家族", 3),
]


def parse_copy(md: Path) -> tuple[str, str, str]:
    """从文案.md 提取 (标题, 正文, tags)。格式由本工程固定,解析失败即中止。"""
    lines = md.read_text(encoding="utf-8").splitlines()
    title = tags = ""
    body_start = body_end = None
    for i, ln in enumerate(lines):
        if ln.startswith("**标题**:"):
            title = ln.split(":", 1)[1].strip()
        elif ln.startswith("**tags**:"):
            tags = ln.split(":", 1)[1].strip()
        elif ln.startswith("**正文**:"):
            body_start = i + 1
        elif ln.startswith("## 三、"):
            body_end = i
    if not (title and tags and body_start and body_end):
        raise SystemExit(f"文案解析失败: {md}")
    body = "\n".join(lines[body_start:body_end]).strip()
    return title, body, tags


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只构建消息不发送")
    ap.add_argument("--force", action="store_true", help="忽略当日幂等锁重推")
    args = ap.parse_args()

    if date.today() > EXPIRE_DATE:
        raise SystemExit(f"时效闸:数据快照已过 5 天窗口(至 {EXPIRE_DATE}),须刷新长图后再推")

    LOCK_DIR.mkdir(exist_ok=True)
    today = date.today().isoformat()
    import os

    from stockhot.notification.feishu_bot import EnterpriseFeishuNotifier

    xhs_chat = os.environ.get("FEISHU_XHS_CHAT_ID", "")
    if not (xhs_chat and os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET")):
        raise SystemExit("未配置飞书通知(缺 FEISHU_APP_ID/SECRET/FEISHU_XHS_CHAT_ID)")
    notifier = EnterpriseFeishuNotifier(
        os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"], xhs_chat)

    sent = 0
    for proj, idx in PROJECTS:
        lock = LOCK_DIR / f"{today}_{proj}.ok"
        if lock.exists() and not args.force:
            print(f"{proj}: 当日已推送(幂等锁),跳过")
            continue
        d = BASE / proj
        img = d / "长图.png"
        title, body, tags = parse_copy(d / "文案.md")
        # 铁律:tags 是消息最后一行;运营提示只放头部括号行
        text = (f"【剪刀差系列长图卡 第{idx}/3篇·发布请在手机App人工完成,图+文一起发】\n"
                f"{title}\n\n{body}\n\n{tags}")
        if args.dry:
            print(f"[dry] {proj}: {img.name}({img.stat().st_size // 1024}KB) + 文案{len(text)}字 | 末行: {text.splitlines()[-1]}")
            continue
        await notifier.send_image(str(img))
        await notifier.send_text(text)
        lock.write_text("ok")
        sent += 1
        print(f"{proj}: 已推送(图+文案)")
    if not args.dry:
        print(f"完成:共推送 {sent}/3")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit as e:
        print(e, file=sys.stderr)
        sys.exit(2)
