"""daily_refresh.py 每日增量刷新测试（全部用 :memory: fixture + 注入 fetch）."""

from __future__ import annotations

import sqlite3

import pandas as pd

from davis_analyzer.limitup import daily_refresh


def _seed_calendar(conn: sqlite3.Connection, *dates: str) -> None:
    conn.executemany(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [("000001.SZ", d, 10.0, 11.0, 9.5, 10.5, 10.0, 5.0, 100, 1e6, 1.0, None)
         for d in dates],
    )
    conn.commit()


def test_missing_dates(limitup_db: sqlite3.Connection) -> None:
    _seed_calendar(limitup_db, "20260812", "20260813", "20260814")
    limitup_db.execute(
        "INSERT INTO moneyflow VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("20260812", "000001.SZ", 1, 1, 1, 1, 1, 1, 1, 1, 0, None),
    )
    limitup_db.commit()
    assert daily_refresh.missing_dates(limitup_db, "moneyflow",
                                       ["20260812", "20260813", "20260814"]) == [
        "20260813", "20260814",
    ]


def test_refresh_moneyflow_idempotent(limitup_db: sqlite3.Connection) -> None:
    _seed_calendar(limitup_db, "20260812")
    df = pd.DataFrame([{
        "ts_code": "000001.SZ", "buy_sm_amount": 10.0, "sell_sm_amount": 20.0,
        "buy_md_amount": 1, "sell_md_amount": 2, "buy_lg_amount": 3,
        "sell_lg_amount": 4, "buy_elg_amount": 5, "sell_elg_amount": 6,
        "net_mf_amount": -13.0,
    }])
    n1 = daily_refresh.refresh_moneyflow(limitup_db, ["20260812"], lambda d: df)
    n2 = daily_refresh.refresh_moneyflow(limitup_db, ["20260812"], lambda d: df)
    assert n1 == 1 and n2 == 1  # OR REPLACE 幂等，行数不涨
    assert limitup_db.execute("SELECT COUNT(*) FROM moneyflow").fetchone()[0] == 1


def test_refresh_top_list(limitup_db: sqlite3.Connection) -> None:
    _seed_calendar(limitup_db, "20260812")
    df = pd.DataFrame([{
        "ts_code": "000001.SZ", "name": "平安银行", "close": 10.5,
        "pct_change": 10.0, "turnover_rate": 5.0, "amount": 1e8,
        "l_sell": 1e7, "l_buy": 2e7, "l_amount": 3e7, "net_amount": 1e7,
        "net_rate": 0.1, "amount_rate": 0.3, "float_values": 1e9,
        "reason": "日涨幅偏离值达7%",
    }])
    assert daily_refresh.refresh_top_list(limitup_db, ["20260812"], lambda d: df) == 1
    assert daily_refresh.refresh_top_list(limitup_db, ["20260812"], lambda d: None) == 0


def test_compute_intraday_features_b_convention() -> None:
    # open 10, high 12, low 9, close 11, pre_close 9.5 → B 口径
    prices = pd.DataFrame([{
        "ts_code": "000001.SZ", "trade_date": "20260812",
        "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "pre_close": 9.5,
    }])
    f = daily_refresh.compute_intraday_features(prices).iloc[0]
    assert abs(f["gap"] - (10.0 / 9.5 - 1)) < 1e-9
    assert abs(f["amplitude"] - 3.0 / 9.5) < 1e-9
    assert abs(f["close_position"] - 2.0 / 3.0) < 1e-9
    assert abs(f["upper_shadow"] - 1.0 / 3.0) < 1e-9  # (12-11)/(h-l)
    assert abs(f["lower_shadow"] - 1.0 / 3.0) < 1e-9  # (min(10,11)-9)/(h-l)
    assert abs(f["body_ratio"] - 1.0 / 3.0) < 1e-9  # (11-10)/(h-l) 带符号
    # 一字板 h==l 跳过
    flat = pd.DataFrame([{
        "ts_code": "000001.SZ", "trade_date": "20260812",
        "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "pre_close": 9.1,
    }])
    assert daily_refresh.compute_intraday_features(flat).empty


def test_refresh_intraday_features(limitup_db: sqlite3.Connection) -> None:
    _seed_calendar(limitup_db, "20260812")
    n = daily_refresh.refresh_intraday_features(limitup_db, ["20260812"])
    assert n == 1
    row = limitup_db.execute(
        "SELECT close_position, body_ratio FROM intraday_feature"
    ).fetchone()
    assert row is not None and row[0] == 2 / 3


def test_refresh_corp_events(limitup_db: sqlite3.Connection) -> None:
    float_df = pd.DataFrame([{
        "ts_code": "000001.SZ", "ann_date": "20260810",
        "float_date": "20260812", "float_ratio": 5.5,
    }])
    trade_df = pd.DataFrame([{
        "ts_code": "000002.SZ", "ann_date": "20260811",
        "in_de": "DE", "change_ratio": -1.2, "holder_type": "GM",
    }])
    n = daily_refresh.refresh_corp_events(
        limitup_db, "20260809", "20260812",
        lambda s, e: float_df, lambda s, e: trade_df,
    )
    assert n == 2
    kinds = dict(limitup_db.execute(
        "SELECT event_type, direction FROM corp_event").fetchall())
    assert kinds == {"share_float": "negative", "holder_trade": "decrease"}
    # OR IGNORE 幂等
    n2 = daily_refresh.refresh_corp_events(
        limitup_db, "20260809", "20260812",
        lambda s, e: float_df, lambda s, e: trade_df,
    )
    assert limitup_db.execute("SELECT COUNT(*) FROM corp_event").fetchone()[0] == 2
    assert n2 == 2  # 返回尝试写入数，实际行数不变


def test_refresh_limit_pool_ext_aware(limitup_db: sqlite3.Connection) -> None:
    from davis_analyzer.limitup import backfill

    _seed_calendar(limitup_db, "20260812", "20260813")
    # 0812：daily_scan 已写过 limit_pool 但 ext 无 float_mv → 需重拉
    limitup_db.execute(
        "INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-08-12", "000001", "limit_up", "甲", "X", 10.0, 1e8, 1, 0,
         "092500", "092500", 5.0, None),
    )
    backfill.ensure_ext_table(limitup_db)
    limitup_db.commit()
    raw = pd.DataFrame([{
        "trade_date": "", "ts_code": "000001.SZ", "industry": "X", "name": "甲",
        "pct_chg": 10.0, "close": 11.0, "amount": 1e8, "limit_times": 1,
        "float_mv": 1e9, "total_mv": 2e9, "turnover_ratio": 5.0, "fd_amount": 1e8,
        "first_time": "92500", "last_time": "92500", "open_times": 0,
        "limit": "U",
    }])
    calls: list[str] = []

    def fetch(d: str, t: str):
        calls.append(d)
        return raw if t == "U" and d == "20260812" else pd.DataFrame()

    n = daily_refresh.refresh_limit_pool(limitup_db, ["20260812", "20260813"], fetch)
    assert n == 1
    assert set(calls) == {"20260812", "20260813"}  # 两天都拉（0813 无任何数据）
    # 重拉后 0812 有了 ext float_mv，再跑只补 0813 之后的日子
    calls.clear()
    daily_refresh.refresh_limit_pool(limitup_db, ["20260812"], fetch)
    assert calls == []  # 0812 已 ext 完备 → 跳过
    ext = limitup_db.execute(
        "SELECT float_mv FROM limit_pool_ext WHERE trade_date='2026-08-12'"
    ).fetchone()
    assert ext[0] == 1e9
    # limit_pool 行被 REPLACE 为 Tushare 口径（封板时间已归一 6 位）
    pool = limitup_db.execute(
        "SELECT first_seal_time, turnover_rate FROM limit_pool WHERE trade_date='2026-08-12'"
    ).fetchone()
    assert pool == ("092500", 5.0)
