"""sw_daily/ths_daily 回补(网关 mock + 临时库)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd


def _sw_df(d: str) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["801010.SI", "850111.SI"], "trade_date": [d, d],
        "open": [1.0, 2.0], "high": [1.5, 2.5], "low": [0.9, 1.9],
        "close": [1.2, 2.2], "vol": [100.0, 200.0], "amount": [1e6, 2e6],
        "pct_change": [1.0, -1.0],
    })


def _conn(tmp_path):
    from stockhot.data_layer import market_db
    db = tmp_path / "t.db"
    market_db.init_db(db)
    return market_db.get_connection(db)


def _seed_calendar(conn) -> None:
    # daily_price 提供交易日历:20220104/20220105 两天
    for d in ("20220104", "20220105"):
        conn.execute(
            "INSERT INTO daily_price (ts_code,trade_date,close) VALUES ('000001.SZ',?,100)",
            (d,))
    conn.commit()


def test_backfill_sw_daily_idempotent(tmp_path):
    from davis_analyzer.thermometer import data

    conn = _conn(tmp_path)
    try:
        _seed_calendar(conn)
        gw = MagicMock()
        gw.call.side_effect = lambda api, **kw: _sw_df(kw["trade_date"])
        r1 = data.backfill_sw_daily(conn, gw, "20220104", "20220105", min_rows=2)
        assert r1["days_done"] == 2 and r1["rows_written"] == 4
        r2 = data.backfill_sw_daily(conn, gw, "20220104", "20220105", min_rows=2)
        assert r2["days_skipped"] == 2 and r2["rows_written"] == 0  # 断点续跑
        n = conn.execute("SELECT COUNT(*) FROM sw_daily").fetchone()[0]
        assert n == 4
    finally:
        conn.close()


def test_update_incremental(tmp_path):
    from davis_analyzer.thermometer import data

    conn = _conn(tmp_path)
    try:
        _seed_calendar(conn)
        gw = MagicMock()
        gw.call.side_effect = lambda api, **kw: _sw_df(kw["trade_date"])
        r = data.update_sw_daily_incremental(conn, gw)
        assert r["days_done"] == 1  # 只补最新交易日 20220105
        assert conn.execute(
            "SELECT COUNT(*) FROM sw_daily WHERE trade_date='20220105'").fetchone()[0] == 2
    finally:
        conn.close()


def test_backfill_daily_basic_circ_mv(tmp_path):
    """circ_mv 回补:UPSERT 不覆盖已有列;幂等."""
    from davis_analyzer.thermometer import data

    conn = _conn(tmp_path)
    try:
        _seed_calendar(conn)
        conn.execute(
            "INSERT INTO daily_basic (ts_code,trade_date,pe_ttm,circ_mv,fetched_at) "
            "VALUES ('000001.SZ','20220104',8.5,NULL,0)")
        conn.commit()
        gw = MagicMock()
        gw.call.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ", "600000.SH"],
            "trade_date": ["20220104", "20220104"],
            "circ_mv": [1000.0, 2000.0],
        })
        r = data.backfill_daily_basic_circ_mv(conn, gw, "20220104", "20220104")
        assert r["days"] == 1 and r["rows"] == 2
        row = conn.execute(
            "SELECT pe_ttm, circ_mv FROM daily_basic "
            "WHERE ts_code='000001.SZ' AND trade_date='20220104'").fetchone()
        assert row == (8.5, 1000.0)  # pe 保留,circ_mv 补上
        assert conn.execute(
            "SELECT circ_mv FROM daily_basic WHERE ts_code='600000.SH' "
            "AND trade_date='20220104'").fetchone()[0] == 2000.0
    finally:
        conn.close()


def test_backfill_ths_daily_by_code(tmp_path):
    from davis_analyzer.thermometer import data

    conn = _conn(tmp_path)
    try:
        conn.execute(
            "INSERT INTO ths_index (ts_code,name,fetched_at) VALUES ('885900.TI','AI',0)")
        conn.commit()
        gw = MagicMock()
        gw.call.side_effect = lambda api, **kw: pd.DataFrame({
            "ts_code": ["885900.TI", "885900.TI"],
            "trade_date": ["20220104", "20220105"],
            "close": [100.0, 101.0], "pct_change": [1.0, 1.0],
        })
        r = data.backfill_ths_daily(conn, gw)
        assert r["codes_done"] == 1
        assert conn.execute("SELECT COUNT(*) FROM ths_daily").fetchone()[0] == 2
        # 幂等:重跑跳过已有码
        r2 = data.backfill_ths_daily(conn, gw)
        assert r2["codes_done"] == 0
    finally:
        conn.close()
