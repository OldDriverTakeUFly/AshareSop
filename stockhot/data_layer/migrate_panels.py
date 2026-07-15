#!/usr/bin/env python
"""迁移 stockhot.db 的 daily_data JSON blob → market_data.db 结构化表.

把盘面采集数据（涨停池/龙虎榜/资金流/指数技术面）从 JSON blob 解析为结构化表，
让 after-hours-review 等消费方可以直接用 SQL 查询，无需解析 JSON。

迁移映射：
    limit_up_pool  ─┐
    broken_pool    ─┼─→ limit_pool (pool_kind 鉴别列)
    limit_down_pool─┘
    dragon_tiger_detail → dragon_tiger
    fund_flow_sector     → fund_flow_sector
    fund_flow_market     → fund_flow_market
    index_technical      → index_technical (reasons/signals 保留 JSON)

保留 stockhot.db 的 daily_data 表不删（双写期，降级回退用）。

用法：
    PYTHONPATH=. .venv/bin/python stockhot/data_layer/migrate_panels.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time

# stockhot.db 路径
from stockhot.core.config import DB_PATH as STOCKHOT_DB
from stockhot.data_layer.market_db import MARKET_DB_PATH, init_db

# pool_kind 映射：daily_data 的 data_type → limit_pool 的 pool_kind
POOL_KIND_MAP = {
    "limit_up_pool": "limit_up",
    "broken_pool": "broken",
    "limit_down_pool": "limit_down",
}


def _safe_float(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _safe_int(v, default=None):
    try:
        return int(float(v)) if v is not None else default
    except (TypeError, ValueError):
        return default


def migrate_pools(src_conn, dst_conn) -> int:
    """迁移涨停/炸板/跌停池 → limit_pool."""
    total = 0
    for data_type, pool_kind in POOL_KIND_MAP.items():
        rows = src_conn.execute(
            "SELECT trade_date, data_json FROM daily_data WHERE data_type=?",
            (data_type,),
        ).fetchall()
        records = []
        for trade_date, data_json in rows:
            try:
                items = json.loads(data_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                # 兼容 code/ts_code 两种字段名
                code = item.get("code") or item.get("ts_code") or ""
                records.append((
                    trade_date, code, pool_kind,
                    item.get("name"), item.get("sector"),
                    _safe_float(item.get("change_pct")),
                    _safe_float(item.get("seal_amount")),
                    _safe_int(item.get("consecutive_boards") or item.get("max_board")),
                    _safe_int(item.get("broken_count")),
                    item.get("first_seal_time"),
                    item.get("last_seal_time"),
                    _safe_float(item.get("turnover_rate")),
                ))
        if records:
            dst_conn.executemany(
                "INSERT OR REPLACE INTO limit_pool "
                "(trade_date, ts_code, pool_kind, name, sector, change_pct, "
                "seal_amount, consecutive_boards, broken_count, "
                "first_seal_time, last_seal_time, turnover_rate) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                records,
            )
            total += len(records)
            print(f"  {data_type} → limit_pool[{pool_kind}]: {len(records)} 行")
    return total


def migrate_dragon_tiger(src_conn, dst_conn) -> int:
    """迁移龙虎榜 → dragon_tiger."""
    rows = src_conn.execute(
        "SELECT trade_date, data_json FROM daily_data WHERE data_type='dragon_tiger_detail'"
    ).fetchall()
    records = []
    for trade_date, data_json in rows:
        try:
            items = json.loads(data_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            code = item.get("code") or item.get("ts_code") or ""
            records.append((
                trade_date, code,
                item.get("name"), item.get("reason"),
                _safe_float(item.get("close_price") or item.get("close")),
                _safe_float(item.get("change_pct")),
                _safe_float(item.get("net_buy_amount") or item.get("net_buy")),
                _safe_float(item.get("buy_amount")),
                _safe_float(item.get("sell_amount")),
                item.get("list_date"),
            ))
    if records:
        dst_conn.executemany(
            "INSERT OR REPLACE INTO dragon_tiger "
            "(trade_date, ts_code, name, reason, close, change_pct, "
            "net_buy, buy_amount, sell_amount, list_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            records,
        )
        print(f"  dragon_tiger_detail → dragon_tiger: {len(records)} 行")
    return len(records)


def migrate_fund_flow(src_conn, dst_conn) -> tuple[int, int]:
    """迁移板块资金流 + 大盘资金流."""
    # 板块
    rows = src_conn.execute(
        "SELECT trade_date, data_json FROM daily_data WHERE data_type='fund_flow_sector'"
    ).fetchall()
    sec_records = []
    for trade_date, data_json in rows:
        try:
            items = json.loads(data_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            sec_records.append((
                trade_date,
                item.get("name") or item.get("sector_name") or "",
                _safe_float(item.get("change_pct")),
                _safe_float(item.get("main_net")),
                _safe_float(item.get("main_pct")),
                _safe_float(item.get("huge_net")),
                _safe_float(item.get("large_net")),
                _safe_float(item.get("medium_net")),
                _safe_float(item.get("small_net")),
            ))
    if sec_records:
        dst_conn.executemany(
            "INSERT OR REPLACE INTO fund_flow_sector "
            "(trade_date, sector_name, change_pct, main_net, main_pct, "
            "huge_net, large_net, medium_net, small_net) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            sec_records,
        )
        print(f"  fund_flow_sector → fund_flow_sector: {len(sec_records)} 行")

    # 大盘
    rows = src_conn.execute(
        "SELECT trade_date, data_json FROM daily_data WHERE data_type='fund_flow_market'"
    ).fetchall()
    mkt_records = []
    for trade_date, data_json in rows:
        try:
            items = json.loads(data_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(items, list):
            continue
        for seq, item in enumerate(items):
            mkt_records.append((
                trade_date, seq,
                _safe_float(item.get("main_net")),
                _safe_float(item.get("main_pct")),
                _safe_float(item.get("huge_net")),
                _safe_float(item.get("large_net")),
                _safe_float(item.get("medium_net")),
                _safe_float(item.get("small_net")),
            ))
    if mkt_records:
        dst_conn.executemany(
            "INSERT OR REPLACE INTO fund_flow_market "
            "(trade_date, seq, main_net, main_pct, huge_net, large_net, medium_net, small_net) "
            "VALUES (?,?,?,?,?,?,?,?)",
            mkt_records,
        )
        print(f"  fund_flow_market → fund_flow_market: {len(mkt_records)} 行")
    return len(sec_records), len(mkt_records)


def migrate_index_technical(src_conn, dst_conn) -> int:
    """迁移指数技术面 → index_technical (reasons/signals 保留 JSON)."""
    rows = src_conn.execute(
        "SELECT trade_date, data_json FROM daily_data WHERE data_type='index_technical'"
    ).fetchall()
    records = []
    for trade_date, data_json in rows:
        try:
            data = json.loads(data_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        indices = data.get("indices", {})
        for ts_code, info in indices.items():
            records.append((
                trade_date, ts_code,
                _safe_float(info.get("close")),
                _safe_float(info.get("pct_chg")),
                _safe_float(info.get("technical_score")),
                info.get("technical_state"),
                info.get("stage"),
                _safe_int(info.get("stage_confidence")),
                info.get("expected_action"),
                json.dumps(info.get("reasons", []), ensure_ascii=False) if info.get("reasons") else None,
                json.dumps(info.get("signals_detail"), ensure_ascii=False) if info.get("signals_detail") else None,
            ))
    if records:
        dst_conn.executemany(
            "INSERT OR REPLACE INTO index_technical "
            "(trade_date, ts_code, close, pct_chg, technical_score, technical_state, "
            "stage, stage_confidence, expected_action, reasons_json, signals_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            records,
        )
        print(f"  index_technical → index_technical: {len(records)} 行")
    return len(records)


def migrate(dry_run: bool = False) -> int:
    print(f"═ 盘面 JSON → 结构化表迁移 ════════════════════════════════")
    print(f"源: {STOCKHOT_DB}")
    print(f"目标: {MARKET_DB_PATH}")

    init_db()

    if dry_run:
        print("\n[DRY RUN] 将迁移以下 data_type：")
        with sqlite3.connect(str(STOCKHOT_DB)) as conn:
            for dt in ["limit_up_pool", "broken_pool", "limit_down_pool",
                        "dragon_tiger_detail", "fund_flow_sector",
                        "fund_flow_market", "index_technical"]:
                n = conn.execute(
                    "SELECT COUNT(*) FROM daily_data WHERE data_type=?", (dt,)
                ).fetchone()[0]
                print(f"  {dt}: {n} 天的数据")
        return 0

    t0 = time.time()
    with sqlite3.connect(str(STOCKHOT_DB)) as src, sqlite3.connect(str(MARKET_DB_PATH)) as dst:
        print("\n开始迁移...")
        n_pools = migrate_pools(src, dst)
        n_dt = migrate_dragon_tiger(src, dst)
        n_sec, n_mkt = migrate_fund_flow(src, dst)
        n_it = migrate_index_technical(src, dst)
        dst.commit()

    elapsed = time.time() - t0
    total = n_pools + n_dt + n_sec + n_mkt + n_it
    print(f"\n✓ 迁移完成，共 {total} 行 ({elapsed:.1f}s)")
    print(f"  limit_pool: {n_pools} | dragon_tiger: {n_dt} | "
          f"fund_flow_sector: {n_sec} | fund_flow_market: {n_mkt} | index_technical: {n_it}")

    # 验证
    print("\n═ 迁移后验证 ════════════════════════════════════════════")
    with sqlite3.connect(str(MARKET_DB_PATH)) as conn:
        for table in ["limit_pool", "dragon_tiger", "fund_flow_sector", "fund_flow_market", "index_technical"]:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            dates = conn.execute(f"SELECT COUNT(DISTINCT trade_date) FROM {table}").fetchone()[0]
            print(f"  ✓ {table}: {n} 行，{dates} 个交易日")

    return 0


def sync_single_day(trade_date: str) -> int:
    """同步单日的 daily_data JSON → market_data.db 结构化表（增量）.

    供 run_daily_scan.py 在采集后调用，让结构化表与 JSON blob 保持同步，
    使 after-hours-review 等消费方能直接读结构化表（repo.get_limit_pool 等）。

    与 ``migrate()`` 的区别：只处理指定日期，不扫全表。用 INSERT OR REPLACE
    保证幂等（同一天重跑不会产生重复行）。

    返回同步的行数（0 表示该日无数据或同步失败）。
    """
    init_db()
    total = 0
    try:
        with sqlite3.connect(str(STOCKHOT_DB)) as src, sqlite3.connect(str(MARKET_DB_PATH)) as dst:
            # 先删除目标库该日的旧数据（避免池子成员变化的脏数据残留）
            for table in ["limit_pool", "dragon_tiger", "fund_flow_sector",
                          "fund_flow_market", "index_technical"]:
                dst.execute(f"DELETE FROM {table} WHERE trade_date=?", (trade_date,))

            # 复用按全表迁移的函数，但通过临时限定只读该日数据
            # —— 直接调 migrate_pools 等，它们会 INSERT OR REPLACE 全部日期，
            #    但因我们已 DELETE 该日，且其他日期已有数据会被 IGNORE，开销可接受。
            # 更精确的做法：内联按单日解析。这里用精确单日解析避免全表扫描。
            _sync_day_pools(src, dst, trade_date)
            _sync_day_dragon_tiger(src, dst, trade_date)
            _sync_day_fund_flow(src, dst, trade_date)
            _sync_day_index_technical(src, dst, trade_date)
            _sync_day_volatility(src, dst, trade_date)

            for table in ["limit_pool", "dragon_tiger", "fund_flow_sector",
                          "fund_flow_market", "index_technical",
                          "daily_volatility_index", "daily_volatility_market"]:
                total += dst.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE trade_date=?", (trade_date,)
                ).fetchone()[0]
            dst.commit()
    except Exception as e:
        print(f"[sync_single_day] {trade_date} 同步失败: {e}")
        return 0
    return total


def _sync_day_pools(src, dst, trade_date: str) -> None:
    """同步单日涨停/炸板/跌停池."""
    for data_type, pool_kind in POOL_KIND_MAP.items():
        row = src.execute(
            "SELECT data_json FROM daily_data WHERE data_type=? AND trade_date=?",
            (data_type, trade_date),
        ).fetchone()
        if not row:
            continue
        try:
            items = json.loads(row[0])
        except json.JSONDecodeError:
            continue
        if not isinstance(items, list):
            continue
        records = []
        for item in items:
            code = item.get("code") or item.get("ts_code") or ""
            records.append((
                trade_date, code, pool_kind,
                item.get("name"), item.get("sector"),
                _safe_float(item.get("change_pct")),
                _safe_float(item.get("seal_amount")),
                _safe_int(item.get("consecutive_boards") or item.get("max_board")),
                _safe_int(item.get("broken_count")),
                item.get("first_seal_time"), item.get("last_seal_time"),
                _safe_float(item.get("turnover_rate")),
            ))
        if records:
            dst.executemany(
                "INSERT OR REPLACE INTO limit_pool "
                "(trade_date, ts_code, pool_kind, name, sector, change_pct, "
                "seal_amount, consecutive_boards, broken_count, "
                "first_seal_time, last_seal_time, turnover_rate) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", records)


def _sync_day_dragon_tiger(src, dst, trade_date: str) -> None:
    """同步单日龙虎榜."""
    row = src.execute(
        "SELECT data_json FROM daily_data WHERE data_type='dragon_tiger_detail' AND trade_date=?",
        (trade_date,),
    ).fetchone()
    if not row:
        return
    try:
        items = json.loads(row[0])
    except json.JSONDecodeError:
        return
    if not isinstance(items, list):
        return
    records = []
    for item in items:
        code = item.get("code") or item.get("ts_code") or ""
        records.append((
            trade_date, code, item.get("name"), item.get("reason"),
            _safe_float(item.get("close_price") or item.get("close")),
            _safe_float(item.get("change_pct")),
            _safe_float(item.get("net_buy_amount") or item.get("net_buy")),
            _safe_float(item.get("buy_amount")), _safe_float(item.get("sell_amount")),
            item.get("list_date"),
        ))
    if records:
        dst.executemany(
            "INSERT OR REPLACE INTO dragon_tiger "
            "(trade_date, ts_code, name, reason, close, change_pct, "
            "net_buy, buy_amount, sell_amount, list_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", records)


def _sync_day_fund_flow(src, dst, trade_date: str) -> None:
    """同步单日板块+大盘资金流."""
    for data_type, table, pk_extra in [
        ("fund_flow_sector", "fund_flow_sector", "sector_name"),
        ("fund_flow_market", "fund_flow_market", "seq"),
    ]:
        row = src.execute(
            f"SELECT data_json FROM daily_data WHERE data_type=? AND trade_date=?",
            (data_type, trade_date),
        ).fetchone()
        if not row:
            continue
        try:
            items = json.loads(row[0])
        except json.JSONDecodeError:
            continue
        if not isinstance(items, list):
            continue
        records = []
        for seq, item in enumerate(items):
            if table == "fund_flow_sector":
                records.append((
                    trade_date,
                    item.get("name") or item.get("sector_name") or "",
                    _safe_float(item.get("change_pct")), _safe_float(item.get("main_net")),
                    _safe_float(item.get("main_pct")), _safe_float(item.get("huge_net")),
                    _safe_float(item.get("large_net")), _safe_float(item.get("medium_net")),
                    _safe_float(item.get("small_net")),
                ))
            else:
                records.append((
                    trade_date, seq,
                    _safe_float(item.get("main_net")), _safe_float(item.get("main_pct")),
                    _safe_float(item.get("huge_net")), _safe_float(item.get("large_net")),
                    _safe_float(item.get("medium_net")), _safe_float(item.get("small_net")),
                ))
        if records:
            if table == "fund_flow_sector":
                dst.executemany(
                    "INSERT OR REPLACE INTO fund_flow_sector "
                    "(trade_date, sector_name, change_pct, main_net, main_pct, "
                    "huge_net, large_net, medium_net, small_net) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", records)
            else:
                dst.executemany(
                    "INSERT OR REPLACE INTO fund_flow_market "
                    "(trade_date, seq, main_net, main_pct, huge_net, large_net, "
                    "medium_net, small_net) VALUES (?,?,?,?,?,?,?,?)", records)


def _sync_day_index_technical(src, dst, trade_date: str) -> None:
    """同步单日指数技术面."""
    row = src.execute(
        "SELECT data_json FROM daily_data WHERE data_type='index_technical' AND trade_date=?",
        (trade_date,),
    ).fetchone()
    if not row:
        return
    try:
        data = json.loads(row[0])
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return
    indices = data.get("indices", {})
    records = []
    for ts_code, info in indices.items():
        records.append((
            trade_date, ts_code,
            _safe_float(info.get("close")), _safe_float(info.get("pct_chg")),
            _safe_float(info.get("technical_score")), info.get("technical_state"),
            info.get("stage"), _safe_int(info.get("stage_confidence")),
            info.get("expected_action"),
            json.dumps(info.get("reasons", []), ensure_ascii=False) if info.get("reasons") else None,
            json.dumps(info.get("signals_detail"), ensure_ascii=False) if info.get("signals_detail") else None,
        ))
    if records:
        dst.executemany(
            "INSERT OR REPLACE INTO index_technical "
            "(trade_date, ts_code, close, pct_chg, technical_score, technical_state, "
            "stage, stage_confidence, expected_action, reasons_json, signals_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", records)


def _sync_day_volatility(src, dst, trade_date: str) -> None:
    """同步单日波动率（indices → daily_volatility_index，market+limit_behavior → daily_volatility_market）."""
    row = src.execute(
        "SELECT data_json FROM daily_data WHERE data_type='volatility' AND trade_date=?",
        (trade_date,),
    ).fetchone()
    if not row:
        return
    try:
        data = json.loads(row[0])
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return

    ts = _safe_float  # 复用 helper（注意 _safe_float 返回 float，这里用 time.time）
    import time as _t
    now = _t.time()

    # 指数层
    indices = data.get("indices", {})
    idx_records = []
    for ts_code, info in indices.items():
        if not isinstance(info, dict):
            continue
        idx_records.append((
            trade_date, ts_code, info.get("name"),
            _safe_float(info.get("close")), _safe_float(info.get("rv20")),
            _safe_float(info.get("rv60")), _safe_float(info.get("rv20_pct")),
            _safe_float(info.get("rv60_pct")), info.get("panic_level"), now,
        ))
    # 先清旧再写
    dst.execute("DELETE FROM daily_volatility_index WHERE trade_date=?", (trade_date,))
    if idx_records:
        dst.executemany(
            "INSERT OR REPLACE INTO daily_volatility_index "
            "(trade_date, ts_code, name, close, rv20, rv60, rv20_pct, rv60_pct, panic_level, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", idx_records)

    # 市场层
    market = data.get("market", {})
    limit_bh = data.get("limit_behavior", {})
    dst.execute("DELETE FROM daily_volatility_market WHERE trade_date=?", (trade_date,))
    if market or limit_bh:
        dst.execute(
            "INSERT INTO daily_volatility_market "
            "(trade_date, ivix_current, ivix_pct, ivix_panic_level, vr_ratio, "
            "limit_up, broken, limit_down, up_down_ratio, broken_rate, "
            "behavior_signal, summary, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                trade_date,
                _safe_float(market.get("ivix_current")),
                _safe_float(market.get("ivix_pct")),
                market.get("ivix_panic_level"),
                _safe_float(market.get("vr_ratio")),
                _safe_int(limit_bh.get("limit_up")),
                _safe_int(limit_bh.get("broken")),
                _safe_int(limit_bh.get("limit_down")),
                _safe_float(limit_bh.get("up_down_ratio")),
                _safe_float(limit_bh.get("broken_rate")),
                limit_bh.get("behavior_signal"),
                data.get("summary"),
                now,
            ),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate panel JSON blobs to structured tables")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sys.exit(migrate(dry_run=args.dry_run))
