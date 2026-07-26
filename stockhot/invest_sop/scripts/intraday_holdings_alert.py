"""盘中持仓异动监控 — 4 次定时用实时价检查持仓，触发推飞书.

仿 run_panic_alert 模式，盘中（10:30/11:30/13:30/14:30）用 AKShare 实时价
检查模拟账户持仓 + 手动持仓的三类信号：
  1. 止损触发：现价 ≤ stop_loss_hard
  2. 目标触发：现价 ≥ target_price
  3. 涨跌幅异动：当日涨跌幅 ≥ ±7%（接近涨跌停）

只提醒不自动下单。无触发不推送。

Usage:
    .venv/bin/python stockhot/invest_sop/scripts/intraday_holdings_alert.py [--dry-run]

Crontab (盘中 4 次):
    30 10,11,13,14 * * 1-5 cd /path && PYTHONPATH=/path \\
        .venv/bin/python stockhot/invest_sop/scripts/intraday_holdings_alert.py \\
        >> stockhot/invest_sop/logs/intraday_holdings.log 2>&1
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date

# 异动阈值
PRICE_CHANGE_THRESHOLD = 7.0  # 当日涨跌幅 ≥ ±7% 触发

# 监控的模拟账户名
PAPER_ACCOUNT = "live_factor_test"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="盘中持仓异动监控 + 飞书推送")
    parser.add_argument("--dry-run", action="store_true", help="只检测不推送")
    args = parser.parse_args(argv)

    # 交易日校验
    try:
        from stockhot.invest_sop.utils.trading_calendar import is_trading_day

        today = date.today().isoformat()
        if not is_trading_day(today):
            print(f"[{today}] 非交易日，跳过持仓异动监控")
            return 0
    except Exception:
        pass  # 日历失败不阻断

    print(f"[{date.today().isoformat()}] === 盘中持仓异动监控开始 ===")

    # 拉实时行情
    market_df = _fetch_realtime_prices()
    if market_df is None or market_df.empty:
        print("[ERROR] 无法获取实时行情，退出")
        return 1

    # 收集持仓（模拟账户 + 手动持仓）
    holdings = _collect_holdings()
    if not holdings:
        print("无持仓可监控，退出")
        return 0

    # 检测三类信号
    signals = _detect_signals(holdings, market_df)
    if not signals:
        print(f"[{date.today().isoformat()}] 无异动信号触发，不推送")
        _log_scan("normal", 0)
        return 0

    # 格式化消息
    msg = _format_message(signals)
    print(msg)

    if args.dry_run:
        print("[DRY-RUN] 不推送飞书")
        _log_scan("triggered_dry_run", len(signals))
        return 0

    # 推送飞书
    pushed = asyncio.run(_push_feishu(msg))
    _log_scan("triggered_pushed" if pushed else "triggered_push_failed", len(signals))
    return 0 if pushed else 1


def _fetch_realtime_prices():
    """用 safe_akshare_call 拉全市场实时快照."""
    try:
        from stockhot.core.rate_limiter import safe_akshare_call
        import akshare as ak

        return safe_akshare_call(ak.stock_zh_a_spot_em)
    except Exception as e:
        print(f"[ERROR] 获取实时行情失败: {type(e).__name__}: {e}")
        return None


def _collect_holdings() -> list[dict]:
    """收集模拟账户持仓 + 手动持仓，统一格式.

    返回 list[dict]，每个含: code(6位), ts_code(带后缀), name, source,
    stop_loss_hard, target_price, avg_cost(模拟仓成本)
    """
    holdings: list[dict] = []

    # 1. 模拟账户持仓（paper_positions）
    try:
        from davis_analyzer.paper_trading.account import PaperAccount
        from stockhot.core.config import DB_PATH
        import sqlite3

        acc = PaperAccount.load(PAPER_ACCOUNT)
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        # 查 watchlist 里的 stop_loss/target（sync_screen_to_watchlist 写入的）
        for pos in acc.get_positions():
            code6 = pos.ts_code.split(".")[0]
            wl = conn.execute(
                "SELECT target_entry_high, stop_loss_pct FROM invest_watchlist WHERE code=?",
                (code6,),
            ).fetchone()
            wl_d = dict(wl) if wl else {}
            # 止损 = 成本价 × (1 + stop_loss_pct)；目标 = watchlist 的 target_entry_high
            stop_pct = wl_d.get("stop_loss_pct", -0.12)
            stop_loss = round(pos.avg_cost * (1 + stop_pct), 2) if pos.avg_cost else None
            target = wl_d.get("target_entry_high")
            holdings.append({
                "code": code6,
                "ts_code": pos.ts_code,
                "name": pos.name,
                "source": "paper",
                "stop_loss_hard": stop_loss,
                "target_price": target,
                "avg_cost": pos.avg_cost,
            })
        conn.close()
    except Exception as e:
        print(f"[WARN] 读取模拟账户持仓失败: {type(e).__name__}: {e}")

    # 2. 手动持仓（invest_holdings）
    try:
        from stockhot.storage.database import get_connection

        conn = get_connection()
        for row in conn.execute(
            "SELECT code, name, stop_loss_hard, target_price "
            "FROM invest_holdings WHERE status='active'"
        ):
            d = dict(row)
            ts_code = _code_to_ts_code(d["code"])
            holdings.append({
                "code": d["code"],
                "ts_code": ts_code,
                "name": d["name"],
                "source": "manual",
                "stop_loss_hard": d["stop_loss_hard"],
                "target_price": d["target_price"],
                "avg_cost": None,
            })
        conn.close()
    except Exception as e:
        print(f"[WARN] 读取手动持仓失败: {type(e).__name__}: {e}")

    return holdings


def _code_to_ts_code(code: str) -> str:
    """6位代码 → 带后缀 ts_code（603061 → 603061.SH）."""
    if code.startswith(("60", "68", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _detect_signals(holdings: list[dict], market_df) -> list[dict]:
    """检测三类信号：止损/目标/涨跌幅异动."""
    import pandas as pd

    signals: list[dict] = []
    for h in holdings:
        row = market_df[market_df["代码"] == h["code"]]
        if row.empty:
            continue
        row = row.iloc[0]
        if pd.isna(row["最新价"]):
            continue
        current_price = float(row["最新价"])
        change_pct = float(row["涨跌幅"]) if not pd.isna(row["涨跌幅"]) else 0.0

        name = h["name"]
        tag = "📊" if h["source"] == "paper" else "💼"

        # 1. 止损触发
        sl = h["stop_loss_hard"]
        if sl and current_price <= sl:
            pct = (sl - current_price) / current_price * 100 if current_price > 0 else 0
            signals.append({
                "type": "stop_loss",
                "holding": h,
                "current": current_price,
                "text": f"🔴 {tag} {name}({h['code']}) 现价{current_price:.2f} ≤ 止损{sl:.2f}（已破{-pct:.1f}%）",
            })

        # 2. 目标触发
        tp = h["target_price"]
        if tp and current_price >= tp:
            pct = (current_price - tp) / tp * 100 if tp > 0 else 0
            signals.append({
                "type": "target",
                "holding": h,
                "current": current_price,
                "text": f"🎯 {tag} {name}({h['code']}) 现价{current_price:.2f} ≥ 目标{tp:.2f}（+{pct:.1f}%）",
            })

        # 3. 涨跌幅异动
        if abs(change_pct) >= PRICE_CHANGE_THRESHOLD:
            arrow = "📈" if change_pct > 0 else "📉"
            signals.append({
                "type": "anomaly",
                "holding": h,
                "current": current_price,
                "text": f"{arrow} {tag} {name}({h['code']}) 当日 {change_pct:+.1f}%",
            })

    return signals


def _format_message(signals: list[dict]) -> str:
    """格式化飞书消息（纯文本）."""
    today = date.today().isoformat()
    from datetime import datetime

    now = datetime.now().strftime("%H:%M")
    lines = [f"⚠️ 盘中持仓异动 | {today} {now}", ""]

    # 按类型分组
    stops = [s for s in signals if s["type"] == "stop_loss"]
    targets = [s for s in signals if s["type"] == "target"]
    anomalies = [s for s in signals if s["type"] == "anomaly"]

    if stops:
        lines.append("🔴 止损触发：")
        lines.extend(f"  {s['text']}" for s in stops)
        lines.append("")
    if targets:
        lines.append("🎯 目标触发：")
        lines.extend(f"  {s['text']}" for s in targets)
        lines.append("")
    if anomalies:
        lines.append("📈📉 涨跌幅异动：")
        lines.extend(f"  {s['text']}" for s in anomalies)
        lines.append("")

    lines.append("📊=模拟账户持仓  💼=手动持仓")
    return "\n".join(lines)


async def _push_feishu(message: str) -> bool:
    """推送飞书."""
    try:
        from stockhot.notification.feishu_bot import get_feishu_notifier

        notifier = get_feishu_notifier()
        if notifier is None:
            print("[WARN] 飞书未配置，跳过推送")
            return False
        await notifier.send_text(message)
        print("[OK] 飞书推送成功")
        return True
    except Exception as e:
        print(f"[ERROR] 飞书推送失败: {type(e).__name__}: {e}")
        return False


def _log_scan(status: str, rows: int) -> None:
    """写 scan_log."""
    try:
        from stockhot.data_layer import get_repository

        repo = get_repository()
        repo.log_scan(
            trade_date=date.today().isoformat(),
            module_name="intraday_holdings_alert",
            status=status,
            error_msg=None,
            started_at=None,
            rows_affected=rows,
        )
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
