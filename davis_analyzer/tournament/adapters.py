"""ModuleAdapter protocol — normalisation layer between engines and judge.

Each participant exposes one windowed run interface; differences between
periodic-rebalance (davis) and passive benchmarks are absorbed here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd

from davis_analyzer.backtest import (
    BacktestConfig,
    BacktestResult,
    EquitySnapshot,
    Trade,
    run_backtest,
)
from davis_analyzer.backtest_factors import FactorConfig
from davis_analyzer.backtest_report import PerformanceStats, compute_performance
from davis_analyzer.constants import CHAMPION_PRESETS, TOURNAMENT_DAVIS_PRESETS
from davis_analyzer.tournament.genome import DAVIS_GENOME, Genome
from davis_analyzer.tushare_client import TushareClient


# ── normalised run result ──


@dataclass
class RunResult:
    """One participant's result inside a single evaluation window."""

    equity_curve: list[EquitySnapshot]
    trades: list[Trade]
    assumptions: dict[str, str]


def stats_from_run(run: RunResult, start: date, end: date) -> PerformanceStats:
    """Reuse compute_performance via a pseudo BacktestResult."""
    pseudo = BacktestResult(
        config=BacktestConfig(start_date=start, end_date=end),
        trades=run.trades,
        equity_curve=run.equity_curve,
    )
    return compute_performance(pseudo)


# ── adapter protocol ──


@runtime_checkable
class ModuleAdapter(Protocol):
    name: str
    horizon: str  # "periodic" | "event" | "passive"
    version: str

    def run_window(
        self, client: TushareClient, start: date, end: date,
        params: dict[str, float | int] | None = None,
    ) -> RunResult | None: ...


def _params_fingerprint(params: dict) -> str:
    return hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]


# ── passive benchmark ──


class IndexBenchmarkAdapter:
    """Buy-and-hold an index (no cost, no trades)."""

    horizon = "passive"

    def __init__(self, index_code: str = "000001.SH") -> None:
        self.index_code = index_code
        self.name = "benchmark_sse" if index_code == "000001.SH" else f"benchmark_{index_code.split('.')[0]}"
        self.version = "v0"

    def run_window(
        self, client: TushareClient, start: date, end: date,
        params: dict[str, float | int] | None = None,
    ) -> RunResult | None:
        df = client.get_daily_prices(
            self.index_code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        )
        if df is None or df.empty:
            return None
        df = df.sort_values("trade_date")
        first_close = float(df.iloc[0]["close"])
        curve: list[EquitySnapshot] = []
        for _, row in df.iterrows():
            d = pd.to_datetime(row["trade_date"], format="%Y%m%d").date()
            equity = 1_000_000.0 * float(row["close"]) / first_close
            curve.append(EquitySnapshot(date=d, equity=equity, cash=0.0, positions_value=equity))
        return RunResult(
            equity_curve=curve, trades=[],
            assumptions={"cost_model": "buy_and_hold_no_cost"},
        )


# ── davis periodic presets ──


_FACTOR_KEYS = {
    "momentum_weight", "valuation_weight", "prosperity_weight",
    "distress_weight", "northbound_weight", "research_weight",
}
_ENGINE_KEYS = {"top_n", "frequency"}


class DavisPresetAdapter:
    """Wrap run_backtest with a frozen parameter point (spec §5.1)."""

    horizon = "periodic"

    def __init__(
        self, name: str, params: dict[str, float | int],
        universe: list[str] | None = None, genome: Genome = DAVIS_GENOME,
    ) -> None:
        self.name = name
        self._params = dict(params)
        self._genome = genome
        self._universe = universe
        self.version = f"v{_params_fingerprint(self._params)}"

    def run_window(
        self, client: TushareClient, start: date, end: date,
        params: dict[str, float | int] | None = None,
    ) -> RunResult | None:
        merged = {**self._params, **(params or {})}
        self._genome.validate(merged)  # D8: undeclared keys can never pass
        factor_kwargs = {k: float(v) for k, v in merged.items() if k in _FACTOR_KEYS}
        engine_kwargs = {k: int(v) for k, v in merged.items() if k in _ENGINE_KEYS}
        cfg = BacktestConfig(
            start_date=start, end_date=end, universe=self._universe,
            factor_config=FactorConfig(**factor_kwargs), **engine_kwargs,
        )
        result = run_backtest(cfg, client)
        if not result.equity_curve:
            return None
        return RunResult(
            equity_curve=result.equity_curve, trades=result.trades,
            assumptions={"cost_model": "commission_2.5bps_stamp_10bps"},
        )


# ── universe builders（--universe 工程修复）──


def liquidity_universe(n: int, conn: "sqlite3.Connection | None" = None) -> list[str]:
    """按最近 20 个交易日的成交额中位数取前 N 只（可交易性过滤）.

    全市场单日因子打分 >280s 不可行（2026-08-17 实测），锦标赛以流动性
    前缀宇宙运行（u200 ≈ 1.9s/日，与 backtest_5yr_u200 惯例一致）。
    conn 可注入供测试；默认走 davis 缓存库。
    """
    import sqlite3
    import statistics
    from collections import defaultdict

    if conn is None:
        from davis_analyzer.tushare_client import _CACHE_DB
        conn = sqlite3.connect(str(_CACHE_DB))
    rows = conn.execute(
        "SELECT ts_code, amount FROM daily_price "
        "WHERE trade_date >= (SELECT DISTINCT trade_date FROM daily_price "
        "ORDER BY trade_date DESC LIMIT 1 OFFSET 19) AND amount > 0"
    ).fetchall()
    buckets: dict[str, list[float]] = defaultdict(list)
    for code, amt in rows:
        buckets[code].append(float(amt))
    ranked = sorted(buckets, key=lambda c: -statistics.median(buckets[c]))
    return ranked[:n]


def resolve_universe(spec: str, conn: "sqlite3.Connection | None" = None) -> list[str] | None:
    """宇宙口径解析: 'all' → None（全缓存）; 'u<N>' → 流动性前 N; 其余 → 文件路径."""
    if spec == "all":
        return None
    if spec.startswith("u") and spec[1:].isdigit():
        return liquidity_universe(int(spec[1:]), conn)
    from pathlib import Path

    text = Path(spec).read_text(encoding="utf-8")
    return [t for t in (x.strip() for x in text.replace(",", "\n").split("\n")) if t]


def default_participants(universe: list[str] | None = None) -> list[ModuleAdapter]:
    """Frozen registry: davis presets + index benchmark + deployed champions.

    *universe* 限定 davis 系参赛者的股票池（None = 全缓存，仅适用于
    短窗口/小宇宙——见 :func:`liquidity_universe`）；基准不受影响。
    """
    participants: list[ModuleAdapter] = [
        DavisPresetAdapter(name, dict(params), universe=universe)
        for name, params in TOURNAMENT_DAVIS_PRESETS.items()
    ]
    participants.append(IndexBenchmarkAdapter())
    for name, params in CHAMPION_PRESETS.items():
        participants.append(DavisPresetAdapter(f"champion_{name}", dict(params), universe=universe))
    return participants
