"""CLI tool for the advisor system — ask/daily/watchlist subcommands.

Usage:
    python -m stockhot.advisor ask 000001
    python -m stockhot.advisor daily --date 2026-06-22
    python -m stockhot.advisor watchlist list
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import date
from typing import Any

from stockhot.advisor import watchlist_cli
from stockhot.advisor.recommendation_engine import Recommendation, run_for_stock
from stockhot.storage.database import get_connection

MAX_STOCKS_PER_DAILY_RUN = 20
MAX_TELEGRAM_MESSAGES = 5


# ── DB helpers ────


def _get_holding_for_code(code: str) -> dict | None:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM invest_holdings WHERE code = ? AND status = 'active'",
            (code,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_active_holdings() -> list[dict]:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM invest_holdings WHERE status = 'active' ORDER BY id"
        )
        return [dict(row) for row in cursor]
    finally:
        conn.close()


def _get_watchlist() -> list[dict]:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM invest_watchlist WHERE status = 'watching' "
            "ORDER BY priority DESC, added_date DESC"
        )
        return [dict(row) for row in cursor]
    finally:
        conn.close()


# ── Conversion helpers ────


def _rec_to_dict(rec: Recommendation) -> dict[str, Any]:
    """Convert Recommendation to a JSON-serializable dict."""
    d = asdict(rec)
    if d.get("entry_zone"):
        d["entry_zone"] = list(d["entry_zone"])
    return d


def _rec_to_telegram_dict(rec: Recommendation) -> dict[str, Any]:
    """Convert Recommendation to dict format expected by TelegramNotifier."""
    return {
        "code": rec.code,
        "action": rec.action,
        "confidence": rec.confidence,
        "reason": rec.reasoning,
    }


# ── Telegram push ────


def _try_telegram_push(recommendations: list[Recommendation]) -> None:
    """Attempt to push actionable recommendations to Telegram.

    Skips silently if Telegram is not configured (EnvironmentError).
    """
    from stockhot.notification.telegram_bot import (
        TelegramNotifier,
        get_telegram_config,
    )

    try:
        bot_token, chat_id, allowed_user_ids = get_telegram_config()
    except EnvironmentError:
        return

    actionable = [r for r in recommendations if r.recommendation_type != "none"]
    if not actionable:
        return

    rec_dicts = [_rec_to_telegram_dict(r) for r in actionable]
    notifier = TelegramNotifier(bot_token, chat_id, allowed_user_ids)
    asyncio.run(
        notifier.send_recommendations_batch(
            rec_dicts, max_messages=MAX_TELEGRAM_MESSAGES
        )
    )


# ── Command handlers ────


def cmd_ask(args: argparse.Namespace) -> int:
    """Generate recommendation for a single stock, output as JSON."""
    trade_date = args.date or date.today().isoformat()
    holding = _get_holding_for_code(args.code)

    try:
        rec = run_for_stock(
            args.code, trade_date, holding=holding, force=args.force
        )
    except Exception as exc:
        print(
            json.dumps(
                {"error": str(exc), "code": args.code}, ensure_ascii=False
            )
        )
        return 1

    output = _rec_to_dict(rec)
    print(json.dumps(output, ensure_ascii=False))
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    """Run advisor for all holdings + watchlist (batch mode)."""
    trade_date = args.date or date.today().isoformat()

    holdings = _get_active_holdings()
    watchlist = _get_watchlist()

    # Combine into unique list: holdings first, then watchlist
    seen_codes: set[str] = set()
    combined: list[tuple[str, dict | None]] = []

    for h in holdings:
        code = h["code"]
        if code not in seen_codes:
            seen_codes.add(code)
            combined.append((code, h))

    for w in watchlist:
        code = w["code"]
        if code not in seen_codes:
            seen_codes.add(code)
            combined.append((code, None))

    total = len(combined)
    if total > MAX_STOCKS_PER_DAILY_RUN:
        truncated = total - MAX_STOCKS_PER_DAILY_RUN
        combined = combined[:MAX_STOCKS_PER_DAILY_RUN]
        print(
            f"⚠️ 已达 MAX_STOCKS_PER_DAILY_RUN={MAX_STOCKS_PER_DAILY_RUN}"
            f" 上限，跳过 {truncated} 只",
            file=sys.stderr,
        )

    processed = len(combined)
    recommendations: list[Recommendation] = []
    generated = 0
    skipped = 0

    for idx, (code, holding) in enumerate(combined, 1):
        try:
            rec = run_for_stock(
                code, trade_date, holding=holding, force=args.force
            )
            recommendations.append(rec)
            if rec.recommendation_type != "none":
                generated += 1
            else:
                skipped += 1
            print(f"[{idx}/{processed}] {code}: {rec.action} ({rec.confidence})")
        except Exception:
            skipped += 1
            print(f"[{idx}/{processed}] {code}: ERROR")

    # Telegram push (unless skipped)
    if not args.no_telegram and recommendations:
        _try_telegram_push(recommendations)

    print(f"完成: {processed} 只股票, {generated} 条建议生成, {skipped} 条跳过")
    return 0


def _dispatch_watchlist(wl_args: list[str]) -> int:
    """Forward remaining args to watchlist_cli.main()."""
    original_argv = sys.argv
    sys.argv = ["watchlist_cli"] + wl_args
    try:
        watchlist_cli.main()
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    finally:
        sys.argv = original_argv


# ── Parser ────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stockhot-advisor",
        description="AI-powered stock advisor CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # ask subcommand
    ask_parser = sub.add_parser(
        "ask", help="Generate recommendation for a single stock"
    )
    ask_parser.add_argument("code", help="Stock code (e.g. 000001)")
    ask_parser.add_argument(
        "--force", action="store_true", help="Override idempotency"
    )
    ask_parser.add_argument(
        "--date", default=None, help="Trade date (default: today)"
    )

    # daily subcommand
    daily_parser = sub.add_parser(
        "daily", help="Run advisor for all holdings + watchlist"
    )
    daily_parser.add_argument(
        "--date", default=None, help="Trade date (default: today)"
    )
    daily_parser.add_argument(
        "--force", action="store_true", help="Override idempotency"
    )
    daily_parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Skip Telegram push",
    )

    # watchlist subcommand (display only; dispatch intercepts before parsing)
    sub.add_parser(
        "watchlist", help="Manage watchlist (add/list/remove/update)"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point. Returns exit code."""
    if argv is None:
        argv = sys.argv[1:]

    # Intercept watchlist to pass remaining args to watchlist_cli
    if argv and argv[0] == "watchlist":
        return _dispatch_watchlist(argv[1:])

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "ask":
        return cmd_ask(args)
    elif args.command == "daily":
        return cmd_daily(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
