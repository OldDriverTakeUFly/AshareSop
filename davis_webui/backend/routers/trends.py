from fastapi import APIRouter, HTTPException

from davis_webui.backend.schemas import TrendDataResponse
from davis_webui.backend.tasks import task_manager

router = APIRouter()


@router.get("/{task_id}/{ts_code}")
async def get_trend_data(task_id: str, ts_code: str):
    info = task_manager.get_task(task_id)
    if info is None or info.result is None:
        raise HTTPException(
            status_code=404, detail="Task or results not found"
        )
    result = info.result

    stock_info = result.stock_infos.get(ts_code)
    if stock_info is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    import pandas as pd

    from davis_analyzer.trend import (
        calculate_monthly_trend,
        calculate_trend_acceleration,
        calculate_trend_slope,
    )
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

    dates = pd.to_datetime(
        [v.trade_date for v in history], format="%Y%m%d"
    )
    pe_series = pd.Series([v.pe_ttm for v in history], index=dates)
    pb_series = pd.Series([v.pb for v in history], index=dates)

    monthly_pe, monthly_pb = calculate_monthly_trend(pe_series, pb_series)

    monthly_dates = [
        d.strftime("%Y-%m") for d in pe_series.resample("ME").mean().index
    ]
    if len(monthly_pe) < len(monthly_dates):
        monthly_dates = monthly_dates[-(len(monthly_pe)):]

    pe_slope = calculate_trend_slope(monthly_pe)
    pb_slope = calculate_trend_slope(monthly_pb)
    pe_accel = calculate_trend_acceleration(monthly_pe)
    pb_accel = calculate_trend_acceleration(monthly_pb)

    trend_score = result.trend_scores.get(ts_code, 50.0)

    return TrendDataResponse(
        ts_code=ts_code,
        monthly_dates=monthly_dates,
        monthly_pe=monthly_pe,
        monthly_pb=monthly_pb,
        pe_slope=pe_slope,
        pb_slope=pb_slope,
        pe_acceleration=pe_accel,
        pb_acceleration=pb_accel,
        trend_score=trend_score,
    )
