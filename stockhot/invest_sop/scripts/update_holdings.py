"""Update active holdings with latest market data.

Run daily after market close (e.g., 16:00).
Updates: name, sector, current_price, stop_loss, target_price, position_pct.

非交易日静默跳过（避免周末/节假日跑空）。

Usage:
    python -m stockhot.invest_sop.scripts.update_holdings [--dry-run]

Crontab (每日收盘后 16:00):
    0 16 * * 1-5 cd /path && PYTHONPATH=/path \\
        .venv/bin/python stockhot/invest_sop/scripts/update_holdings.py \\
        >> stockhot/invest_sop/logs/update_holdings.log 2>&1
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import date, datetime

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
    sector = (
        str(row.get("所属行业", holding.get("sector")))
        if pd.notna(row.get("所属行业"))
        else holding.get("sector")
    )

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
    position_pct = (
        round((quantity * current_price / total_value * 100), 2) if total_value > 0 else 0
    )

    # Update DB
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE invest_holdings SET
                name = ?, sector = ?, current_price = ?,
                entry_price = ?, stop_loss_hard = ?, target_price = ?,
                position_pct = ?, updated_at = ?
            WHERE id = ?
        """,
            (
                name,
                sector,
                current_price,
                entry_price,
                stop_loss_hard,
                target_price,
                position_pct,
                datetime.now().isoformat(),
                holding["id"],
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return True


def run(force: bool = False, dry_run: bool = False) -> None:
    """Main entry point.

    Args:
        force: Force update even if already updated today（保留向后兼容，当前未做去重）
        dry_run: 只读取并打印将要更新的值，不写数据库
    """
    mode = "[DRY-RUN] " if dry_run else ""
    print(f"=== update_holdings {mode}@ {datetime.now().isoformat()} ===")

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
            if dry_run:
                # dry-run 只打印不写库
                _dry_run_print(h, market_df, total_value)
            elif update_holding(h, market_df, total_value):
                updated += 1
        except Exception as e:
            print(f"  ERROR updating {h['code']}: {e}")
            traceback.print_exc()

    if not dry_run:
        print(f"Updated {updated}/{len(holdings)} holdings.")


def _dry_run_print(holding: dict, market_df: pd.DataFrame, total_value: float) -> None:
    """dry-run 模式：计算并打印将要写入的值，不操作数据库."""
    code = holding["code"]
    row = market_df[market_df["代码"] == code]
    if row.empty:
        print(f"  [DRY-RUN] {code} not found in market data, would skip")
        return
    row = row.iloc[0]
    current_price = float(row["最新价"]) if pd.notna(row["最新价"]) else None
    if current_price is None:
        print(f"  [DRY-RUN] {code} has no price, would skip")
        return
    sector = str(row.get("所属行业", holding.get("sector"))) if pd.notna(row.get("所属行业")) else holding.get("sector")
    rule = get_sector_rule(sector or "default")
    entry_price = holding.get("entry_price") or current_price
    stop_loss_hard = round(entry_price * (1 + rule["stop_loss_pct"]), 2)
    target_price = round(entry_price * (1 + rule["target_pct"]), 2)
    quantity = holding.get("quantity", 0)
    position_pct = round(quantity * current_price / total_value * 100, 2) if total_value > 0 else 0
    print(
        f"  [DRY-RUN] {code} {holding.get('name','?')}: "
        f"current_price {holding.get('current_price')}→{current_price}, "
        f"stop_loss_hard→{stop_loss_hard}, target→{target_price}, "
        f"position_pct→{position_pct}%"
    )


def main(argv: list[str] | None = None) -> int:
    """Cron 入口：交易日校验 + 调用 run(). 返回 0 成功 / 1 失败."""
    parser = argparse.ArgumentParser(description="Update holdings with latest market data")
    parser.add_argument(
        "--force", action="store_true", help="Force update even if already updated today"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只读取并打印将要更新的值，不写数据库"
    )
    args = parser.parse_args(argv)

    # 交易日校验（非交易日静默跳过）
    today = date.today().isoformat()
    try:
        from stockhot.invest_sop.utils.trading_calendar import is_trading_day

        if not is_trading_day(today):
            print(f"[{today}] 非交易日，跳过持仓更新")
            return 0
    except Exception as e:
        # 日历失败不阻断（宁可多跑，也不因日历故障漏更新）
        print(f"[WARN] 交易日校验失败（{e}），继续执行")

    try:
        run(force=args.force, dry_run=args.dry_run)
        return 0
    except Exception as e:
        print(f"[ERROR] update_holdings 失败: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
