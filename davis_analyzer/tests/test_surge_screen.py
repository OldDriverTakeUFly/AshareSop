"""screen.run_day 集成测试: 内存库+合成日线+FakePro(cyq)+跳过巨潮."""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from davis_analyzer.systems.surge import db, screen
from davis_analyzer.tests.test_surge_pattern import _base_series, _mk

_DATES = [f"2026{m:02d}{d:02d}"
          for m in range(1, 9) for d in range(1, 29)]  # 伪日期序列,仅保证有序
_DAY = _DATES[127]  # 128行合成序列的末日(突破日)


class FakePro:
    def __init__(self, day: str):
        self.day = day

    def cyq_perf(self, ts_code=None, start_date=None, end_date=None, trade_date=None):
        n = 2500
        return pd.DataFrame([{
            "ts_code": f"{i:06d}.SZ" if i else "000001.SZ",
            "trade_date": trade_date, "his_low": 5.0, "his_high": 15.0,
            "cost_5pct": 9.8, "cost_15pct": 10.0, "cost_50pct": 10.2,
            "cost_85pct": 10.5, "cost_95pct": 11.0,
            "weight_avg": 10.3, "winner_rate": 55.0} for i in range(n)])


@pytest.fixture()
def setup_db():
    conn = sqlite3.connect(":memory:")
    for ddl in (
        "CREATE TABLE daily_price (ts_code TEXT, trade_date TEXT, open REAL,"
        " high REAL, low REAL, close REAL, pre_close REAL, pct_chg REAL,"
        " vol REAL, amount REAL, adj_factor REAL,"
        " PRIMARY KEY(ts_code, trade_date))",
        "CREATE TABLE stock_basic (ts_code TEXT PRIMARY KEY, name TEXT,"
        " industry TEXT, market TEXT, list_date TEXT)",
        "CREATE TABLE moneyflow (trade_date TEXT, ts_code TEXT,"
        " buy_sm_amount REAL, sell_sm_amount REAL, buy_md_amount REAL,"
        " sell_md_amount REAL, buy_lg_amount REAL, sell_lg_amount REAL,"
        " buy_elg_amount REAL, sell_elg_amount REAL, net_mf_amount REAL,"
        " fetched_at REAL, PRIMARY KEY(ts_code, trade_date))",
        "CREATE TABLE corp_event (ts_code TEXT, ann_date TEXT, event_type TEXT,"
        " direction TEXT, magnitude REAL, details_json TEXT, source TEXT,"
        " fetched_at REAL)",
        "CREATE TABLE research (ts_code TEXT, report_date TEXT, rating TEXT,"
        " target_price REAL, org_name TEXT, fetched_at REAL)",
        "CREATE TABLE financial (ts_code TEXT, end_date TEXT, endpoint TEXT,"
        " payload TEXT, fetched_at REAL, PRIMARY KEY(ts_code, end_date, endpoint))",
        "CREATE TABLE sw_index (index_code TEXT PRIMARY KEY, name TEXT,"
        " level TEXT, parent_code TEXT, src TEXT, is_pub TEXT, fetched_at REAL)",
        "CREATE TABLE sw_member (index_code TEXT, con_code TEXT, in_date TEXT,"
        " out_date TEXT, is_new TEXT, snapshot_date TEXT)",
        "CREATE TABLE sw_daily (ts_code TEXT, trade_date TEXT, close REAL)",
    ):
        conn.execute(ddl)
    db.ensure_tables(conn)
    # 注入 000001.SZ 合成序列(128日,末日命中形态)
    seq = _mk(_base_series())
    for i, (_, r) in enumerate(seq.iterrows()):
        conn.execute(
            "INSERT OR REPLACE INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("000001.SZ", _DATES[i], r["open"], r["high"], r["low"], r["close"],
             float(r["close"]) / 1.084, 8.4 if i == len(seq) - 1 else 0.5,
             r["vol"], r["close"] * r["vol"] * 10, r["adj_factor"]))
    conn.execute("INSERT INTO stock_basic VALUES"
                 " ('000001.SZ','测试股','银行','主板','20200101')")
    conn.execute("INSERT INTO moneyflow VALUES"
                 " ('20260828','000001.SZ',100,100,100,100,2000,1000,5000,1000,3900,0)")
    conn.commit()
    yield conn
    conn.close()


def _patch_screen(monkeypatch):
    monkeypatch.setattr(screen.db, "read_sw_industry",
                        lambda c, codes: pd.DataFrame(
                            columns=["ts_code", "index_code", "level", "name"]))
    monkeypatch.setattr(screen.db, "read_sw_daily_all",
                        lambda c, day, lookback=260: pd.DataFrame())
    monkeypatch.setattr(screen.cninfo, "sync_cninfo",
                        lambda c, codes, day, **kw: {"ok": 0, "fail": 0, "events": 0})


def test_run_day_end_to_end(setup_db, monkeypatch):
    _patch_screen(monkeypatch)
    monkeypatch.setattr(screen.chips, "ensure_cyq", lambda c, p, d: d)
    day = _DAY
    out = screen.run_day(day, conn=setup_db, pro=FakePro(day), do_cninfo=False)
    assert out["pool_n"] == 1
    snap = out["snapshot_df"]
    assert not snap.empty
    assert snap["composite"].iloc[0] > 0
    assert not out["pattern_df"].empty  # 合成序列命中 C1/C2/C3
    assert isinstance(out["tags_df"], pd.DataFrame) and not out["tags_df"].empty


def test_run_day_idempotent(setup_db, monkeypatch):
    _patch_screen(monkeypatch)
    day = _DAY
    monkeypatch.setattr(screen.chips, "ensure_cyq", lambda c, p, d: d)
    screen.run_day(day, conn=setup_db, pro=FakePro(day), do_cninfo=False)
    screen.run_day(day, conn=setup_db, pro=FakePro(day), do_cninfo=False)
    n = setup_db.execute(
        "SELECT COUNT(*) FROM surge_snapshot WHERE trade_date=?", (day,)).fetchone()[0]
    assert n == 1
    nt = setup_db.execute(
        "SELECT COUNT(*) FROM surge_tags WHERE trade_date=?", (day,)).fetchone()[0]
    np_ = setup_db.execute(
        "SELECT COUNT(*) FROM surge_pattern_hits WHERE trade_date=?", (day,)).fetchone()[0]
    assert nt > 0 and np_ == 1


def test_run_day_empty_pool(setup_db, monkeypatch):
    _patch_screen(monkeypatch)
    out = screen.run_day("20250101", conn=setup_db, pro=FakePro("20250101"),
                         do_cninfo=False)
    assert out["pool_n"] == 0 and out["snapshot_df"].empty
