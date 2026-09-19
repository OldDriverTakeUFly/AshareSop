"""surge CLI: run / backfill / status (python -m davis_analyzer.surge)."""

from __future__ import annotations

import argparse
import sys

from loguru import logger

from davis_analyzer.surge import chips, cninfo, db, report, screen


def _cmd_run(args: argparse.Namespace) -> int:
    out = screen.run_day(args.date, do_cninfo=not args.no_cninfo)
    print(f"surge {out['day']}: pool={out['pool_n']} "
          f"pattern={len(out['pattern_df'])} cyq_day={out['cyq_day'] or '缺失'}")
    if not args.no_report:
        p1 = report.render_full_report(out["day"], out)
        p2 = report.render_pattern_report(out["day"], out)
        print(f"报告: {p1}\n     {p2}")
    if not args.no_longpic:
        from davis_analyzer.surge import longpic
        conn = db.connect()
        paths = longpic.run_longpics(
            conn, out["day"], out["snapshot_df"], out["pattern_df"],
            out["tags_df"])
        print("长图(出口数字闸放行): " + " / ".join(str(x) for x in paths))
    return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    conn = db.connect()
    pro = screen._tushare_pro()
    day0 = db.latest_trade_date(conn)
    dates = db.trading_dates(conn, "20200101", day0 or "20991231")
    if args.replay:
        targets = dates[-args.replay:]
        results = screen.backfill_replay(conn, pro, targets)
        print(f"回放 {len(results)} 日完成(样本 "
              f"{sum(len(r['snapshot_df']) for r in results)} 行)")
        if args.with_cninfo:
            for r in results:
                codes = (r["snapshot_df"]["ts_code"].tolist()
                         if not r["snapshot_df"].empty else [])
                if codes:
                    cninfo.sync_cninfo(conn, codes, r["day"])
            print("巨潮回补完成")
        if not args.no_report and results:
            latest = results[-1]
            report.render_full_report(latest["day"], latest)
            report.render_pattern_report(latest["day"], latest)
    elif args.cyq_days:
        done = chips.backfill_cyq(conn, pro, dates[-args.cyq_days:])
        print(f"cyq 回补 {len(done)} 日: {done[:10]}{'...' if len(done) > 10 else ''}")
    else:
        print("需要 --cyq-days N 或 --replay N")
        return 2
    return 0


def _cmd_status(_: argparse.Namespace) -> int:
    conn = db.connect()
    day = db.latest_trade_date(conn)
    cyq_days = conn.execute(
        "SELECT COUNT(DISTINCT trade_date) FROM cyq_perf_cache").fetchone()[0]
    ann = conn.execute(
        "SELECT COUNT(*) FROM cninfo_announcement").fetchone()[0]
    events = conn.execute("SELECT COUNT(*) FROM major_events").fetchone()[0]
    pat = conn.execute("SELECT COUNT(*) FROM surge_pattern_hits").fetchone()[0]
    tags = conn.execute("SELECT COUNT(*) FROM surge_tags").fetchone()[0]
    print(f"latest_trade_date={day} cyq_days={cyq_days} "
          f"announcements={ann} major_events={events} "
          f"pattern_hits={pat} tags={tags}")
    for d, n in conn.execute(
            "SELECT trade_date, COUNT(*) FROM surge_snapshot "
            "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5"):
        print(f"  snapshot {d}: {n} 行")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="surge", description="涨幅7%+筛选分析(九维/形态副本/16标签)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="当日筛选(巨潮→cyq→九维→形态→两份报告)")
    p_run.add_argument("--date", default=None, help="YYYYMMDD,默认最新交易日")
    p_run.add_argument("--no-cninfo", action="store_true", help="跳过巨潮拉取")
    p_run.add_argument("--no-report", action="store_true")
    p_run.add_argument("--no-longpic", action="store_true",
                       help="跳过750px长图(出口数字闸)")
    p_run.set_defaults(func=_cmd_run)
    p_bf = sub.add_parser("backfill", help="回补")
    p_bf.add_argument("--cyq-days", type=int, default=0, help="cyq_perf 回补日数")
    p_bf.add_argument("--replay", type=int, default=0,
                      help="回放最近 N 个交易日截面")
    p_bf.add_argument("--with-cninfo", action="store_true",
                      help="回放时对命中池拉巨潮(慎用,请求量大)")
    p_bf.add_argument("--no-report", action="store_true")
    p_bf.set_defaults(func=_cmd_backfill)
    p_st = sub.add_parser("status", help="台账状态")
    p_st.set_defaults(func=_cmd_status)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:  # cron 无人值守: 显式非零退出码
        logger.exception("surge {} 失败", args.cmd)
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
