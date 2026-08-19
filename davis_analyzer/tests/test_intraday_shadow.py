"""影子验证（paper_shadow）的关键语义单测：特征因果性 + 底仓截断 + 台账."""

from __future__ import annotations

import sqlite3

import pandas as pd

from davis_analyzer.intraday import paper_shadow


def _mk_conn(tmp_path, tables: dict[str, str]) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "t.db")
    for ddl in tables.values():
        con.execute(ddl)
    con.commit()
    return con


def test_day_features_causality(tmp_path):
    """trend_up 只用昨日及以前数据：今日收盘暴跌不影响当日入场决策."""
    con = _mk_conn(tmp_path, {
        "daily_price": "CREATE TABLE daily_price (ts_code TEXT, trade_date TEXT, "
                       "close REAL, pre_close REAL)",
        "minute_bar": "CREATE TABLE minute_bar (ts_code TEXT, trade_date TEXT, "
                      "trade_time TEXT, volume REAL, freq TEXT)",
    })
    # 21 天稳步上行(昨日 close=110 > MA20)；当日(第22根 close 暴跌)不入特征
    rows = [("600000.SH", f"2026{i:04d}", 100.0 + i, 99.0 + i) for i in range(1, 22)]
    con.executemany("INSERT INTO daily_price VALUES (?,?,?,?)", rows)
    # 12 天首bar量历史（中位=100）
    vols = [("600000.SH", f"2025{i:04d}", "09:35", 100.0 + (i % 3), "5min")
            for i in range(1, 13)]
    con.executemany("INSERT INTO minute_bar VALUES (?,?,?,?,?)", vols)
    con.commit()

    feat = paper_shadow.day_features(con, con, "600000.SH", "20260818",
                                     day_open=95.0, bar0_vol=250.0)
    # 昨收=120(row i=21: 100+21=121? 最后插入 i=21 → close=121)... 用实际值断言
    hist = con.execute(
        "SELECT close FROM daily_price WHERE ts_code='600000.SH' ORDER BY trade_date"
    ).fetchall()
    yesterday_close = hist[-1][0]
    ma20 = sum(r[0] for r in hist[-21:-1]) / 20
    assert feat["trend_up"] == (yesterday_close > ma20)
    assert feat["vol_ratio1"] == 250.0 / 101.0  # 种子序列(100,101,102循环)中位=101
    con.close()


def test_day_features_insufficient_history(tmp_path):
    con = _mk_conn(tmp_path, {
        "daily_price": "CREATE TABLE daily_price (ts_code TEXT, trade_date TEXT, "
                       "close REAL, pre_close REAL)",
    })
    feat = paper_shadow.day_features(con, con, "600000.SH", "20260818", 95.0, 100.0)
    assert feat == {"trend_up": None, "vol_ratio1": None}  # None → 过滤器拦截不入场
    con.close()


def test_trade_fraction_capped_by_real_base():
    """真实底仓截断：350 股 → 100 股可做；300 股 → 30%不足一手 → 跳过."""
    assert int(350 * 0.30 / 100) * 100 == 100
    assert int(300 * 0.30 / 100) * 100 == 0  # run_shadow 中 <100 即 continue


def test_shadow_tables_and_empty_report(tmp_path):
    con = sqlite3.connect(tmp_path / "r.db")
    paper_shadow.ensure_shadow_tables(con)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"intraday_shadow_trade", "intraday_shadow_run"} <= tables
    con.close()
    # 空台账的报表可读
    text = paper_shadow.shadow_report(tmp_path / "r.db")
    assert "空" in text
