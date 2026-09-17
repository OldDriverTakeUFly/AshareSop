"""大盘温度:合成序列的档位边界/最少历史闸/落库."""
from __future__ import annotations


def _conn(tmp_path):
    from stockhot.data_layer import market_db
    db = tmp_path / "t.db"
    market_db.init_db(db)
    return market_db.get_connection(db)


def _seed(conn, days: int = 300) -> None:
    import pandas as pd

    dates = [d.strftime("%Y%m%d")
             for d in pd.date_range("2022-01-04", periods=days, freq="D")]
    for code in ("000300.SH", "399006.SZ"):
        for i, d in enumerate(dates):
            close = 4000 + i * 2 if code == "000300.SH" else 2000 + i
            conn.execute(
                "INSERT INTO index_daily (ts_code,trade_date,close,amount,fetched_at) "
                "VALUES (?,?,?,?,0)", (code, d, float(close), 1e9))
    for i, d in enumerate(dates):
        conn.execute(
            "INSERT INTO daily_price (ts_code,trade_date,close,pre_close,amount,fetched_at) "
            "VALUES ('000001.SZ',?,?,?,?,0)",
            (d, 10.0 if i % 2 == 0 else 9.5, 10.0, 1e8))
        conn.execute(
            "INSERT INTO moneyflow (trade_date,ts_code,buy_elg_amount,sell_elg_amount,"
            "buy_lg_amount,sell_lg_amount,fetched_at) VALUES (?,?,?,?,?,?,0)",
            (d, "000001.SZ", 10.0, 5.0, 1.0, 1.0))  # 每日主力净 +5
        conn.execute(
            "INSERT INTO daily_basic (ts_code,trade_date,circ_mv,fetched_at) VALUES (?,?,?,0)",
            ("000001.SZ", d, 1000.0))
        # 大盘资金维走 L1 板块聚合沉淀表(market_flow_from_sectors)
        conn.execute(
            "INSERT INTO sector_moneyflow_daily "
            "(level,index_code,trade_date,main_net,mkt_cap,fetched_at) "
            "VALUES ('L1','801010.SI',?,5.0,1000.0,0)", (d,))
        dash = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        conn.execute(
            "INSERT INTO limit_pool (trade_date,ts_code,pool_kind,consecutive_boards,fetched_at) "
            "VALUES (?, '000001', 'limit_up', 2, 0)", (dash,))
    conn.commit()


def test_market_temperature_series(tmp_path):
    from davis_analyzer.thermometer import market_temp

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        df = market_temp.compute_market_history(conn, "20220101", "20991231")
        assert len(df) > 0
        # 最少 250 日历史闸:前 249 日无温度
        assert df["temperature"].isna().sum() >= 249
        valid = df.dropna(subset=["temperature"])
        assert len(valid) >= 1
        assert valid["temperature"].between(0, 100).all()
        assert set(valid["regime_label"]) <= {"冰点", "低温", "温和", "偏热", "过热"}
        n_db = conn.execute("SELECT COUNT(*) FROM thermometer_market").fetchone()[0]
        assert n_db == len(df)
    finally:
        conn.close()


def test_short_history_no_temperature(tmp_path):
    from davis_analyzer.thermometer import market_temp

    conn = _conn(tmp_path)
    try:
        _seed(conn, days=100)  # 不足 250 日
        df = market_temp.compute_market_history(conn, "20220101", "20991231")
        assert df["temperature"].isna().all()
    finally:
        conn.close()
