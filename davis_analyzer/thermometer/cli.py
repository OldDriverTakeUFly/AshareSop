"""thermometer 模块 CLI(backfill/run/calibrate/report/status 子命令)。"""

from __future__ import annotations

import argparse


def _not_implemented(_: argparse.Namespace) -> None:
    raise SystemExit("该子命令尚未实现(按实施计划逐步落地)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="thermometer", description="板块温度计")
    sub = p.add_subparsers(dest="cmd", required=True)
    bf = sub.add_parser("backfill", help="全量回补:universe+sw_daily+ths+资金流聚合")
    bf.add_argument("--universe-only", action="store_true", help="仅刷新板块池/成分")
    bf.add_argument("--start", default="20220104")
    bf.add_argument("--end", default=None)
    bf.set_defaults(func=_not_implemented)
    run = sub.add_parser("run", help="盘后增量:当日行情+评分+日报+卡片")
    run.add_argument("--no-card", action="store_true")
    run.set_defaults(func=_not_implemented)
    cal = sub.add_parser("calibrate", help="IC/分组/walk-forward 校准")
    cal.add_argument("--start", default="20220104")
    cal.add_argument("--end", default=None)
    cal.set_defaults(func=_not_implemented)
    rep = sub.add_parser("report", help="生成盘后日报")
    rep.add_argument("--date", default=None)
    rep.set_defaults(func=_not_implemented)
    st = sub.add_parser("status", help="数据覆盖与温度最新日期")
    st.set_defaults(func=_not_implemented)
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)
