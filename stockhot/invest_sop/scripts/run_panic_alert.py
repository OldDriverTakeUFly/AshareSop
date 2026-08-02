#!/usr/bin/env python
"""盘中恐慌预警入口脚本 — 检测四象限市场状态，推送飞书.

Designed for crontab — 盘中定时运行（10:30/11:30/13:30/14:30）。
非交易日或数据完全不可用时不推送。

四象限分类（叠加方向维度，区分加仓点/减仓点）：
  🔴 下跌恐慌：高波 P90+ × 方向↓ → 减仓信号
  🟠 逼空过热：高波 P90+ × 方向↑ → 防回撤
  🟡 阴跌预警：低波 P90- × 方向↓ → 谨慎观望
  🟢 强势上涨：低波 P90- × 方向↑ → 加仓机会

四象限都推送（每个都有行动参考），仅当 quadrant 为空（数据全部不可用）时不推。

三大传统信号仍并行检测（用于详情展示）：
1. 系统性恐慌：≥3 个指数 RV20 历史分位 ≥ 90
2. 行为面恐慌抛售：涨跌停比 < 0.5 或 跌停占比 > 50%
3. iVIX/V-R 极端值：iVIX > 25 或 V/R > 1.3

⚠️ 信号仅提示市场状态，不构成交易建议。

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

    # 推送策略：四象限都推（强势=加仓机会，下跌=减仓信号，同等信息价值）
    # 仅当 quadrant 为空（数据全部不可用）时才不推
    should_push = bool(report.quadrant)
    if not should_push:
        print(f"\n[{date.today().isoformat()}] 象限判定数据不足，不推送")
        _log_panic_scan(report, "normal")
        return 0

    # 推送飞书
    print(f"\n[{date.today().isoformat()}] 推送象限={report.quadrant} 强度={report.intensity_score:.0f}")

    if args.dry_run:
        print("[DRY-RUN] 不推送飞书")
        _log_panic_scan(report, "triggered_dry_run")
        return 0

    # 推送
    pushed = asyncio.run(_push_feishu(msg))
    _log_panic_scan(report, "triggered_pushed" if pushed else "triggered_push_failed")

    # 追加图片仪表盘推送（文本 + 图片双消息）
    if pushed:
        _push_dashboard_image(report)

    return 0 if pushed else 1


def _save_panic_history(report) -> None:
    """把本次检测读数写入 panic_history 表.

    直接从 report 的结构化字段读（不再用正则解析 detail 文本）：
    - 涨跌停：从 report.direction（DirectionReading）读
    - RV20：从 report.volatility_indices 聚合
    - iVIX：从 report.ivix_value 读
    - 象限/强度/方向：从 report 顶层字段读
    """
    try:
        from stockhot.data_layer import get_repository
        repo = get_repository()

        # 行为面读数（优先从 direction 拿，回退到 signals 解析）
        limit_up = limit_down = broken = None
        up_down_ratio = None
        if report.direction is not None:
            limit_up = report.direction.limit_up
            limit_down = report.direction.limit_down
            broken = report.direction.broken
            up_down_ratio = report.direction.limit_ratio

        # RV20 聚合
        rv20_max_pct = max(
            (i.rv20_pct for i in report.volatility_indices), default=None
        )
        rv20_p90_n = sum(1 for i in report.volatility_indices if i.rv20_pct >= 90)

        # 方向分（direction_score / sse_pct_chg）
        direction_score = report.direction.direction_score if report.direction else None
        sse_pct_chg = report.direction.sse_pct_chg if report.direction else None

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
            quadrant=report.quadrant or None,
            intensity_score=report.intensity_score or None,
            direction_score=direction_score,
            sse_pct_chg=sse_pct_chg,
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


def _push_dashboard_image(report) -> None:
    """生成仪表盘图片并推送到飞书（文本消息的补充）.

    失败不阻断主流程（文本已推送成功，图片是锦上添花）。
    """
    try:
        from stockhot.alert.panic_chart_builder import (
            build_panic_dashboard, render_dashboard_png, add_trend_data,
        )
        import sqlite3
        from stockhot.data_layer import MARKET_DB_PATH

        # 构建图表
        fig = build_panic_dashboard(report)

        # 注入趋势数据（近 5 日跌停 + iVIX）
        with sqlite3.connect(str(MARKET_DB_PATH)) as conn:
            rows = conn.execute(
                "SELECT trade_date, limit_down, ivix_current "
                "FROM daily_volatility_market ORDER BY trade_date DESC LIMIT 5"
            ).fetchall()
        if rows:
            rows = list(reversed(rows))  # 正序
            dates = [r[0][5:] for r in rows]  # MM-DD
            limit_downs = [r[1] or 0 for r in rows]
            ivix_vals = [r[2] or 0 for r in rows]
            add_trend_data(fig, dates, limit_downs, ivix_vals)

        # 渲染 PNG
        png_path = render_dashboard_png(fig)

        # 推送图片（复用 _push_feishu 的 notifier）
        pushed = asyncio.run(_push_image_feishu(png_path))
        if pushed:
            print("[OK] 仪表盘图片推送成功")
        else:
            print("[WARN] 仪表盘图片推送失败（文本已成功，不影响）")

        # 清理临时文件
        import os
        if png_path.startswith("/tmp/") or png_path.startswith(tempfile.gettempdir() if hasattr(__import__('tempfile'), 'gettempdir') else "/tmp"):
            os.unlink(png_path)

    except ImportError as e:
        print(f"[WARN] 图片生成依赖未安装（plotly/kaleido）: {e}")
    except Exception as e:
        print(f"[WARN] 仪表盘图片生成/推送失败: {type(e).__name__}: {e}")


async def _push_image_feishu(image_path: str) -> bool:
    """推送图片到飞书，返回是否成功."""
    try:
        from stockhot.notification.feishu_bot import get_feishu_notifier
        notifier = get_feishu_notifier()
        if notifier is None:
            return False
        if not hasattr(notifier, "send_image"):
            print("[WARN] 当前 notifier 不支持图片推送（仅企业自建应用支持）")
            return False
        await notifier.send_image(image_path)
        return True
    except Exception as e:
        print(f"[ERROR] 图片推送失败: {type(e).__name__}: {e}")
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
