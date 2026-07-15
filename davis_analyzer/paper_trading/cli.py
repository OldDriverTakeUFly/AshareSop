"""CLI for the paper-trading system.

Usage::

    # Create a new paper account
    python -m davis_analyzer.paper_trading init --name davis_v1 \\
        --strategy davis_double --capital 1000000

    # Run one trading day (live mode)
    python -m davis_analyzer.paper_trading run --name davis_v1

    # Backfill from a historical start date
    python -m davis_analyzer.paper_trading backfill --name davis_v1 \\
        --start 20260101

    # Generate performance report
    python -m davis_analyzer.paper_trading report --name davis_v1

    # List all accounts
    python -m davis_analyzer.paper_trading list
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

# Ensure PROJECT_ROOT is set for stockhot imports
os.environ.setdefault("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _ensure_db():
    """Initialize the stockhot.db schema (creates paper_* tables if missing)."""
    from stockhot.storage.database import init_database

    init_database()


def _get_recent_trade_day() -> str:
    """Get the most recent trading day in YYYYMMDD format."""
    from stockhot.data_layer.market_db import get_connection as get_market_conn

    today = datetime.now().strftime("%Y%m%d")
    with get_market_conn() as conn:
        row = conn.execute(
            "SELECT MAX(trade_date) FROM daily_price WHERE ts_code='000001.SH' AND trade_date <= ?",
            (today,),
        ).fetchone()
    if row and row[0]:
        return row[0]
    return today


def cmd_init(args):
    """Create a new paper-trading account."""
    _ensure_db()
    from davis_analyzer.paper_trading.account import PaperAccount
    from davis_analyzer.paper_trading.strategy import STRATEGY_REGISTRY

    if args.strategy not in STRATEGY_REGISTRY:
        print(f"Unknown strategy '{args.strategy}'. Available: {list(STRATEGY_REGISTRY)}")
        sys.exit(1)

    config = {}
    if args.strategy == "davis_double":
        config = {"top_n": args.top_n, "frequency": args.frequency, "min_score": args.min_score}
    elif args.strategy == "factor_threshold":
        config = {
            "max_positions": args.max_positions,
            "buy_momentum": args.buy_momentum,
            "sell_momentum": args.sell_momentum,
        }

    try:
        account = PaperAccount.create(
            name=args.name,
            strategy_name=args.strategy,
            initial_capital=args.capital,
            config=config,
        )
        print(f"\n✓ 模拟盘账户创建成功：{args.name}")
        print(f"  策略：{args.strategy}")
        print(f"  初始资金：{args.capital:,.0f} 元")
        print(f"  配置：{json.dumps(config, ensure_ascii=False)}")
        print(f"\n  使用 `python -m davis_analyzer.paper_trading run --name {args.name}` 开始交易")
    except ValueError as e:
        print(f"\n✗ {e}")
        sys.exit(1)


def cmd_run(args):
    """Run one trading day (live mode)."""
    _ensure_db()
    from davis_analyzer.paper_trading.account import PaperAccount
    from davis_analyzer.paper_trading.executor import DailyExecutor
    from davis_analyzer.paper_trading.strategy import create_strategy

    account = PaperAccount.load(args.name)
    strategy = create_strategy(account.strategy_name, account.config)
    executor = DailyExecutor(account, strategy)

    trade_date = args.date or _get_recent_trade_day()
    print(f"\n运行模拟盘 [{args.name}] 交易日 {trade_date}...")

    result = executor.run_day(trade_date)
    print(f"  状态：{result['status']}")
    if result["status"] == "ok":
        print(f"  信号：{result['signals']} 个")
        print(f"  交易：{result['trades']} 笔")
        print(f"  NAV：{result['nav']:,.0f} 元")
        if result.get("daily_return") is not None:
            dr = result["daily_return"]
            print(f"  日收益：{'+' if dr >= 0 else ''}{dr:.2f}%")

    account.close()


def cmd_backfill(args):
    """Backfill over a historical date range."""
    _ensure_db()
    from davis_analyzer.paper_trading.account import PaperAccount
    from davis_analyzer.paper_trading.executor import run_backfill
    from davis_analyzer.paper_trading.strategy import create_strategy

    account = PaperAccount.load(args.name)
    strategy = create_strategy(account.strategy_name, account.config)

    start = args.start.replace("-", "")
    end = args.end.replace("-", "") if args.end else None

    print(f"\n回填模拟盘 [{args.name}] {start} → {end or 'today'}...")

    # For backfill, we need point-in-time factor scores.
    # Currently the system doesn't have pre-computed historical scores,
    # so davis_double strategy will only produce sells (no new buys).
    # Factor_threshold will also need live computation.
    # This is a known limitation — full backfill requires a scoring pipeline.
    results = run_backfill(account, strategy, start, end)

    ok_days = sum(1 for r in results if r["status"] == "ok")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    print(f"\n完成：{ok_days} 天执行，{skipped} 天跳过")

    if results:
        last = results[-1]
        if "nav" in last:
            print(f"  最终 NAV：{last['nav']:,.0f} 元")

    account.close()


def cmd_report(args):
    """Generate performance report."""
    _ensure_db()
    from davis_analyzer.paper_trading.account import PaperAccount
    from davis_analyzer.paper_trading.report import generate_report

    account = PaperAccount.load(args.name)
    report = generate_report(account)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n✓ 报告已保存到 {args.output}")
    else:
        print(report)

    account.close()


def cmd_list(args):
    """List all paper accounts."""
    _ensure_db()
    from davis_analyzer.paper_trading.account import PaperAccount

    accounts = PaperAccount.list_accounts()
    if not accounts:
        print("\n暂无模拟盘账户。使用 `init` 创建。")
        return

    print(f"\n{'名称':<20} {'策略':<20} {'初始资金':>12} {'现金':>12} {'最后交易日':<12} {'状态'}")
    print("-" * 90)
    for a in accounts:
        print(
            f"{a['name']:<20} {a['strategy_name']:<20} "
            f"{a['initial_capital']:>12,.0f} {a['cash']:>12,.0f} "
            f"{a.get('last_trade') or '—':<12} {a['status']}"
        )


def main():
    parser = argparse.ArgumentParser(
        prog="davis_analyzer.paper_trading",
        description="模拟盘系统 — 基于因子引擎的量化交易模拟",
    )
    subparsers = parser.add_subparsers(dest="command")

    # init
    p_init = subparsers.add_parser("init", help="创建模拟盘账户")
    p_init.add_argument("--name", required=True, help="账户名称")
    p_init.add_argument("--strategy", default="davis_double", help="策略名称")
    p_init.add_argument("--capital", type=float, default=1_000_000, help="初始资金")
    p_init.add_argument("--top-n", type=int, default=10, help="davis_double: 持仓数量")
    p_init.add_argument("--frequency", type=int, default=5, help="davis_double: 调仓频率(天)")
    p_init.add_argument("--min-score", type=float, default=50.0, help="davis_double: 最低评分")
    p_init.add_argument("--max-positions", type=int, default=10, help="factor_threshold: 最大持仓")
    p_init.add_argument("--buy-momentum", type=float, default=70.0, help="factor_threshold: 买入动量阈值")
    p_init.add_argument("--sell-momentum", type=float, default=40.0, help="factor_threshold: 卖出动量阈值")
    p_init.set_defaults(func=cmd_init)

    # run
    p_run = subparsers.add_parser("run", help="运行一个交易日")
    p_run.add_argument("--name", required=True, help="账户名称")
    p_run.add_argument("--date", default=None, help="交易日 (YYYYMMDD，默认最新)")
    p_run.set_defaults(func=cmd_run)

    # backfill
    p_bf = subparsers.add_parser("backfill", help="历史回填")
    p_bf.add_argument("--name", required=True, help="账户名称")
    p_bf.add_argument("--start", required=True, help="开始日期 (YYYYMMDD)")
    p_bf.add_argument("--end", default=None, help="结束日期 (YYYYMMDD)")
    p_bf.set_defaults(func=cmd_backfill)

    # report
    p_rep = subparsers.add_parser("report", help="生成报告")
    p_rep.add_argument("--name", required=True, help="账户名称")
    p_rep.add_argument("--output", "-o", default=None, help="输出文件路径")
    p_rep.set_defaults(func=cmd_report)

    # list
    p_list = subparsers.add_parser("list", help="列出所有账户")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    if args.command:
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
