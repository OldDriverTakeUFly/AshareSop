from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

from stockhot.api import invest_sop_db as db
from stockhot.api.schemas import (
    Holding,
    HoldingAdjustRequest,
    HoldingCreateRequest,
    HoldingCreateSimple,
    HoldingTransaction,
    HoldingUpdatePriceRequest,
    HoldingUpdateStoplossRequest,
    HistoryPoint,
    ReportInfo,
    SectorRule,
    SectorRuleUpdate,
    SupplyChainRecord,
)
from stockhot.invest_sop.config import INVEST_REPORTS_DIR, get_sector_rule
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


def _strip_proxy():
    import os
    removed = {}
    for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
        if key in os.environ:
            removed[key] = os.environ.pop(key)
    return removed


def _restore_proxy(removed):
    import os
    os.environ.update(removed)


@router.post("/holdings", response_model=Holding)
async def add_holding(body: HoldingCreateSimple):
    import akshare as ak

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")

    removed = _strip_proxy()
    try:
        df = ak.stock_zh_a_spot_em()
    finally:
        _restore_proxy(removed)

    row = df[df["代码"] == body.code]
    if row.empty:
        raise HTTPException(status_code=400, detail=f"Stock code {body.code} not found")
    row = row.iloc[0]
    name = str(row["名称"])
    sector = str(row.get("所属行业", "default"))
    current_price = float(row["最新价"])

    price_for_calc = body.entry_price if body.entry_price is not None else current_price
    rule = get_sector_rule(sector)
    stop_loss_hard = round(price_for_calc * (1 + rule["stop_loss_pct"]), 2)
    target_price = round(price_for_calc * (1 + rule["target_pct"]), 2)

    data = {
        "code": body.code,
        "name": name,
        "sector": sector,
        "entry_price": body.entry_price,
        "current_price": current_price,
        "stop_loss_hard": stop_loss_hard,
        "target_price": target_price,
        "quantity": body.quantity,
        "avg_cost": body.entry_price,
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

    if body.entry_price is not None:
        tx_data = {
            "holding_id": new_id,
            "type": "buy",
            "quantity": body.quantity,
            "price": body.entry_price,
            "date": today,
            "notes": None,
        }
        tx_cols = ", ".join(tx_data.keys())
        tx_ph = ", ".join("?" for _ in tx_data)
        conn = get_connection()
        try:
            conn.execute(
                f"INSERT INTO invest_holdings_transactions ({tx_cols}) VALUES ({tx_ph})",
                tuple(tx_data.values()),
            )
            conn.commit()
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


@router.post("/holdings/{id}/adjust", response_model=Holding)
async def adjust_holding(id: int, body: HoldingAdjustRequest):
    if body.type not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="type must be 'buy' or 'sell'")

    holding = await db.get_holding_by_id(id)
    if holding is None:
        raise HTTPException(status_code=404, detail="Holding not found")

    old_qty = holding.get("quantity") or 0
    old_avg = holding.get("avg_cost") or 0.0

    if body.type == "sell" and body.quantity > old_qty:
        raise HTTPException(status_code=400, detail="Cannot sell more than held")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")

    tx_data = {
        "holding_id": id,
        "type": body.type,
        "quantity": body.quantity,
        "price": body.price,
        "date": today,
        "notes": body.notes,
    }
    tx_cols = ", ".join(tx_data.keys())
    tx_ph = ", ".join("?" for _ in tx_data)

    if body.type == "buy":
        new_qty = old_qty + body.quantity
        new_avg = (old_qty * old_avg + body.quantity * body.price) / new_qty if new_qty else 0.0
    else:
        new_qty = old_qty - body.quantity
        new_avg = old_avg

    new_status = "closed" if new_qty == 0 else holding.get("status", "active")

    conn = get_connection()
    try:
        conn.execute(
            f"INSERT INTO invest_holdings_transactions ({tx_cols}) VALUES ({tx_ph})",
            tuple(tx_data.values()),
        )
        conn.execute(
            "UPDATE invest_holdings SET quantity=?, avg_cost=?, status=?, updated_at=? WHERE id=?",
            (new_qty, round(new_avg, 4), new_status, now, id),
        )
        conn.commit()
    finally:
        conn.close()

    return await db.get_holding_by_id(id)


@router.get("/holdings/{id}/transactions", response_model=list[HoldingTransaction])
async def list_holding_transactions(id: int):
    return await db.get_holding_transactions(id)


@router.get("/sector-rules", response_model=list[SectorRule])
async def list_sector_rules():
    return await db.get_sector_rules()


@router.put("/sector-rules/{sector}", response_model=SectorRule)
async def update_sector_rule(sector: str, body: SectorRuleUpdate):
    updates: dict[str, object] = {}
    if body.stop_loss_pct is not None:
        updates["stop_loss_pct"] = body.stop_loss_pct
    if body.target_pct is not None:
        updates["target_pct"] = body.target_pct

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updates["updated_at"] = now

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [sector]

    conn = get_connection()
    try:
        cur = conn.execute(
            f"UPDATE invest_sector_rules SET {set_clause} WHERE sector=?",
            tuple(values),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Sector rule not found")
    finally:
        conn.close()

    rules = await db.get_sector_rules()
    for r in rules:
        if r["sector"] == sector:
            return r
    raise HTTPException(status_code=404, detail="Sector rule not found after update")


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
