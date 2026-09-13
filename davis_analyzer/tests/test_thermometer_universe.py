"""universe 刷新与读取(网关 mock,临时库注入)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd


def _gw_index_classify(level: str, src: str = "SW2021") -> pd.DataFrame:
    # L1=801xxx 段,L2=850xxx 段(真实代码段不重叠)
    if level == "L1":
        return pd.DataFrame({
            "index_code": ["801010.SI", "801080.SI"],
            "industry_name": ["农林牧渔", "电子"],
            "level": ["L1", "L1"],
            "parent_code": [None, None],
            "is_pub": ["1", "1"],
        })
    return pd.DataFrame({
        "index_code": ["850111.SI"],
        "industry_name": ["种植业"],
        "level": ["L2"],
        "parent_code": ["801010.SI"],
        "is_pub": ["1"],
    })


def _make_gw() -> MagicMock:
    gw = MagicMock()
    gw.call.side_effect = lambda api, **kw: {
        "index_classify": lambda: _gw_index_classify(kw["level"]),
        "index_member": lambda: pd.DataFrame({
            "index_code": [kw["index_code"]] * 2,
            "con_code": ["000001.SZ", "600000.SH"],
            "in_date": ["20220104", "20220104"], "out_date": [None, None],
            "is_new": ["1", "1"]}),
    }[api]()
    return gw


def _conn(tmp_path):
    from stockhot.data_layer import market_db
    db = tmp_path / "t.db"
    market_db.init_db(db)
    return market_db.get_connection(db)


def test_refresh_and_load(tmp_path):
    from davis_analyzer.thermometer import universe

    conn = _conn(tmp_path)
    try:
        gw = _make_gw()
        n = universe.refresh_sw_index(conn, gw)
        assert n == 3  # L1 两条 + L2 一条
        m = universe.refresh_sw_member(conn, gw, "20260913")
        assert m == 6  # 3 个指数 × 2 成分
        uni = universe.load_universe(conn, "L1")
        assert list(uni["name"]) == ["农林牧渔", "电子"]
        mem = universe.load_members(conn)
        assert set(mem["con_code"]) == {"000001.SZ", "600000.SH"}
        assert len(mem) == 6  # L1(2 指数)+L2(1 指数)各带成分
    finally:
        conn.close()


def test_load_members_latest_snapshot(tmp_path):
    from davis_analyzer.thermometer import universe

    conn = _conn(tmp_path)
    try:
        gw = _make_gw()
        universe.refresh_sw_index(conn, gw)
        for snap in ("20260901", "20260913"):
            universe.refresh_sw_member(conn, gw, snap)
        mem = universe.load_members(conn)
        # 只取每 index_code 最新快照,旧快照不混入
        assert (mem["snapshot_date"] == "20260913").all()
    finally:
        conn.close()


def test_refresh_ths(tmp_path):
    from davis_analyzer.thermometer import universe

    conn = _conn(tmp_path)
    try:
        gw = MagicMock()
        gw.call.side_effect = lambda api, **kw: {
            "ths_index": lambda: pd.DataFrame({
                "ts_code": ["885900.TI"], "name": ["AI"],
                "count": [25], "exchange": ["A"], "list_date": ["20200102"],
                "type": ["N"]}),
            "ths_member": lambda: pd.DataFrame({
                "ts_code": ["885900.TI"] * 2,
                "con_code": ["000001.SZ", "300750.SZ"],
                "con_name": ["平安银行", "宁德时代"]}),
        }[api]()
        n = universe.refresh_ths_index(conn, gw)
        assert n == 1
        m = universe.refresh_ths_member(conn, gw, "20260913")
        assert m == 2
    finally:
        conn.close()
