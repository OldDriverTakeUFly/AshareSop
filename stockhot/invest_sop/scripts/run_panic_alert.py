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
    from stockhot.alert import detect_panic_signals, format_alert_message
    report = detect_panic_signals()

    msg = format_alert_message(report)
    print(msg)

    # 无触发 → 不推送
    if not report.any_triggered:
        print(f"\n[{date.today().isoformat()}] 无恐慌信号触发，不推送")
        # 写 scan_log（状态 normal）
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
