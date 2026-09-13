"""五族因子纯函数(v2 中期窗口):窗口值/排序/量价交互/单成员截面."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _panel() -> pd.DataFrame:
    # 135 自然日(合成日历):覆盖 120 日滚动窗(动量/量能/趋势 level)
    dates = [d.strftime("%Y%m%d")
             for d in pd.date_range("2022-01-04", periods=135, freq="D")]
    rows = []
    for i, code in enumerate(("801010.SI", "801011.SI")):
        base = 100.0 + i * 10
        for j, d in enumerate(dates):
            close = base * (1.01 ** j) if i == 0 else base * (0.99 ** j)
            rows.append({
                "index_code": code, "trade_date": d, "close": close,
                "amount": 1e6 * (1 + 0.1 * j) if i == 0 else 1e6,
                "vol": 1e4, "pct_change": 1.0 if i == 0 else -1.0,
                "main_net_pct": 0.001 * (1 if i == 0 else -1),
                "limit_ratio": 0.02 if i == 0 else 0.0,
            })
    return pd.DataFrame(rows).sort_values(["index_code", "trade_date"]).reset_index(drop=True)


def test_add_factor_columns_windows():
    from davis_analyzer.thermometer import factors

    p = factors.add_factor_columns(_panel())
    a = p[p["index_code"] == "801010.SI"].reset_index(drop=True)
    # v2 动量 level = 0.5×ret60 + 0.5×ret120(idx=125 处两窗均满)
    exp = 0.5 * (1.01 ** 60 - 1) + 0.5 * (1.01 ** 120 - 1)
    assert abs(a.loc[125, "mom_level"] - exp) < 1e-9
    # v2 动量 slope = ret60 − ret120/2
    assert abs(a.loc[125, "mom_slope"] - ((1.01 ** 60 - 1) - (1.01 ** 120 - 1) / 2)) < 1e-9
    # v2 资金 level = 60 日和: 0.001×60 = 0.06
    assert abs(a.loc[125, "flow_level"] - 0.06) < 1e-9
    # v2 趋势 slope = 60 日上行占比 = 1.0
    assert a.loc[125, "trend_slope"] == 1.0
    # v2 涨停 level = 60 日均 = 0.02
    assert abs(a.loc[125, "limit_level"] - 0.02) < 1e-9
    # v2 量价交互:方向判据 ret60 → 801010 放量+60日涨=1.0;801011 60日跌=0.3
    b = p[p["index_code"] == "801011.SI"].reset_index(drop=True)
    assert a.loc[125, "pv_decay"] == 1.0
    assert b.loc[125, "pv_decay"] == 0.3
    # 窗口不足 → NaN(动量 level 需 120 日)
    assert np.isnan(a.loc[100, "mom_level"])


def test_family_scores_z_and_ordering():
    from davis_analyzer.thermometer import factors

    p = factors.family_scores(factors.add_factor_columns(_panel()))
    last_day = p["trade_date"].max()
    day = p[p["trade_date"] == last_day]
    # 两成员截面 z 互为相反数(未乘 decay 的族)
    assert abs(day["mom_score"].sum()) < 1e-9
    hi = day[day["index_code"] == "801010.SI"].iloc[0]
    lo = day[day["index_code"] == "801011.SI"].iloc[0]
    for col in ("mom_score", "flow_score", "vol_score", "trend_score", "limit_score"):
        assert hi[col] > lo[col], col
    # clip 生效
    assert day["mom_score"].abs().max() <= 3.0 + 1e-9


def test_single_member_cross_section_zero():
    """单成员截面(无横截面对比)族分应为 0(std=0 分支)."""
    from davis_analyzer.thermometer import factors

    p = factors.add_factor_columns(_panel())
    solo = p[p["index_code"] == "801010.SI"].reset_index(drop=True)
    scored = factors.family_scores(solo)
    assert (scored["mom_score"].fillna(0) == 0).all()


def test_windows_single_source():
    """窗口单一真相源:constants.THERMOMETER_WINDOWS 与文档口径一致."""
    from davis_analyzer.constants import THERMOMETER_WINDOWS
    assert THERMOMETER_WINDOWS["momentum"] == (60, 120)
    assert THERMOMETER_WINDOWS["trend"] == (60, 120)
    assert THERMOMETER_WINDOWS["volume"] == (20, 120)
    assert THERMOMETER_WINDOWS["flow"] == (20, 60)
    assert THERMOMETER_WINDOWS["limit"] == (20, 60)
