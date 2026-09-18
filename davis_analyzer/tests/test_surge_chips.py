"""chips.cyq_perf 拉取入库与回退测试（内存库+FakePro,不发真请求）."""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from davis_analyzer.surge import chips, db


class FakePro:
    def __init__(self, data: dict[str, pd.DataFrame]):
        self.data = data
        self.calls: list[str] = []

    def cyq_perf(self, ts_code=None, start_date=None, end_date=None, trade_date=None):
        self.calls.append(trade_date or ts_code)
        return self.data.get(trade_date or "", pd.DataFrame())


@pytest.fixture()
def mem_conn():
    conn = sqlite3.connect(":memory:")
    db.ensure_tables(conn)
    yield conn
    conn.close()


def _df(day: str, winner: float, n: int = 2000) -> pd.DataFrame:
    return pd.DataFrame([{
        "ts_code": f"{i:06d}.SZ", "trade_date": day, "his_low": 5.0, "his_high": 15.0,
        "cost_5pct": 9.0, "cost_15pct": 9.5, "cost_50pct": 10.0,
        "cost_85pct": 10.5, "cost_95pct": 11.0,
        "weight_avg": 10.2, "winner_rate": winner} for i in range(n)])


def test_ensure_cyq_fresh_day(mem_conn):
    pro = FakePro({"20260918": _df("20260918", 85.0)})
    assert chips.ensure_cyq(mem_conn, pro, "20260918") == "20260918"
    n = mem_conn.execute(
        "SELECT COUNT(*) FROM cyq_perf_cache WHERE trade_date='20260918'").fetchone()[0]
    assert n == 2000


def test_ensure_cyq_falls_back_to_recent(mem_conn):
    # 先落一个完整历史日,当日拉不到 → 回退
    pro0 = FakePro({"20260917": _df("20260917", 60.0)})
    assert chips.ensure_cyq(mem_conn, pro0, "20260917") == "20260917"
    pro = FakePro({})  # 当日无数据
    assert chips.ensure_cyq(mem_conn, pro, "20260918") == "20260917"


def test_ensure_cyq_no_data_returns_empty(mem_conn):
    pro = FakePro({})
    assert chips.ensure_cyq(mem_conn, pro, "20260918") == ""


def test_ensure_cyq_idempotent(mem_conn):
    pro = FakePro({"20260918": _df("20260918", 85.0)})
    chips.ensure_cyq(mem_conn, pro, "20260918")
    chips.ensure_cyq(mem_conn, pro, "20260918")  # 已有日期不再插入
    assert len(pro.calls) == 1  # 第二次未触网


def test_read_cyq_lookback(mem_conn):
    for d, w in (("20260910", 50.0), ("20260911", 55.0), ("20260918", 60.0)):
        df = _df(d, w)
        df["ts_code"] = "000001.SZ"
        chips._insert(mem_conn, df)
    got = chips.read_cyq(mem_conn, ["000001.SZ"], "20260918", lookback=2)
    assert set(got["trade_date"]) == {"20260911", "20260918"}
