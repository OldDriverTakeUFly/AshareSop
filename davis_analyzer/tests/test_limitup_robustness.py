"""robustness.py 样本门槛/IS-OOS/扰动测试。"""

from __future__ import annotations

import pandas as pd

from davis_analyzer.limitup import robustness


def test_split_is_oos_no_overlap_ordered() -> None:
    ev = pd.DataFrame({"trade_date": ["20230101", "20250630", "20250701", "20260801"]})
    is_ev, oos_ev = robustness.split_is_oos(ev, "20250701")
    assert list(is_ev["trade_date"]) == ["20230101", "20250630"]
    assert list(oos_ev["trade_date"]) == ["20250701", "20260801"]


def test_sufficient_thresholds() -> None:
    counts = pd.Series([29, 30, 49, 50])
    assert list(robustness.sufficient(counts, "return")) == [False, True, True, True]
    assert list(robustness.sufficient(counts, "promotion")) == [False, False, False, True]


def test_direction_stable() -> None:
    assert robustness.direction_stable(0.02, 0.015, 0.03)
    assert robustness.direction_stable(-0.01, -0.02, -0.005)
    assert not robustness.direction_stable(0.02, -0.01, 0.03)
    assert robustness.perturb_factors() == (0.8, 1.2)
