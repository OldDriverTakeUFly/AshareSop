"""Index trend stage classifier — 6 阶段趋势识别器。

核心方法论：基于"均线排列 × 价格vs均线 × MACD状态 × RSI位置 × 成交量趋势"
五维组合规则，将指数当前状态归入 6 个互斥阶段之一。

6 阶段定义（互斥，优先级从高到低）：
    ① 主升浪       — 多头排列 + 站上MA20 + MACD红柱放大 + RSI强区 + 放量上涨
    ② 上涨中回调   — 多头但MA5下穿MA10 + 跌破MA5守MA20 + 缩量
    ③ 高位震荡筑顶 — 均线粘合走平 + 顶背离 + 量价背离
    ④ 主跌浪       — 空头排列 + 跌破所有短均线 + MACD绿柱放大 + RSI弱区 + 放量下跌
    ⑤ 下跌中反弹   — 空头但MA5上穿MA10 + 反弹至MA20遇阻 + 缩量反弹
    ⑥ 低位筑底     — 均线走平 + 底背离 + 缩量至地量

判定优先级（互斥）：先判主升/主跌（最明确趋势）→ 再判筑顶/筑底（背离信号）
→ 最后判回调/反弹（中继形态）。

置信度：满足条件数/总条件数 × 100。<60 标注"阶段不明确"。

与 composite_technical_score 的关系：
    - composite_technical_score 输出 0-100 连续分 + 强势/震荡/弱势三态（量化感）
    - classify_stage 输出 6 选 1 离散阶段 + 盘前预期（定性判断）
    - 两者互补：评分高(>65)+阶段"主升浪"=强信号共振；
      评分高+阶段"高位震荡筑顶"=顶背离预警
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# ── 阶段常量 ──────────────────────────────────────────────────────────────

STAGE_MAIN_UPTREND = "主升浪"
STAGE_UPTREND_PULLBACK = "上涨中回调"
STAGE_TOPPING = "高位震荡筑顶"
STAGE_MAIN_DOWNTREND = "主跌浪"
STAGE_DOWNTREND_BOUNCE = "下跌中反弹"
STAGE_BOTTOMING = "低位筑底"
STAGE_UNKNOWN = "阶段不明确"

# 每个阶段对应的盘前预期行为（不构成买卖指令，仅是阶段属性）
EXPECTED_ACTIONS: dict[str, str] = {
    STAGE_MAIN_UPTREND: "顺势持有，不追高加仓",
    STAGE_UPTREND_PULLBACK: "回调低吸，破MA20止损",
    STAGE_TOPPING: "逐步减仓，警惕破位",
    STAGE_MAIN_DOWNTREND: "空仓观望，严禁抢反弹",
    STAGE_DOWNTREND_BOUNCE: "不参与或轻仓做T",
    STAGE_BOTTOMING: "分批建仓，需多次确认",
    STAGE_UNKNOWN: "观望，等待阶段明确",
}

# 阶段 → 信心度评分（用于盘前报告 §1.4 仓位建议映射）
# 主升/筑底 = 偏多（3-5分）；回调/反弹 = 中性（2-3分）；筑顶/主跌 = 偏空（1-2分）
STAGE_CONFIDENCE_SCORE: dict[str, int] = {
    STAGE_MAIN_UPTREND: 5,
    STAGE_BOTTOMING: 4,
    STAGE_UPTREND_PULLBACK: 3,
    STAGE_DOWNTREND_BOUNCE: 2,
    STAGE_TOPPING: 2,
    STAGE_MAIN_DOWNTREND: 1,
    STAGE_UNKNOWN: 2,
}


def _safe_last(series: pd.Series) -> float:
    """取 Series 最后一个非 NaN 值，失败返回 NaN。"""
    if series is None or len(series) == 0:
        return float("nan")
    v = series.iloc[-1]
    try:
        v = float(v)
        if pd.isna(v):
            return float("nan")
        return v
    except (TypeError, ValueError):
        return float("nan")


def _is_bullish_arrangement(ma5: float, ma10: float, ma20: float, ma60: float) -> bool:
    """多头排列：MA5 > MA10 > MA20 > MA60。"""
    if any(pd.isna(x) for x in [ma5, ma10, ma20, ma60]):
        return False
    return ma5 > ma10 > ma20 > ma60


def _is_bearish_arrangement(ma5: float, ma10: float, ma20: float, ma60: float) -> bool:
    """空头排列：MA5 < MA10 < MA20 < MA60。"""
    if any(pd.isna(x) for x in [ma5, ma10, ma20, ma60]):
        return False
    return ma5 < ma10 < ma20 < ma60


def _ma_slope(ma_series: pd.Series, window: int = 5) -> str:
    """判断均线斜率方向：upward/downward/flat。"""
    if ma_series is None or len(ma_series) < window + 1:
        return "flat"
    recent = ma_series.tail(window + 1).dropna()
    if len(recent) < 2:
        return "flat"
    diff = recent.iloc[-1] - recent.iloc[0]
    # 斜率阈值：日均 0.1% 以上才算方向性
    avg_val = recent.mean()
    if avg_val == 0:
        return "flat"
    slope_pct = (diff / avg_val) * 100
    if slope_pct > 0.5:
        return "upward"
    elif slope_pct < -0.5:
        return "downward"
    return "flat"


def _detect_top_divergence(close: pd.Series, macd_hist: pd.Series, rsi: pd.Series) -> bool:
    """顶背离：价格创新高但 MACD/RSI 未创新高（近 30 日内）。"""
    lookback = min(30, len(close) - 1)
    if lookback < 10:
        return False
    recent_close = close.tail(lookback)
    recent_macd = macd_hist.tail(lookback).dropna()
    recent_rsi = rsi.tail(lookback).dropna()
    if len(recent_macd) < 5 or len(recent_rsi) < 5:
        return False
    # 当前是否是近期高点（前 5 日均值接近近 30 日最高）
    current_close = close.iloc[-1]
    high_30 = recent_close.max()
    if current_close < high_30 * 0.98:
        return False
    # MACD 当前值是否低于前期高点
    current_macd = _safe_last(macd_hist)
    prior_macd_high = recent_macd.iloc[:-5].max() if len(recent_macd) > 5 else recent_macd.max()
    current_rsi = _safe_last(rsi)
    prior_rsi_high = recent_rsi.iloc[:-5].max() if len(recent_rsi) > 5 else recent_rsi.max()
    # 价格接近新高，但 MACD 或 RSI 低于前期高点 → 顶背离
    return (current_macd < prior_macd_high * 0.9) or (current_rsi < prior_rsi_high * 0.95)


def _detect_bottom_divergence(close: pd.Series, macd_hist: pd.Series, rsi: pd.Series) -> bool:
    """底背离：价格创新低但 MACD/RSI 未创新低（近 30 日内）。"""
    lookback = min(30, len(close) - 1)
    if lookback < 10:
        return False
    recent_close = close.tail(lookback)
    recent_macd = macd_hist.tail(lookback).dropna()
    recent_rsi = rsi.tail(lookback).dropna()
    if len(recent_macd) < 5 or len(recent_rsi) < 5:
        return False
    current_close = close.iloc[-1]
    low_30 = recent_close.min()
    if current_close > low_30 * 1.02:
        return False
    current_macd = _safe_last(macd_hist)
    prior_macd_low = recent_macd.iloc[:-5].min() if len(recent_macd) > 5 else recent_macd.min()
    current_rsi = _safe_last(rsi)
    prior_rsi_low = recent_rsi.iloc[:-5].min() if len(recent_rsi) > 5 else recent_rsi.min()
    return (current_macd > prior_macd_low * 1.1) or (current_rsi > prior_rsi_low * 1.05)


def _ma_congestion(ma5: float, ma10: float, ma20: float, ma60: float) -> tuple[bool, float]:
    """判断均线是否粘合（4 线在窄区间内缠绕）。

    返回 (is_congested, spread_pct)。spread_pct = (max-min)/mean。
    粘合阈值：spread < 2% 视为粘合态（变盘前兆）。
    """
    vals = [v for v in [ma5, ma10, ma20, ma60] if not pd.isna(v)]
    if len(vals) < 3:
        return False, 0.0
    mean_v = sum(vals) / len(vals)
    if mean_v == 0:
        return False, 0.0
    spread = (max(vals) - min(vals)) / abs(mean_v) * 100
    return spread < 2.0, round(spread, 2)


def classify_stage(
    df: pd.DataFrame,
    indicators: dict[str, Any],
) -> dict[str, Any]:
    """6 阶段趋势识别器（核心）。

    参数：
        df: OHLCV DataFrame（升序，DatetimeIndex）
        indicators: 预算好的指标 dict，包含：
            - ma5/ma10/ma20/ma60: pd.Series
            - ma5_series/ma20_series: 完整 Series（用于算斜率）
            - macd_hist: pd.Series
            - rsi: pd.Series
            - kdj: pd.DataFrame (k/d/j)
            - volume_price: dict (volume_trend/price_volume_divergence)
            - support_resistance: dict

    返回：
        {
            "stage": "主升浪"/"上涨中回调"/.../"阶段不明确",
            "stage_confidence": 0-100,
            "reasons": [str, ...],  # 命中条件的解释
            "expected_action": "顺势持有，不追高加仓",
            "confidence_score": 1-5,  # 盘前信心度（映射仓位）
            "signals_detail": {...}  # 各判定维度的明细，供调试/报告
        }
    """
    if df.empty or len(df) < 20:
        return {
            "stage": STAGE_UNKNOWN,
            "stage_confidence": 0,
            "reasons": ["数据不足 20 日，无法判定阶段"],
            "expected_action": EXPECTED_ACTIONS[STAGE_UNKNOWN],
            "confidence_score": STAGE_CONFIDENCE_SCORE[STAGE_UNKNOWN],
            "signals_detail": {},
        }

    # ── 提取指标值 ──────────────────────────────────────────────────────
    ma5 = _safe_last(indicators["ma5"])
    ma10 = _safe_last(indicators["ma10"])
    ma20 = _safe_last(indicators["ma20"])
    ma60 = _safe_last(indicators["ma60"])
    close = float(df["close"].iloc[-1])
    pre_close = float(df["close"].iloc[-2]) if len(df) > 1 else close

    macd_hist_series = indicators["macd_hist"]
    macd_hist = _safe_last(macd_hist_series)
    macd_hist_prev = _safe_last(macd_hist_series.shift(1)) if len(macd_hist_series) > 1 else float("nan")

    rsi_series = indicators["rsi"]
    rsi = _safe_last(rsi_series)

    kdj_df = indicators["kdj"]
    kdj_k = _safe_last(kdj_df["k"]) if len(kdj_df) > 0 else float("nan")

    vp = indicators["volume_price"]
    vol_trend = vp.get("volume_trend", "flat")
    vol_divergence = vp.get("price_volume_divergence", False)

    ma5_slope = _ma_slope(indicators.get("ma5_series", pd.Series()))
    ma20_slope = _ma_slope(indicators.get("ma20_series", pd.Series()))

    bullish_arr = _is_bullish_arrangement(ma5, ma10, ma20, ma60)
    bearish_arr = _is_bearish_arrangement(ma5, ma10, ma20, ma60)

    price_above_ma20 = close > ma20 if not pd.isna(ma20) else False
    price_above_ma5 = close > ma5 if not pd.isna(ma5) else False
    ma5_above_ma10 = ma5 > ma10 if not pd.isna(ma5) and not pd.isna(ma10) else False
    ma5_below_ma10 = ma5 < ma10 if not pd.isna(ma5) and not pd.isna(ma10) else False
    ma5_above_ma10_prev = False  # MA5 是否刚刚上穿 MA10（简化：当前 ma5>ma10 且 5 日前 ma5<ma10）
    if len(indicators.get("ma5_series", pd.Series())) >= 5 and len(indicators.get("ma10_series", pd.Series())) >= 5:
        m5_s = indicators["ma5_series"]
        m10_s = indicators["ma10_series"]
        if len(m5_s) >= 6 and len(m10_s) >= 6:
            ma5_prev = _safe_last(m5_s.iloc[-6:-5]) if len(m5_s) > 5 else float("nan")
            ma10_prev = _safe_last(m10_s.iloc[-6:-5]) if len(m10_s) > 5 else float("nan")
            if not pd.isna(ma5_prev) and not pd.isna(ma10_prev):
                ma5_above_ma10_prev = ma5 > ma10 and ma5_prev <= ma10_prev  # 金叉
                ma5_below_ma10_prev = ma5 < ma10 and ma5_prev >= ma10_prev  # 死叉

    macd_hist_positive = macd_hist > 0 if not pd.isna(macd_hist) else False
    macd_hist_negative = macd_hist < 0 if not pd.isna(macd_hist) else False
    macd_hist_expanding = (abs(macd_hist) > abs(macd_hist_prev)) if not pd.isna(macd_hist) and not pd.isna(macd_hist_prev) else False

    rsi_strong = 60 <= rsi <= 80 if not pd.isna(rsi) else False
    rsi_weak = 20 <= rsi <= 40 if not pd.isna(rsi) else False
    rsi_mid = 40 < rsi < 60 if not pd.isna(rsi) else False

    vol_increasing = vol_trend == "increasing"
    vol_decreasing = vol_trend == "decreasing"

    price_up = close > pre_close
    price_down = close < pre_close

    signals_detail = {
        "ma_arrangement": "多头" if bullish_arr else ("空头" if bearish_arr else "混合"),
        "ma5_ma10_cross": "金叉" if ma5_above_ma10_prev else ("死叉" if ma5_below_ma10_prev else "无"),
        "price_vs_ma20": "上方" if price_above_ma20 else "下方",
        "price_vs_ma5": "上方" if price_above_ma5 else "下方",
        "ma20_slope": ma20_slope,
        "macd_hist": "红柱放大" if (macd_hist_positive and macd_hist_expanding) else
                     ("绿柱放大" if (macd_hist_negative and macd_hist_expanding) else
                      ("红柱缩小" if macd_hist_positive else "绿柱缩小" if macd_hist_negative else "中性")),
        "rsi_zone": "强区" if rsi_strong else ("弱区" if rsi_weak else "中区"),
        "volume_trend": vol_trend,
        "vol_price_divergence": vol_divergence,
    }

    # ── 阶段判定（按优先级，互斥）──────────────────────────────────────────

    # ① 主升浪：多头排列 + 站上MA20 + MA20上行 + MACD红柱放大 + RSI强区 + 放量上涨
    conditions_uptrend = [
        ("多头排列", bullish_arr),
        ("站上MA20", price_above_ma20),
        ("MA20上行", ma20_slope == "upward"),
        ("MACD红柱放大", macd_hist_positive and macd_hist_expanding),
        ("RSI强区", rsi_strong),
        ("放量上涨", vol_increasing and price_up),
    ]
    hits_uptrend = [name for name, hit in conditions_uptrend if hit]
    if len(hits_uptrend) >= 4 and bullish_arr and price_above_ma20:
        confidence = int(len(hits_uptrend) / len(conditions_uptrend) * 100)
        return _build_result(STAGE_MAIN_UPTREND, confidence, hits_uptrend, signals_detail)

    # ④ 主跌浪：空头排列 + 跌破所有短均线 + MACD绿柱放大 + RSI弱区 + 放量下跌
    conditions_downtrend = [
        ("空头排列", bearish_arr),
        ("跌破MA20", not price_above_ma20),
        ("MA20下行", ma20_slope == "downward"),
        ("MACD绿柱放大", macd_hist_negative and macd_hist_expanding),
        ("RSI弱区", rsi_weak),
        ("放量下跌", vol_increasing and price_down),
    ]
    hits_downtrend = [name for name, hit in conditions_downtrend if hit]
    if len(hits_downtrend) >= 4 and bearish_arr and not price_above_ma20:
        confidence = int(len(hits_downtrend) / len(conditions_downtrend) * 100)
        return _build_result(STAGE_MAIN_DOWNTREND, confidence, hits_downtrend, signals_detail)

    # ③ 高位震荡筑顶：均线粘合/走平 + 顶背离 + 量价背离（需在高位）
    is_high_level = close > ma60 * 0.95 if not pd.isna(ma60) and ma60 > 0 else False  # 接近或高于半年线
    is_ma_congested, ma_spread = _ma_congestion(ma5, ma10, ma20, ma60)
    top_divergence = _detect_top_divergence(df["close"], macd_hist_series, rsi_series)
    conditions_topping = [
        ("MA20走平", ma20_slope == "flat"),
        ("均线粘合", is_ma_congested),
        ("顶背离", top_divergence),
        ("量价背离", vol_divergence),
        ("位于高位", is_high_level),
        ("MACD绿柱", macd_hist_negative),
        ("跌破MA20", not price_above_ma20),
    ]
    hits_topping = [name for name, hit in conditions_topping if hit]
    # 高位粘合 + 跌破均线 + MACD 绿柱 = 筑顶信号（放宽：粘合态下 3 条即可）
    if is_high_level and len(hits_topping) >= 3 and (is_ma_congested or top_divergence or (not price_above_ma20 and macd_hist_negative)):
        confidence = int(len(hits_topping) / len(conditions_topping) * 100)
        signals_detail["ma_congestion_spread"] = f"{ma_spread}%"
        return _build_result(STAGE_TOPPING, confidence, hits_topping, signals_detail)

    # ⑥ 低位筑底：均线走平 + 底背离 + 缩量至地量（需在低位）
    is_low_level = close < ma60 * 1.05 if not pd.isna(ma60) and ma60 > 0 else False  # 接近或低于半年线
    bottom_divergence = _detect_bottom_divergence(df["close"], macd_hist_series, rsi_series)
    conditions_bottoming = [
        ("MA20走平", ma20_slope == "flat"),
        ("均线粘合", is_ma_congested),
        ("底背离", bottom_divergence),
        ("缩量", vol_decreasing),
        ("位于低位", is_low_level),
        ("MACD红柱", macd_hist_positive),
    ]
    hits_bottoming = [name for name, hit in conditions_bottoming if hit]
    if is_low_level and len(hits_bottoming) >= 3 and (is_ma_congested or bottom_divergence):
        confidence = int(len(hits_bottoming) / len(conditions_bottoming) * 100)
        signals_detail["ma_congestion_spread"] = f"{ma_spread}%"
        return _build_result(STAGE_BOTTOMING, confidence, hits_bottoming, signals_detail)

    # ② 上涨中回调：多头排列但 MA5 下穿 MA10 + 跌破 MA5 守住 MA20 + 缩量
    # 放宽：MA20>MA60（长线多头）+ MA5<MA10（短线调整）+ 守住 MA20 也算回调
    ma20_above_ma60 = ma20 > ma60 if not pd.isna(ma20) and not pd.isna(ma60) else False
    conditions_pullback = [
        ("多头大格局(MA20>MA60)", ma20_above_ma60 or bullish_arr),
        ("MA5死叉MA10", ma5_below_ma10 or ma5_below_ma10_prev),
        ("跌破MA5", not price_above_ma5),
        ("守住或贴近MA20", price_above_ma20 or (abs(close - ma20) / ma20 < 0.01 if not pd.isna(ma20) and ma20 > 0 else False)),
        ("MACD绿柱缩短", macd_hist_negative and not macd_hist_expanding if not pd.isna(macd_hist) else False),
    ]
    hits_pullback = [name for name, hit in conditions_pullback if hit]
    if len(hits_pullback) >= 3 and (price_above_ma20 or (ma20_above_ma60 and not bearish_arr)):
        confidence = int(len(hits_pullback) / len(conditions_pullback) * 100)
        return _build_result(STAGE_UPTREND_PULLBACK, confidence, hits_pullback, signals_detail)

    # ⑤ 下跌中反弹：空头排列但 MA5 上穿 MA10 + 反弹至 MA20 遇阻 + 缩量反弹
    ma20_below_ma60 = ma20 < ma60 if not pd.isna(ma20) and not pd.isna(ma60) else False
    conditions_bounce = [
        ("空头大格局(MA20<MA60)", ma20_below_ma60 or bearish_arr),
        ("MA5金叉MA10", ma5_above_ma10 or ma5_above_ma10_prev),
        ("反弹", price_up),
        ("MA20下方", not price_above_ma20),
        ("MACD红柱", macd_hist_positive),
    ]
    hits_bounce = [name for name, hit in conditions_bounce if hit]
    if len(hits_bounce) >= 3:
        confidence = int(len(hits_bounce) / len(conditions_bounce) * 100)
        return _build_result(STAGE_DOWNTREND_BOUNCE, confidence, hits_bounce, signals_detail)

    # 兜底：阶段不明确
    return _build_result(STAGE_UNKNOWN, 0, ["未命中任何阶段的充分条件"], signals_detail)


def _build_result(stage: str, confidence: int, hits: list[str], signals_detail: dict) -> dict[str, Any]:
    """组装 classify_stage 的返回 dict。"""
    return {
        "stage": stage,
        "stage_confidence": confidence,
        "reasons": hits,
        "expected_action": EXPECTED_ACTIONS[stage],
        "confidence_score": STAGE_CONFIDENCE_SCORE[stage],
        "signals_detail": signals_detail,
    }
