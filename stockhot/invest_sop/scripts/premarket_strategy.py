#!/usr/bin/env python
"""盘前操作策略表 — 每日 08:15 推送持仓 + top20 的操作建议.

生成完整策略表，包括：
  - 当前持仓：成本/盈亏/止损价/T+减仓线/止盈价/操作建议
  - top20 新候选：因子评分/建议操作
  - 账户状态：总权益/仓位/可用资金

数据源（全部盘前可得，无实时依赖）：
  - top20_screen_latest.json → 因子评分
  - paper_positions / invest_holdings → 持仓
  - invest_watchlist → stop_loss_pct / target_entry_high
  - 前日收盘价（DB daily_prices）→ 昨收

Usage:
    .venv/bin/python stockhot/invest_sop/scripts/premarket_strategy.py [--dry-run]

Crontab:
    15 8 * * 1-5 ... premarket_strategy.py >> premarket_strategy.log 2>&1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PAPER_ACCOUNT = "live_factor_test"

# 操作阈值（与 executor / intraday_manager 一致）
TAKE_PROFIT_PCT = 0.20       # 止盈线 +20%
T_TRIM_THRESHOLD = 0.08      # T+减仓触发 +8%
T_TRIM_RATIO = 1 / 3         # T+减仓比例 1/3
PULLBACK_ADD_THRESHOLD = -0.05  # 回调加仓 -5%
WARN_PROXIMITY = 0.01        # 接近预警（1% 以内）


def _load_top20() -> list[dict]:
    """加载最新 top20 选股结果."""
    path = PROJECT_ROOT / "studies" / "output" / "top20_screen_latest.json"
    if not path.exists():
        # 尝试日期文件
        for back in range(0, 7):
            d = (date.today() - timedelta(days=back)).strftime("%Y-%m-%d")
            p = PROJECT_ROOT / "studies" / "output" / f"top20_screen_{d}.json"
            if p.exists():
                path = p
                break
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return data.get("top20", [])


def _load_paper_positions() -> list[dict]:
    """加载模拟账户持仓."""
    import sqlite3
    from stockhot.core.config import DB_PATH

    holdings = []
    try:
        from davis_analyzer.paper_trading.account import PaperAccount

        acc = PaperAccount.load(PAPER_ACCOUNT)
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # 读 watchlist 的 stop_loss_pct
        wl_map: dict[str, dict] = {}
        for r in conn.execute(
            "SELECT code, stop_loss_pct, target_entry_high, composite_score "
            "FROM invest_watchlist WHERE source='screen_top20'"
        ):
            wl_map[r["code"]] = dict(r)

        initial_capital = 0
        cash = 0
        for r in conn.execute(
            "SELECT initial_capital, cash FROM paper_accounts WHERE id=?", (acc.account_id,)
        ):
            initial_capital = r["initial_capital"]
            cash = r["cash"]

        for pos in acc.get_positions():
            code6 = pos.ts_code.split(".")[0]
            wl = wl_map.get(code6, {})
            stop_pct = wl.get("stop_loss_pct", -0.12)
            holdings.append({
                "code": code6,
                "ts_code": pos.ts_code,
                "name": pos.name,
                "shares": pos.shares,
                "avg_cost": pos.avg_cost,
                "source": "paper",
                "stop_loss_pct": stop_pct,
                "target_high": wl.get("target_entry_high"),
                "composite": wl.get("composite_score"),
                "signal_reason": pos.signal_reason or "",
            })

        acc.close()
        holdings_initial = initial_capital
        holdings_cash = cash
    except Exception as e:
        print(f"[WARN] 加载模拟持仓失败: {e}")
        holdings_initial = 0
        holdings_cash = 0

    # 也加载手动持仓
    try:
        from stockhot.storage.database import get_connection

        conn2 = get_connection()
        for row in conn2.execute(
            "SELECT code, name, stop_loss_hard, target_price, avg_cost, quantity "
            "FROM invest_holdings WHERE status='active'"
        ):
            d = dict(row)
            code6 = d.get("code", "")
            qty = d.get("quantity") or 0
            cost = d.get("avg_cost") or 0
            if qty > 0 and cost > 0:
                holdings.append({
                    "code": code6,
                    "ts_code": _code_to_ts_code(code6),
                    "name": d.get("name", ""),
                    "shares": qty,
                    "avg_cost": cost,
                    "source": "manual",
                    "stop_loss_pct": -0.12,
                    "target_high": d.get("target_price"),
                    "composite": None,
                    "signal_reason": "",
                })
    except Exception:
        pass

    return holdings, holdings_initial, holdings_cash


def _code_to_ts_code(code: str) -> str:
    """6 位代码 → ts_code."""
    if code.startswith(("60", "68", "11", "13")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _get_prev_close(code6: str) -> float | None:
    """获取前一交易日收盘价."""
    try:
        import sqlite3
        from stockhot.data_layer import get_repository

        repo = get_repository()
        ts_code = _code_to_ts_code(code6)
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=10)).strftime("%Y%m%d")
        df = repo.get_daily_prices(ts_code, start, end)
        if df is not None and not df.empty:
            return float(df.sort_values("trade_date")["close"].iloc[-1])
    except Exception:
        pass
    return None


def _compute_strategy(h: dict, prev_close: float | None) -> dict:
    """计算单只持仓的操作策略."""
    cost = h["avg_cost"]
    stop_pct = h.get("stop_loss_pct", -0.12)
    stop_price = round(cost * (1 + stop_pct), 2)
    take_profit = round(cost * (1 + TAKE_PROFIT_PCT), 2)
    t_trim_price = round(cost * (1 + T_TRIM_THRESHOLD), 2)
    add_price = round(cost * (1 + PULLBACK_ADD_THRESHOLD), 2)

    pnl_pct = 0.0
    action = "持有"
    action_emoji = "✅"
    details = []

    if prev_close and prev_close > 0:
        pnl_pct = (prev_close / cost - 1) * 100

        # 止损判断
        if prev_close <= stop_price:
            action = "已破止损"
            action_emoji = "🔴"
        elif prev_close <= stop_price * (1 + WARN_PROXIMITY):
            action = "⚠️接近止损"
            action_emoji = "🟡"
            details.append(f"距止损{stop_price}仅{(prev_close/stop_price-1)*100:.1f}%")

        # T+减仓判断
        if pnl_pct >= T_TRIM_THRESHOLD * 100:
            trim_shares = int(h["shares"] * T_TRIM_RATIO // 100) * 100
            if trim_shares >= 100:
                if action == "持有":
                    action = f"T+减仓{trim_shares}股"
                    action_emoji = "🟠"
                details.append(f"T+减仓线{t_trim_price}已触发")

        # 止盈判断
        if pnl_pct >= TAKE_PROFIT_PCT * 100:
            action = f"止盈减仓"
            action_emoji = "🟠"
            details.append(f"止盈线{take_profit}已触发")

    return {
        "prev_close": prev_close,
        "pnl_pct": pnl_pct,
        "stop_price": stop_price,
        "take_profit": take_profit,
        "t_trim_price": t_trim_price,
        "add_price": add_price,
        "action": action,
        "action_emoji": action_emoji,
        "details": details,
    }


def generate_strategy_report() -> str:
    """生成盘前策略报告文本."""
    top20 = _load_top20()
    holdings, initial_capital, cash = _load_paper_positions()

    lines = [f"📋 盘前操作策略 [{date.today().isoformat()}]", ""]

    # ── 持仓策略 ──
    held_codes = set()
    if holdings:
        lines.append(f"💼 持仓（{len(holdings)}只）：")
        for h in holdings:
            prev_close = _get_prev_close(h["code"])
            strat = _compute_strategy(h, prev_close)
            held_codes.add(h["code"])

            tag = "📊" if h["source"] == "paper" else "💼"
            pnl_str = f"{strat['pnl_pct']:+.1f}%" if prev_close else "N/A"
            close_str = f"{prev_close:.2f}" if prev_close else "N/A"

            lines.append(
                f"  {tag} {h['name']:6s} {h['code']}  "
                f"昨收{close_str}  成本{h['avg_cost']:.2f}  {pnl_str}"
            )
            detail_parts = [
                f"{strat['action_emoji']}{strat['action']}",
                f"止损{strat['stop_price']}",
            ]
            if strat["details"]:
                detail_parts.extend(strat["details"])
            detail_parts.append(f"止盈{strat['take_profit']}")
            lines.append(f"    {' | '.join(detail_parts)}")
        lines.append("")

    # ── top20 新候选（不在持仓的）──
    new_candidates = [t for t in top20 if t["ts_code"].split(".")[0] not in held_codes][:5]
    if new_candidates:
        lines.append("📊 top20 新候选（不在持仓）：")
        for t in new_candidates:
            code6 = t["ts_code"].split(".")[0]
            prev_close = _get_prev_close(code6)
            close_str = f"昨收{prev_close:.2f}" if prev_close else ""
            lines.append(
                f"  {t['name']:6s} {code6}  因子{t.get('composite', 0):.1f}  {close_str}"
            )
        lines.append("")

    # ── 账户状态 ──
    if initial_capital > 0:
        # 估算总权益 = 现金 + 持仓市值（用昨收估算）
        pos_value = 0
        for h in holdings:
            prev_close = _get_prev_close(h["code"])
            if prev_close:
                pos_value += prev_close * h["shares"]
        total_equity = cash + pos_value
        position_pct = pos_value / total_equity * 100 if total_equity > 0 else 0
        total_pnl = (total_equity / initial_capital - 1) * 100 if initial_capital > 0 else 0

        lines.append("💰 账户状态：")
        lines.append(
            f"  总权益 {total_equity/1e4:.1f}万 | 仓位 {position_pct:.0f}%（{len(holdings)}只）"
            f" | 可用 {cash/1e4:.1f}万"
        )
        lines.append(f"  总盈亏 {total_pnl:+.1f}%（初始 {initial_capital/1e4:.0f}万）")
        lines.append("")

    # ── 近期事件提醒 ──
    try:
        from stockhot.invest_sop.event_calendar import get_upcoming_events, format_events_for_report

        events = get_upcoming_events(5)  # 未来 5 天
        if events:
            lines.append("")
            lines.append(format_events_for_report(events))
    except Exception as e:
        print(f"[WARN] 事件日历加载失败: {e}")

    lines.append("")
    lines.append("⚠️ 模拟账户策略参考，非实盘指令。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="盘前操作策略表推送")
    parser.add_argument("--dry-run", action="store_true", help="只打印不推送")
    args = parser.parse_args(argv)

    print(f"[{date.today().isoformat()}] === 生成盘前策略表 ===")

    report = generate_strategy_report()
    print(report)

    if args.dry_run:
        print("\n[DRY-RUN] 不推送飞书")
        return 0

    # 推送飞书
    try:
        from stockhot.notification.feishu_bot import get_feishu_notifier

        notifier = get_feishu_notifier()
        if notifier is None:
            print("[WARN] 飞书未配置")
            return 0
        asyncio.run(notifier.send_text(report))
        print("[OK] 盘前策略表推送成功")
    except Exception as e:
        print(f"[ERROR] 推送失败: {type(e).__name__}: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
