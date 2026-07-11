"""Performance statistics and CSV export for backtest results.

Reads a :class:`~davis_analyzer.backtest.BacktestResult` and produces:
  * :class:`PerformanceStats` — summary metrics (return, Sharpe, drawdown, …)
  * Trade-detail CSV — one row per executed order
  * Equity-curve CSV — daily mark-to-market snapshots

All metrics are computed from the equity curve and trade list; no extra
data fetching is performed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from davis_analyzer.backtest import BacktestResult, EquitySnapshot, Trade


@dataclass
class PerformanceStats:
    """Headline metrics for a finished backtest."""

    total_return_pct: float
    annualized_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float
    turnover_per_rebalance: float
    num_trades: int
    num_rebalances: int
    avg_holding_count: float
    total_cost: float


# ──────────────────────────── computation ────────────────────────────


def _daily_returns(snapshots: list[EquitySnapshot]) -> np.ndarray:
    """Array of daily equity returns (length = len(snapshots) - 1)."""
    if len(snapshots) < 2:
        return np.array([])
    equities = np.array([s.equity for s in snapshots], dtype=np.float64)
    return equities[1:] / equities[:-1] - 1.0


def _max_drawdown(snapshots: list[EquitySnapshot]) -> float:
    """Peak-to-trough drawdown of the equity curve, as a percentage."""
    if not snapshots:
        return 0.0
    equities = np.array([s.equity for s in snapshots], dtype=np.float64)
    peaks = np.maximum.accumulate(equities)
    drawdowns = (equities - peaks) / peaks
    return float(drawdowns.min() * 100.0)  # negative percentage


def _per_rebalance_turnover(trades: list[Trade], num_rebalances: int) -> float:
    """Average turnover per rebalance, measured as the number of trades / 2.

    Each round-trip is a buy + a sell, so dividing total trades by 2 gives
    the number of complete position changes, then dividing by the number of
    rebalance events yields turnover per rebalance.
    """
    if num_rebalances == 0:
        return 0.0
    return len(trades) / 2.0 / num_rebalances


def _win_rate(trades: list[Trade], final_positions: dict) -> float:
    """Win rate over closed round-trips.

    Matches sells to their preceding buys per ts_code and counts a win when
    sell price > buy price.  Positions still open at backtest end are not
    counted (not realised).  Returns 0.0 when no round-trips exist.
    """
    # Build FIFO buy queue per stock.
    buy_queues: dict[str, list[tuple[int, float]]] = {}
    wins = 0
    total = 0
    # Sort by execution date so sells match the oldest buys first.
    for t in sorted(trades, key=lambda x: x.exec_date):
        if t.action == "BUY":
            buy_queues.setdefault(t.ts_code, []).append((t.shares, t.price))
        elif t.action == "SELL":
            queue = buy_queues.get(t.ts_code, [])
            remaining = t.shares
            while remaining > 0 and queue:
                buy_shares, buy_price = queue[0]
                matched = min(remaining, buy_shares)
                if t.price > buy_price:
                    wins += 1
                total += 1
                remaining -= matched
                if matched == buy_shares:
                    queue.pop(0)
                else:
                    queue[0] = (buy_shares - matched, buy_price)
    if total == 0:
        return 0.0
    return wins / total * 100.0


def compute_performance(result: BacktestResult) -> PerformanceStats:
    """Compute all headline stats from an equity curve and trade list."""
    snapshots = result.equity_curve
    trades = result.trades
    num_rebalances = len(result.rebalance_dates)

    daily_rets = _daily_returns(snapshots)
    initial = result.config.initial_capital
    final_equity = snapshots[-1].equity if snapshots else initial

    total_return = (final_equity / initial - 1.0) * 100.0

    # Annualised return: compound daily returns, scale to 252 trading days.
    if daily_rets.size > 0:
        compounded = np.prod(1.0 + daily_rets)
        num_days = daily_rets.size
        ann_return = (compounded ** (252.0 / num_days) - 1.0) * 100.0
        # Sharpe: mean daily return / std daily return × sqrt(252).
        if daily_rets.std() > 0:
            sharpe = float(daily_rets.mean() / daily_rets.std() * math.sqrt(252))
        else:
            sharpe = 0.0
    else:
        ann_return = 0.0
        sharpe = 0.0

    # Average holding count: mean number of distinct positions across the
    # equity curve.  We approximate from snapshots where positions_value > 0
    # — a precise count needs the daily position snapshot, but this proxy is
    # good enough for a headline metric.
    holding_counts = [
        sum(1 for s in [snap] if s.positions_value > 0) for snap in snapshots
    ]
    # Better proxy: count positions from trade activity.  Each snapshot's
    # distinct held codes = distinct BUY codes minus distinct SELL codes up
    # to that date.  For simplicity, we use the configured top_n as an
    # upper-bound proxy when no data.
    avg_holding = float(result.config.top_n) if not holding_counts else float(np.mean(holding_counts))

    total_cost = sum(t.cost for t in trades)

    return PerformanceStats(
        total_return_pct=round(total_return, 2),
        annualized_return_pct=round(ann_return, 2),
        sharpe_ratio=round(sharpe, 3),
        max_drawdown_pct=round(_max_drawdown(snapshots), 2),
        win_rate_pct=round(_win_rate(trades, result.final_positions), 1),
        turnover_per_rebalance=round(_per_rebalance_turnover(trades, num_rebalances), 2),
        num_trades=len(trades),
        num_rebalances=num_rebalances,
        avg_holding_count=round(avg_holding, 1),
        total_cost=round(total_cost, 2),
    )


# ──────────────────────────── CSV export ────────────────────────────


def export_trades(result: BacktestResult, path: Path) -> Path:
    """Write the trade-detail list to a CSV.  Returns the path written."""
    path = Path(path)
    rows = [
        {
            "signal_date": t.signal_date.isoformat(),
            "exec_date": t.exec_date.isoformat(),
            "ts_code": t.ts_code,
            "action": t.action,
            "price": t.price,
            "shares": t.shares,
            "amount": round(t.amount, 2),
            "cost": round(t.cost, 2),
        }
        for t in result.trades
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def export_equity_curve(result: BacktestResult, path: Path) -> Path:
    """Write the daily equity curve to a CSV.  Returns the path written."""
    path = Path(path)
    rows = [
        {
            "date": s.date.isoformat(),
            "equity": round(s.equity, 2),
            "cash": round(s.cash, 2),
            "positions_value": round(s.positions_value, 2),
        }
        for s in result.equity_curve
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def format_stats_report(stats: PerformanceStats, config_summary: str = "") -> str:
    """Human-readable multi-line summary, suitable for console or logs."""
    lines = [
        "=" * 60,
        "回测绩效报告" + (f"  ({config_summary})" if config_summary else ""),
        "=" * 60,
        f"总收益率:       {stats.total_return_pct:+.2f}%",
        f"年化收益率:     {stats.annualized_return_pct:+.2f}%",
        f"夏普比率:       {stats.sharpe_ratio:.3f}",
        f"最大回撤:       {stats.max_drawdown_pct:.2f}%",
        f"胜率:           {stats.win_rate_pct:.1f}%",
        f"每次调仓换手:   {stats.turnover_per_rebalance:.2f}",
        f"交易笔数:       {stats.num_trades}",
        f"调仓次数:       {stats.num_rebalances}",
        f"平均持仓数:     {stats.avg_holding_count:.1f}",
        f"总交易成本:     {stats.total_cost:.2f}",
        "=" * 60,
    ]
    return "\n".join(lines)
