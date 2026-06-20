"""TDD tests for OHLCV data loader — mock safe_akshare_call seam."""

from __future__ import annotations

import pandas as pd
import pytest

import stockhot.technical_analyzer.data_loader as dl
from stockhot.technical_analyzer.data_loader import fetch_ohlcv


def _make_akshare_hist_df(rows: list[dict] | None = None) -> pd.DataFrame:
    if rows is None:
        rows = [
            {
                "日期": "2024-01-03",
                "股票代码": "000001",
                "开盘": 10.0,
                "收盘": 10.5,
                "最高": 10.8,
                "最低": 9.8,
                "成交量": 1000000,
                "成交额": 10500000.0,
                "振幅": 10.0,
                "涨跌幅": 5.0,
                "涨跌额": 0.5,
                "换手率": 1.2,
            },
            {
                "日期": "2024-01-02",
                "股票代码": "000001",
                "开盘": 9.5,
                "收盘": 10.0,
                "最高": 10.2,
                "最低": 9.3,
                "成交量": 800000,
                "成交额": 8000000.0,
                "振幅": 9.0,
                "涨跌幅": 5.3,
                "涨跌额": 0.5,
                "换手率": 1.0,
            },
            {
                "日期": "2024-01-04",
                "股票代码": "000001",
                "开盘": 10.5,
                "收盘": 11.0,
                "最高": 11.2,
                "最低": 10.3,
                "成交量": 1200000,
                "成交额": 13200000.0,
                "振幅": 8.5,
                "涨跌幅": 4.8,
                "涨跌额": 0.5,
                "换手率": 1.5,
            },
        ]
    return pd.DataFrame(rows)


class TestFetchOhlcv:
    def test_normal_data_loading(self, monkeypatch):
        mock_df = _make_akshare_hist_df()
        monkeypatch.setattr(dl, "safe_akshare_call", lambda fn, **kw: mock_df)

        result = fetch_ohlcv("000001", "2024-01-01", "2024-01-31")

        assert not result.empty
        assert list(result.columns) == ["open", "high", "low", "close", "volume"]
        assert isinstance(result.index, pd.DatetimeIndex)

    def test_ascending_sort(self, monkeypatch):
        mock_df = _make_akshare_hist_df()
        monkeypatch.setattr(dl, "safe_akshare_call", lambda fn, **kw: mock_df)

        result = fetch_ohlcv("000001", "2024-01-01", "2024-01-31")

        dates = list(result.index)
        assert dates == sorted(dates)
        assert dates[0] == pd.Timestamp("2024-01-02")
        assert dates[-1] == pd.Timestamp("2024-01-04")

    def test_symbol_normalization_suffix(self, monkeypatch):
        captured = {}

        def capture(fn, **kw):
            captured.update(kw)
            return _make_akshare_hist_df()

        monkeypatch.setattr(dl, "safe_akshare_call", capture)

        fetch_ohlcv("000001.SZ", "2024-01-01", "2024-01-31")

        assert captured["symbol"] == "000001"

    def test_symbol_normalization_prefix(self, monkeypatch):
        captured = {}

        def capture(fn, **kw):
            captured.update(kw)
            return _make_akshare_hist_df()

        monkeypatch.setattr(dl, "safe_akshare_call", capture)

        fetch_ohlcv("sh600519", "2024-01-01", "2024-01-31")

        assert captured["symbol"] == "600519"

    def test_symbol_already_clean(self, monkeypatch):
        captured = {}

        def capture(fn, **kw):
            captured.update(kw)
            return _make_akshare_hist_df()

        monkeypatch.setattr(dl, "safe_akshare_call", capture)

        fetch_ohlcv("000001", "2024-01-01", "2024-01-31")

        assert captured["symbol"] == "000001"

    def test_empty_dataframe_graceful(self, monkeypatch):
        monkeypatch.setattr(dl, "safe_akshare_call", lambda fn, **kw: pd.DataFrame())

        result = fetch_ohlcv("000001", "2024-01-01", "2024-01-31")

        assert result.empty
        assert isinstance(result, pd.DataFrame)

    def test_none_return_graceful(self, monkeypatch):
        monkeypatch.setattr(dl, "safe_akshare_call", lambda fn, **kw: None)

        result = fetch_ohlcv("000001", "2024-01-01", "2024-01-31")

        assert result.empty

    def test_column_mapping_values(self, monkeypatch):
        mock_df = _make_akshare_hist_df()
        monkeypatch.setattr(dl, "safe_akshare_call", lambda fn, **kw: mock_df)

        result = fetch_ohlcv("000001", "2024-01-01", "2024-01-31")

        row_jan02 = result.loc[pd.Timestamp("2024-01-02")]
        assert row_jan02["open"] == pytest.approx(9.5)
        assert row_jan02["close"] == pytest.approx(10.0)
        assert row_jan02["high"] == pytest.approx(10.2)
        assert row_jan02["low"] == pytest.approx(9.3)
        assert row_jan02["volume"] == 800000

    def test_date_conversion_to_akshare_format(self, monkeypatch):
        captured = {}

        def capture(fn, **kw):
            captured.update(kw)
            return _make_akshare_hist_df()

        monkeypatch.setattr(dl, "safe_akshare_call", capture)

        fetch_ohlcv("000001", "2024-01-01", "2024-06-30")

        assert captured["start_date"] == "20240101"
        assert captured["end_date"] == "20240630"

    def test_adjust_parameter_passed(self, monkeypatch):
        captured = {}

        def capture(fn, **kw):
            captured.update(kw)
            return _make_akshare_hist_df()

        monkeypatch.setattr(dl, "safe_akshare_call", capture)

        fetch_ohlcv("000001", "2024-01-01", "2024-01-31", adjust="hfq")

        assert captured["adjust"] == "hfq"

    def test_period_is_daily(self, monkeypatch):
        captured = {}

        def capture(fn, **kw):
            captured.update(kw)
            return _make_akshare_hist_df()

        monkeypatch.setattr(dl, "safe_akshare_call", capture)

        fetch_ohlcv("000001", "2024-01-01", "2024-01-31")

        assert captured["period"] == "daily"

    def test_extra_columns_dropped(self, monkeypatch):
        mock_df = _make_akshare_hist_df()
        monkeypatch.setattr(dl, "safe_akshare_call", lambda fn, **kw: mock_df)

        result = fetch_ohlcv("000001", "2024-01-01", "2024-01-31")

        assert "成交额" not in result.columns
        assert "振幅" not in result.columns
        assert "涨跌幅" not in result.columns
        assert "股票代码" not in result.columns
