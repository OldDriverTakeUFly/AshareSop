# davis_analyzer/tests/test_recap_data.py
"""recap 数据层:双库 bundle、去重、时间归一、日期转换。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from davis_analyzer.recap import data


def _mk_stockhot_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE daily_data (trade_date TEXT, data_type TEXT, data_json TEXT);
    CREATE TABLE analysis_results (trade_date TEXT, analysis_type TEXT, result_json TEXT);
    """)
    con.execute("INSERT INTO daily_data VALUES (?,?,?)", ("2026-09-18", "limit_up_pool", json.dumps([
        {"code": "605577.SH", "name": "龙版传媒", "sector": "出版", "change_pct": 9.97,
         "consecutive_boards": 3, "broken_count": 2, "first_seal_time": "94700",
         "last_seal_time": "143500", "turnover_rate": 11.87},
        # 双写重复行(裸码),应被去重
        {"code": "605577", "name": "龙版传媒", "sector": "出版", "change_pct": 9.97,
         "consecutive_boards": 3, "broken_count": 2, "first_seal_time": "94700",
         "last_seal_time": "143500", "turnover_rate": 11.87},
    ])))
    con.execute("INSERT INTO daily_data VALUES (?,?,?)", ("2026-09-18", "broken_pool", json.dumps([
        {"code": "688296.SH", "name": "和达科技", "sector": "软件开发", "change_pct": 9.83,
         "broken_count": 1}])))
    con.execute("INSERT INTO daily_data VALUES (?,?,?)", ("2026-09-18", "limit_down_pool", json.dumps([
        {"code": "002163.SZ", "name": "海南发展", "sector": "装修装饰", "change_pct": -9.97}])))
    con.execute("INSERT INTO analysis_results VALUES (?,?,?)", ("2026-09-18", "limit_up_analysis", json.dumps(
        {"consecutive_boards": [{"board_count": 3, "stocks": [{"code": "605577.SH", "name": "龙版传媒"}]}]})))
    con.execute("INSERT INTO analysis_results VALUES (?,?,?)", (
        "2026-09-18", "dragon_tiger", json.dumps(
            {"brokers": [{"broker_name": "某营业部", "net_amount": 4.5e8}]})))
    con.execute("INSERT INTO daily_data VALUES (?,?,?)", ("2026-09-18", "dragon_tiger_detail", json.dumps([
        {"code": "605577.SH", "name": "龙版传媒", "net_buy_amount": 1.2e8}])))
    con.commit(); con.close()


def _mk_market_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE daily_price (ts_code TEXT, trade_date TEXT, close REAL, pre_close REAL,
                              high REAL, low REAL, pct_chg REAL, PRIMARY KEY (ts_code, trade_date));
    CREATE TABLE index_daily (ts_code TEXT, trade_date TEXT, close REAL, pct_chg REAL);
    """)
    for code, close, pre, high, low, pct in [
        ("605577.SH", 10.97, 9.97, 11.0, 9.0, 10.0),
        ("000001.SH", 3875.6, 3891.6, 3900.0, 3860.0, -0.41),   # 上证也会进 daily_price
        ("300XXX.SZ", 20.0, 15.0, 21.0, 15.5, 33.3),            # 振幅 36%
    ]:
        con.execute("INSERT INTO daily_price VALUES (?,?,?,?,?,?,?)",
                    (code, "20260918", close, pre, high, low, pct))
    con.execute("INSERT INTO index_daily VALUES (?,?,?,?)", ("000001.SH", "20260918", 3875.6, -0.411))
    con.execute("INSERT INTO index_daily VALUES (?,?,?,?)", ("399001.SZ", "20260918", 12345.6, 0.52))
    con.execute("INSERT INTO index_daily VALUES (?,?,?,?)", ("399006.SZ", "20260918", 2710.2, 0.85))
    con.commit(); con.close()


@pytest.fixture()
def bundle(tmp_path, monkeypatch):
    sh, mk = tmp_path / "stockhot.db", tmp_path / "market_data.db"
    _mk_stockhot_db(sh)
    _mk_market_db(mk)
    monkeypatch.setattr(data, "stockhot_db_path", lambda: sh)
    monkeypatch.setattr(data, "market_db_path", lambda: mk)
    return data.fetch_bundle("2026-09-18")


def test_pool_dedup_and_time_normalize(bundle):
    codes = [r["ts_code"] for r in bundle["pool"]]
    assert codes.count("605577.SH") == 1          # 双写去重
    row = bundle["pool"][0]
    assert row["first_seal_time"] == "09:47:00"   # 94700 → HH:MM:SS
    assert row["consecutive_boards"] == 3 and row["broken_count"] == 2


def test_bundle_keys_and_shapes(bundle):
    assert bundle["boards"][0]["board_count"] == 3
    assert "605577.SH" in bundle["lhb_codes"]
    assert bundle["brokers"][0]["net_amount"] == 4.5e8
    idx = {r["code"] for r in bundle["index"]}
    assert idx == {"000001.SH", "399001.SZ", "399006.SZ"}
    assert any(a["ts_code"] == "300XXX.SZ" for a in bundle["amplitude_top"])
    assert bundle["limit_up_count"] == 1


def test_missing_pool_raises(tmp_path, monkeypatch):
    sh, mk = tmp_path / "s.db", tmp_path / "m.db"
    _mk_stockhot_db(sh)
    con = sqlite3.connect(sh)
    con.execute("DELETE FROM daily_data WHERE data_type='limit_up_pool'")
    con.commit(); con.close()
    _mk_market_db(mk)
    monkeypatch.setattr(data, "stockhot_db_path", lambda: sh)
    monkeypatch.setattr(data, "market_db_path", lambda: mk)
    with pytest.raises(data.DailyDataMissing):
        data.fetch_bundle("2026-09-18")
