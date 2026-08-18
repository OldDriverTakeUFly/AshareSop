"""study.py 晋级率矩阵/收益分布/分桶有效性测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from davis_analyzer.limitup import report, study


def _ev(n: int = 40) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": [f"6000{i:02d}.SH" for i in range(n)],
        "trade_date": ["20240102"] * n,
        "consecutive_boards": ([1] * 20 + [2] * 20)[:n],
        "ret_open_1": ([0.03] * 10 + [-0.02] * 10 + [0.05] * 15 + [-0.06] * 5)[:n],
        "promoted": ([True] * 12 + [False] * 8 + [True] * 10 + [False] * 10)[:n],
        "pattern_label": (["突破型"] * 20 + ["其他"] * 20)[:n],
    })


def test_promotion_matrix() -> None:
    m = study.promotion_matrix(_ev())
    assert list(m.index) == [1, 2]
    assert abs(m.loc[1, "promo_rate"] - 0.6) < 1e-9
    assert m.loc[1, "n"] == 20


def test_return_distribution_stats() -> None:
    d = study.return_distribution(_ev(), by=["consecutive_boards"])
    row1 = d[d["consecutive_boards"] == 1].iloc[0]
    assert abs(row1["mean"] - 0.005) < 1e-9  # (10*0.03 + 10*-0.02)/20
    assert row1["win_rate"] == 0.5
    assert row1["n"] == 20


def test_feature_effectiveness_flags_small_sample() -> None:
    df = study.feature_effectiveness(_ev(10), "pattern_label")
    assert set(df["pattern_label"]) == {"突破型"}
    assert not bool(df.iloc[0]["enough_sample"])  # 10 < 30


def test_seal_bucket_perturbation() -> None:
    # 边界样本：0.021/0.049 紧贴阈值，±20% 扰动后档位漂移
    ev = pd.DataFrame({
        "seal_ratio": [0.01] * 10 + [0.021] * 5 + [0.03] * 10
                      + [0.049] * 5 + [0.10] * 10,
        "ret_open_1": ([-0.02] * 10 + [-0.10] * 5 + [0.04] * 10
                       + [0.10] * 5 + [0.08] * 10),
    })
    df = study.seal_bucket_perturbation(ev, base=(0.02, 0.05))
    assert list(df["封档"]) == ["弱", "中", "强"]
    weak = df[df["封档"] == "弱"].iloc[0]
    mid = df[df["封档"] == "中"].iloc[0]
    strong = df[df["封档"] == "强"].iloc[0]
    # 弱档：base/0.8x 均 -0.02；1.2x 阈值 0.024 吸收 0.021 行
    assert abs(weak["mean_base"] - (-0.02)) < 1e-9
    assert abs(weak["mean_0.8x"] - (-0.02)) < 1e-9
    assert abs(weak["mean_1.2x"] - (10 * -0.02 + 5 * -0.10) / 15) < 1e-9
    assert bool(weak["dir_stable"])  # 三场景均值同负
    # 中档：base=0.02、1.2x=0.06；0.8x 阈值 0.04 使 0.049 行升强档 → 均值转负
    assert abs(mid["mean_base"] - 0.02) < 1e-9
    assert abs(mid["mean_1.2x"] - (10 * 0.04 + 5 * 0.10) / 15) < 1e-9
    assert mid["mean_0.8x"] < 0
    assert not bool(mid["dir_stable"])  # 方向翻转
    # 强档：三场景同正
    assert abs(strong["mean_base"] - 0.08) < 1e-9
    assert bool(strong["dir_stable"])


def test_seal_bucket_perturbation_empty_bucket_unstable() -> None:
    # 空档均值为 NaN：无方向可言 → dir_stable=False（宁缺毋错）
    ev = pd.DataFrame({"seal_ratio": [0.10, 0.20], "ret_open_1": [0.05, 0.06]})
    df = study.seal_bucket_perturbation(ev, base=(0.02, 0.05))
    weak = df[df["封档"] == "弱"].iloc[0]
    assert np.isnan(weak["mean_base"])
    assert not bool(weak["dir_stable"])


def test_report_writes_markdown(tmp_path) -> None:
    tbl = pd.DataFrame({"a": [1, 2], "b": [0.5, -0.25]})
    md = report.df_to_md_table(tbl)
    # iterrows 将混合 int/float 行上溯为 float64，_fmt 统一 %.4f 渲染
    assert "| a | b |" in md and "|---|---|" in md and "| 1.0000 | 0.5000 |" in md
    out = report.write_report(tmp_path / "r.md", "测试", [("小节", md)])
    assert out.exists() and "测试" in out.read_text(encoding="utf-8")


# ── threshold_perturbation：形态与 regime 阈值 ±20% 扰动（规格 §4 补课）──

def _px_frame(code: str, prior_closes: list[float], event_close: float,
              event_date: str) -> pd.DataFrame:
    """合成日线：prior 各日 OHLC=close，末行为事件日（event_date 须为工作日）."""
    dates = pd.bdate_range(end=event_date, periods=len(prior_closes) + 1)
    rows = [
        (code, d.strftime("%Y%m%d"), c, c, c, c, c, 1e4, 1e6, 1.0)
        for d, c in zip(dates[:-1], prior_closes)
    ]
    rows.append((code, event_date, event_close, event_close, event_close,
                 event_close, prior_closes[-1], 1e5, 1e7, 1.0))
    return pd.DataFrame(rows, columns=[
        "ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
        "vol", "amount", "adj_factor"])


def _breakout_px(code: str, event_date: str) -> pd.DataFrame:
    """60 日 @10 平盘 + 事件日 close 12.0：±20% 扰动下恒为突破型（≥10×1.176）."""
    return _px_frame(code, [10.0] * 60, 12.0, event_date)


def _wide_box_px(code: str, event_date: str) -> pd.DataFrame:
    """30 日 @10 → 30 日 @15 + 事件日 close 15.5：box40=0.5 恒非突破 → 其他."""
    return _px_frame(code, [10.0] * 30 + [15.0] * 30, 15.5, event_date)


def _boundary_px(code: str, event_date: str) -> pd.DataFrame:
    """60 日 @10 + 事件日 close 9.9：基准 9.9 ≥ 9.8 突破、1.2x 9.9 < 11.76 失守."""
    return _px_frame(code, [10.0] * 60, 9.9, event_date)


def _regime_row(trade_date: str, max_boards: int, limit_up_count: int = 60,
                premium: float = 0.05, promo_12: float = 0.5) -> dict:
    """合成 regime 轴行：premium/promo_12 取中性值，不触发冰点/退潮."""
    return {"trade_date": trade_date, "max_boards": max_boards,
            "limit_up_count": limit_up_count, "premium": premium,
            "promo_12": promo_12}


def _perturb_rows(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    assert list(df.columns) == ["结论", "基准差", "扰动0.8x差", "扰动1.2x差",
                                "dir_stable"]
    row1 = df[df["结论"].str.contains("突破型")].iloc[0]
    row2 = df[df["结论"].str.contains("高潮")].iloc[0]
    return row1, row2


def test_threshold_perturbation_stable() -> None:
    d1, d2 = "20240102", "20240103"
    brk = [f"6001{i:02d}.SH" for i in range(4)]
    oth = [f"6002{i:02d}.SH" for i in range(4)]
    events = pd.DataFrame({
        "ts_code": brk + oth,
        "trade_date": [d1] * 4 + [d2] * 4,
        "ret_open_1": [0.05] * 4 + [-0.05] * 4,
        "promoted": [True, True, True, False] + [False] * 4,
    })
    prices = pd.concat(
        [_breakout_px(c, d1) for c in brk] + [_wide_box_px(c, d2) for c in oth],
        ignore_index=True)
    # d1 max_boards=9 ≥ 7×1.2=8.4 恒高潮；d2 max_boards=3 < 7×0.8=5.6 恒回暖
    regime = pd.DataFrame([_regime_row(d1, 9, limit_up_count=200),
                           _regime_row(d2, 3)])

    row1, row2 = _perturb_rows(study.threshold_perturbation(events, prices, regime))
    # 结论1：突破组晋级率 3/4 − 其他组 0/4 = 0.75，扰动下标签不变
    for col in ("基准差", "扰动0.8x差", "扰动1.2x差"):
        assert row1[col] == 0.75
        assert abs(row2[col] - 0.10) < 1e-9  # 0.05 − (−0.05)
    assert bool(row1["dir_stable"]) and bool(row2["dir_stable"])


def test_threshold_perturbation_pattern_flip() -> None:
    d1 = "20240102"
    events = pd.DataFrame({
        "ts_code": ["600501.SH", "600502.SH", "600503.SH", "600504.SH"],
        "trade_date": [d1] * 4,
        "ret_open_1": [0.0] * 4,
        # 稳定突破(promoted=False) + 临界突破(promoted=True) + 2×恒其他(False)
        "promoted": [False, True, False, False],
    })
    prices = pd.concat([
        _breakout_px("600501.SH", d1),
        _boundary_px("600502.SH", d1),
        _wide_box_px("600503.SH", d1),
        _wide_box_px("600504.SH", d1),
    ], ignore_index=True)
    regime = pd.DataFrame([_regime_row(d1, 9, limit_up_count=200)])

    row1, row2 = _perturb_rows(study.threshold_perturbation(events, prices, regime))
    # 基准/0.8x：突破组 {F,T}=0.5 − 其他组 {F,F}=0 → +0.5
    assert row1["基准差"] == 0.5 and row1["扰动0.8x差"] == 0.5
    # 1.2x：临界事件沦入其他组 → 突破组 {F}=0 − 其他组 {T,F,F}=1/3 → 翻负
    assert abs(row1["扰动1.2x差"] - (0.0 - 1 / 3)) < 1e-9
    assert not bool(row1["dir_stable"])
    # 全部事件同处高潮日：其他档空 → 差值 NaN → 不稳定（宁缺毋错）
    assert np.isnan(row2["基准差"]) and not bool(row2["dir_stable"])


def test_threshold_perturbation_regime_flip() -> None:
    d1, d2, d3 = "20240102", "20240103", "20240104"
    hot = [f"6001{i:02d}.SH" for i in range(4)]
    events = pd.DataFrame({
        "ts_code": hot + ["600201.SH", "600202.SH", "600301.SH", "600302.SH"],
        "trade_date": [d1] * 4 + [d2] * 2 + [d3] * 2,
        "ret_open_1": [0.10] * 4 + [-0.05] * 2 + [-0.50] * 2,
        "promoted": [True, True, True, False, False, False, False, False],
    })
    prices = pd.concat(
        [_breakout_px(c, d1) for c in hot]
        + [_wide_box_px(c, d2) for c in ("600201.SH", "600202.SH")]
        + [_wide_box_px(c, d3) for c in ("600301.SH", "600302.SH")],
        ignore_index=True)
    # d3 max_boards=6：基准 6<7 回暖；0.8x 阈值 5.6 → 6≥5.6 升高潮；1.2x 8.4 回暖
    regime = pd.DataFrame([
        _regime_row(d1, 9, limit_up_count=200),
        _regime_row(d2, 3),
        _regime_row(d3, 6),
    ])

    row1, row2 = _perturb_rows(study.threshold_perturbation(events, prices, regime))
    # 结论1 不受 regime 扰动影响：3/4 − 0 恒正
    assert bool(row1["dir_stable"])
    # 基准/1.2x：高潮 0.10 − 其他 (−0.05×2−0.50×2)/4=−0.275 → +0.375
    assert abs(row2["基准差"] - 0.375) < 1e-9
    assert abs(row2["扰动1.2x差"] - 0.375) < 1e-9
    # 0.8x：d3 升高潮 → 高潮均值 (0.4−1.0)/6=−0.10 − 其他 −0.05 → −0.05 翻负
    assert abs(row2["扰动0.8x差"] - (-0.05)) < 1e-9
    assert not bool(row2["dir_stable"])
