"""Tests for davis_analyzer.profitability — margin-trend + R&D engine."""

import pytest

from davis_analyzer.profitability import (
    analyze_profitability_quality,
    score_gross_margin_trend,
    score_rd_intensity,
)
from davis_analyzer.types import FinancialData


def _fd(
    period: str,
    grossprofit_margin: float | None = 40.0,
    rd_exp: float | None = 10.0,
    revenue: float = 1000.0,
    ts_code: str = "x.SZ",
) -> FinancialData:
    return FinancialData(
        ts_code=ts_code,
        report_period=period,
        revenue=revenue,
        net_profit=100.0,
        eps=1.0,
        roe=10.0,
        operating_cf=50.0,
        total_debt=200.0,
        total_assets=500.0,
        grossprofit_margin=grossprofit_margin,
        rd_exp=rd_exp,
    )


# ── score_gross_margin_trend ──────────────────────────────────────────


class TestScoreGrossMarginTrend:
    def test_expanding_margin_scores_high(self):
        score, delta = score_gross_margin_trend([40.0, 42.0, 44.0])
        assert delta == pytest.approx(4.0)
        assert score == 50 + 4 * 5  # 70

    def test_contracting_margin_scores_low(self):
        score, delta = score_gross_margin_trend([44.0, 42.0, 40.0])
        assert delta == pytest.approx(-4.0)
        assert score == 50 - 20  # 30

    def test_flat_margin_neutral(self):
        score, delta = score_gross_margin_trend([40.0, 40.0])
        assert score == 50.0
        assert delta == 0.0

    def test_single_point_returns_neutral_none(self):
        score, delta = score_gross_margin_trend([40.0])
        assert score == 50.0
        assert delta is None

    def test_clamped_at_100(self):
        score, _ = score_gross_margin_trend([30.0, 60.0])  # +30pp
        assert score == 100.0

    def test_clamped_at_0(self):
        score, _ = score_gross_margin_trend([60.0, 30.0])  # -30pp
        assert score == 0.0


# ── score_rd_intensity ────────────────────────────────────────────────


class TestScoreRdIntensity:
    def test_high_intensity_scores_high(self):
        score, latest = score_rd_intensity([15.0])
        assert score == 100.0
        assert latest == 15.0

    def test_zero_intensity(self):
        score, latest = score_rd_intensity([0.0])
        assert score == 25.0
        assert latest == 0.0

    def test_capped_above_15(self):
        score, _ = score_rd_intensity([40.0])
        assert score == 100.0

    def test_empty_returns_neutral(self):
        score, latest = score_rd_intensity([])
        assert score == 50.0
        assert latest is None


# ── analyze_profitability_quality ─────────────────────────────────────


class TestAnalyzeProfitabilityQuality:
    def test_empty_returns_insufficient(self):
        pq = analyze_profitability_quality([])
        assert pq.data_sufficient is False
        assert pq.quality_score == 50.0

    def test_expanding_margin_high_rd(self):
        fds = [
            _fd("20240331", grossprofit_margin=38.0, rd_exp=80.0, revenue=1000.0),
            _fd("20240630", grossprofit_margin=40.0, rd_exp=90.0, revenue=1000.0),
            _fd("20240930", grossprofit_margin=42.0, rd_exp=100.0, revenue=1000.0),
            _fd("20241231", grossprofit_margin=44.0, rd_exp=110.0, revenue=1000.0),
        ]
        pq = analyze_profitability_quality(fds)
        assert pq.data_sufficient is True
        assert pq.gross_margin_score > 60.0  # +6pp → 80
        assert pq.gross_margin_delta == pytest.approx(6.0)
        # rd 110/1000 = 11% → 25 + 11/15*75 ≈ 80
        assert pq.rd_intensity_score > 70.0
        assert pq.quality_score > 70.0

    def test_contracting_margin_low_rd(self):
        fds = [
            _fd("20240331", grossprofit_margin=44.0, rd_exp=10.0),
            _fd("20241231", grossprofit_margin=40.0, rd_exp=10.0),
        ]
        pq = analyze_profitability_quality(fds)
        assert pq.gross_margin_score < 50.0
        assert pq.rd_intensity_score < 35.0  # 1% → low

    def test_missing_margin_fields(self):
        # grossprofit_margin None → falls back gracefully
        fds = [
            _fd("20240331", grossprofit_margin=None, rd_exp=10.0),
            _fd("20241231", grossprofit_margin=None, rd_exp=10.0),
        ]
        pq = analyze_profitability_quality(fds)
        assert pq.gross_margin_score == 50.0
        assert pq.latest_gross_margin is None
        # still sufficient because rd present
        assert pq.data_sufficient is True

    def test_respects_lookback_window(self):
        # 6 periods, lookback=4 → only last 4 scored
        fds = [
            _fd("20231231", grossprofit_margin=50.0),  # outside window
            _fd("20240331", grossprofit_margin=50.0),  # outside window
            _fd("20240630", grossprofit_margin=40.0),
            _fd("20240930", grossprofit_margin=40.0),
            _fd("20241231", grossprofit_margin=40.0),
            _fd("20250331", grossprofit_margin=40.0),
        ]
        pq = analyze_profitability_quality(fds, lookback=4)
        # within window: flat 40→40 → delta 0 → score 50
        assert pq.gross_margin_delta == pytest.approx(0.0)

    def test_rd_intensity_calculation(self):
        fds = [_fd("20241231", rd_exp=150.0, revenue=1000.0)]
        pq = analyze_profitability_quality(fds)
        assert pq.latest_rd_intensity == pytest.approx(15.0)
