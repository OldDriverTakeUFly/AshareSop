"""Tests for 盘中实时板块涨跌幅（2026-0826 修复:强势板块负涨幅的口径错配）.

覆盖：
- _realtime_phase_label: 盘中/盘后标签与盘前/周末守卫（空串→回退 sw_daily）
- _aggregate_sector_pct: 成交额加权聚合 + 未匹配名落「综合」
- _detect_sector_structure: 实时源优先于 sw_daily；实时空时回退且标签如实
- _format_sector_section: 涨跌幅/主力双日期标注
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

import stockhot.alert.panic_detector as pd_mod
from stockhot.alert.panic_detector import (
    SectorStrength,
    SectorStructure,
    _aggregate_sector_pct,
    _detect_sector_structure,
    _format_sector_section,
    _realtime_phase_label,
)


# ── 时段守卫 ──────────────────────────────────────────────────────

def test_phase_label_intraday():
    assert _realtime_phase_label(datetime(2026, 8, 26, 10, 30)) == "08-26 盘中10:30"


def test_phase_label_after_close():
    assert _realtime_phase_label(datetime(2026, 8, 26, 16, 0)) == "08-26 收盘"


def test_phase_label_pre_market_returns_empty():
    # 盘前快照仍是昨收,不能当今日实时数据用
    assert _realtime_phase_label(datetime(2026, 8, 26, 9, 20)) == ""
    assert _realtime_phase_label(datetime(2026, 8, 26, 8, 0)) == ""


def test_phase_label_weekend_returns_empty():
    # 2026-08-29 是周六
    assert _realtime_phase_label(datetime(2026, 8, 29, 10, 30)) == ""


# ── 聚合 ─────────────────────────────────────────────────────────

def test_aggregate_weighted_by_amount():
    df = pd.DataFrame([
        {"板块名称": "半导体", "涨跌幅": 5.0, "成交额": 3e9},
        {"板块名称": "消费电子", "涨跌幅": 1.0, "成交额": 1e9},
    ])
    pct = _aggregate_sector_pct(df, "板块名称", "涨跌幅", "成交额")
    # 两个电子细分 → 加权 (5*3 + 1*1) / 4 = 4.0
    assert abs(pct["电子"] - 4.0) < 1e-6


def test_aggregate_equal_weight_when_no_amount_col():
    df = pd.DataFrame([
        {"板块名称": "半导体", "涨跌幅": 2.0},
        {"板块名称": "消费电子", "涨跌幅": 4.0},
    ])
    pct = _aggregate_sector_pct(df, "板块名称", "涨跌幅", None)
    assert abs(pct["电子"] - 3.0) < 1e-6


def test_aggregate_unmatched_goes_to_zonghe():
    df = pd.DataFrame([{"板块名称": "未知板块XYZ", "涨跌幅": 1.0}])
    pct = _aggregate_sector_pct(df, "板块名称", "涨跌幅", None)
    assert "综合" in pct


# ── 接线:实时优先 / 回退 ─────────────────────────────────────────

def test_realtime_preferred_over_sw_daily(monkeypatch):
    """实时源可用时,涨跌幅用当日实时值而非 sw_daily 的 T-1."""
    monkeypatch.setattr(
        pd_mod, "_fetch_realtime_sector_pct",
        lambda: ({"电子": 4.0, "医药生物": 2.0}, "08-26 盘中10:30"))
    monkeypatch.setattr(
        pd_mod, "_fetch_sw_daily_pct",
        lambda: ({"电子": -7.0, "医药生物": -3.2}, "08-25"))
    monkeypatch.setattr(pd_mod, "_fetch_sector_main_net", lambda: ({}, ""))

    result = _detect_sector_structure({"元件": {"limit_up": 5, "limit_down": 0, "broken": 0}})
    assert result.strong[0].name == "电子"
    assert result.strong[0].pct_change == 4.0  # 实时值,而非 -7.0
    assert result.pct_change_as_of == "08-26 盘中10:30"


def test_fallback_to_sw_daily_when_realtime_empty(monkeypatch):
    monkeypatch.setattr(pd_mod, "_fetch_realtime_sector_pct", lambda: ({}, ""))
    monkeypatch.setattr(
        pd_mod, "_fetch_sw_daily_pct",
        lambda: ({"电子": -7.0}, "08-25"))
    monkeypatch.setattr(pd_mod, "_fetch_sector_main_net", lambda: ({}, ""))

    result = _detect_sector_structure({"元件": {"limit_up": 5, "limit_down": 0, "broken": 0}})
    assert result.strong[0].pct_change == -7.0
    assert result.pct_change_as_of == "08-25"


def test_main_net_as_of_threaded_through(monkeypatch):
    monkeypatch.setattr(pd_mod, "_fetch_realtime_sector_pct", lambda: ({}, ""))
    monkeypatch.setattr(pd_mod, "_fetch_sw_daily_pct", lambda: ({}, ""))
    monkeypatch.setattr(
        pd_mod, "_fetch_sector_main_net",
        lambda: ({"电子": -65.4}, "08-25"))

    result = _detect_sector_structure({"元件": {"limit_up": 5, "limit_down": 0, "broken": 0}})
    assert result.strong[0].main_net == -65.4
    assert result.main_net_as_of == "08-25"


# ── 展示:双日期标注 ──────────────────────────────────────────────

def test_format_section_labels_both_dates():
    sectors = SectorStructure(
        strong=[SectorStrength(name="电子", pct_change=4.0, limit_up=8)],
        weak=[],
        pct_change_as_of="08-26 盘中10:30",
        main_net_as_of="08-25",
        available=True,
    )
    lines = _format_sector_section(sectors)
    assert any("涨跌幅截至 08-26 盘中10:30" in ln and "主力截至 08-25" in ln
               for ln in lines)
