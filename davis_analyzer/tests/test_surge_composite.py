"""综合分测试(spec §5.10)."""

from __future__ import annotations

import math

from davis_analyzer.systems.surge.factors import compute_composite

_BASE = dict(
    money={"elg_net_d0": 5000.0, "lg_net_5d": 20000.0,
           "net_ratio_d0": 0.12, "consec_net_days": 3},
    chips={"cost_5pct": 9.0, "cost_50pct": 10.0, "cost_95pct": 11.0,
           "weight_avg": 10.1},
    winner={"winner_rate": 50.0, "winner_delta_5d": 5.0},
    position={"pos_250d": 0.3},
    rs={"resistance_dist": 0.15, "support_dist": -0.08},
)


def test_composite_hype_beats_risk():
    c1 = compute_composite(hype=["增持", "并购重组"], risk=[], **_BASE)
    c2 = compute_composite(hype=[], risk=["减持", "定增", "爆雷监管"], **_BASE)
    assert 0 <= c2 < c1 <= 100


def test_composite_nan_safe_neutral():
    c = compute_composite(
        money={"elg_net_d0": math.nan, "lg_net_5d": math.nan,
               "net_ratio_d0": math.nan, "consec_net_days": 0},
        chips={}, winner={}, position={}, rs={}, hype=[], risk=[])
    assert 0 <= c <= 100


def test_composite_resistance_distance_matters():
    near = compute_composite(hype=[], risk=[], **{**_BASE,
        "rs": {"resistance_dist": 0.02, "support_dist": -0.05}})
    far = compute_composite(hype=[], risk=[], **{**_BASE,
        "rs": {"resistance_dist": 0.30, "support_dist": -0.05}})
    assert far > near  # 压力越远分越高(clip 于 0.20 满档)
