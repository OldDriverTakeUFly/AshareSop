from fastapi import APIRouter, HTTPException

from davis_webui.backend.schemas import (
    IndustryScoreResponse,
    ProsperityIndustryDetailResponse,
    ProsperitySectorResultsResponse,
    ProsperitySectorStartRequest,
    ProsperityStockResponse,
    TaskStatusEnum,
    TaskStatusResponse,
)
from davis_webui.backend.tasks import task_manager

router = APIRouter()


@router.post("/start")
async def start_prosperity_sector(req: ProsperitySectorStartRequest):
    try:
        task_id = task_manager.start_prosperity_sector(
            top_n_per_industry=req.top_n_per_industry
        )
        return {"task_id": task_id}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/{task_id}/status")
async def get_status(task_id: str):
    info = task_manager.get_task(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(
        task_id=info.task_id,
        status=TaskStatusEnum(info.status.value),
        progress=info.progress,
        message=info.message,
        error=info.error,
    )


@router.get("/{task_id}/results")
async def get_results(task_id: str):
    info = task_manager.get_task(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if info.status.value != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Task is {info.status.value}, not completed",
        )
    result = info.result
    industries = [
        IndustryScoreResponse(
            industry=s.industry,
            stock_count=s.stock_count,
            avg_composite_score=s.avg_composite_score,
            median_delta_g=s.median_delta_g,
            avg_revenue_score=s.avg_revenue_score,
            avg_profit_score=s.avg_profit_score,
            avg_slope_score=s.avg_slope_score,
            avg_duration_score=s.avg_duration_score,
            stage=s.stage,
            ignition_count=s.ignition_count,
            top_stock_codes=s.top_stock_codes,
        )
        for s in result.industry_scores
    ]
    return ProsperitySectorResultsResponse(
        industries=industries,
        total_industries=len(industries),
        analysis_date=result.analysis_date,
    )


@router.get("/{task_id}/industries/{industry_name}")
async def get_industry_detail(task_id: str, industry_name: str):
    info = task_manager.get_task(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if info.status.value != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Task is {info.status.value}, not completed",
        )
    result = info.result

    industry_score = None
    for s in result.industry_scores:
        if s.industry == industry_name:
            industry_score = IndustryScoreResponse(
                industry=s.industry,
                stock_count=s.stock_count,
                avg_composite_score=s.avg_composite_score,
                median_delta_g=s.median_delta_g,
                avg_revenue_score=s.avg_revenue_score,
                avg_profit_score=s.avg_profit_score,
                avg_slope_score=s.avg_slope_score,
                avg_duration_score=s.avg_duration_score,
                stage=s.stage,
                ignition_count=s.ignition_count,
                top_stock_codes=s.top_stock_codes,
            )
            break

    if industry_score is None:
        raise HTTPException(status_code=404, detail="Industry not found")

    stocks = []
    for ts_code in industry_score.top_stock_codes:
        detail = result.stock_details.get(ts_code)
        if detail is None:
            continue
        ps = detail.prosperity_score
        stocks.append(
            ProsperityStockResponse(
                ts_code=detail.ts_code,
                name=detail.name,
                industry=detail.industry,
                revenue_score=ps.revenue_score,
                profit_score=ps.profit_score,
                slope_score=ps.slope_score,
                duration_score=ps.duration_score,
                composite_score=ps.composite_score,
                delta_g=ps.delta_g,
                stage=detail.stage,
                is_ignition=detail.is_ignition,
                risk_warnings=detail.risk_warnings,
                rank_in_industry=detail.rank_in_industry,
            )
        )

    return ProsperityIndustryDetailResponse(
        industry=industry_name,
        stocks=stocks,
        industry_score=industry_score,
    )
