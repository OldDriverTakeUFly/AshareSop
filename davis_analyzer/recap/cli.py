"""recap CLI:python -m davis_analyzer.recap {run|select|script|sheet|audio|post|status}。"""
from __future__ import annotations

import argparse
from datetime import datetime


def _conn():
    from stockhot.data_layer.market_db import get_connection
    return get_connection()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recap", description="每晚NBA解说式复盘短视频")
    sub = p.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status", help="查看台账")
    st.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    st.set_defaults(func=cmd_status)
    return p


def cmd_status(args) -> None:
    from davis_analyzer.recap import db
    conn = _conn()
    try:
        db.ensure_tables(conn)
        row = db.get_episode(conn, args.date)
    finally:
        conn.close()
    if not row:
        print(f"{args.date}: 无台账(未选片)")
        return
    seg_n = len((row.get("episode") or {}).get("segments", []))
    print(f"{args.date}: status={row['status']} "
          f"candidates={len(row.get('candidates') or [])} segments={seg_n}")


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)
