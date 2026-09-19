from fastapi import APIRouter, HTTPException

from davis_webui.backend.schemas import (
    DavisScoreResponse,
    ScreeningResultsResponse,
    ScreeningStartRequest,
    TaskStatusEnum,
    TaskStatusResponse,
)
from davis_webui.backend.tasks import task_manager

router = APIRouter()


@router.post("/start")
async def start_screening(req: ScreeningStartRequest):
    try:
        task_id = task_manager.start_screening(top_n=req.top_n, dry_run=req.dry_run)
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
    scores = [
        DavisScoreResponse(
            ts_code=s.ts_code,
            name=s.name,
            valuation_score=s.valuation_score,
            trend_score=s.trend_score,
            prosperity_score=s.prosperity_score,
            distress_score=s.distress_score,
            final_score=s.final_score,
            rank=s.rank,
        )
        for s in result.scores
    ]
    return ScreeningResultsResponse(scores=scores, total_count=len(scores))


@router.delete("/{task_id}/stocks/{ts_code}")
async def remove_stock(task_id: str, ts_code: str):
    removed = task_manager.remove_stock(task_id, ts_code)
    if not removed:
        raise HTTPException(status_code=404, detail="Task or stock not found")
    return {"deleted": True, "ts_code": ts_code}
