from fastapi import APIRouter, HTTPException

from davis_webui.backend.tasks import task_manager

router = APIRouter()


@router.get("/")
async def list_history():
    entries = task_manager.list_history()
    return {"history": entries}


@router.get("/{task_id}")
async def load_task(task_id: str):
    loaded = task_manager.load_task_from_disk(task_id)
    if not loaded:
        raise HTTPException(status_code=404, detail="Task not found on disk")
    return {"task_id": task_id, "loaded": True}


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    task_manager.delete_task(task_id)
    return {"deleted": True}
