"""温度合成:边界/单调/涨停密度join/衍生读数."""
from __future__ import annotations

import pandas as pd

_N_DAYS = 70  # 60 日趋势窗预热 + 10 个温度日(其中后 5 日有 delta_temp5)


def _dates() -> list[str]:
    return [d.strftime("%Y%m%d")
            for d in pd.date_range("2022-01-04", periods=_N_DAYS, freq="D")]


def _conn(tmp_path):
    from stockhot.data_layer import market_db
    db = tmp_path / "t.db"
    market_db.init_db(db)
    return market_db.get_connection(db)


def _seed(conn) -> None:
    dates = _dates()
    conn.executemany(
        "INSERT INTO sw_index (index_code,name,level,is_pub,fetched_at) VALUES (?,?,?,'1',0)",
        [("801010.SI", "农林牧渔", "L1"), ("801011.SI", "电子", "L1")])
    conn.executemany(
        "INSERT INTO sw_member (index_code,con_code,snapshot_date) VALUES (?,?,'20260913')",
        [("801010.SI", "000001.SZ"), ("801010.SI", "600000.SH"),
         ("801011.SI", "300750.SZ")])
    # 日历(daily_price 一行/日)
    conn.executemany(
        "INSERT INTO daily_price (ts_code,trade_date,close) VALUES ('000001.SZ',?,10)",
        [(d,) for d in dates])
    # 行情:801010 稳涨放量,801011 稳跌恒量
    rows = []
    for j, d in enumerate(dates):
        rows.append(("801010.SI", d, 100 * 1.01 ** j, 1e6 * (1 + 0.1 * j)))
        rows.append(("801011.SI", d, 100 * 0.99 ** j, 1e6))
    conn.executemany(
        "INSERT INTO sw_daily (ts_code,trade_date,close,amount,fetched_at) VALUES (?,?,?,?,0)",
        rows)
    # 资金流:801010 每日 +0.5%,801011 −0.5%
    conn.executemany(
        "INSERT INTO sector_moneyflow_daily (level,index_code,trade_date,main_net_pct,fetched_at) "
        "VALUES ('L1',?,?,?,0)",
        [(r[0], r[1], 0.005 if r[0] == "801010.SI" else -0.005) for r in rows])
    # 涨停:801010 的 000001.SZ 每日涨停(dash 日期,无后缀代码)
    conn.executemany(
        "INSERT INTO limit_pool (trade_date,ts_code,pool_kind,fetched_at) "
        "VALUES (?, '000001', 'limit_up', 0)",
        [(f"{d[:4]}-{d[4:6]}-{d[6:]}",) for d in dates])
    conn.commit()


def _read_thermo(conn) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM thermometer_sector ORDER BY trade_date, index_code", conn)


def test_temperature_bounded_and_monotonic(tmp_path):
    from davis_analyzer.thermometer import scoring

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        r = scoring.score_history(conn, "20220101", "20991231")
        assert r["rows"] > 0
        df = _read_thermo(conn)
        assert df["temperature"].between(0, 100).all()
        last = df[df["trade_date"] == df["trade_date"].max()]
        hot = last[last["index_code"] == "801010.SI"]["temperature"].iloc[0]
        cold = last[last["index_code"] == "801011.SI"]["temperature"].iloc[0]
        assert hot > cold
        # 涨停密度入 panel:801010 = 1/2 成分,801011 = 0
        panel = scoring.build_panel(conn, "L1", df["trade_date"].max(), df["trade_date"].max())
        assert panel["limit_ratio"].max() > 0.4
    finally:
        conn.close()


def test_hot_streak_and_delta(tmp_path):
    from davis_analyzer.thermometer import scoring

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        scoring.score_history(conn, "20220101", "20991231")
        df = _read_thermo(conn)
        assert df["delta_temp5"].notna().sum() > 0
        assert df["hot_streak"].fillna(0).ge(0).all()
        # 热板块(恒 100 分位)最后一天 streak = 有温度的天数
        hot_last = df[(df["index_code"] == "801010.SI")
                      & (df["trade_date"] == df["trade_date"].max())]
        assert int(hot_last["hot_streak"].iloc[0]) >= 5
    finally:
        conn.close()


def test_idempotent_rescore(tmp_path):
    from davis_analyzer.thermometer import scoring

    conn = _conn(tmp_path)
    try:
        _seed(conn)
        scoring.score_history(conn, "20220101", "20991231")
        n1 = conn.execute("SELECT COUNT(*) FROM thermometer_sector").fetchone()[0]
        scoring.score_history(conn, "20220101", "20991231")
        n2 = conn.execute("SELECT COUNT(*) FROM thermometer_sector").fetchone()[0]
        assert n1 == n2  # 全量覆写幂等
    finally:
        conn.close()
