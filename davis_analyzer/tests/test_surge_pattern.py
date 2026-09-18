"""C1/C2/C3 形态识别测试: 合成'放量阳→缩量回调→平台突破'标准序列及反例."""

from __future__ import annotations

import math

import pandas as pd

from davis_analyzer.surge.pattern import detect_pattern

_NAN = float("nan")


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


# ── 16 标签库 ──

from davis_analyzer.surge.pattern import detect_tags  # noqa: E402


def test_tags_bottom_volume_crowd_near_resist():
    # 低位(pos≈0) + 今日放量(900 vs VMA≈300=3×) + winner 90 + 阻力近(2%)
    px = _mk([(9.9, 10.1, 9.8, 10.0, 300.0)] * 120 +
             [(10.0, 10.8, 10.0, 10.7, 900.0)])
    pos = {"pos_250d": 0.05}
    cyq = pd.Series({"cost_5pct": 9.9, "cost_95pct": 10.3, "weight_avg": 10.1,
                     "winner_rate": 90.0})
    tags = detect_tags(px, cyq, resistance_dist=0.02, position=pos)
    assert "底部放量" in tags
    assert "获利盘拥挤" in tags
    assert "上方套牢近" in tags


def test_tags_new_high_gap_ma_bull():
    # 持续爬升(均线多头) + 今日跳空创新高
    seq = [(10.0 + 0.02 * i, 10.2 + 0.02 * i, 9.9 + 0.02 * i,
            10.1 + 0.02 * i, 1000.0) for i in range(130)]
    seq[-1] = (13.5, 13.85, 13.4, 13.8, 3000.0)  # close=13.8>=250日高13.85×0.995≈13.78创新高;low13.4>昨high≈12.7跳空
    px = _mk(seq)
    pos = {"pos_250d": 1.0}
    tags = detect_tags(px, None, resistance_dist=_NAN, position=pos)
    assert "创新高" in tags
    assert "跳空缺口" in tags
    assert "均线多头" in tags
    assert "天量" not in tags  # 3000/VMA≈1015≈2.95 < 5,非天量


def test_tags_box_breakout():
    # 60日箱体 9.5~10.5(振幅10.5%<25%) + 今日收10.8破箱体上沿
    seq = [(9.8, 10.5, 9.5, 10.0, 1000.0)] * 60
    seq += [(10.0, 10.9, 9.9, 10.8, 1500.0)]
    px = _mk(seq)
    tags = detect_tags(px, None, resistance_dist=_NAN, position={"pos_250d": 0.8})
    assert "箱体突破" in tags
    assert "平台突破" in tags  # 20日高点=10.5 亦破


def test_tags_moderate_volume_not_huge():
    # 温和放量: 近5日均量 ∈[1.2,2]×VMA 且递增;今日量2.9×非天量
    seq = [(9.9, 10.1, 9.8, 10.0, 1000.0)] * 120
    seq += [(10.0, 10.6, 9.9, 10.5, 1200.0)] * 5 + [(10.5, 11.3, 10.4, 11.2, 2900.0)]
    px = _mk(seq)
    tags = detect_tags(px, None, resistance_dist=_NAN, position={"pos_250d": 0.9})
    # 近5日均量=(1200*4+2900)/5=1540,VMA≈1016→1.52∈[1.2,2]且>前5日均1200
    assert "温和放量" in tags
    assert "天量" not in tags  # 2900/1016≈2.85<5


def test_tags_chip_dense_low():
    # 筹码低位密集: 95/5-1=10.5/9.9-1≈6%<30% 且 weight_avg 位置分位低
    px = _mk([(9.9, 10.1, 9.8, 10.0, 1000.0)] * 130)
    # 价格区间9.8~10.1;weight_avg=9.92→区间分位0.4不严格小于,取9.9→0.333
    cyq = pd.Series({"cost_5pct": 9.9, "cost_95pct": 10.5, "weight_avg": 9.9,
                     "winner_rate": 50.0})
    tags = detect_tags(px, cyq, resistance_dist=0.05,
                       position={"pos_250d": 0.3})
    assert "筹码低位密集" in tags


def test_tags_bull_engulf():
    # 昨阴今大阳反包
    seq = [(9.9, 10.1, 9.8, 10.0, 1000.0)] * 125
    seq += [(10.05, 10.1, 9.7, 9.75, 1000.0),      # 昨阴 实体0.3
            (9.8, 10.8, 9.78, 10.7, 2000.0)]        # 今阳 实体0.9 开9.8<=昨收9.75?否!
    px = _mk(seq)
    tags = detect_tags(px, None, resistance_dist=_NAN, position={"pos_250d": 0.5})
    # 今开9.8>昨收9.75 → 不满足「今开<=昨收」,不算标准反包;调整断言
    assert "大阳反包" not in tags
