"""price_estimator 单元测试 — 目标价反推 + 技术止损.

重点测 domain 分流（normal PE / 周期股 PB）、异常保护、数据不足。
"""

from __future__ import annotations

from datetime import date

import pytest

from davis_analyzer.price_estimator import (
    estimate_target_price,
    estimate_technical_stop,
    _has_near_zero_eps,
)
from davis_analyzer.types import ValuationData


def _vd(pe: float | None, pb: float | None, trade_date: str = "20260101") -> ValuationData:
    """构造 ValuationData（pe/pb 可 None）."""
    return ValuationData(
        ts_code="603629.SH", trade_date=trade_date,
        pe_ttm=pe, pb=pb, ps=0.0, total_mv=0.0,
    )


def _make_history(current_pe: float, current_pb: float, median_pe: float, median_pb: float,
                  n: int = 30) -> list[ValuationData]:
    """构造 history：[0] 为最新（current），其余围绕 median 波动."""
    hist = [_vd(current_pe, current_pb, "20260714")]
    for i in range(1, n):
        # 围绕 median 的小波动，保证 median ≈ 指定值
        pe = median_pe * (1 + (i % 5 - 2) * 0.01)
        pb = median_pb * (1 + (i % 5 - 2) * 0.01)
        hist.append(_vd(pe, pb, f"2026{1 + i:05d}"))
    return hist


# ===================================================================
# estimate_target_price — domain 分流
# ===================================================================


class TestEstimateTargetPrice:
    def test_normal_domain_uses_pe(self):
        """normal 域用 PE 反推：current 100, median 150 → target 150."""
        hist = _make_history(current_pe=20, current_pb=3, median_pe=30, median_pb=4)
        # current_price=100, median_pe/current_pe_metric = 30/20 = 1.5 → target 150
        target, method = estimate_target_price(hist, 100.0, "normal")
        assert method == "pe_median"
        assert target == 150.0

    def test_cyclical_domain_uses_pb(self):
        """classic_cyclical 强制 PB：PE 被忽略."""
        # PE ratio 异常大（周期股盈利底），但 PB 正常
        hist = _make_history(current_pe=500, current_pb=2, median_pe=100, median_pb=3)
        target, method = estimate_target_price(hist, 100.0, "classic_cyclical")
        assert method == "pb_median"
        # median_pb/current_pb = 3/2 = 1.5 → target 150
        assert target == 150.0

    def test_super_cycle_uses_pb(self):
        """super_cycle 也用 PB."""
        hist = _make_history(current_pe=50, current_pb=2, median_pe=80, median_pb=3)
        target, method = estimate_target_price(hist, 100.0, "super_cycle")
        assert method == "pb_median"
        assert target == 150.0

    def test_negative_eps_forces_pb(self):
        """normal 域但有近零 EPS → 强制 PB."""
        # 最新 PE 近零（EPS 巨大），PE 反推会失真
        hist = _make_history(current_pe=0.005, current_pb=2, median_pe=0.005, median_pb=3)
        target, method = estimate_target_price(hist, 100.0, "normal")
        assert method == "pb_median"


# ===================================================================
# estimate_target_price — 异常保护
# ===================================================================


class TestTargetPriceGuards:
    def test_ratio_too_high_skipped(self):
        """ratio > 2.0 跳过（PE 反推离谱）."""
        # current_pe=5, median_pe=50 → ratio=10，远超 2.0
        hist = _make_history(current_pe=5, current_pb=2, median_pe=50, median_pb=3)
        target, method = estimate_target_price(hist, 100.0, "normal")
        assert target is None
        assert method == "skip"

    def test_ratio_too_low_skipped(self):
        """ratio < 0.8 跳过（median < current，目标价比现价还低）."""
        # current_pe=100, median_pe=50 → ratio=0.5 < 0.8
        hist = _make_history(current_pe=100, current_pb=2, median_pe=50, median_pb=3)
        target, method = estimate_target_price(hist, 100.0, "normal")
        assert target is None
        assert method == "skip"

    def test_empty_history_skipped(self):
        target, method = estimate_target_price([], 100.0, "normal")
        assert target is None
        assert method == "skip"

    def test_none_current_price_skipped(self):
        hist = _make_history(20, 3, 30, 4)
        target, method = estimate_target_price(hist, None, "normal")
        assert target is None
        assert method == "skip"

    def test_insufficient_samples_skipped(self):
        """样本 < 10 → median 不可靠，跳过."""
        hist = _make_history(20, 3, 30, 4, n=5)
        target, method = estimate_target_price(hist, 100.0, "normal")
        assert target is None
        assert method == "skip"

    def test_zero_current_metric_skipped(self):
        """current metric 为 0 → 除零保护（转 PB 后 PB 也为 0 才 skip）."""
        # PE=0 触发近零 → 转 PB；PB 也为 0 → PB 路径也 skip
        hist = [_vd(0.0, 0.0)] + [_vd(30, 4) for _ in range(20)]
        target, method = estimate_target_price(hist, 100.0, "normal")
        assert target is None
        assert method == "skip"


# ===================================================================
# _has_near_zero_eps
# ===================================================================


class TestHasNearZeroEps:
    def test_normal_pe_series(self):
        assert _has_near_zero_eps([20, 25, 30, 22]) is False

    def test_near_zero_pe_detected(self):
        assert _has_near_zero_eps([20, 0.005, 30]) is True

    def test_negative_pe_detected(self):
        assert _has_near_zero_eps([20, -5, 30]) is True

    def test_none_pe_ignored(self):
        assert _has_near_zero_eps([20, None, 30]) is False


# ===================================================================
# estimate_technical_stop — mock client
# ===================================================================


class TestEstimateTechnicalStop:
    def test_insufficient_data_returns_none(self, monkeypatch):
        """<20 行数据返回 None."""
        import pandas as pd

        class FakeClient:
            def get_daily_prices(self, ts_code, start, end):
                return pd.DataFrame({
                    "trade_date": ["20260701", "20260702"],
                    "close": [10.0, 10.5],
                    "adj_factor": [1.0, 1.0],
                })

        result = estimate_technical_stop(FakeClient(), "603629.SH", date(2026, 7, 14))
        assert result is None

    def test_normal_case_returns_stop(self):
        """足够数据 → 返回止损价（max(MA20, low) × 0.98）."""
        import pandas as pd

        # 构造 25 行，close 在 10-11 波动，low ≈ 10
        closes = [10.0 + (i % 3) * 0.3 for i in range(25)]  # 10.0, 10.3, 10.6 循环
        lows = [c - 0.1 for c in closes]
        class FakeClient:
            def get_daily_prices(self, ts_code, start, end):
                return pd.DataFrame({
                    "trade_date": [f"202607{i+1:02d}" for i in range(25)],
                    "close": closes,
                    "adj_factor": [1.0] * 25,
                })

        result = estimate_technical_stop(FakeClient(), "603629.SH", date(2026, 7, 31))
        assert result is not None
        # MA20 ≈ mean(closes[-20:]) ≈ 10.3, recent_low = min(closes[-20:]) = 10.0
        # trailing = max(10.3, 10.0) × 0.98 ≈ 10.09
        assert 9.5 < result < 11.0

    def test_api_error_returns_none(self):
        """get_daily_prices 抛异常 → 返回 None."""
        class FakeClient:
            def get_daily_prices(self, *args, **kwargs):
                raise RuntimeError("API error")

        result = estimate_technical_stop(FakeClient(), "603629.SH", date(2026, 7, 14))
        assert result is None

    def test_future_data_excluded(self):
        """只用 as_of 及之前的数据（防未来函数）."""
        import pandas as pd

        closes = [10.0 + i * 0.1 for i in range(30)]  # 递增到 12.9
        class FakeClient:
            def get_daily_prices(self, ts_code, start, end):
                return pd.DataFrame({
                    "trade_date": [f"202607{i+1:02d}" for i in range(30)],
                    "close": closes,
                    "adj_factor": [1.0] * 30,
                })

        # as_of = 0720，应只用 0720 及之前的数据（20 行）
        result = estimate_technical_stop(FakeClient(), "603629.SH", date(2026, 7, 20))
        assert result is not None
        # 不应该用到 0721+ 的更高价
