"""intraday 模块 CLI（backfill/status/verify 子命令）。"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime


def cmd_backfill(args: argparse.Namespace) -> None:
    from davis_analyzer.intraday import backfill

    codes = (
        [c.strip() for c in args.codes.split(",") if c.strip()]
        if args.codes else backfill.paper_universe()
    )
    end = args.end or datetime.now().strftime("%Y%m%d")
    start = args.start or backfill.default_start(args.months, end)
    print(f"回补宇宙: {len(codes)} 只 | 窗口 {start}~{end} | freq={args.freq}min")
    stats = backfill.backfill(
        codes, start, end,
        freq_label=f"{args.freq}min", freq_param=str(args.freq),
        sleep_sec=args.sleep,
    )
    print(
        f"回补完成: 新增 {stats['chunks_done']} 月块 / {stats['rows_written']:,} 行, "
        f"跳过 {stats['chunks_skipped']} 块, 失败 {len(stats['failures'])} 块"
    )
    if stats["failures"]:
        print("失败块:", ", ".join(stats["failures"][:20]), "...")
        sys.exit(1)


def cmd_status(args: argparse.Namespace) -> None:
    from davis_analyzer.intraday import db

    conn = db.connect(args.db)
    try:
        summary = db.coverage_summary(conn)
        if summary.empty:
            print("研究库为空——先运行 backfill")
            return
        print(summary.to_string(index=False))
        print(f"\n共 {len(summary)} 只 | {int(summary['minute_rows'].sum()):,} 根分钟K线")
    finally:
        conn.close()


def cmd_verify(args: argparse.Namespace) -> None:
    """对账：分钟聚合的日 high/low/open/close vs 生产库 daily_price。"""
    import pandas as pd
    from davis_analyzer.intraday import backfill, db
    from davis_analyzer.limitup.db import connect as market_connect

    conn = db.connect(args.db)
    try:
        codes = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT ts_code FROM backfill_chunk"
            ).fetchall()
        ]
        rng = conn.execute(
            "SELECT MIN(start_date), MAX(end_date) FROM backfill_chunk"
        ).fetchone()
        minute = (
            db.read_bars(conn, codes, rng[0], rng[1], freq=f"{args.freq}min")
            if codes else pd.DataFrame()
        )
    finally:
        conn.close()
    if codes and minute.empty:
        print("对账失败：分钟数据读取为空")
        sys.exit(1)
    if not codes:
        print("研究库为空——先运行 backfill")
        return
    agg = (
        minute.sort_values(["ts_code", "trade_date", "trade_time"])
        .groupby(["ts_code", "trade_date"])
        .agg(m_open=("open", "first"), m_close=("close", "last"),
             m_high=("high", "max"), m_low=("low", "min"))
        .reset_index()
    )
    mkt = market_connect()
    try:
        daily = pd.read_sql_query(
            "SELECT ts_code, trade_date, open, high, low, close FROM daily_price "
            "WHERE trade_date>=? AND trade_date<=?",
            mkt, params=(rng[0], rng[1]),
        )
    finally:
        mkt.close()
    merged = agg.merge(daily, on=["ts_code", "trade_date"], how="inner")
    if merged.empty:
        print("对账失败：分钟与日线无交集日期")
        sys.exit(1)
    # open 严格校验（连续竞价首笔=日线开盘，实测一致率 99.96%）。
    # close 一致率 ~98%：日线 close 为收盘集合竞价价（14:57-15:00），分钟末根
    # bar 只含连续竞价——日内引擎的收盘成交应取日线 close（竞价可执行）。
    # high/low 仅提示：baostock 分钟 bar 系快照采样构建，会漏掉瞬时极值成交
    # （2026-08-19 全量对账：约 30% 股票日 high/low 低于日线极值，幅度多 <1.5%，
    # 高价/高波动股更明显；对做T回测反而是正确口径——瞬时尖刺不可执行）。
    dev = (merged["m_open"] - merged["open"]).abs()
    ok = (dev <= 0.011).mean()
    print(f"{('open'):>5} [{'OK' if ok >= 0.995 else 'FAIL'}] 最大偏差 {dev.max():.4f} | 一致率 {ok:.2%}")
    hard_fail = ok < 0.995
    for col_m, col_d, miss_dir in (
        ("m_close", "close", 1), ("m_high", "high", -1), ("m_low", "low", 1),
    ):
        dev = (merged[col_m] - merged[col_d]).abs()
        below = ((merged[col_m] - merged[col_d]) * miss_dir) > 0.011
        note = "收盘竞价差异" if col_d == "close" else "快照采样漏瞬时极值"
        print(
            f"{col_d:>5} [INFO] 最大偏差 {dev.max():.4f} | 分钟未及日线占比 "
            f"{below.mean():.1%}（{note}，预期内）"
        )
    print(f"对账样本: {len(merged):,} 个股票日（{merged['ts_code'].nunique()} 只）")
    if hard_fail:
        sys.exit(1)


def cmd_run(args: argparse.Namespace) -> None:
    from davis_analyzer.intraday.engine import IntradayConfig
    from davis_analyzer.intraday.report import run_and_report
    from davis_analyzer.intraday.strategies import (
        AmplitudeGrid, GapDownLongT, GapDownSmart, SpikeFadeShortT,
    )

    all_strats = {
        "gap": GapDownLongT(args.gap),
        "fade": SpikeFadeShortT(args.spike, args.fade),
        "grid": AmplitudeGrid(args.ampth, args.step, args.rungs),
        # 增强版获胜配置（2026-08-19 训练/留出验证）：gap 3% + 14:00 退出
        # + 趋势过滤 + 量比上限（固定参数保持可复现，调参用 --gap 影响的是朴素 gap）
        "smart": GapDownSmart(
            0.03, exit_time="14:00",
            require={"trend_up": True, "vol_ratio1_max": 2.5},
        ),
    }
    picked = [all_strats[k] for k in args.strategies.split(",")]
    config = IntradayConfig(
        per_stock_notional=args.notional, trade_fraction=args.fraction,
    )
    _, text, path = run_and_report(picked, config, args.db, args.out)
    print(text)
    print(f"明细已导出: {path}")


def cmd_shadow(args: argparse.Namespace) -> None:
    from datetime import datetime

    from davis_analyzer.intraday import paper_shadow

    if args.date:
        trade_date = args.date
    else:
        # 默认最近一个已有日线锚的交易日（盘后运行=当日，依赖 19:20 daily_refresh）
        from davis_analyzer.limitup.db import connect as market_connect

        mcon = market_connect()
        try:
            trade_date = mcon.execute(
                "SELECT MAX(trade_date) FROM daily_price"
            ).fetchone()[0] or datetime.now().strftime("%Y%m%d")
        finally:
            mcon.close()

    if args.dry_run:
        print(f"dry-run {trade_date}: 只统计当日可得性/信号，不写台账")
        summary = paper_shadow.run_shadow(trade_date, args.db, persist=False)
        print(
            f"status={summary['status']} universe={summary['n_universe']} "
            f"trades={summary['n_trades']} note={summary['note']}"
        )
        return
    summary = paper_shadow.run_shadow(trade_date, args.db)
    print(
        f"shadow {trade_date}: {summary['status']} | "
        f"宇宙 {summary['n_universe']} | 成交 {summary['n_trades']} 笔"
        + (f" | {summary['note']}" if summary["note"] else "")
    )
    for t in summary["trades"]:
        print(
            f"  {t.ts_code} {t.entry_time}@{t.avg_buy:.2f} -> "
            f"{t.exit_time}@{t.avg_sell:.2f} | {t.net_bps:+.0f}bps | ¥{t.pnl:+,.0f}"
        )


def cmd_shadow_report(args: argparse.Namespace) -> None:
    from davis_analyzer.intraday import paper_shadow

    print(paper_shadow.shadow_report(args.db))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="davis_analyzer.intraday")
    sub = parser.add_subparsers(dest="command", required=True)

    p_backfill = sub.add_parser("backfill", help="baostock 分钟线回补（断点续跑）")
    p_backfill.add_argument("--months", type=int, default=12, help="回补月数")
    p_backfill.add_argument("--start", help="起始日 YYYYMMDD（默认 months 推算）")
    p_backfill.add_argument("--end", help="截止日 YYYYMMDD（默认今天）")
    p_backfill.add_argument("--codes", help="逗号分隔 ts_code；默认模拟盘持仓+上证指数")
    p_backfill.add_argument("--freq", type=int, default=5, choices=(5, 15, 30, 60))
    p_backfill.add_argument("--sleep", type=float, default=0.3, help="每次查询间隔秒")
    p_backfill.add_argument("--db", help="研究库路径（默认 intraday_research.db）")
    p_backfill.set_defaults(func=cmd_backfill)

    p_status = sub.add_parser("status", help="覆盖情况一览")
    p_status.add_argument("--db")
    p_status.set_defaults(func=cmd_status)

    p_verify = sub.add_parser("verify", help="分钟聚合 vs 日线 OHLC 对账")
    p_verify.add_argument("--freq", type=int, default=5, choices=(5, 15, 30, 60))
    p_verify.add_argument("--db")
    p_verify.set_defaults(func=cmd_verify)

    p_run = sub.add_parser("run", help="做T策略回测（分钟沙盒数据）")
    p_run.add_argument("--strategies", default="gap,fade,grid",
                       help="逗号分隔: gap,fade,grid,smart(smart=增强版获胜配置)")
    p_run.add_argument("--notional", type=float, default=200_000,
                       help="每股模拟底仓市值")
    p_run.add_argument("--fraction", type=float, default=0.3, help="每次动用底仓比例")
    p_run.add_argument("--gap", type=float, default=0.02, help="低开阈值")
    p_run.add_argument("--spike", type=float, default=0.02, help="冲高阈值")
    p_run.add_argument("--fade", type=float, default=0.015, help="回落阈值")
    p_run.add_argument("--ampth", type=float, default=0.05, help="前日振幅阈值")
    p_run.add_argument("--step", type=float, default=0.015, help="网格步长")
    p_run.add_argument("--rungs", type=int, default=2, help="网格档数")
    p_run.add_argument("--db", help="研究库路径")
    p_run.add_argument("--out", help="报告输出目录")
    p_run.set_defaults(func=cmd_run)

    p_shadow = sub.add_parser("shadow", help="模拟盘影子验证：盘后回放 smart 配置")
    p_shadow.add_argument("--date", help="交易日 YYYYMMDD（默认最近日线锚）")
    p_shadow.add_argument("--dry-run", action="store_true", help="只看可得性不写台账")
    p_shadow.add_argument("--db")
    p_shadow.set_defaults(func=cmd_shadow)

    p_srep = sub.add_parser("shadow-report", help="影子台账累计报表")
    p_srep.add_argument("--db")
    p_srep.set_defaults(func=cmd_shadow_report)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
