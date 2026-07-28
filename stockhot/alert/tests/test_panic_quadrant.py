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
# 强度分（象限专属公式，2026-07-28 修订）
# 核心原则：强度 = 该象限特征的显著程度，与方向无关
# ═══════════════════════════════════════════════════════════════════


def test_intensity_diagonal_high_score():
    """象限专属强度对角线高分：场景与象限匹配时分数应最高.

    同样的高波数据，4 个场景对应 4 个象限，每个场景在自己的象限
    公式下得分应高于在其他象限公式下的得分（验证公式差异化有效）。
    """
    indices = _high_vol_indices()  # rv_max_pct = 99
    # 场景：逼空大涨 +2% 涨120/跌2
    dir_squeeze = _direction(1.0, sse_pct_chg=2.0, limit_up=120, limit_down=2)
    # 场景：暴跌真恐慌 -2.5% 涨20/跌80
    dir_panic = _direction(-1.0, sse_pct_chg=-2.5, limit_up=20, limit_down=80)

    # 逼空场景用🟠公式应高于用🔴公式（涨幅项贡献 > 跌幅项贡献）
    score_orange = _compute_intensity(indices, dir_squeeze, "逼空过热")[0]
    score_red_for_squeeze = _compute_intensity(indices, dir_squeeze, "下跌恐慌")[0]
    assert score_orange > score_red_for_squeeze, (
        f"逼空场景用🟠公式({score_orange})应高于🔴公式({score_red_for_squeeze})"
    )

    # 暴跌场景用🔴公式应高于用🟠公式
    score_red = _compute_intensity(indices, dir_panic, "下跌恐慌")[0]
    score_orange_for_panic = _compute_intensity(indices, dir_panic, "逼空过热")[0]
    assert score_red > score_orange_for_panic, (
        f"暴跌场景用🔴公式({score_red})应高于🟠公式({score_orange_for_panic})"
    )


def test_intensity_same_data_same_quadrant_consistency():
    """同样数据 + 同样象限 → 稳定的分数（公式确定性）."""
    indices = _high_vol_indices()
    d = _direction(-1.0, sse_pct_chg=-1.5, limit_up=30, limit_down=40)
    s1, _ = _compute_intensity(indices, d, "下跌恐慌")
    s2, _ = _compute_intensity(indices, d, "下跌恐慌")
    assert s1 == s2


def test_intensity_squeeze_high_when_strong_up():
    """🟠 逼空过热：涨幅大 + 涨停多 → 强度高（不再是涨日低分）."""
    indices = _high_vol_indices()  # rv_max_pct = 99
    # 强势逼空：+3% 涨150/跌2
    d_strong = _direction(1.0, sse_pct_chg=3.0, limit_up=150, limit_down=2)
    score_strong, label_strong = _compute_intensity(indices, d_strong, "逼空过热")
    # 弱势逼空：+0.3% 涨50/跌30
    d_weak = _direction(1.0, sse_pct_chg=0.3, limit_up=50, limit_down=30)
    score_weak, _ = _compute_intensity(indices, d_weak, "逼空过热")
    assert score_strong > score_weak, "强势逼空分数应高于弱势"
    assert score_strong >= 70, f"强势逼空分数 {score_strong} 应≥70（特征显著）"


def test_intensity_panic_high_when_strong_drop():
    """🔴 下跌恐慌：跌幅大 + 跌停多 → 强度高."""
    indices = _high_vol_indices()
    d_strong = _direction(-1.0, sse_pct_chg=-3.0, limit_up=10, limit_down=120)
    score_strong, _ = _compute_intensity(indices, d_strong, "下跌恐慌")
    d_weak = _direction(-1.0, sse_pct_chg=-0.3, limit_up=50, limit_down=20)
    score_weak, _ = _compute_intensity(indices, d_weak, "下跌恐慌")
    assert score_strong > score_weak
    assert score_strong >= 70


def test_intensity_clamped_to_100():
    """极端情况分数不超过 100."""
    indices = [IndexVolatility("a", "A", 100, 100, "极度恐慌")] * 5
    # 🔴 极端暴跌
    d = _direction(-1.0, sse_pct_chg=-20.0, limit_up=0, limit_down=100)
    score, label = _compute_intensity(indices, d, "下跌恐慌")
    assert score == 100.0
    assert label == "极高"


def test_intensity_labels_five_buckets():
    """5 档等级标签边界（极低/偏低/中等/偏高/极高）."""
    indices_low = [IndexVolatility("a", "A", 5, 10, "平静")] * 5
    d = _direction(1.0, sse_pct_chg=0.1, limit_up=30, limit_down=20)
    score, label = _compute_intensity(indices_low, d, "强势上涨")
    # 低波 + 微涨 → 低强度
    assert label in ("极低", "偏低", "中等")
    assert score < 50


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
    """标题包含象限专属强度词 + 强度分（避免歧义）."""
    report = _build_report("下跌恐慌", intensity=75.0)
    msg = format_alert_message(report)
    header = msg.split("\n")[0]
    assert "75/100" in header
    assert "恐慌强度" in header, f"标题应含'恐慌强度'主语，实际：{header}"


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


@pytest.mark.parametrize(
    "quadrant, intensity_word",
    [
        ("下跌恐慌", "恐慌强度"),
        ("逼空过热", "逼空强度"),
        ("阴跌预警", "阴跌强度"),
        ("强势上涨", "上涨强度"),
    ],
)
def test_format_message_intensity_word_varies(quadrant, intensity_word):
    """每个象限的强度都有专属主语词（避免'强度'歧义）."""
    report = _build_report(quadrant)
    msg = format_alert_message(report)
    header = msg.split("\n")[0]
    assert intensity_word in header, f"{quadrant} 标题应含'{intensity_word}'，实际：{header}"


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
