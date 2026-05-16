"""Integration tests for invest_sop — full pipeline validation.

Covers five scenarios:
1. Empty DB → generate report → '数据不可用' appears, no crash
2. Single day full flow → data in all 6 tables → report contains data
3. Holdings CRUD + report linkage
4. Non-trading day skip
5. Duplicate run idempotency (UNIQUE constraints)
"""

import argparse

import pytest

from stockhot.invest_sop.utils.db_helpers import query_by_date, upsert_record
from stockhot.invest_sop.scripts.generate_premarket_report import (
    generate_report,
    generate_template,
)
from stockhot.invest_sop import holdings_cli
from stockhot.storage.database import get_connection

TEST_DATE = "2026-05-13"


# ---------------------------------------------------------------------------
# Helpers to seed test data into invest_ tables
# ---------------------------------------------------------------------------

def _insert_overseas(date: str) -> None:
    upsert_record("invest_overseas_market", {
        "date": date,
        "sp500_pct": 0.52,
        "nasdaq_pct": 1.23,
        "dow_pct": -0.15,
        "us_10y": 4.4521,
        "us_10y_change_bp": 3.2,
        "vix": 15.67,
        "a50_pct": -0.34,
        "usd_cny": 7.2150,
    }, unique_keys=["date"])


def _insert_events(date: str) -> None:
    upsert_record("invest_domestic_events", {
        "date": date,
        "event_name": "LPR利率公布",
        "affected_sector": "银行",
        "impact_direction": "偏多",
        "severity": "🟠",
        "source": "manual",
    }, unique_keys=["date", "event_name"])


def _insert_futures(date: str) -> None:
    upsert_record("invest_futures_sentiment", {
        "date": date,
        "if_pct": 0.45,
        "ic_pct": -0.23,
        "im_pct": 0.12,
        "if_basis": -0.35,
        "ic_basis": -0.28,
        "northbound_net": 35.67,
        "margin_balance": 15234.5,
        "put_call_ratio": 0.82,
    }, unique_keys=["date"])


def _insert_morning(date: str) -> None:
    upsert_record("invest_morning_data", {
        "date": date,
        "a50_morning_pct": -0.15,
        "nikkei_pct": 0.67,
        "kospi_pct": -0.23,
        "usd_cny_morning": 7.2100,
        "notes": "vs overnight: A50Δ=0.19%",
    }, unique_keys=["date"])


def _insert_cycle() -> None:
    upsert_record("invest_cycle_assessments", {
        "sector": "AI",
        "cycle_position": "繁荣",
        "crowding_score": 7,
        "assessment_date": "2026-05-12",
        "notes": "高景气度持续",
    }, unique_keys=["sector"])


def _insert_holding() -> None:
    upsert_record("invest_holdings", {
        "code": "688256",
        "name": "寒武纪",
        "sector": "AI",
        "entry_price": 250.00,
        "current_price": 268.50,
        "stop_loss_logic": 220.00,
        "stop_loss_technical": 235.00,
        "stop_loss_hard": 200.00,
        "target_price": 320.00,
        "position_pct": 10.0,
        "entry_date": "2026-04-15",
        "status": "active",
        "notes": "AI芯片龙头",
    }, unique_keys=[])


def _seed_all(date: str) -> None:
    _insert_overseas(date)
    _insert_events(date)
    _insert_futures(date)
    _insert_morning(date)
    _insert_cycle()
    _insert_holding()


# ===================================================================
# Scenario 1: Empty DB → full flow
# ===================================================================

class TestEmptyDBFullFlow:

    def test_generate_report_no_crash(self, temp_db):
        report = generate_report(TEST_DATE)
        assert "盘前SOP报告" in report
        assert TEST_DATE in report
        assert "数据不可用" in report

    def test_generate_template_no_crash(self, temp_db):
        report = generate_template(TEST_DATE)
        assert "盘前SOP报告" in report
        assert "数据不可用" in report
        assert "无活跃持仓" in report


# ===================================================================
# Scenario 2: Single day complete flow
# ===================================================================

class TestSingleDayFullFlow:

    def test_report_contains_data_from_all_tables(self, temp_db):
        _seed_all(TEST_DATE)
        report = generate_report(TEST_DATE)

        # Section 1.1 — overseas market
        assert "标普" in report
        assert "+0.52%" in report
        assert "+1.23%" in report
        assert "-0.15%" in report
        assert "4.4521" in report
        assert "+3.2bp" in report
        assert "15.67" in report
        assert "-0.34%" in report
        assert "7.215" in report

        # Section 1.1 — futures
        assert "股指期货" in report
        assert "+0.45%" in report

        # Section 1.2 — events
        assert "LPR利率公布" in report
        assert "偏多" in report
        assert "🟠" in report

        # Section 2 — cycle assessments
        assert "繁荣" in report
        assert "7/12" in report

        # Section 3 — holdings
        assert "寒武纪" in report
        assert "688256" in report

    def test_overseas_data_renders(self, temp_db):
        _insert_overseas(TEST_DATE)
        report = generate_report(TEST_DATE)
        assert "海外市场" in report
        assert "美债10Y" in report
        assert "VIX" in report
        assert "A50夜盘" in report
        assert "USD/CNY" in report

    def test_events_table_renders(self, temp_db):
        _insert_events(TEST_DATE)
        report = generate_report(TEST_DATE)
        assert "重大事件" in report
        assert "LPR利率公布" in report

    def test_futures_data_renders(self, temp_db):
        _insert_futures(TEST_DATE)
        report = generate_report(TEST_DATE)
        assert "股指期货" in report


# ===================================================================
# Scenario 3: Holdings CRUD + report linkage
# ===================================================================

class TestHoldingsCRUDAndReport:

    def _add_holding(self, **overrides):
        defaults = dict(
            code="688256", name="寒武纪", sector="AI",
            price=250.00, stop_loss_logic=220.00,
            target=320.00, position_pct=10.0,
            stop_loss_hard=None, stop_loss_technical=None,
        )
        defaults.update(overrides)
        holdings_cli.cmd_add(argparse.Namespace(**defaults))

    def test_add_holding_appears_in_report(self, temp_db):
        self._add_holding()

        conn = get_connection()
        rows = [dict(r) for r in conn.execute("SELECT * FROM invest_holdings WHERE status='active'")]
        conn.close()
        assert len(rows) == 1
        assert rows[0]["name"] == "寒武纪"

        report = generate_report(TEST_DATE)
        assert "寒武纪" in report
        assert "688256" in report

    def test_remove_holding_disappears_from_report(self, temp_db):
        self._add_holding()
        holdings_cli.cmd_remove(argparse.Namespace(id=1))

        report = generate_report(TEST_DATE)
        assert "无活跃持仓" in report

    def test_update_price_reflected_in_report(self, temp_db):
        self._add_holding()
        holdings_cli.cmd_update_price(argparse.Namespace(id=1, price=275.00))

        conn = get_connection()
        row = conn.execute("SELECT current_price FROM invest_holdings WHERE id=1").fetchone()
        conn.close()
        assert row["current_price"] == 275.00

        report = generate_report(TEST_DATE)
        assert "寒武纪" in report


# ===================================================================
# Scenario 4: Non-trading day skip
# ===================================================================

class TestNonTradingDay:

    def test_weekend_not_trading_day(self, monkeypatch):
        from stockhot.invest_sop.utils import trading_calendar

        monkeypatch.setattr(trading_calendar, "_trade_dates", {
            "2026-05-11", "2026-05-12", "2026-05-13",
            "2026-05-14", "2026-05-15",
        })
        assert trading_calendar.is_trading_day("2026-05-16") is False
        assert trading_calendar.is_trading_day("2026-05-17") is False

    def test_known_weekday_is_trading_day(self, monkeypatch):
        from stockhot.invest_sop.utils import trading_calendar

        monkeypatch.setattr(trading_calendar, "_trade_dates", {"2026-05-13"})
        assert trading_calendar.is_trading_day("2026-05-13") is True

    def test_holiday_weekday_not_trading_day(self, monkeypatch):
        from stockhot.invest_sop.utils import trading_calendar

        monkeypatch.setattr(trading_calendar, "_trade_dates", {
            "2026-05-11", "2026-05-13",
        })
        assert trading_calendar.is_trading_day("2026-05-12") is False


# ===================================================================
# Scenario 5: Duplicate run idempotency
# ===================================================================

class TestIdempotency:

    def test_overseas_upsert_twice_keeps_one(self, temp_db):
        data = {"date": TEST_DATE, "sp500_pct": 0.52, "nasdaq_pct": 1.23}
        upsert_record("invest_overseas_market", data, unique_keys=["date"])
        data["sp500_pct"] = 0.55
        upsert_record("invest_overseas_market", data, unique_keys=["date"])

        rows = query_by_date("invest_overseas_market", TEST_DATE)
        assert len(rows) == 1
        assert rows[0]["sp500_pct"] == 0.55

    def test_event_upsert_twice_keeps_one(self, temp_db):
        data = {
            "date": TEST_DATE, "event_name": "LPR利率公布",
            "affected_sector": "银行", "severity": "🟠",
        }
        upsert_record("invest_domestic_events", data, unique_keys=["date", "event_name"])
        data["severity"] = "🔴"
        upsert_record("invest_domestic_events", data, unique_keys=["date", "event_name"])

        rows = query_by_date("invest_domestic_events", TEST_DATE)
        assert len(rows) == 1
        assert rows[0]["severity"] == "🔴"

    def test_futures_upsert_twice_keeps_one(self, temp_db):
        data = {"date": TEST_DATE, "if_pct": 0.45, "ic_pct": -0.23}
        upsert_record("invest_futures_sentiment", data, unique_keys=["date"])
        data["if_pct"] = 0.50
        upsert_record("invest_futures_sentiment", data, unique_keys=["date"])

        rows = query_by_date("invest_futures_sentiment", TEST_DATE)
        assert len(rows) == 1
        assert rows[0]["if_pct"] == 0.50

    def test_supply_chain_upsert_twice_keeps_one(self, temp_db):
        data = {
            "date": TEST_DATE, "sector": "有色", "metric_name": "LME铜",
            "value": 9523.0, "unit": "USD/t", "source": "test",
        }
        upsert_record("invest_supply_chain", data, unique_keys=["date", "sector", "metric_name"])
        data["value"] = 9600.0
        upsert_record("invest_supply_chain", data, unique_keys=["date", "sector", "metric_name"])

        conn = get_connection()
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM invest_supply_chain WHERE date=? AND sector=? AND metric_name=?",
            (TEST_DATE, "有色", "LME铜"),
        )]
        conn.close()
        assert len(rows) == 1
        assert rows[0]["value"] == 9600.0

    def test_cycle_upsert_twice_keeps_one(self, temp_db):
        data = {"sector": "AI", "cycle_position": "繁荣", "crowding_score": 7}
        upsert_record("invest_cycle_assessments", data, unique_keys=["sector"])
        data["crowding_score"] = 8
        upsert_record("invest_cycle_assessments", data, unique_keys=["sector"])

        conn = get_connection()
        rows = [dict(r) for r in conn.execute("SELECT * FROM invest_cycle_assessments WHERE sector='AI'")]
        conn.close()
        assert len(rows) == 1
        assert rows[0]["crowding_score"] == 8
