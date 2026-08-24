"""stockhot.macro 趋势列渲染的单元测试（离线，无网络调用）."""
from __future__ import annotations

import pandas as pd

from stockhot.macro import (
    MacroSnapshot,
    _fmt_lpr_trend,
    _fmt_monthly_trend,
    _fmt_shibor_trend,
    _tail_points,
    format_macro_section,
)


def test_tail_points_labels_and_nan() -> None:
    pts = _tail_points(
        pd.Series(["202605", "202606", "202607"]), pd.Series([48.3, 48.8, 49.0])
    )
    assert pts == [("5月", 48.3), ("6月", 48.8), ("7月", 49.0)]

    pts_nan = _tail_points(
        pd.Series(["202605", "202606", "202607"]),
        pd.Series([48.3, float("nan"), 49.0]),
    )
    assert pts_nan == [("5月", 48.3), ("7月", 49.0)]


def test_monthly_trend_rising() -> None:
    pts = [("5月", 48.3), ("6月", 48.8), ("7月", 49.0)]
    assert _fmt_monthly_trend(pts) == "5月48.3→6月48.8→7月49.0 ↗"


def test_monthly_trend_flat() -> None:
    pts = [("5月", 49.0), ("6月", 49.0), ("7月", 49.0)]
    assert _fmt_monthly_trend(pts).endswith("→")


def test_monthly_trend_signed_falling() -> None:
    pts = [("5月", -2.6), ("6月", -2.9), ("7月", -3.0)]
    assert _fmt_monthly_trend(pts, signed=True) == "5月-2.6→6月-2.9→7月-3.0 ↘"


def test_shibor_trend() -> None:
    # 10 日累计下行 9bp（>5bp 噪声阈值）→ ↘；首末差 ≤5bp 时视为横盘
    pts = [(f"d{i}", 1.52 - i * 0.01) for i in range(10)]
    out = _fmt_shibor_trend(pts)
    assert "10日均值" in out and "↘" in out
    assert _fmt_shibor_trend([("d0", 1.4)]) == "—"
    flat = [(f"d{i}", 1.40 + i * 0.0004) for i in range(10)]  # 累计 3.6bp
    assert _fmt_shibor_trend(flat).endswith("→")


def test_lpr_trend_flat() -> None:
    pts = [(f"{m}月", 3.0) for m in range(1, 13)]
    assert _fmt_lpr_trend(pts) == "近12个月持平"


def test_lpr_trend_recent_cut() -> None:
    pts = [(f"{m}月", 3.10) for m in range(1, 5)] + [
        (f"{m}月", 3.0) for m in range(5, 13)
    ]
    assert _fmt_lpr_trend(pts) == "5月 3.10→3.00（-10bp）后持平"


def test_format_section_trend_column() -> None:
    snap = MacroSnapshot(pmi=49.0, pmi_month="202607")
    snap.trends["PMI"] = [("5月", 48.3), ("6月", 48.8), ("7月", 49.0)]
    md = format_macro_section(snap)
    assert "| 指标 | 最新值 | 月份 | 近期趋势 | 含义 |" in md
    assert "5月48.3→6月48.8→7月49.0 ↗" in md


def test_format_section_missing_trends_renders_dash() -> None:
    snap = MacroSnapshot(cpi_yoy=0.5, inflation_month="202607")
    md = format_macro_section(snap)
    row = next(l for l in md.splitlines() if l.startswith("| CPI 同比"))
    assert "—" in row
