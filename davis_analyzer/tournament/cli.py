"""Tournament CLI (argparse, mirrors paper_trading style)."""

from __future__ import annotations

import argparse


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tournament", description="策略锦标赛/参数进化")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="列出参赛者")
    p_run = sub.add_parser("run", help="运行当期锦标赛并出报告")
    p_run.add_argument("--start", required=True, help="YYYYMMDD")
    p_run.add_argument("--end", required=True, help="YYYYMMDD")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "list":
        from davis_analyzer.tournament.adapters import default_participants
        for p in default_participants():
            print(f"参赛者: {p.name:<24} horizon={p.horizon:<8} version={p.version}")
        return 0
    if args.command == "run":
        from datetime import datetime
        from davis_analyzer.tournament.adapters import default_participants
        from davis_analyzer.tournament.judge import JudgeHarness, trading_calendar
        from davis_analyzer.tournament.report import render_report, write_report
        from davis_analyzer.tournament.scorecard import score_participant
        from davis_analyzer.tushare_client import TushareClient

        client = TushareClient()
        start = datetime.strptime(args.start, "%Y%m%d").date()
        end = datetime.strptime(args.end, "%Y%m%d").date()
        adapters = default_participants()
        judge = JudgeHarness(adapters, client)
        calendar = trading_calendar(client, start, end)
        snap = judge.snapshot(end, calendar)
        from davis_analyzer.market_regime import get_market_regime_with_confirm
        current_regime = get_market_regime_with_confirm(end.strftime("%Y%m%d"))
        scores = {}
        reports_by_participant: dict[str, list] = {}
        for _, reports in snap.items():
            for name, r in reports.items():
                reports_by_participant.setdefault(name, []).append(r)
        for name, reports in reports_by_participant.items():
            scores[name] = score_participant(reports, current_regime)
        text = render_report(snap, scores, current_regime)
        path = write_report(text, end)
        print(f"锦标赛报告已写入: {path}")
        return 0
    return 1
