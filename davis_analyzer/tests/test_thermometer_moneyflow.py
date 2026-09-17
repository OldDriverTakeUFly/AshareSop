"""资金流自聚合:手造板块×日,Decimal 求和,断言到分毫."""
from __future__ import annotations


def _conn(tmp_path):
    from stockhot.data_layer import market_db
    db = tmp_path / "t.db"
    market_db.init_db(db)
    return market_db.get_connection(db)


def _seed(conn) -> None:
    # 成分:801010.SI(L1) ← 000001.SZ;850111.SI(L2,父级801010) ← 600000.SH
    conn.executemany(
        "INSERT INTO sw_index (index_code,name,level,is_pub,fetched_at) VALUES (?,?,?,'1',0)",
        [("801010.SI", "农林牧渔", "L1"), ("850111.SI", "种植业", "L2")])
    conn.executemany(
        "INSERT INTO sw_member (index_code,con_code,snapshot_date) VALUES (?,?,'20260913')",
        [("801010.SI", "000001.SZ"), ("850111.SI", "600000.SH")])
    # moneyflow:两日;金额单位万元
    conn.executemany(
        "INSERT INTO moneyflow (trade_date,ts_code,buy_elg_amount,sell_elg_amount,"
        "buy_lg_amount,sell_lg_amount,fetched_at) VALUES (?,?,?,?,?,?,0)",
        [("20220104", "000001.SZ", 100.0, 60.0, 30.0, 20.0),   # 主力净 +50
         ("20220104", "600000.SH", 10.0, 90.0, 5.0, 5.0),      # 主力净 -80
         ("20220105", "000001.SZ", 1.0, 2.0, 1.0, 2.0),        # 主力净 -2
         ("20220105", "600000.SH", 7.0, 3.0, 2.0, 1.0)])       # 主力净 +5
    # daily_basic 流通市值(万元):恒定 1000
    conn.executemany(
        "INSERT INTO daily_basic (ts_code,trade_date,circ_mv,fetched_at) VALUES (?,?,?,0)",
        [("000001.SZ", "20220104", 1000.0), ("000001.SZ", "20220105", 1000.0),
         ("600000.SH", "20220104", 1000.0), ("600000.SH", "20220105", 1000.0)])
    conn.commit()


def test_aggregate_sector_moneyflow(tmp_path):
    from davis_analyzer.thermometer import moneyflow_agg

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        r = moneyflow_agg.aggregate_sector_moneyflow(conn, "20220104", "20220105")
        assert r["rows"] == 4  # 2 (level,index_code) × 2 日
        row = conn.execute(
            "SELECT main_net, main_net_pct FROM sector_moneyflow_daily "
            "WHERE level='L1' AND index_code='801010.SI' AND trade_date='20220104'").fetchone()
        assert row[0] == 50.0            # 100-60+30-20
        assert abs(row[1] - 0.05) < 1e-9  # 50/1000
        # 幂等:重跑行数不增
        moneyflow_agg.aggregate_sector_moneyflow(conn, "20220104", "20220105")
        assert conn.execute(
            "SELECT COUNT(*) FROM sector_moneyflow_daily").fetchone()[0] == 4
    finally:
        conn.close()


def test_market_flow_series(tmp_path):
    from davis_analyzer.thermometer import moneyflow_agg

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        df = moneyflow_agg.market_flow_series(conn, "20220104", "20220105")
        d1 = df[df["trade_date"] == "20220104"].iloc[0]
        assert d1["main_net_sum"] == -30.0   # 50 + (-80)
        assert d1["circ_mv_sum"] == 2000.0
    finally:
        conn.close()


def test_market_flow_from_sectors(tmp_path):
    """大盘资金历史口径:L1 聚合求和,不依赖 daily_basic."""
    from davis_analyzer.thermometer import moneyflow_agg

    conn = _conn(tmp_path)
    try:
        conn.executemany(
            "INSERT INTO sector_moneyflow_daily "
            "(level,index_code,trade_date,main_net,mkt_cap,fetched_at) VALUES (?,?,?,?,?,0)",
            [("L1", "801010.SI", "20220104", 50.0, 1000.0),
             ("L1", "801080.SI", "20220104", -30.0, 3000.0),
             ("L2", "850111.SI", "20220104", -80.0, 1000.0),  # L2 不计入大盘
             ("L1", "801010.SI", "20220105", -2.0, 1000.0)])
        conn.commit()
        df = moneyflow_agg.market_flow_from_sectors(conn, "20220104", "20220105")
        d1 = df[df["trade_date"] == "20220104"].iloc[0]
        assert d1["main_net_sum"] == 20.0    # 50 − 30(L2 排除)
        assert d1["circ_mv_sum"] == 4000.0
    finally:
        conn.close()
