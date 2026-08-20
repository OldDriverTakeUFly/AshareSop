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


def min_buy_lots(ts_code: str) -> int:
    """Return the minimum BUY order size (shares) for a board.

    科创板（688/689 开头）限价单笔申报下限 200 股（卖出余额不受限）；
    其余板块（含创业板/北交所）100 股整手。小资金账户若按 100 股买
    科创板，产生的模拟成交在真实市场不可执行——所有买入定仓/可买性
    判断必须用本函数，而不是裸的 100 整手。
    """
    code = (ts_code or "").split(".")[0]
    return 200 if code.startswith(("688", "689")) else _BOARD_LOT


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
        if row is None:
            return 0.0
        return row["initial_capital"]

    @property
    def cash(self) -> float:
        row = self._conn.execute(
            "SELECT cash FROM paper_accounts WHERE id=?", (self.account_id,)
        ).fetchone()
        if row is None:
            # Account was deleted by a concurrent process (e.g. sweep rerun).
            # Return 0 rather than crashing — caller can detect via account state.
            return 0.0
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

    def _begin_immediate(self) -> None:
        """Open a write transaction up front (read-modify-write serialization).

        BEGIN IMMEDIATE takes the DB write lock before any read, so the
        cash/position checks inside buy/sell see committed state and cannot
        interleave with a concurrent writer.
        """
        if self._conn.in_transaction:
            # Stray transaction left by a previously failed op — discard it.
            self._conn.rollback()
        self._conn.execute("BEGIN IMMEDIATE")

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
        if shares < min_buy_lots(ts_code) or price <= 0:
            return None

        self._begin_immediate()
        try:
            gross = shares * price
            cost = _trade_cost(gross, commission_bps, stamp_tax_bps, is_sell=False)
            cash = self.cash
            if gross + cost > cash:
                # Trim to affordable (board-lot aligned)
                affordable = int(
                    (cash / (price * (1 + commission_bps / 1e4))) // _BOARD_LOT
                ) * _BOARD_LOT
                if affordable < min_buy_lots(ts_code):
                    self._conn.rollback()
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
        except Exception:
            self._conn.rollback()
            raise

        # 登记 到 watchlist（供盘前报告/飞书推送引用实盘参考价）
        # 仅对新开仓登记（加仓不重复更新），记录买入价 + 日期 + 信号
        if not existing:
            self._register_buy_to_watchlist(
                ts_code=ts_code, name=name, price=price,
                trade_date=trade_date, signal_reason=signal_reason,
            )

        return trade

    def _register_buy_to_watchlist(
        self, ts_code: str, name: str, price: float,
        trade_date: str, signal_reason: str,
    ) -> None:
        """把模拟买入价登记到 invest_watchlist（供实盘参考）.

        在 notes 字段追加格式化记录（不改 schema）：
        "📊 模拟买入@141.56(20260804) 因子71.9"
        盘前报告 / intraday_holdings_alert 可读此字段展示参考价。

        仅在前向测试账户（非回测）生效——回测账户名含 'backtest'/'abtest' 时跳过。
        """
        # 回测账户跳过（避免高频写入）
        if any(tag in self.name.lower() for tag in ("backtest", "abtest", "shadow")):
            return
        try:
            # Resolve DB_PATH through the module at call time so tests that
            # monkeypatch stockhot.storage.database.DB_PATH stay isolated.
            from stockhot.storage.database import DB_PATH
            import sqlite3 as _sqlite3
            with _sqlite3.connect(str(DB_PATH)) as wl_conn:
                code6 = ts_code.split(".")[0]
                note = f"📊 模拟买入@{price:.2f}({trade_date})"
                if signal_reason:
                    # 提取因子评分（如 "final_score=71.9 top5"）
                    note += f" {signal_reason[:30]}"
                wl_conn.execute(
                    "UPDATE invest_watchlist SET notes=COALESCE(notes, '') || ? "
                    "WHERE code=?",
                    (f" {note}" if True else note, code6),
                )
                wl_conn.commit()
        except Exception:
            pass  # watchlist 登记失败不影响买入执行

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

        self._begin_immediate()
        try:
            pos = self._conn.execute(
                "SELECT shares FROM paper_positions WHERE account_id=? AND ts_code=?",
                (self.account_id, ts_code),
            ).fetchone()
            if pos is None or pos["shares"] <= 0:
                self._conn.rollback()
                return None

            # Can't sell more than held
            shares = min(shares, pos["shares"])
            shares = (shares // _BOARD_LOT) * _BOARD_LOT
            if shares <= 0:
                # Allow selling remaining odd lot if it's all we have
                if pos["shares"] < _BOARD_LOT:
                    shares = pos["shares"]
                else:
                    self._conn.rollback()
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
        except Exception:
            self._conn.rollback()
            raise
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
        # BEGIN IMMEDIATE gives a consistent cash + positions snapshot even
        # when another process is trading this account concurrently.
        self._begin_immediate()
        try:
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
        except Exception:
            self._conn.rollback()
            raise
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


# ── backtest-completeness helpers (abx 脚本复用/续跑判定) ──


def expected_trading_days(start: str, end: str) -> int:
    """Count distinct trading days in [start, end] from the cached daily_price calendar.

    Calendar source matches the project convention (回测日历从缓存日线推导,
    not a dedicated calendar API).
    """
    from stockhot.data_layer.market_db import get_connection as get_market_conn

    with get_market_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT trade_date) FROM daily_price WHERE trade_date BETWEEN ? AND ?",
            (start, end),
        ).fetchone()
    return int(row[0]) if row and row[0] else 0


def account_nav_complete(name: str, start: str, end: str) -> bool:
    """True if the account's NAV history covers the full [start, end] trading calendar.

    Replacement for the old ``COUNT(nav) >= 120`` heuristic: an interrupted
    run past day 120 of a ~130-day window used to pass as "complete" and
    silently feed partial results into cross-experiment comparisons.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM paper_nav_history v "
            "JOIN paper_accounts a ON v.account_id=a.id WHERE a.name=?",
            (name,),
        ).fetchone()
    finally:
        conn.close()
    n_nav = int(row[0]) if row else 0
    expected = expected_trading_days(start, end)
    return expected > 0 and n_nav >= expected
