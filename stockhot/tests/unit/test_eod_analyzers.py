"""eod_review 分析层单测 — 验证板块聚合/涨停归因/情绪温度计/箱体突破的纯逻辑.

所有测试用构造的 DataFrame/dict 驱动，不依赖 Tushare 网络。
"""

from __future__ import annotations

import pandas as pd
import pytest

from stockhot.eod_review.analyzers import (
    ATTR_BREAKOUT,
    ATTR_EVENT,
    ATTR_RELAY,
    ATTR_VALUE_REPAIR,
    ATTR_VOLUME_FUND,
    aggregate_sector_performance,
    attribute_limit_up,
    compute_n_day_trend,
    compute_sentiment_thermometer,
    _check_box_breakout,
    _compute_block_discounts,
    _detect_divergence,
    _score_to_label,
)
from stockhot.eod_review.data_layer import MarketSnapshot


# ── 辅助构造函数 ──────────────────────────────────────────────────────


def _make_snapshot(
    *,
    daily_with_industry: pd.DataFrame | None = None,
    daily: pd.DataFrame | None = None,
    limit_up: list[dict] | None = None,
    limit_down: list[dict] | None = None,
    broken: list[dict] | None = None,
    moneyflow_sector: list[dict] | None = None,
    dragon_tiger: list[dict] | None = None,
    daily_basic: pd.DataFrame | None = None,
    north_flow: pd.DataFrame | None = None,
    margin: pd.DataFrame | None = None,
    block_trade: pd.DataFrame | None = None,
) -> MarketSnapshot:
    return MarketSnapshot(
        trade_date="20260101",
        daily=daily if daily is not None else pd.DataFrame(),
        daily_with_industry=daily_with_industry if daily_with_industry is not None else pd.DataFrame(),
        daily_basic=daily_basic if daily_basic is not None else pd.DataFrame(),
        limit_up=limit_up if limit_up is not None else [],
        broken=broken if broken is not None else [],
        limit_down=limit_down if limit_down is not None else [],
        moneyflow_sector=moneyflow_sector if moneyflow_sector is not None else [],
        dragon_tiger=dragon_tiger if dragon_tiger is not None else [],
        north_flow=north_flow if north_flow is not None else pd.DataFrame(),
        margin=margin if margin is not None else pd.DataFrame(),
        block_trade=block_trade if block_trade is not None else pd.DataFrame(),
    )


def _make_daily_with_industry(rows: list[dict]) -> pd.DataFrame:
    """构造 daily_with_industry DataFrame.

    rows: [{ts_code, name, industry, pct_chg}, ...]
    """
    return pd.DataFrame(rows)


def _make_history(
    closes: list[float],
    *,
    vols: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> pd.DataFrame:
    """构造历史日线 DataFrame."""
    n = len(closes)
    vols = vols or [1000.0] * n
    highs = highs or closes
    lows = lows or closes
    dates = [f"2026010{i:02d}" for i in range(n)]
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * n,
            "trade_date": dates,
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "vol": vols,
        }
    )


# ═══════════════════════════════════════════════════════════════════════
# 模块 A：板块聚合
# ═══════════════════════════════════════════════════════════════════════


class TestAggregateSectorPerformance:
    def test_empty_snapshot_returns_empty(self):
        snap = _make_snapshot()
        assert aggregate_sector_performance(snap) == []

    def test_basic_aggregation(self):
        dwi = _make_daily_with_industry(
            [
                {"ts_code": "001.SZ", "name": "A", "industry": "半导体", "pct_chg": 5.0},
                {"ts_code": "002.SZ", "name": "B", "industry": "半导体", "pct_chg": 3.0},
                {"ts_code": "003.SZ", "name": "C", "industry": "半导体", "pct_chg": 7.0},
                {"ts_code": "004.SZ", "name": "D", "industry": "白酒", "pct_chg": 2.0},
                {"ts_code": "005.SZ", "name": "E", "industry": "白酒", "pct_chg": 4.0},
                {"ts_code": "006.SZ", "name": "F", "industry": "白酒", "pct_chg": 6.0},
            ]
        )
        snap = _make_snapshot(daily_with_industry=dwi)
        result = aggregate_sector_performance(snap)

        assert len(result) == 2
        # 半导体均涨幅 5.0% > 白酒 4.0%，应排第一
        assert result[0].name == "半导体"
        assert result[0].mean_pct == pytest.approx(5.0)
        assert result[0].member_count == 3
        assert result[1].name == "白酒"
        assert result[1].mean_pct == pytest.approx(4.0)

    def test_limit_up_down_counting(self):
        dwi = _make_daily_with_industry(
            [
                {"ts_code": "001.SZ", "name": "A", "industry": "半导体", "pct_chg": 10.0},
                {"ts_code": "002.SZ", "name": "B", "industry": "半导体", "pct_chg": -10.0},
                {"ts_code": "003.SZ", "name": "C", "industry": "半导体", "pct_chg": 5.0},
            ]
        )
        snap = _make_snapshot(
            daily_with_industry=dwi,
            limit_up=[{"code": "001.SZ"}],
            limit_down=[{"code": "002.SZ"}],
        )
        result = aggregate_sector_performance(snap)
        assert result[0].limit_up_count == 1
        assert result[0].limit_down_count == 1

    def test_small_sector_filtered(self):
        """成分股 < 3 的板块应被过滤."""
        dwi = _make_daily_with_industry(
            [
                {"ts_code": "001.SZ", "name": "A", "industry": "小板块", "pct_chg": 10.0},
                {"ts_code": "002.SZ", "name": "B", "industry": "大板块", "pct_chg": 5.0},
                {"ts_code": "003.SZ", "name": "C", "industry": "大板块", "pct_chg": 3.0},
                {"ts_code": "004.SZ", "name": "D", "industry": "大板块", "pct_chg": 4.0},
            ]
        )
        snap = _make_snapshot(daily_with_industry=dwi)
        result = aggregate_sector_performance(snap)
        assert len(result) == 1
        assert result[0].name == "大板块"


# ═══════════════════════════════════════════════════════════════════════
# 模块 B：涨停归因
# ═══════════════════════════════════════════════════════════════════════


class TestAttributeLimitUp:
    def test_relay_attribution_for_consecutive_boards(self):
        """连板数 >= 2 应归因为连板接力."""
        limit_up = [
            {
                "code": "001.SZ",
                "name": "连板股",
                "sector": "半导体",
                "consecutive_boards": 3,
                "turnover_rate": 10.0,
                "seal_amount": 5e8,
                "change_pct": 10.0,
            }
        ]
        snap = _make_snapshot(limit_up=limit_up)
        result = attribute_limit_up(snap, max_history_fetch=0)
        assert len(result) == 1
        assert result[0].attribution_type == ATTR_RELAY
        assert result[0].confidence > 0.5

    def test_volume_fund_attribution_with_dragon_tiger(self):
        """龙虎榜净买入 > 5000万 + 非连板 → 放量资金推动."""
        limit_up = [
            {
                "code": "001.SZ",
                "name": "资金股",
                "sector": "医药",
                "consecutive_boards": 1,
                "turnover_rate": 8.0,
                "seal_amount": 3e8,
                "change_pct": 10.0,
            }
        ]
        dragon_tiger = [{"code": "001.SZ", "net_buy_amount": 8000.0}]
        snap = _make_snapshot(limit_up=limit_up, dragon_tiger=dragon_tiger)
        result = attribute_limit_up(snap, max_history_fetch=0)
        assert result[0].attribution_type == ATTR_VOLUME_FUND

    def test_event_fallback(self):
        """无任何量化特征的事件驱动兜底."""
        limit_up = [
            {
                "code": "001.SZ",
                "name": "事件股",
                "sector": "概念",
                "consecutive_boards": 1,
                "turnover_rate": 5.0,
                "seal_amount": 1e8,
                "change_pct": 10.0,
            }
        ]
        snap = _make_snapshot(limit_up=limit_up)
        result = attribute_limit_up(snap, max_history_fetch=0)
        assert result[0].attribution_type == ATTR_EVENT
        assert result[0].confidence == pytest.approx(0.3)

    def test_empty_limit_up(self):
        snap = _make_snapshot()
        assert attribute_limit_up(snap) == []


class TestCheckBoxBreakout:
    def test_clear_breakout(self):
        """收盘突破60日箱体上沿 + 量比>2 + 振幅<45% → 突破."""
        # 60日箱体在 10-12 区间，今日 13 放量突破
        closes = [11.0] * 60 + [13.0]
        highs = [12.0] * 60 + [13.0]
        lows = [10.0] * 60 + [12.5]
        vols = [1000.0] * 60 + [5000.0]  # 量比 5x
        hist = _make_history(closes, vols=vols, highs=highs, lows=lows)
        result = _check_box_breakout(hist, {})
        assert result["is_breakout"] is True
        assert result["confidence"] > 0.6
        assert result["vol_ratio"] >= 2.0

    def test_no_breakout_below_box(self):
        """收盘未突破上沿 → 非突破."""
        closes = [11.0] * 60 + [11.5]
        highs = [12.0] * 60 + [12.0]
        lows = [10.0] * 60 + [11.0]
        hist = _make_history(closes, highs=highs, lows=lows)
        result = _check_box_breakout(hist, {})
        assert result["is_breakout"] is False

    def test_low_volume_not_breakout(self):
        """突破上沿但量比不足 → 非突破."""
        closes = [11.0] * 60 + [13.0]
        highs = [12.0] * 60 + [13.0]
        lows = [10.0] * 60 + [12.5]
        vols = [1000.0] * 60 + [1200.0]  # 量比 1.2x，不足
        hist = _make_history(closes, vols=vols, highs=highs, lows=lows)
        result = _check_box_breakout(hist, {})
        assert result["is_breakout"] is False

    def test_insufficient_history(self):
        """历史不足 61 天 → 非突破."""
        hist = _make_history([10.0, 11.0, 12.0])
        result = _check_box_breakout(hist, {})
        assert result["is_breakout"] is False


# ═══════════════════════════════════════════════════════════════════════
# 模块 C：情绪温度计
# ═══════════════════════════════════════════════════════════════════════


class TestSentimentThermometer:
    def test_hot_market(self):
        """涨停多+融资高+北向流入 → 高分（偏热/极热）."""
        snap = _make_snapshot(
            limit_up=[{}] * 80,
            limit_down=[{}] * 10,
            broken=[{}] * 5,
            margin=pd.DataFrame({"rzye": [2e12, 1.5e12]}),  # 融资余额高
            north_flow=pd.DataFrame({"north_money": [500000.0]}),  # +50亿
            block_trade=pd.DataFrame(
                {"ts_code": ["001.SZ"], "price": [10.0]}
            ),
            daily=pd.DataFrame({"ts_code": ["001.SZ"], "close": [10.0]}),
        )
        result = compute_sentiment_thermometer(snap)
        assert result is not None
        assert result.score > 60
        assert result.label in ("偏热", "极热")

    def test_cold_market(self):
        """跌停多+北向大幅流出 → 低分（偏冷/极冷）."""
        snap = _make_snapshot(
            limit_up=[{}] * 10,
            limit_down=[{}] * 80,
            broken=[{}] * 5,
            north_flow=pd.DataFrame({"north_money": [-200000.0]}),  # -20亿
            margin=pd.DataFrame({"rzye": [1.2e12]}),  # 融资余额低
        )
        result = compute_sentiment_thermometer(snap)
        assert result is not None
        assert result.score < 40

    def test_all_empty_returns_none(self):
        snap = _make_snapshot()
        assert compute_sentiment_thermometer(snap) is None

    def test_divergence_detection(self):
        """北向流出 + 融资加杠杆 → 检测到背离."""
        components = {"north": 30.0, "margin": 70.0, "limit": 50.0}
        div = _detect_divergence(components, {})
        assert div is not None
        assert "分歧" in div


class TestBlockDiscount:
    def test_discount_calculation(self):
        snap = _make_snapshot(
            daily=pd.DataFrame(
                {"ts_code": ["001.SZ", "002.SZ"], "close": [10.0, 20.0]}
            ),
            block_trade=pd.DataFrame(
                {
                    "ts_code": ["001.SZ", "002.SZ"],
                    "price": [9.5, 20.5],  # -5% 折价, +2.5% 溢价
                }
            ),
        )
        discounts = _compute_block_discounts(snap)
        assert len(discounts) == 2
        assert discounts[0] == pytest.approx(-5.0, abs=0.1)
        assert discounts[1] == pytest.approx(2.5, abs=0.1)

    def test_missing_close_skipped(self):
        snap = _make_snapshot(
            daily=pd.DataFrame({"ts_code": ["001.SZ"], "close": [10.0]}),
            block_trade=pd.DataFrame(
                {"ts_code": ["001.SZ", "999.SZ"], "price": [9.0, 5.0]}
            ),
        )
        discounts = _compute_block_discounts(snap)
        assert len(discounts) == 1  # 999.SZ 无 close 被跳过


class TestScoreToLabel:
    @pytest.mark.parametrize(
        "score,label",
        [
            (85, "极热"),
            (70, "偏热"),
            (50, "中性"),
            (30, "偏冷"),
            (10, "极冷"),
            (80, "极热"),
            (60, "偏热"),
            (40, "中性"),
            (20, "偏冷"),
        ],
    )
    def test_label_mapping(self, score, label):
        assert _score_to_label(score) == label


# ═══════════════════════════════════════════════════════════════════════
# 模块 D：N 日趋势
# ═══════════════════════════════════════════════════════════════════════


class TestNDayTrend:
    def test_first_run_no_history(self):
        """首次运行（无历史 eod_sentiment）应 has_history=False."""
        snap = _make_snapshot(
            limit_up=[{"consecutive_boards": 2}, {"consecutive_boards": 1}] * 10,
        )
        result = compute_n_day_trend("20260101", snap)
        assert result.has_history is False
        assert result.today_limit_up == 20
        assert result.height_distribution.get(2) == 10

    def test_height_distribution(self):
        snap = _make_snapshot(
            limit_up=[
                {"consecutive_boards": 5},
                {"consecutive_boards": 3},
                {"consecutive_boards": 3},
                {"consecutive_boards": 2},
                {"consecutive_boards": 1},  # 1板不计入梯队
                {"consecutive_boards": 1},
            ],
        )
        result = compute_n_day_trend("20260101", snap)
        assert result.height_distribution.get(5) == 1
        assert result.height_distribution.get(3) == 2
        assert result.height_distribution.get(2) == 1
        assert 1 not in result.height_distribution  # 1板不计入
