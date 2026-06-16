from fastapi import APIRouter, HTTPException

from davis_webui.backend.schemas import (
    CatalystSignalResponse,
    InflectionAnalysisResponse,
    IndustryScoreResponse,
    ProsperityIndustryDetailResponse,
    ProsperitySectorResultsResponse,
    ProsperitySectorStartRequest,
    ProsperityStockResponse,
    StockValuationResponse,
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
        inflection_resp = None
        if detail.inflection:
            inflection_resp = InflectionAnalysisResponse(
                ts_code=detail.inflection.ts_code,
                stage=detail.inflection.stage,
                inflection_quarter=detail.inflection.inflection_quarter,
                primary_driver=detail.inflection.primary_driver,
                catalysts=[
                    CatalystSignalResponse(**c.__dict__)
                    for c in detail.inflection.catalysts
                ],
                narrative=detail.inflection.narrative,
                inflection_axis=detail.inflection.inflection_axis,
            )
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
                relative_delta_g=ps.relative_delta_g,
                stage=detail.stage,
                is_ignition=detail.is_ignition,
                risk_warnings=detail.risk_warnings,
                rank_in_industry=detail.rank_in_industry,
                inflection=inflection_resp,
                ignition_reasons=detail.ignition_reasons,
                dupont_driver=detail.dupont_driver,
            )
        )

    return ProsperityIndustryDetailResponse(
        industry=industry_name,
        stocks=stocks,
        industry_score=industry_score,
    )


@router.get("/{task_id}/stocks/{ts_code}/valuation")
async def get_stock_valuation(task_id: str, ts_code: str):
    info = task_manager.get_task(task_id)
    if info is None or info.result is None:
        raise HTTPException(
            status_code=404, detail="Task or results not found"
        )
    result = info.result

    if ts_code not in result.stock_details:
        raise HTTPException(status_code=404, detail="Stock not found")

    from davis_analyzer.tushare_client import TushareClient
    from davis_analyzer.valuation import fetch_valuation_history

    try:
        client = TushareClient()
        history = fetch_valuation_history(client, ts_code)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch valuation history: {e}",
        )

    if not history:
        raise HTTPException(
            status_code=404, detail="No valuation history available"
        )

    daily_dates = [v.trade_date for v in history]
    daily_pe = [v.pe_ttm for v in history]
    daily_pb = [v.pb for v in history]

    fd_list = result.financial_data.get(ts_code, [])
    fd_chronological = sorted(fd_list, key=lambda d: d.report_period)
    quarterly_periods = [d.report_period for d in fd_chronological]
    quarterly_revenue_growth = [d.yoy_revenue_growth * 100 for d in fd_chronological]
    quarterly_profit_growth = [d.yoy_profit_growth * 100 for d in fd_chronological]

    return StockValuationResponse(
        ts_code=ts_code,
        daily_dates=daily_dates,
        daily_pe=daily_pe,
        daily_pb=daily_pb,
        quarterly_periods=quarterly_periods,
        quarterly_revenue_growth=quarterly_revenue_growth,
        quarterly_profit_growth=quarterly_profit_growth,
    )
