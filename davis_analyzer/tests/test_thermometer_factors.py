"""五族因子纯函数:构造 2 指数×22 日 panel,断言关键窗口值与 z 性质."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _panel() -> pd.DataFrame:
    # 61 自然日(合成日历):覆盖 60 日滚动窗(trend 族 hh60/ma60)
    dates = [d.strftime("%Y%m%d")
             for d in pd.date_range("2022-01-04", periods=61, freq="D")]
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
    # mom_level 第21行(idx=20 起 20 日窗口满)= 1.01**20−1
    assert abs(a.loc[21, "mom_level"] - (1.01 ** 20 - 1)) < 1e-9
    # flow_level 20 日和: 0.001×20 = 0.02
    assert abs(a.loc[21, "flow_level"] - 0.02) < 1e-9
    # trend_slope 恒 1(每日上涨)
    assert a.loc[21, "trend_slope"] == 1.0
    # pv_decay: 801010 放量(amount 递增)且上涨 → 1.0;801011 缩量上涨?不,下跌 → 0.3
    b = p[p["index_code"] == "801011.SI"].reset_index(drop=True)
    assert a.loc[21, "pv_decay"] == 1.0
    assert b.loc[21, "pv_decay"] == 0.3  # 价格反向
    # 窗口不足 → NaN
    assert np.isnan(a.loc[5, "mom_level"])


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
