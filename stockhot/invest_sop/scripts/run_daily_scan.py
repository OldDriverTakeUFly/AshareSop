#!/usr/bin/env python
"""Daily market scan orchestrator for after-hours data collection.

Runs the 6 stockhot analysis modules (limit_up → dragon_tiger +
fund_flow + index_technical + volatility → risk_alert) in the order
mandated by the daily-market-scan skill, with try/except isolation so
one module's failure does not block others. Persists all results to
SQLite.

Designed for crontab — run at 17:00+ on trading days (after the
dragon-tiger list is published ~17:30).

Usage:
    .venv/bin/python stockhot/invest_sop/scripts/run_daily_scan.py [--date YYYY-MM-DD]

Crontab example (17:00 Mon–Fri):
    0 17 * * 1-5 cd /home/leo/Projects/AshareSop && \\
        PYTHONPATH=/home/leo/Projects/AshareSop \\
        .venv/bin/python stockhot/invest_sop/scripts/run_daily_scan.py \\
        >> stockhot/invest_sop/logs/daily_scan.log 2>&1
"""

from __future__ import annotations

import argparse
import sys
import time as _time
from datetime import date


def _log_scan(trade_date: str, module: str, status: str,
              error_msg: str | None = None, started_at: float | None = None,
              rows: int | None = None) -> None:
    """记录采集模块执行结果到 market_data.db 的 scan_log 表.

    解决"不知道哪个模块跑没跑"的问题——盘后总结/盘前报告可通过
    repository.get_scan_log(date) 查询当日各模块状态。
    """
    try:
        from stockhot.data_layer import get_repository
        repo = get_repository()
        repo.log_scan(
            trade_date=trade_date, module_name=module, status=status,
            error_msg=error_msg, started_at=started_at,
            finished_at=_time.time(), rows_affected=rows,
        )
    except Exception:
        pass  # scan_log 失败不应阻断采集


# ── Wave 1: limit_up (must run first — risk_alert reads its DB output) ──


def run_wave_0(trade_date: str) -> bool:
    """Wave 0: 更新全市场个股日线行情 (daily_price + daily_basic).

    这是所有后续分析的基础数据——如果不更新，盘后分析/策略运行会用到旧数据。
    使用 repository.get_daily_by_date() 分页拉取全市场 ~5500 只股票的 OHLCV+adj_factor，
    并通过 TushareClient 补充 daily_basic（PE/PB/turnover_rate）。

    耗时约 2-3 分钟（Tushare 分页限频）。
    """
    t0 = _time.time()
    try:
        from stockhot.data_layer import get_repository

        repo = get_repository()
        # get_daily_by_date 内部会查缓存；如果当日已有 >1000 行则跳过
        df = repo.get_daily_by_date(trade_date, use_cache=True)
        n_prices = len(df) if df is not None else 0

        # 同步更新 daily_basic（PE/PB/turnover 等）—— 用于策略 PE 百分位计算
        n_basic = 0
        try:
            from davis_analyzer.core.tushare_client import TushareClient

            client = TushareClient()
            basic_df = client.get_daily_basic_by_date(
                trade_date,
                fields=(
                    "ts_code,trade_date,pe_ttm,pb,ps,total_mv,"
                    "turnover_rate,circ_mv,free_share"
                ),
            )
            if basic_df is not None and not basic_df.empty:
                from stockhot.data_layer.market_db import get_connection
                import time as _t

                records = []
                for r in basic_df.to_dict("records"):
                    records.append((
                        r.get("ts_code", ""), str(r.get("trade_date", trade_date)),
                        r.get("pe_ttm"), r.get("pb"), r.get("ps"), r.get("total_mv"),
                        r.get("turnover_rate"), r.get("circ_mv"), r.get("free_share"),
                        _t.time(),
                    ))
                with get_connection() as conn:
                    conn.executemany(
                        "INSERT OR REPLACE INTO daily_basic "
                        "(ts_code, trade_date, pe_ttm, pb, ps, total_mv, "
                        "turnover_rate, circ_mv, free_share, fetched_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        records,
                    )
                    conn.commit()
                n_basic = len(records)
        except Exception as e_basic:
            print(f"[{trade_date}] Wave 0 daily_basic: WARNING — {type(e_basic).__name__}: {e_basic}")
            # daily_basic 失败不阻断 daily_price

        print(f"[{trade_date}] Wave 0 market_data: {n_prices} prices + {n_basic} basic ✓")

        # T352(D8): 写后完整性自检——20261030 曾发生 thermometer 骨架行铺满
        # (total_mv 全 NULL)静默事故;NULL>100 即告警,不静默
        try:
            from stockhot.data_layer.market_db import get_connection as _gc
            with _gc() as _c:
                _nulls = _c.execute(
                    "SELECT COUNT(*) FROM daily_basic WHERE trade_date=?"
                    " AND total_mv IS NULL", (trade_date,)).fetchone()[0]
            if _nulls > 100:
                _msg = (f"🚨 daily_basic 完整性告警 {trade_date}: total_mv NULL {_nulls} 行"
                        f"(全市场~5570)——疑似骨架行污染,amp-pool 等下游将回退旧快照")
                print(_msg)
                try:
                    from stockhot.notification.feishu_bot import get_feishu_notifier as _gf
                    import asyncio as _a
                    _n = _gf()
                    if _n is not None:
                        _a.run(_n.send_text(_msg))
                except Exception as _e2:
                    print(f"[WARN] 告警推送失败: {_e2}")
        except Exception as _e:
            print(f"[WARN] 完整性自检失败(不阻断): {_e}")

        _log_scan(trade_date, "market_data", "success", started_at=t0,
                  rows=n_prices + n_basic)
        return n_prices > 0
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"[{trade_date}] Wave 0 market_data: FAILED — {msg}")
        _log_scan(trade_date, "market_data", "failed", error_msg=msg, started_at=t0)
        # T352(D8): 失败即告警(2026-10-27~29 曾静默三晚,bug 由画像线下游发现)
        try:
            from stockhot.notification.feishu_bot import get_feishu_notifier as _gf
            import asyncio as _a
            _n = _gf()
            if _n is not None:
                _a.run(_n.send_text(f"🚨 daily_scan Wave 0 失败 {trade_date}: {msg}"
                                    f"——daily_price/daily_basic 今日未更新"))
        except Exception as _e2:
            print(f"[WARN] 失败告警推送异常: {_e2}")
        return False


def run_wave_1(trade_date: str) -> bool:
    """limit_up analysis —涨停池/炸板池/跌停池/连板梯队/板块联动."""
    t0 = _time.time()
    try:
        from stockhot.limit_up import run_limit_up_analysis

        run_limit_up_analysis(trade_date)
        # 从 DB 读实际入库行数（result 返回值结构不稳定，DB 是 ground truth）
        from stockhot.storage.database import get_daily_data

        data = get_daily_data(trade_date)
        count = len(data.get("limit_up_pool", []))
        print(f"[{trade_date}] Wave 1 limit_up: OK ({count} 涨停)")
        _log_scan(trade_date, "limit_up", "success", started_at=t0, rows=count)
        return True
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"[{trade_date}] Wave 1 limit_up: FAILED — {msg}")
        _log_scan(trade_date, "limit_up", "failed", error_msg=msg, started_at=t0)
        return False


# ── Wave 2: dragon_tiger + fund_flow + index_technical + volatility
#    (all parallel-safe, none reads the others' DB output) ──


def run_wave_2(trade_date: str) -> tuple[bool, bool, bool, bool]:
    """dragon_tiger (龙虎榜) + fund_flow (资金流) + index_technical (技术面) + volatility (波动率)."""
    # fund_flow
    ff_ok = False
    t0 = _time.time()
    try:
        from stockhot.fund_flow import run_fund_flow_analysis

        run_fund_flow_analysis(trade_date)
        from stockhot.storage.database import get_daily_data

        ff_data = get_daily_data(trade_date)
        ff_rows = len(ff_data.get("fund_flow_sector", []))
        print(f"[{trade_date}] Wave 2 fund_flow: OK ({ff_rows} 板块)")
        _log_scan(trade_date, "fund_flow", "success", started_at=t0, rows=ff_rows)
        ff_ok = True
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"[{trade_date}] Wave 2 fund_flow: FAILED — {msg}")
        _log_scan(trade_date, "fund_flow", "failed", error_msg=msg, started_at=t0)

    # dragon_tiger
    dt_ok = False
    t0 = _time.time()
    try:
        from stockhot.dragon_tiger import run_dragon_tiger_analysis

        run_dragon_tiger_analysis(trade_date)
        from stockhot.storage.database import get_daily_data

        dt_data = get_daily_data(trade_date)
        dt_rows = len(dt_data.get("dragon_tiger_detail", []))
        print(f"[{trade_date}] Wave 2 dragon_tiger: OK ({dt_rows} 条)")
        _log_scan(trade_date, "dragon_tiger", "success", started_at=t0, rows=dt_rows)
        dt_ok = True
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"[{trade_date}] Wave 2 dragon_tiger: FAILED — {msg}")
        _log_scan(trade_date, "dragon_tiger", "failed", error_msg=msg, started_at=t0)

    # index_technical (大盘技术面)
    it_ok = False
    t0 = _time.time()
    try:
        from stockhot.index_technical import run_index_technical_analysis
        from stockhot.storage.database import save_daily_data

        result = run_index_technical_analysis(trade_date)
        if result.get("status") == "success":
            save_daily_data({"date": trade_date, "index_technical": result})
        print(f"[{trade_date}] Wave 2 index_technical: OK")
        _log_scan(trade_date, "index_technical", "success", started_at=t0)
        it_ok = True
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"[{trade_date}] Wave 2 index_technical: FAILED — {msg}")
        _log_scan(trade_date, "index_technical", "failed", error_msg=msg, started_at=t0)

    # volatility (波动率观察 — 中国版 VIX 五层体系)
    vol_ok = False
    t0 = _time.time()
    try:
        from stockhot.volatility import run_volatility_analysis

        run_volatility_analysis(trade_date)
        print(f"[{trade_date}] Wave 2 volatility: OK")
        _log_scan(trade_date, "volatility", "success", started_at=t0)
        vol_ok = True
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"[{trade_date}] Wave 2 volatility: FAILED — {msg}")
        _log_scan(trade_date, "volatility", "failed", error_msg=msg, started_at=t0)

    return ff_ok, dt_ok, it_ok, vol_ok


# ── Wave 3: risk_alert (reads upstream DB data — must run last) ──


def run_wave_3(trade_date: str) -> bool:
    """risk_alert — ST/异常波动/资金出逃/高位连板 (reads limit_up + dragon_tiger)."""
    t0 = _time.time()
    try:
        from stockhot.risk_alert import run_risk_alert_analysis

        run_risk_alert_analysis(trade_date)
        print(f"[{trade_date}] Wave 3 risk_alert: OK")
        _log_scan(trade_date, "risk_alert", "success", started_at=t0)
        return True
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"[{trade_date}] Wave 3 risk_alert: FAILED — {msg}")
        _log_scan(trade_date, "risk_alert", "failed", error_msg=msg, started_at=t0)
        return False


def _guard_index_gap(trade_date: str) -> None:
    """index_daily 缺口守卫(T364):最近开市日未入库即飞书告警。

    2026-11-18 实锤:Tushare 空响应时增量写入守卫静默跳过(index_technical
    波次照常报 OK),当晚无任何告警,次日全球科技早报据此误判「休市精简」。
    交易日判定走 AKShare 日历(非交易日不判缺口,防节假日误报);告警失败只
    打日志,不改变 scan 退出码。仅统计意义告警——触发后人工用
    `run_daily_scan.py --date <缺口日>` 补采。
    """
    from contextlib import closing

    from stockhot.data_layer.market_db import get_connection
    from stockhot.invest_sop.utils.trading_calendar import (
        get_recent_trade_day, is_trading_day)

    expected_dash = get_recent_trade_day(trade_date)
    if not expected_dash or not is_trading_day(expected_dash):
        return  # 日历不可用/非交易日 → 不判缺口
    expected = expected_dash.replace("-", "")
    with closing(get_connection()) as conn:
        row = conn.execute("SELECT MAX(trade_date) FROM index_daily").fetchone()
    latest = (row[0] or "") if row else ""
    if latest >= expected:
        return
    msg = (f"⚠️ 行情采集缺口:{trade_date} 扫描后 index_daily 最新={latest},"
           f"预期≥{expected}——次日全球科技早报将误判「休市精简」。"
           f"处置:补跑 run_daily_scan.py --date {expected_dash} 后复核。")
    print(f"[{trade_date}] {msg}")
    try:
        import asyncio

        from stockhot.notification.feishu_bot import get_feishu_notifier
        notifier = get_feishu_notifier()
        if notifier:
            asyncio.run(notifier.send_text(msg))
    except Exception as e:
        print(f"[{trade_date}] feishu alert failed: {type(e).__name__}: {e}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run daily market scan (6 modules)")
    parser.add_argument("--date", default=None, help="Trade date YYYY-MM-DD (default: today)")
    args = parser.parse_args(argv)

    trade_date = args.date or date.today().isoformat()

    # Check trading day
    try:
        from stockhot.invest_sop.utils.trading_calendar import is_trading_day

        if not is_trading_day(trade_date):
            print(f"[{trade_date}] Not a trading day, skipping scan.")
            return 0
    except Exception:
        pass  # If calendar check fails, proceed anyway

    print(f"[{trade_date}] === Daily Market Scan starting ===")

    results = {}
    # Wave 0: 更新全市场个股行情（daily_price + daily_basic）
    # 这是所有后续分析的基础——必须在其他模块之前运行
    results["market_data"] = run_wave_0(trade_date)
    results["limit_up"] = run_wave_1(trade_date)
    ff_ok, dt_ok, it_ok, vol_ok = run_wave_2(trade_date)
    results["fund_flow"] = ff_ok
    results["dragon_tiger"] = dt_ok
    results["index_technical"] = it_ok
    results["volatility"] = vol_ok
    results["risk_alert"] = run_wave_3(trade_date)

    succeeded = sum(results.values())
    total = len(results)
    print(f"[{trade_date}] === Scan complete: {succeeded}/{total} modules succeeded ===")
    for mod, ok in results.items():
        status = "✓" if ok else "✗"
        print(f"  {status} {mod}")

    # Wave 4: 同步 daily_data JSON → market_data.db 结构化表
    # 让 after-hours-review 等消费方能直接读结构化表（repo.get_limit_pool 等），
    # 不改采集模块源码（符合 daily-market-scan guardrail）。
    if succeeded > 0:
        try:
            from stockhot.data_layer.migrate_panels import sync_single_day
            n = sync_single_day(trade_date)
            print(f"[{trade_date}] Wave 4 sync_to_market_db: {n} rows synced ✓")
            _log_scan(trade_date, "sync_market_db", "success", rows=n)
        except Exception as e:
            print(f"[{trade_date}] Wave 4 sync_to_market_db: FAILED — {type(e).__name__}: {e}")
            _log_scan(trade_date, "sync_market_db", "failed",
                      error_msg=f"{type(e).__name__}: {e}")

        # 顺带清理过期缓存行（每天一次，低频不影响采集）
        try:
            from stockhot.data_layer import get_repository
            deleted = get_repository().cleanup_expired_cache()
            if any(deleted.values()):
                print(f"[{trade_date}] Wave 4 cleanup: {deleted}")
        except Exception:
            pass  # cleanup 失败不影响主流程

    # T364 采集缺口守卫:scan 完成后 index_daily 必须已推进到最近已开市日——
    # 2026-11-18 Tushare 空响应被增量写入守卫静默跳过(波次照常报 OK),次日
    # globaltech 早报误判「休市精简」并带出硬编码「国庆假期」横幅的实锤。
    try:
        _guard_index_gap(trade_date)
    except Exception as e:  # 守卫自身故障不拖垮 scan 退出码
        print(f"[{trade_date}] index freshness guard error: {type(e).__name__}: {e}")

    # Exit 0 if at least limit_up succeeded (core data), else 1
    return 0 if results["limit_up"] else 1


if __name__ == "__main__":
    sys.exit(main())
