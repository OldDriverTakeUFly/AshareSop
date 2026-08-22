"""daily_price 复权因子合并测试（2026-07~08 adj_factor 单值广播事故修复）.

背景：_save_daily_prices 曾用 trade_date 单键 dict 合并全市场同日 adj_df，
dict 构造时 5500+ 行互相覆盖只剩最后一行票的因子，再 map 广播到全市场
（污染 16568 行）。修复：按 (ts_code, trade_date) 复合键 merge +
广播形态防呆拒写。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

import pandas as pd
import pytest

from stockhot.data_layer.repository import MarketDataRepository


@contextmanager
def _no_close(thing):
    yield thing  # 透传且不关闭, 让测试在写入后仍可查询内存库


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE daily_price ("
        "ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL, "
        "close REAL, pre_close REAL, pct_chg REAL, vol REAL, amount REAL, "
        "adj_factor REAL, fetched_at REAL)"
    )
    return conn


def _save(daily_df: pd.DataFrame, adj_df: pd.DataFrame) -> pd.DataFrame:
    """跑 _save_daily_prices 写入内存库, 返回落库行."""
    conn = _mem_conn()
    with patch(
        "stockhot.data_layer.repository.get_connection", return_value=conn
    ), patch("stockhot.data_layer.repository.closing", _no_close):
        repo = MarketDataRepository.__new__(MarketDataRepository)
        repo._save_daily_prices(daily_df, adj_df)
    rows = conn.execute(
        "SELECT ts_code, trade_date, close, adj_factor FROM daily_price "
        "ORDER BY ts_code, trade_date"
    ).fetchall()
    return pd.DataFrame(rows, columns=["ts_code", "trade_date", "close", "adj_factor"])


def _daily(codes: list[str], dates: list[str], close: float = 10.0) -> pd.DataFrame:
    return pd.DataFrame(
        [(c, d, close) for c in codes for d in dates],
        columns=["ts_code", "trade_date", "close"],
    )


def _adj(pairs: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(pairs, columns=["ts_code", "trade_date", "adj_factor"])


def test_same_day_universe_merge_no_broadcast() -> None:
    """全市场同日: 每票得自己的因子, 不再被最后一行票广播.

    (旧 trade_date 单键 dict 实现在此用例上必挂: 两票都会得到 2.0)"""
    daily = _daily(["000001.SZ", "600000.SH"], ["20260817"])
    adj = _adj([("000001.SZ", "20260817", 139.0), ("600000.SH", "20260817", 2.0)])
    out = _save(daily, adj)
    assert out["adj_factor"].tolist() == [139.0, 2.0]


def test_per_stock_timeseries_merge() -> None:
    """单票多日(davis 按票增量路径): 逐日因子正确对齐."""
    daily = _daily(["000001.SZ"], ["20260817", "20260818", "20260819"])
    adj = _adj([
        ("000001.SZ", "20260817", 139.0),
        ("000001.SZ", "20260818", 139.0),
        ("000001.SZ", "20260819", 140.0),
    ])
    out = _save(daily, adj)
    assert out["adj_factor"].tolist() == [139.0, 139.0, 140.0]


def test_empty_adj_leaves_null() -> None:
    """adj_df 为空: 因子留空(None), 不阻断价格落库."""
    out = _save(_daily(["000001.SZ"], ["20260817"]), pd.DataFrame())
    assert out["adj_factor"].isna().all()


def test_guard_rejects_broadcast_pattern() -> None:
    """广播形态防呆: 多票相邻日跳变>3x 占比超阈值 → 拒写."""
    codes = [f"00000{i}.SZ" for i in range(100)]
    daily = _daily(codes, ["20260817", "20260818"])
    # 模拟广播: 第一天各票真值 1.0, 第二天全部被写成同一票的 5.0 (>3x 跳变 100%)
    adj = _adj(
        [(c, "20260817", 1.0) for c in codes]
        + [(c, "20260818", 5.0) for c in codes]
    )
    with pytest.raises(ValueError, match="单值广播"):
        _save(daily, adj)


def test_guard_allows_sparse_real_exdiv() -> None:
    """真实除权(少数票单日跳变)不触发防呆."""
    codes = [f"00000{i}.SZ" for i in range(100)]
    daily = _daily(codes, ["20260817", "20260818"])
    pairs = [(c, "20260817", 1.0) for c in codes]
    pairs += [(c, "20260818", 4.0) for c in codes[:3]]  # 3 只真实除权
    pairs += [(c, "20260818", 1.0) for c in codes[3:]]
    out = _save(daily, _adj(pairs))
    assert len(out) == 200
