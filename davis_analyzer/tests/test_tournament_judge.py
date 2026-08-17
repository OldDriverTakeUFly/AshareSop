"""judge 裁判铁律测试（防前视/样本门槛/regime 切片）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from davis_analyzer.backtest import EquitySnapshot, Trade
from davis_analyzer.tournament.adapters import RunResult
from davis_analyzer.tournament.judge import JudgeHarness, WindowReport


def _cal(n: int = 200) -> list[date]:
    d0 = date(2023, 1, 2)
    return [d0 + timedelta(days=i) for i in range(n)]


@dataclass
class FakeAdapter:
    name: str = "fake"
    horizon: str = "periodic"
    version: str = "v0"
    curve_days: int = 63
    n_trades: int = 20
    calls: list[tuple[date, date]] = field(default_factory=list)

    def run_window(self, client, start, end, params=None):
        self.calls.append((start, end))
        curve = [EquitySnapshot(date=start + timedelta(days=i), equity=1_000_000.0 + i * 100.0,
                                cash=0.0, positions_value=1_000_000.0)
                 for i in range(self.curve_days)]
        trades = [Trade(signal_date=start, exec_date=start, ts_code="X.SZ", action="BUY",
                        price=1.0, shares=100, amount=100.0, cost=0.0)] * self.n_trades
        return RunResult(curve, trades, {"cost_model": "fake"})


def _regime_fn(trade_date: str) -> str:
    return "risk_on"


def test_build_windows_step_and_coverage() -> None:
    judge = JudgeHarness([], client=None, regime_fn=_regime_fn)
    windows = judge.build_windows(_cal(200))
    assert windows[0][0] == _cal(200)[0]
    assert windows[-1][1] <= _cal(200)[-1]
    assert all((end - start).days >= 60 for start, end in windows[:2])


def test_snapshot_no_lookahead() -> None:
    """核心铁律：as_of 时点只允许看到 end <= as_of 的窗口."""
    adapter = FakeAdapter()
    judge = JudgeHarness([adapter], client=None, regime_fn=_regime_fn)
    cal = _cal(200)
    as_of = cal[100]
    judge.snapshot(as_of, cal)
    assert adapter.calls, "snapshot should have evaluated windows"
    assert all(end <= as_of for _, end in adapter.calls)


def test_min_sample_thresholds_na() -> None:
    short = FakeAdapter(name="short", curve_days=10)
    notrading = FakeAdapter(name="notrading", n_trades=0)
    passive = FakeAdapter(name="passive_bench", horizon="passive", n_trades=0)
    judge = JudgeHarness([short, notrading, passive], client=None, regime_fn=_regime_fn)
    cal = _cal(80)
    reports = judge.evaluate_window(cal[0], cal[62])
    assert reports["short"].stats is None and "交易日" in reports["short"].na_reason
    assert reports["notrading"].stats is None and "成交笔数" in reports["notrading"].na_reason
    assert reports["passive_bench"].stats is not None  # passive 豁免笔数门槛


def test_regime_label_attached() -> None:
    adapter = FakeAdapter()
    judge = JudgeHarness([adapter], client=None, regime_fn=_regime_fn)
    cal = _cal(80)
    reports = judge.evaluate_window(cal[0], cal[62])
    assert reports["fake"].regime == "risk_on"
    assert isinstance(reports["fake"], WindowReport)
