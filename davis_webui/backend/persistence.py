from __future__ import annotations

import dataclasses
import math
from typing import TYPE_CHECKING, Any

from davis_analyzer.types import (
    CatalystSignal,
    DavisDoubleScore,
    DistressSignal,
    FinancialData,
    InflectionAnalysis,
    IndustryProsperityScore,
    PipelineResult,
    ProsperityScore,
    ProsperitySectorResult,
    ProsperityStockDetail,
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
        "pipeline_type": "davis",
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


def deserialize_result(data: dict) -> PipelineResult | ProsperitySectorResult:
    pipeline_type = data.get("pipeline_type", "davis")
    if pipeline_type == "prosperity_sector":
        return deserialize_prosperity_result(data)
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


def serialize_prosperity_result(task_id: str, task_info: TaskInfo, result: ProsperitySectorResult) -> dict:
    raw = {
        "pipeline_type": "prosperity_sector",
        "task_id": task_id,
        "created_at": task_info.created_at,
        "top_n": getattr(task_info, "top_n", 0),
        "dry_run": getattr(task_info, "dry_run", False),
        "total_count": len(result.industry_scores),
        "status": task_info.status.value,
        "result": {
            "industry_scores": [dataclasses.asdict(s) for s in result.industry_scores],
            "stock_details": {
                k: {
                    "ts_code": v.ts_code,
                    "name": v.name,
                    "industry": v.industry,
                    "prosperity_score": dataclasses.asdict(v.prosperity_score),
                    "stage": v.stage,
                    "is_ignition": v.is_ignition,
                    "risk_warnings": v.risk_warnings,
                    "rank_in_industry": v.rank_in_industry,
                    "ignition_reasons": v.ignition_reasons,
                    "inflection": dataclasses.asdict(v.inflection) if v.inflection else None,
                    "dupont_driver": v.dupont_driver,
                }
                for k, v in result.stock_details.items()
            },
            "stock_infos": {k: dataclasses.asdict(v) for k, v in result.stock_infos.items()},
            "prosperity_scores": {k: dataclasses.asdict(v) for k, v in result.prosperity_scores.items()},
            "financial_data": {k: [dataclasses.asdict(f) for f in v] for k, v in result.financial_data.items()},
            "analysis_date": result.analysis_date,
        },
    }
    return _sanitize(raw)


def deserialize_prosperity_result(data: dict) -> ProsperitySectorResult:
    r = data["result"]
    stock_details = {}
    for k, v in r["stock_details"].items():
        prosperity_score = ProsperityScore(**v["prosperity_score"])
        inflection_data = v.get("inflection")
        inflection = None
        if inflection_data:
            catalysts = [CatalystSignal(**c) for c in inflection_data.get("catalysts", [])]
            inflection = InflectionAnalysis(
                ts_code=inflection_data["ts_code"],
                stage=inflection_data["stage"],
                inflection_quarter=inflection_data.get("inflection_quarter"),
                primary_driver=inflection_data["primary_driver"],
                catalysts=catalysts,
                narrative=inflection_data["narrative"],
                inflection_axis=inflection_data.get("inflection_axis"),
            )
        stock_details[k] = ProsperityStockDetail(
            ts_code=v["ts_code"],
            name=v["name"],
            industry=v["industry"],
            prosperity_score=prosperity_score,
            stage=v["stage"],
            is_ignition=v["is_ignition"],
            risk_warnings=v["risk_warnings"],
            rank_in_industry=v["rank_in_industry"],
            ignition_reasons=v.get("ignition_reasons", []),
            inflection=inflection,
            dupont_driver=v.get("dupont_driver"),
        )

    return ProsperitySectorResult(
        industry_scores=[IndustryProsperityScore(**s) for s in r["industry_scores"]],
        stock_details=stock_details,
        stock_infos={k: StockInfo(**v) for k, v in r["stock_infos"].items()},
        prosperity_scores={k: ProsperityScore(**v) for k, v in r["prosperity_scores"].items()},
        financial_data={k: [FinancialData(**f) for f in v] for k, v in r["financial_data"].items()},
        analysis_date=r["analysis_date"],
    )
