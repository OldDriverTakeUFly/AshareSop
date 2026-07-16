"""eod_review CLI — 盘后复盘引擎命令行入口.

用法::

    python -m stockhot.eod_review review --date 20260715
    python -m stockhot.eod_review backtest --date 20260708
    python -m stockhot.eod_review list --limit 5
"""

from __future__ import annotations

import argparse
import sys
from contextlib import closing
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from stockhot.core.logging import logger
from stockhot.data_layer.market_db import get_connection
from stockhot.eod_review.engine import EODReviewEngine
from stockhot.eod_review.reporter import generate_report

_TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stockhot.eod_review",
        description="盘后日线复盘引擎（量化归因 + 情绪温度计）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # review：当日复盘
    p_review = sub.add_parser("review", help="复盘某交易日")
    p_review.add_argument("--date", "-d", help="交易日 YYYYMMDD（默认今日）")
    p_review.add_argument("--no-cache", action="store_true", help="禁用进程内缓存")
    p_review.add_argument("--no-persist", action="store_true", help="不落库")
    p_review.add_argument(
        "--no-report", action="store_true", help="不生成 markdown 报告"
    )

    # backtest：历史回放
    p_bt = sub.add_parser("backtest", help="回放历史交易日")
    p_bt.add_argument("--date", "-d", required=True, help="交易日 YYYYMMDD")
    p_bt.add_argument("--days", type=int, default=1, help="向前回放天数")

    # list：列出已复盘的日期
    p_list = sub.add_parser("list", help="列出已复盘日期")
    p_list.add_argument("--limit", type=int, default=10)

    args = parser.parse_args(argv)

    if args.command == "review":
        return _cmd_review(args)
    elif args.command == "backtest":
        return _cmd_backtest(args)
    elif args.command == "list":
        return _cmd_list(args)
    return 1


def _resolve_date(date_str: str | None) -> str:
    if date_str:
        return date_str
    return datetime.now(_TZ_SHANGHAI).strftime("%Y%m%d")


def _cmd_review(args) -> int:
    date = _resolve_date(args.date)
    engine = EODReviewEngine()
    result = engine.run_review(
        date, use_cache=not args.no_cache, persist=not args.no_persist
    )

    if not result.ok:
        print(f"❌ {date} 复盘失败：无日线数据（可能非交易日）")
        return 1

    # 控制台摘要
    print(f"\n{'='*60}")
    print(f"  {date} 量化复盘完成")
    print(f"{'='*60}")
    if result.sentiment:
        print(f"  情绪温度计: {result.sentiment.score}/100 ({result.sentiment.label})")
    if result.limit_up_attributions:
        from collections import Counter
        tc = Counter(a.attribution_type for a in result.limit_up_attributions)
        print(f"  涨停归因: {len(result.limit_up_attributions)}只 — {dict(tc)}")
    if result.sector_performance:
        top3 = result.sector_performance[:3]
        print(f"  强势板块Top3: {[(s.name, s.mean_pct) for s in top3]}")
    if result.errors:
        print(f"  ⚠️ 失败维度: {result.errors}")
    print()

    if not args.no_report:
        path = generate_report(result)
        print(f"  📄 报告: {path}")

    return 0


def _cmd_backtest(args) -> int:
    """回放历史交易日（用于验证策略/总结规律）."""
    end_date = datetime.strptime(args.date, "%Y%m%d")
    dates: list[str] = []
    # 向前找 N 个交易日（简单按自然日回退，非交易日会自动跳过）
    cur = end_date
    while len(dates) < args.days:
        ds = cur.strftime("%Y%m%d")
        dates.append(ds)
        cur -= timedelta(days=1)
    dates.reverse()  # 升序处理

    print(f"\n回放 {len(dates)} 个交易日: {dates[0]} ~ {dates[-1]}\n")
    engine = EODReviewEngine()
    success = 0
    for d in dates:
        result = engine.run_review(d, use_cache=False)
        if result.ok:
            path = generate_report(result)
            sent = f"{result.sentiment.score}" if result.sentiment else "N/A"
            print(f"  ✅ {d}: 情绪{sent} | {path.name}")
            success += 1
        else:
            print(f"  ⏭️  {d}: 非交易日，跳过")

    print(f"\n完成: {success}/{len(dates)} 成功")
    return 0 if success > 0 else 1


def _cmd_list(args) -> int:
    """列出已复盘（已落库 eod_sentiment）的日期."""
    try:
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT trade_date, sentiment_score, sentiment_label "
                "FROM eod_sentiment ORDER BY trade_date DESC LIMIT ?",
                (args.limit,),
            ).fetchall()
    except Exception as e:
        logger.error(f"读取 eod_sentiment 失败: {e}")
        return 1

    if not rows:
        print("（无复盘记录）")
        return 0

    print(f"\n最近 {len(rows)} 个复盘记录:")
    print(f"{'日期':<12} {'情绪分':>6} {'标签':<8}")
    print("-" * 30)
    for r in rows:
        score = f"{r[1]}" if r[1] is not None else "N/A"
        print(f"{r[0]:<12} {score:>6} {r[2] or 'N/A':<8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
