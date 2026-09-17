"""thermometer 模块 CLI(backfill/run/calibrate/report/status 子命令)."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta

from davis_analyzer.thermometer import (
    data,
    market_temp,
    moneyflow_agg,
    report,
    scoring,
    universe,
)

_THERMO_TABLES = ["sw_index", "sw_daily", "sw_member", "sector_moneyflow_daily",
                  "ths_index", "ths_daily", "ths_member",
                  "thermometer_sector", "thermometer_market"]


def _conn():
    from stockhot.data_layer.market_db import get_connection
    return get_connection()


def _gw():
    from stockhot.data_layer.tushare_gateway import get_gateway
    return get_gateway()


def _minus_days(day: str, n: int) -> str:
    """compact 日期减 n 自然日(窗口放宽无妨,聚合/评分幂等覆写)."""
    return (datetime.strptime(day, "%Y%m%d") - timedelta(days=n)).strftime("%Y%m%d")


def _build_card(latest: str) -> None:
    """评分完成后生成当日板块温度卡(Task 13 接入 cardgen;此前 no-op)."""
    print("卡片通道未接入(Task 13),跳过")


# ── 子命令 ─────────────────────────────────────────────────────────────

def cmd_backfill(args: argparse.Namespace) -> None:
    today = datetime.now().strftime("%Y%m%d")
    conn = _conn()
    try:
        gw = _gw()
        universe.refresh_sw_index(conn, gw)
        universe.refresh_sw_member(conn, gw, today)
        universe.refresh_ths_index(conn, gw)
        universe.refresh_ths_member(conn, gw, today)
        if args.universe_only:
            print("universe 刷新完成")
            return
        end = args.end or today
        r1 = data.backfill_sw_daily(conn, gw, args.start, end)
        r2 = data.backfill_ths_daily(conn, gw)
        r3 = moneyflow_agg.aggregate_sector_moneyflow(conn, args.start, end)
        r4 = scoring.score_history(conn, args.start, end)
        r5 = market_temp.compute_market_history(conn, args.start, end)
        print(f"backfill 完成: sw_daily={r1} ths_daily={r2} 资金={r3} "
              f"温度rows={r4['rows']} 大盘{len(r5)}日")
    finally:
        conn.close()


def cmd_run(args: argparse.Namespace) -> None:
    from davis_analyzer.limitup import db as limitup_db

    conn = _conn()
    try:
        gw = _gw()
        data.refresh_recent(conn, gw)  # 盘后自举:直连补最近缺失日(不依赖 stockhot 采集链)
        latest = limitup_db.latest_trade_date(conn)
        if latest is None:
            sys.exit("daily_price 为空,先跑 19:20 daily_refresh")
        moneyflow_agg.aggregate_sector_moneyflow(conn, _minus_days(latest, 40), latest)
        scoring.score_history(conn, _minus_days(latest, 730), latest)
        market_temp.compute_market_history(conn, _minus_days(latest, 1100), latest)
        path = report.write_daily_report(conn, latest)
        print(f"run 完成: 日报 {path}")
        if not args.no_card:
            _build_card(latest)
    finally:
        conn.close()


def cmd_calibrate(args: argparse.Namespace) -> None:
    from davis_analyzer.thermometer import calibrate

    conn = _conn()
    try:
        end = args.end or datetime.now().strftime("%Y%m%d")
        path = calibrate.run_calibration(conn, args.start, end)
        print(f"校准完成: {path}")
    finally:
        conn.close()


def cmd_report(args: argparse.Namespace) -> None:
    conn = _conn()
    try:
        day = args.date or conn.execute(
            "SELECT MAX(trade_date) FROM thermometer_sector").fetchone()[0]
        if day is None:
            sys.exit("thermometer_sector 为空,先 backfill/run")
        path = report.write_daily_report(conn, day.replace("-", ""))
        print(f"日报: {path}")
    finally:
        conn.close()


def cmd_status(_: argparse.Namespace) -> None:
    conn = _conn()
    try:
        for t in _THERMO_TABLES:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            hi = None
            try:
                hi = conn.execute(
                    f"SELECT MAX(trade_date) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                pass  # sw_index/ths_index 无 trade_date,只报行数
            print(f"{t:26s} {n:>10,}  latest={hi}")
    finally:
        conn.close()


# ── parser ────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="thermometer", description="板块温度计")
    sub = p.add_subparsers(dest="cmd", required=True)
    bf = sub.add_parser("backfill", help="全量回补:universe+sw_daily+ths+资金流聚合")
    bf.add_argument("--universe-only", action="store_true", help="仅刷新板块池/成分")
    bf.add_argument("--start", default="20220104")
    bf.add_argument("--end", default=None)
    bf.set_defaults(func=cmd_backfill)
    run = sub.add_parser("run", help="盘后增量:当日行情+评分+日报+卡片")
    run.add_argument("--no-card", action="store_true")
    run.set_defaults(func=cmd_run)
    cal = sub.add_parser("calibrate", help="IC/分组/walk-forward 校准")
    cal.add_argument("--start", default="20220104")
    cal.add_argument("--end", default=None)
    cal.set_defaults(func=cmd_calibrate)
    rep = sub.add_parser("report", help="生成盘后日报")
    rep.add_argument("--date", default=None)
    rep.set_defaults(func=cmd_report)
    st = sub.add_parser("status", help="数据覆盖与温度最新日期")
    st.set_defaults(func=cmd_status)
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)
