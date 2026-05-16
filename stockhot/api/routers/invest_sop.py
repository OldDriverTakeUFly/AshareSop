from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

from stockhot.api import invest_sop_db as db
from stockhot.api.schemas import (
    Holding,
    HoldingCreateRequest,
    HoldingUpdatePriceRequest,
    HoldingUpdateStoplossRequest,
    HistoryPoint,
    ReportInfo,
    SupplyChainRecord,
)
from stockhot.invest_sop.config import INVEST_REPORTS_DIR
from stockhot.storage.database import get_connection

router = APIRouter(prefix="/api/invest-sop", tags=["invest-sop"])


@router.get("/holdings", response_model=list[Holding])
async def list_holdings():
    return await db.get_holdings(status="active")


@router.get("/holdings/{id}", response_model=Holding)
async def get_holding(id: int):
    row = await db.get_holding_by_id(id)
    if row is None:
        raise HTTPException(status_code=404, detail="Holding not found")
    return row


@router.post("/holdings", response_model=Holding)
async def add_holding(body: HoldingCreateRequest):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")

    stop_loss_hard = (
        body.stop_loss_hard
        if body.stop_loss_hard is not None
        else round(body.entry_price * 0.88, 2)
    )

    data = {
        "code": body.code,
        "name": body.name,
        "sector": body.sector,
        "entry_price": body.entry_price,
        "current_price": body.entry_price,
        "stop_loss_logic": body.stop_loss_logic,
        "stop_loss_technical": body.stop_loss_technical,
        "stop_loss_hard": stop_loss_hard,
        "target_price": body.target_price,
        "position_pct": body.position_pct,
        "entry_date": today,
        "status": "active",
        "notes": None,
        "updated_at": now,
    }

    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)

    conn = get_connection()
    try:
        cur = conn.execute(
            f"INSERT INTO invest_holdings ({cols}) VALUES ({placeholders})",
            tuple(data.values()),
        )
        conn.commit()
        new_id = cur.lastrowid
    finally:
        conn.close()

    assert new_id is not None
    row = await db.get_holding_by_id(new_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to fetch created holding")
    return row


@router.put("/holdings/{id}/price", response_model=Holding)
async def update_holding_price(id: int, body: HoldingUpdatePriceRequest):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE invest_holdings SET current_price=?, updated_at=? WHERE id=?",
            (body.current_price, now, id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Holding not found")
    finally:
        conn.close()

    row = await db.get_holding_by_id(id)
    return row


@router.put("/holdings/{id}/stoploss", response_model=Holding)
async def update_holding_stoploss(id: int, body: HoldingUpdateStoplossRequest):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updates: dict[str, object] = {"updated_at": now}

    if body.stop_loss_logic is not None:
        updates["stop_loss_logic"] = body.stop_loss_logic
    if body.stop_loss_technical is not None:
        updates["stop_loss_technical"] = body.stop_loss_technical
    if body.stop_loss_hard is not None:
        updates["stop_loss_hard"] = body.stop_loss_hard

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [id]

    conn = get_connection()
    try:
        cur = conn.execute(
            f"UPDATE invest_holdings SET {set_clause} WHERE id=?",
            tuple(values),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Holding not found")
    finally:
        conn.close()

    row = await db.get_holding_by_id(id)
    return row


@router.delete("/holdings/{id}")
async def delete_holding(id: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE invest_holdings SET status='closed', updated_at=? WHERE id=?",
            (now, id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Holding not found")
    finally:
        conn.close()

    return {"status": "closed", "id": id}


@router.get("/overview/{date}")
async def get_overview(date: str):
    overseas, futures, events = await asyncio.gather(
        db.get_overseas_by_date(date),
        db.get_futures_by_date(date),
        db.get_events_by_date(date),
    )
    return {
        "date": date,
        "overseas": overseas,
        "futures": futures,
        "events": events,
    }


@router.get("/supply-chain/{date}", response_model=list[SupplyChainRecord])
async def get_supply_chain(date: str):
    return await db.get_supply_chain_by_date(date)


@router.get("/history/commodity", response_model=list[HistoryPoint])
async def get_commodity_history(metric_name: str, start_date: str, end_date: str):
    rows = await db.get_supply_chain_history([metric_name], start_date, end_date)
    return [{"date": r["date"], "value": r["value"]} for r in rows if r.get("value") is not None]


@router.get("/history/vix")
async def get_vix_history(start_date: str, end_date: str):
    rows = await db.get_overseas_history(start_date, end_date)
    return [
        {
            "date": r["date"],
            "vix": r.get("vix"),
            "us_vix": r.get("us_vix"),
        }
        for r in rows
    ]


@router.get("/reports", response_model=list[ReportInfo])
async def list_reports():
    return await db.get_report_dates()


_REPORT_TYPE_MAP = {
    "pre_market": "盘前预研",
    "directive": "操作指令",
    "cycle_review": "周期评估",
}


@router.get("/reports/{date}")
async def get_report(date: str):
    results: list[dict] = []
    reports_dir = INVEST_REPORTS_DIR
    if not reports_dir.exists():
        return {"date": date, "reports": results}

    for path in sorted(reports_dir.glob(f"{date}_*.md")):
        suffix = path.stem.split("_", 1)[1] if "_" in path.stem else ""
        if suffix in _REPORT_TYPE_MAP:
            results.append({
                "type": _REPORT_TYPE_MAP[suffix],
                "content": path.read_text(encoding="utf-8"),
            })
    return {"date": date, "reports": results}
