"""candidates.py 盘后候选构建测试（first_board 口径 + 增强标注 + 风险列 + 空数据防线）."""

from __future__ import annotations

import sqlite3
from typing import Any

import pandas as pd
from loguru import logger

from davis_analyzer.limitup import candidates

EVENT_DAY = "20240415"  # 周一；前置 60 个 bdate 横盘窗口


def _prior_dates(day: str, periods: int) -> list[str]:
    """事件日前恰好 periods 个工作日（end 用工作日本身，过滤非工作日 end 的差一）."""
    end = pd.to_datetime(day, format="%Y%m%d")
    dates = pd.bdate_range(end=end, periods=periods + 1)
    return [d.strftime("%Y%m%d") for d in dates if d.strftime("%Y%m%d") < day]


def _seed_stock(
    conn: sqlite3.Connection, code: str, day: str, *,
    periods: int = 60, alternating: bool = False,
) -> None:
    """前置横盘（10.0）或大箱体交替（10/13）后首板涨停的日线序列.

    60 日横盘 + 涨停创新高 → 突破型（prior_high60=10, box40=0）；
    45 日交替（不足 60 行 prior_high60 缺失 + box40≈0.30 ≥ 0.25）→ 其他。
    """
    rows = []
    for i, d in enumerate(_prior_dates(day, periods)):
        px = (10.0 if i % 2 == 0 else 13.0) if alternating else 10.0
        rows.append((code, d, px, px, px, px, px, 0.0, 1e4, 1e7, 1.0, None))
    # 事件日：非一字开盘、收盘涨停（round(10.0×1.1, 2)=11.0）
    rows.append((code, day, 10.5, 11.0, 10.5, 11.0, 10.0, 10.0, 1e6, 1e8, 1.0, None))
    conn.executemany("INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def _pool_row(
    code6: str, day: str, name: str, sector: str, boards: int,
    seal: float, broken: int, seal_time: str,
) -> tuple[Any, ...]:
    dash = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    return (dash, code6, "limit_up", name, sector, 10.0, seal, boards, broken,
            seal_time, seal_time, 5.0, None)


def _seed_day(conn: sqlite3.Connection, day: str = EVENT_DAY) -> None:
    """单日夹具：5 只目标股 + 30 只陪跑股（抬过 regime 冰点线 30 家）."""
    _seed_stock(conn, "600100.SH", day)                              # 甲 突破型
    _seed_stock(conn, "600200.SH", day)                              # 乙 突破型
    _seed_stock(conn, "600300.SH", day)                              # 丙 突破型(池记 2 板)
    _seed_stock(conn, "600400.SH", day, periods=45, alternating=True)  # 丁 其他
    _seed_stock(conn, "600500.SH", day)                              # 戊 突破型
    pool = [
        _pool_row("600100", day, "甲", "X业", 1, 1e8, 0, "093000"),
        _pool_row("600200", day, "乙", "Y业", 1, 3e7, 2, "103000"),
        _pool_row("600300", day, "丙", "Z业", 2, 1e8, 0, "093000"),
        _pool_row("600400", day, "丁", "W业", 1, 1e8, 0, "093000"),
        _pool_row("600500", day, "戊", "V业", 1, 5e7, 0, "143000"),
    ]
    # 陪跑股仅池行（无日线/无 basic → build_events 剔除，但 _limit_axes 计数）
    pool += [
        _pool_row(f"601{i:02d}", day, f"陪{i}", "P业", 1, 1e7, 0, "093000")
        for i in range(30)
    ]
    conn.executemany("INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", pool)
    dash = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    conn.executemany(
        "INSERT INTO limit_pool_ext VALUES (?,?,?,?)",
        [(dash, c, "limit_up", 1e9) for c in
         ("600100", "600200", "600300", "600400", "600500")],
    )
    conn.executemany(
        "INSERT INTO stock_basic VALUES (?,?,?,?,?,?)",
        [(f"{c}.SH", n, "I", "L", None, "20000101") for c, n in
         (("600100", "甲"), ("600200", "乙"), ("600300", "丙"),
          ("600400", "丁"), ("600500", "戊"))],
    )
    # moneyflow 当日：甲 大单主导 0.6 / 乙 小单主导 0.2 / 戊 无卖出额 → NaN
    conn.executemany(
        "INSERT INTO moneyflow VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (day, "600100.SH", 1e7, 2e7, 1e7, 2e7, 1e7, 3e7, 1e7, 3e7, 0.0, None),
            (day, "600200.SH", 1e7, 6e7, 1e7, 2e7, 1e7, 1e7, 1e7, 1e7, 0.0, None),
            (day, "600500.SH", None, None, None, None, None, None, None, None,
             None, None),
        ],
    )
    conn.commit()


def test_build_candidates_filters_order_and_annotations(
    limitup_db: sqlite3.Connection,
) -> None:
    _seed_day(limitup_db)
    df = candidates.build_candidates(limitup_db, EVENT_DAY)
    # 过滤：2 板（600300）与其他形态（600400）被滤；陪跑股无日线不入事件表
    assert list(df["ts_code"]) == ["600100.SH", "600500.SH", "600200.SH"]
    for col in candidates.CANDIDATE_COLUMNS:
        assert col in df.columns
    a, e, b = df.iloc[0], df.iloc[1], df.iloc[2]
    # 形态：60 日横盘后涨停创新高 → 突破型
    assert (df["pattern_label"] == "突破型").all()
    assert a["name"] == "甲" and a["sector"] == "X业"
    # seal_ratio = seal_amount/float_mv，降序输出
    assert abs(a["seal_ratio"] - 0.10) < 1e-9
    assert abs(e["seal_ratio"] - 0.05) < 1e-9
    assert abs(b["seal_ratio"] - 0.03) < 1e-9
    # lookback 窗口内前置涨停计数（窗口正确性证据）
    assert a["prev_limit_count_60"] == 0
    # 封档（pd.cut 右闭）：0.10→强；0.05/0.03→中
    assert a["封档"] == "强" and e["封档"] == "中" and b["封档"] == "中"
    # 首封时间档 / 炸板次数透传
    assert a["first_seal_band"] == "早盘"
    assert b["first_seal_band"] == "午盘"
    assert e["first_seal_band"] == "尾盘"
    assert b["broken_count"] == 2
    # 卖出结构：lg_sell_share = (大单+特大单卖出)/总卖出
    assert abs(a["lg_sell_share"] - 0.6) < 1e-9
    assert abs(b["lg_sell_share"] - 0.2) < 1e-9
    assert pd.isna(e["lg_sell_share"])  # sell_total<=0 → NaN
    # enhanced = 大单主导(≥0.50) × 强封(≥0.05)
    assert bool(a["enhanced"]) and a["enhanced"] is not None
    assert not bool(e["enhanced"])  # lg NaN → False
    assert not bool(b["enhanced"])  # seal 0.03 < 0.05
    # fill_prob（engine base 档）：早盘硬板 0.20 / 尾盘 0.35 / 炸板回封 0.70
    assert abs(a["fill_prob"] - 0.20) < 1e-9
    assert abs(e["fill_prob"] - 0.35) < 1e-9
    assert abs(b["fill_prob"] - 0.70) < 1e-9
    assert df["fill_prob"].between(0.05, 0.95).all()


def test_enhanced_filter_subset(limitup_db: sqlite3.Connection) -> None:
    _seed_day(limitup_db)
    df = candidates.build_candidates(limitup_db, EVENT_DAY, enhanced_filter=True)
    # 仅「大单主导 × 强封」子集（双臂对照的增强臂口径）
    assert list(df["ts_code"]) == ["600100.SH"]
    assert bool(df.iloc[0]["enhanced"])


def test_empty_limit_pool_day_warns_and_returns_empty(
    limitup_db: sqlite3.Connection,
) -> None:
    # 日历非空但当日 limit_pool 无行（19:20 daily 刷新失败场景）
    limitup_db.execute(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("600100.SH", "20240412", 10, 10, 10, 10, 10, 0, 1e4, 1e7, 1.0, None),
    )
    limitup_db.commit()
    msgs: list[str] = []
    handler = logger.add(lambda m: msgs.append(str(m)), level="WARNING")
    try:
        df = candidates.build_candidates(limitup_db, EVENT_DAY)
    finally:
        logger.remove(handler)
    assert df.empty  # 不抛异常
    assert "ts_code" in df.columns  # 契约列仍在（CLI 层可直接渲染）
    assert any("limit_pool" in m for m in msgs)  # 告警供 CLI 退出提示


def test_candidate_context(limitup_db: sqlite3.Connection) -> None:
    _seed_day(limitup_db)
    ctx = candidates.candidate_context(limitup_db, EVENT_DAY)
    assert ctx["trade_date"] == EVENT_DAY
    # 35 家涨停 > 30（冰点线），premium/promo_12 窗口末日不可观测 → 回暖
    assert ctx["regime_label"] == "回暖"
    assert ctx["limit_up_count"] == 35  # 5 目标 + 30 陪跑
    assert ctx["promo_12"] is None
    assert ctx["premium"] is None


def test_candidate_context_empty_db(limitup_db: sqlite3.Connection) -> None:
    ctx = candidates.candidate_context(limitup_db, EVENT_DAY)
    assert ctx == {"trade_date": EVENT_DAY, "regime_label": "无数据"}
