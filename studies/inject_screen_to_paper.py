"""注入选股 top20 信号到前向实盘测试账户（paper_trading）.

读当日 ``studies/output/top20_screen_<date>.json``，桥接字段后注入
``executor.run_day(factor_scores=...)``，驱动 DavisDoubleStrategy 自动调仓。

⚠️ 绕过 paper_trading `run` 命令的残缺路径（run 不传 factor_scores → 跑不出 BUY）。
   本脚本直接把 screen 信号注入 executor，复用全部 14 个安全阀
   （max_positions/bear门控/止损/整手/cash管理/NAV）。

Usage:
    .venv/bin/python studies/inject_screen_to_paper.py [--date YYYY-MM-DD] [--dry-run] [--name live_factor_test]

Crontab (screen 跑完后):
    25 17 * * 1-5 cd /path && PYTHONPATH=/path \\
        .venv/bin/python studies/inject_screen_to_paper.py \\
        >> stockhot/invest_sop/logs/paper_inject.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "studies" / "output"
DEFAULT_ACCOUNT = "live_factor_test"


def load_top20(as_of: str) -> list[dict]:
    """读取指定日期的 top20 JSON."""
    json_path = OUTPUT_DIR / f"top20_screen_{as_of}.json"
    if not json_path.exists():
        raise FileNotFoundError(f"选股结果不存在: {json_path}（请先运行 screen_top20.py）")
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("top20", [])


def bridge_to_davis_scores(top20: list[dict]) -> dict[str, dict]:
    """桥接 screen_top20 字段 → DavisDoubleStrategy 期望的 davis_scores 格式.

    screen_top20: {ts_code, name, composite, ...}
    davis_double: {ts_code: {"final_score": float, "name": str}}
    """
    return {
        r["ts_code"]: {
            "final_score": r.get("composite", 0),
            "name": r.get("name", ""),
            "rank": i,
        }
        for i, r in enumerate(top20)
    }


def inject(as_of: str, account_name: str, dry_run: bool = False) -> dict:
    """注入选股信号并执行调仓.

    Args:
        as_of: 选股日期 YYYY-MM-DD（需对应 top20 JSON）
        account_name: paper_trading 账户名
        dry_run: 只打印桥接结果，不实际调仓

    Returns:
        executor.run_day 返回的 summary dict（dry_run 时返回桥接预览）
    """
    top20 = load_top20(as_of)
    davis_scores = bridge_to_davis_scores(top20)
    trade_date = as_of.replace("-", "")

    if dry_run:
        print(f"[DRY-RUN] 将注入 {len(davis_scores)} 只候选到账户 {account_name}:")
        for code, info in list(davis_scores.items())[:5]:
            print(f"  {code} {info['name']} final_score={info['final_score']}")
        if len(davis_scores) > 5:
            print(f"  ... 共 {len(davis_scores)} 只")
        return {"status": "dry_run", "candidates": len(davis_scores)}

    # 加载账户 + 创建策略 + 实例化 executor
    from davis_analyzer.paper_trading.account import PaperAccount
    from davis_analyzer.paper_trading.executor import DailyExecutor
    from davis_analyzer.paper_trading.strategy import create_strategy

    account = PaperAccount.load(account_name)
    strategy = create_strategy(account.strategy_name, account.config)
    executor = DailyExecutor(account, strategy)

    # 注入选股信号，复用 executor 全部安全阀
    result = executor.run_day(
        trade_date,
        factor_scores={"_davis_scores": davis_scores},
    )
    account.close()
    return result


def main(argv: list[str] | None = None) -> int:
    """CLI 入口."""
    parser = argparse.ArgumentParser(description="注入选股信号到前向测试账户")
    parser.add_argument("--date", default=None, help="选股日期 YYYY-MM-DD（默认：今天）")
    parser.add_argument("--name", default=DEFAULT_ACCOUNT, help=f"账户名（默认：{DEFAULT_ACCOUNT}）")
    parser.add_argument("--dry-run", action="store_true", help="只打印桥接结果，不实际调仓")
    args = parser.parse_args(argv)

    from datetime import date

    as_of = args.date or date.today().strftime("%Y-%m-%d")
    print(f"=== inject_screen_to_paper | AS_OF={as_of} | account={args.name} ===")

    try:
        result = inject(as_of, args.name, dry_run=args.dry_run)
        status = result.get("status", "unknown")
        print(f"结果: {status}")
        if status not in ("skipped", "dry_run", "no_prices"):
            # 成功执行，打印交易摘要
            buys = result.get("buys", 0)
            sells = result.get("sells", 0)
            nav = result.get("nav")
            print(f"  买入 {buys} 只, 卖出 {sells} 只" + (f", NAV {nav}" if nav else ""))
        return 0 if status != "no_prices" else 1
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1
    except Exception as e:
        import traceback

        print(f"[ERROR] 注入失败: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
