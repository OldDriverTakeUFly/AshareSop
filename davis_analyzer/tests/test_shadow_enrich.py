"""影子数据增强单测：require 镜像判别 / 状态分类 / 收盘退出对照算术."""

from __future__ import annotations

import sqlite3

import pytest

from davis_analyzer.intraday.engine import Bar, IntradayConfig
from davis_analyzer.intraday.paper_shadow import (
    SMART_CONFIG,
    classify_state,
    ensure_shadow_tables,
    eval_require,
    exit_alt_metrics,
)

REQUIRE = SMART_CONFIG["require"]  # {"trend_up": True, "vol_ratio1_max": 2.5}


# ── eval_require：镜像 GapDownSmart._passes ──


def test_eval_require_all_pass():
    ok, reason = eval_require(REQUIRE, {"trend_up": True, "vol_ratio1": 1.2})
    assert ok and reason == ""


def test_eval_require_bool_fail_reason_is_feature_name():
    ok, reason = eval_require(REQUIRE, {"trend_up": False, "vol_ratio1": 1.2})
    assert not ok and reason == "trend_up"


def test_eval_require_max_fail():
    ok, reason = eval_require(REQUIRE, {"trend_up": True, "vol_ratio1": 3.0})
    assert not ok and reason == "vol_ratio1"


def test_eval_require_missing_feature_is_nohist():
    ok, reason = eval_require(REQUIRE, {"trend_up": True, "vol_ratio1": None})
    assert not ok and reason == "nohist:vol_ratio1"
    ok, _ = eval_require(REQUIRE, {})
    assert not ok


def test_eval_require_empty_require_passes_without_features():
    assert eval_require({}, {})


# ── classify_state：信号 / 近阈值 / 过滤 / 无信号 ──


def _feat(trend_up=True, vol=1.2):
    return {"trend_up": trend_up, "vol_ratio1": vol}


def test_classify_traded_on_deep_gap_with_filters_passed():
    assert classify_state(96.9, 100.0, _feat(), REQUIRE, True) == "traded"


def test_classify_signal_boundary_is_inclusive():
    # open 恰好 = pre_close*(1-3%) → 信号成立（引擎语义 <=）
    assert classify_state(97.0, 100.0, _feat(), REQUIRE, True) == "traded"


def test_classify_filtered_by_trend():
    assert classify_state(96.0, 100.0, _feat(trend_up=False), REQUIRE, True) \
        == "filtered_trend_up"


def test_classify_filtered_by_vol():
    assert classify_state(96.0, 100.0, _feat(vol=2.6), REQUIRE, True) \
        == "filtered_vol_ratio1"


def test_classify_filtered_nohist():
    assert classify_state(96.0, 100.0, _feat(vol=None), REQUIRE, True) \
        == "filtered_nohist:vol_ratio1"


def test_classify_near_miss_band():
    # -3% < gap <= -2% → near_miss
    assert classify_state(97.5, 100.0, _feat(), REQUIRE, True) == "near_miss"
    assert classify_state(98.0, 100.0, _feat(), REQUIRE, True) == "near_miss"


def test_classify_no_signal_above_near_band():
    assert classify_state(98.5, 100.0, _feat(), REQUIRE, True) == "no_signal"


def test_classify_thin_base_wins_over_signal():
    assert classify_state(96.0, 100.0, _feat(), REQUIRE, False) == "thin_base"


# ── exit_alt_metrics：d0_close 反事实净收益 ──


def test_exit_alt_metrics_arithmetic():
    cfg = IntradayConfig()  # comm 2.5 / stamp 10 / slip 10 bps
    bars = [
        Bar("09:35", 100.2, 100.8, 99.9, 100.1),
        Bar("09:40", 100.1, 100.4, 100.5, 100.2),  # 入场 bar（低点 100.5 计入 MAE）
        Bar("09:45", 100.0, 100.3, 98.0, 98.5),    # 盘中最差低点
        Bar("14:00", 99.0, 99.2, 98.9, 99.1),
        Bar("14:05", 99.1, 99.3, 99.0, 99.2),
    ]
    m = exit_alt_metrics(
        entry_px=100.0, shares=1000, net_bps_actual=-231.57,
        bars=bars, entry_time="09:40", daily_close=99.0, config=cfg,
    )
    # 手算：sell_fill=99.0*0.999=98.901; buy_per=100.0*1.00025=100.025
    # pnl=1000*(98.901*0.99875-100.025)=-1247.63; notional=1000*(100+98.901)/2
    assert m["exit_px_d0_close"] == pytest.approx(98.901, abs=1e-6)
    assert m["net_bps_d0_close"] == pytest.approx(-125.45, abs=0.02)
    assert m["delta_bps"] == pytest.approx(-125.45 - (-231.57), abs=0.02)
    assert m["mae_bps"] == pytest.approx(-200.0, abs=1e-6)  # 98.0 vs 入场 100.0


# ── 表结构：三张增强表可建 ──


def test_ensure_shadow_tables_creates_enrich_tables():
    conn = sqlite3.connect(":memory:")
    try:
        ensure_shadow_tables(conn)
        names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"intraday_shadow_universe", "intraday_shadow_exit_alt",
                "intraday_shadow_mkt"} <= names
    finally:
        conn.close()
