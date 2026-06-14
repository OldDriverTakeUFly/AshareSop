from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class TaskStatusEnum(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ── Stock & Score responses ──────────────────────────────────────────


class StockInfoResponse(BaseModel):
    ts_code: str
    name: str
    industry: str
    is_cyclical: bool


class DavisScoreResponse(BaseModel):
    ts_code: str
    name: str
    valuation_score: float
    trend_score: float
    prosperity_score: float
    distress_score: float
    final_score: float
    rank: int


class DistressDetailResponse(BaseModel):
    ts_code: str
    layer1_score: float
    layer2_score: float
    layer3_score: float
    total_score: float
    signals_detail: dict


class ProsperityDetailResponse(BaseModel):
    ts_code: str
    revenue_score: float
    profit_score: float
    slope_score: float
    duration_score: float
    composite_score: float
    delta_g: float


# ── Screening & Task ─────────────────────────────────────────────────


class ScreeningStartRequest(BaseModel):
    top_n: int = 30
    dry_run: bool = False


class TaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatusEnum
    progress: float
    message: str
    error: str | None = None


class ScreeningResultsResponse(BaseModel):
    scores: list[DavisScoreResponse]
    total_count: int


# ── Stock detail ─────────────────────────────────────────────────────


class StockDetailResponse(BaseModel):
    stock_info: StockInfoResponse
    davis_score: DavisScoreResponse
    prosperity_detail: ProsperityDetailResponse | None = None
    distress_detail: DistressDetailResponse | None = None
    financial_summary: dict


# ── Report ───────────────────────────────────────────────────────────


class ReportResponse(BaseModel):
    ts_code: str
    name: str
    markdown_content: str


# ── Trend ────────────────────────────────────────────────────────────


class TrendDataResponse(BaseModel):
    ts_code: str
    monthly_dates: list[str]
    monthly_pe: list[float | None]
    monthly_pb: list[float]
    pe_slope: float
    pb_slope: float
    pe_acceleration: float
    pb_acceleration: float
    trend_score: float


# ── Distress heatmap ─────────────────────────────────────────────────


class DistressHeatmapStock(BaseModel):
    ts_code: str
    name: str
    rank: int
    layer1_signals: dict
    layer2_signals: dict
    layer3_signals: dict
    layer_scores: dict
    total_score: float


class DistressHeatmapResponse(BaseModel):
    stocks: list[DistressHeatmapStock]


# ── Checklist ────────────────────────────────────────────────────────


class ChecklistGenerateRequest(BaseModel):
    task_id: str
    top_n: int = 3


class ChecklistSection(BaseModel):
    title: str
    items: list[str]


class ChecklistData(BaseModel):
    ts_code: str
    name: str
    rank: int
    scores: dict
    prosperity_display: str
    distress_display: str
    sections: list[ChecklistSection]


class ChecklistFillRequest(BaseModel):
    prosperity_adjustment: float
    distress_adjustment: float
    research_notes: dict = {}


# ── Rescore ──────────────────────────────────────────────────────────


class RescoreRequest(BaseModel):
    task_id: str


class RescoreResultItem(BaseModel):
    ts_code: str
    name: str
    original_prosperity: float
    adjusted_prosperity: float
    original_distress: float
    adjusted_distress: float
    prosperity_adjustment: float
    distress_adjustment: float


class RescoreResponse(BaseModel):
    results: list[RescoreResultItem]


# ── History ──────────────────────────────────────────────────────────


class HistoryEntryResponse(BaseModel):
    task_id: str
    created_at: str
    top_n: int
    total_count: int
