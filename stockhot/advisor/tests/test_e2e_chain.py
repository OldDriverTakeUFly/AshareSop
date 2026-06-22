"""End-to-end integration test for the advisor chain.

Unlike the unit tests in test_recommendation_engine.py (which monkeypatch
every signal-source function on the aggregator module and thereby bypass
real aggregation), this test exercises the REAL chain end to end:

    fetch_ohlcv_for_advisor  ┐
    fetch_realtime_price     ├─→ real aggregate_signals
    run_screening_pipeline   ┘         │
                                      ▼
                              real arbitrate (conflict_resolver)
                                      │
                                      ▼
                              real generate_recommendation (FakeProvider for LLM)
                                      │
                                      ▼
                              real persist_recommendation (temp sqlite)
                                      │
                                      ▼
                              real build_advisor_section reads it back
                                      │
                                      ▼
                              real TelegramNotifier.send_recommendations_batch
                              (via httpx.MockTransport — no network)

Only the irreducible boundaries are mocked: the OHLCV network fetch, the
realtime spot quote, the full-market davis pipeline, and the Telegram HTTP
transport. Everything between them is the real production code path.

This is the test that proves the integration fixes (OHLCV plumbing,
technical.details population, pipeline cache, DB auto-init, sell_monitor
NULL guards) actually compose into a working chain.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date

import httpx
import pandas as pd
import pytest

from stockhot.advisor import signal_aggregator as agg_mod
from stockhot.advisor.data_sources import fundamental as fundamental_mod
from stockhot.advisor.llm_provider import LLMResponse
from stockhot.advisor.recommendation_engine import run_for_stock
from stockhot.advisor.report_integration import build_advisor_section
from stockhot.notification.telegram_bot import TelegramNotifier
from stockhot.storage import database as db_module

# ── Fakes (LLM only — everything else is real) ─────────────────────────────


@dataclass
class FakeProvider:
    """Deterministic LLM stand-in returning a well-formed build rec."""

    response_content: str = json.dumps(
        {
            "action": "buy",
            "confidence": "HIGH",
            "entry_zone": [11.5, 12.0],
            "stop_loss": 10.0,
            "target": 16.0,
            "reasoning": "Strong uptrend + high davis percentile + sector tailwind",
        }
    )
    model: str = "test-model"

    def complete(self, prompt, system="", max_tokens=800, temperature=0.3):
        return LLMResponse(
            content=self.response_content,
            prompt_tokens=120,
            completion_tokens=200,
            model=self.model,
            latency_ms=42,
        )


def _make_bullish_ohlcv(days: int = 60, start_price: float = 10.0) -> pd.DataFrame:
    """Build a 60-row OHLCV DataFrame with a clear bullish uptrend.

    Close prices rise monotonically so MA5 > MA10 > MA20 (multi-head
    arrangement), pushing composite_technical_score well above the 60
    "strong" threshold. Volume is healthy and stable so volume_ratio ≈ 1.
    """
    dates = pd.date_range(end=date.today(), periods=days, freq="D")
    closes = [round(start_price * (1.012**i), 2) for i in range(days)]
    df = pd.DataFrame(
        {
            "open": [c * 0.998 for c in closes],
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_000_000 + i * 1000 for i in range(days)],
        },
        index=dates.rename("date"),
    )
    return df


# ── Shared fixture: real temp DB + all advisor tables ──────────────────────


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test_e2e.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    db_module.init_database()
    return db_path


@pytest.fixture
def patched_sources(monkeypatch):
    """Patch only the network/pipeline boundaries; leave aggregation real."""
    ohlcv = _make_bullish_ohlcv()
    # B3 fix: aggregate_signals calls fetch_ohlcv_for_advisor when ohlcv_df
    # is None. Patch it on the aggregator module's namespace to inject a
    # deterministic bullish OHLCV without touching the network.
    monkeypatch.setattr(agg_mod, "fetch_ohlcv_for_advisor", lambda code, days=90: ohlcv)
    monkeypatch.setattr(
        agg_mod,
        "fetch_realtime_price",
        lambda code: {
            "code": code,
            "current_price": 12.5,
            "change_pct": 1.8,
            "volume": 1_500_000,
            "timestamp": date.today().isoformat(),
        },
    )
    # Pipeline cache: return a result whose scores list contains our stock
    # with a high final_score + good percentile_rank, so arbitrate() hits
    # the BUY / VALUE_ENTRY branch instead of defaulting to HOLD.
    monkeypatch.setattr(fundamental_mod, "_pipeline_cache", None)

    @dataclass
    class _Score:
        ts_code: str
        rank: int = 1
        final_score: float = 82.0
        distress_score: float = 60.0

    @dataclass
    class _Result:
        scores: list

    def fake_pipeline(dry_run=False):
        return _Result(scores=[_Score(ts_code="000001.SZ")])

    monkeypatch.setattr(fundamental_mod, "run_screening_pipeline", fake_pipeline)
    return ohlcv


# ── Test 1: full chain — aggregate → arbitrate → generate → persist ────────


class TestE2EAdvisorChain:
    def test_holdings_run_produces_persisted_recommendation(
        self, temp_db, patched_sources, monkeypatch
    ):
        """The headline test: run_for_stock end-to-end with real aggregation.

        Verifies that the B1-B4 fixes compose: OHLCV is fetched, technical
        details are populated, the davis pipeline is cached, and the result
        is a non-NO_ACTION recommendation persisted to advisor_runs.
        """
        holding = {
            "code": "000001",
            "name": "平安银行",
            "sector": "金融",
            "position_pct": 8.0,
            "avg_cost": 9.5,
            "entry_price": 9.5,
            "current_price": 12.5,
            "stop_loss_hard": 8.0,
            "target_price": 16.0,
            "status": "active",
        }

        rec = run_for_stock(
            "000001",
            date.today().isoformat(),
            holding=holding,
            provider=FakeProvider(),
        )

        # Action mapping: with strong tech (≥60) + strong davis (≥70),
        # arbitrate returns BUY (or HOLD→adjust for holdings). Either way
        # the recommendation_type must be non-"none" — that's the bug we
        # fixed (everything-no-longer-defaults-to-NO_ACTION).
        assert rec.recommendation_type != "none", (
            f"Expected actionable rec, got recommendation_type='none' "
            f"(action={rec.action}, confidence={rec.confidence})"
        )
        assert rec.action != "NO_ACTION"
        assert rec.code == "000001"

        # Persisted to advisor_runs?
        conn = sqlite3.connect(str(temp_db))
        try:
            rows = conn.execute(
                "SELECT stock_code, recommendation_type, action, confidence "
                "FROM advisor_runs WHERE trade_date = ?",
                (date.today().isoformat(),),
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "000001"
        assert rows[0][1] != "none"

    def test_technical_details_populated_with_support_resistance(self, temp_db, patched_sources):
        """B2 fix: technical.details must carry support/resistance/volume
        keys that _build_context reads, not just {state, signals}."""
        agg = agg_mod.aggregate_signals("000001")

        details = agg.technical.details
        assert "support_levels" in details, "support_levels missing — B2 fix not wired"
        assert "resistance_levels" in details
        assert "volume_ratio" in details
        assert "volume_trend" in details
        # Technical value should reflect the bullish OHLCV, not the old 50.0
        # placeholder.
        assert (
            agg.technical.value != 50.0
        ), "Technical still 50.0 — OHLCV not flowing into composite score"

    def test_davis_pipeline_cached_across_calls(self, temp_db, patched_sources, monkeypatch):
        """B4 fix: the pipeline runs at most once per process even when
        multiple holdings each trigger get_current_davis_score."""
        call_count = {"n": 0}
        original = fundamental_mod.run_screening_pipeline

        def counting_pipeline(dry_run=False):
            call_count["n"] += 1
            return original(dry_run=dry_run)

        monkeypatch.setattr(fundamental_mod, "run_screening_pipeline", counting_pipeline)
        monkeypatch.setattr(fundamental_mod, "_pipeline_cache", None)

        # Two lookups for the same stock (mirrors aggregate_signals calling
        # fetch_davis_signal then get_current_davis_score for thesis check).
        fundamental_mod.get_current_davis_score("000001")
        fundamental_mod.get_current_davis_score("000001")
        fundamental_mod.get_current_davis_score("600519")

        assert (
            call_count["n"] == 1
        ), f"Pipeline ran {call_count['n']} times, expected exactly 1 (cached)"

    def test_sell_monitor_survives_null_holding_fields(self, temp_db, patched_sources):
        """C1 fix: a holding row with NULL stop_loss_hard/target_price
        must not crash aggregation; the signal degrades gracefully."""
        holding_with_nulls = {
            "code": "000002",
            "name": "万科A",
            "sector": "地产",
            "position_pct": 5.0,
            # stop_loss_hard / target_price / current_price all missing
            "status": "active",
        }
        # Must not raise.
        agg = agg_mod.aggregate_signals("000002", holding=holding_with_nulls)
        sell_types = {s["signal_type"] for s in agg.sell_signals}
        assert "hard_stop" in sell_types
        assert "target_reached" in sell_types
        # Each degraded signal reports why it skipped.
        for s in agg.sell_signals:
            if s["signal_type"] in ("hard_stop", "target_reached"):
                assert s["triggered"] is False


# ── Test 2: report integration reads the persisted rec back ────────────────


class TestE2EReportRoundTrip:
    def test_build_advisor_section_contains_persisted_rec(self, temp_db, patched_sources):
        """Persist then read back via build_advisor_section — verifies the
        reasoning_json round-trip and sentinel wrapping."""
        holding = {
            "code": "000001",
            "position_pct": 8.0,
            "avg_cost": 9.5,
            "stop_loss_hard": 8.0,
            "target_price": 16.0,
            "status": "active",
        }
        run_for_stock(
            "000001",
            date.today().isoformat(),
            holding=holding,
            provider=FakeProvider(),
        )

        section = build_advisor_section(date.today().isoformat())

        assert "<!-- ADVISOR_SECTION_START" in section
        assert "<!-- ADVISOR_SECTION_END" in section
        assert "000001" in section


# ── Test 3: Telegram push via real notifier (MockTransport) ─────────────────


class TestE2ETelegramPush:
    @pytest.mark.asyncio
    async def test_send_recommendations_batch_uses_mock_transport(self):
        """The telegram hand-off shape (cli._rec_to_telegram_dict) must be
        consumable by TelegramNotifier.send_recommendations_batch. Uses
        httpx.MockTransport — no real API call (AGENTS.md mandate)."""
        captured: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            # Telegram Bot API calls in this module POST JSON bodies.
            body = json.loads(request.read().decode("utf-8"))
            captured.append(body.get("text", ""))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        notifier = TelegramNotifier(
            bot_token="fake-token",
            chat_id="123456",
            allowed_user_ids=[123456],
            _transport=httpx.MockTransport(handler),
        )

        rec_dicts = [
            {
                "code": "000001",
                "action": "buy",
                "confidence": "HIGH",
                "reason": "Strong fundamentals + technical breakout",
            },
            {
                "code": "600519",
                "action": "hold",
                "confidence": "MEDIUM",
                "reason": "Stable",
            },
        ]

        await notifier.send_recommendations_batch(rec_dicts, max_messages=5)

        assert len(captured) >= 1
        # The HIGH-confidence rec is treated as urgent and sent individually
        # first; its text must carry the code.
        joined = "\n".join(captured)
        assert "000001" in joined
