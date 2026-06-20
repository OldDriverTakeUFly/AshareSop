"""CLI for managing invest holdings.

Usage:
    PYTHONPATH=. .venv/bin/python -m stockhot.invest_sop.holdings_cli <command> [args]

Commands:
    list             — Print all active holdings as formatted table
    add              — Add new holding
    remove           — Soft-delete (set status='closed')
    update-price     — Update current_price
    update-stoploss  — Update stop-loss fields
"""

import argparse
import sys
from datetime import datetime

from stockhot.storage.database import get_connection

TABLE = "invest_holdings"

COLUMNS_DISPLAY = [
    "id", "code", "name", "sector", "entry_price", "current_price",
    "stop_loss_hard", "target_price", "position_pct", "status",
]


def cmd_list(_args: argparse.Namespace) -> None:
    conn = get_connection()
    try:
        cursor = conn.execute(
            f"SELECT {', '.join(COLUMNS_DISPLAY)} FROM {TABLE} "
            f"ORDER BY status = 'active' DESC, id"
        )
        rows = [dict(row) for row in cursor]
    finally:
        conn.close()

    if not rows:
        print("No holdings found.")
        return

    headers = COLUMNS_DISPLAY
    widths = {h: len(h) for h in headers}
    for row in rows:
        for h in headers:
            val = row[h]
            val_str = _fmt(val)
            widths[h] = max(widths[h], len(val_str))

    header_line = " | ".join(h.ljust(widths[h]) for h in headers)
    sep_line = "-+-".join("-" * widths[h] for h in headers)
    print(header_line)
    print(sep_line)

    for row in rows:
        line = " | ".join(_fmt(row[h]).ljust(widths[h]) for h in headers)
        print(line)


def cmd_add(args: argparse.Namespace) -> None:
    entry_price = args.price
    stop_loss_hard = args.stop_loss_hard if args.stop_loss_hard else round(entry_price * 0.88, 2)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")

    data = {
        "code": args.code,
        "name": args.name,
        "sector": args.sector,
        "entry_price": entry_price,
        "current_price": entry_price,
        "stop_loss_hard": stop_loss_hard,
        "target_price": args.target,
        "position_pct": args.position_pct,
        "entry_date": today,
        "status": "active",
        "updated_at": now,
    }
    if args.stop_loss_logic:
        data["stop_loss_logic"] = args.stop_loss_logic
    if args.stop_loss_technical:
        data["stop_loss_technical"] = args.stop_loss_technical
    if args.davis_score_at_buy is not None:
        data["davis_score_at_buy"] = args.davis_score_at_buy
    if args.thesis_snapshot_json is not None:
        data["thesis_snapshot_json"] = args.thesis_snapshot_json

    conn = get_connection()
    try:
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        sql = f"INSERT INTO {TABLE} ({cols}) VALUES ({placeholders})"
        cur = conn.execute(sql, tuple(data.values()))
        conn.commit()
        print(f"[OK] Added holding id={cur.lastrowid} code={args.code} name={args.name}")
    finally:
        conn.close()


def cmd_remove(args: argparse.Namespace) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        cur = conn.execute(
            f"UPDATE {TABLE} SET status='closed', updated_at=? WHERE id=?",
            (now, args.id),
        )
        conn.commit()
        if cur.rowcount == 0:
            print(f"[ERROR] Holding id={args.id} not found.")
            sys.exit(1)
        print(f"[OK] Holding id={args.id} set to closed.")
    finally:
        conn.close()


def cmd_update_price(args: argparse.Namespace) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        cur = conn.execute(
            f"UPDATE {TABLE} SET current_price=?, updated_at=? WHERE id=?",
            (args.price, now, args.id),
        )
        conn.commit()
        if cur.rowcount == 0:
            print(f"[ERROR] Holding id={args.id} not found.")
            sys.exit(1)
        print(f"[OK] Holding id={args.id} current_price={args.price}")
    finally:
        conn.close()


def cmd_update_stoploss(args: argparse.Namespace) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updates: dict[str, object] = {"updated_at": now}

    if args.logic is not None:
        updates["stop_loss_logic"] = args.logic
    if args.technical is not None:
        updates["stop_loss_technical"] = args.technical
    if args.hard is not None:
        updates["stop_loss_hard"] = args.hard

    if len(updates) == 1:
        print("[WARN] No stop-loss values specified. Nothing to update.")
        return

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [args.id]

    conn = get_connection()
    try:
        cur = conn.execute(
            f"UPDATE {TABLE} SET {set_clause} WHERE id=?",
            tuple(values),
        )
        conn.commit()
        if cur.rowcount == 0:
            print(f"[ERROR] Holding id={args.id} not found.")
            sys.exit(1)
        fields = [k for k in updates if k != "updated_at"]
        print(f"[OK] Holding id={args.id} updated: {', '.join(fields)}")
    finally:
        conn.close()


def _fmt(val: object) -> str:
    if val is None:
        return "-"
    if isinstance(val, float):
        return f"{val:.2f}"
    return str(val)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="holdings_cli",
        description="Manage invest holdings",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all holdings")

    p_add = sub.add_parser("add", help="Add a new holding")
    p_add.add_argument("--code", required=True)
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--sector", required=True)
    p_add.add_argument("--price", required=True, type=float)
    p_add.add_argument("--stop-loss", required=True, type=float, dest="stop_loss_logic")
    p_add.add_argument("--target", required=True, type=float)
    p_add.add_argument("--position-pct", required=True, type=float, dest="position_pct")
    p_add.add_argument("--stop-loss-hard", type=float, dest="stop_loss_hard")
    p_add.add_argument("--stop-loss-technical", type=float, dest="stop_loss_technical")
    p_add.add_argument("--davis-score", type=float, default=None, dest="davis_score_at_buy",
                        help="Davis Double score at buy time (for thesis tracking)")
    p_add.add_argument("--thesis-snapshot", type=str, default=None, dest="thesis_snapshot_json",
                        help='JSON string of thesis snapshot, e.g. \'{"percentile_rank": 80}\'')

    p_rm = sub.add_parser("remove", help="Close a holding")
    p_rm.add_argument("--id", required=True, type=int)

    p_up = sub.add_parser("update-price", help="Update current price")
    p_up.add_argument("--id", required=True, type=int)
    p_up.add_argument("--price", required=True, type=float)

    p_sl = sub.add_parser("update-stoploss", help="Update stop-loss fields")
    p_sl.add_argument("--id", required=True, type=int)
    p_sl.add_argument("--logic", type=float, default=None)
    p_sl.add_argument("--technical", type=float, default=None)
    p_sl.add_argument("--hard", type=float, default=None)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "list": cmd_list,
        "add": cmd_add,
        "remove": cmd_remove,
        "update-price": cmd_update_price,
        "update-stoploss": cmd_update_stoploss,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
