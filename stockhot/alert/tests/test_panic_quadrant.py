"""Tests for panic_detector 四象限细化 — direction/quadrant/intensity.

覆盖：
- _classify_quadrant 四象限边界（高/低波 × 上/下方向）
- _compute_intensity 强度分（涨日跌幅贡献=0，跌日三项叠加）
- _detect_direction 方向综合分符号
- format_alert_message 四象限标题与 emoji
- _save_panic_history 直读 report（不依赖 detail 文本）
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from stockhot.alert.panic_detector import (
    DirectionReading,
    IndexVolatility,
    LimitBehaviorReading,
    PanicReport,
    SignalResult,
    _classify_quadrant,
    _compute_intensity,
    _QUADRANT_META,
    format_alert_message,
)


# ── 公共 fixture ──────────────────────────────────────────────────

def _high_vol_indices() -> list[IndexVolatility]:
    """高波 P90+ × 5（系统性恐慌阈值满足）."""
    return [
        IndexVolatility("000688.SH", "科创50", 60.0, 99, "极度恐慌"),
        IndexVolatility("399006.SZ", "创业板指", 50.0, 98, "极度恐慌"),
        IndexVolatility("399001.SZ", "深证成指", 40.0, 97, "极度恐慌"),
        IndexVolatility("000300.SH", "沪深300", 30.0, 95, "极度恐慌"),
        IndexVolatility("000001.SH", "上证指数", 20.0, 92, "明显恐慌"),
    ]


def _low_vol_indices() -> list[IndexVolatility]:
    """低波 P90- × 5（无系统性恐慌）."""
    return [
        IndexVolatility("000688.SH", "科创50", 15.0, 40, "正常"),
        IndexVolatility("399006.SZ", "创业板指", 12.0, 35, "正常"),
        IndexVolatility("399001.SZ", "深证成指", 10.0, 30, "正常"),
        IndexVolatility("000300.SH", "沪深300", 8.0, 25, "平静"),
        IndexVolatility("000001.SH", "上证指数", 6.0, 20, "平静"),
    ]


def _direction(score: float, **kwargs) -> DirectionReading:
    """构造 DirectionReading，direction_score + label 自动算，其他可覆盖."""
    label = "上涨" if score > 0 else ("下跌" if score < 0 else "中性")
    defaults = dict(direction_score=score, direction_label=label, available=True)
    defaults.update(kwargs)
    return DirectionReading(**defaults)


# ═══════════════════════════════════════════════════════════════════
# 四象限边界
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "direction, indices, expected",
    [
        # 高波 × 下跌 → 下跌恐慌
        ("down", "high", "下跌恐慌"),
        # 高波 × 上涨 → 逼空过热
        ("up", "high", "逼空过热"),
        # 低波 × 下跌 → 阴跌预警
        ("down", "low", "阴跌预警"),
        # 低波 × 上涨 → 强势上涨
        ("up", "low", "强势上涨"),
        # 中性（direction_score=0）按上涨处理，避免无端恐慌
        ("neutral", "high", "逼空过热"),
        ("neutral", "low", "强势上涨"),
    ],
)
def test_quadrant_classification(direction, indices, expected):
    """四象限边界：高/低波 × 上/下/中方向."""
    score_map = {"down": -1.0, "up": 1.0, "neutral": 0.0}
    indices_map = {"high": _high_vol_indices(), "low": _low_vol_indices()}
    d = _direction(score_map[direction])
    assert _classify_quadrant(d, indices_map[indices]) == expected


def test_quadrant_no_indices_returns_empty():
    """无 RV20 数据时返回空串（数据全部不可用）."""
    d = _direction(-1.0)
    assert _classify_quadrant(d, []) == ""


def test_quadrant_direction_none_falls_back_to_vol():
    """direction=None 时按波动率粗分（不崩）。"""
    assert _classify_quadrant(None, _high_vol_indices()) == "下跌恐慌"
    assert _classify_quadrant(None, _low_vol_indices()) == "强势上涨"


def test_quadrant_meta_has_emoji_for_all_four():
    """四象限元数据完整（emoji 用于消息标题）."""
    for q in ["下跌恐慌", "逼空过热", "阴跌预警", "强势上涨"]:
        assert q in _QUADRANT_META
        assert _QUADRANT_META[q]["emoji"]
        assert _QUADRANT_META[q]["disclaimer"]


# ═══════════════════════════════════════════════════════════════════
# 强度分
# ═══════════════════════════════════════════════════════════════════


def test_intensity_drop_day_scores_higher_than_up_day():
    """跌日强度分应高于涨日（核心特性：涨日跌幅贡献=0）."""
    indices = _high_vol_indices()
    # 跌日：上证 -1.6% + 涨40 跌17
    dir_down = _direction(-1.0, sse_pct_chg=-1.6, limit_up=40, limit_down=17)
    # 涨日：上证 +0.25% + 涨102 跌1
    dir_up = _direction(1.0, sse_pct_chg=0.25, limit_up=102, limit_down=1)

    score_down, _ = _compute_intensity(indices, dir_down)
    score_up, _ = _compute_intensity(indices, dir_up)
    assert score_down > score_up, f"跌日 {score_down} 应高于涨日 {score_up}"


def test_intensity_up_day_drop_contribution_is_zero():
    """涨日跌幅贡献为 0（max(0, -正涨幅) = 0）。"""
    indices = _high_vol_indices()  # rv_max_pct = 99
    dir_up = _direction(1.0, sse_pct_chg=1.5, limit_up=100, limit_down=1)
    score, _ = _compute_intensity(indices, dir_up)
    # rv 贡献 = 99 × 0.5 = 49.5；跌停占比贡献 ≈ 1/101 × 100 × 0.2 ≈ 0.2
    # 跌幅贡献 = 0；总分应接近 49.5 + 0.2 ≈ 49.7
    assert 49.0 <= score <= 50.5, f"涨日强度 {score} 应接近 49.7"


def test_intensity_clamped_to_100():
    """极端跌幅 + 极端跌停占比不会超过 100。"""
    indices = [IndexVolatility("a", "A", 100, 100, "极度恐慌")] * 5
    dir_extreme = _direction(-1.0, sse_pct_chg=-20.0, limit_up=0, limit_down=100)
    score, label = _compute_intensity(indices, dir_extreme)
    assert score == 100.0
    assert label == "极高"


def test_intensity_labels_boundary():
    """5 档等级标签边界（极低/偏低/中等/偏高/极高）."""
    indices_low = [IndexVolatility("a", "A", 5, 10, "平静")] * 5
    d = _direction(1.0, sse_pct_chg=1.0, limit_up=50, limit_down=5)
    score, label = _compute_intensity(indices_low, d)
    # rv 贡献 = 10 × 0.5 = 5；跌幅贡献 = 0；跌停占比贡献 = 5/55 × 100 × 0.2 ≈ 1.8
    # 总分 ≈ 6.8 → 极低
    assert label == "极低"
    assert score < 20


# ═══════════════════════════════════════════════════════════════════
# 消息格式
# ═══════════════════════════════════════════════════════════════════


def _build_report(quadrant: str, intensity: float = 50.0) -> PanicReport:
    """构造完整 report 用于格式化测试."""
    return PanicReport(
        trade_date="2026-07-24",
        timestamp="14:30",
        signals=[SignalResult("系统性恐慌", True, "5/5 P90+", available=True)],
        volatility_indices=_high_vol_indices(),
        direction=_direction(-1.0, sse_pct_chg=-1.6, cum_5d_pct=-3.2,
                              limit_up=40, limit_down=17, broken=10, limit_ratio=2.35),
        quadrant=quadrant,
        intensity_score=intensity,
        intensity_label="偏高" if intensity >= 55 else "中等",
    )


@pytest.mark.parametrize(
    "quadrant, emoji",
    [
        ("下跌恐慌", "🔴"),
        ("逼空过热", "🟠"),
        ("阴跌预警", "🟡"),
        ("强势上涨", "🟢"),
    ],
)
def test_format_message_uses_correct_emoji(quadrant, emoji):
    """四象限对应不同 emoji 标题。"""
    report = _build_report(quadrant)
    msg = format_alert_message(report)
    first_line = msg.split("\n")[0]
    assert first_line.startswith(emoji), f"{quadrant} 应以 {emoji} 开头，实际：{first_line}"


def test_format_message_includes_direction_section():
    """消息包含【方向拆解】章节。"""
    report = _build_report("下跌恐慌")
    msg = format_alert_message(report)
    assert "【方向拆解】" in msg
    assert "上证当日" in msg
    assert "-1.60%" in msg


def test_format_message_includes_intensity_in_header():
    """标题包含强度分。"""
    report = _build_report("下跌恐慌", intensity=75.0)
    msg = format_alert_message(report)
    assert "75/100" in msg.split("\n")[0]


def test_format_message_quadrant_empty_degrades_gracefully():
    """quadrant 为空时降级为中性标题，不崩。"""
    report = PanicReport(trade_date="2026-07-24", timestamp="14:30")
    msg = format_alert_message(report)
    assert "⚪" in msg
    assert "数据不足" in msg


def test_format_message_disclaimer_varies_by_quadrant():
    """不同象限有不同的免责声明（减仓 vs 加仓 vs 防回撤）。"""
    msg_down = format_alert_message(_build_report("下跌恐慌"))
    msg_up = format_alert_message(_build_report("强势上涨"))
    assert "减仓" in msg_down
    assert "加仓" in msg_up


# ═══════════════════════════════════════════════════════════════════
# _save_panic_history 直读 report（不再 regex 解析 detail）
# ═══════════════════════════════════════════════════════════════════


def test_save_panic_history_reads_structured_fields(tmp_path, monkeypatch):
    """_save_panic_history 应从 report.direction 等结构化字段直读，
    不依赖 detail 文本解析。"""
    import importlib.util
    import sys

    # 加载 run_panic_alert 模块（不在 sys.path 中，用 spec 加载）
    spec = importlib.util.spec_from_file_location(
        "run_panic_alert",
        "stockhot/invest_sop/scripts/run_panic_alert.py",
    )
    rpa = importlib.util.module_from_spec(spec)
    sys.modules["run_panic_alert"] = rpa
    spec.loader.exec_module(rpa)

    # Mock repository
    mock_repo = MagicMock()
    import stockhot.data_layer as dl
    monkeypatch.setattr(dl, "get_repository", lambda: mock_repo)

    # 构造 report：故意在 signal.detail 里写"错误"数据，验证不被 regex 解析
    report = PanicReport(
        trade_date="2026-07-24",
        timestamp="14:30",
        signals=[SignalResult("行为面恐慌抛售", False,
                              "涨停999/跌停999/炸板999，涨跌停比999", available=True)],
        volatility_indices=[IndexVolatility("a", "A", 60, 99, "极度恐慌")],
        direction=_direction(-1.0, sse_pct_chg=-1.6,
                              limit_up=40, limit_down=17, broken=10, limit_ratio=2.35),
        quadrant="下跌恐慌",
        intensity_score=60.3,
    )

    rpa._save_panic_history(report)

    # 验证：从 direction 直读，不是 detail 里的 999
    call = mock_repo.save_panic_history.call_args
    assert call.kwargs["limit_up"] == 40
    assert call.kwargs["limit_down"] == 17
    assert call.kwargs["broken"] == 10
    assert call.kwargs["up_down_ratio"] == 2.35
    assert call.kwargs["quadrant"] == "下跌恐慌"
    assert call.kwargs["intensity_score"] == 60.3
    assert call.kwargs["direction_score"] == -1.0
    assert call.kwargs["sse_pct_chg"] == -1.6


def test_save_panic_history_handles_missing_direction(tmp_path, monkeypatch):
    """direction=None 时不崩，行为面字段传 None。"""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "run_panic_alert2",
        "stockhot/invest_sop/scripts/run_panic_alert.py",
    )
    rpa = importlib.util.module_from_spec(spec)
    sys.modules["run_panic_alert2"] = rpa
    spec.loader.exec_module(rpa)

    mock_repo = MagicMock()
    import stockhot.data_layer as dl
    monkeypatch.setattr(dl, "get_repository", lambda: mock_repo)

    report = PanicReport(trade_date="2026-07-24", timestamp="14:30")
    # direction=None 是默认值
    rpa._save_panic_history(report)

    call = mock_repo.save_panic_history.call_args
    assert call.kwargs["limit_up"] is None
    assert call.kwargs["limit_down"] is None
    assert call.kwargs["quadrant"] is None  # quadrant="" → or None
