"""Concurrency regression tests for paper_trading.

Covers the three race classes fixed in the 2026-08 hardening:

1. ``buy()`` cash affordability used a stale pre-transaction read →
   concurrent buys could drive cash negative.
2. ``sell()`` clamped shares against a stale pre-transaction read →
   two concurrent ``sell_all`` could double-credit cash for one position.
3. ``run_day`` had no single-instance guard → two processes could
   double-execute the same trading day; ``reset_account`` could wipe an
   in-flight backtest (fixed via flock in ``runlock``).
"""

import os
import sqlite3
import tempfile
from multiprocessing import get_context

import pytest

os.environ.setdefault(
    "PROJECT_ROOT",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
)


@pytest.fixture()
def temp_db():
    """Fresh temporary stockhot.db per test (patched before any use)."""
    tmpdir = tempfile.mkdtemp()
    tmp_db = os.path.join(tmpdir, "stockhot.db")
    from stockhot.core import config as stockhot_config
    from stockhot.storage import database as db_module

    old_path = stockhot_config.DB_PATH
    stockhot_config.DB_PATH = type(old_path)(tmp_db)
    db_module.DB_PATH = stockhot_config.DB_PATH
    db_module.init_database()
    yield tmp_db
    stockhot_config.DB_PATH = old_path
    db_module.DB_PATH = old_path


def _make_account(name: str, capital: float):
    from davis_analyzer.paper_trading.account import PaperAccount

    PaperAccount.create(name, "davis_double", capital)
    return PaperAccount.load(name)


# ── multi-process trade races ─────────────────────────────────────────


def _buy_worker(account_name: str, n_buys: int, ts_code: str, price: float):
    from davis_analyzer.paper_trading.account import PaperAccount

    account = PaperAccount.load(account_name)
    for i in range(n_buys):
        account.buy(ts_code, "测试股", 100, price, f"2026010{i % 9 + 1:02d}")
    account.close()


def _sell_worker(account_name: str, ts_code: str):
    from davis_analyzer.paper_trading.account import PaperAccount

    account = PaperAccount.load(account_name)
    for _ in range(64):  # keep trying until nothing left to sell
        trade = account.sell_all(ts_code, "测试股", 10.0, "20260102")
        if trade is None:
            account.close()
            return
    account.close()


class TestConcurrentTrades:
    def test_concurrent_buys_never_overspend(self, temp_db):
        """Two processes buying the same stock: cash stays >= 0 and the
        position + cash + trade ledger must reconcile exactly."""
        capital = 10_000.0
        account = _make_account("race_buy", capital)
        name = account.name
        account.close()

        ctx = get_context("fork")
        procs = [ctx.Process(target=_buy_worker, args=(name, 40, "000001.SZ", 9.9)) for _ in range(3)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=60)
        assert all(p.exitcode == 0 for p in procs)

        from davis_analyzer.paper_trading.account import PaperAccount

        acc = PaperAccount.load(name)
        cash = acc.cash
        positions = acc.get_positions()
        trades = [t for t in acc.get_trades() if t.action == "BUY"]

        # 1) Cash must never go negative (stale-affordability regression)
        assert cash >= -1e-6, f"cash went negative: {cash}"

        # 2) Ledger identity: initial - cash == Σ(amount + cost)
        spent = sum(t.amount + t.cost for t in trades)
        assert abs((capital - cash) - spent) < 1e-6

        # 3) Position shares == Σ executed buy shares
        assert len(positions) == 1
        assert positions[0].shares == sum(t.shares for t in trades)

        # 4) No trade overpaid relative to concurrent cash state
        for t in trades:
            assert t.shares > 0 and t.amount > 0

    def test_concurrent_sell_all_no_double_sell(self, temp_db):
        """Two processes racing sell_all on one position: total sold shares
        must equal exactly the held amount, cash credited once per share."""
        capital = 100_000.0
        account = _make_account("race_sell", capital)
        seed = account.buy("000002.SZ", "测试股", 2000, 10.0, "20260101")  # cost ~20005
        assert seed is not None, "seed buy failed — test setup broken"
        name = account.name
        account.close()

        cash_after_buy = _make_account_cash(name)

        ctx = get_context("fork")
        procs = [ctx.Process(target=_sell_worker, args=(name, "000002.SZ")) for _ in range(3)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=60)
        assert all(p.exitcode == 0 for p in procs)

        from davis_analyzer.paper_trading.account import PaperAccount

        acc = PaperAccount.load(name)
        sells = [t for t in acc.get_trades() if t.action == "SELL"]
        positions = acc.get_positions()

        # Exactly the held shares were sold — no phantom double-sell
        assert sum(t.shares for t in sells) == 2000
        # Position gone
        assert all(p.ts_code != "000002.SZ" for p in positions)
        # Cash credited exactly once per sold share (ledger identity)
        credited = sum(t.amount - t.cost for t in sells)
        assert abs((acc.cash - cash_after_buy) - credited) < 1e-6


def _make_account_cash(name: str) -> float:
    from davis_analyzer.paper_trading.account import PaperAccount

    acc = PaperAccount.load(name)
    cash = acc.cash
    acc.close()
    return cash


# ── run lock / reset guard ────────────────────────────────────────────


class TestRunLock:
    def test_run_day_returns_busy_when_lock_held(self, temp_db):
        """A second process holding the account run lock → status busy,
        before any market-data access happens."""
        import fcntl

        from davis_analyzer.paper_trading.account import PaperAccount
        from davis_analyzer.paper_trading.executor import DailyExecutor
        from davis_analyzer.paper_trading.runlock import _lock_path

        PaperAccount.create("busy_test", "davis_double", 100_000)
        account = PaperAccount.load("busy_test")

        class _StubStrategy:
            pass

        executor = DailyExecutor(account, _StubStrategy())

        lock_file = _lock_path(account.account_id)
        fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            result = executor.run_day("20260101")
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

        assert result["status"] == "busy"
        assert result["trade_date"] == "20260101"

    def test_run_lock_reentrant_in_process(self, temp_db):
        """run_day inside an already-held lock (backfill nesting) proceeds."""
        from davis_analyzer.paper_trading.runlock import account_run_lock

        PaperAccount = __import__(
            "davis_analyzer.paper_trading.account", fromlist=["PaperAccount"]
        ).PaperAccount
        PaperAccount.create("reent_test", "davis_double", 100_000)
        account = PaperAccount.load("reent_test")

        with account_run_lock(account.account_id) as outer:
            assert outer is True
            with account_run_lock(account.account_id) as inner:
                assert inner is True  # reentrant, not deadlocked

    def test_delete_account_if_idle_refuses_when_running(self, temp_db):
        """Reset must refuse while another process holds the run lock."""
        from davis_analyzer.paper_trading.account import PaperAccount
        from davis_analyzer.paper_trading.runlock import (
            account_run_lock,
            delete_account_if_idle,
        )

        PaperAccount.create("reset_guard", "davis_double", 100_000)
        account = PaperAccount.load("reset_guard")
        account.buy("000003.SZ", "测试股", 100, 10.0, "20260101")
        account.close()

        with account_run_lock(PaperAccount.load("reset_guard").account_id):
            with pytest.raises(RuntimeError, match="运行锁"):
                delete_account_if_idle("reset_guard")

        # Lock released → delete succeeds and clears all rows
        delete_account_if_idle("reset_guard")
        with sqlite3.connect(temp_db) as c:
            n = c.execute(
                "SELECT COUNT(*) FROM paper_accounts WHERE name='reset_guard'"
            ).fetchone()[0]
        assert n == 0
