"""振幅研究模块单测：振幅表因果性 + atr20 门控生效 + 切分窗口."""

from __future__ import annotations

import pandas as pd
import pytest

from davis_analyzer.intraday.amplitude_study import build_amplitude_table
from davis_analyzer.intraday.engine import IntradayConfig, run_backtest
from davis_analyzer.intraday.strategies import GapDownSmart

CFG = IntradayConfig(per_stock_notional=100_000, trade_fraction=0.3)


def _daily_df(amps: list[float]) -> pd.DataFrame:
    """单只票连续 22 日：pre_close=100，按给定振幅构造 high/low/close."""
    rows = []
    for i, a in enumerate(amps):
        pre = 100.0
        close = 100.0  # 平收，简化
        high = pre * (1 + a / 2)
        low = pre * (1 - a / 2)
        rows.append({
            "ts_code": "600000.SH", "trade_date": f"2026{i + 1:04d}",
            "pre_close": pre, "close": close, "high": high, "low": low,
        })
    return pd.DataFrame(rows)


def test_build_amplitude_table_is_causal():
    """atr20/prev_amp 在 T 日只含昨日及以前振幅——手工数列核对."""
    amps = [0.01] * 20 + [0.05, 0.03]  # 22 日
    amp = build_amplitude_table(_daily_df(amps))
    tail = amp.set_index("trade_date")
    # 第 21 日（首日=0.01×20 结束后）: atr20 = mean(前20日)=0.01, prev_amp=0.01
    assert tail.loc["20260021", "atr20"] == pytest.approx(0.01)
    assert tail.loc["20260021", "prev_amp"] == pytest.approx(0.01)
    # 第 22 日: atr20 = mean(日2..日21) = (0.01*19 + 0.05)/20 = 0.012
    assert tail.loc["20260022", "atr20"] == pytest.approx(0.012)
    assert tail.loc["20260022", "prev_amp"] == pytest.approx(0.05)
    assert tail.loc["20260021", "atr5"] == pytest.approx(0.01)  # 前5日均=0.01


def test_atr20_gate_blocks_low_amplitude_entry():
    """atr20_min 门控：低波动日不入场、高波动日正常入场."""
    s = GapDownSmart(0.03, exit_time="14:00", require={"atr20_min": 0.02})
    minute = pd.DataFrame([
        {"ts_code": "600000.SH", "trade_date": "20260817", "trade_time": "09:35",
         "open": 96.5, "high": 97.0, "low": 96.0, "close": 96.6},
        {"ts_code": "600000.SH", "trade_date": "20260817", "trade_time": "09:40",
         "open": 96.2, "high": 96.8, "low": 96.0, "close": 96.3},
        {"ts_code": "600519.SH", "trade_date": "20260817", "trade_time": "09:35",
         "open": 96.5, "high": 97.0, "low": 96.0, "close": 96.6},
        {"ts_code": "600519.SH", "trade_date": "20260817", "trade_time": "09:40",
         "open": 96.2, "high": 96.8, "low": 96.0, "close": 96.3},
    ])
    daily = pd.DataFrame([
        {"ts_code": "600000.SH", "trade_date": "20260817", "pre_close": 100.0,
         "close": 96.0, "high": 97.0, "low": 95.5},
        {"ts_code": "600519.SH", "trade_date": "20260817", "pre_close": 100.0,
         "close": 96.0, "high": 97.0, "low": 95.5},
    ])
    feats = pd.DataFrame([
        {"ts_code": "600000.SH", "trade_date": "20260817", "atr20": 0.01},
        {"ts_code": "600519.SH", "trade_date": "20260817", "atr20": 0.03},
    ])
    res = run_backtest(minute, daily, [s], CFG, features_df=feats)
    codes = set(res["ts_code"])
    assert codes == {"600519.SH"}  # 低 atr20 的 600000 被门控拦截
