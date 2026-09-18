"""C1/C2/C3 形态识别测试: 合成'放量阳→缩量回调→平台突破'标准序列及反例."""

from __future__ import annotations

import pandas as pd

from davis_analyzer.surge.pattern import detect_pattern


def _mk(ohlcvs: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    """ohlcvs: (open,high,low,close,vol) 逐日,末行为突破日."""
    n = len(ohlcvs)
    return pd.DataFrame({
        "trade_date": [f"d{i:03d}" for i in range(n)],
        "open": [o for o, *_ in ohlcvs],
        "high": [h for _, h, *_ in ohlcvs],
        "low": [l for _, _, l, *_ in ohlcvs],
        "close": [c for _, _, _, c, _ in ohlcvs],
        "vol": [v for *_, v in ohlcvs],
        "adj_factor": [1.0] * n,
    })


def _base_series() -> list[tuple]:
    """128日: 平稳期(vol=1000,价10) → d121 放量阳 → 5日缩量回调(量>VMA,相对萎缩)
    → d127 平台突破. 回调量 1700→1100: 衰减比 1150/1650≈0.697 贴 0.70 线(验证边界),
    且全程 >VMA(C1 消歧口径: 萎缩但不冷)."""
    seq = [(9.9, 10.1, 9.8, 10.0, 1000.0)] * 121
    seq += [(10.0, 10.8, 10.0, 10.7, 3000.0)]            # d121 放量阳 +7%, ~3×VMA
    seq += [(10.6, 10.7, 10.3, 10.4, 1700.0),             # d122 阴 实体0.2
            (10.4, 10.5, 10.2, 10.3, 1600.0),             # d123 阴 实体0.1
            (10.3, 10.45, 10.15, 10.25, 1200.0),          # d124 阴 实体0.05
            (10.25, 10.45, 10.2, 10.4, 1150.0),           # d125 小阳
            (10.4, 10.52, 10.3, 10.45, 1100.0)]           # d126 小阳
    seq += [(10.5, 11.4, 10.5, 11.3, 2500.0)]             # d127 今日 +8.4% 突破
    return seq


def test_standard_pattern_hits():
    r = detect_pattern(_mk(_base_series()))
    assert r is not None, "标准形态应命中"
    assert r["breakout_pct"] > 0
    assert r["boom_vol_ratio"] >= 2.0
    assert 0 < r["pullback_depth"] <= 0.15
    assert r["plateau_high"] >= 10.5  # 平台在回调高点附近
    assert r["boom_date"] == "d121"


def test_deep_pullback_rejected():
    seq = _base_series()
    # 回调跌破放量阳最低价(10.0)
    seq[122] = (10.6, 10.7, 9.5, 9.6, 950.0)
    assert detect_pattern(_mk(seq)) is None


def test_vol_break_3days_rejected():
    seq = _base_series()
    for i in (124, 125, 126):  # 回调尾段连续3日量掉到 300(<VMA)→量能断裂
        seq[i] = (seq[i][0], seq[i][1], seq[i][2], seq[i][3], 300.0)
    assert detect_pattern(_mk(seq)) is None


def test_vol_break_2days_tolerated():
    seq = _base_series()
    for i in (124, 125):  # 连续2日低于均量→容忍(其余判据不受前半窗高量影响)
        seq[i] = (seq[i][0], seq[i][1], seq[i][2], seq[i][3], 300.0)
    r = detect_pattern(_mk(seq))
    assert r is not None


def test_no_breakout_rejected():
    seq = _base_series()
    seq[127] = (10.45, 10.55, 10.4, 10.5, 2000.0)  # 今日未破平台(>10.52需>10.52)
    assert detect_pattern(_mk(seq)) is None


def test_today_below_vma_rejected():
    seq = _base_series()
    seq[127] = (10.5, 11.4, 10.5, 11.3, 500.0)  # 突破日缩量
    assert detect_pattern(_mk(seq)) is None


def test_no_boom_anchor_rejected():
    # 无放量阳: 全程温和量
    seq = [(9.9, 10.1, 9.8, 10.0, 1000.0)] * 125
    seq += [(10.0, 10.5, 9.9, 10.4, 1050.0), (10.45, 11.3, 10.4, 11.2, 1200.0)]
    assert detect_pattern(_mk(seq)) is None
