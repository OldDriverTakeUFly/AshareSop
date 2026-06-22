"""TDD tests for advisor data-source wrappers.

Mocks at MODULE level (where the name is imported, not where defined)
following the project's established pattern from test_data_loader.py.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

import stockhot.advisor.data_sources.fundamental as fundamental
import stockhot.advisor.data_sources.technical as technical
from stockhot.advisor.data_sources.fundamental import (
    clear_pipeline_cache,
    fetch_davis_signal,
    get_current_davis_score,
)
from stockhot.advisor.data_sources.technical import (
    _compute_data_age,
    fetch_realtime_price,
    fetch_technical_signal,
)
from stockhot.advisor.types import UnifiedSignal


# Auto-reset the fundamental pipeline cache before each test so monkeypatching
# run_screening_pipeline actually takes effect (the cache would otherwise
# serve a stale result from a previous test in this module).
@pytest.fixture(autouse=True)
def _reset_davis_cache():
    clear_pipeline_cache()
    yield
    clear_pipeline_cache()


# ── _compute_data_age ──────────────────────────────────────────────


class TestComputeDataAge:
    def test_none_returns_none(self):
        assert _compute_data_age(None) is None

    def test_one_day_ago(self):
        ts = (date.today() - timedelta(days=1)).isoformat()
        assert _compute_data_age(ts) == 1

    def test_seven_days_ago(self):
        ts = (date.today() - timedelta(days=7)).isoformat()
        assert _compute_data_age(ts) == 7

    def test_invalid_format_returns_none(self):
        assert _compute_data_age("not-a-date") is None

    def test_datetime_string_truncated(self):
        ts = (date.today() - timedelta(days=3)).isoformat() + "T15:30:00"
        assert _compute_data_age(ts) == 3


# ── fetch_technical_signal ─────────────────────────────────────────


def _make_mock_score_result(score=72.5, state="强势"):
    return {
        "state": state,
        "score": score,
        "signals": [
            {"name": "ma_arrangement", "weight": 0.30, "hit": True},
            {"name": "rsi", "weight": 0.15, "hit": True},
        ],
    }


def _make_ohlcv_df(days=5):
    dates = pd.date_range(end=date.today(), periods=days, freq="D")
    return pd.DataFrame(
        {
            "open": [10.0] * days,
            "high": [11.0] * days,
            "low": [9.0] * days,
            "close": [10.5] * days,
            "volume": [100000] * days,
        },
        index=dates,
    )


class TestFetchTechnicalSignal:
    def test_wraps_composite_score(self, monkeypatch):
        mock_result = _make_mock_score_result(score=72.5, state="强势")
        monkeypatch.setattr(technical, "composite_technical_score", lambda df: mock_result)
        ohlcv = _make_ohlcv_df()

        signal = fetch_technical_signal("000001", ohlcv)

        assert isinstance(signal, UnifiedSignal)
        assert signal.name == "technical"
        assert signal.value == 72.5
        assert signal.polarity == "higher_is_better"
        assert signal.source == "technical_analyzer"
        assert signal.details["state"] == "强势"
        assert "signals" in signal.details

    def test_data_timestamp_from_last_index(self, monkeypatch):
        mock_result = _make_mock_score_result()
        monkeypatch.setattr(technical, "composite_technical_score", lambda df: mock_result)
        ohlcv = _make_ohlcv_df()

        signal = fetch_technical_signal("000001", ohlcv)

        expected_ts = str(ohlcv.index[-1])[:10]
        assert signal.data_timestamp == expected_ts

    def test_data_age_days_calculated(self, monkeypatch):
        mock_result = _make_mock_score_result()
        monkeypatch.setattr(technical, "composite_technical_score", lambda df: mock_result)
        old_date = date.today() - timedelta(days=5)
        ohlcv = pd.DataFrame(
            {"open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5], "volume": [1000]},
            index=pd.DatetimeIndex([old_date]),
        )

        signal = fetch_technical_signal("000001", ohlcv)

        assert signal.data_age_days == 5

    def test_empty_ohlcv_returns_neutral(self):
        signal = fetch_technical_signal("000001", pd.DataFrame())

        assert signal.value == 50.0
        assert signal.data_age_days is None
        assert signal.data_timestamp is None
        assert signal.details == {"error": "empty_ohlcv"}

    def test_none_ohlcv_returns_neutral(self):
        signal = fetch_technical_signal("000001", None)  # type: ignore[arg-type]

        assert signal.value == 50.0
        assert signal.details == {"error": "empty_ohlcv"}

    def test_calls_composite_technical_score(self, monkeypatch):
        called_with = {}

        def mock_fn(df):
            called_with["df"] = df
            return _make_mock_score_result()

        monkeypatch.setattr(technical, "composite_technical_score", mock_fn)
        ohlcv = _make_ohlcv_df()

        fetch_technical_signal("000001", ohlcv)

        assert called_with["df"] is ohlcv


# ── fetch_realtime_price ───────────────────────────────────────────


def _make_spot_df(code="000001", price=10.5, change=2.3, volume=999000):
    return pd.DataFrame(
        [
            {
                "代码": code,
                "名称": "TestStock",
                "最新价": price,
                "涨跌幅": change,
                "成交量": volume,
            },
            {
                "代码": "600519",
                "名称": "OtherStock",
                "最新价": 1800.0,
                "涨跌幅": -1.0,
                "成交量": 5000,
            },
        ]
    )


class TestFetchRealtimePrice:
    def test_extracts_correct_fields(self, monkeypatch):
        mock_df = _make_spot_df(code="000001", price=10.5, change=2.3, volume=999000)
        monkeypatch.setattr(technical, "safe_akshare_call", lambda fn, *a, **kw: mock_df)

        result = fetch_realtime_price("000001")

        assert result["code"] == "000001"
        assert result["current_price"] == 10.5
        assert result["change_pct"] == 2.3
        assert result["volume"] == 999000.0
        assert "timestamp" in result

    def test_filters_by_code(self, monkeypatch):
        mock_df = _make_spot_df()
        monkeypatch.setattr(technical, "safe_akshare_call", lambda fn, *a, **kw: mock_df)

        result = fetch_realtime_price("600519")

        assert result["code"] == "600519"
        assert result["current_price"] == 1800.0

    def test_empty_df_returns_none_price(self, monkeypatch):
        monkeypatch.setattr(technical, "safe_akshare_call", lambda fn, *a, **kw: pd.DataFrame())

        result = fetch_realtime_price("000001")

        assert result["current_price"] is None
        assert result["code"] == "000001"

    def test_none_df_returns_none_price(self, monkeypatch):
        monkeypatch.setattr(technical, "safe_akshare_call", lambda fn, *a, **kw: None)

        result = fetch_realtime_price("000001")

        assert result["current_price"] is None

    def test_code_not_in_df_returns_none_price(self, monkeypatch):
        mock_df = _make_spot_df(code="000001")
        monkeypatch.setattr(technical, "safe_akshare_call", lambda fn, *a, **kw: mock_df)

        result = fetch_realtime_price("999999")

        assert result["current_price"] is None


# ── get_current_davis_score ────────────────────────────────────────


def _make_pipeline_result_with_stock(
    ts_code="000001.SZ", final_score=78.5, distress=65.0, rank=3, total=30
):
    from davis_analyzer.types import DavisDoubleScore, PipelineResult

    scores = []
    for i in range(1, total + 1):
        if i == rank:
            scores.append(
                DavisDoubleScore(
                    ts_code=ts_code,
                    name="TestStock",
                    valuation_score=70.0,
                    prosperity_score=80.0,
                    distress_score=distress,
                    final_score=final_score,
                    rank=rank,
                    trend_score=60.0,
                )
            )
        else:
            filler_num = 900000 + i
            scores.append(
                DavisDoubleScore(
                    ts_code=f"{filler_num:06d}.SZ",
                    name=f"Stock{i}",
                    valuation_score=55.0,
                    prosperity_score=60.0,
                    distress_score=50.0,
                    final_score=55.0 + i,
                    rank=i,
                    trend_score=50.0,
                )
            )
    return PipelineResult(
        scores=scores,
        stock_infos={},
        valuation_data={},
        prosperity_scores={},
        distress_signals={},
        financial_data={},
    )


class TestGetCurrentDavisScore:
    def test_returns_score_for_stock(self, monkeypatch):
        mock_result = _make_pipeline_result_with_stock(
            ts_code="000001.SZ", final_score=78.5, distress=65.0, rank=3, total=30
        )
        monkeypatch.setattr(fundamental, "run_screening_pipeline", lambda **kw: mock_result)

        result = get_current_davis_score("000001")

        assert result["final_score"] == 78.5
        assert result["distress_score"] == 65.0
        assert "percentile_rank" in result
        assert "data_date" in result
        assert "error" not in result

    def test_code_conversion_sh_600(self, monkeypatch):
        captured = {}

        def mock_pipeline(**kw):
            mock_result = _make_pipeline_result_with_stock(
                ts_code="600519.SH", final_score=90.0, distress=70.0, rank=1, total=10
            )
            captured["result"] = mock_result
            return mock_result

        monkeypatch.setattr(fundamental, "run_screening_pipeline", mock_pipeline)

        result = get_current_davis_score("600519")

        assert result["final_score"] == 90.0

    def test_stock_not_found_returns_no_data(self, monkeypatch):
        mock_result = _make_pipeline_result_with_stock(ts_code="000001.SZ", rank=1, total=5)
        monkeypatch.setattr(fundamental, "run_screening_pipeline", lambda **kw: mock_result)

        result = get_current_davis_score("999999")

        assert result["final_score"] == 50.0
        assert result["percentile_rank"] == 50.0
        assert result["distress_score"] == 0.0
        assert result["data_date"] is None
        assert result["error"] == "no_data"

    def test_empty_scores_returns_no_data(self, monkeypatch):
        from davis_analyzer.types import PipelineResult

        empty_result = PipelineResult(
            scores=[],
            stock_infos={},
            valuation_data={},
            prosperity_scores={},
            distress_signals={},
            financial_data={},
        )
        monkeypatch.setattr(fundamental, "run_screening_pipeline", lambda **kw: empty_result)

        result = get_current_davis_score("000001")

        assert result["error"] == "no_data"

    def test_pipeline_exception_returns_no_data(self, monkeypatch):
        def mock_pipeline(**kw):
            raise RuntimeError("tushare unavailable")

        monkeypatch.setattr(fundamental, "run_screening_pipeline", mock_pipeline)

        result = get_current_davis_score("000001")

        assert result["error"] == "no_data"

    def test_percentile_rank_decreases_with_rank(self, monkeypatch):
        mock_result = _make_pipeline_result_with_stock(ts_code="000001.SZ", rank=1, total=10)
        monkeypatch.setattr(fundamental, "run_screening_pipeline", lambda **kw: mock_result)

        result_rank1 = get_current_davis_score("000001")

        # The pipeline cache holds the rank=1 result; clear it before
        # swapping in the rank=10 mock so the second lookup actually
        # re-invokes the (patched) pipeline.
        clear_pipeline_cache()
        mock_result2 = _make_pipeline_result_with_stock(ts_code="000001.SZ", rank=10, total=10)
        monkeypatch.setattr(fundamental, "run_screening_pipeline", lambda **kw: mock_result2)

        result_rank10 = get_current_davis_score("000001")

        assert result_rank1["percentile_rank"] > result_rank10["percentile_rank"]


# ── fetch_davis_signal ─────────────────────────────────────────────


class TestFetchDavisSignal:
    def test_returns_unified_signal(self, monkeypatch):
        monkeypatch.setattr(
            fundamental,
            "get_current_davis_score",
            lambda code: {
                "final_score": 78.5,
                "percentile_rank": 90.0,
                "distress_score": 65.0,
                "data_date": "2024-06-20",
            },
        )

        signal = fetch_davis_signal("000001")

        assert isinstance(signal, UnifiedSignal)
        assert signal.name == "davis"
        assert signal.value == 78.5
        assert signal.source == "davis_analyzer"

    def test_polarity_higher_is_better(self, monkeypatch):
        monkeypatch.setattr(
            fundamental,
            "get_current_davis_score",
            lambda code: {
                "final_score": 78.5,
                "percentile_rank": 90.0,
                "distress_score": 65.0,
                "data_date": "2024-06-20",
            },
        )

        signal = fetch_davis_signal("000001")

        assert signal.polarity == "higher_is_better"

    def test_distress_in_details_not_value(self, monkeypatch):
        monkeypatch.setattr(
            fundamental,
            "get_current_davis_score",
            lambda code: {
                "final_score": 78.5,
                "percentile_rank": 90.0,
                "distress_score": 65.0,
                "data_date": "2024-06-20",
            },
        )

        signal = fetch_davis_signal("000001")

        assert signal.details["distress_score"] == 65.0
        assert signal.details["percentile_rank"] == 90.0
        assert signal.value == 78.5

    def test_no_data_fallback(self, monkeypatch):
        monkeypatch.setattr(
            fundamental,
            "get_current_davis_score",
            lambda code: {
                "final_score": 50.0,
                "percentile_rank": 50.0,
                "distress_score": 0.0,
                "data_date": None,
                "error": "no_data",
            },
        )

        signal = fetch_davis_signal("000001")

        assert signal.value == 50.0
        assert signal.data_age_days is None
        assert signal.details.get("error") == "no_data"

    def test_data_age_days_from_data_date(self, monkeypatch):
        old_date = (date.today() - timedelta(days=10)).isoformat()
        monkeypatch.setattr(
            fundamental,
            "get_current_davis_score",
            lambda code: {
                "final_score": 70.0,
                "percentile_rank": 80.0,
                "distress_score": 55.0,
                "data_date": old_date,
            },
        )

        signal = fetch_davis_signal("000001")

        assert signal.data_age_days == 10
