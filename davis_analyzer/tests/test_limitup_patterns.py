"""patterns.py K线/位置形态识别测试。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime

import numpy as np
import pandas as pd
from pytest import MonkeyPatch

from davis_analyzer.limitup import patterns


def test_seal_band() -> None:
    assert patterns.seal_band("093000") == "早盘"
    assert patterns.seal_band("133000") == "午盘"
    assert patterns.seal_band("143500") == "尾盘"
    assert patterns.seal_band("000000") == "未知"


def _mk_prices() -> pd.DataFrame:
    rows = []
    code = "600100.SH"
    rng = np.random.default_rng(7)
    # 40 天横盘 9.5-10.5，第 41 天放量涨停 11.0（突破 60 日前高近似）
    for i in range(1, 61):
        close = 10.0 + float(rng.normal(0, 0.1))
        rows.append((code, f"2023{int(10 + (i - 1) // 30):02d}{(i - 1) % 30 + 1:02d}",
                     close, close, close, close, close, close, 1e4, 1e7, 1.0))
    rows.append((code, "20240102", 10.5, 11.0, 10.5, 11.0, 10.0,
                 10.0, 1e6, 1e8, 1.0))
    df = pd.DataFrame(rows, columns=[
        "ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
        "pct_chg", "vol", "amount", "adj_factor"])
    return df


def test_classify_breakout() -> None:
    prices = _mk_prices()
    high = prices["high"]
    prior_high60 = high.rolling(60).max().shift(1)
    assert prices.iloc[-1]["close"] >= prior_high60.iloc[-1] * 0.98  # 突破条件成立
    ev = pd.DataFrame([{
        "ts_code": "600100.SH", "trade_date": "20240102", "close": 11.0,
        "first_seal_time": "093000", "last_seal_time": "093500",
        "consecutive_boards": 1,
    }])
    labeled = patterns.classify_from_prices(ev, prices)
    assert labeled.iloc[0]["pattern_label"] == "突破型"


def test_classify_from_prices_thresholds_param() -> None:
    # 60 日 @10 平盘（box40=0）+ 事件日 close 9.9：默认 0.98 → 9.9 ≥ 9.8 突破
    code = "600200.SH"
    dates = pd.bdate_range(end="20240102", periods=61)
    rows = [
        (code, d.strftime("%Y%m%d"), 10.0, 10.0, 10.0, 10.0, 10.0, 1e4, 1e6, 1.0)
        for d in dates[:-1]
    ]
    rows.append((code, "20240102", 9.9, 9.9, 9.9, 9.9, 10.0, 1e5, 1e7, 1.0))
    prices = pd.DataFrame(rows, columns=[
        "ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
        "vol", "amount", "adj_factor"])
    ev = pd.DataFrame([{"ts_code": code, "trade_date": "20240102"}])
    # 默认（含显式 thresholds=None）= 冻结先验，行为与参数化前一致
    assert patterns.classify_from_prices(ev, prices).iloc[0]["pattern_label"] == "突破型"
    assert (patterns.classify_from_prices(ev, prices, thresholds=None)
            .iloc[0]["pattern_label"] == "突破型")
    # 1.2x 等效阈值 1.176：9.9 < 11.76 失守突破 → 其他（窗口不足 120 日无横盘档）
    assert (patterns.classify_from_prices(
        ev, prices, thresholds={"breakout_close": 1.176})
        .iloc[0]["pattern_label"] == "其他")
    # 部分覆盖仅改给定键：breakout_box 收紧到 0 → 0 < 0 不成立 → 不再突破
    assert (patterns.classify_from_prices(
        ev, prices, thresholds={"breakout_box": 0.0})
        .iloc[0]["pattern_label"] == "其他")


def test_read_buffered_prices_returns_rows(
    limitup_db: sqlite3.Connection,
) -> None:
    limitup_db.execute(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("600100.SH", "20231229", 10, 11, 10, 11, 10, 10, 1e4, 1e7, 1.0, None),
    )
    limitup_db.commit()
    ev = pd.DataFrame([{"ts_code": "600100.SH", "trade_date": "20240102"}])
    px = patterns.read_buffered_prices(ev, limitup_db, "20240102", "20240110")
    assert list(px["ts_code"]) == ["600100.SH"]
    assert list(px["trade_date"]) == ["20231229"]


def test_attach_kline_and_bands(limitup_db: sqlite3.Connection) -> None:
    limitup_db.execute(
        "INSERT INTO intraday_feature VALUES (?,?,?,?,?,?,?,?,?)",
        ("600100.SH", "20240102", 0.5, 10.0, 1.0, 0.1, 0.2, 0.7, None),
    )
    limitup_db.commit()
    ev = pd.DataFrame([{
        "ts_code": "600100.SH", "trade_date": "20240102",
        "first_seal_time": "143800", "last_seal_time": "145500",
    }])
    out = patterns.attach_kline_features(ev, limitup_db, "20240101", "20240110")
    assert out.iloc[0]["k_body_ratio"] == 0.7
    assert out.iloc[0]["first_seal_band"] == "尾盘"
    assert bool(out.iloc[0]["late_reseal"])


def test_price_buffer_covers_120d_window(
    monkeypatch: MonkeyPatch, limitup_db: sqlite3.Connection
) -> None:
    """read_daily_prices 请求的 start 须比事件最小日期早 ≥190 自然日（120 交易日窗口）."""
    captured: dict[str, str] = {}

    def _fake_read_daily_prices(
        conn: sqlite3.Connection, ts_codes: list[str], start: str, end: str
    ) -> pd.DataFrame:
        captured["start"] = start
        captured["end"] = end
        return pd.DataFrame()

    fake: Callable[..., pd.DataFrame] = _fake_read_daily_prices
    monkeypatch.setattr(patterns.db, "read_daily_prices", fake)
    ev = pd.DataFrame([{
        "ts_code": "600100.SH", "trade_date": "20240102",
        "first_seal_time": "093000", "last_seal_time": "093500",
    }])
    patterns.attach_pattern_features(ev, limitup_db, "20240102", "20240102")

    buffer_start = datetime.strptime(captured["start"], "%Y%m%d")
    min_event = datetime.strptime(str(ev["trade_date"].min()), "%Y%m%d")
    assert (min_event - buffer_start).days >= 190
