"""Tests for Signal.sell_at_open — executor 次日开盘价卖出扩展.

Spec: docs/superpowers/specs/2026-08-17-limitup-phase3-design.md §3.2 第 2 条.
语义与 limitup 回测引擎 (engine.py) 对齐:
- sell_at_open=True → 成交价 = 当日 open × (1 − 10bps)
- open 缺失（停牌/数据缺口）或一字跌停（open=low=跌停价）→ 顺延:
  当日不卖、保留持仓，下一 run_day 策略重发 SELL 自然重试
- sell_at_open=False（默认）→ 原收盘价路径，零变化（回归）
"""

import os
import sqlite3
import tempfile

import pytest

# Ensure PROJECT_ROOT before any stockhot import
os.environ.setdefault(
    "PROJECT_ROOT",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)


@pytest.fixture()
def temp_db():
    """Use a temporary stockhot.db for paper-trading tests."""
    tmpdir = tempfile.mkdtemp()
    tmp_db = os.path.join(tmpdir, "stockhot.db")
    from stockhot.core import config as stockhot_config

    old_path = stockhot_config.DB_PATH
    stockhot_config.DB_PATH = type(old_path)(tmp_db)
    from stockhot.storage import database as db_module

    db_module.DB_PATH = stockhot_config.DB_PATH
    db_module.init_database()
    yield tmp_db
    stockhot_config.DB_PATH = old_path
    db_module.DB_PATH = old_path


# ── Test doubles ───────────────────────────────────────────────────────


class _StubStrategy:
    """Minimal Strategy stand-in: emits preset signals on every evaluate."""

    name = "stub"

    def __init__(self, signals: list) -> None:
        self._signals = signals

    def evaluate(self, positions, snapshot, total_equity) -> list:
        return list(self._signals)


class _CtxConn:
    """Wrap a sqlite3 connection so it works in `with ... as conn:` blocks."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, *args) -> bool:
        return False


def _patch_run_day_env(monkeypatch, closes: dict, open_rows: dict | None = None) -> None:
    """Stub every external dependency DailyExecutor.run_day touches.

    Pure in-memory — no real DB, no Tushare API.
    """
    from davis_analyzer.paper_trading import executor as ex

    monkeypatch.setattr(ex, "_get_close_prices", lambda codes, d: dict(closes))
    monkeypatch.setattr(ex, "_get_open_prices", lambda codes, d: dict(open_rows or {}))
    monkeypatch.setattr(ex, "_get_market_regime", lambda d: "neutral")
    monkeypatch.setattr(ex, "_get_overseas_risk", lambda d: 0.0)
    monkeypatch.setattr(ex, "_get_market_vol_regime", lambda d: ("normal_vol", 1.0))
    monkeypatch.setattr(ex, "_get_industries", lambda codes: {})
    monkeypatch.setattr(ex, "_infer_industry_trends", lambda fd, ind, trade_date=None: {})
    monkeypatch.setattr(ex, "_compute_short_momentum", lambda codes, d: {})
    monkeypatch.setattr(ex, "_compute_mom60", lambda codes, d: {})
    monkeypatch.setattr(ex, "_compute_pe_percentiles", lambda codes, d: {})
    monkeypatch.setattr(ex, "_compute_volatilities", lambda codes, d: {})
    monkeypatch.setattr(ex, "_compute_volume_signals", lambda codes, d: {})
    monkeypatch.setattr(ex, "_compute_rv_decay_ratio", lambda d: None)
    monkeypatch.setattr(ex, "_compute_index_20d_drop", lambda d: None)
    monkeypatch.setattr(ex, "_compute_stock_20d_drops", lambda codes, d: {})
    monkeypatch.setattr(ex, "_compute_vol_ratio_250", lambda d: None)
    monkeypatch.setattr(ex, "_get_stock_name", lambda c: c)
    monkeypatch.setattr(ex, "_get_daily_pct_chg", lambda c, d: 0.0)
    monkeypatch.setattr(ex, "_record_shadow_trade", lambda *a, **k: None)
    monkeypatch.setattr(ex, "_update_shadow_tracking", lambda *a, **k: None)
    # ivix read: bare in-memory DB without ivix_history → exception swallowed
    monkeypatch.setattr(
        ex, "get_market_conn", lambda: _CtxConn(sqlite3.connect(":memory:"))
    )
    monkeypatch.setattr(ex.DailyExecutor, "_check_risk_signals", lambda self, *a, **k: [])


def _make_executor(name: str, signals: list):
    from davis_analyzer.paper_trading.account import PaperAccount
    from davis_analyzer.paper_trading.executor import DailyExecutor

    account = PaperAccount.create(name, "stub", 1_000_000)
    executor = DailyExecutor(account, _StubStrategy(signals))
    executor.enable_t_trading = False
    return executor


# ── Signal dataclass ───────────────────────────────────────────────────


def test_signal_sell_at_open_defaults_false():
    """新字段带默认值——既有策略构造 Signal 零影响."""
    from davis_analyzer.paper_trading.strategy import Signal

    sig = Signal(ts_code="000001.SZ", name="平安银行", action="SELL")
    assert sig.sell_at_open is False


# ── _get_open_prices: 同源读当日 open ─────────────────────────────────


def test_get_open_prices_reads_same_day_rows_only(monkeypatch):
    """只取当日行（无回看回退）；open 为 NULL 的行剔除."""
    from davis_analyzer.paper_trading import executor as ex

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE daily_price (ts_code TEXT, trade_date TEXT, open REAL, "
        "high REAL, low REAL, close REAL, pre_close REAL, pct_chg REAL, "
        "vol REAL, amount REAL, adj_factor REAL, fetched_at REAL)"
    )
    conn.executemany(
        "INSERT INTO daily_price (ts_code, trade_date, open, low, pre_close) "
        "VALUES (?,?,?,?,?)",
        [
            ("000001.SZ", "20260106", 10.0, 9.8, 10.05),
            ("300750.SZ", "20260106", 50.0, 49.0, 51.0),
            ("600519.SH", "20260103", 1600.0, 1590.0, 1610.0),  # 前一日行，不应取到
            ("688981.SH", "20260106", None, None, 100.0),       # open NULL → 剔除
        ],
    )
    conn.commit()
    monkeypatch.setattr(ex, "get_market_conn", lambda: _CtxConn(conn))

    rows = ex._get_open_prices(
        ["000001.SZ", "300750.SZ", "600519.SH", "688981.SH", "000002.SZ"], "20260106"
    )

    assert set(rows) == {"000001.SZ", "300750.SZ"}
    assert rows["000001.SZ"] == {"open": 10.0, "low": 9.8, "pre_close": 10.05}
    assert rows["300750.SZ"]["open"] == 50.0
    conn.close()


def test_get_open_prices_empty_input(monkeypatch):
    from davis_analyzer.paper_trading import executor as ex

    called = []

    def _boom():
        called.append(1)
        raise AssertionError("must not touch DB for empty input")

    monkeypatch.setattr(ex, "get_market_conn", _boom)
    assert ex._get_open_prices([], "20260106") == {}
    assert called == []


# ── _limit_down_locked: 一字跌停判定（与回测引擎同口径） ──────────────


def test_limit_down_locked_main_board_one_word():
    """主板 10cm：pre_close=10 → 跌停价 9.00，open=low=9.00 → 锁死."""
    from davis_analyzer.paper_trading.executor import _limit_down_locked

    assert _limit_down_locked("000010.SZ", open_px=9.00, low=9.00, pre_close=10.00)


def test_limit_down_not_locked_when_open_above_limit():
    """低开但未一字（open=9.50 > 跌停价）→ 可卖."""
    from davis_analyzer.paper_trading.executor import _limit_down_locked

    assert not _limit_down_locked("000010.SZ", open_px=9.50, low=9.00, pre_close=10.00)


def test_limit_down_not_locked_when_low_below_limit():
    """open=跌停价但 low 更低（机械判定不满足 open=low）→ 不视为一字."""
    from davis_analyzer.paper_trading.executor import _limit_down_locked

    assert not _limit_down_locked("000010.SZ", open_px=9.00, low=8.95, pre_close=10.00)


def test_limit_down_locked_gem_20cm():
    """创业板 30 开头 20cm：pre_close=10 → 跌停价 8.00."""
    from davis_analyzer.paper_trading.executor import _limit_down_locked

    assert _limit_down_locked("300750.SZ", open_px=8.00, low=8.00, pre_close=10.00)
    # 10cm 口径下 8.00 不是主板 000010 的跌停价 → 不锁死
    assert not _limit_down_locked("000010.SZ", open_px=8.00, low=8.00, pre_close=10.00)


def test_limit_down_locked_star_20cm():
    """科创板 68 开头 20cm."""
    from davis_analyzer.paper_trading.executor import _limit_down_locked

    assert _limit_down_locked("688981.SH", open_px=8.00, low=8.00, pre_close=10.00)


def test_limit_down_no_valid_pre_close():
    from davis_analyzer.paper_trading.executor import _limit_down_locked

    assert not _limit_down_locked("000010.SZ", open_px=9.00, low=9.00, pre_close=0.0)


# ── run_day 集成：卖出执行分支 ────────────────────────────────────────


class TestSellAtOpenExecution:
    def test_fills_at_open_with_10bps_slippage(self, temp_db, monkeypatch):
        """① sell_at_open=True → 以 open×(1−1e-3) 成交，而非收盘价."""
        from davis_analyzer.paper_trading.strategy import Signal

        executor = _make_executor(
            "sao_fill",
            [Signal(
                ts_code="000010.SZ", name="深测A", action="SELL",
                signal_reason="打板次日开盘卖", sell_at_open=True,
            )],
        )
        executor.account.buy("000010.SZ", "深测A", 1000, 10.0, "20260105")

        _patch_run_day_env(
            monkeypatch,
            closes={"000010.SZ": 10.5},
            open_rows={"000010.SZ": {"open": 10.0, "low": 9.9, "pre_close": 10.2}},
        )
        result = executor.run_day("20260106", factor_scores={})

        assert result["status"] == "ok"
        assert len(result["sell_trades"]) == 1
        assert result["sell_trades"][0]["price"] == pytest.approx(10.0 * (1 - 1e-3))
        assert result["sell_trades"][0]["ts_code"] == "000010.SZ"
        assert executor.account.get_positions() == []
        executor.account.close()

    def test_deferred_when_open_missing_then_refills_next_day(self, temp_db, monkeypatch):
        """② open 缺失（停牌/数据缺口）→ 顺延；次日 open 恢复 → 自然重试成交."""
        from davis_analyzer.paper_trading import executor as ex
        from davis_analyzer.paper_trading.strategy import Signal

        executor = _make_executor(
            "sao_defer",
            [Signal(
                ts_code="000010.SZ", name="深测A", action="SELL",
                signal_reason="打板次日开盘卖", sell_at_open=True,
            )],
        )
        executor.account.buy("000010.SZ", "深测A", 1000, 10.0, "20260105")

        # Day 1: 无当日 open 行 → 顺延不卖、持仓保留
        _patch_run_day_env(monkeypatch, closes={"000010.SZ": 10.5}, open_rows={})
        r1 = executor.run_day("20260106", factor_scores={})
        assert r1["status"] == "ok"
        assert r1["sell_trades"] == []
        positions = executor.account.get_positions()
        assert len(positions) == 1 and positions[0].ts_code == "000010.SZ"

        # Day 2: 策略重发 SELL，open 恢复 → 开盘价成交
        monkeypatch.setattr(
            ex, "_get_open_prices",
            lambda codes, d: {"000010.SZ": {"open": 10.2, "low": 10.0, "pre_close": 10.5}},
        )
        r2 = executor.run_day("20260107", factor_scores={})
        assert len(r2["sell_trades"]) == 1
        assert r2["sell_trades"][0]["price"] == pytest.approx(10.2 * (1 - 1e-3))
        assert executor.account.get_positions() == []
        executor.account.close()

    def test_deferred_on_one_word_limit_down(self, temp_db, monkeypatch):
        """③ 一字跌停（open=low=跌停价）→ 顺延不卖、持仓保留."""
        from davis_analyzer.paper_trading.strategy import Signal

        executor = _make_executor(
            "sao_lock",
            [Signal(
                ts_code="000010.SZ", name="深测A", action="SELL",
                signal_reason="打板次日开盘卖", sell_at_open=True,
            )],
        )
        executor.account.buy("000010.SZ", "深测A", 1000, 10.0, "20260105")

        # 主板 pre_close=10.00 → 跌停价 9.00；open=low=9.00 → 一字跌停
        _patch_run_day_env(
            monkeypatch,
            closes={"000010.SZ": 9.0},
            open_rows={"000010.SZ": {"open": 9.0, "low": 9.0, "pre_close": 10.0}},
        )
        result = executor.run_day("20260106", factor_scores={})

        assert result["status"] == "ok"
        assert result["sell_trades"] == []
        positions = executor.account.get_positions()
        assert len(positions) == 1 and positions[0].ts_code == "000010.SZ"
        executor.account.close()

    def test_sell_without_flag_uses_close_price_regression(self, temp_db, monkeypatch):
        """④ sell_at_open=False（默认）→ 原收盘价路径，且不触发 open 查询."""
        from davis_analyzer.paper_trading import executor as ex
        from davis_analyzer.paper_trading.strategy import Signal

        executor = _make_executor(
            "sao_legacy",
            [Signal(
                ts_code="000010.SZ", name="深测A", action="SELL",
                signal_reason="常规轮动卖出",
            )],
        )
        executor.account.buy("000010.SZ", "深测A", 1000, 10.0, "20260105")

        _patch_run_day_env(monkeypatch, closes={"000010.SZ": 10.5}, open_rows={})
        open_calls: list = []

        def _spy(codes, d):
            open_calls.append(codes)
            return {}

        monkeypatch.setattr(ex, "_get_open_prices", _spy)
        result = executor.run_day("20260106", factor_scores={})

        assert len(result["sell_trades"]) == 1
        assert result["sell_trades"][0]["price"] == pytest.approx(10.5)
        assert open_calls == []  # 无 sell_at_open 信号 → 不读开盘价
        assert executor.account.get_positions() == []
        executor.account.close()
