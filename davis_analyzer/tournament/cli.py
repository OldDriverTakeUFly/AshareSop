"""Tournament CLI (argparse, mirrors paper_trading style)."""

from __future__ import annotations

import argparse


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tournament", description="策略锦标赛/参数进化")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="列出参赛者")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "list":
        from davis_analyzer.tournament.adapters import default_participants
        for p in default_participants():
            print(f"参赛者: {p.name:<24} horizon={p.horizon:<8} version={p.version}")
        return 0
    return 1
