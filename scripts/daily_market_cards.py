# scripts/daily_market_cards.py
"""每日盘面复盘卡管线(2026-09-01):stockhot.db → 连板天梯/龙虎榜 两卡 → validate → render。

用法: .venv/bin/python scripts/daily_market_cards.py --type all [--date 2026-09-01] [--no-render]
纪律:生成不发布——enqueue/发布留人工(责任分层:人管值不值得发)。
缺数据(节假日/扫描未跑)非零退出并说明,不硬造。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from davis_analyzer.cardgen import daily, ledger            # noqa: E402
from davis_analyzer.cardgen.builder import render           # noqa: E402


def _projects_root() -> Path:
    return Path(os.environ.get("CARDGEN_PROJECT_ROOT", REPO_ROOT / "docs" / "小红书卡片"))


def _ledger_db() -> Path | None:
    env = os.environ.get("CARDGEN_LEDGER_DB")
    return Path(env) if env else None


def run_one(kind: str, day: str, do_render: bool) -> bool:
    try:
        proj, topic, report = daily.generate(kind, day, _projects_root(), _ledger_db())
    except daily.DailyDataMissing as e:
        print(f"✗ {kind} {day}: 数据不完整,拒绝生成——{e}")
        return False
    if not report.passed:
        print(f"✗ {kind} {day}: validate 未过({len(report.failures)} 项),未渲染")
        for f in report.failures:
            print(f"    - {f}")
        return False
    print(f"✓ {topic} validate 通过 | as_of={report.as_of} expires={report.expires_at}")
    if not do_render:
        return True
    conn = ledger.connect(_ledger_db())
    try:
        release = render(proj, topic, conn)
        print(f"✓ {topic} 渲染完成 v{release['version']}: {len(release['images'])} 张 PNG | "
              f"过期日 {release['expires_at']}")
    except (SystemExit, RuntimeError) as e:
        print(f"✗ {topic} 渲染失败: {e}")
        return False
    finally:
        conn.close()
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="每日盘面复盘卡(连板天梯+龙虎榜)")
    ap.add_argument("--type", choices=["ladder", "lhb", "all"], default="all")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--no-render", action="store_true", help="只生成+validate,不渲染(调试用)")
    args = ap.parse_args()
    kinds = ["ladder", "lhb"] if args.type == "all" else [args.type]
    ok = all(run_one(k, args.date, not args.no_render) for k in kinds)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
