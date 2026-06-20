"""RED-phase contract tests — verify all 4 signal stubs raise NotImplementedError.

These tests lock the contract: every function in
``stockhot.sell_monitor.signals`` must be importable and must raise
``NotImplementedError`` until the implementation tasks (T14, T15, T16)
replace the stubs. Once a signal is implemented, its test here should be
removed or updated.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockhot.sell_monitor.signals import (
    check_hard_stop_loss,
    check_target_reached,
    check_thesis_broken,
    check_trailing_stop,
)


@pytest.fixture
def sample_holding() -> dict:
    return {
        "code": "000001",
        "name": "TestStock",
        "entry_price": 10.0,
        "current_price": 11.5,
        "stop_loss_hard": 9.0,
        "target_price": 13.0,
        "position_pct": 12.0,
    }


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=30)
    rng = np.random.default_rng(7)
    close = 10.0 + rng.standard_normal(30).cumsum()
    low = close - rng.uniform(0.1, 0.4, size=30)
    high = close + rng.uniform(0.1, 0.4, size=30)
    op = close + rng.uniform(-0.2, 0.2, size=30)
    volume = rng.integers(1_000_000, 5_000_000, size=30)
    return pd.DataFrame(
        {"open": op, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


@pytest.fixture
def sample_davis_score() -> dict:
    return {"final_score": 65.0, "percentile_rank": 45}


class TestSignals:
    """One test per signal function — all must raise NotImplementedError."""

    def test_check_hard_stop_loss_not_implemented(self, sample_holding):
        with pytest.raises(NotImplementedError):
            check_hard_stop_loss(sample_holding, current_price=9.5)

    def test_check_trailing_stop_not_implemented(self, sample_holding, sample_ohlcv):
        with pytest.raises(NotImplementedError):
            check_trailing_stop(sample_holding, sample_ohlcv)

    def test_check_target_reached_not_implemented(self, sample_holding):
        with pytest.raises(NotImplementedError):
            check_target_reached(sample_holding, current_price=13.5)

    def test_check_thesis_broken_not_implemented(self, sample_holding, sample_davis_score):
        with pytest.raises(NotImplementedError):
            check_thesis_broken(sample_holding, sample_davis_score)
