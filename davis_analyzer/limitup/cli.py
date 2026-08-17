"""limitup 模块 CLI（backfill/study/backtest 子命令）。"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

    from davis_analyzer.limitup.backfill import FetchFn


def _make_fetch() -> FetchFn:
    from stockhot.core.tushare_client_safe import safe_tushare_call

    def fetch(trade_date: str, limit_type: str) -> pd.DataFrame | None:
        return safe_tushare_call("limit_list_d", trade_date=trade_date,
                                 limit_type=limit_type)

    return fetch


def cmd_backfill(args: argparse.Namespace) -> None:
    from davis_analyzer.limitup import backfill, db

    if not args.probe and not args.start:
        args.parser.error("--start 仅在 probe 模式可省略")

    conn = db.connect()
    try:
        if args.probe:
            earliest = backfill.probe_earliest(conn, _make_fetch())
            print(f"limit_list_d 最早可用日期: {earliest}")
            return
        end = args.end or datetime.now().strftime("%Y%m%d")
        result = backfill.backfill(conn, args.start, end, _make_fetch())
        print(
            f"回补完成: {result['days_done']} 天, "
            f"{result['rows_written']} 行, 跳过 {result['days_skipped']} 天"
        )
    finally:
        conn.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="davis_analyzer.limitup",
        description="连板打板/抓涨停启动研究模块",
    )
    sub = parser.add_subparsers(dest="command")

    p_bf = sub.add_parser("backfill", help="回补 limit_list_d 涨停池历史")
    p_bf.add_argument("--start", default=None, help="YYYYMMDD")
    p_bf.add_argument("--end", default=None, help="YYYYMMDD，默认今日")
    p_bf.add_argument("--probe", action="store_true", help="只探测历史最早日期")
    p_bf.set_defaults(func=cmd_backfill, parser=p_bf)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command:
        args.func(args)
    else:
        parser.print_help()
