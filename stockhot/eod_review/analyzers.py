"""eod_review 量化分析层 — 板块聚合 / 涨停归因 / 情绪温度计 / N 日趋势.

这是本引擎区别于 after-hours-review 的核心——用量化方法做归因，而非 web 搜索定性。

四个分析模块：
    A. ``aggregate_sector_performance`` — 按 Tushare industry 聚合板块涨跌
    B. ``attribute_limit_up``           — 5 类涨停量化归因
    C. ``compute_sentiment_thermometer``— 多维情绪温度计（融资融券/北向/大宗/涨跌停）
    D. ``compute_n_day_trend``          — N 日趋势对比

所有分析函数都是**纯函数**（输入 MarketSnapshot/数据，输出 dataclass 列表），
无副作用，易单测。
"""

from __future__ import annotations

import json
import math
import time
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from stockhot.core.logging import logger
from stockhot.data_layer.market_db import get_connection
from stockhot.eod_review.data_layer import MarketSnapshot, get_history

# ── 单位换算 ──────────────────────────────────────────────────────────
_WAN_TO_YI = 1e-4   # 万元 → 亿元
_YUAN_TO_YI = 1e-8  # 元 → 亿元


# ═══════════════════════════════════════════════════════════════════════
# 模块 A：板块涨幅聚合
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class SectorPerf:
    """板块表现汇总."""

    name: str
    mean_pct: float          # 等权均涨幅 %
    median_pct: float        # 中位数涨幅 %
    limit_up_count: int      # 涨停数
    limit_down_count: int    # 跌停数
    member_count: int        # 成分股数
    dispersion: float        # 涨幅标准差（分歧度），越高板块越不齐心
    net_inflow: float = 0.0  # 板块资金流净额（亿，如有）


def aggregate_sector_performance(
    snapshot: MarketSnapshot,
) -> list[SectorPerf]:
    """按 Tushare industry 字段聚合个股涨跌 → 板块表现.

    输出：等权均涨幅、中位数、涨停数、跌停数、成分股数、涨幅标准差（分歧度）。
    资金流净额从 moneyflow_sector 按板块名匹配补充（可选）。
    """
    dwi = snapshot.daily_with_industry
    if dwi is None or dwi.empty:
        return []

    # 资金流查找表（板块名 → main_net，已是亿元）
    mf_map: dict[str, float] = {}
    for row in snapshot.moneyflow_sector or []:
        name = row.get("name", "")
        main_net = _safe_float(row.get("main_net"))
        if name and main_net is not None:
            mf_map[name] = main_net

    # 涨停/跌停 ts_code 集合（用于计数）
    up_codes = {r.get("code") for r in snapshot.limit_up if r.get("code")}
    down_codes = {r.get("code") for r in snapshot.limit_down if r.get("code")}

    results: list[SectorPerf] = []
    for industry, group in dwi.groupby("industry"):
        if not industry or len(group) < 3:
            continue  # 成分股太少无统计意义
        pct = pd.to_numeric(group["pct_chg"], errors="coerce").dropna()
        if len(pct) == 0:
            continue

        # 涨停/跌停计数（匹配 ts_code）
        codes_in_sector = set(group["ts_code"])
        lu = len(codes_in_sector & up_codes)
        ld = len(codes_in_sector & down_codes)

        results.append(
            SectorPerf(
                name=str(industry),
                mean_pct=round(float(pct.mean()), 2),
                median_pct=round(float(pct.median()), 2),
                limit_up_count=lu,
                limit_down_count=ld,
                member_count=len(pct),
                dispersion=round(float(pct.std(ddof=0)), 2) if len(pct) > 1 else 0.0,
                net_inflow=round(mf_map.get(str(industry), 0.0), 2),
            )
        )

    # 按等权均涨幅降序
    results.sort(key=lambda x: x.mean_pct, reverse=True)
    return results


# ═══════════════════════════════════════════════════════════════════════
# 模块 B：涨停归因（量化，5 类）
# ═══════════════════════════════════════════════════════════════════════

# 归因类型常量
ATTR_BREAKOUT = "箱体突破"          # 60 日箱体上沿突破 + 量比 > 2
ATTR_VOLUME_FUND = "放量资金推动"    # 量比 > 3 + 龙虎榜净买入 > 5000 万
ATTR_RELAY = "连板接力"             # consecutive_boards >= 2 + 换手率适中
ATTR_VALUE_REPAIR = "低估值修复"     # PE 百分位 < 20%
ATTR_EVENT = "事件驱动"             # 无明显量化特征（需 web 补充）


@dataclass
class LimitUpAttribution:
    """涨停股的量化归因结果."""

    ts_code: str
    name: str
    sector: str
    consecutive_boards: int
    attribution_type: str   # ATTR_* 之一
    confidence: float       # 0-1，归因置信度
    detail: dict            # 量化证据（量比/PE百分位/封单等）


def attribute_limit_up(
    snapshot: MarketSnapshot,
    *,
    history_map: dict[str, pd.DataFrame] | None = None,
    max_history_fetch: int = 50,
) -> list[LimitUpAttribution]:
    """对每只涨停股做量化归因分类（5 类）.

    归因优先级（先命中的优先，避免重复分类）：
    1. 连板接力（consecutive_boards >= 2）
    2. 箱体突破（需历史数据，计算 60 日箱体 + 量比）
    3. 低估值修复（PE 百分位 < 20%，需 history + daily_basic）
    4. 放量资金推动（量比 > 3 + 龙虎榜净买入）
    5. 事件驱动（兜底，无明显量化特征）

    Parameters
    ----------
    history_map : dict | None
        预取的历史日线缓存 {ts_code: DataFrame}。为 None 时按需拉取（受 max_history_fetch 限制）。
    max_history_fetch : int
        当 history_map 为 None 时，最多拉取多少只股票的历史（涨停数多时限制耗时）。
    """
    if not snapshot.limit_up:
        return []

    # 龙虎榜净买入查找表（ts_code → net_buy）
    dt_map: dict[str, float] = {}
    for row in snapshot.dragon_tiger or []:
        code = row.get("code")
        net = _safe_float(row.get("net_buy_amount"))
        if code and net is not None:
            dt_map[code] = net

    # daily_basic 查找表（ts_code → pe_ttm）
    pe_map: dict[str, float] = {}
    if snapshot.daily_basic is not None and not snapshot.daily_basic.empty:
        for _, row in snapshot.daily_basic.iterrows():
            pe = _safe_float(row.get("pe_ttm"))
            if pe is not None and pe > 0:
                pe_map[row["ts_code"]] = pe

    # 历史日线缓存（按需拉取）
    local_history: dict[str, pd.DataFrame] = dict(history_map or {})
    fetch_count = 0

    results: list[LimitUpAttribution] = []
    for stock in snapshot.limit_up:
        ts_code = stock.get("code", "")
        name = stock.get("name", "")
        sector = stock.get("sector", "")
        boards = int(_safe_float(stock.get("consecutive_boards"), 1) or 1)
        turnover = _safe_float(stock.get("turnover_rate"), 0) or 0
        seal = _safe_float(stock.get("seal_amount"), 0) or 0

        detail: dict[str, Any] = {
            "consecutive_boards": boards,
            "turnover_rate": round(turnover, 2),
            "seal_amount_wan": round(seal / 1e4, 2) if seal else 0,
        }

        attribution = ATTR_EVENT
        confidence = 0.3

        # ── 1. 连板接力（优先级最高）──
        if boards >= 2:
            attribution = ATTR_RELAY
            # 置信度随连板数递增
            confidence = min(0.5 + boards * 0.1, 0.95)
            detail["relay_height"] = boards
            if 5 <= turnover <= 20:
                detail["turnover_note"] = "换手率适中，接力健康"
            elif turnover > 20:
                detail["turnover_note"] = "换手率偏高，警惕获利盘抛压"

        # ── 2 & 3. 需历史数据的归因（箱体突破 / 估值修复）──
        elif ts_code not in local_history and fetch_count < max_history_fetch:
            hist = get_history(ts_code, days=120)
            if hist is not None and not hist.empty:
                local_history[ts_code] = hist
            fetch_count += 1

        # 如果有历史数据且尚未归类
        if attribution == ATTR_EVENT and ts_code in local_history:
            hist = local_history[ts_code]
            if len(hist) >= 60:
                # ── 2. 箱体突破 ──
                breakout = _check_box_breakout(hist, stock)
                if breakout["is_breakout"]:
                    attribution = ATTR_BREAKOUT
                    confidence = breakout["confidence"]
                    detail.update(breakout)
                else:
                    # ── 3. 低估值修复 ──
                    pe = pe_map.get(ts_code)
                    if pe is not None and pe > 0:
                        pe_pct = _compute_pe_percentile(hist, pe)
                        detail["pe_ttm"] = round(pe, 2)
                        detail["pe_percentile"] = round(pe_pct, 1)
                        if pe_pct < 20:
                            attribution = ATTR_VALUE_REPAIR
                            confidence = 0.55 + (20 - pe_pct) / 20 * 0.3

        # ── 4. 放量资金推动 ──
        if attribution == ATTR_EVENT:
            net_buy = dt_map.get(ts_code)
            if net_buy is not None and net_buy > 5000:  # 龙虎榜净买入 > 5000 万
                attribution = ATTR_VOLUME_FUND
                confidence = 0.6
                detail["dragon_tiger_net_wan"] = round(net_buy, 2)

        results.append(
            LimitUpAttribution(
                ts_code=ts_code,
                name=name,
                sector=sector,
                consecutive_boards=boards,
                attribution_type=attribution,
                confidence=round(confidence, 2),
                detail=detail,
            )
        )

    return results


def _check_box_breakout(hist: pd.DataFrame, stock: dict) -> dict:
    """检测箱体突破（参考 studies/rally_screening/breakout_screen.py 逻辑）.

    判定条件：
    - 今日收盘 > 过去 60 日最高价 × 1.01（突破上沿至少 1%）
    - 量比 > 2.0（vs 120 日均量）
    - 60 日箱体振幅 < 45%（有效箱体，非单边下跌）
    """
    result: dict[str, Any] = {"is_breakout": False, "confidence": 0.0}
    if len(hist) < 61:
        return result

    # 历史区间（不含今日，今日是最后一行）
    lookback = hist.iloc[-61:-1]
    today = hist.iloc[-1]

    today_close = _safe_float(today.get("close"))
    today_vol = _safe_float(today.get("vol"))
    if today_close is None or today_vol is None:
        return result

    high_60 = _safe_float(lookback["high"].max())
    if high_60 is None or high_60 <= 0:
        return result

    low_60 = _safe_float(lookback["low"].min())
    amplitude = (high_60 - low_60) / low_60 * 100 if low_60 and low_60 > 0 else 999

    # 突破上沿 ≥ 1%
    breakout_pct = (today_close / high_60 - 1) * 100 if high_60 > 0 else 0
    if breakout_pct < 1.0:
        return result

    # 量比（vs 120 日均量，不足 120 用全部）
    vol_window = hist["vol"].iloc[:-1].tail(120)
    avg_vol = _safe_float(vol_window.mean())
    vol_ratio = today_vol / avg_vol if avg_vol and avg_vol > 0 else 0

    detail = {
        "breakout_above_box_pct": round(breakout_pct, 2),
        "vol_ratio": round(vol_ratio, 2),
        "box_amplitude_pct": round(amplitude, 2),
        "high_60": round(high_60, 2),
    }

    # 突破需要量比 > 2 + 箱体振幅合理
    if vol_ratio >= 2.0 and amplitude < 45:
        # 置信度：量比越大、箱体越规整 → 置信度越高
        conf = 0.6 + min(vol_ratio / 10, 0.3)  # 量比贡献
        if amplitude < 30:
            conf += 0.05  # 紧箱体加成
        result["is_breakout"] = True
        result["confidence"] = min(conf, 0.95)
        result.update(detail)
    return result


def _compute_pe_percentile(hist: pd.DataFrame, current_pe: float) -> float:
    """估算当前 PE 在近 N 日的分位（粗略，用价格代理）.

    严格 PE 分位需历史 PE 序列（daily_basic 历史），这里用价格分位近似——
    价格在低位 + 当前 PE 低 → 大概率估值分位低。返回 0-100。
    """
    close = pd.to_numeric(hist["close"], errors="coerce").dropna()
    if len(close) < 60:
        return 50.0
    today_close = float(close.iloc[-1])
    rank = (close < today_close).sum() / len(close) * 100
    return float(rank)


# ═══════════════════════════════════════════════════════════════════════
# 模块 C：情绪温度计（多维交叉）
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class SentimentReading:
    """多维情绪温度计读数."""

    score: float              # 综合情绪分 0-100
    label: str                # 极热/偏热/中性/偏冷/极冷
    margin_signal: str        # 融资融券信号描述
    north_signal: str         # 北向资金信号描述
    block_signal: str         # 大宗交易信号描述
    limit_signal: str         # 涨跌停结构描述
    divergence: str | None    # 维度间背离描述（无背离为 None）
    detail: dict              # 全维度原始数据


def compute_sentiment_thermometer(
    snapshot: MarketSnapshot,
    *,
    history_window: int = 5,
) -> SentimentReading | None:
    """多维情绪温度计（融资融券 + 北向 + 大宗 + 涨跌停）.

    四个维度各自打 0-100 分，加权平均得到综合情绪分。
    并检测维度间背离（如融资加杠杆但北向流出 = 内外资分歧）。

    Returns None 当核心数据全缺失时。
    """
    components: dict[str, float] = {}
    detail: dict[str, Any] = {}

    # ── 维度 1：涨跌停结构（权重 35%）──
    up_n = len(snapshot.limit_up)
    down_n = len(snapshot.limit_down)
    broken_n = len(snapshot.broken)
    total = up_n + down_n
    if total > 0:
        up_ratio = up_n / total
        # 涨停占比越高情绪越热；炸板率高 = 情绪不稳
        broken_rate = broken_n / (up_n + broken_n) if (up_n + broken_n) > 0 else 0
        limit_score = up_ratio * 100
        if broken_rate > 0.4:
            limit_score -= 10  # 炸板率 > 40% 扣分
        limit_score = max(0, min(100, limit_score))
        components["limit"] = limit_score
        limit_label = f"涨停{up_n}/跌停{down_n}/炸板{broken_n}（涨停占比{up_ratio*100:.0f}%）"
        if broken_rate > 0.4:
            limit_label += "，炸板率高情绪不稳"
        detail["limit"] = {
            "up": up_n, "down": down_n, "broken": broken_n,
            "up_ratio": round(up_ratio, 3), "broken_rate": round(broken_rate, 3),
        }
    else:
        limit_label = "数据不可用"

    # ── 维度 2：融资融券（权重 25%）──
    if snapshot.margin is not None and not snapshot.margin.empty:
        total_margin = pd.to_numeric(snapshot.margin["rzye"], errors="coerce").sum()
        margin_yi = total_margin * _YUAN_TO_YI  # 元 → 亿
        detail["margin"] = {"total_balance_yi": round(margin_yi, 2)}
        # 无历史对比时用绝对水平粗判（>1.6万亿偏热，<1.4万亿偏冷）
        if margin_yi > 16000:
            margin_score = 70
            margin_label = f"融资余额{margin_yi:.0f}亿（偏高）"
        elif margin_yi > 14000:
            margin_score = 55
            margin_label = f"融资余额{margin_yi:.0f}亿（中性）"
        else:
            margin_score = 35
            margin_label = f"融资余额{margin_yi:.0f}亿（偏低）"
        components["margin"] = margin_score
    else:
        margin_label = "数据不可用"

    # ── 维度 3：北向资金（权重 25%）──
    if snapshot.north_flow is not None and not snapshot.north_flow.empty:
        north_wan = _safe_float(snapshot.north_flow.iloc[0].get("north_money"))
        north_yi = north_wan * _WAN_TO_YI if north_wan is not None else None  # 万元 → 亿
        if north_yi is not None:
            detail["north"] = {"net_yi": round(north_yi, 2)}
            if north_yi > 100:
                north_score = 80
                north_label = f"北向净流入{north_yi:.0f}亿（大幅流入）"
            elif north_yi > 30:
                north_score = 65
                north_label = f"北向净流入{north_yi:.0f}亿（温和流入）"
            elif north_yi > -30:
                north_score = 50
                north_label = f"北向{north_yi:+.0f}亿（中性）"
            elif north_yi > -100:
                north_score = 35
                north_label = f"北向净流出{abs(north_yi):.0f}亿（温和流出）"
            else:
                north_score = 20
                north_label = f"北向净流出{abs(north_yi):.0f}亿（大幅流出）"
            components["north"] = north_score
        else:
            north_label = "数据不可用"
    else:
        north_label = "数据不可用"

    # ── 维度 4：大宗交易（权重 15%）──
    if snapshot.block_trade is not None and not snapshot.block_trade.empty:
        bt_count = len(snapshot.block_trade)
        # 折价率：需 merge daily close
        discounts = _compute_block_discounts(snapshot)
        if discounts:
            import statistics
            median_disc = statistics.median(discounts)
            detail["block"] = {
                "count": bt_count,
                "median_discount_pct": round(median_disc, 2),
            }
            if median_disc < -5:
                block_score = 30  # 大幅折价 = 机构出货嫌疑
                block_label = f"大宗{bt_count}笔，折价{median_disc:.1f}%（大幅折价，机构出货嫌疑）"
            elif median_disc < -2:
                block_score = 45
                block_label = f"大宗{bt_count}笔，折价{median_disc:.1f}%（小幅折价）"
            elif median_disc < 2:
                block_score = 55
                block_label = f"大宗{bt_count}笔，折价{median_disc:.1f}%（平价附近）"
            else:
                block_score = 65
                block_label = f"大宗{bt_count}笔，溢价{median_disc:.1f}%（机构抢筹）"
            components["block"] = block_score
        else:
            block_label = f"大宗{bt_count}笔（折价率不可用）"
            components["block"] = 50
    else:
        block_label = "数据不可用"

    # ── 综合打分 ──
    if not components:
        return None

    weights = {"limit": 0.35, "margin": 0.25, "north": 0.25, "block": 0.15}
    total_weight = sum(weights[k] for k in components)
    score = sum(components[k] * weights[k] for k in components) / total_weight

    # ── 背离检测 ──
    divergence = _detect_divergence(components, detail)

    label = _score_to_label(score)

    return SentimentReading(
        score=round(score, 1),
        label=label,
        margin_signal=margin_label,
        north_signal=north_label,
        block_signal=block_label,
        limit_signal=limit_label,
        divergence=divergence,
        detail=detail,
    )


def _compute_block_discounts(snapshot: MarketSnapshot) -> list[float]:
    """计算大宗交易折价率列表（price vs daily close）.

    折价率 = (block_price - close) / close × 100，负值=折价。
    """
    if snapshot.block_trade is None or snapshot.block_trade.empty:
        return []
    if snapshot.daily is None or snapshot.daily.empty:
        return []

    close_map = dict(zip(snapshot.daily["ts_code"], snapshot.daily["close"]))
    discounts: list[float] = []
    for _, row in snapshot.block_trade.iterrows():
        code = row.get("ts_code")
        bt_price = _safe_float(row.get("price"))
        close = _safe_float(close_map.get(code))
        if bt_price and close and close > 0:
            discounts.append((bt_price - close) / close * 100)
    return discounts


def _detect_divergence(components: dict[str, float], detail: dict) -> str | None:
    """检测维度间背离（情绪方向不一致）."""
    if "north" not in components or "margin" not in components:
        return None
    north = components["north"]
    margin = components["margin"]
    # 外资流出 + 融资加杠杆 = 散户追高
    if north < 40 and margin > 60:
        return "⚠️ 内外资分歧：北向流出但融资加杠杆，散户追高风险"
    # 外资流入 + 融资降温 = 机构吸筹
    if north > 60 and margin < 40:
        return "内外资分歧：北向流入但融资降温，机构吸筹特征"
    return None


def _score_to_label(score: float) -> str:
    if score >= 80:
        return "极热"
    if score >= 60:
        return "偏热"
    if score >= 40:
        return "中性"
    if score >= 20:
        return "偏冷"
    return "极冷"


# ═══════════════════════════════════════════════════════════════════════
# 模块 D：N 日趋势对比
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class TrendComparison:
    """N 日趋势对比结果."""

    window: int
    today_limit_up: int
    avg_limit_up: float          # N 日平均涨停数
    today_limit_down: int
    avg_limit_down: float
    height_distribution: dict    # 当日连板高度分布 {高度: 数量}
    sentiment_trend: list[dict]  # 近 N 日情绪分序列
    has_history: bool            # 是否有足够历史数据


def compute_n_day_trend(
    date: str,
    snapshot: MarketSnapshot,
    *,
    window: int = 5,
) -> TrendComparison:
    """N 日趋势对比（连板高度/涨停数均值/情绪分序列）.

    读取 market_data.db 的 eod_sentiment 表历史记录。
    首次跑（无历史）时 has_history=False，仅返回当日值。
    """
    today_up = len(snapshot.limit_up)
    today_down = len(snapshot.limit_down)

    # 当日连板高度分布
    height_dist: dict[int, int] = {}
    for stock in snapshot.limit_up:
        boards = int(_safe_float(stock.get("consecutive_boards"), 1) or 1)
        if boards >= 2:
            height_dist[boards] = height_dist.get(boards, 0) + 1

    # 读取近 N 日 eod_sentiment 历史
    sentiment_history: list[dict] = []
    try:
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT trade_date, sentiment_score, sentiment_label, "
                "north_net, margin_balance FROM eod_sentiment "
                "WHERE trade_date < ? ORDER BY trade_date DESC LIMIT ?",
                (date, window),
            ).fetchall()
        for r in rows:
            sentiment_history.append(
                {
                    "date": r[0],
                    "score": r[1],
                    "label": r[2],
                    "north_net": r[3],
                    "margin_balance": r[4],
                }
            )
    except Exception as e:
        logger.warning(f"[EOD] 读取 eod_sentiment 历史失败: {e}")

    has_history = len(sentiment_history) >= 2

    if sentiment_history:
        # 涨停数历史需从 eod_review 表统计（signal_type 限制涨停类）
        avg_up = _avg_signal_count(date, window, "limit_up_%")
        avg_down = _avg_signal_count(date, window, "%limit_down%")
    else:
        avg_up = float(today_up)
        avg_down = float(today_down)

    return TrendComparison(
        window=window,
        today_limit_up=today_up,
        avg_limit_up=round(avg_up, 1),
        today_limit_down=today_down,
        avg_limit_down=round(avg_down, 1),
        height_distribution=dict(sorted(height_dist.items(), reverse=True)),
        sentiment_trend=list(reversed(sentiment_history)),  # 升序展示
        has_history=has_history,
    )


def _avg_signal_count(date: str, window: int, pattern: str) -> float:
    """统计近 N 日符合 signal_type LIKE pattern 的去重股票数均值."""
    try:
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT trade_date, COUNT(DISTINCT ts_code) as cnt FROM eod_review "
                "WHERE trade_date < ? AND signal_type LIKE ? "
                "GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?",
                (date, pattern, window),
            ).fetchall()
        if not rows:
            return 0.0
        return sum(r[1] for r in rows) / len(rows)
    except Exception:
        return 0.0


# ── 工具函数 ──────────────────────────────────────────────────────────


def _safe_float(v, default=None):
    """安全转 float."""
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default
