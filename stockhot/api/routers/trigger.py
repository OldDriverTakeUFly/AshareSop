"""Manual data trigger endpoints."""

from __future__ import annotations

import asyncio
import sys

from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.post("/trigger/{date}")
async def trigger_collection(date: str):
    """Fire-and-forget: launch the CLI to collect data for *date*."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "stockhot.main",
        "--mode",
        "all",
        "--date",
        date,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    return {"status": "triggered", "date": date, "pid": proc.pid}


@router.get("/trigger/status")
async def trigger_status():
    """Return current trigger subsystem status (MVP: static)."""
    return {"status": "available"}
