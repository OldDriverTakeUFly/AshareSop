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
    p_replay = sub.add_parser("replay", help="历史回放（meta 序列+前向模拟曲线）")
    p_replay.add_argument("--start", required=True, help="YYYYMMDD")
    p_replay.add_argument("--end", required=True, help="YYYYMMDD")
    p_evolve = sub.add_parser("evolve", help="进化战役（变异-选择循环+晋升门槛，年度限额）")
    p_evolve.add_argument("--participant", required=True, help="参赛者名，如 davis_balanced")
    p_evolve.add_argument("--start", required=True, help="YYYYMMDD")
    p_evolve.add_argument("--end", required=True, help="YYYYMMDD")
    p_evolve.add_argument("--seed", type=int, default=None, help="随机种子（可复现）")
    p_ch = sub.add_parser("champions", help="冠军存档管理")
    ch_sub = p_ch.add_subparsers(dest="ch_command", required=True)
    ch_sub.add_parser("list", help="列出冠军")
    ch_sub.add_parser("promote", help="从台账晋升最近一次通过的战役为冠军")
    ch_sub.add_parser("deploy", help="生成部署说明（人工同步 constants.py）")
    ch_sub.add_parser("verify", help="校验 CHAMPION_PRESETS 与 DB 现任一致")
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
        from davis_analyzer.tournament.allocator import allocate
        allocation = allocate({k: s.total for k, s in scores.items()})
        text = render_report(snap, scores, current_regime, allocation=allocation)
        path = write_report(text, end)
        print(f"锦标赛报告已写入: {path}")
        return 0
    if args.command == "replay":
        from datetime import datetime
        from davis_analyzer.config import TOURNAMENT_REPORTS_DIR
        from davis_analyzer.tournament.adapters import default_participants
        from davis_analyzer.tournament.judge import JudgeHarness, trading_calendar
        from davis_analyzer.tournament.replay import export_replay, replay
        from davis_analyzer.tushare_client import TushareClient

        client = TushareClient()
        start = datetime.strptime(args.start, "%Y%m%d").date()
        end = datetime.strptime(args.end, "%Y%m%d").date()
        judge = JudgeHarness(default_participants(), client)
        calendar = trading_calendar(client, start, end)
        windows = judge.build_windows(calendar)
        reports_by_window = {w: judge.evaluate_window(*w) for w in windows}
        result = replay(windows, reports_by_window)
        meta_path, forward_path = export_replay(result, TOURNAMENT_REPORTS_DIR)
        print(f"meta 序列: {meta_path}\n前向曲线: {forward_path}")
        return 0
    if args.command == "evolve":
        from datetime import date, datetime

        from davis_analyzer import constants as C
        from davis_analyzer.tournament.adapters import default_participants
        from davis_analyzer.tournament.evolution import (
            build_score_fn, check_promotion, draw_segments,
            improvement_distribution, mutate, perturb_decay,
            run_campaign, split_finals,
        )
        from davis_analyzer.tournament.genome import DAVIS_GENOME
        from davis_analyzer.tournament.judge import JudgeHarness, trading_calendar
        from davis_analyzer.tournament.ledger import (
            LedgerRecord, append_record, count_campaigns, open_db,
        )
        from davis_analyzer.tushare_client import TushareClient

        today = date.today()
        ledger_conn = open_db()
        if count_campaigns(ledger_conn, today.year) >= C.TOURNAMENT_CAMPAIGNS_PER_YEAR:
            print(f"进化战役年度限额已满（{C.TOURNAMENT_CAMPAIGNS_PER_YEAR}/年），拒绝执行")
            return 1

        adapters = {a.name: a for a in default_participants()}
        adapter = adapters[args.participant]
        incumbent = dict(C.TOURNAMENT_DAVIS_PRESETS.get(args.participant, {}))
        client = TushareClient()
        start = datetime.strptime(args.start, "%Y%m%d").date()
        end = datetime.strptime(args.end, "%Y%m%d").date()
        calendar = trading_calendar(client, start, end)
        # 日历长度护栏（Task 10 评审遗留的退化输入边缘）：日历过短时
        # draw_segments 会静默丢尾甚至段内越界，须在入口挡掉
        if len(calendar) < C.TOURNAMENT_FINALS_WINDOW_DAYS + C.TOURNAMENT_SEGMENTS_N * 10:
            print("日历长度不足以支撑进化战役（需决赛段+10 段评估）")
            return 1
        evolve_cal, finals_cal = split_finals(calendar)
        splits = draw_segments(evolve_cal, seed=args.seed)
        judge = JudgeHarness([adapter], client)
        score_fn = build_score_fn(judge, args.participant)

        best, best_sel_score = run_campaign(
            incumbent,
            lambda p, rng: mutate(p, DAVIS_GENOME, rng),
            score_fn,
            selection_ranges=splits[0].selection,
            seed=args.seed,
        )
        improvements = improvement_distribution(
            score_fn, incumbent, best, [s.validation for s in splits]
        )
        # 扰动稳健性：对每个参数 ±20% 重估（简单实现：全参数同向扰动）
        base = score_fn(best, splits[0].selection)
        perturbed = [
            score_fn({k: min(max(float(v) * (1 + sgn * C.TOURNAMENT_PERTURB_PCT), 0.0), 1.0)
                      if DAVIS_GENOME.spec(k).kind == "weight" else v
                      for k, v in best.items()}, splits[0].selection)
            for sgn in (1, -1)
        ]
        decay = perturb_decay(base, perturbed)
        finals_pass = score_fn(best, [(finals_cal[0], finals_cal[-1])]) > \
            score_fn(incumbent, [(finals_cal[0], finals_cal[-1])])
        decision = check_promotion(improvements, decay, finals_pass)

        append_record(ledger_conn, LedgerRecord(
            op_type="evolve", run_date=today,
            participants=[(args.participant, adapter.version)],
            params_version=f"campaign-{today.isoformat()}",
            oos_windows_used=len(splits),
            detail={"improvements": [round(x, 4) for x in improvements],
                    "decay": round(decay, 4), "finals_pass": finals_pass,
                    "ok": decision.ok, "reasons": decision.reasons,
                    "best_params": {k: float(v) for k, v in best.items()}},
        ))
        print(f"晋升判定: {'通过' if decision.ok else '未通过'}")
        for r in decision.reasons:
            print(f"  - {r}")
        print(f"最优参数: {best}")
        print("结果已记入 tournament_ledger（通过后由 champions 流程存档）")
        return 0 if decision.ok else 2
    if args.command == "champions":
        from davis_analyzer import constants as C
        from davis_analyzer.config import TOURNAMENT_REPORTS_DIR
        from davis_analyzer.tournament.champions import (
            incumbents, render_deploy_note, verify_sync,
        )
        from davis_analyzer.tournament.ledger import open_db

        conn = open_db()
        from davis_analyzer.tournament.champions import ensure_tables as ensure_ch
        ensure_ch(conn)
        if args.ch_command == "list":
            for c in incumbents(conn):
                print(f"{c.participant:<20} regime={c.regime:<10} gen={c.generation} params={c.params}")
            return 0
        if args.ch_command == "promote":
            from davis_analyzer.tournament.champions import promote_from_ledger
            rec = promote_from_ledger(conn)
            if rec is None:
                print("没有可晋升的战役（台账中无 ok=true 的 evolve 记录）")
                return 1
            print(f"已晋升: {rec.participant} gen={rec.generation} params={rec.params}")
            return 0
        if args.ch_command == "deploy":
            recs = incumbents(conn)
            note = render_deploy_note(recs)
            path = TOURNAMENT_REPORTS_DIR / "champion_deploy_note.md"
            path.write_text(note, encoding="utf-8")
            print(f"部署说明已生成: {path}（请人工同步 constants.py 与 SOP.md）")
            return 0
        if args.ch_command == "verify":
            problems = verify_sync(conn, dict(C.CHAMPION_PRESETS))
            if problems:
                for p in problems:
                    print(f"不一致: {p}")
                return 1
            print("CHAMPION_PRESETS 与 DB 现任冠军一致")
            return 0
    return 1
