"""Price estimation for stock candidates — 目标价反推 + 技术止损.

Two pure-ish functions used by the screening pipeline to attach actionable
price levels (target price, technical stop-loss) to each top-20 candidate.

目标价：估值历史分位反推（回到中位对应价位）
  - normal 域: current_price × (median_pe / current_pe)
  - classic_cyclical / super_cycle / 负EPS: current_price × (median_pb / current_pb)
    （周期股盈利底 PE 失真，必须用 PB 锚）

技术止损：trailing_stop（趋势跟随）
  - max(MA20, 近20日最低) × 0.98
  - 与 sell_monitor.check_trailing_stop 的判定价一致
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

from davis_analyzer.constants import EPS_NEAR_ZERO_THRESHOLD
from davis_analyzer.types import ValuationData

# 目标价 ratio 合理区间（防异常值）
_TARGET_RATIO_MIN = 0.8  # 目标价不低于现价 80%（否则"看跌"无意义）
_TARGET_RATIO_MAX = 2.0  # 目标价不超过现价 2 倍（防周期股 PE 反推离谱）
# 技术止损回看窗口
_STOP_LOOKBACK_DAYS = 90  # 拉取日线天数（MA20 需 20 日，留 buffer）
_STOP_MA_WINDOW = 20
_STOP_LOW_WINDOW = 20
_STOP_BUFFER = 0.02  # 2% 缓冲


def estimate_target_price(
    history: list[ValuationData],
    current_price: float | None,
    domain: str,
) -> tuple[float | None, str]:
    """估值反推目标价，返回 (target_price, method).

    Args:
        history: 估值历史（ValuationData 列表，按 trade_date DESC，最新在 [0]）
        current_price: 当前价（最新收盘）；None 则无法反推
        domain: classify_stock 返回值（normal/classic_cyclical/super_cycle）

    Returns:
        (target_price, method)：
        - method ∈ {'pe_median', 'pb_median', 'skip'}
        - 数据不足/ratio 异常 → (None, 'skip')
    """
    if not history or current_price is None or current_price <= 0:
        return None, "skip"

    latest = history[0]
    pe_series = [v.pe_ttm for v in history if v.pe_ttm is not None]
    pb_series = [v.pb for v in history if v.pb is not None]

    # 周期股 / super_cycle / 负 EPS → 强制 PB 反推
    use_pb = domain in ("classic_cyclical", "super_cycle") or _has_near_zero_eps(pe_series)

    if use_pb:
        return _ratio_target(current_price, latest.pb, pb_series, "pb_median")
    return _ratio_target(current_price, latest.pe_ttm, pe_series, "pe_median")


def _ratio_target(
    current_price: float,
    current_metric: float | None,
    series: list[float],
    method: str,
) -> tuple[float | None, str]:
    """通用 ratio 反推：target = current_price × (median / current_metric)."""
    if not series or current_metric is None or current_metric <= 0:
        return None, "skip"
    if len(series) < 10:  # 样本太少，median 不可靠
        return None, "skip"

    median_metric = statistics.median(series)
    if median_metric <= 0:
        return None, "skip"

    ratio = median_metric / current_metric
    # 异常保护：ratio 超出合理区间说明估值结构异常，反推不可靠
    if ratio < _TARGET_RATIO_MIN or ratio > _TARGET_RATIO_MAX:
        return None, "skip"

    target = round(current_price * ratio, 2)
    return target, method


def estimate_technical_stop(
    client,
    ts_code: str,
    as_of: date,
) -> float | None:
    """trailing_stop 技术止损：max(MA20, 近20日最低) × 0.98.

    用未复权 close（真实交易价）计算。不用 close×adj_factor——那是绝对前复权，
    对长期分红股会失真几倍，且与现价（未复权）不可比。
    短期 20 日窗口内未复权价准确（除非正好有除权日，概率低）。
    数据来自 daily_price 缓存（选股流程的 momentum 已预热），零额外 API。

    Args:
        client: TushareClient（用 get_daily_prices 读缓存）
        ts_code: 股票代码（带后缀，如 603629.SH）
        as_of: 截止日期

    Returns:
        stop_loss_technical 价格，或 None（数据不足）
    """
    end_date = as_of.strftime("%Y%m%d")
    start_date = (as_of - timedelta(days=_STOP_LOOKBACK_DAYS + 60)).strftime("%Y%m%d")

    try:
        df = client.get_daily_prices(ts_code, start_date, end_date)
    except Exception:
        return None

    if df is None or df.empty:
        return None

    # 截止 as_of 及之前的数据（防止用到未来数据）
    df = df[df["trade_date"] <= end_date].sort_values("trade_date")
    if len(df) < _STOP_MA_WINDOW:
        return None

    # 统一用未复权 close（与现价同基准，可正确比较）
    closes = df["close"].astype(float)
    latest_close = float(closes.iloc[-1])
    ma20 = closes.tail(_STOP_MA_WINDOW).mean()
    recent_low = float(closes.tail(_STOP_LOW_WINDOW).min())

    # 下跌趋势保护：当 MA20 > 现价时（股票急跌破均线），max(MA20, low) 会远高于
    # 现价，止损位无意义（等于"早就该止损"）。此时改用近期低点（更低、更现实），
    # 避免给出高于现价的止损位。正常趋势（MA20 < 现价）用 max(MA20, low)。
    if ma20 > latest_close:
        trailing_stop = recent_low * (1 - _STOP_BUFFER)
    else:
        trailing_stop = max(ma20, recent_low) * (1 - _STOP_BUFFER)
    return round(trailing_stop, 2)


def _has_near_zero_eps(pe_series: list[float | None]) -> bool:
    """是否有近零/负 PE（EPS_NEAR_ZERO_THRESHOLD 已覆盖负值）."""
    for pe in pe_series:
        if pe is not None and pe < EPS_NEAR_ZERO_THRESHOLD:
            return True
    return False
