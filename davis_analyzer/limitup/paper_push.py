"""打板双臂模拟盘飞书日报：NAV/当日收益/持仓/当日交易一览（幂等，单条推送）.

推送范围默认 fb_base/fb_enhanced（limitup 双臂）；其他账户可经 ARMS 扩展。
数据源：stockhot.db 的 paper_* 表（只读）。
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger

from davis_analyzer.config import LIMITUP_REPORTS_DIR

# (账户名, 展示标签)——如需纳入 gx_* / abtest_* 臂，在此追加即可
ARMS: list[tuple[str, str]] = [
    ("fb_base", "基准"),
    ("fb_enhanced", "增强"),
]

_MARKER_DIR = Path(__file__).parent / "logs"


def _connect() -> sqlite3.Connection:
    from stockhot.core.config import DB_PATH

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _arm_summary(conn: sqlite3.Connection, name: str, label: str, day: str) -> str:
    acc = conn.execute(
        "SELECT id, initial_capital FROM paper_accounts WHERE name=?", (name,)
    ).fetchone()
    if acc is None:
        return f"■ {name}({label}): 账户不存在"
    nav = conn.execute(
        "SELECT trade_date, total_equity, daily_return FROM paper_nav_history "
        "WHERE account_id=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 1",
        (acc["id"], day),
    ).fetchone()
    if nav is None:
        return f"■ {name}({label}): 尚无运行记录"
    cum = nav["total_equity"] / acc["initial_capital"] - 1
    pos_n = conn.execute(
        "SELECT COUNT(*) FROM paper_positions WHERE account_id=?", (acc["id"],)
    ).fetchone()[0]
    trades = conn.execute(
        "SELECT action, name, shares, price FROM paper_trades "
        "WHERE account_id=? AND trade_date=? ORDER BY id", (acc["id"], day)
    ).fetchall()
    trade_bits = [
        f"{'买' if t['action'] == 'BUY' else '卖'}{t['name']} {t['shares']}@{t['price']:.3f}"
        for t in trades[:3]
    ]
    extra = f" 等{len(trades)}笔" if len(trades) > 3 else ""
    day_ret = nav["daily_return"]
    day_s = f"{day_ret:+.2%}" if day_ret is not None else "—"
    return (
        f"■ {name}({label}): NAV {nav['total_equity']:,.0f}（{day_s}｜累计 {cum:+.1%}）"
        f"｜持仓 {pos_n}｜{'、'.join(trade_bits) + extra if trade_bits else '当日无交易'}"
        f"｜截至 {nav['trade_date']}"
    )


def build_arms_summary(day: str) -> str:
    conn = _connect()
    try:
        lines = [f"[打板双臂日报] {day}"]
        lines += [_arm_summary(conn, name, label, day) for name, label in ARMS]
        # 排队模拟摘要（market_data.db；无记录时自述）
        try:
            from davis_analyzer.limitup.db import connect as _mkt_connect

            from davis_analyzer.limitup import queue_sim

            mkt = _mkt_connect()
            try:
                lines.append(queue_sim.queue_summary(mkt, day))
            finally:
                mkt.close()
        except Exception as exc:  # 摘要失败不影响主报告
            lines.append(f"排队模拟[{day}]: 摘要不可用（{exc}）")
        lines.append("（candidates 清单见 davis_analyzer/limitup/reports/）")
        return "\n".join(lines)
    finally:
        conn.close()


def push_paper_summary(day: str, *, force: bool = False) -> bool:
    """Push the dual-arm daily summary to Feishu (idempotent per day).

    Returns True if pushed (or already pushed today and not forced).
    """
    _MARKER_DIR.mkdir(parents=True, exist_ok=True)
    marker = _MARKER_DIR / f"paper_push_{day}.ok"
    if marker.exists() and not force:
        logger.info("paper_push {} 已推送过（幂等跳过）", day)
        return True

    from stockhot.notification.feishu_bot import get_feishu_notifier

    notifier = get_feishu_notifier()
    if notifier is None:
        logger.warning("paper_push: 飞书未配置，跳过推送（摘要已生成）")
        logger.info("\n{}", build_arms_summary(day))
        return False
    text = build_arms_summary(day)
    result = asyncio.run(notifier.send_text(text))
    if result.get("code") == 0:
        marker.write_text(datetime.now().isoformat(), encoding="utf-8")
        logger.info("paper_push {} 推送成功", day)
        return True
    logger.warning("paper_push {} 推送失败: {}", day, result)
    return False


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")
