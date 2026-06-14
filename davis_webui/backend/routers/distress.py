from fastapi import APIRouter, HTTPException

from davis_webui.backend.schemas import (
    DistressHeatmapResponse,
    DistressHeatmapStock,
)
from davis_webui.backend.tasks import task_manager

router = APIRouter()


@router.get("/{task_id}")
async def get_distress_heatmap(task_id: str):
    info = task_manager.get_task(task_id)
    if info is None or info.result is None:
        raise HTTPException(
            status_code=404, detail="Task or results not found"
        )
    result = info.result

    rank_lookup = {s.ts_code: s.rank for s in result.scores}

    stocks = []
    for ts_code, distress in result.distress_signals.items():
        if ts_code not in rank_lookup:
            continue

        stock_info = result.stock_infos.get(ts_code)
        name = stock_info.name if stock_info else ts_code

        detail = distress.signals_detail or {}
        stocks.append(
            DistressHeatmapStock(
                ts_code=ts_code,
                name=name,
                rank=rank_lookup[ts_code],
                layer1_signals=detail.get("layer1", {}),
                layer2_signals=detail.get("layer2", {}),
                layer3_signals=detail.get("layer3", {}),
                layer_scores={
                    "layer1": distress.layer1_score,
                    "layer2": distress.layer2_score,
                    "layer3": distress.layer3_score,
                },
                total_score=distress.total_score,
            )
        )

    stocks.sort(key=lambda s: s.rank)

    return DistressHeatmapResponse(stocks=stocks)
