"""Tests for vol_streak_analyzer — 高波持续分析三维度."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from stockhot.alert.vol_streak_analyzer import (
    VolStreakReport,
    SectorImpact,
    analyze_vol_streak,
    format_streak_brief,
    _compute_current_streak,
    _compute_historical_streaks,
    _identify_streak_event,
)


# ═══════════════════════════════════════════════════════════════════
# 持续天数
# ═══════════════════════════════════════════════════════════════════


def test_current_streak_from_volatility_index():
    """从 daily_volatility_index 表算当前连续高波天数."""
    # 用真实 DB 测试（已知有 13 天 P90+ 数据）
    days, start = _compute_current_streak("2026-07-31")
    assert days > 0, "07-31 应该有持续高波"
    assert start  # 起始日非空


def test_current_streak_no_data_returns_zero():
    """数据起始日之前返回 0."""
    # 2020 年在 daily_volatility_index 表（2026-07 起）之前
    days, start = _compute_current_streak("2020-01-01")
    assert days == 0
    assert start == ""


# ═══════════════════════════════════════════════════════════════════
# 历史回算
# ═══════════════════════════════════════════════════════════════════


def test_historical_streaks_identifies_multiple_periods():
    """历史回算应识别出多个高波期（5.5年数据）."""
    streaks = _compute_historical_streaks()
    assert len(streaks) >= 3, f"应至少有 3 个历史高波期，实际 {len(streaks)}"
    # 每个高波期有完整字段
    for s in streaks:
        assert s["start"]
        assert s["end"]
        assert s["days"] >= 3  # 最小长度
        assert s["max_rv"] > 0


def test_historical_streaks_current_period_is_longest_or_recent():
    """最后一个高波期应该是当前或最近的（含今日数据）."""
    streaks = _compute_historical_streaks()
    if streaks:
        last = streaks[-1]
        # 最后一个的 end 应该接近今日（2026 年）
        assert "2026" in last["end"]


def test_identify_streak_event_known_dates():
    """已知事件日期的标注."""
    import datetime
    # 2024-09 政策大反转
    assert _identify_streak_event(
        datetime.datetime(2024, 9, 27), datetime.datetime(2024, 10, 7)
    ) == "政策大反转"
    # 2022-03 上海封城
    assert _identify_streak_event(
        datetime.datetime(2022, 3, 15), datetime.datetime(2022, 4, 13)
    ) == "上海封城"
    # 未知日期返回空
    assert _identify_streak_event(
        datetime.datetime(2023, 6, 1), datetime.datetime(2023, 6, 10)
    ) == ""


# ═══════════════════════════════════════════════════════════════════
# 板块影响（受影响 + 逆势）
# ═══════════════════════════════════════════════════════════════════


def test_analyze_vol_streak_returns_impacted_and_resilient():
    """analyze_vol_streak 在高波期应返回受影响 + 逆势板块."""
    report = analyze_vol_streak("2026-07-31")
    assert report.available
    assert report.is_high_vol
    assert report.current_days > 0
    # 高波期间应该有板块影响数据
    # （取决于 limit_pool 数据完整性，至少有其一）
    assert len(report.impacted_sectors) > 0 or len(report.resilient_sectors) > 0


def test_analyze_vol_streak_historical_context():
    """analyze_vol_streak 历史对比数据."""
    report = analyze_vol_streak("2026-07-31")
    assert report.historical_count >= 3  # 至少 3 个历史高波期
    assert report.historical_avg_days > 0
    assert report.historical_max_days > 0
    assert report.historical_max_note  # 有标注


def test_analyze_vol_streak_rank():
    """当前高波期的历史排名."""
    report = analyze_vol_streak("2026-07-31")
    if report.current_days >= 3:
        assert report.current_rank  # 有排名描述
        assert "第" in report.current_rank


# ═══════════════════════════════════════════════════════════════════
# format_streak_brief
# ═══════════════════════════════════════════════════════════════════


def test_format_streak_brief_high_vol():
    """高波时格式化摘要行."""
    report = VolStreakReport(
        current_days=26, is_high_vol=True,
        historical_count=6, historical_avg_days=19.0, historical_max_days=25,
    )
    brief = format_streak_brief(report)
    assert "26" in brief
    assert "19" in brief
    assert "25" in brief


def test_format_streak_brief_low_vol_returns_empty():
    """低波时返回空串."""
    report = VolStreakReport(is_high_vol=False, current_days=0)
    assert format_streak_brief(report) == ""


def test_format_streak_brief_no_history():
    """有高波但无历史数据时的降级."""
    report = VolStreakReport(
        current_days=5, is_high_vol=True,
        historical_count=0,
    )
    brief = format_streak_brief(report)
    assert "5" in brief  # 仍有天数
    assert "历史" not in brief  # 但无历史对比


# ═══════════════════════════════════════════════════════════════════
# SectorImpact dataclass
# ═══════════════════════════════════════════════════════════════════


def test_sector_impact_divergence_flag():
    """SectorImpact 的 has_divergence 标注."""
    s = SectorImpact(name="电子", limit_count=10, main_net_total=-50.0, has_divergence=True)
    assert s.has_divergence
    s2 = SectorImpact(name="银行", limit_count=5, main_net_total=10.0)
    assert not s2.has_divergence
