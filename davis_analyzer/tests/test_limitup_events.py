"""events.py 基础构建测试。"""

from __future__ import annotations

import sqlite3

import numpy as np

from davis_analyzer.limitup import events


def _seed_base(conn: sqlite3.Connection) -> None:
    rows = [
        # (code, date, open, high, low, close, pre_close, adj)
        ("600001.SH", "20240102", 9.5, 11.0, 9.5, 11.0, 10.0, 1.0),   # 真涨停 +10%
        ("600001.SH", "20240103", 11.0, 12.1, 11.0, 12.1, 11.0, 1.0),  # 2 连板
        ("300002.SZ", "20240102", 10.0, 12.0, 10.0, 12.0, 10.0, 1.0),  # 创业板 +20%
        ("600003.SH", "20240102", 10.0, 10.5, 9.8, 10.2, 10.0, 1.0),   # 未涨停（不应入选）
        ("600004.SH", "20240102", 11.0, 11.0, 11.0, 11.0, 10.0, 2.0),  # 除权日 adj 变化
    ]
    conn.executemany(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(c, d, o, h, l, cl, pc, (cl/pc-1)*100, 0.0, 0.0, a, None)
         for c, d, o, h, l, cl, pc, a in rows],
    )
    conn.executemany(
        "INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("2024-01-02", "600001", "limit_up", "甲", "X业", 10.0, 1e8, 1, 0,
             "093000", "093000", 5.0, None),
            ("2024-01-03", "600001", "limit_up", "甲", "X业", 10.0, 1e8, 2, 0,
             "093000", "093000", 5.0, None),
            ("2024-01-02", "300002", "limit_up", "乙创", "Y业", 20.0, 1e8, 1, 0,
             "093000", "093000", 5.0, None),
            ("2024-01-02", "600003", "limit_up", "丙", "Z业", 2.0, 0.0, 1, 0,
             "093000", "093000", 5.0, None),
            ("2024-01-02", "600004", "limit_up", "丁", "W业", 10.0, 1e8, 1, 0,
             "093000", "093000", 5.0, None),
        ],
    )
    conn.execute(
        "INSERT INTO limit_pool_ext VALUES ('2024-01-02','600001','limit_up',1e9)"
    )
    conn.execute(
        "INSERT INTO limit_pool_ext VALUES ('2024-01-03','600001','limit_up',1.1e9)"
    )
    conn.execute(
        "INSERT INTO limit_pool_ext VALUES ('2024-01-02','300002','limit_up',2e9)"
    )
    conn.executemany(
        "INSERT INTO stock_basic VALUES (?,?,?,?,?,?)",
        [
            ("600001.SH", "甲", "X业", "L", None, "20000101"),
            ("300002.SZ", "乙创", "Y业", "L", None, "20000101"),
            ("600003.SH", "丙", "Z业", "L", None, "20000101"),
            ("600004.SH", "丁", "W业", "L", None, "20231220"),  # 上市<60天
        ],
    )
    conn.commit()


def test_prev_window_count() -> None:
    ranks = np.array([10, 11, 80, 82])
    # 简报原期望 [0,1,1,2] 与文档语义矛盾：80 与前序 10/11 相差 69/70 > 60 不应计入，
    # 82 只计入 2 天前的 80 → 正确期望为 [0,1,0,1]。
    np.testing.assert_array_equal(events.prev_window_count(ranks, 60),
                                  [0, 1, 0, 1])
    np.testing.assert_array_equal(events.prev_window_count(np.array([1])), [0])


def test_build_events_filters(limitup_db: sqlite3.Connection) -> None:
    _seed_base(limitup_db)
    df = events.build_events(limitup_db, "20240101", "20240110")
    codes = set(df["ts_code"])
    # 600003 非真实涨停、600004 上市<60天 被剔除；600001 两天保留、300002 保留
    assert codes == {"600001.SH", "300002.SZ"}
    row = df[(df.ts_code == "600001.SH") & (df.trade_date == "20240103")].iloc[0]
    assert row["limit_price"] == 12.1
    assert row["consecutive_boards"] == 2
    assert abs(row["seal_ratio"] - 1e8 / 1.1e9) < 1e-9
    # 首板事件的 60 日前置涨停计数
    first = df[(df.ts_code == "600001.SH") & (df.trade_date == "20240102")].iloc[0]
    assert first["prev_limit_count_60"] == 0


def _seed_ex_dividend(conn: sqlite3.Connection) -> None:
    rows = [
        # (code, date, open, high, low, close, pre_close, adj)
        ("600005.SH", "20240102", 9.8, 11.0, 9.8, 11.0, 10.0, 1.0),   # 真涨停 +10%
        ("600005.SH", "20240103", 11.0, 12.1, 11.0, 12.1, 11.0, 2.0),  # 真涨停但除权
    ]
    conn.executemany(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(c, d, o, h, l, cl, pc, (cl/pc-1)*100, 0.0, 0.0, a, None)
         for c, d, o, h, l, cl, pc, a in rows],
    )
    conn.executemany(
        "INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("2024-01-02", "600005", "limit_up", "戊", "V业", 10.0, 1e8, 1, 0,
             "093000", "093000", 5.0, None),
            ("2024-01-03", "600005", "limit_up", "戊", "V业", 10.0, 1e8, 2, 0,
             "093000", "093000", 5.0, None),
        ],
    )
    conn.execute(
        "INSERT INTO stock_basic VALUES (?,?,?,?,?,?)",
        ("600005.SH", "戊", "V业", "L", None, "20000101"),
    )
    conn.commit()


def test_ex_dividend_event_removed(limitup_db: sqlite3.Connection) -> None:
    _seed_ex_dividend(limitup_db)
    df = events.build_events(limitup_db, "20240101", "20240110")
    # 除权日（adj_factor 1.0 → 2.0）事件剔除，宁少勿错；前一日涨停保留
    assert set(df["trade_date"]) == {"20240102"}
    row = df.iloc[0]
    assert row["ts_code"] == "600005.SH"
    assert row["limit_price"] == 11.0


def test_return_labels(limitup_db: sqlite3.Connection) -> None:
    _seed_base(limitup_db)
    # 再造 2 天价格：600001 在 0104 断板低开、0105 反包
    conn = limitup_db
    conn.executemany(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("600001.SH", "20240104", 11.5, 11.8, 11.0, 11.2, 12.1, -7.4, 0, 0, 1.0, None),
            ("600001.SH", "20240105", 11.0, 12.32, 11.0, 12.32, 11.2, 10.0, 0, 0, 1.0, None),
        ],
    )
    conn.commit()
    df = events.build_events(conn, "20240101", "20240110")
    e2 = df[(df.ts_code == "600001.SH") & (df.trade_date == "20240103")].iloc[0]
    # 简报原式 abs(ret - x/y - 1) 运算优先级错误（恒为 |-2|=2），
    # 最小修正为 abs(ret - (x/y - 1))，断言语义不变
    assert abs(e2["ret_open_1"] - (11.5 / 12.1 - 1)) < 1e-9
    assert abs(e2["ret_close_1"] - (11.2 / 12.1 - 1)) < 1e-9
    assert not e2["promoted"]  # 0104 收盘 11.2 未涨停
    e1 = df[(df.ts_code == "600001.SH") & (df.trade_date == "20240102")].iloc[0]
    assert e1["promoted"]  # 0103 12.1 = 11.0*1.1 涨停
    assert abs(e1["ret_open_1"] - (11.0 / 11.0 - 1)) < 1e-9
    assert abs(e1["ret_3d"] - (12.32 / 11.0 - 1)) < 1e-9  # 0105 收盘/0102 涨停价
