# davis_analyzer/metrics/cli.py
"""CLI 入口:python -m davis_analyzer.metrics {init|collect|record|report}。

print 只在本文件(用户可见 CLI 输出),模块内一律 loguru(项目规范)。
"""
from __future__ import annotations

import argparse
from datetime import datetime

from davis_analyzer.metrics import db as mdb


def cmd_init(args: argparse.Namespace) -> None:
    mdb.init_db(args.account_id, name=args.name)


def cmd_collect(args: argparse.Namespace) -> None:
    from davis_analyzer.metrics.collector import collect
    print(collect(args.account_id))


def cmd_record(args: argparse.Namespace) -> None:
    """人工兜底:录入/校准一条笔记快照或账号快照(source=manual,同时刻覆盖 vision)。"""
    now = args.at or datetime.now().isoformat(timespec="seconds")
    with mdb.connect() as c:
        if args.title:
            note_id = mdb.upsert_note(c, args.account_id, args.title, grp=args.group or "",
                                      published_at=args.published or "")
            mdb.record_note_metrics(c, note_id, now, views=args.views, likes=args.likes,
                                    collects=args.collects, comments=args.comments,
                                    shares=args.shares, source="manual")
            print(f"已记录笔记快照: {args.title} @ {now}")
        else:
            mdb.record_account_metrics(c, args.account_id, now, followers=args.followers,
                                       following=args.following, total_likes=args.total_likes,
                                       source="manual")
            print(f"已记录账号快照: {args.account_id} @ {now}")


def cmd_report(args: argparse.Namespace) -> None:
    from davis_analyzer.metrics.report import report
    report(args.account_id, out_md=args.out)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="metrics", description="小红书运营数据回流台账")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="登记账号")
    i.add_argument("--account-id", required=True)
    i.add_argument("--name", default="")
    i.set_defaults(func=cmd_init)

    c = sub.add_parser("collect", help="半自动采集(浏览器+vision)")
    c.add_argument("--account-id", required=True)
    c.set_defaults(func=cmd_collect)

    r = sub.add_parser("record", help="人工录入/校准")
    r.add_argument("--account-id", required=True)
    r.add_argument("--title", default="", help="笔记标题(留空则记账号快照)")
    r.add_argument("--group", default="")
    r.add_argument("--published", default="")
    r.add_argument("--views", type=int)
    r.add_argument("--likes", type=int)
    r.add_argument("--collects", type=int)
    r.add_argument("--comments", type=int)
    r.add_argument("--shares", type=int)
    r.add_argument("--followers", type=int)
    r.add_argument("--following", type=int)
    r.add_argument("--total-likes", type=int)
    r.add_argument("--at", default="")
    r.set_defaults(func=cmd_record)

    p = sub.add_parser("report", help="聚合报告")
    p.add_argument("--account-id", default=None)
    p.add_argument("--out", default=None, help="另存 markdown 路径")
    p.set_defaults(func=cmd_report)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
