"""strategies.py 预设过滤与预算约束测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from davis_analyzer.limitup.strategies import PRESETS, ExitRule, apply_preset


def _ev() -> pd.DataFrame:
    return pd.DataFrame([
        {"ts_code": "600001.SH", "trade_date": "20240102", "consecutive_boards": 1,
         "pattern_label": "突破型", "seal_ratio": 0.03, "sector_linkage": 3,
         "negative_event_30d": False, "ret_open_1": 0.01, "promoted": True},
        {"ts_code": "600002.SH", "trade_date": "20240102", "consecutive_boards": 1,
         "pattern_label": "其他", "seal_ratio": 0.01, "sector_linkage": 1,
         "negative_event_30d": False, "ret_open_1": -0.01, "promoted": False},
        {"ts_code": "600003.SH", "trade_date": "20240102", "consecutive_boards": 2,
         "pattern_label": "其他", "seal_ratio": 0.06, "sector_linkage": 2,
         # 简报修正：原 True 与 relay_2 默认 exclude_negative_event=True 矛盾
         # （本行是 relay 测试唯一期望存活的候选），改 False 后断言逐字保留
         "negative_event_30d": False, "ret_open_1": 0.02, "promoted": True},
    ])


def test_first_board_filters() -> None:
    out = apply_preset(_ev(), PRESETS["first_board"])
    assert list(out["ts_code"]) == ["600001.SH"]


def test_relay_2_median_filter() -> None:
    out = apply_preset(_ev(), PRESETS["relay_2"], seal_ratio_median=0.05)
    assert list(out["ts_code"]) == ["600003.SH"]


def test_negative_event_excluded() -> None:
    # 补充用例（简报 fixture 修正的配套）：利空排雷默认开启，
    # 负事件行即使满足板数/seal 条件也应被剔除
    ev = _ev()
    ev.loc[ev["ts_code"] == "600003.SH", "negative_event_30d"] = True
    out = apply_preset(ev, PRESETS["relay_2"], seal_ratio_median=0.05)
    assert out.empty


def test_regime_filter() -> None:
    regime = pd.DataFrame([{"trade_date": "20240102", "regime_label": "冰点"}])
    out = apply_preset(_ev(), PRESETS["first_board"], regime=regime)
    assert out.empty


def test_filter_budget_raises() -> None:
    preset = PRESETS["relay_2"]
    # 板数+regime+seal+联动 = 4 条，预算内不触发
    out = apply_preset(_ev(), preset, min_sector_linkage=2, seal_ratio_median=0.01)
    assert not out.empty
    # 构造 5 条件触发：直接换 preset 字段
    from dataclasses import replace
    fat = replace(preset, pattern_labels=("突破型",), regime_allow=("回暖",),
                  exclude_negative_event=True)
    with pytest.raises(ValueError, match="过滤条件超过预算"):
        apply_preset(_ev(), fat, min_sector_linkage=2, seal_ratio_median=0.01)


def test_exit_rules_defined() -> None:
    assert PRESETS["first_board"].exit_rule is ExitRule.OPEN_NEXT
    assert PRESETS["relay_2"].exit_rule is ExitRule.RIDE_BOARD
