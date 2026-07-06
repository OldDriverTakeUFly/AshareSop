"""Index technical analysis orchestrator — 编排入口。

单指数流程：fetch_index_ohlcv → 算指标 → composite_technical_score → classify_stage
            → 组装结果（含支撑压力位、关键均线值、盘前预期）

与 daily-market-scan 的其他模块（limit_up/dragon_tiger/fund_flow/risk_alert）平级，
作为 Wave 2 并行模块，输出通过 save_daily_data(date, 'index_technical', result) 持久化。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from stockhot.core.logging import logger
from stockhot.index_technical.data_loader import fetch_index_ohlcv
from stockhot.index_technical.stages import classify_stage
from stockhot.technical_analyzer.indicators import (
    bollinger,
    kdj,
    ma,
    macd,
    rsi,
    support_resistance,
    volume_price_analysis,
)
from stockhot.technical_analyzer.scoring import composite_technical_score

# 默认分析的 4 大指数（覆盖大盘主流）
DEFAULT_INDICES = [
    "000001.SH",  # 上证指数
    "399001.SZ",  # 深证成指
    "399006.SZ",  # 创业板指
    "000688.SH",  # 科创50
]

INDEX_NAMES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "399005.SZ": "中小板指",
}


def _compute_indicators(df: pd.DataFrame) -> dict[str, Any]:
    """复用 technical_analyzer.indicators 计算全部指标。"""
    ma5_s = ma(df, 5)
    ma10_s = ma(df, 10)
    ma20_s = ma(df, 20)
    ma60_s = ma(df, 60)

    return {
        "ma5": ma5_s,
        "ma10": ma10_s,
        "ma20": ma20_s,
        "ma60": ma60_s,
        "ma5_series": ma5_s,
        "ma10_series": ma10_s,
        "ma20_series": ma20_s,
        "ma60_series": ma60_s,
        "macd": macd(df),
        "macd_hist": macd(df)["macd_hist"],
        "rsi": rsi(df),
        "kdj": kdj(df),
        "bollinger": bollinger(df),
        "support_resistance": support_resistance(df),
        "volume_price": volume_price_analysis(df),
    }


def _safe_last(series) -> float:
    if series is None or len(series) == 0:
        return float("nan")
    try:
        v = float(series.iloc[-1])
        return v if not pd.isna(v) else float("nan")
    except (TypeError, ValueError, IndexError):
        return float("nan")


def _analyze_single_index(ts_code: str, days: int = 120) -> dict[str, Any]:
    """分析单个指数，返回结构化结果。失败返回 {status: 'error', ...}。"""
    name = INDEX_NAMES.get(ts_code, ts_code)
    df = fetch_index_ohlcv(ts_code, days=days)
    if df.empty:
        return {
            "ts_code": ts_code,
            "name": name,
            "status": "数据不可用",
            "error": "OHLCV 采集失败",
        }
    if len(df) < 30:
        return {
            "ts_code": ts_code,
            "name": name,
            "status": "数据不足",
            "error": f"仅 {len(df)} 行，需 ≥30",
        }

    indicators = _compute_indicators(df)
    score_result = composite_technical_score(df)
    stage_result = classify_stage(df, indicators)

    close = float(df["close"].iloc[-1])
    pre_close = float(df["close"].iloc[-2]) if len(df) > 1 else close
    pct_chg = (close - pre_close) / pre_close * 100 if pre_close else 0.0

    ma5 = _safe_last(indicators["ma5"])
    ma10 = _safe_last(indicators["ma10"])
    ma20 = _safe_last(indicators["ma20"])
    ma60 = _safe_last(indicators["ma60"])
    macd_hist = _safe_last(indicators["macd_hist"])
    rsi_v = _safe_last(indicators["rsi"])
    kdj_k = _safe_last(indicators["kdj"]["k"]) if len(indicators["kdj"]) > 0 else float("nan")
    kdj_d = _safe_last(indicators["kdj"]["d"]) if len(indicators["kdj"]) > 0 else float("nan")

    sr = indicators["support_resistance"]
    support_levels = sr.get("support", [])[:3]  # Top 3
    resistance_levels = sr.get("resistance", [])[-3:]  # 最近 3 个

    boll = indicators["bollinger"]
    boll_upper = _safe_last(boll["boll_upper"]) if len(boll) > 0 else float("nan")
    boll_lower = _safe_last(boll["boll_lower"]) if len(boll) > 0 else float("nan")

    return {
        "ts_code": ts_code,
        "name": name,
        "status": "success",
        "trade_date": df.index[-1].strftime("%Y-%m-%d"),
        "close": round(close, 2),
        "pct_chg": round(pct_chg, 2),
        "technical_score": score_result["score"],
        "technical_state": score_result["state"],
        "stage": stage_result["stage"],
        "stage_confidence": stage_result["stage_confidence"],
        "reasons": stage_result["reasons"],
        "expected_action": stage_result["expected_action"],
        "confidence_score": stage_result["confidence_score"],
        "signals_detail": stage_result["signals_detail"],
        "ma5": round(ma5, 2) if not pd.isna(ma5) else None,
        "ma10": round(ma10, 2) if not pd.isna(ma10) else None,
        "ma20": round(ma20, 2) if not pd.isna(ma20) else None,
        "ma60": round(ma60, 2) if not pd.isna(ma60) else None,
        "macd_hist": round(macd_hist, 4) if not pd.isna(macd_hist) else None,
        "rsi": round(rsi_v, 2) if not pd.isna(rsi_v) else None,
        "kdj_k": round(kdj_k, 2) if not pd.isna(kdj_k) else None,
        "kdj_d": round(kdj_d, 2) if not pd.isna(kdj_d) else None,
        "boll_upper": round(boll_upper, 2) if not pd.isna(boll_upper) else None,
        "boll_lower": round(boll_lower, 2) if not pd.isna(boll_lower) else None,
        "support": [round(s, 2) for s in support_levels],
        "resistance": [round(r, 2) for r in resistance_levels],
    }


def _build_summary(indices_results: dict[str, dict]) -> str:
    """根据各指数阶段，生成一句话整体技术面定性。"""
    stages = [r["stage"] for r in indices_results.values() if r.get("status") == "success"]
    if not stages:
        return "数据不可用，无法生成整体定性"

    # 统计阶段分布
    from collections import Counter

    stage_counts = Counter(stages)
    main_stage = stage_counts.most_common(1)[0]

    # 偏空/偏多判断
    bearish_stages = {"主跌浪", "下跌中反弹", "高位震荡筑顶"}
    bullish_stages = {"主升浪", "上涨中回调", "低位筑底"}
    bearish_n = sum(stage_counts.get(s, 0) for s in bearish_stages)
    bullish_n = sum(stage_counts.get(s, 0) for s in bullish_stages)

    parts = [f"上证{indices_results.get('000001.SH', {}).get('stage', '?')}"
             f"/创业板{indices_results.get('399006.SZ', {}).get('stage', '?')}"
             f"/科创50{indices_results.get('000688.SH', {}).get('stage', '?')}"]

    if bearish_n > bullish_n:
        parts.append(f"整体偏弱（{bearish_n}/{len(stages)} 指数处于下跌/筑顶阶段），盘前不建议重仓")
    elif bullish_n > bearish_n:
        parts.append(f"整体偏强（{bullish_n}/{len(stages)} 指数处于上涨/筑底阶段），可适度参与")
    else:
        parts.append(f"整体分化（多空各 {bullish_n}/{len(stages)}），按指数差异化应对")

    return "，".join(parts)


def run_index_technical_analysis(
    date: str | None = None,
    indices: list[str] | None = None,
    days: int = 120,
) -> dict[str, Any]:
    """指数技术面分析入口（供 daily-market-scan 的 Wave 2 调用）。

    参数：
        date: 日期字符串 YYYY-MM-DD（仅用于结果标记，实际取最新数据）
        indices: 指数代码列表，None 则用 DEFAULT_INDICES
        days: 拉取交易日数（默认 120）

    返回：
        {
            "date": "2026-07-06",
            "status": "success",
            "indices": {ts_code: {单指数结果}},
            "summary": "上证.../创业板...，整体偏弱/偏强/分化",
        }

    单个指数失败不影响其他指数（遵循 daily-market-scan 的 try/except 隔离原则）。
    """
    indices = indices or DEFAULT_INDICES
    date = date or datetime.now().strftime("%Y-%m-%d")

    logger.info(f"run_index_technical_analysis: date={date}, indices={indices}")

    indices_results: dict[str, dict] = {}
    for ts_code in indices:
        try:
            result = _analyze_single_index(ts_code, days=days)
            indices_results[ts_code] = result
            stage = result.get("stage", "?")
            score = result.get("technical_score", "?")
            logger.info(f"  {ts_code} ({result.get('name')}): stage={stage}, score={score}")
        except Exception as e:
            indices_results[ts_code] = {
                "ts_code": ts_code,
                "name": INDEX_NAMES.get(ts_code, ts_code),
                "status": "数据不可用",
                "error": f"{type(e).__name__}: {e}",
            }
            logger.error(f"  {ts_code} 分析失败: {type(e).__name__}: {e}")

    summary = _build_summary(indices_results)
    success_n = sum(1 for r in indices_results.values() if r.get("status") == "success")

    return {
        "date": date,
        "status": "success" if success_n > 0 else "数据不可用",
        "indices": indices_results,
        "summary": summary,
    }
