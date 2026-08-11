"""注入 screen_top20 选股信号到 FactorThreshold 模拟盘账户.

读当日 ``studies/output/top20_screen_<date>.json``，桥接成 davis_scores，
然后用 FactorThresholdStrategy 执行调仓（复用全部风控安全阀）。

2026-08-06 重构：从 DavisDoubleStrategy 改为 FactorThresholdStrategy。
- run_day 现在自动计算 factor_scores（不再需要外部注入 factor_data）
- davis_scores 从 screen_top20 注入（作为 DavisDouble 维度的补充）
- 策略切换：账户需要用 strategy_name=factor_threshold

Usage:
    .venv/bin/python studies/inject_screen_to_paper.py [--date YYYY-MM-DD] [--dry-run] [--name production_forward]

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
DEFAULT_ACCOUNT = "production_forward"


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

    # 注入 davis_scores（FactorThreshold 自动计算 factor_scores），
    # davis_scores 作为 DavisDouble 维度的补充评分传入
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

            # 有交易时推飞书调仓报告
            buy_trades = result.get("buy_trades", [])
            sell_trades = result.get("sell_trades", [])
            account_summary = result.get("account_summary")
            if buy_trades or sell_trades:
                _push_rebalance_report(
                    args.name, buy_trades, sell_trades, account_summary, as_of,
                )
        return 0 if status != "no_prices" else 1
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1
    except Exception as e:
        import traceback

        print(f"[ERROR] 注入失败: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1


def _push_rebalance_report(
    account_name: str,
    buy_trades: list[dict],
    sell_trades: list[dict],
    account_summary: dict | None,
    as_of: str,
) -> None:
    """推飞书调仓报告——买入+卖出+整体仓位+盈亏（供实盘参考）.

    格式：
    📊 因子选股调仓 [2026-08-06]
    账户：live_factor_test

    🟢 买入：
      兆易创新 603986  @385.00  因子68.9 top5
      光迅科技 002281  @175.49  因子67.7 top5
    🔴 卖出：
      睿创微纳 688002  @155.51  跌出top5
      澜起科技 688008  @155.51  T+减仓33% P&L=+9.9%

    💰 账户状态：
      总权益 89.2万 | 仓位 86%（5只） | 当日 +2.3%
      总盈亏 -10.8%（初始 100万）

    ⚠️ 模拟账户信号，非实盘指令。仅供参考。
    """
    try:
        import asyncio
        from stockhot.notification.feishu_bot import get_feishu_notifier

        lines = [f"📊 因子选股调仓 [{as_of}]"]
        lines.append(f"账户：{account_name}")
        lines.append("")

        # 买入
        if buy_trades:
            lines.append("🟢 买入：")
            for t in buy_trades[:8]:
                code6 = t["ts_code"].split(".")[0]
                reason = _format_signal_short(t.get("signal_reason", ""))
                lines.append(f"  {t['name']:6s} {code6}  @{t['price']:.2f}  {reason}")
            if len(buy_trades) > 8:
                lines.append(f"  ... 共 {len(buy_trades)} 只")
            lines.append("")

        # 卖出
        if sell_trades:
            lines.append("🔴 卖出：")
            for t in sell_trades[:8]:
                code6 = t["ts_code"].split(".")[0]
                reason = _format_signal_short(t.get("signal_reason", ""))
                lines.append(f"  {t['name']:6s} {code6}  @{t['price']:.2f}  {reason}")
            if len(sell_trades) > 8:
                lines.append(f"  ... 共 {len(sell_trades)} 只")
            lines.append("")

        # 账户状态
        if account_summary:
            equity = account_summary.get("total_equity", 0)
            initial = account_summary.get("initial_capital", 0)
            cash = account_summary.get("cash", 0)
            pos_val = account_summary.get("positions_value", 0)
            pos_count = account_summary.get("position_count", 0)
            daily_ret = account_summary.get("daily_return", 0)

            total_pnl_pct = (equity / initial - 1) * 100 if initial > 0 else 0
            position_pct = (pos_val / equity * 100) if equity > 0 else 0

            lines.append("💰 账户状态：")
            lines.append(
                f"  总权益 {equity/1e4:.1f}万 | 仓位 {position_pct:.0f}%（{pos_count}只）"
                f" | 当日 {'+' if daily_ret >= 0 else ''}{daily_ret:.1f}%"
            )
            lines.append(
                f"  总盈亏 {'+' if total_pnl_pct >= 0 else ''}{total_pnl_pct:.1f}%"
                f"（初始 {initial/1e4:.0f}万）"
            )
            lines.append("")

        lines.append("⚠️ 模拟账户信号，非实盘指令。仅供参考，跟单风险自负。")
        lines.append("买入价已登记到 watchlist，盘前报告可引用。")

        msg = "\n".join(lines)
        print(msg)

        notifier = get_feishu_notifier()
        if notifier is None:
            print("[WARN] 飞书未配置，跳过推送")
            return
        asyncio.run(notifier.send_text(msg))
        print("[OK] 调仓报告推送成功")
    except Exception as e:
        print(f"[WARN] 调仓报告推送失败: {type(e).__name__}: {e}")


def _format_signal_short(reason: str) -> str:
    """把 signal_reason 格式化为简短展示."""
    import re
    if not reason:
        return ""
    # 提取因子评分
    m = re.search(r"final_score=([\d.]+)", reason)
    if m:
        return f"因子{m.group(1)}"
    # T+减仓/止盈/止损等直接展示（截断到 30 字符）
    return reason[:30]


if __name__ == "__main__":
    sys.exit(main())
