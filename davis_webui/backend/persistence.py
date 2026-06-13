from __future__ import annotations

import dataclasses
import math
from typing import TYPE_CHECKING, Any

from davis_analyzer.types import (
    DavisDoubleScore,
    DistressSignal,
    FinancialData,
    PipelineResult,
    ProsperityScore,
    StockInfo,
)

if TYPE_CHECKING:
    from davis_webui.backend.tasks import TaskInfo


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return 0.0
        return obj
    elif isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def serialize_result(task_id: str, task_info: TaskInfo, result: PipelineResult) -> dict:
    raw = {
        "task_id": task_id,
        "created_at": task_info.created_at,
        "top_n": getattr(task_info, "top_n", 0),
        "dry_run": getattr(task_info, "dry_run", False),
        "total_count": len(result.scores),
        "status": task_info.status.value,
        "result": {
            "scores": [dataclasses.asdict(s) for s in result.scores],
            "stock_infos": {k: dataclasses.asdict(v) for k, v in result.stock_infos.items()},
            "valuation_data": {k: list(v) for k, v in result.valuation_data.items()},
            "prosperity_scores": {k: dataclasses.asdict(v) for k, v in result.prosperity_scores.items()},
            "distress_signals": {k: dataclasses.asdict(v) for k, v in result.distress_signals.items()},
            "financial_data": {k: [dataclasses.asdict(f) for f in v] for k, v in result.financial_data.items()},
            "trend_scores": result.trend_scores,
        },
    }
    return _sanitize(raw)


def deserialize_result(data: dict) -> PipelineResult:
    r = data["result"]
    return PipelineResult(
        scores=[DavisDoubleScore(**s) for s in r["scores"]],
        stock_infos={k: StockInfo(**v) for k, v in r["stock_infos"].items()},
        valuation_data={k: tuple(v) for k, v in r["valuation_data"].items()},
        prosperity_scores={k: ProsperityScore(**v) for k, v in r["prosperity_scores"].items()},
        distress_signals={k: DistressSignal(**v) for k, v in r["distress_signals"].items()},
        financial_data={k: [FinancialData(**f) for f in v] for k, v in r["financial_data"].items()},
        trend_scores=r["trend_scores"],
    )
