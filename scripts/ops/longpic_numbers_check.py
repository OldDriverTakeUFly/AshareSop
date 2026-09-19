#!/usr/bin/env python3
# longpic_numbers_check.py —— 长图工程数字闸审计(2026-09-18)
# 对所有 长图.html × facts.json 跑 cardgen 数字闸(mask style 属性),
# 报告未锚定数字。用法: [--project 相对路径 ...] 缺省扫全部已知长图工程。
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path("/home/leo/Projects/CodeAgentDashboard")
BASE = ROOT / "docs/发布/小红书/未发布"
sys.path.insert(0, str(ROOT))

from scripts.daily_longpic import numbers_gate  # noqa: E402

DEFAULT_PROJECTS = [
    "长文图卡_镍出海链", "长文图卡_碳价CBAM", "长文图卡_电解家族",
    "长文图卡_AI基建涨价", "长文图卡_全球龙头对照", "长文图卡_国产替代梯度",
    "板块热点复盘/2026-09-18_光通信复活",
    "连板天梯", "龙虎榜", "板块温度",  # 日更系(扫其下全部日期子目录)
]


def main() -> None:
    ap = argparse.ArgumentParser(description="长图数字闸审计")
    ap.add_argument("--project", action="append", default=None,
                    help="相对 未发布/ 的工程路径(可多次);缺省扫全部")
    args = ap.parse_args()

    projects = args.project or DEFAULT_PROJECTS
    targets: list[tuple[str, Path]] = []
    for proj in projects:
        root = BASE / proj
        if not root.exists():
            continue
        if (root / "长图.html").exists():
            targets.append((proj, root))
        else:  # 日更系:日期子目录
            for sub in sorted(root.iterdir()):
                if (sub / "长图.html").exists():
                    targets.append((f"{proj}/{sub.name}", sub))

    total_bad = 0
    for name, d in targets:
        facts = d / "facts.json"
        html = d / "长图.html"
        if not facts.exists():
            print(f"✗ {name}: 缺 facts.json(数字无真相源)")
            total_bad += 1
            continue
        res = numbers_gate(html.read_text(encoding="utf-8"), facts)
        if res is None:
            print(f"⚠ {name}: facts 为叙事锚格式,机器数字闸不可运行(校验层不完备)")
            total_bad += 1
        elif res:
            print(f"✗ {name}: 未锚定 {len(res)} 个 → {res[:12]}")
            total_bad += 1
        else:
            print(f"✓ {name}: 零未锚定")
    print(f"\n审计 {len(targets)} 个工程,异常 {total_bad} 个")
    sys.exit(1 if total_bad else 0)


if __name__ == "__main__":
    main()
