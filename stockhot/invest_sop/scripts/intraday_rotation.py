"""尾盘调仓 — 14:40 用「昨日因子 + 实时价格」执行模拟盘轮动，推送可跟随信号.

为什么存在（2026-08-19）：
原 inject_screen_to_paper 链路 19:00 盘后执行，成交价是当日收盘价——推送
到达时该价格已不可成交，跟随只能次日开盘接力（吃隔夜跳空），对实盘没有
借鉴意义。本模块把轮动决策挪到尾盘 14:40：

  - 因子：最近一晚的 top20_screen JSON（T-1 收盘因子。14:40 时 T 日因子
    必然不存在——这是盘中决策的本质约束，与真实盘面一致）
  - 价格：实时行情（复用 intraday_manager 的东财→新浪双源快照）
  - 模拟成交按实时价 + 板块整手规则（含科创板 200 股下限、小资金可买性
    递补），尾盘价≈收盘价，与收盘价回测口径偏差最小
  - 推送后用户有 ~20 分钟跟随窗口

触发方式（2026-08-20 起）：由 intraday_manager 主循环在 ≥14:40 的周期调用
trigger_rotation()（窗口守卫 + 最多 3 次重试），不再依赖独立 cron/timer。
也可手动运行：--dry-run 只打印计划；--force 跳过时间窗重放。

19:00 的 inject 保留作兜底：本模块对账户成功跑完当日（含无信号的空轮，
record_nav 已落账）后 has_run_on(T)=True，inject 自动跳过；当日触发始终
失败（重试超限）不落 NAV，inject 照常执行。

Usage:
    .venv/bin/python stockhot/invest_sop/scripts/intraday_rotation.py [--dry-run] [--force]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # scripts → invest_sop → stockhot → 仓库根
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "studies"))

from davis_analyzer.paper_trading.account import PaperAccount, min_buy_lots
from davis_analyzer.paper_trading.strategy import (
    DavisDoubleStrategy,
    MarketSnapshot,
)
from inject_screen_to_paper import _push_rebalance_report, bridge_to_davis_scores

# 与 inject_screen_to_paper.INJECT_ACCOUNTS 保持一致（主仓 + 小仓）
ROTATION_ACCOUNTS = ["live_factor_test", "mini_100k"]

# 尾盘触发时刻：intraday_manager 主循环在 ≥该时刻的周期调用 trigger_rotation，
# 收盘即止；窗口内失败自动重试（价格源抖动），--force 供人工重放/调试
ROTATION_TRIGGER = "14:40"
WINDOW_START, WINDOW_END = ROTATION_TRIGGER, "15:00"

# 价格源/单账户异常的最大重试次数（每 2 分钟一个周期），超限放弃交 19:00 兜底
MAX_ATTEMPTS = 3


def _limit_pct(ts_code: str) -> float:
    """按板块返回涨跌停幅度（%）。

    北交所 30%，科创板/创业板 20%，主板 10%。用于实时涨跌幅接近涨停时
    拒绝买入（现实填不成）、接近跌停时顺延卖出。
    """
    code = (ts_code or "").split(".")[0]
    if code.startswith(("83", "87", "88", "92")):  # 北交所（老 8 系 + 新 920 系）
        return 30.0
    if code.startswith(("688", "689", "300", "301")):
        return 20.0
    return 10.0


def _in_run_window(now: datetime | None = None) -> bool:
    """是否处于尾盘执行窗口（工作日 14:30-15:00）."""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    hhmm = now.strftime("%H:%M")
    return WINDOW_START <= hhmm <= WINDOW_END


def _load_latest_top20() -> tuple[str, list[dict]]:
    """加载最近 7 天内的 top20 日期文件，返回 (as_of, top20)."""
    base = PROJECT_ROOT / "studies" / "output"
    for back in range(1, 8):  # 从 T-1 开始——T 日盘中的决策用 T-1 因子
        d = (date.today() - timedelta(days=back)).strftime("%Y-%m-%d")
        p = base / f"top20_screen_{d}.json"
        if p.exists():
            data = json.loads(p.read_text())
            return d, data.get("top20", [])
    return "", []


def _fallback_close_prices(ts_codes: list[str]) -> dict[str, float]:
    """实时价不可用时的兜底：读 market_data.db 最近收盘价（仅 dry-run 用）."""
    from stockhot.core.config import DB_PATH

    conn = sqlite3.connect(str(DB_PATH.parent / "market_data.db"))
    try:
        prices: dict[str, float] = {}
        for ts in ts_codes:
            row = conn.execute(
                "SELECT close FROM daily_price WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1",
                (ts,),
            ).fetchone()
            if row and row[0]:
                prices[ts] = float(row[0])
        return prices
    finally:
        conn.close()


def _fetch_live_quotes(ts_codes: list[str]) -> tuple[dict[str, float], dict[str, float]]:
    """拉实时行情，返回 (prices_ts, pct_map)。失败时抛 RuntimeError.

    prices_ts: {ts_code: 实时价}；pct_map: {code6: 今日涨跌幅%}。
    """
    from stockhot.invest_sop.scripts.intraday_manager import _fetch_realtime_prices

    code6_map: dict[str, str] = {}
    for ts in ts_codes:
        code6_map[ts.split(".")[0]] = ts
    quotes = _fetch_realtime_prices(set(code6_map.keys()))
    if not quotes:
        raise RuntimeError("实时行情双源均失败（东财+新浪）")
    prices_ts: dict[str, float] = {}
    pct_map: dict[str, float] = {}
    for code6, q in quotes.items():
        ts = code6_map.get(code6)
        if ts:
            prices_ts[ts] = q["price"]
            pct_map[code6] = q.get("pct_chg", 0.0)
    return prices_ts, pct_map


def _trade_dict(trade) -> dict:
    """TradeRecord → 推送报告需要的 dict 形态（与 executor 结果一致）."""
    return {
        "ts_code": trade.ts_code,
        "name": trade.name,
        "price": trade.price,
        "shares": trade.shares,
        "signal_reason": trade.signal_reason,
    }


def run_rotation(dry_run: bool = False) -> bool:
    """对 ROTATION_ACCOUNTS 逐账户执行尾盘轮动。

    Returns:
        True = 当日轮动已完成（含"已执行过"跳过与无信号空轮——NAV 已落账，
              调用方不必重试）；False = 未完成（价格源失败/账户异常，可重试）。
    """
    today_dash = date.today().isoformat()
    today = today_dash.replace("-", "")

    as_of, top20 = _load_latest_top20()
    if not top20:
        print(f"[{today_dash}] [WARN] 近 7 天无 top20 筛选结果，跳过（19:00 inject 兜底）")
        return True
    if as_of == today_dash:
        print(f"[{today_dash}] [WARN] 因子基准为当日 {as_of}（仅重放场景会出现）")
    davis_scores = bridge_to_davis_scores(top20)
    print(f"[{today_dash}] 因子基准: top20_screen_{as_of}.json（{len(davis_scores)} 只）")

    # ── 收集全部需要定价的代码（持仓 + 候选）──
    accounts: dict[str, PaperAccount] = {}
    pending_codes: set[str] = set(davis_scores.keys())
    for name in ROTATION_ACCOUNTS:
        try:
            acc = PaperAccount.load(name)
        except ValueError as e:
            print(f"[{today_dash}] [WARN] 账户 {name} 不存在: {e}")
            continue
        accounts[name] = acc
        pending_codes.update(p.ts_code for p in acc.get_positions())

    ts_codes = sorted(pending_codes)

    # ── 实时价（dry-run 盘外回退昨收）──
    pct_map: dict[str, float] = {}
    try:
        prices_ts, pct_map = _fetch_live_quotes(ts_codes)
        src = "实时行情"
    except RuntimeError:
        if not dry_run:
            print(f"[{today_dash}] [WARN] 实时价不可用（本次重试失败）")
            for acc in accounts.values():
                acc.close()
            return False
        prices_ts = _fallback_close_prices(ts_codes)
        src = "昨收回退（盘外 dry-run）"
        pct_map = {}
    print(f"[{today_dash}] 价格源: {src}（{len(prices_ts)}/{len(ts_codes)} 只有价）")

    completed = True
    for name, acc in accounts.items():
        try:
            ok = _rotate_one(acc, davis_scores, prices_ts, pct_map, today, dry_run)
            if not ok:
                completed = False
        except Exception as e:
            import traceback

            print(f"[{today_dash}] [ERROR] {name} 轮动失败: {type(e).__name__}: {e}")
            traceback.print_exc()
            completed = False
        finally:
            acc.close()
    return completed


# 触发器重试状态（进程生命周期内有效；intraday_manager 每日重启自然复位）
_attempt_count = 0


def trigger_rotation(dry_run: bool = False) -> bool:
    """intraday_manager 主循环的触发入口：窗口守卫 + 有限重试。

    Returns:
        True = 触发任务结束（成功完成、或已达重试上限放弃——调用方停止
              触发，失败日由 19:00 inject 兜底）；False = 本次失败且还有
              重试额度（下个周期再来）。
    """
    global _attempt_count
    if not _in_run_window() and not dry_run:
        return False  # 未到窗口/已收盘：由调用方按时间守卫，此处不消耗额度
    _attempt_count += 1
    try:
        ok = run_rotation(dry_run=dry_run)
    except Exception as e:
        print(f"[{date.today().isoformat()}] [ERROR] trigger_rotation 异常: {type(e).__name__}: {e}")
        ok = False
    if ok:
        return True
    if _attempt_count >= MAX_ATTEMPTS:
        print(f"[{date.today().isoformat()}] [WARN] 尾盘轮动重试 {MAX_ATTEMPTS} 次未完成，放弃（19:00 inject 兜底）")
        return True
    return False


def _rotate_one(
    acc: PaperAccount,
    davis_scores: dict[str, dict],
    prices_ts: dict[str, float],
    pct_map: dict[str, float],
    today: str,
    dry_run: bool,
) -> bool:
    """单账户尾盘轮动：评估 → 卖出 → 买入 → 记 NAV → 推送。"""
    if acc.strategy_name != "davis_double":
        print(f"[{acc.name}] [WARN] 策略 {acc.strategy_name} 非轮动型，跳过")
        return True
    if not dry_run and acc.has_run_on(today):
        print(f"[{acc.name}] {today} 已执行过（14:40 或 19:00），跳过")
        return True

    positions = acc.get_positions()
    snapshot = MarketSnapshot(
        trade_date=today,
        prices=prices_ts,
        davis_scores=davis_scores,
        stock_names={c: info.get("name", c) for c, info in davis_scores.items()},
    )
    equity0 = acc.cash + sum(
        p.shares * prices_ts.get(p.ts_code, p.avg_cost) for p in positions
    )
    strategy = DavisDoubleStrategy(**acc.config)
    signals = strategy.evaluate(positions, snapshot, equity0)
    sells = [s for s in signals if s.action == "SELL"]
    buys = [s for s in signals if s.action == "BUY"]
    print(f"[{acc.name}] 计划: 卖 {len(sells)} / 买 {len(buys)}（权益 {equity0:,.0f}）")

    if dry_run:
        for s in sells:
            px = prices_ts.get(s.ts_code)
            px_str = f"@{px:.2f}" if px else "@无价(顺延)"
            print(f"  [DRY] SELL {s.name:6s} {s.ts_code} {px_str} {s.signal_reason}")
        for s in buys:
            px = prices_ts.get(s.ts_code)
            px_str = f"@{px:.2f}" if px else "@无价(跳过)"
            print(f"  [DRY] BUY  {s.name:6s} {s.ts_code} {px_str} {s.signal_reason}")
        return True

    # ── 卖出（先卖后买，回笼资金）；接近跌停顺延（现实卖不出）──
    executed_sells: list[dict] = []
    for s in sells:
        c6 = s.ts_code.split(".")[0]
        px = prices_ts.get(s.ts_code)
        if px is None:
            continue
        if pct_map.get(c6, 0.0) <= -(_limit_pct(s.ts_code) - 0.5):
            print(f"[{acc.name}] {s.name} 接近跌停（{pct_map.get(c6, 0):+.1f}%），卖出顺延")
            continue
        trade = acc.sell_all(
            ts_code=s.ts_code, name=s.name, price=px,
            trade_date=today, signal_reason=s.signal_reason,
        )
        if trade:
            executed_sells.append(_trade_dict(trade))

    # ── 买入（按卖出后权益定仓；接近涨停跳过——现实填不成）──
    equity1 = acc.market_value(prices_ts)
    executed_buys: list[dict] = []
    for s in buys:
        c6 = s.ts_code.split(".")[0]
        px = prices_ts.get(s.ts_code)
        if px is None or px <= 0:
            continue
        if pct_map.get(c6, 0.0) >= _limit_pct(s.ts_code) - 0.5:
            print(f"[{acc.name}] {s.name} 接近涨停（{pct_map.get(c6, 0):+.1f}%），放弃买入")
            continue
        shares = int(equity1 * s.target_weight / px)
        if shares < min_buy_lots(s.ts_code):
            print(f"[{acc.name}] {s.name} 槽位资金不足一手（@{px:.2f}），跳过")
            continue
        trade = acc.buy(
            ts_code=s.ts_code, name=s.name, shares=shares, price=px,
            trade_date=today, signal_reason=s.signal_reason,
        )
        if trade:
            executed_buys.append(_trade_dict(trade))

    # ── 记 NAV（标记当日已决策，19:00 inject 自动跳过）──
    nav = acc.record_nav(today, prices_ts)
    final_positions = acc.get_positions()
    summary = {
        "initial_capital": acc.initial_capital,
        "total_equity": nav.total_equity,
        "cash": nav.cash,
        "positions_value": nav.positions_value,
        "position_count": len(final_positions),
        "daily_return": nav.daily_return,
    }
    print(
        f"[{acc.name}] 成交: 卖 {len(executed_sells)} / 买 {len(executed_buys)}, "
        f"NAV {nav.total_equity:,.0f}（当日 {nav.daily_return if nav.daily_return is None else round(nav.daily_return, 2)}%）"
    )

    if executed_buys or executed_sells:
        _push_rebalance_report(
            acc.name, executed_buys, executed_sells, summary,
            f"{today[:4]}-{today[4:6]}-{today[6:]} 尾盘实时",
        )
    return True


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：时间窗校验 → 尾盘轮动."""
    parser = argparse.ArgumentParser(description="尾盘 14:40 实时价轮动（可跟随信号）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不成交不推送")
    parser.add_argument("--force", action="store_true", help="跳过时间窗校验（人工重放）")
    args = parser.parse_args(argv)

    if not args.force and not args.dry_run and not _in_run_window():
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] 不在尾盘窗口（{WINDOW_START}-{WINDOW_END}），退出")
        return 0

    print(f"=== intraday_rotation @ {datetime.now().isoformat()} | dry_run={args.dry_run} ===")
    return 0 if run_rotation(dry_run=args.dry_run) else 1


if __name__ == "__main__":
    sys.exit(main())
