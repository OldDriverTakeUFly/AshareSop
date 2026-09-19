from fastapi import APIRouter, HTTPException

from davis_webui.backend.schemas import ReportResponse
from davis_webui.backend.tasks import task_manager

router = APIRouter()


@router.get("/{task_id}/{ts_code}")
async def get_report(task_id: str, ts_code: str):
    info = task_manager.get_task(task_id)
    if info is None or info.result is None:
        raise HTTPException(status_code=404, detail="Task or results not found")
    result = info.result

    stock_info = result.stock_infos.get(ts_code)
    davis_score = next(
        (s for s in result.scores if s.ts_code == ts_code), None
    )
    if stock_info is None or davis_score is None:
        raise HTTPException(status_code=404, detail="Stock not found")

    val = result.valuation_data.get(ts_code)
    prop = result.prosperity_scores.get(ts_code)
    dist = result.distress_signals.get(ts_code)
    fin_list = result.financial_data.get(ts_code, [])

    if val is None or prop is None or dist is None:
        raise HTTPException(
            status_code=404, detail="Insufficient data for report"
        )

    from davis_analyzer.report_generator import generate_stock_report

    fin_latest = fin_list[0] if fin_list else None
    markdown = generate_stock_report(
        stock_info=stock_info,
        valuation_data=val,
        prosperity=prop,
        distress=dist,
        davis_score=davis_score,
        financial_data=fin_latest,
    )

    return ReportResponse(
        ts_code=ts_code,
        name=stock_info.name,
        markdown_content=markdown,
    )
