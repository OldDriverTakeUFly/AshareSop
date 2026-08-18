"""limitup 模块 CLI（backfill/study/backtest/daily/candidates 子命令）。"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
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


def cmd_study(args: argparse.Namespace) -> None:
    import pandas as pd

    from davis_analyzer import config
    from davis_analyzer.limitup import db, patterns, report, study
    from davis_analyzer.limitup.events import build_events
    from davis_analyzer.limitup.robustness import split_is_oos
    from davis_analyzer.limitup.sentiment import build_market_regime

    conn = db.connect()
    try:
        events = build_events(conn, args.start, args.end)
        events = patterns.attach_pattern_features(events, conn, args.start, args.end)
        regime = build_market_regime(conn, args.start, args.end)
        is_ev, oos_ev = split_is_oos(events, args.oos_start)
        sections = [
            ("数据概览", f"事件数 IS={len(is_ev)} / OOS={len(oos_ev)}；"
                        f"样本门槛：收益类≥30、晋级率类≥50（不足标记样本不足）"),
            ("晋级率矩阵（全样本）",
             report.df_to_md_table(study.promotion_matrix(events).reset_index())),
            ("晋级率矩阵 × 形态标签",
             report.df_to_md_table(
                 study.promotion_matrix(events, by=["pattern_label"]).reset_index())),
            ("打板次日开盘收益分布（全样本）",
             report.df_to_md_table(study.return_distribution(events))),
            ("形态标签收益分布",
             report.df_to_md_table(
                 study.feature_effectiveness(events, "pattern_label"))),
            ("龙虎榜有效性",
             report.df_to_md_table(study.feature_effectiveness(
                 events.assign(上榜=lambda d: d["on_lhb"].map({True: "上榜", False: "未榜"})),
                 "上榜"))),
            ("封单强度分档有效性",
             report.df_to_md_table(study.feature_effectiveness(
                 events.assign(封档=lambda d: pd.cut(
                     d["seal_ratio"], [-1, 0.02, 0.05, 100],
                     labels=["弱", "中", "强"])), "封档"))),
            ("封单分档扰动稳定性（±20%）",
             report.df_to_md_table(study.seal_bucket_perturbation(events))),
            ("形态与 regime 阈值扰动稳定性（±20%）",
             report.df_to_md_table(study.threshold_perturbation(
                 events,
                 patterns.read_buffered_prices(events, conn, args.start, args.end),
                 regime))),
            ("情绪 regime 切片",
             report.df_to_md_table(study.regime_slices(events, regime))),
        ]
        out = report.write_report(
            config.LIMITUP_REPORTS_DIR / f"{args.start}-{args.end}_limitup_study.md",
            f"连板打板事件研究 [{args.start} → {args.end}]",
            sections,
        )
        print(f"研究报告已生成: {out}")
    finally:
        conn.close()


def _trades_csv_name(preset: str, start: str, end: str) -> str:
    """交易明细文件名：与 md 报告同窗口段，避免跨窗口静默覆盖."""
    return f"{preset}_{start}-{end}_trades.csv"


def cmd_backtest(args: argparse.Namespace) -> None:
    import pandas as pd

    from davis_analyzer import config
    from davis_analyzer.limitup import db, engine, patterns, report
    from davis_analyzer.limitup.engine import LimitupBacktestConfig, run_sensitivity
    from davis_analyzer.limitup.events import build_events
    from davis_analyzer.limitup.robustness import split_is_oos
    from davis_analyzer.limitup.sentiment import build_market_regime
    from davis_analyzer.limitup.strategies import PRESETS, apply_preset

    conn = db.connect()
    try:
        events = build_events(conn, args.start, args.end)
        events = patterns.attach_pattern_features(events, conn, args.start, args.end)
        regime = build_market_regime(conn, args.start, args.end)
        is_ev, _ = split_is_oos(events, args.oos_start)
        preset = PRESETS[args.preset]
        seal_med = float(is_ev["seal_ratio"].median()) if len(is_ev) else None
        # IS 中位封单过滤仅 relay_2（use_is_median_seal=True）规格内启用，
        # 其余预设不注入该过滤条件（防止规格外过滤）
        seal_arg = seal_med if preset.use_is_median_seal else None
        candidates = apply_preset(
            events, preset, regime=regime, seal_ratio_median=seal_arg
        )
        prices = db.read_daily_prices(
            conn, sorted(candidates["ts_code"].unique()),
            args.start, args.end,
        )
        cfg = LimitupBacktestConfig(initial_capital=args.capital)
        sens = run_sensitivity(candidates, prices, preset, cfg, seed=args.seed)
        rows = pd.DataFrame(
            [{**{"scenario": k}, **vars(v)} for k, v in sens.items()]
        )
        base_trades, base_nav = engine.run_backtest(
            candidates, prices, preset, cfg, scenario=args.fill_scenario,
            seed=args.seed,
        )
        n_days = max(len(base_nav) - 1, 1)
        daily_signal = len(candidates) / max(n_days, 1)
        sections = [
            ("策略与参数", f"预设={args.preset}（{preset.name}）；窗口 "
                        f"{args.start}→{args.end}；IS 中位 seal_ratio="
                        f"{seal_arg if preset.use_is_median_seal else '未启用'}；"
                        f"日均信号数={daily_signal:.2f}"
                        + ("（⚠ 过稀疏）" if daily_signal < 0.5 else "")),
            ("三档成交敏感性", report.df_to_md_table(rows)),
            ("结论纪律",
             "三档方向不一致时结论必须写\"不确定\"（规格 §14.3）；"
             "样本门槛：收益类≥30、晋级率类≥50。"),
        ]
        out_md = report.write_report(
            config.LIMITUP_REPORTS_DIR
            / f"{args.preset}_{args.start}-{args.end}_backtest.md",
            f"打板回测 [{preset.name}]（{args.fill_scenario} 档明细）",
            sections,
        )
        out_csv = config.LIMITUP_REPORTS_DIR / _trades_csv_name(
            args.preset, args.start, args.end
        )
        pd.DataFrame([vars(t) for t in base_trades]).to_csv(out_csv, index=False)
        print(f"回测报告: {out_md}\n交易明细: {out_csv}")
    finally:
        conn.close()


def _write_candidates_report(day: str, md: str) -> Path:
    from davis_analyzer import config

    out = config.LIMITUP_REPORTS_DIR / f"candidates_{day}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    return out


def cmd_candidates(args: argparse.Namespace) -> None:
    """盘后候选清单报告（first_board 口径，规格 §2.3 五节结构）.

    空态防线：regime=="无数据" 或当日 limit_pool 无行 → 报告仍写（注明
    原因）+ stderr 告警 + 退出码 1；候选空但 regime 正常 → 「当日无候选」
    正常退出 0。报告路径 LIMITUP_REPORTS_DIR/candidates_{date}.md。
    """
    import pandas as pd

    from davis_analyzer.limitup import candidates, db

    conn = db.connect()
    try:
        day = (db.normalize_date(args.date) if args.date
               else db.latest_trade_date(conn))
        if day is None:
            print("candidates: daily_price 无数据，无法确定默认交易日",
                  file=sys.stderr)
            sys.exit(1)

        ctx = candidates.candidate_context(conn, day)
        pool_empty = db.read_limit_pool(conn, day, day).empty
        no_data = ctx.get("regime_label") == "无数据"
        if pool_empty or no_data:
            # 两种数据缺失空态：不用陈旧数据生成候选，报告注明原因后退出 1
            reason = (f"{day} 当日 limit_pool 无数据（daily 刷新失败?），"
                      if pool_empty else
                      f"{day} 当日 regime 无数据（日历缺日/空库），")
            md = candidates.render_candidates_md(
                pd.DataFrame(columns=candidates.CANDIDATE_COLUMNS),
                ctx, top=args.top, note=reason + "无法生成候选清单。",
            )
            _write_candidates_report(day, md)
            print(f"candidates: {reason}已写空态报告并退出", file=sys.stderr)
            sys.exit(1)

        cands = candidates.build_candidates(conn, day)
        md = candidates.render_candidates_md(cands, ctx, top=args.top)
        out = _write_candidates_report(day, md)
        if cands.empty:
            print(candidates.empty_candidates_message(ctx))
        else:
            print(f"候选清单已生成: {out}（{len(cands)} 条，"
                  f"enhanced {int(cands['enhanced'].sum())} 条）")
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

    p_st = sub.add_parser("study", help="涨停事件研究（Phase 1）")
    p_st.add_argument("--start", required=True, help="YYYYMMDD")
    p_st.add_argument("--end", required=True, help="YYYYMMDD")
    p_st.add_argument("--oos-start", default="20250701",
                      help="IS/OOS 切分日（默认 20250701）")
    p_st.set_defaults(func=cmd_study)

    p_bt = sub.add_parser("backtest", help="事件驱动打板回测（Phase 2）")
    p_bt.add_argument("--preset", required=True,
                      choices=["first_board", "relay_2", "relay_3"])
    p_bt.add_argument("--start", required=True, help="YYYYMMDD")
    p_bt.add_argument("--end", required=True, help="YYYYMMDD")
    p_bt.add_argument("--oos-start", default="20250701")
    p_bt.add_argument("--fill-scenario", default="base",
                      choices=["base", "optimistic", "pessimistic", "always"])
    p_bt.add_argument("--capital", type=float, default=1_000_000.0)
    p_bt.add_argument("--seed", type=int, default=42)
    p_bt.set_defaults(func=cmd_backtest)

    p_daily = sub.add_parser("daily", help="每日增量刷新（limit_pool/moneyflow/top_list/intraday/corp_event）")
    p_daily.add_argument("--lookback", type=int, default=7,
                         help="回看交易日数（默认 7，自动补漏）")
    p_daily.set_defaults(func=cmd_daily)

    p_cand = sub.add_parser("candidates", help="盘后候选清单报告（first_board 口径，Phase 3）")
    p_cand.add_argument("--date", default=None,
                        help="YYYYMMDD，默认 daily_price 最新交易日")
    p_cand.add_argument("--top", type=int, default=10,
                        help="候选表取前 N 条（默认 10）")
    p_cand.set_defaults(func=cmd_candidates)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command:
        args.func(args)
    else:
        parser.print_help()


def cmd_daily(args: argparse.Namespace) -> None:
    from davis_analyzer.limitup import daily_refresh, db

    conn = db.connect()
    try:
        summary = daily_refresh.run_daily_refresh(conn, lookback_days=args.lookback)
        print(f"每日刷新完成: {summary}")
    finally:
        conn.close()
