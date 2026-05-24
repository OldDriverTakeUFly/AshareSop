"""Update active holdings with latest market data.

Run daily after market close (e.g., 16:00).
Updates: name, sector, current_price, stop_loss, target_price, position_pct.

Usage:
    python -m stockhot.invest_sop.scripts.update_holdings
"""

import argparse
import os
import traceback
from datetime import datetime

import akshare as ak
import pandas as pd

from stockhot.invest_sop.config import get_sector_rule
from stockhot.storage.database import get_connection

TABLE = "invest_holdings"


def strip_proxy() -> dict[str, str]:
    """Remove proxy env vars, return dict for restoration."""
    removed: dict[str, str] = {}
    for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"]:
        if key in os.environ:
            removed[key] = os.environ.pop(key)
    return removed


def restore_proxy(removed: dict[str, str]) -> None:
    os.environ.update(removed)


def _call_akshare(method_name: str, **kwargs):
    """Call an akshare method with proxy stripping."""
    removed = strip_proxy()
    try:
        method = getattr(ak, method_name)
        return method(**kwargs)
    finally:
        restore_proxy(removed)


def get_active_holdings() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, code, name, sector, entry_price, quantity, avg_cost FROM invest_holdings WHERE status = 'active'"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def fetch_market_data() -> pd.DataFrame | None:
    """Fetch A-share spot data for all stocks (single API call)."""
    try:
        df = _call_akshare("stock_zh_a_spot_em")
        return df
    except Exception as e:
        print(f"ERROR fetching market data: {e}")
        traceback.print_exc()
        return None


def update_holding(holding: dict, market_df: pd.DataFrame, total_value: float) -> bool:
    """Update a single holding with latest data. Returns True if updated."""
    code = holding["code"]

    # Find the stock in market data
    # stock_zh_a_spot_em columns include: 代码, 名称, 最新价, 涨跌幅, 所属行业
    row = market_df[market_df["代码"] == code]
    if row.empty:
        print(f"  WARN: {code} not found in market data, skipping")
        return False

    row = row.iloc[0]
    current_price = float(row["最新价"]) if pd.notna(row["最新价"]) else None
    name = str(row["名称"]) if pd.notna(row["名称"]) else holding.get("name")
    sector = str(row.get("所属行业", holding.get("sector"))) if pd.notna(row.get("所属行业")) else holding.get("sector")

    if current_price is None:
        print(f"  WARN: {code} has no price, skipping")
        return False

    # Get sector rules for stop-loss and target
    rule = get_sector_rule(sector or "default")

    # Calculate derived values
    entry_price = holding.get("entry_price") or current_price  # Use current price if no entry
    stop_loss_hard = round(entry_price * (1 + rule["stop_loss_pct"]), 2)
    target_price = round(entry_price * (1 + rule["target_pct"]), 2)

    quantity = holding.get("quantity", 0)
    position_pct = round((quantity * current_price / total_value * 100), 2) if total_value > 0 else 0

    # Update DB
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE invest_holdings SET
                name = ?, sector = ?, current_price = ?,
                entry_price = ?, stop_loss_hard = ?, target_price = ?,
                position_pct = ?, updated_at = ?
            WHERE id = ?
        """, (
            name, sector, current_price,
            entry_price, stop_loss_hard, target_price,
            position_pct, datetime.now().isoformat(),
            holding["id"],
        ))
        conn.commit()
    finally:
        conn.close()

    return True


def run(force: bool = False) -> None:
    """Main entry point."""
    print(f"=== update_holdings @ {datetime.now().isoformat()} ===")

    holdings = get_active_holdings()
    if not holdings:
        print("No active holdings to update.")
        return

    print(f"Active holdings: {len(holdings)}")
    for h in holdings:
        print(f"  {h['code']} {h.get('name', '?')} qty={h.get('quantity', 0)}")

    # Fetch all market data in one call
    market_df = fetch_market_data()
    if market_df is None:
        print("ERROR: Could not fetch market data. Aborting.")
        return

    # First pass: calculate total portfolio value
    total_value = 0.0
    for h in holdings:
        row = market_df[market_df["代码"] == h["code"]]
        if not row.empty and pd.notna(row.iloc[0]["最新价"]):
            total_value += (h.get("quantity", 0) or 0) * float(row.iloc[0]["最新价"])

    print(f"Total portfolio value: {total_value:,.2f}")

    # Second pass: update each holding
    updated = 0
    for h in holdings:
        try:
            if update_holding(h, market_df, total_value):
                updated += 1
        except Exception as e:
            print(f"  ERROR updating {h['code']}: {e}")
            traceback.print_exc()

    print(f"Updated {updated}/{len(holdings)} holdings.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update holdings with latest market data")
    parser.add_argument("--force", action="store_true", help="Force update even if already updated today")
    args = parser.parse_args()
    run(force=args.force)
