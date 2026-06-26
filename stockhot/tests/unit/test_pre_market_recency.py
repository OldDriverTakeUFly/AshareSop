"""Tests for the pre-market news-recency framework.

Covers:
- ``classify_news_recency`` tier boundaries (T0/T1/T2) — pure function
- ``query_by_date_range`` window + ordering — temp DB
- ``read_recent_overseas_trend`` cumulative/vix/digestion logic — temp DB
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from stockhot.pre_market import (
    classify_news_recency,
    read_recent_overseas_trend,
)
from stockhot.storage import database as db_module
from stockhot.invest_sop.utils import db_helpers


TODAY = date(2026, 6, 26)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Point DB_PATH to a temp file, initialize schema, seed overseas rows."""
    temp_path = tmp_path / "test_recency.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_path)
    db_module.init_database()
    yield temp_path


def _insert_overseas(db_path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        for r in rows:
            conn.execute(
                "INSERT OR REPLACE INTO invest_overseas_market "
                "(date, sp500_pct, nasdaq_pct, dow_pct, us_vix, a50_pct) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    r["date"],
                    r.get("sp500_pct"),
                    r.get("nasdaq_pct"),
                    r.get("dow_pct"),
                    r.get("us_vix"),
                    r.get("a50_pct"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# classify_news_recency — pure function, tier boundaries
# ---------------------------------------------------------------------------


class TestClassifyNewsRecency:
    @pytest.mark.parametrize(
        "days_back, expected_tier, expected_basis",
        [
            (0, "T0", True),   # today
            (1, "T0", True),   # yesterday / just-completed session
            (2, "T1", False),  # D-2
            (3, "T1", False),  # D-3
            (4, "T2", False),  # T2 boundary
            (7, "T2", False),  # a week old
            (30, "T2", False), # very old
        ],
    )
    def test_tier_boundaries(self, days_back, expected_tier, expected_basis):
        event = TODAY - timedelta(days=days_back)
        verdict = classify_news_recency(event, today=TODAY)
        assert verdict.tier == expected_tier
        assert verdict.can_be_today_basis == expected_basis
        assert verdict.days_old == days_back

    def test_accepts_string_date(self):
        verdict = classify_news_recency("2026-06-23", today="2026-06-26")
        assert verdict.tier == "T1"
        assert verdict.days_old == 3

    def test_accepts_datetime(self):
        from datetime import datetime

        verdict = classify_news_recency(
            datetime(2026, 6, 26, 9, 30), today=datetime(2026, 6, 26)
        )
        assert verdict.tier == "T0"

    def test_future_event_clamps_to_t0(self):
        # A future-dated event should not produce negative days_old or break tiers.
        verdict = classify_news_recency(TODAY + timedelta(days=1), today=TODAY)
        assert verdict.days_old == 0
        assert verdict.tier == "T0"

    def test_defaults_today_to_real_date(self):
        # today=None should use the real current date without error.
        verdict = classify_news_recency(date.today())
        assert verdict.tier in {"T0", "T1", "T2"}

    def test_usage_text_mentions_tier_role(self):
        v0 = classify_news_recency(TODAY, today=TODAY)
        v1 = classify_news_recency(TODAY - timedelta(days=3), today=TODAY)
        v2 = classify_news_recency(TODAY - timedelta(days=5), today=TODAY)
        assert "今日重点" in v0.usage
        assert "消化状态" in v1.usage
        assert "背景" in v2.usage


# ---------------------------------------------------------------------------
# query_by_date_range — temp DB, window + ordering
# ---------------------------------------------------------------------------


class TestQueryByDateRange:
    def test_returns_rows_in_window_ascending(self, temp_db):
        _insert_overseas(
            temp_db,
            [
                {"date": "2026-06-20", "sp500_pct": -7.87},
                {"date": "2026-06-23", "sp500_pct": 1.2},
                {"date": "2026-06-24", "sp500_pct": 2.0},
                {"date": "2026-06-25", "sp500_pct": 0.5},
                # outside the default 3-day window ending 06-26:
                {"date": "2026-06-15", "sp500_pct": 0.1},
            ],
        )
        rows = db_helpers.query_by_date_range(
            "invest_overseas_market", end_date="2026-06-26", days_back=3
        )
        dates = [r["date"] for r in rows]
        assert dates == ["2026-06-23", "2026-06-24", "2026-06-25"]
        # ordered ascending
        assert dates == sorted(dates)

    def test_empty_window_returns_empty_list(self, temp_db):
        _insert_overseas(temp_db, [{"date": "2026-06-01", "sp500_pct": 0.1}])
        rows = db_helpers.query_by_date_range(
            "invest_overseas_market", end_date="2026-06-26", days_back=3
        )
        assert rows == []

    def test_rejects_unknown_table(self):
        with pytest.raises(ValueError):
            db_helpers.query_by_date_range("not_a_table", end_date="2026-06-26")

    def test_accepts_date_object(self, temp_db):
        _insert_overseas(temp_db, [{"date": "2026-06-25", "sp500_pct": 0.5}])
        rows = db_helpers.query_by_date_range(
            "invest_overseas_market", end_date=date(2026, 6, 26), days_back=3
        )
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# read_recent_overseas_trend — cumulative, vix trend, digestion hint
# ---------------------------------------------------------------------------


class TestReadRecentOverseasTrend:
    def test_empty_db_returns_unknown_hint(self, temp_db):
        summary = read_recent_overseas_trend(days_back=3, end_date=TODAY)
        assert summary.available_days == 0
        assert summary.sp500_cum is None
        assert "无法判定" in summary.digestion_hint

    def test_cumulative_and_vix_trend(self, temp_db):
        # A sharp drop on 06-23, then recovery -> digested.
        _insert_overseas(
            temp_db,
            [
                {"date": "2026-06-23", "nasdaq_pct": -7.87, "us_vix": 25.0},
                {"date": "2026-06-24", "nasdaq_pct": 3.5, "us_vix": 22.0},
                {"date": "2026-06-25", "nasdaq_pct": 3.0, "us_vix": 20.0},
            ],
        )
        summary = read_recent_overseas_trend(days_back=3, end_date=TODAY)
        assert summary.available_days == 3
        # cumulative nasdaq ≈ -7.87 + 3.5 + 3.0 = -1.37
        assert summary.nasdaq_cum is not None
        assert abs(summary.nasdaq_cum - (-1.37)) < 0.01
        assert summary.vix_latest == 20.0
        assert summary.vix_trend == "falling"
        # latest_sharp_move scans newest-first, so the most recent >=2% day wins
        assert summary.latest_sharp_move_date == "2026-06-25"

    def test_digestion_hint_when_recovered(self, temp_db):
        # Drop then full recovery -> cumulative positive -> digested.
        _insert_overseas(
            temp_db,
            [
                {"date": "2026-06-23", "nasdaq_pct": -4.0, "sp500_pct": -4.0},
                {"date": "2026-06-24", "nasdaq_pct": 4.0, "sp500_pct": 4.0},
                {"date": "2026-06-25", "nasdaq_pct": 2.0, "sp500_pct": 2.0},
            ],
        )
        summary = read_recent_overseas_trend(days_back=3, end_date=TODAY)
        # cumulative ≈ +2.0 -> "消化"
        assert summary.sp500_cum is not None and summary.sp500_cum > 1.0
        assert "消化" in summary.digestion_hint

    def test_digestion_hint_when_intensifying(self, temp_db):
        # Continued decline -> cumulative negative -> intensifying.
        _insert_overseas(
            temp_db,
            [
                {"date": "2026-06-23", "sp500_pct": -3.0, "nasdaq_pct": -3.0},
                {"date": "2026-06-24", "sp500_pct": -2.0, "nasdaq_pct": -2.0},
                {"date": "2026-06-25", "sp500_pct": -1.5, "nasdaq_pct": -1.5},
            ],
        )
        summary = read_recent_overseas_trend(days_back=3, end_date=TODAY)
        assert summary.sp500_cum is not None and summary.sp500_cum < -1.0
        assert "发酵" in summary.digestion_hint

    def test_no_sharp_move_flat_window(self, temp_db):
        _insert_overseas(
            temp_db,
            [
                {"date": "2026-06-23", "sp500_pct": 0.3},
                {"date": "2026-06-24", "sp500_pct": -0.2},
                {"date": "2026-06-25", "sp500_pct": 0.1},
            ],
        )
        summary = read_recent_overseas_trend(days_back=3, end_date=TODAY)
        assert summary.latest_sharp_move_date == ""
        assert "窄幅" in summary.digestion_hint
