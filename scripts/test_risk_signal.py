"""End-to-end test: high-position high-volume risk sell trigger."""
import os, sys
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")

from datetime import date
from davis_analyzer.paper_trading.account import Position
from davis_analyzer.paper_trading.strategy import Signal
from davis_analyzer.paper_trading.executor import DailyExecutor, _compute_volume_signals

# Build a minimal executor without an account (we test _check_risk_signals directly)
class DummyAccount:
    name = "test"
    initial_capital = 1_000_000
    commission_bps = 2.5
    stamp_tax_bps = 10.0

class DummyStrategy:
    name = "factor_threshold"
    _RISK_RULES = DailyExecutor._RISK_RULES
    _DEFAULT_RISK = DailyExecutor._DEFAULT_RISK
    _get_risk_thresholds = DailyExecutor._get_risk_thresholds
    _check_risk_signals = DailyExecutor._check_risk_signals

# Use宁德时代 on 20260321 — we know it triggers high_vol
TEST_DATE = "20260321"
TEST_CODE = "300750.SZ"

# Build a position that is profitable (avg_cost well below current)
# From earlier diagnosis: close=413.00 on 20260321
current_price = 413.00
positions = [
    Position(
        ts_code=TEST_CODE,
        name="宁德时代",
        shares=1000,
        avg_cost=350.00,  # ~+18% gain
        entry_date="20251201",
    ),
]

# Compute volume signal for the position
vol_signals = _compute_volume_signals([TEST_CODE], TEST_DATE)
print(f"Volume signal for {TEST_CODE}:")
for k, v in vol_signals[TEST_CODE].items():
    print(f"  {k}: {v}")

# Construct a fake executor to call _check_risk_signals
class FakeExecutor:
    _RISK_RULES = DailyExecutor._RISK_RULES
    _DEFAULT_RISK = DailyExecutor._DEFAULT_RISK
    _get_risk_thresholds = DailyExecutor._get_risk_thresholds
    _check_risk_signals = DailyExecutor._check_risk_signals

prices = {TEST_CODE: current_price}
executor = FakeExecutor()

# Test 1: WITH volume_signals passed → should trigger high_vol SELL
signals = executor._check_risk_signals(
    positions, prices, TEST_DATE,
    market_regime="bull",
    industries={TEST_CODE: "电池"},
    industry_trend={"电池": "up"},
    volatilities={TEST_CODE: 35.0},
    volume_signals=vol_signals,
)
print(f"\n[Test 1] With volume_signals passed → {len(signals)} signals")
for s in signals:
    print(f"  {s.action} {s.ts_code}: {s.signal_reason}")

assert len(signals) == 1, f"Expected 1 signal, got {len(signals)}"
assert signals[0].action == "SELL"
assert "高位放量" in signals[0].signal_reason
print("✓ Test 1 passed: high_vol SELL triggered")

# Test 2: WITHOUT volume_signals (legacy behavior) → no high_vol trigger
signals2 = executor._check_risk_signals(
    positions, prices, TEST_DATE,
    market_regime="bull",
    industries={TEST_CODE: "电池"},
    industry_trend={"电池": "up"},
    volatilities={TEST_CODE: 35.0},
    volume_signals=None,  # legacy
)
print(f"\n[Test 2] Without volume_signals → {len(signals2)} signals")
for s in signals2:
    print(f"  {s.action} {s.ts_code}: {s.signal_reason}")
# Should be 0 because P&L=+18% < take_profit for bull/up (0.30)
assert len(signals2) == 0, f"Expected 0 signals (no high_vol trigger), got {len(signals2)}"
print("✓ Test 2 passed: legacy mode does not fire high_vol")

# Test 3: high_vol signal but P&L < 10% → should NOT trigger
positions_low_pnl = [
    Position(
        ts_code=TEST_CODE, name="宁德时代",
        shares=1000, avg_cost=405.00,  # only ~+2% gain
        entry_date="20260301",
    ),
]
signals3 = executor._check_risk_signals(
    positions_low_pnl, prices, TEST_DATE,
    market_regime="bull",
    industries={TEST_CODE: "电池"},
    industry_trend={"电池": "up"},
    volatilities={TEST_CODE: 35.0},
    volume_signals=vol_signals,
)
print(f"\n[Test 3] high_vol but P&L < 10% → {len(signals3)} signals")
assert len(signals3) == 0, f"Expected 0 signals (low P&L), got {len(signals3)}"
print("✓ Test 3 passed: high_vol without enough profit does not fire")

print("\n" + "=" * 60)
print("  All risk-signal tests passed ✓")
print("=" * 60)
