from fastapi import APIRouter, HTTPException

from davis_webui.backend.schemas import (
    DavisScoreResponse,
    DistressDetailResponse,
    ProsperityDetailResponse,
    StockDetailResponse,
    StockInfoResponse,
)
from davis_webui.backend.tasks import task_manager

router = APIRouter()


@router.get("/{task_id}/{ts_code}")
async def get_stock_detail(task_id: str, ts_code: str):
    info = task_manager.get_task(task_id)
    if info is None or info.result is None:
        raise HTTPException(status_code=404, detail="Task or results not found")
    result = info.result

    stock_info = result.stock_infos.get(ts_code)
    if stock_info is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    davis_score = next(
        (s for s in result.scores if s.ts_code == ts_code), None
    )
    if davis_score is None:
        raise HTTPException(status_code=404, detail="Score not found")

    prosperity = result.prosperity_scores.get(ts_code)
    distress = result.distress_signals.get(ts_code)
    fin_data = result.financial_data.get(ts_code, [])
    latest_fin = fin_data[0] if fin_data else None

    financial_summary = {}
    if latest_fin:
        financial_summary = {
            "revenue": latest_fin.revenue,
            "net_profit": latest_fin.net_profit,
            "eps": latest_fin.eps,
            "roe": latest_fin.roe,
            "operating_cf": latest_fin.operating_cf,
            "total_debt": latest_fin.total_debt,
            "total_assets": latest_fin.total_assets,
            "yoy_revenue_growth": latest_fin.yoy_revenue_growth,
            "yoy_profit_growth": latest_fin.yoy_profit_growth,
        }

    return StockDetailResponse(
        stock_info=StockInfoResponse(
            ts_code=stock_info.ts_code,
            name=stock_info.name,
            industry=stock_info.industry,
            is_cyclical=stock_info.is_cyclical,
        ),
        davis_score=DavisScoreResponse(
            ts_code=davis_score.ts_code,
            name=davis_score.name,
            valuation_score=davis_score.valuation_score,
            trend_score=davis_score.trend_score,
            prosperity_score=davis_score.prosperity_score,
            distress_score=davis_score.distress_score,
            final_score=davis_score.final_score,
            rank=davis_score.rank,
        ),
        prosperity_detail=(
            ProsperityDetailResponse(
                ts_code=prosperity.ts_code,
                revenue_score=prosperity.revenue_score,
                profit_score=prosperity.profit_score,
                slope_score=prosperity.slope_score,
                duration_score=prosperity.duration_score,
                composite_score=prosperity.composite_score,
                delta_g=prosperity.delta_g,
            )
            if prosperity
            else None
        ),
        distress_detail=(
            DistressDetailResponse(
                ts_code=distress.ts_code,
                layer1_score=distress.layer1_score,
                layer2_score=distress.layer2_score,
                layer3_score=distress.layer3_score,
                total_score=distress.total_score,
                signals_detail=distress.signals_detail,
            )
            if distress
            else None
        ),
        financial_summary=financial_summary,
    )
