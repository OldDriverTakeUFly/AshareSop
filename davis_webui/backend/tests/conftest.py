"""Shared fixtures for davis_webui backend tests."""

import pytest
from fastapi.testclient import TestClient

from davis_webui.backend.main import app
from davis_webui.backend.tasks import TaskInfo, TaskStatus, task_manager
from davis_analyzer.types import (
    DavisDoubleScore,
    DividendSignal,
    DistressSignal,
    FinancialData,
    ForecastSignal,
    MomentumSignal,
    PipelineResult,
    ProsperityScore,
    StockInfo,
)


# ── mock data builders ───────────────────────────────────────────────


def _make_stock(ts_code: str, name: str, rank: int) -> tuple[
    DavisDoubleScore,
    StockInfo,
    ProsperityScore,
    DistressSignal,
    FinancialData,
]:
    """Build a complete set of mock objects for one stock."""
    score = DavisDoubleScore(
        ts_code=ts_code,
        name=name,
        valuation_score=65.0,
        prosperity_score=72.0,
        distress_score=58.0,
        final_score=65.3,
        rank=rank,
        trend_score=60.0,
    )
    info = StockInfo(
        ts_code=ts_code,
        name=name,
        industry="电子",
        list_status="上市",
        is_cyclical=False,
    )
    prosperity = ProsperityScore(
        ts_code=ts_code,
        revenue_score=15.0,
        profit_score=20.0,
        slope_score=18.0,
        duration_score=10.0,
        composite_score=70.0,
        delta_g=0.5,
    )
    distress = DistressSignal(
        ts_code=ts_code,
        layer1_score=70.0,
        layer2_score=60.0,
        layer3_score=50.0,
        total_score=59.0,
        signals_detail={
            "layer1": {
                "eps_decline": 0.8,
                "pe_pb_percentile": 0.6,
                "financial_health": 0.7,
            },
            "layer2": {
                "balance_sheet": 0.5,
                "operating_cf": 0.9,
                "roe_trend": 0.6,
            },
            "layer3": {
                "revenue_inflection": 0.4,
                "profit_inflection": 0.3,
                "delta_g_positive": 0.8,
            },
        },
    )
    fin = FinancialData(
        ts_code=ts_code,
        report_period="2024Q3",
        revenue=10_000_000_000.0,
        net_profit=1_000_000_000.0,
        eps=0.85,
        roe=12.5,
        operating_cf=2_000_000_000.0,
        total_debt=3_000_000_000.0,
        total_assets=10_000_000_000.0,
        yoy_revenue_growth=15.0,
        yoy_profit_growth=20.0,
    )
    return score, info, prosperity, distress, fin


def _make_pipeline_result() -> PipelineResult:
    """Create a PipelineResult with 3 mock stocks."""
    stocks_data = [
        ("000001.SZ", "平安银行", 1),
        ("000002.SZ", "万科A", 2),
        ("600036.SH", "招商银行", 3),
    ]

    scores: list[DavisDoubleScore] = []
    stock_infos: dict[str, StockInfo] = {}
    valuation_data: dict[str, tuple] = {}
    prosperity_scores: dict[str, ProsperityScore] = {}
    distress_signals: dict[str, DistressSignal] = {}
    financial_data: dict[str, list[FinancialData]] = {}
    momentum_signals: dict[str, MomentumSignal] = {}
    dividend_signals: dict[str, DividendSignal] = {}
    forecast_signals: dict[str, ForecastSignal] = {}

    for ts_code, name, rank in stocks_data:
        s, info, prop, dist, fin = _make_stock(ts_code, name, rank)
        scores.append(s)
        stock_infos[ts_code] = info
        valuation_data[ts_code] = (65.0, 0.30, 0.25)
        prosperity_scores[ts_code] = prop
        distress_signals[ts_code] = dist
        financial_data[ts_code] = [fin]
        # Supplementary factor signals (one per stock so roundtrip covers them).
        momentum_signals[ts_code] = MomentumSignal(
            ts_code=ts_code,
            window_returns={60: 12.0},
            absolute_momentum_score=65.0,
            rs_percentile=70.0,
            momentum_score=67.5,
            data_sufficient=True,
        )
        dividend_signals[ts_code] = DividendSignal(
            ts_code=ts_code,
            consecutive_years=3,
            latest_yield_pct=4.2,
            dividend_score=80.0,
            payout_years=["20221231", "20231231", "20241231"],
            data_sufficient=True,
        )
        forecast_signals[ts_code] = ForecastSignal(
            ts_code=ts_code,
            ann_date="20260131",
            end_date="20251231",
            type="预增",
            p_change_min=50.0,
            p_change_max=70.0,
            p_change_mid=60.0,
            leading_score=85.0,
            is_stale=False,
        )

    return PipelineResult(
        scores=scores,
        stock_infos=stock_infos,
        valuation_data=valuation_data,
        prosperity_scores=prosperity_scores,
        distress_signals=distress_signals,
        financial_data=financial_data,
        trend_scores={s.ts_code: s.trend_score for s in scores},
        momentum_signals=momentum_signals,
        dividend_signals=dividend_signals,
        forecast_signals=forecast_signals,
    )


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_pipeline_result() -> PipelineResult:
    return _make_pipeline_result()


@pytest.fixture
def mock_task(mock_pipeline_result: PipelineResult):
    """Inject a completed task into task_manager, return task_id."""
    task_id = "test-task-001"
    task_manager.tasks[task_id] = TaskInfo(
        task_id=task_id,
        status=TaskStatus.COMPLETED,
        progress=100.0,
        message="Done",
        result=mock_pipeline_result,
    )
    yield task_id
    task_manager.tasks.pop(task_id, None)


@pytest.fixture
def mock_running_task():
    """Inject a running task, return task_id."""
    task_id = "running-task-002"
    task_manager.tasks[task_id] = TaskInfo(
        task_id=task_id,
        status=TaskStatus.RUNNING,
        progress=45.0,
        message="Screening pipeline running",
    )
    yield task_id
    task_manager.tasks.pop(task_id, None)


@pytest.fixture
def clean_task_manager():
    """Clear all tasks before and after test."""
    task_manager.tasks.clear()
    yield
    task_manager.tasks.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
