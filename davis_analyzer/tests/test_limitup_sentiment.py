"""sentiment.py 三轴环境与 regime 分档测试。"""

from __future__ import annotations

import sqlite3

import pandas as pd

from davis_analyzer.limitup import sentiment


def test_classify_regime_priority() -> None:
    freeze = pd.Series({"premium": -0.03, "limit_up_count": 100, "max_boards": 3,
                        "promo_12": 0.5})
    assert sentiment.classify_regime(freeze) == "冰点"  # 冰点优先于高潮/退潮
    hot = pd.Series({"premium": 0.05, "limit_up_count": 150, "max_boards": 8,
                     "promo_12": 0.6})
    assert sentiment.classify_regime(hot) == "高潮"
    cool = pd.Series({"premium": -0.005, "limit_up_count": 60, "max_boards": 4,
                      "promo_12": 0.5})
    assert sentiment.classify_regime(cool) == "退潮"
    warm = pd.Series({"premium": 0.02, "limit_up_count": 60, "max_boards": 4,
                      "promo_12": 0.5})
    assert sentiment.classify_regime(warm) == "回暖"
    nan_case = pd.Series({"premium": float("nan"), "limit_up_count": 60,
                          "max_boards": 4, "promo_12": 0.5})
    assert sentiment.classify_regime(nan_case) == "回暖"  # NaN 不触发条件


def test_build_market_regime(limitup_db: sqlite3.Connection) -> None:
    conn = limitup_db
    # 指数两天
    conn.executemany(
        "INSERT INTO index_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
        [("000001.SH", "20240102", 3000, 3050, 2990, 3040, 1, 1, 1.3, None),
         ("000001.SH", "20240103", 3040, 3060, 3030, 3050, 1, 1, 0.3, None),
         ("399001.SZ", "20240102", 9500, 9600, 9450, 9550, 1, 1, 1.0, None),
         ("399001.SZ", "20240103", 9550, 9620, 9540, 9600, 1, 1, 0.5, None),
         ("399006.SZ", "20240102", 1800, 1830, 1795, 1820, 1, 1, 1.1, None),
         ("399006.SZ", "20240103", 1820, 1840, 1810, 1830, 1, 1, 0.5, None)],
    )
    # 全市场宽度：两天各 2 只
    conn.executemany(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("600001.SH", "20240102", 10, 11, 10, 11, 10, 10, 0, 0, 1.0, None),
            ("600002.SH", "20240102", 10, 10.5, 9.9, 10.2, 10, 2, 0, 0, 1.0, None),
            ("600001.SH", "20240103", 11, 12.1, 11, 12.1, 11, 10, 0, 0, 1.0, None),
            ("600002.SH", "20240103", 10.2, 10.4, 10.1, 10.3, 10.2, 1, 0, 0, 1.0, None),
        ],
    )
    # 涨停池：0102 两只首板（其中 600001），0103 600001 晋级 2 板 → premium 出现在 0103
    conn.executemany(
        "INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("2024-01-02", "600001", "limit_up", "甲", "X", 10, 1e8, 1, 0,
             "093000", "093000", 5, None),
            ("2024-01-02", "600002", "limit_up", "乙", "X", 10, 1e8, 1, 0,
             "093000", "093000", 5, None),
            ("2024-01-02", "600003", "broken", "丙", "X", 5, 0, 1, 2,
             "093000", "140000", 5, None),
            ("2024-01-03", "600001", "limit_up", "甲", "X", 10, 1e8, 2, 0,
             "093000", "093000", 5, None),
        ],
    )
    conn.commit()
    regime = sentiment.build_market_regime(conn, "20240101", "20240110")
    assert len(regime) == 2
    r2 = regime[regime.trade_date == "20240102"].iloc[0]
    r3 = regime[regime.trade_date == "20240103"].iloc[0]
    assert r2["limit_up_count"] == 2
    assert abs(r2["broken_rate"] - 1 / 3) < 1e-9  # broken 1 / (2 up + 1 broken)
    # C1 修复：T 日池的晋级结果 T+1 才可观测，promo_* 归属到 T+1——
    # 0102 行 promo_12 为 NaN，0102 两只首板仅 600001 晋级 → 0.5 出现在 0103 行
    assert pd.isna(r2["promo_12"])
    assert pd.isna(r2["promo_23"]) and pd.isna(r2["promo_34"])
    assert abs(r3["promo_12"] - 0.5) < 1e-9
    # premium@0103 = mean(600001: 11/11-1=0, 600002: 10.2/10.2-1=0)
    assert abs(r3["premium"]) < 1e-9
    assert r3["lianban_count"] == 1 and r3["max_boards"] == 2
    assert 0 <= r3["up_down_ratio"] <= 1
    assert "regime_label" in regime.columns


def test_build_market_regime_empty_pool(limitup_db: sqlite3.Connection) -> None:
    conn = limitup_db
    # 窗口内无任何 limit_pool 行（回测窗口早于池覆盖起点）：空态须返回带列空帧
    conn.executemany(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("600001.SH", "20240102", 10, 11, 10, 11, 10, 10, 0, 0, 1.0, None),
            ("600001.SH", "20240103", 11, 12.1, 11, 12.1, 11, 10, 0, 0, 1.0, None),
        ],
    )
    conn.executemany(
        "INSERT INTO index_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
        [("000001.SH", "20240102", 3000, 3050, 2990, 3040, 1, 1, 1.3, None),
         ("000001.SH", "20240103", 3040, 3060, 3030, 3050, 1, 1, 0.3, None)],
    )
    conn.commit()
    regime = sentiment.build_market_regime(conn, "20240101", "20240110")
    assert len(regime) == 2
    assert "regime_label" in regime.columns
    # 涨停三轴全 NaN → 所有 regime 条件跳过 → 回暖
    assert (regime["regime_label"] == "回暖").all()


def test_build_market_regime_single_day_window(limitup_db: sqlite3.Connection) -> None:
    # fix round 2 回归：单日窗口（start == end）池日无 T+1 可观测，
    # _promotion_axes 须返回带列空帧，不得在 merge 处抛 KeyError
    conn = limitup_db
    conn.executemany(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [("600001.SH", "20240102", 10, 11, 10, 11, 10, 10, 0, 0, 1.0, None)],
    )
    conn.executemany(
        "INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [("2024-01-02", "600001", "limit_up", "甲", "X", 10, 1e8, 1, 0,
          "093000", "093000", 5, None)],
    )
    conn.commit()
    regime = sentiment.build_market_regime(conn, "20240102", "20240102")
    assert len(regime) == 1
    row = regime.iloc[0]
    assert pd.isna(row["promo_12"]) and pd.isna(row["promo_23"])
    assert pd.isna(row["promo_34"])  # 无 T+1 → 晋级轴全 NaN 而非 KeyError
    assert "regime_label" in regime.columns
