"""CLI for managing invest_watchlist table.

Usage:
    PYTHONPATH=. .venv/bin/python -m stockhot.advisor.watchlist_cli <command> [args]

Commands:
    add       — Add a stock to the watchlist
    list      — List all (or filtered) watching stocks
    remove    — Remove a stock from the watchlist
    update    — Update fields of a watched stock
"""

import argparse
import sys
from datetime import datetime

from stockhot.storage.database import get_connection

TABLE = "invest_watchlist"

COLUMNS_DISPLAY = [
    "code",
    "name",
    "sector",
    "priority",
    "status",
    "trigger_reason",
    "added_date",
]


def cmd_add(args: argparse.Namespace) -> None:
    today = datetime.now().strftime("%Y-%m-%d")

    conn = get_connection()
    try:
        cur = conn.execute(f"SELECT 1 FROM {TABLE} WHERE code = ?", (args.code,))
        if cur.fetchone() is not None:
            print(f"[ERROR] {args.code} 已在关注列表中")
            sys.exit(1)

        data: dict[str, object] = {
            "code": args.code,
            "added_date": today,
        }
        if args.name is not None:
            data["name"] = args.name
        if args.sector is not None:
            data["sector"] = args.sector
        if args.reason is not None:
            data["trigger_reason"] = args.reason
        if args.entry_low is not None:
            data["target_entry_low"] = args.entry_low
        if args.entry_high is not None:
            data["target_entry_high"] = args.entry_high
        if args.stop_loss is not None:
            data["stop_loss_pct"] = args.stop_loss
        if args.priority is not None:
            data["priority"] = args.priority
        if args.notes is not None:
            data["notes"] = args.notes

        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        sql = f"INSERT INTO {TABLE} ({cols}) VALUES ({placeholders})"
        cur = conn.execute(sql, tuple(data.values()))
        conn.commit()
        print(f"[OK] Added to watchlist: code={args.code} name={args.name or '-'}")
    finally:
        conn.close()


def cmd_list(args: argparse.Namespace) -> None:
    query = f"SELECT {', '.join(COLUMNS_DISPLAY)} FROM {TABLE}"
    conditions: list[str] = []
    params: list[object] = []

    if args.status is not None and args.status.lower() != "all":
        conditions.append("status = ?")
        params.append(args.status)
    if args.sector is not None:
        conditions.append("sector = ?")
        params.append(args.sector)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY priority DESC, added_date DESC"

    conn = get_connection()
    try:
        cursor = conn.execute(query, tuple(params))
        rows = [dict(row) for row in cursor]
    finally:
        conn.close()

    if not rows:
        print("No watchlist entries found.")
        return

    headers = COLUMNS_DISPLAY
    widths = {h: len(h) for h in headers}
    for row in rows:
        for h in headers:
            widths[h] = max(widths[h], len(_fmt(row[h])))

    header_line = " | ".join(h.ljust(widths[h]) for h in headers)
    sep_line = "-+-".join("-" * widths[h] for h in headers)
    print(header_line)
    print(sep_line)

    for row in rows:
        line = " | ".join(_fmt(row[h]).ljust(widths[h]) for h in headers)
        print(line)


def cmd_remove(args: argparse.Namespace) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        cur = conn.execute(
            f"UPDATE {TABLE} SET status = 'removed', updated_at = ? WHERE code = ?",
            (now, args.code),
        )
        conn.commit()
        if cur.rowcount == 0:
            print(f"[WARN] {args.code} not found in watchlist. Nothing removed.")
            return
        print(f"[OK] Removed from watchlist: code={args.code}")
    finally:
        conn.close()


def cmd_update(args: argparse.Namespace) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updates: dict[str, object] = {"updated_at": now}

    if args.status is not None:
        updates["status"] = args.status
    if args.priority is not None:
        updates["priority"] = args.priority
    if args.notes is not None:
        updates["notes"] = args.notes
    if args.reason is not None:
        updates["trigger_reason"] = args.reason

    if len(updates) == 1:
        print("[WARN] No fields specified. Nothing to update.")
        return

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [args.code]

    conn = get_connection()
    try:
        cur = conn.execute(
            f"UPDATE {TABLE} SET {set_clause} WHERE code=?",
            tuple(values),
        )
        conn.commit()
        if cur.rowcount == 0:
            print(f"[ERROR] {args.code} not found in watchlist.")
            sys.exit(1)
        fields = [k for k in updates if k != "updated_at"]
        print(f"[OK] Watchlist {args.code} updated: {', '.join(fields)}")
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
        prog="watchlist_cli",
        description="Manage invest_watchlist",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Add a stock to the watchlist")
    p_add.add_argument("code", help="Stock code, e.g. 600519")
    p_add.add_argument("--name", default=None, help="Stock name")
    p_add.add_argument("--sector", default=None, help="Sector classification")
    p_add.add_argument("--reason", default=None, help="Trigger reason for watching")
    p_add.add_argument(
        "--entry-low", type=float, default=None, dest="entry_low", help="Target entry price low"
    )
    p_add.add_argument(
        "--entry-high", type=float, default=None, dest="entry_high", help="Target entry price high"
    )
    p_add.add_argument(
        "--stop-loss", type=float, default=None, dest="stop_loss", help="Stop-loss percentage"
    )
    p_add.add_argument(
        "--priority", type=int, default=None, help="Priority (higher = more important)"
    )
    p_add.add_argument("--notes", default=None, help="Free-text notes")

    p_list = sub.add_parser("list", help="List watchlist entries")
    p_list.add_argument("--status", default="watching", help="Filter by status (default: watching)")
    p_list.add_argument("--sector", default=None, help="Filter by sector")

    p_rm = sub.add_parser("remove", help="Remove a stock from the watchlist")
    p_rm.add_argument("code", help="Stock code to remove")

    p_up = sub.add_parser("update", help="Update fields of a watched stock")
    p_up.add_argument("code", help="Stock code to update")
    p_up.add_argument("--status", default=None, help="New status")
    p_up.add_argument("--priority", type=int, default=None, help="New priority")
    p_up.add_argument("--notes", default=None, help="New notes")
    p_up.add_argument("--reason", default=None, help="New trigger reason")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "add": cmd_add,
        "list": cmd_list,
        "remove": cmd_remove,
        "update": cmd_update,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
