"""TDD tests for recommendation engine.

Mocks LLM provider at function level; uses temp DB for persist tests.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from stockhot.advisor.conflict_resolver import ArbitrationResult
from stockhot.advisor.exceptions import LLMUnavailableError
from stockhot.advisor.llm_provider import LLMResponse
from stockhot.advisor.prompts.registry import default_registry
from stockhot.advisor.recommendation_engine import (
    IdempotencyError,
    Recommendation,
    _check_t_trade_conditions,
    _hallucination_check,
    _map_action_to_type,
    generate_recommendation,
    persist_recommendation,
    run_for_stock,
)
from stockhot.advisor.signal_aggregator import AggregatedSignals
from stockhot.advisor.types import UnifiedSignal
from stockhot.storage import database as db_module


@dataclass
class FakeProvider:
    response_content: str = '{"action": "buy", "confidence": "HIGH", "entry_zone": [10.5, 11.0], "stop_loss": 9.5, "target": 15.0, "reasoning": "Strong fundamentals"}'
    model: str = "test-model"
    raises: Exception | None = None

    def complete(self, prompt, system="", max_tokens=800, temperature=0.3):
        if self.raises:
            raise self.raises
        return LLMResponse(
            content=self.response_content,
            prompt_tokens=100,
            completion_tokens=200,
            model=self.model,
            latency_ms=50,
        )


def _make_signal(name="technical", value=50.0, source="technical_analyzer", details=None) -> UnifiedSignal:
    return UnifiedSignal(
        name=name,
        value=value,
        polarity="higher_is_better",
        data_timestamp=None,
        data_age_days=None,
        source=source,
        details=details or {},
    )


def _make_agg(
    tech_score=50.0,
    davis_score=50.0,
    current_price=12.0,
    sell_signals=None,
    tech_details=None,
) -> AggregatedSignals:
    return AggregatedSignals(
        code="000001",
        technical=_make_signal("technical", tech_score, details=tech_details or {}),
        davis=_make_signal("davis", davis_score, source="davis_analyzer"),
        realtime_price={"code": "000001", "current_price": current_price, "change_pct": 2.0, "volume": 100000, "timestamp": "2025-01-01"},
        sell_signals=sell_signals or [],
        data_freshness={},
    )


def _make_arb(action="HOLD", scenario="NEUTRAL") -> ArbitrationResult:
    return ArbitrationResult(
        primary_signal="none",
        action=action,
        reasoning="test",
        conflict_notes="",
        scenario=scenario,
    )


# ── generate_recommendation — normal flow ──────────────────────────


class TestGenerateBuild:
    def test_build_recommendation_parsed(self):
        agg = _make_agg(tech_score=35, davis_score=80, current_price=12.0)
        arb = _make_arb(action="BUY")
        provider = FakeProvider()

        rec = generate_recommendation("000001", agg, arb, default_registry, provider=provider)

        assert rec.recommendation_type == "build"
        assert rec.action == "buy"
        assert rec.confidence == "HIGH"
        assert rec.entry_zone == (10.5, 11.0)
        assert rec.stop_loss == 9.5
        assert rec.target == 15.0
        assert rec.prompt_tokens == 100
        assert rec.completion_tokens == 200
        assert rec.model_name == "test-model"

    def test_hold_no_holding_returns_none_type(self):
        agg = _make_agg()
        arb = _make_arb(action="HOLD")
        provider = FakeProvider()

        rec = generate_recommendation("000001", agg, arb, default_registry, provider=provider, holding=None)

        assert rec.recommendation_type == "none"
        assert rec.action == "NO_ACTION"

    def test_exit_maps_to_clear(self):
        agg = _make_agg(current_price=8.0, sell_signals=[
            {"triggered": True, "signal_type": "hard_stop", "details": {}}
        ])
        arb = _make_arb(action="EXIT")
        provider = FakeProvider(
            response_content='{"action": "exit", "urgency": "HIGH", "reasoning": "stop hit"}'
        )

        rec = generate_recommendation("000001", agg, arb, default_registry, provider=provider)

        assert rec.recommendation_type == "clear"
        assert rec.action == "exit"

    def test_trim_maps_to_adjust(self):
        agg = _make_agg(current_price=15.0, sell_signals=[
            {"triggered": True, "signal_type": "target_reached", "details": {}}
        ])
        arb = _make_arb(action="TRIM")
        provider = FakeProvider(
            response_content='{"action": "trim", "trim_pct": 0.5, "reasoning": "target"}'
        )
        holding = {"position_pct": 8.0, "avg_cost": 10.0, "stop_loss_hard": 8.0}

        rec = generate_recommendation("000001", agg, arb, default_registry, provider=provider, holding=holding)

        assert rec.recommendation_type == "adjust"


# ── LLM unavailable ────────────────────────────────────────────────


class TestLLMUnavailable:
    def test_unavailable_returns_no_action(self):
        agg = _make_agg(tech_score=35, davis_score=80)
        arb = _make_arb(action="BUY")
        provider = FakeProvider(raises=LLMUnavailableError("network down"))

        rec = generate_recommendation("000001", agg, arb, default_registry, provider=provider)

        assert rec.action == "NO_ACTION"
        assert rec.confidence == "LOW"
        assert "unavailable" in rec.reasoning.lower()

    def test_unavailable_never_fabricates(self):
        agg = _make_agg(tech_score=35, davis_score=80)
        arb = _make_arb(action="BUY")
        provider = FakeProvider(raises=LLMUnavailableError("error"))

        rec = generate_recommendation("000001", agg, arb, default_registry, provider=provider)

        assert rec.entry_zone is None
        assert rec.stop_loss is None
        assert rec.target is None


# ── Hallucination guard ────────────────────────────────────────────


class TestHallucinationGuard:
    def test_unit_function_detects_outrange_entry(self):
        parsed = {"entry_zone": [999, 1000]}
        assert _hallucination_check(parsed, 12.0) is not None

    def test_unit_function_passes_valid_entry(self):
        parsed = {"entry_zone": [11.0, 12.0]}
        assert _hallucination_check(parsed, 12.0) is None

    def test_unit_function_detects_bad_stop_loss(self):
        parsed = {"stop_loss": -5.0}
        assert _hallucination_check(parsed, 12.0) is not None

    def test_unit_function_detects_bad_target(self):
        parsed = {"target": 9999.0}
        assert _hallucination_check(parsed, 12.0) is not None

    def test_integration_downgrades_confidence(self):
        agg = _make_agg(tech_score=35, davis_score=80, current_price=12.0)
        arb = _make_arb(action="BUY")
        provider = FakeProvider(
            response_content='{"action": "buy", "confidence": "HIGH", "entry_zone": [999, 1000], "stop_loss": 9.5, "target": 15.0, "reasoning": "test"}'
        )

        rec = generate_recommendation("000001", agg, arb, default_registry, provider=provider)

        assert rec.confidence == "LOW"
        assert "HALLUCINATION" in rec.reasoning

    def test_none_price_skips_check(self):
        parsed = {"entry_zone": [999, 1000]}
        assert _hallucination_check(parsed, None) is None


# ── 做T logic ──────────────────────────────────────────────────────


class TestTTrade:
    def _make_t_agg(self, price=9.9, support=9.8, vol_ratio=1.5):
        return _make_agg(
            current_price=price,
            tech_details={
                "support_levels": [support],
                "resistance_levels": [11.0],
                "volume_ratio": vol_ratio,
                "volume_trend": "increasing",
            },
        )

    def test_check_conditions_triggers(self):
        agg = self._make_t_agg(price=9.9, support=9.8, vol_ratio=1.5)
        holding = {"position_pct": 5.0}
        assert _check_t_trade_conditions(agg, holding) is True

    def test_check_conditions_no_holding(self):
        agg = self._make_t_agg()
        assert _check_t_trade_conditions(agg, None) is False

    def test_check_conditions_far_from_support(self):
        agg = self._make_t_agg(price=15.0, support=9.8, vol_ratio=1.5)
        holding = {"position_pct": 5.0}
        assert _check_t_trade_conditions(agg, holding) is False

    def test_check_conditions_low_volume_ratio(self):
        agg = self._make_t_agg(price=9.9, support=9.8, vol_ratio=1.0)
        holding = {"position_pct": 5.0}
        assert _check_t_trade_conditions(agg, holding) is False

    def test_t_trade_always_low_confidence(self):
        agg = self._make_t_agg(price=9.9, support=9.8, vol_ratio=1.5)
        arb = _make_arb(action="HOLD")
        provider = FakeProvider(
            response_content='{"action": "swing_buy", "confidence": "HIGH", "intraday_buy_zone": [9.7, 9.9], "intraday_sell_zone": [10.2, 10.5], "disclaimer": "test"}'
        )
        holding = {"position_pct": 5.0}

        rec = generate_recommendation("000001", agg, arb, default_registry, provider=provider, holding=holding)

        assert rec.recommendation_type == "t_trade"
        assert rec.confidence == "LOW"
        assert "做T" in rec.reasoning

    def test_t_trade_priority_over_adjust(self):
        agg = self._make_t_agg(price=9.9, support=9.8, vol_ratio=1.5)
        arb = _make_arb(action="HOLD")
        provider = FakeProvider(
            response_content='{"action": "swing_buy", "confidence": "LOW", "disclaimer": "t"}'
        )
        holding = {"position_pct": 5.0}

        rec = generate_recommendation("000001", agg, arb, default_registry, provider=provider, holding=holding)

        assert rec.recommendation_type == "t_trade"

    def test_no_t_trade_when_price_far(self):
        agg = self._make_t_agg(price=15.0, support=9.8, vol_ratio=1.5)
        arb = _make_arb(action="HOLD")
        provider = FakeProvider()
        holding = {"position_pct": 5.0}

        rec = generate_recommendation("000001", agg, arb, default_registry, provider=provider, holding=holding)

        assert rec.recommendation_type != "t_trade"


# ── action mapping ─────────────────────────────────────────────────


class TestActionMapping:
    def test_exit_maps_to_clear(self):
        agg = _make_agg()
        arb = _make_arb(action="EXIT")
        assert _map_action_to_type(arb, agg, None) == "clear"

    def test_trim_maps_to_adjust(self):
        agg = _make_agg()
        arb = _make_arb(action="TRIM")
        assert _map_action_to_type(arb, agg, None) == "adjust"

    def test_buy_maps_to_build(self):
        agg = _make_agg()
        arb = _make_arb(action="BUY")
        assert _map_action_to_type(arb, agg, None) == "build"

    def test_hold_no_holding_maps_none(self):
        agg = _make_agg()
        arb = _make_arb(action="HOLD")
        assert _map_action_to_type(arb, agg, None) is None

    def test_hold_with_holding_maps_adjust(self):
        agg = _make_agg()
        arb = _make_arb(action="HOLD")
        holding = {"position_pct": 5.0}
        assert _map_action_to_type(arb, agg, holding) == "adjust"


# ── persist_recommendation ─────────────────────────────────────────


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    temp_path = tmp_path / "test_recomm.db"
    monkeypatch.setattr(db_module, "DB_PATH", temp_path)
    db_module.init_database()
    yield temp_path


class TestPersist:
    def _make_rec(self, rec_type="build") -> Recommendation:
        return Recommendation(
            code="000001",
            recommendation_type=rec_type,
            action="buy",
            confidence="HIGH",
            reasoning="test reasoning",
            prompt_version="v1",
            prompt_tokens=100,
            completion_tokens=200,
            model_name="test-model",
        )

    def test_insert_returns_rowid(self, temp_db):
        rec = self._make_rec()
        rowid = persist_recommendation(rec, "2025-01-01")
        assert rowid > 0

    def test_duplicate_raises_idempotency(self, temp_db):
        rec = self._make_rec()
        persist_recommendation(rec, "2025-01-01")
        with pytest.raises(IdempotencyError):
            persist_recommendation(rec, "2025-01-01")

    def test_different_type_allowed(self, temp_db):
        rec1 = self._make_rec("build")
        rec2 = self._make_rec("clear")
        rid1 = persist_recommendation(rec1, "2025-01-01")
        rid2 = persist_recommendation(rec2, "2025-01-01")
        assert rid1 != rid2

    def test_data_stored_correctly(self, temp_db):
        rec = self._make_rec()
        rowid = persist_recommendation(rec, "2025-01-01")

        conn = sqlite3.connect(str(temp_db))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM advisor_runs WHERE id=?", (rowid,)).fetchone()
            assert row["stock_code"] == "000001"
            assert row["action"] == "buy"
            assert row["confidence"] == "HIGH"
            assert row["prompt_tokens"] == 100
        finally:
            conn.close()


# ── run_for_stock (integration with mocks) ─────────────────────────


class TestRunForStock:
    def test_full_pipeline_with_holding(self, monkeypatch, tmp_path):
        temp_path = tmp_path / "test_run.db"
        monkeypatch.setattr(db_module, "DB_PATH", temp_path)
        db_module.init_database()

        import stockhot.advisor.recommendation_engine as engine
        import stockhot.advisor.signal_aggregator as agg_mod

        monkeypatch.setattr(
            agg_mod, "fetch_technical_signal",
            lambda code, df: _make_signal("technical", 35.0),
        )
        monkeypatch.setattr(
            agg_mod, "fetch_realtime_price",
            lambda code: {"code": code, "current_price": 12.0, "change_pct": 2.0, "volume": 100000, "timestamp": "2025-01-01"},
        )
        monkeypatch.setattr(
            agg_mod, "fetch_davis_signal",
            lambda code: _make_signal("davis", 80.0, source="davis_analyzer"),
        )
        monkeypatch.setattr(
            agg_mod, "get_current_davis_score",
            lambda code: {"final_score": 80.0, "percentile_rank": 90.0, "distress_score": 50.0, "data_date": "2025-01-01"},
        )
        monkeypatch.setattr(agg_mod, "check_hard_stop_loss", lambda h, p: {"triggered": False, "signal_type": "hard_stop", "details": {}})
        monkeypatch.setattr(agg_mod, "check_trailing_stop", lambda h, df: {"triggered": False, "signal_type": "trailing_stop", "details": {}})
        monkeypatch.setattr(agg_mod, "check_target_reached", lambda h, p: {"triggered": False, "signal_type": "target_reached", "details": {}})
        monkeypatch.setattr(agg_mod, "check_thesis_broken", lambda h, d: {"triggered": False, "signal_type": "thesis_broken", "details": {}})

        provider = FakeProvider()
        holding = {"position_pct": 5.0, "avg_cost": 10.0, "stop_loss_hard": 8.0}

        rec = run_for_stock("000001", "2025-01-01", holding=holding, provider=provider)

        assert rec.code == "000001"
        assert rec.recommendation_type in ("build", "adjust", "t_trade")
        assert rec.action != "NO_ACTION"

    def test_idempotent_second_call_silently_skips(self, monkeypatch, tmp_path):
        temp_path = tmp_path / "test_run2.db"
        monkeypatch.setattr(db_module, "DB_PATH", temp_path)
        db_module.init_database()

        import stockhot.advisor.signal_aggregator as agg_mod

        monkeypatch.setattr(
            agg_mod, "fetch_technical_signal",
            lambda code, df: _make_signal("technical", 35.0),
        )
        monkeypatch.setattr(
            agg_mod, "fetch_realtime_price",
            lambda code: {"code": code, "current_price": 12.0, "change_pct": 2.0, "volume": 100000, "timestamp": "2025-01-01"},
        )
        monkeypatch.setattr(
            agg_mod, "fetch_davis_signal",
            lambda code: _make_signal("davis", 80.0, source="davis_analyzer"),
        )
        monkeypatch.setattr(
            agg_mod, "get_current_davis_score",
            lambda code: {"final_score": 80.0, "percentile_rank": 90.0, "distress_score": 50.0, "data_date": "2025-01-01"},
        )
        monkeypatch.setattr(agg_mod, "check_hard_stop_loss", lambda h, p: {"triggered": False, "signal_type": "hard_stop", "details": {}})
        monkeypatch.setattr(agg_mod, "check_trailing_stop", lambda h, df: {"triggered": False, "signal_type": "trailing_stop", "details": {}})
        monkeypatch.setattr(agg_mod, "check_target_reached", lambda h, p: {"triggered": False, "signal_type": "target_reached", "details": {}})
        monkeypatch.setattr(agg_mod, "check_thesis_broken", lambda h, d: {"triggered": False, "signal_type": "thesis_broken", "details": {}})

        provider = FakeProvider()

        rec1 = run_for_stock("000001", "2025-01-01", provider=provider)
        rec2 = run_for_stock("000001", "2025-01-01", provider=provider)

        assert rec1.action == rec2.action

    def test_force_overrides_idempotency(self, monkeypatch, tmp_path):
        temp_path = tmp_path / "test_run3.db"
        monkeypatch.setattr(db_module, "DB_PATH", temp_path)
        db_module.init_database()

        import stockhot.advisor.signal_aggregator as agg_mod

        monkeypatch.setattr(
            agg_mod, "fetch_technical_signal",
            lambda code, df: _make_signal("technical", 35.0),
        )
        monkeypatch.setattr(
            agg_mod, "fetch_realtime_price",
            lambda code: {"code": code, "current_price": 12.0, "change_pct": 2.0, "volume": 100000, "timestamp": "2025-01-01"},
        )
        monkeypatch.setattr(
            agg_mod, "fetch_davis_signal",
            lambda code: _make_signal("davis", 80.0, source="davis_analyzer"),
        )
        monkeypatch.setattr(
            agg_mod, "get_current_davis_score",
            lambda code: {"final_score": 80.0, "percentile_rank": 90.0, "distress_score": 50.0, "data_date": "2025-01-01"},
        )
        monkeypatch.setattr(agg_mod, "check_hard_stop_loss", lambda h, p: {"triggered": False, "signal_type": "hard_stop", "details": {}})
        monkeypatch.setattr(agg_mod, "check_trailing_stop", lambda h, df: {"triggered": False, "signal_type": "trailing_stop", "details": {}})
        monkeypatch.setattr(agg_mod, "check_target_reached", lambda h, p: {"triggered": False, "signal_type": "target_reached", "details": {}})
        monkeypatch.setattr(agg_mod, "check_thesis_broken", lambda h, d: {"triggered": False, "signal_type": "thesis_broken", "details": {}})

        provider = FakeProvider()

        run_for_stock("000001", "2025-01-01", provider=provider)
        rec = run_for_stock("000001", "2025-01-01", provider=provider, force=True)

        assert rec.action != "NO_ACTION"
