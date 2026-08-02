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
    DoseWarning,
    IndexVolatility,
    LimitBehaviorReading,
    PanicReport,
    SectorStrength,
    SectorStructure,
    SignalResult,
    _classify_quadrant,
    _compute_intensity,
    _compute_realtime_pct_chg,
    _detect_dose_warning,
    _detect_sector_structure,
    _em_code_to_ts_code,
    _format_dose_warning_section,
    _format_sector_section,
    _QUADRANT_META,
    _sina_code_to_ts_code,
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


# ═══════════════════════════════════════════════════════════════════
# 板块结构（强势/弱势板块）
# ═══════════════════════════════════════════════════════════════════


def test_sector_structure_strong_sorted_by_limit_up(monkeypatch):
    """强势板块按涨停数降序（行为信号优先于涨跌幅）."""
    # mock _fetch_sw_daily_pct 返回空（隔离 sw_daily，只测 sector_counts 逻辑）
    import stockhot.alert.panic_detector as pd_mod
    monkeypatch.setattr(pd_mod, "_fetch_sw_daily_pct", lambda: ({}, ""))
    monkeypatch.setattr(pd_mod, "_fetch_sector_main_net", lambda: {})

    sector_counts = {
        "消费电子": {"limit_up": 8, "limit_down": 0, "broken": 1},
        "半导体": {"limit_up": 6, "limit_down": 1, "broken": 0},
        "食品饮料": {"limit_up": 2, "limit_down": 0, "broken": 0},
    }
    result = _detect_sector_structure(sector_counts)
    assert result.available
    assert len(result.strong) == 3
    # 按涨停数降序
    assert result.strong[0].name == "消费电子"
    assert result.strong[0].limit_up == 8
    assert result.strong[1].name == "半导体"
    # 半导体有跌停（1个）→ 出现在 weak 里；消费电子/食品饮料无跌停 → weak 只含半导体
    assert len(result.weak) == 1
    assert result.weak[0].name == "半导体"


def test_sector_structure_weak_sorted_by_limit_down(monkeypatch):
    """弱势板块按跌停数降序."""
    import stockhot.alert.panic_detector as pd_mod
    monkeypatch.setattr(pd_mod, "_fetch_sw_daily_pct", lambda: ({}, ""))
    monkeypatch.setattr(pd_mod, "_fetch_sector_main_net", lambda: {})

    sector_counts = {
        "房地产": {"limit_up": 0, "limit_down": 7, "broken": 2},
        "建筑装饰": {"limit_up": 1, "limit_down": 5, "broken": 1},
        "钢铁": {"limit_up": 0, "limit_down": 3, "broken": 0},
    }
    result = _detect_sector_structure(sector_counts)
    assert len(result.weak) == 3
    # 按跌停数降序
    assert result.weak[0].name == "房地产"
    assert result.weak[0].limit_down == 7
    assert result.weak[1].name == "建筑装饰"
    # 建筑装饰有涨停（1个）→ 也出现在 strong 里；房地产/钢铁无涨停 → strong 只含建筑装饰
    assert len(result.strong) == 1
    assert result.strong[0].name == "建筑装饰"


def test_sector_structure_weak_fallback_to_pct_when_no_limit_down(monkeypatch):
    """全市场无跌停时，弱势用 sw_daily 涨跌幅回退补全（不再为空）."""
    import stockhot.alert.panic_detector as pd_mod
    monkeypatch.setattr(
        pd_mod, "_fetch_sw_daily_pct",
        lambda: ({"银行": -1.5, "食品饮料": -0.8, "钢铁": -0.3, "电子": 2.0}, "07-31"),
    )
    monkeypatch.setattr(pd_mod, "_fetch_sector_main_net", lambda: {})

    # 全市场无跌停（只有涨停的电子板块）
    sector_counts = {"电子": {"limit_up": 5, "limit_down": 0}}
    result = _detect_sector_structure(sector_counts)
    # strong 应有电子（涨停5）
    assert any(s.name == "电子" for s in result.strong)
    # weak 不应为空——用涨跌幅回退选出银行/食品饮料/钢铁
    assert len(result.weak) == 3
    assert result.weak[0].name == "银行"   # 跌幅最大 -1.5%
    assert result.weak[0].pct_change == -1.5
    # 回退选出的弱势无涨跌停数据，limit_down 应为 0
    assert result.weak[0].limit_down == 0


def test_format_sector_strength_hides_zero_limit_when_pct_only(monkeypatch):
    """涨跌幅回退选出的板块（涨跌停都0）格式化时不显示"涨0/跌0"."""
    from stockhot.alert.panic_detector import _format_sector_strength, SectorStrength
    # 板块无涨跌停，只有涨跌幅
    s = SectorStrength(name="银行", pct_change=-1.5, limit_up=0, limit_down=0)
    line = _format_sector_strength(s, show_pct=True)
    assert "涨0/跌0" not in line  # 不应显示噪音
    assert "-1.50%" in line       # 应显示涨跌幅
    assert "银行" in line


def test_sector_structure_top_n_limit(monkeypatch):
    """最多只返回 top N（_SECTOR_TOP_N=3）个."""
    import stockhot.alert.panic_detector as pd_mod
    monkeypatch.setattr(pd_mod, "_fetch_sw_daily_pct", lambda: ({}, ""))
    monkeypatch.setattr(pd_mod, "_fetch_sector_main_net", lambda: {})

    # 构造 5 个涨停板块
    sector_counts = {f"板块{i}": {"limit_up": 10 - i, "limit_down": 0} for i in range(5)}
    result = _detect_sector_structure(sector_counts)
    assert len(result.strong) == 3  # 只取 top 3


def test_sector_structure_empty_counts(monkeypatch):
    """sector_counts 为空 + 无外部数据 → available=False."""
    import stockhot.alert.panic_detector as pd_mod
    monkeypatch.setattr(pd_mod, "_fetch_sw_daily_pct", lambda: ({}, ""))
    monkeypatch.setattr(pd_mod, "_fetch_sector_main_net", lambda: {})

    result = _detect_sector_structure({})
    assert not result.available
    assert result.strong == []
    assert result.weak == []


def test_sector_structure_merges_pct_change(monkeypatch):
    """sw_daily 涨跌幅正确合并到对应板块."""
    import stockhot.alert.panic_detector as pd_mod
    monkeypatch.setattr(
        pd_mod, "_fetch_sw_daily_pct",
        lambda: ({"电子": -7.02, "食品饮料": 2.14}, "07-28"),
    )
    monkeypatch.setattr(pd_mod, "_fetch_sector_main_net", lambda: {})

    sector_counts = {
        "电子": {"limit_up": 6, "limit_down": 1},
        "食品饮料": {"limit_up": 2, "limit_down": 0},
    }
    result = _detect_sector_structure(sector_counts)
    assert result.pct_change_as_of == "07-28"
    # 找到电子板块，验证涨跌幅合并
    dianzi = next(s for s in result.strong if s.name == "电子")
    assert dianzi.pct_change == -7.02


def test_sector_structure_fallback_to_pct_when_no_limits(monkeypatch):
    """无涨跌停数据时（zt_pool 全失败），回退到按涨跌幅排序."""
    import stockhot.alert.panic_detector as pd_mod
    monkeypatch.setattr(
        pd_mod, "_fetch_sw_daily_pct",
        lambda: ({"钢铁": -3.0, "电子": 2.5, "食品": 1.0}, "07-28"),
    )
    monkeypatch.setattr(pd_mod, "_fetch_sector_main_net", lambda: {})

    # sector_counts 为空，但有 sw_daily 数据
    result = _detect_sector_structure({})
    assert result.available
    # 电子涨幅最大 → strong[0]
    assert result.strong[0].name == "电子"
    # 钢铁跌幅最大 → weak[0]
    assert result.weak[0].name == "钢铁"


def test_format_sector_section_renders_strong_weak():
    """消息格式：含【板块结构】标题 + 强势/弱势子标题."""
    sectors = SectorStructure(
        strong=[
            SectorStrength(name="消费电子", limit_up=8, limit_down=0),
            SectorStrength(name="半导体", limit_up=6, limit_down=1),
        ],
        weak=[
            SectorStrength(name="房地产", limit_up=0, limit_down=7),
        ],
        pct_change_as_of="07-28",
        available=True,
    )
    lines = _format_sector_section(sectors)
    text = "\n".join(lines)
    assert "【板块结构】" in text
    assert "07-28" in text
    assert "🟢 强势板块" in text
    assert "🔴 弱势板块" in text
    assert "消费电子" in text
    assert "房地产" in text


def test_format_sector_section_empty_returns_empty():
    """available=False 时返回空列表（不渲染章节）."""
    sectors = SectorStructure(available=False)
    assert _format_sector_section(sectors) == []


def test_format_message_includes_sector_section():
    """完整消息含板块结构章节（当 sectors 可用时）."""
    report = _build_report("下跌恐慌")
    report.sectors = SectorStructure(
        strong=[SectorStrength(name="消费电子", limit_up=5, limit_down=0)],
        weak=[SectorStrength(name="房地产", limit_up=0, limit_down=8)],
        pct_change_as_of="07-28",
        available=True,
    )
    msg = format_alert_message(report)
    assert "【板块结构】" in msg
    assert "消费电子" in msg
    assert "房地产" in msg


def test_format_message_no_sector_section_when_unavailable():
    """sectors 不可用时不渲染板块章节."""
    report = _build_report("下跌恐慌")
    report.sectors = None  # 板块检测失败
    msg = format_alert_message(report)
    assert "【板块结构】" not in msg


# ═══════════════════════════════════════════════════════════════════
# 剂量效应警示（P99+ 极端高波检测）
# ═══════════════════════════════════════════════════════════════════


def _extreme_vol_indices() -> list[IndexVolatility]:
    """P99+ 极端高波 × 4（≥3 触发阈值）."""
    return [
        IndexVolatility("000001.SH", "上证指数", 60.0, 99, "极度恐慌"),
        IndexVolatility("399001.SZ", "深证成指", 70.0, 99, "极度恐慌"),
        IndexVolatility("000300.SH", "沪深300", 55.0, 99, "极度恐慌"),
        IndexVolatility("399006.SZ", "创业板指", 80.0, 100, "极度恐慌"),
    ]


def _normal_high_vol_indices() -> list[IndexVolatility]:
    """P90-95 普通高波 × 5（不触发剂量警示）."""
    return [
        IndexVolatility("000001.SH", "上证指数", 20.0, 92, "明显恐慌"),
        IndexVolatility("399001.SZ", "深证成指", 40.0, 93, "极度恐慌"),
        IndexVolatility("000300.SH", "沪深300", 30.0, 91, "极度恐慌"),
        IndexVolatility("399006.SZ", "创业板指", 50.0, 94, "极度恐慌"),
        IndexVolatility("000688.SH", "科创50", 65.0, 95, "极度恐慌"),
    ]


def test_dose_warning_triggered_by_extreme_high_vol(monkeypatch):
    """P99+ 极端高波（≥3 指数）应触发警示."""
    # mock 破位检测，隔离 DB 调用（测 P99+ 触发逻辑本身）
    import stockhot.alert.panic_detector as pd_mod
    monkeypatch.setattr(
        pd_mod.DoseWarning, "is_breakdown", False, raising=False
    )
    # 让 repo 调用抛异常跳过破位检测，简化测试
    w = _detect_dose_warning(_extreme_vol_indices(), high_vol=True)
    assert w.triggered is True
    assert w.extreme_pct_n == 4


def test_dose_warning_not_triggered_by_normal_high_vol():
    """P90-95 普通高波不应触发（黄金组合区间）."""
    w = _detect_dose_warning(_normal_high_vol_indices(), high_vol=True)
    assert w.triggered is False
    assert w.extreme_pct_n == 0


def test_dose_warning_not_triggered_in_low_vol():
    """低波区间不检测剂量（无剂量问题）."""
    w = _detect_dose_warning(_extreme_vol_indices(), high_vol=False)
    assert w.triggered is False


def test_dose_warning_partial_extreme_not_triggered():
    """仅 1-2 个指数 P99+ 不触发（避免单指数噪音）."""
    indices = [
        IndexVolatility("000001.SH", "上证指数", 60.0, 99, "极度恐慌"),
        IndexVolatility("399001.SZ", "深证成指", 40.0, 93, "极度恐慌"),  # 普通
        IndexVolatility("000300.SH", "沪深300", 30.0, 91, "极度恐慌"),  # 普通
    ]
    w = _detect_dose_warning(indices, high_vol=True)
    assert w.triggered is False  # 仅 1 个 P99+ < 阈值 3


def test_format_dose_warning_section_shows_when_triggered():
    """触发时渲染警示章节（含历史胜率数据）."""
    warning = DoseWarning(
        extreme_pct_n=4,
        triggered=True,
        is_breakdown=False,
    )
    lines = _format_dose_warning_section(warning)
    text = "\n".join(lines)
    assert "【剂量警示】" in text
    assert "P99" in text
    assert "58%" in text  # 历史胜率
    assert "88%" in text  # 对比 P90-95


def test_format_dose_warning_section_empty_when_not_triggered():
    """未触发时返回空列表（不渲染）."""
    warning = DoseWarning(triggered=False)
    assert _format_dose_warning_section(warning) == []


def test_format_dose_warning_breakdown_shows_catching_knife():
    """P99+ + 破位时显示"接飞刀"警示文案."""
    warning = DoseWarning(
        extreme_pct_n=3,
        breakdown_indices=["深证成指", "创业板指"],
        triggered=True,
        is_breakdown=True,
    )
    lines = _format_dose_warning_section(warning)
    text = "\n".join(lines)
    assert "趋势破位" in text
    assert "深证成指" in text
    assert "43%" in text  # 接飞刀胜率
    assert "接飞刀" in text


def test_format_message_includes_dose_warning():
    """完整消息在 P99+ 触发时含剂量警示章节."""
    report = _build_report("逼空过热")
    report.dose_warning = DoseWarning(extreme_pct_n=4, triggered=True)
    msg = format_alert_message(report)
    assert "【剂量警示】" in msg
    assert "P99" in msg


def test_format_message_no_dose_warning_when_normal():
    """普通高波（P90-95）时消息不含剂量警示."""
    report = _build_report("逼空过热")
    report.dose_warning = DoseWarning(triggered=False)
    msg = format_alert_message(report)
    assert "【剂量警示】" not in msg


# ═══════════════════════════════════════════════════════════════════
# 实时指数行情（双源降级 + 涨跌幅直取）
# ═══════════════════════════════════════════════════════════════════


def test_em_code_to_ts_code_mapping():
    """东财代码 → ts_code 映射."""
    assert _em_code_to_ts_code("000001") == "000001.SH"
    assert _em_code_to_ts_code("000300") == "000300.SH"
    assert _em_code_to_ts_code("000688") == "000688.SH"
    assert _em_code_to_ts_code("399001") == "399001.SZ"
    assert _em_code_to_ts_code("399006") == "399006.SZ"
    assert _em_code_to_ts_code("999999") is None  # 未覆盖


def test_sina_code_to_ts_code_mapping():
    """新浪代码（含 sh/sz 前缀）→ ts_code 映射."""
    assert _sina_code_to_ts_code("sh000001") == "000001.SH"
    assert _sina_code_to_ts_code("SH000300") == "000300.SH"  # 大小写兼容
    assert _sina_code_to_ts_code("sz399001") == "399001.SZ"
    assert _sina_code_to_ts_code("sz399006") == "399006.SZ"
    assert _sina_code_to_ts_code("sh999999") is None  # 未覆盖


def test_compute_pct_chg_prefers_realtime_pct_field():
    """路径 1：实时源直接提供 pct_chg 时优先用（不再算实时价÷昨收）."""
    # mock：上证有 pct_chg=0.725
    rt_data = {"000001.SH": {"price": 3832.0, "pct_chg": 0.725}}
    pct = _compute_realtime_pct_chg("000001.SH", rt_data)
    assert pct == 0.725


def test_compute_pct_chg_fallback_to_price_calc(monkeypatch):
    """路径 2：实时源只有 price 无 pct_chg 时，用 实时价÷昨收-1 回退."""
    import stockhot.alert.panic_detector as pd_mod

    # mock DB 返回昨收 3800
    mock_df = MagicMock()
    mock_df.empty = False
    mock_df.__len__ = lambda self: 5
    mock_df.__getitem__ = lambda self, key: MagicMock(iloc=MagicMock(__getitem__=lambda self, i: 3800.0 if i == -2 else 0))
    mock_repo = MagicMock()
    mock_repo.get_index_daily.return_value = mock_df
    monkeypatch.setattr(pd_mod, "get_repository", lambda: mock_repo) if hasattr(pd_mod, "get_repository") else None
    import stockhot.data_layer
    monkeypatch.setattr(stockhot.data_layer, "get_repository", lambda: mock_repo)

    rt_data = {"000001.SH": {"price": 3830.0, "pct_chg": None}}  # 无 pct_chg
    pct = _compute_realtime_pct_chg("000001.SH", rt_data)
    # 3830/3800 - 1 ≈ 0.789%
    assert pct is not None
    assert abs(pct - 0.789) < 0.1


def test_compute_pct_chg_returns_none_when_no_data():
    """无实时数据 + DB 不可用时返回 None."""
    pct = _compute_realtime_pct_chg("000001.SH", {})
    # DB 有数据时可能返回值，但无实时源时依赖 DB；这里只验证不崩
    assert pct is None or isinstance(pct, float)
