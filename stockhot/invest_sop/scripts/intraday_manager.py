#!/usr/bin/env python
"""盘中高频仓位管理 — 常驻进程，每 2 分钟轮询实时价 + 自动执行止损/止盈/加仓.

在交易日 09:25~15:05 期间持续运行：
  1. 每 poll_interval 秒拉全市场实时价（stock_zh_a_spot_em，~2-3s）
  2. 检查持仓的止损/止盈/T+减仓/回调加仓信号
  3. 触发 → 执行模拟交易 + 即时推飞书
  4. 接近信号线（1%以内）→ 推预警
  5. 无信号 → 静默

执行规则（与 executor 一致）：
  - 止损（现价 ≤ stop_price）→ sell_all 全部清仓
  - T+减仓（现价 ≥ cost×1.08）→ sell 1/3
  - 止盈（现价 ≥ cost×1.20）→ sell 1/3（与 T+减仓互斥，取止盈优先）
  - 回调加仓（现价 ≤ cost×0.95 且 composite>60）→ buy 1/4

安全机制：
  - 数据源失败 → 跳过本轮（不执行不推送）
  - 回测账户（backtest/abtest/shadow）→ 只监控不执行
  - 手动持仓（invest_holdings）→ 只推送不执行
  - 同一信号同一日不重复执行（去重）

Usage:
    .venv/bin/python stockhot/invest_sop/scripts/intraday_manager.py [--dry-run] [--interval 120]

Crontab（常驻进程，09:25 启动，15:05 自动退出）:
    25 9 * * 1-5 ... intraday_manager.py >> intraday_manager.log 2>&1
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

PROJECT_ROOT = PROJECT_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent.parent.parent
PAPER_ACCOUNT = "live_factor_test"

# 轮询间隔（秒）
DEFAULT_INTERVAL = 120

# 交易时段（24h 制）
TRADING_START = "09:25"
TRADING_END = "15:05"
LUNCH_START = "11:30"
LUNCH_END = "13:00"

# 信号阈值（与 executor / premarket_strategy 一致）
TAKE_PROFIT_PCT = 0.20
T_TRIM_THRESHOLD = 0.08
T_TRIM_RATIO = 1 / 3
PULLBACK_ADD_THRESHOLD = -0.05
WARN_PROXIMITY = 0.01  # 接近预警 1%

# 板手（A 股最小交易单位）
BOARD_LOT = 100


def _in_trading_hours() -> bool:
    """判断当前是否在交易时段（含集合竞价)."""
    now = datetime.now()
    # 周末跳过
    if now.weekday() >= 5:
        return False
    t = now.strftime("%H:%M")
    return TRADING_START <= t <= TRADING_END


def _is_lunch_break() -> bool:
    """判断是否在午休时段（11:30~13:00）."""
    t = datetime.now().strftime("%H:%M")
    return LUNCH_START <= t < LUNCH_END


def _fetch_realtime_prices(codes: set[str]) -> dict[str, dict] | None:
    """拉全市场实时价，过滤出需要的 codes.

    返回 {code6: {"price": float, "pct_chg": float}} 或 None（失败时）。
    """
    import akshare as ak
    import pandas as pd
    from stockhot.core.rate_limiter import safe_akshare_call

    try:
        df = safe_akshare_call(ak.stock_zh_a_spot_em)
        if df is None or df.empty:
            return None
    except Exception as e:
        print(f"[WARN] 实时价拉取失败: {e}")
        return None

    result = {}
    for _, row in df.iterrows():
        code = str(row.get("代码", ""))
        if code in codes:
            price = pd.to_numeric(row.get("最新价"), errors="coerce")
            pct = pd.to_numeric(row.get("涨跌幅"), errors="coerce")
            if not pd.isna(price) and price > 0:
                result[code] = {
                    "price": float(price),
                    "pct_chg": float(pct) if not pd.isna(pct) else 0.0,
                }
    return result if result else None


def _collect_holdings() -> tuple[list[dict], object | None, float, float]:
    """收集持仓（模拟 + 手动），返回 (holdings, account, initial_capital, cash).

    account 为 None 时表示模拟账户不可用（只监控手动持仓）。
    """
    import sqlite3
    from stockhot.core.config import DB_PATH

    holdings: list[dict] = []
    account = None
    initial_capital = 0
    cash = 0

    # 模拟账户持仓
    try:
        from davis_analyzer.paper_trading.account import PaperAccount

        account = PaperAccount.load(PAPER_ACCOUNT)
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        wl_map = {}
        for r in conn.execute(
            "SELECT code, stop_loss_pct, composite_score FROM invest_watchlist WHERE source='screen_top20'"
        ):
            wl_map[r["code"]] = dict(r)

        for r in conn.execute(
            "SELECT initial_capital, cash FROM paper_accounts WHERE id=?", (account.account_id,)
        ):
            initial_capital = r["initial_capital"]
            cash = r["cash"]

        for pos in account.get_positions():
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
                "stop_pct": stop_pct,
                "composite": wl.get("composite_score"),
                "can_execute": True,  # 模拟账户可执行
            })
        account.close()
    except Exception as e:
        print(f"[WARN] 模拟账户加载失败: {e}")

    # 手动持仓（只提醒不执行）
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
                stop_hard = d.get("stop_loss_hard") or cost * 0.88
                holdings.append({
                    "code": code6,
                    "ts_code": _code_to_ts_code(code6),
                    "name": d.get("name", ""),
                    "shares": qty,
                    "avg_cost": cost,
                    "source": "manual",
                    "stop_pct": (stop_hard / cost - 1),
                    "composite": None,
                    "can_execute": False,  # 手动持仓不自动执行
                })
    except Exception:
        pass

    return holdings, account, initial_capital, cash


def _code_to_ts_code(code: str) -> str:
    if code.startswith(("60", "68", "11", "13")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _check_signals(h: dict, current_price: float) -> list[dict]:
    """检查单只持仓的信号，返回信号列表.

    信号类型：stop_loss / take_profit / t_trim / pullback_add / warn_stop / warn_target
    """
    signals = []
    cost = h["avg_cost"]
    pnl_pct = (current_price / cost - 1) if cost > 0 else 0

    stop_price = cost * (1 + h.get("stop_pct", -0.12))
    take_profit_price = cost * (1 + TAKE_PROFIT_PCT)
    t_trim_price = cost * (1 + T_TRIM_THRESHOLD)
    add_price = cost * (1 + PULLBACK_ADD_THRESHOLD)

    # 止损
    if current_price <= stop_price:
        signals.append({
            "type": "stop_loss",
            "price": current_price,
            "shares": h["shares"],
            "pnl_pct": pnl_pct * 100,
            "stop_price": stop_price,
        })
    # 接近止损预警
    elif current_price <= stop_price * (1 + WARN_PROXIMITY):
        signals.append({
            "type": "warn_stop",
            "price": current_price,
            "stop_price": stop_price,
            "proximity_pct": (current_price / stop_price - 1) * 100,
        })

    # 止盈（优先于 T+减仓）
    if pnl_pct >= TAKE_PROFIT_PCT:
        trim_shares = int(h["shares"] * T_TRIM_RATIO // BOARD_LOT) * BOARD_LOT
        signals.append({
            "type": "take_profit",
            "price": current_price,
            "shares": max(trim_shares, BOARD_LOT),
            "pnl_pct": pnl_pct * 100,
            "target_price": take_profit_price,
        })
    # T+减仓
    elif pnl_pct >= T_TRIM_THRESHOLD:
        trim_shares = int(h["shares"] * T_TRIM_RATIO // BOARD_LOT) * BOARD_LOT
        if trim_shares >= BOARD_LOT:
            signals.append({
                "type": "t_trim",
                "price": current_price,
                "shares": trim_shares,
                "pnl_pct": pnl_pct * 100,
                "trim_price": t_trim_price,
            })

    # 回调加仓
    if pnl_pct <= PULLBACK_ADD_THRESHOLD and h.get("composite", 0) and h["composite"] > 60:
        add_shares = int(h["shares"] * 0.25 // BOARD_LOT) * BOARD_LOT
        if add_shares >= BOARD_LOT:
            signals.append({
                "type": "pullback_add",
                "price": current_price,
                "shares": add_shares,
                "pnl_pct": pnl_pct * 100,
                "add_price": add_price,
            })

    return signals


def _execute_signal(
    h: dict, signal: dict, account, trade_date: str,
) -> dict | None:
    """执行单个信号（模拟账户），返回交易结果或 None."""
    if not h.get("can_execute") or account is None:
        return None

    sig_type = signal["type"]
    price = signal["price"]
    shares = signal.get("shares", 0)

    try:
        if sig_type == "stop_loss":
            trade = account.sell_all(h["ts_code"], price, trade_date, f"盘中止损@{price:.2f}")
        elif sig_type in ("take_profit", "t_trim"):
            reason = "盘中止盈" if sig_type == "take_profit" else f"盘中T+减仓{shares}股"
            trade = account.sell(h["ts_code"], h["name"], shares, price, trade_date, signal_reason=reason)
        elif sig_type == "pullback_add":
            trade = account.buy(h["ts_code"], h["name"], shares, price, trade_date, signal_reason="盘中回调加仓")
        else:
            return None

        if trade:
            return {
                "type": sig_type,
                "name": h["name"],
                "code": h["code"],
                "price": price,
                "shares": shares if sig_type != "stop_loss" else h["shares"],
                "pnl_pct": signal.get("pnl_pct", 0),
                "remaining": h["shares"] - shares if sig_type != "stop_loss" else 0,
            }
    except Exception as e:
        print(f"[ERROR] 执行失败 {h['name']}: {e}")
    return None


async def _push_message(msg: str) -> bool:
    """推送飞书消息."""
    try:
        from stockhot.notification.feishu_bot import get_feishu_notifier

        notifier = get_feishu_notifier()
        if notifier is None:
            return False
        await notifier.send_text(msg)
        return True
    except Exception as e:
        print(f"[ERROR] 推送失败: {e}")
        return False


def _format_execution_report(executed: list[dict], warnings: list[dict], account_info: dict) -> str:
    """格式化盘中执行报告."""
    now = datetime.now().strftime("%H:%M")
    lines = [f"⚡ 盘中调仓执行 [{now}]", ""]

    for ex in executed:
        tag = "📊" if ex.get("source") == "paper" else "💼"
        if ex["type"] == "stop_loss":
            lines.append(f"🔴 止损卖出：{ex['name']} {ex['code']} @{ex['price']:.2f}（清仓 {ex['pnl_pct']:+.1f}%）")
        elif ex["type"] == "take_profit":
            lines.append(f"🟢 止盈减仓：{ex['name']} {ex['code']} @{ex['price']:.2f}（卖{ex['shares']}股 剩{ex['remaining']}股 +{ex['pnl_pct']:.1f}%）")
        elif ex["type"] == "t_trim":
            lines.append(f"🟠 T+减仓：{ex['name']} {ex['code']} @{ex['price']:.2f}（卖{ex['shares']}股 剩{ex['remaining']}股 +{ex['pnl_pct']:.1f}%）")
        elif ex["type"] == "pullback_add":
            lines.append(f"🔵 回调加仓：{ex['name']} {ex['code']} @{ex['price']:.2f}（买{ex['shares']}股 {ex['pnl_pct']:+.1f}%）")

    for w in warnings:
        if w["type"] == "warn_stop":
            lines.append(f"⚠️ 接近止损：{w['name']} {w['code']} 现价{w['price']:.2f} 距止损{w['stop_price']:.2f}仅{w['proximity_pct']:.1f}%")

    if account_info:
        lines.append("")
        equity = account_info.get("equity", 0)
        cash = account_info.get("cash", 0)
        pos_count = account_info.get("pos_count", 0)
        pos_pct = account_info.get("pos_pct", 0)
        lines.append(f"💰 仓位 {pos_pct:.0f}%（{pos_count}只）| 权益 {equity/1e4:.1f}万 | 可用 {cash/1e4:.1f}万")

    return "\n".join(lines)


def run_one_cycle(dry_run: bool = False) -> dict:
    """执行一轮监控（单次），返回执行统计."""
    trade_date = date.today().strftime("%Y%m%d")
    holdings, account, initial_capital, cash = _collect_holdings()

    if not holdings:
        return {"checked": 0, "executed": 0, "warnings": 0}

    codes = {h["code"] for h in holdings}
    prices = _fetch_realtime_prices(codes)
    if prices is None:
        print(f"[{datetime.now().strftime('%H:%M')}] 实时价不可用，跳过本轮")
        return {"checked": 0, "executed": 0, "warnings": 0, "price_failed": True}

    executed: list[dict] = []
    warnings: list[dict] = []
    executed_codes_today: set[str] = set()  # 去重

    for h in holdings:
        if h["code"] not in prices:
            continue
        current = prices[h["code"]]["price"]
        signals = _check_signals(h, current)

        for sig in signals:
            if sig["type"] in ("warn_stop", "warn_target"):
                # 预警信号
                w = {"type": sig["type"], "name": h["name"], "code": h["code"],
                     "price": current, **sig}
                warnings.append(w)
            elif h["code"] not in executed_codes_today:
                # 执行信号
                if dry_run:
                    executed.append({
                        "type": sig["type"], "name": h["name"], "code": h["code"],
                        "price": current, "shares": sig.get("shares", h["shares"]),
                        "pnl_pct": sig.get("pnl_pct", 0),
                        "remaining": h["shares"] - sig.get("shares", 0),
                        "source": h["source"],
                    })
                    executed_codes_today.add(h["code"])
                elif h.get("can_execute"):
                    result = _execute_signal(h, sig, account, trade_date)
                    if result:
                        result["source"] = h["source"]
                        executed.append(result)
                        executed_codes_today.add(h["code"])
                        print(f"  执行: {result['type']} {result['name']} @{result['price']:.2f}")

    # 推送（有执行或预警时）
    if executed or warnings:
        # 计算账户状态
        account_info = {}
        if initial_capital > 0:
            pos_value = sum(
                prices.get(h["code"], {}).get("price", 0) * h["shares"]
                for h in holdings if h["code"] in prices
            )
            equity = cash + pos_value
            account_info = {
                "equity": equity, "cash": cash,
                "pos_count": len([h for h in holdings if h["code"] in prices]),
                "pos_pct": pos_value / equity * 100 if equity > 0 else 0,
            }

        msg = _format_execution_report(executed, warnings, account_info)
        print(msg)
        if not dry_run:
            asyncio.run(_push_message(msg))

    return {
        "checked": len(holdings),
        "executed": len(executed),
        "warnings": len(warnings),
    }


def run_intraday_loop(interval: int = DEFAULT_INTERVAL, dry_run: bool = False) -> None:
    """盘中高频监控主循环."""
    print(f"[{date.today().isoformat()}] === 盘中高频监控启动 ===")
    print(f"  轮询间隔: {interval}s | 交易时段: {TRADING_START}~{TRADING_END}")
    if dry_run:
        print("  [DRY-RUN] 只检测不执行不推送")

    daily_stats = {"cycles": 0, "executed": 0, "warnings": 0}

    while _in_trading_hours():
        if _is_lunch_break():
            time.sleep(30)
            continue

        daily_stats["cycles"] += 1
        now = datetime.now().strftime("%H:%M:%S")

        result = run_one_cycle(dry_run=dry_run)
        daily_stats["executed"] += result.get("executed", 0)
        daily_stats["warnings"] += result.get("warnings", 0)

        if result.get("price_failed"):
            print(f"  [{now}] 轮询 #{daily_stats['cycles']} 价格不可用，跳过")
        elif result["executed"] == 0 and result["warnings"] == 0:
            print(f"  [{now}] 轮询 #{daily_stats['cycles']} 正常（{result['checked']}只持仓）")

        time.sleep(interval)

    # 收盘摘要
    print(f"\n[{date.today().isoformat()}] === 盘中监控结束 ===")
    print(f"  总轮询: {daily_stats['cycles']} | 执行: {daily_stats['executed']} | 预警: {daily_stats['warnings']}")

    if daily_stats["executed"] > 0 or daily_stats["warnings"] > 0:
        summary = (
            f"📊 盘中监控结束 [{date.today().isoformat()}]\n"
            f"当日交易：{daily_stats['executed']}笔 | 预警：{daily_stats['warnings']}条\n"
            f"总轮询：{daily_stats['cycles']}次"
        )
        print(summary)
        if not dry_run:
            asyncio.run(_push_message(summary))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="盘中高频仓位管理")
    parser.add_argument("--dry-run", action="store_true", help="只检测不执行不推送")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="轮询间隔（秒）")
    parser.add_argument("--once", action="store_true", help="只跑一轮（测试用）")
    args = parser.parse_args(argv)

    # 交易日校验
    try:
        from stockhot.invest_sop.utils.trading_calendar import is_trading_day

        today = date.today().isoformat()
        if not is_trading_day(today):
            print(f"[{today}] 非交易日，跳过")
            return 0
    except Exception:
        pass

    if args.once:
        # 单次模式（测试用）
        result = run_one_cycle(dry_run=args.dry_run)
        print(f"\n单次执行结果: {result}")
        return 0

    # 常驻模式
    run_intraday_loop(interval=args.interval, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
