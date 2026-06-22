"""TDD tests for report_integration — AI advisor section injection.

Uses temp DB seeded with advisor_runs rows to verify build_advisor_section()
formats recommendations correctly with sentinel markers.
"""

from __future__ import annotations

import json

import pytest

from stockhot.advisor.report_integration import (
    ADVISOR_SECTION_END,
    ADVISOR_SECTION_START,
    build_advisor_section,
)
from stockhot.storage import database as db_module


# ── temp DB fixture ────────────────────────────────────────────────


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    temp_path = tmp_path / "test_advisor_report.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_path)
    db_module.init_database()
    yield temp_path


def _insert_run(
    conn,
    trade_date: str,
    stock_code: str,
    rec_type: str,
    action: str,
    confidence: str,
    reasoning: str = "test",
    entry_zone=None,
    stop_loss=None,
    target=None,
):
    reasoning_json = json.dumps(
        {
            "reasoning": reasoning,
            "entry_zone": list(entry_zone) if entry_zone else None,
            "stop_loss": stop_loss,
            "target": target,
        },
        ensure_ascii=False,
    )
    conn.execute(
        """INSERT INTO advisor_runs
           (trade_date, stock_code, recommendation_type, action, confidence,
            reasoning_json, prompt_version, prompt_tokens, completion_tokens,
            model_name, data_age_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            trade_date,
            stock_code,
            rec_type,
            action,
            confidence,
            reasoning_json,
            "v1",
            100,
            200,
            "test-model",
            json.dumps({}),
        ),
    )
    conn.commit()


# ── sentinel markers ───────────────────────────────────────────────


class TestSentinels:
    def test_start_marker_present(self, temp_db):
        section = build_advisor_section("2025-01-01")
        assert ADVISOR_SECTION_START in section

    def test_end_marker_present(self, temp_db):
        section = build_advisor_section("2025-01-01")
        assert ADVISOR_SECTION_END in section

    def test_start_before_end(self, temp_db):
        section = build_advisor_section("2025-01-01")
        assert section.index(ADVISOR_SECTION_START) < section.index(ADVISOR_SECTION_END)


# ── empty / no recommendations ─────────────────────────────────────


class TestNoRecommendations:
    def test_shows_placeholder_when_empty(self, temp_db):
        section = build_advisor_section("2025-01-01")
        assert "暂无 AI 建议" in section

    def test_sentinels_present_even_when_empty(self, temp_db):
        section = build_advisor_section("2025-01-01")
        assert ADVISOR_SECTION_START in section
        assert ADVISOR_SECTION_END in section

    def test_disclaimer_present_when_empty(self, temp_db):
        section = build_advisor_section("2025-01-01")
        assert "不构成投资建议" in section


# ── recommendations present ────────────────────────────────────────


class TestWithRecommendations:
    def _seed_three_types(self, temp_db):
        conn = db_module.get_connection()
        try:
            _insert_run(
                conn, "2025-01-01", "000001", "build", "buy", "HIGH",
                reasoning="技术强势+基本面良好",
                entry_zone=(10.5, 11.0), stop_loss=9.8, target=13.0,
            )
            _insert_run(
                conn, "2025-01-01", "600519", "clear", "exit", "HIGH",
                reasoning="硬止损触发",
            )
            _insert_run(
                conn, "2025-01-01", "300750", "adjust", "trim", "MEDIUM",
                reasoning="仓位偏重需减仓",
            )
        finally:
            conn.close()

    def test_date_in_header(self, temp_db):
        self._seed_three_types(temp_db)
        section = build_advisor_section("2025-01-01")
        assert "2025-01-01" in section

    def test_build_section_present(self, temp_db):
        self._seed_three_types(temp_db)
        section = build_advisor_section("2025-01-01")
        assert "建仓建议" in section
        assert "000001" in section

    def test_clear_section_present(self, temp_db):
        self._seed_three_types(temp_db)
        section = build_advisor_section("2025-01-01")
        assert "清仓建议" in section
        assert "600519" in section

    def test_adjust_section_present(self, temp_db):
        self._seed_three_types(temp_db)
        section = build_advisor_section("2025-01-01")
        assert "调仓建议" in section
        assert "300750" in section

    def test_disclaimer_present(self, temp_db):
        self._seed_three_types(temp_db)
        section = build_advisor_section("2025-01-01")
        assert "仅供参考" in section
        assert "不构成投资建议" in section

    def test_no_placeholder_when_recs_exist(self, temp_db):
        self._seed_three_types(temp_db)
        section = build_advisor_section("2025-01-01")
        assert "暂无 AI 建议" not in section

    def test_confidence_in_output(self, temp_db):
        self._seed_three_types(temp_db)
        section = build_advisor_section("2025-01-01")
        assert "HIGH" in section

    def test_reasoning_in_output(self, temp_db):
        self._seed_three_types(temp_db)
        section = build_advisor_section("2025-01-01")
        assert "技术强势" in section


# ── t_trade type ───────────────────────────────────────────────────


class TestTTradeRecommendations:
    def test_t_trade_section_present(self, temp_db):
        conn = db_module.get_connection()
        try:
            _insert_run(
                conn, "2025-01-01", "002594", "t_trade", "t_trade", "LOW",
                reasoning="支撑位附近做T",
            )
        finally:
            conn.close()

        section = build_advisor_section("2025-01-01")
        assert "做T建议" in section
        assert "002594" in section
        assert "低置信度" in section


# ── date filtering ─────────────────────────────────────────────────


class TestDateFiltering:
    def test_only_fetches_matching_date(self, temp_db):
        conn = db_module.get_connection()
        try:
            _insert_run(
                conn, "2025-01-01", "000001", "build", "buy", "HIGH",
                reasoning="Jan 1 rec",
            )
            _insert_run(
                conn, "2025-01-02", "000002", "build", "buy", "HIGH",
                reasoning="Jan 2 rec",
            )
        finally:
            conn.close()

        section = build_advisor_section("2025-01-01")
        assert "000001" in section
        assert "000002" not in section
