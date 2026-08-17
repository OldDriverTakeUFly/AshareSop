"""JudgeHarness — unified, point-in-time evaluation of participants.

Hard rules (spec §5.2): rolling windows, independent per-window runs,
minimum-sample gates, regime slicing, no-lookahead by construction (the
judge alone owns window boundaries; adapters never see the schedule).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd
from loguru import logger

from davis_analyzer.constants import (
    TOURNAMENT_EVAL_STEP_DAYS,
    TOURNAMENT_MIN_TRADES,
    TOURNAMENT_MIN_WINDOW_DAYS,
)
from davis_analyzer.backtest_report import PerformanceStats
from davis_analyzer.tournament.adapters import ModuleAdapter, stats_from_run
from davis_analyzer.tushare_client import TushareClient

RegimeFn = Callable[[str], str]


@dataclass
class WindowReport:
    """One participant's outcome in one window (N/A when gates fail)."""

    participant: str
    start: date
    end: date
    stats: PerformanceStats | None
    regime: str | None
    na_reason: str | None


def _default_regime_fn(trade_date: str) -> str:
    from davis_analyzer.market_regime import get_market_regime_with_confirm
    return get_market_regime_with_confirm(trade_date)


def trading_calendar(client: TushareClient, start: date, end: date,
                     anchor: str = "000001.SH") -> list[date]:
    """Derive trading dates from the anchor's cached prices (project rule)."""
    df = client.get_daily_prices(anchor, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    if df is None or df.empty:
        raise ValueError(f"anchor {anchor} has no cached prices in window")
    return sorted(pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.date.unique().tolist())


class JudgeHarness:
    """Owns the window schedule; the only caller of adapter.run_window."""

    def __init__(
        self,
        adapters: list[ModuleAdapter],
        client: TushareClient | None,
        regime_fn: RegimeFn | None = None,
    ) -> None:
        self._adapters = adapters
        self._client = client
        self._regime_fn = regime_fn or _default_regime_fn

    def build_windows(self, calendar: list[date]) -> list[tuple[date, date]]:
        step = TOURNAMENT_EVAL_STEP_DAYS
        return [
            (calendar[i], calendar[min(i + step - 1, len(calendar) - 1)])
            for i in range(0, len(calendar), step)
        ]

    def evaluate_window(
        self, start: date, end: date,
        params_by_participant: dict[str, dict] | None = None,
    ) -> dict[str, WindowReport]:
        params_by_participant = params_by_participant or {}
        regime = self._regime_fn(end.strftime("%Y%m%d"))
        reports: dict[str, WindowReport] = {}
        for adapter in self._adapters:
            run = adapter.run_window(
                self._client, start, end,
                params=params_by_participant.get(adapter.name),
            )
            na: str | None = None
            stats: PerformanceStats | None = None
            if run is None:
                na = "窗口内数据不足"
            elif len(run.equity_curve) < TOURNAMENT_MIN_WINDOW_DAYS:
                na = f"窗口交易日 {len(run.equity_curve)} < {TOURNAMENT_MIN_WINDOW_DAYS}"
            elif adapter.horizon != "passive" and len(run.trades) < TOURNAMENT_MIN_TRADES:
                na = f"窗口成交笔数 {len(run.trades)} < {TOURNAMENT_MIN_TRADES}"
            else:
                stats = stats_from_run(run, start, end)
            reports[adapter.name] = WindowReport(
                participant=adapter.name, start=start, end=end,
                stats=stats, regime=regime, na_reason=na,
            )
            if na:
                logger.info("{} window [{} {}] N/A: {}", adapter.name, start, end, na)
        return reports

    def snapshot(
        self, as_of: date, calendar: list[date],
    ) -> dict[tuple[date, date], dict[str, WindowReport]]:
        """Evaluate ONLY windows fully realised before *as_of* (no lookahead)."""
        out: dict[tuple[date, date], dict[str, WindowReport]] = {}
        for start, end in self.build_windows(calendar):
            if end <= as_of:
                out[(start, end)] = self.evaluate_window(start, end)
        return out
