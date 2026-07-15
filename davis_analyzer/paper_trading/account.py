"""DB-backed paper-trading account.

Wraps the trade-execution math from ``davis_analyzer.backtest`` (A-share 100-lot
board size, commission both sides, stamp duty sell-only) with SQLite persistence
in ``stockhot.db`` (``paper_*`` tables).

Unlike the in-memory ``backtest.Portfolio``, this account survives across
process invocations — each daily run loads state from DB, executes trades, and
writes the updated state + NAV snapshot back.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from stockhot.core.config import DB_PATH
from stockhot.storage.database import get_connection

# Reuse the exact trade-cost function from the backtest engine.
from davis_analyzer.backtest import _trade_cost

_BOARD_LOT = 100  # A-share minimum lot


@dataclass
class Position:
    """A held position snapshot."""

    ts_code: str
    name: str
    shares: int
    avg_cost: float
    entry_date: str
    signal_reason: str = ""


@dataclass
class TradeRecord:
    """One executed virtual order."""

    trade_date: str
    ts_code: str
    name: str
    action: str  # "BUY" / "SELL"
    shares: int
    price: float
    amount: float
    cost: float
    signal_reason: str = ""


@dataclass
class NAVSnapshot:
    """Daily mark-to-market snapshot."""

    trade_date: str
    cash: float
    positions_value: float
    total_equity: float
    daily_return: float | None = None


class PaperAccount:
    """A virtual trading account persisted in ``stockhot.db``.

    Create via :meth:`create` (new account) or :meth:`load` (existing).
    """

    def __init__(self, account_id: int, name: str, strategy_name: str) -> None:
        self.account_id = account_id
        self.name = name
        self.strategy_name = strategy_name
        self._conn = get_connection()

    # ── factory methods ──

    @classmethod
    def create(
        cls,
        name: str,
        strategy_name: str,
        initial_capital: float = 1_000_000.0,
        config: dict[str, Any] | None = None,
    ) -> PaperAccount:
        """Create a new paper-trading account. Raises if name exists."""
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO paper_accounts (name, strategy_name, initial_capital, cash, config_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    name,
                    strategy_name,
                    initial_capital,
                    initial_capital,
                    json.dumps(config or {}, ensure_ascii=False),
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError(f"Paper account '{name}' already exists")
        finally:
            conn.close()

        account = cls.load(name)
        return account

    @classmethod
    def load(cls, name: str) -> PaperAccount:
        """Load an existing account by name. Raises if not found."""
        conn = get_connection()
        row = conn.execute(
            "SELECT id, name, strategy_name FROM paper_accounts WHERE name=?", (name,)
        ).fetchone()
        conn.close()
        if row is None:
            raise ValueError(f"Paper account '{name}' not found")
        return cls(row["id"], row["name"], row["strategy_name"])

    @classmethod
    def list_accounts(cls) -> list[dict]:
        """List all paper accounts with summary stats."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT a.id, a.name, a.strategy_name, a.initial_capital, a.cash, "
            "a.status, a.created_at, "
            "(SELECT MAX(trade_date) FROM paper_trades WHERE account_id=a.id) AS last_trade, "
            "(SELECT MAX(trade_date) FROM paper_nav_history WHERE account_id=a.id) AS last_nav "
            "FROM paper_accounts a ORDER BY a.created_at DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── properties ──

    @property
    def initial_capital(self) -> float:
        row = self._conn.execute(
            "SELECT initial_capital FROM paper_accounts WHERE id=?", (self.account_id,)
        ).fetchone()
        return row["initial_capital"]

    @property
    def cash(self) -> float:
        row = self._conn.execute(
            "SELECT cash FROM paper_accounts WHERE id=?", (self.account_id,)
        ).fetchone()
        return row["cash"]

    @property
    def config(self) -> dict:
        row = self._conn.execute(
            "SELECT config_json FROM paper_accounts WHERE id=?", (self.account_id,)
        ).fetchone()
        return json.loads(row["config_json"] or "{}")

    def get_positions(self) -> list[Position]:
        """Load all current positions from DB."""
        rows = self._conn.execute(
            "SELECT ts_code, name, shares, avg_cost, entry_date, signal_reason "
            "FROM paper_positions WHERE account_id=?",
            (self.account_id,),
        ).fetchall()
        return [
            Position(
                ts_code=r["ts_code"],
                name=r["name"],
                shares=r["shares"],
                avg_cost=r["avg_cost"],
                entry_date=r["entry_date"],
                signal_reason=r["signal_reason"] or "",
            )
            for r in rows
        ]

    # ── trade execution ──

    def buy(
        self,
        ts_code: str,
        name: str,
        shares: int,
        price: float,
        trade_date: str,
        commission_bps: float = 2.5,
        stamp_tax_bps: float = 10.0,
        signal_reason: str = "",
    ) -> TradeRecord | None:
        """Execute a virtual buy. Returns the trade record, or None if failed."""
        # Enforce board lot
        shares = (shares // _BOARD_LOT) * _BOARD_LOT
        if shares <= 0 or price <= 0:
            return None

        gross = shares * price
        cost = _trade_cost(gross, commission_bps, stamp_tax_bps, is_sell=False)
        if gross + cost > self.cash:
            # Trim to affordable (board-lot aligned)
            affordable = int(
                (self.cash / (price * (1 + commission_bps / 1e4))) // _BOARD_LOT
            ) * _BOARD_LOT
            if affordable <= 0:
                return None
            shares = affordable
            gross = shares * price
            cost = _trade_cost(gross, commission_bps, stamp_tax_bps, is_sell=False)

        # Update cash
        self._conn.execute(
            "UPDATE paper_accounts SET cash=cash-?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (gross + cost, self.account_id),
        )

        # Upsert position (add to existing or create new)
        existing = self._conn.execute(
            "SELECT shares, avg_cost FROM paper_positions WHERE account_id=? AND ts_code=?",
            (self.account_id, ts_code),
        ).fetchone()
        if existing:
            old_shares = existing["shares"]
            old_cost = existing["avg_cost"]
            new_shares = old_shares + shares
            new_avg = (old_shares * old_cost + gross) / new_shares
            self._conn.execute(
                "UPDATE paper_positions SET shares=?, avg_cost=? WHERE account_id=? AND ts_code=?",
                (new_shares, new_avg, self.account_id, ts_code),
            )
        else:
            self._conn.execute(
                "INSERT INTO paper_positions (account_id, ts_code, name, shares, avg_cost, entry_date, signal_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (self.account_id, ts_code, name, shares, gross / shares, trade_date, signal_reason),
            )

        # Record trade
        trade = TradeRecord(
            trade_date=trade_date,
            ts_code=ts_code,
            name=name,
            action="BUY",
            shares=shares,
            price=price,
            amount=gross,
            cost=cost,
            signal_reason=signal_reason,
        )
        self._record_trade(trade)
        self._conn.commit()
        return trade

    def sell(
        self,
        ts_code: str,
        name: str,
        shares: int,
        price: float,
        trade_date: str,
        commission_bps: float = 2.5,
        stamp_tax_bps: float = 10.0,
        signal_reason: str = "",
    ) -> TradeRecord | None:
        """Execute a virtual sell. Returns the trade record, or None if failed."""
        if shares <= 0 or price <= 0:
            return None

        pos = self._conn.execute(
            "SELECT shares FROM paper_positions WHERE account_id=? AND ts_code=?",
            (self.account_id, ts_code),
        ).fetchone()
        if pos is None or pos["shares"] <= 0:
            return None

        # Can't sell more than held
        shares = min(shares, pos["shares"])
        shares = (shares // _BOARD_LOT) * _BOARD_LOT
        if shares <= 0:
            # Allow selling remaining odd lot if it's all we have
            if pos["shares"] < _BOARD_LOT:
                shares = pos["shares"]
            else:
                return None

        gross = shares * price
        cost = _trade_cost(gross, commission_bps, stamp_tax_bps, is_sell=True)

        # Update cash
        self._conn.execute(
            "UPDATE paper_accounts SET cash=cash+?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (gross - cost, self.account_id),
        )

        # Update/delete position
        remaining = pos["shares"] - shares
        if remaining <= 0:
            self._conn.execute(
                "DELETE FROM paper_positions WHERE account_id=? AND ts_code=?",
                (self.account_id, ts_code),
            )
        else:
            self._conn.execute(
                "UPDATE paper_positions SET shares=? WHERE account_id=? AND ts_code=?",
                (remaining, self.account_id, ts_code),
            )

        trade = TradeRecord(
            trade_date=trade_date,
            ts_code=ts_code,
            name=name,
            action="SELL",
            shares=shares,
            price=price,
            amount=gross,
            cost=cost,
            signal_reason=signal_reason,
        )
        self._record_trade(trade)
        self._conn.commit()
        return trade

    def sell_all(
        self,
        ts_code: str,
        name: str,
        price: float,
        trade_date: str,
        signal_reason: str = "",
    ) -> TradeRecord | None:
        """Sell entire position."""
        pos = self._conn.execute(
            "SELECT shares FROM paper_positions WHERE account_id=? AND ts_code=?",
            (self.account_id, ts_code),
        ).fetchone()
        if pos is None or pos["shares"] <= 0:
            return None
        return self.sell(ts_code, name, pos["shares"], price, trade_date, signal_reason=signal_reason)

    # ── NAV ──

    def market_value(self, prices: dict[str, float]) -> float:
        """Total equity = cash + sum(shares × price)."""
        val = self.cash
        for pos in self.get_positions():
            px = prices.get(pos.ts_code)
            if px is not None:
                val += pos.shares * px
        return val

    def positions_value(self, prices: dict[str, float]) -> float:
        """Sum of position market values (excludes cash)."""
        val = 0.0
        for pos in self.get_positions():
            px = prices.get(pos.ts_code)
            if px is not None:
                val += pos.shares * px
        return val

    def record_nav(self, trade_date: str, prices: dict[str, float]) -> NAVSnapshot:
        """Write a daily NAV snapshot. Returns the snapshot."""
        cash = self.cash
        pos_val = self.positions_value(prices)
        total = cash + pos_val

        # Daily return vs previous NAV
        prev = self._conn.execute(
            "SELECT total_equity FROM paper_nav_history WHERE account_id=? ORDER BY trade_date DESC LIMIT 1",
            (self.account_id,),
        ).fetchone()
        daily_return = None
        if prev and prev["total_equity"] > 0:
            daily_return = round((total / prev["total_equity"] - 1) * 100, 4)

        snap = NAVSnapshot(
            trade_date=trade_date,
            cash=round(cash, 2),
            positions_value=round(pos_val, 2),
            total_equity=round(total, 2),
            daily_return=daily_return,
        )

        self._conn.execute(
            "INSERT OR REPLACE INTO paper_nav_history "
            "(account_id, trade_date, cash, positions_value, total_equity, daily_return) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self.account_id, snap.trade_date, snap.cash, snap.positions_value, snap.total_equity, snap.daily_return),
        )
        self._conn.commit()
        return snap

    def get_nav_history(self) -> list[NAVSnapshot]:
        """Load full NAV history."""
        rows = self._conn.execute(
            "SELECT trade_date, cash, positions_value, total_equity, daily_return "
            "FROM paper_nav_history WHERE account_id=? ORDER BY trade_date",
            (self.account_id,),
        ).fetchall()
        return [
            NAVSnapshot(
                trade_date=r["trade_date"],
                cash=r["cash"],
                positions_value=r["positions_value"],
                total_equity=r["total_equity"],
                daily_return=r["daily_return"],
            )
            for r in rows
        ]

    def get_trades(self, limit: int | None = None) -> list[TradeRecord]:
        """Load trade history."""
        sql = (
            "SELECT trade_date, ts_code, name, action, shares, price, amount, cost, signal_reason "
            "FROM paper_trades WHERE account_id=? ORDER BY trade_date DESC, id DESC"
        )
        params: tuple = (self.account_id,)
        if limit:
            sql += " LIMIT ?"
            params = (self.account_id, limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [
            TradeRecord(
                trade_date=r["trade_date"],
                ts_code=r["ts_code"],
                name=r["name"],
                action=r["action"],
                shares=r["shares"],
                price=r["price"],
                amount=r["amount"],
                cost=r["cost"],
                signal_reason=r["signal_reason"] or "",
            )
            for r in rows
        ]

    def has_run_on(self, trade_date: str) -> bool:
        """Check if this account already has a NAV snapshot for *trade_date*."""
        row = self._conn.execute(
            "SELECT 1 FROM paper_nav_history WHERE account_id=? AND trade_date=?",
            (self.account_id, trade_date),
        ).fetchone()
        return row is not None

    # ── internals ──

    def _record_trade(self, trade: TradeRecord) -> None:
        self._conn.execute(
            "INSERT INTO paper_trades "
            "(account_id, trade_date, ts_code, name, action, shares, price, amount, cost, signal_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.account_id,
                trade.trade_date,
                trade.ts_code,
                trade.name,
                trade.action,
                trade.shares,
                trade.price,
                trade.amount,
                trade.cost,
                trade.signal_reason,
            ),
        )

    def close(self) -> None:
        self._conn.close()
