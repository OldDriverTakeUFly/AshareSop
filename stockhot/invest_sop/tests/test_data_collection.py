"""Unit tests for data collection scripts — mocked API calls, no network."""

import argparse

import pandas as pd
import pytest

from stockhot.invest_sop.utils.db_helpers import upsert_record
from stockhot.storage.database import get_connection

TEST_DATE = "2026-05-13"


# ===================================================================
# overseas_market_data
# ===================================================================

class TestOverseasMarketDataCalc:

    def test_calc_pct_change_basic(self):
        from stockhot.invest_sop.scripts.overseas_market_data import _calc_pct_change

        df = pd.DataFrame({"close": [100.0, 102.5]})
        assert _calc_pct_change(df) == 2.5

    def test_calc_pct_change_single_row_returns_none(self):
        from stockhot.invest_sop.scripts.overseas_market_data import _calc_pct_change

        assert _calc_pct_change(pd.DataFrame({"close": [100.0]})) is None

    def test_calc_pct_change_none_df(self):
        from stockhot.invest_sop.scripts.overseas_market_data import _calc_pct_change

        assert _calc_pct_change(None) is None

    def test_calc_pct_change_zero_previous(self):
        from stockhot.invest_sop.scripts.overseas_market_data import _calc_pct_change

        df = pd.DataFrame({"close": [0.0, 10.0]})
        assert _calc_pct_change(df) is None

    def test_get_last_value_normal(self):
        from stockhot.invest_sop.scripts.overseas_market_data import _get_last_value

        df = pd.DataFrame({"央行中间价": [7.15, 7.21, 7.22]})
        assert _get_last_value(df, "央行中间价") == 7.22

    def test_get_last_value_missing_col(self):
        from stockhot.invest_sop.scripts.overseas_market_data import _get_last_value

        df = pd.DataFrame({"other": [1, 2]})
        assert _get_last_value(df, "央行中间价") is None

    def test_get_last_value_nan(self):
        from stockhot.invest_sop.scripts.overseas_market_data import _get_last_value

        df = pd.DataFrame({"央行中间价": [7.15, float("nan")]})
        assert _get_last_value(df, "央行中间价") is None

    def test_collect_overseas_data_mocked(self, monkeypatch):
        from stockhot.invest_sop.scripts import overseas_market_data

        def fake_call(method_name, **kwargs):
            if method_name == "index_us_stock_sina":
                return pd.DataFrame({"close": [100.0, 102.5]})
            if method_name == "bond_zh_us_rate":
                return pd.DataFrame({"美国国债收益率10年": [4.42, 4.45]})
            if method_name == "currency_boc_sina":
                return pd.DataFrame({"央行中间价": [7.215]})
            if method_name == "futures_foreign_hist":
                return pd.DataFrame({"close": [13500.0, 13450.0]})
            if method_name == "index_option_50etf_qvix":
                return pd.DataFrame({"close": [15.67]})
            return None

        monkeypatch.setattr(overseas_market_data, "_call_akshare", fake_call)
        result = overseas_market_data.collect_overseas_data(TEST_DATE)

        assert "sp500_pct" in result or "dow_pct" in result or "nasdaq_pct" in result


# ===================================================================
# domestic_events
# ===================================================================

class TestDomesticEventsGrading:

    def test_grade_severity_red(self):
        from stockhot.invest_sop.scripts.domestic_events import grade_severity

        assert grade_severity("突发战争爆发") == "🔴"
        assert grade_severity("金融危机来袭") == "🔴"

    def test_grade_severity_orange(self):
        from stockhot.invest_sop.scripts.domestic_events import grade_severity

        assert grade_severity("央行降息通知") == "🟠"
        assert grade_severity("LPR利率调整") == "🟠"

    def test_grade_severity_yellow(self):
        from stockhot.invest_sop.scripts.domestic_events import grade_severity

        assert grade_severity("PMI数据公布") == "🟡"
        assert grade_severity("CPI超预期") == "🟡"

    def test_grade_severity_green(self):
        from stockhot.invest_sop.scripts.domestic_events import grade_severity

        assert grade_severity("普通市场动态") == "🟢"


class TestDomesticEventsCollect:

    def test_restricted_release_above_500y(self, monkeypatch):
        from stockhot.invest_sop.scripts import domestic_events

        monkeypatch.setattr(
            domestic_events, "_call_akshare",
            lambda *a, **kw: pd.DataFrame({
                "解禁时间": ["2026-05-13"],
                "实际解禁市值": [600e8],
                "当日解禁股票家数": [5],
            }),
        )
        events = domestic_events._collect_restricted_release_events(TEST_DATE)
        assert len(events) == 1
        assert "限售股解禁" in events[0]["event_name"]
        assert events[0]["severity"] == "🟡"

    def test_restricted_release_below_500y(self, monkeypatch):
        from stockhot.invest_sop.scripts import domestic_events

        monkeypatch.setattr(
            domestic_events, "_call_akshare",
            lambda *a, **kw: pd.DataFrame({
                "解禁时间": ["2026-05-13"],
                "实际解禁市值": [100e8],
                "当日解禁股票家数": [2],
            }),
        )
        events = domestic_events._collect_restricted_release_events(TEST_DATE)
        assert len(events) == 1
        assert events[0]["severity"] == "🟢"

    def test_restricted_release_empty(self, monkeypatch):
        from stockhot.invest_sop.scripts import domestic_events

        monkeypatch.setattr(
            domestic_events, "_call_akshare",
            lambda **kw: pd.DataFrame(),
        )
        assert domestic_events._collect_restricted_release_events(TEST_DATE) == []

    def test_cls_news_filters_by_date(self, monkeypatch):
        from stockhot.invest_sop.scripts import domestic_events

        monkeypatch.setattr(
            domestic_events, "_call_akshare",
            lambda *a, **kw: pd.DataFrame({
                "标题": ["PMI数据超预期", "旧闻"],
                "内容": ["PMI好于预期", "无关"],
                "发布日期": [TEST_DATE, "2026-05-12"],
            }),
        )
        events = domestic_events._collect_cls_news_events(TEST_DATE)
        assert len(events) == 1
        assert events[0]["event_name"] == "PMI数据超预期"
        assert events[0]["severity"] == "🟡"


# ===================================================================
# futures_sentiment
# ===================================================================

class TestFuturesSentimentCalc:

    def test_calc_pct_change(self):
        from stockhot.invest_sop.scripts.futures_sentiment import _calc_pct_change

        df = pd.DataFrame({"收盘": [3500.0, 3517.5]})
        assert round(_calc_pct_change(df), 2) == 0.50

    def test_calc_pct_change_none(self):
        from stockhot.invest_sop.scripts.futures_sentiment import _calc_pct_change

        assert _calc_pct_change(None) is None

    def test_collect_futures_pct_mocked(self, monkeypatch):
        from stockhot.invest_sop.scripts import futures_sentiment

        monkeypatch.setattr(
            futures_sentiment, "_call_akshare",
            lambda method_name, **kw: pd.DataFrame({"收盘": [3500.0, 3517.5]}),
        )
        result = futures_sentiment._collect_futures_pct(TEST_DATE)
        assert result["if_pct"] is not None
        assert round(result["if_pct"], 2) == 0.50

    def test_collect_futures_pct_handles_failure(self, monkeypatch):
        from stockhot.invest_sop.scripts import futures_sentiment

        monkeypatch.setattr(
            futures_sentiment, "_call_akshare",
            lambda **kw: (_ for _ in ()).throw(ConnectionError("down")),
        )
        result = futures_sentiment._collect_futures_pct(TEST_DATE)
        assert result["if_pct"] is None
        assert result["ic_pct"] is None
        assert result["im_pct"] is None


# ===================================================================
# supply_chain
# ===================================================================

class TestSupplyChainCalc:

    def test_last_close(self):
        from stockhot.invest_sop.scripts.supply_chain import _last_close

        df = pd.DataFrame({"close": [9500.0, 9523.0]})
        assert _last_close(df) == 9523.0

    def test_last_close_empty(self):
        from stockhot.invest_sop.scripts.supply_chain import _last_close

        assert _last_close(pd.DataFrame()) is None
        assert _last_close(None) is None

    def test_last_close_nan(self):
        from stockhot.invest_sop.scripts.supply_chain import _last_close

        df = pd.DataFrame({"close": [9500.0, float("nan")]})
        assert _last_close(df) is None

    def test_collect_lme_metals_mocked(self, monkeypatch):
        from stockhot.invest_sop.scripts import supply_chain

        monkeypatch.setattr(
            supply_chain, "_call_akshare",
            lambda *a, **kw: pd.DataFrame({"close": [9500.0, 9523.0]}),
        )
        records = supply_chain._collect_lme_metals(TEST_DATE)
        assert len(records) == 3
        assert records[0]["sector"] == "有色"
        assert records[0]["value"] == 9523.0

    def test_collect_lme_metals_failure(self, monkeypatch):
        from stockhot.invest_sop.scripts import supply_chain

        monkeypatch.setattr(
            supply_chain, "_call_akshare",
            lambda **kw: (_ for _ in ()).throw(ConnectionError("down")),
        )
        assert supply_chain._collect_lme_metals(TEST_DATE) == []

    def test_collect_bdi_mocked(self, monkeypatch):
        from stockhot.invest_sop.scripts import supply_chain

        monkeypatch.setattr(
            supply_chain, "_call_akshare",
            lambda *a, **kw: pd.DataFrame({"date": ["d1", "d2"], "指数": [1500, 1523]}),
        )
        records = supply_chain._collect_bdi(TEST_DATE)
        assert len(records) == 1
        assert records[0]["metric_name"] == "BDI"
        assert records[0]["value"] == 1523.0


# ===================================================================
# morning_confirm
# ===================================================================

class TestMorningConfirmCalc:

    def test_calc_pct_change(self):
        from stockhot.invest_sop.scripts.morning_confirm import _calc_pct_change

        df = pd.DataFrame({"close": [13450.0, 13420.0]})
        assert round(_calc_pct_change(df), 2) == -0.22

    def test_calc_pct_change_single_row(self):
        from stockhot.invest_sop.scripts.morning_confirm import _calc_pct_change

        assert _calc_pct_change(pd.DataFrame({"close": [100.0]})) is None

    def test_get_latest_close(self):
        from stockhot.invest_sop.scripts.morning_confirm import _get_latest_close

        df = pd.DataFrame({"close": [13450.0, 13480.0]})
        assert _get_latest_close(df) == 13480.0

    def test_get_latest_close_empty(self):
        from stockhot.invest_sop.scripts.morning_confirm import _get_latest_close

        assert _get_latest_close(None) is None
        assert _get_latest_close(pd.DataFrame()) is None


# ===================================================================
# weekly_cycle
# ===================================================================

class TestWeeklyCycleCalc:

    def test_sector_trends_up(self):
        from stockhot.invest_sop.scripts.weekly_cycle import compute_sector_trends

        rows = [
            {"sector": "AI", "metric_name": "m", "value": 100.0},
            {"sector": "AI", "metric_name": "m", "value": 105.0},
        ]
        assert compute_sector_trends(rows)["AI"] == "上行"

    def test_sector_trends_down(self):
        from stockhot.invest_sop.scripts.weekly_cycle import compute_sector_trends

        rows = [
            {"sector": "煤炭", "metric_name": "m", "value": 2000.0},
            {"sector": "煤炭", "metric_name": "m", "value": 1900.0},
        ]
        assert compute_sector_trends(rows)["煤炭"] == "下行"

    def test_sector_trends_neutral(self):
        from stockhot.invest_sop.scripts.weekly_cycle import compute_sector_trends

        rows = [
            {"sector": "化工", "metric_name": "a", "value": 100.0},
            {"sector": "化工", "metric_name": "a", "value": 105.0},
            {"sector": "化工", "metric_name": "b", "value": 200.0},
            {"sector": "化工", "metric_name": "b", "value": 195.0},
        ]
        assert compute_sector_trends(rows)["化工"] == "震荡"

    def test_sector_trends_insufficient_data(self):
        from stockhot.invest_sop.scripts.weekly_cycle import compute_sector_trends

        rows = [{"sector": "AI", "metric_name": "m", "value": 100.0}]
        assert compute_sector_trends(rows)["AI"] == "震荡"

    def test_build_cycle_speed_table(self):
        from stockhot.invest_sop.scripts.weekly_cycle import build_cycle_speed_table

        result = build_cycle_speed_table([{"sector": "AI", "cycle_position": "繁荣", "crowding_score": 7}])
        assert "AI" in result
        assert "繁荣" in result
        assert "7/12" in result

    def test_update_sector_persists(self, temp_db, monkeypatch):
        from stockhot.invest_sop.scripts import weekly_cycle

        monkeypatch.setattr(weekly_cycle, "get_recent_trade_day", lambda: TEST_DATE)
        weekly_cycle.update_sector(argparse.Namespace(
            update_sector="AI", position="复苏", crowding=4, notes="开始复苏",
        ))

        conn = get_connection()
        row = dict(conn.execute("SELECT * FROM invest_cycle_assessments WHERE sector='AI'").fetchone())
        conn.close()
        assert row["cycle_position"] == "复苏"
        assert row["crowding_score"] == 4
