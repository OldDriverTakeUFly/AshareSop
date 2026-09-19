"""校准工具:构造温度完全预测/完全不预测两种面板,断言 IC 与分组单调."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _panel(predictive: bool, n_days: int = 60, n_sec: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    for i in range(n_days):
        d = f"2023{i + 1:04d}"
        temp = rng.uniform(0, 100, n_sec)
        fwd = (temp / 100 if predictive else rng.uniform(0, 100, n_sec)) * 0.1
        for j in range(n_sec):
            rows.append({"trade_date": d, "level": "L1",
                         "index_code": f"S{j:03d}", "temperature": temp[j],
                         "composite_z": temp[j], "fwd10": fwd[j],
                         # 族分列:动量=温度(信息全在这),其余恒 0
                         "mom_score": temp[j], "flow_score": 0.0,
                         "vol_score": 0.0, "trend_score": 0.0, "limit_score": 0.0})
    return pd.DataFrame(rows)


def test_rank_ic_extremes():
    from davis_analyzer.systems.thermometer import calibrate

    assert calibrate.daily_rank_ic(_panel(True), 10).mean() > 0.95
    assert abs(calibrate.daily_rank_ic(_panel(False), 10).mean()) < 0.15


def test_quintile_monotonic_and_spread():
    from davis_analyzer.systems.thermometer import calibrate

    rep = calibrate.quintile_report(_panel(True), 10)
    means = [rep[f"q{i}"] for i in range(1, 6)]
    assert all(a < b for a, b in zip(means, means[1:]))  # 单调
    assert rep["spread_p"] < 0.05                          # top-bottom 显著


def test_walk_forward_shape():
    from davis_analyzer.systems.thermometer import calibrate

    panel = _panel(True, n_days=200)
    # 缩小窗口让单折成立:200 日 → train=74/valid=26 也能出先验档
    out = calibrate.walk_forward(panel, train_days=74, valid_days=26)
    assert set(out) >= {"prior", "ic_weighted", "ridge"}
    assert out["prior"]["oos_ic_mean"] > 0.9  # 完美预测下先验也达标


def test_verdict_targets():
    from davis_analyzer.core.constants import THERMOMETER_CALIBRATION_TARGETS
    from davis_analyzer.systems.thermometer import calibrate

    ok = calibrate._verdict({"oos_ic_mean": 0.05, "oos_icir": 0.4}, 0.01)
    assert ok is True
    bad = calibrate._verdict({"oos_ic_mean": 0.01, "oos_icir": 0.1}, 0.5)
    assert bad is False
    assert THERMOMETER_CALIBRATION_TARGETS["min_ic"] == 0.03
