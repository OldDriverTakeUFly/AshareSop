#!/usr/bin/env python
"""盘中恐慌预警入口脚本 — 检测三大恐慌信号，达标时推送飞书.

Designed for crontab — 盘中定时运行（10:30/11:30/13:30/14:30）。
非交易日或无信号触发时不推送（避免打扰）。

三大信号（任一达标即推送）：
1. 系统性恐慌：≥3 个指数 RV20 历史分位 ≥ 90
2. 行为面恐慌抛售：涨跌停比 < 0.5 或 跌停占比 > 50%
3. iVIX/V-R 极端值：iVIX > 25 或 V/R > 1.3

⚠️ 信号仅提示恐慌升温，不构成交易建议。

Usage:
    .venv/bin/python stockhot/invest_sop/scripts/run_panic_alert.py [--dry-run]

Crontab (盘中 4 次):
    30 10,11,13,14 * * 1-5 cd /path && PYTHONPATH=/path \\
        .venv/bin/python stockhot/invest_sop/scripts/run_panic_alert.py \\
        >> stockhot/invest_sop/logs/panic_alert.log 2>&1
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="盘中恐慌预警检测 + 飞书推送")
    parser.add_argument("--dry-run", action="store_true",
                        help="只检测不推送（打印消息到 stdout）")
    args = parser.parse_args(argv)

    # 交易日校验
    try:
        from stockhot.invest_sop.utils.trading_calendar import is_trading_day
        today = date.today().isoformat()
        if not is_trading_day(today):
            print(f"[{today}] 非交易日，跳过恐慌检测")
            return 0
    except Exception:
        pass  # 日历失败不阻断

    print(f"[{date.today().isoformat()}] === 恐慌预警检测开始 ===")

    # 检测信号
    from stockhot.alert import detect_panic_signals, format_alert_message, format_trend_section
    report = detect_panic_signals()

    # 存 panic_history（无论是否触发都存，用于趋势分析）
    _save_panic_history(report)

    # 构建趋势部分（今日盘中变化 + vs 昨日收盘 + 多日趋势）
    trend_text = _build_trend(report)

    msg = format_alert_message(report)
    if trend_text:
        msg = msg + "\n" + trend_text
    print(msg)

    # 无触发 → 不推送
    if not report.any_triggered:
        print(f"\n[{date.today().isoformat()}] 无恐慌信号触发，不推送")
        _log_panic_scan(report, "normal")
        return 0

    # 有触发 → 推送飞书
    print(f"\n[{date.today().isoformat()}] 恐慌信号触发：{report.triggered_names}")

    if args.dry_run:
        print("[DRY-RUN] 不推送飞书")
        _log_panic_scan(report, "triggered_dry_run")
        return 0

    # 推送
    pushed = asyncio.run(_push_feishu(msg))
    _log_panic_scan(report, "triggered_pushed" if pushed else "triggered_push_failed")
    return 0 if pushed else 1


def _save_panic_history(report) -> None:
    """把本次检测读数写入 panic_history 表."""
    try:
        from stockhot.data_layer import get_repository
        repo = get_repository()
        # 从 report 提取关键读数
        limit_up = limit_down = broken = None
        up_down_ratio = None
        for sig in report.signals:
            if "行为面" in sig.name:
                # detail 格式: "涨停X/跌停Y/炸板Z，涨跌停比W..."
                import re
                m = re.search(r"涨停(\d+)/跌停(\d+)/炸板(\d+).*?涨跌停比([\d.]+)", sig.detail)
                if m:
                    limit_up = int(m.group(1))
                    limit_down = int(m.group(2))
                    broken = int(m.group(3))
                    up_down_ratio = float(m.group(4))

        rv20_max_pct = max(
            (i.rv20_pct for i in report.volatility_indices), default=None
        )
        rv20_p90_n = sum(1 for i in report.volatility_indices if i.rv20_pct >= 90)

        repo.save_panic_history(
            trade_date=report.trade_date,
            check_time=report.timestamp,
            triggered=report.any_triggered,
            triggered_names=report.triggered_names,
            limit_up=limit_up, broken=broken, limit_down=limit_down,
            up_down_ratio=up_down_ratio,
            ivix_current=report.ivix_value,
            rv20_max_pct=rv20_max_pct,
            rv20_indices_p90=rv20_p90_n,
        )
    except Exception as e:
        print(f"[WARN] save_panic_history failed: {e}")


def _build_trend(report) -> str:
    """构建趋势部分文本（今日盘中 + vs 昨日 + 多日）."""
    try:
        from stockhot.data_layer import get_repository
        from stockhot.alert import format_trend_section
        from datetime import date as _date, timedelta as _timedelta
        repo = get_repository()

        # 今日盘中历史
        today_history = repo.get_panic_history_today(report.trade_date)

        # 昨日收盘
        yesterday = (_date.today() - _timedelta(days=1)).isoformat()
        # 跳过非交易日（往前找最近的收盘）
        yesterday_close = repo.get_volatility_market(yesterday)
        if not yesterday_close:
            # 尝试前 2-3 天（周末情况）
            for back in range(2, 5):
                d = (_date.today() - _timedelta(days=back)).isoformat()
                yesterday_close = repo.get_volatility_market(d)
                if yesterday_close:
                    break

        # 近 5 日收盘趋势
        import sqlite3
        from stockhot.data_layer import MARKET_DB_PATH
        multi_day = []
        with sqlite3.connect(str(MARKET_DB_PATH)) as conn:
            rows = conn.execute(
                "SELECT trade_date, ivix_current, limit_up, limit_down "
                "FROM daily_volatility_market ORDER BY trade_date DESC LIMIT 5"
            ).fetchall()
            multi_day = [
                {"trade_date": r[0], "ivix_current": r[1], "limit_up": r[2], "limit_down": r[3]}
                for r in rows
            ]

        return format_trend_section(today_history, yesterday_close, multi_day)
    except Exception as e:
        print(f"[WARN] build_trend failed: {e}")
        return ""


async def _push_feishu(message: str) -> bool:
    """推送消息到飞书，返回是否成功."""
    try:
        from stockhot.notification.feishu_bot import get_feishu_notifier
        notifier = get_feishu_notifier()
        if notifier is None:
            print("[WARN] FEISHU_WEBHOOK_URL 未配置，跳过推送")
            return False
        await notifier.send_text(message)
        print("[OK] 飞书推送成功")
        return True
    except Exception as e:
        print(f"[ERROR] 飞书推送失败: {type(e).__name__}: {e}")
        return False


def _log_panic_scan(report, status: str) -> None:
    """写 scan_log."""
    try:
        from stockhot.data_layer import get_repository
        repo = get_repository()
        repo.log_scan(
            trade_date=report.trade_date,
            module_name="panic_alert",
            status=status,
            error_msg=None,
            started_at=None,
            rows_affected=len(report.triggered_names),
        )
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
