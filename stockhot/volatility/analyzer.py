"""Volatility analyzer — 五层观察体系的 Layer 1/2/5 计算 + run_volatility_analysis 入口。

⚠️ **数据原则**：本模块的 RV20/RV60 严格使用**收盘价**计算。数据源是 DAL index_daily
表（由 daily_scan 17:30 跑，此时已收盘，数据完整）。如果当日收盘数据未入库，
宁可不算也不要用盘中价凑。盘中实时预警（panic_detector）是独立场景，不在此约束内。

实现方法论研报（``docs/方法论/A股波动率观察框架方法论深度研报.md``）中的：

Layer 1（已实现波动率 RV，主轴）— ``realized_vol`` + ``analyze_single_index``
Layer 2（RV 历史分位数，标准化刻度）— ``percentile_rank`` + ``classify_panic_level``
Layer 5（隐含波动率 IV + V/R 比率）— ``analyze_iv_rv_basis``

Layer 3（涨跌停行为代理）不入库——已在 ``limit_up`` 模块，本模块交由盘后总结联读。
Layer 4（期权期限结构）暂未实现——AKShare iVIX 仅单点，无近月/季月曲线。

入口：``run_volatility_analysis(date, indices=None, days=1300) -> dict``，
签名/返回结构对齐 ``index_technical.run_index_technical_analysis``，
作为 daily-market-scan Wave 2 并行模块，输出经 ``save_daily_data`` 持久化。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from stockhot.core.logging import logger
from stockhot.storage.database import save_daily_data
from stockhot.volatility.data_loader import fetch_index_history, fetch_ivix_history

# 默认观察的 5 大指数（与方法论研报 §4.1 一致）
DEFAULT_INDICES: list[str] = [
    "000001.SH",  # 上证指数
    "399001.SZ",  # 深证成指
    "000300.SH",  # 沪深 300
    "399006.SZ",  # 创业板指
    "000688.SH",  # 科创 50
]

INDEX_NAMES: dict[str, str] = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "000300.SH": "沪深300",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
}

# A 股年化交易日数（242，方法论研报 §3.1 校准）
TRADING_DAYS_PER_YEAR = 242

# 恐慌等级阈值（方法论研报 §2.2 刻度表）
PANIC_LEVELS = [
    (10, "极度自满"),
    (25, "平静"),
    (75, "正常"),
    (90, "偏高"),
    (95, "明显恐慌"),
    (101, "极度恐慌"),
]


# ── 纯函数（无副作用，便于测试）──────────────────────────────────────


def realized_vol(close_series: pd.Series, window: int = 20) -> pd.Series:
    """计算年化已实现波动率（RV）。

    公式：对数收益率 × √242 × 100，年化百分比（与 VIX 可比）。

    参数：
        close_series: 收盘价序列（DatetimeIndex 升序）
        window: 滚动窗口（默认 20 日 = 月度，对应 VIX 30 天窗口）

    返回：
        RV 序列（年化 %），与输入等长，前 ``window`` 个为 NaN。
    """
    logret = np.log(close_series).diff()
    return (logret.rolling(window).std() * np.sqrt(TRADING_DAYS_PER_YEAR) * 100).round(2)


def percentile_rank(current_value: float, historical_series: pd.Series) -> float:
    """计算当前值在历史序列中的分位（0-100）。

    分位定义：历史序列中小于当前值的比例 × 100。
    与方法论研报 §3.2 公式一致。

    参数：
        current_value: 当前值
        historical_series: 历史序列（自动去 NaN）

    返回：
        分位数 0-100；current_value 为 NaN 或历史序列空返回 NaN。
    """
    if pd.isna(current_value):
        return float("nan")
    clean = historical_series.dropna()
    if len(clean) == 0:
        return float("nan")
    return round(float((clean < current_value).mean() * 100), 1)


def vr_ratio(iv_current: float, rv_current: float) -> float:
    """计算 V/R 比率（隐含/已实现波动率，期权昂贵度）。

    方法论研报 §3.5：
        V/R < 0.9  期权便宜（做多波动率）
        0.9-1.1    定价合理
        1.1-1.3    期权略贵
        > 1.3      期权极贵（强烈做空波动率）

    返回：
        V/R 比率；任一输入为 NaN 或 rv_current=0 返回 NaN。
    """
    if pd.isna(iv_current) or pd.isna(rv_current) or rv_current == 0:
        return float("nan")
    return round(iv_current / rv_current, 2)


def classify_panic_level(percentile: float) -> str:
    """根据 RV 历史分位数判定恐慌等级。

    对应方法论研报 §2.2 刻度表：
        < P10      极度自满
        P10-P25    平静
        P25-P75    正常
        P75-P90    偏高
        P90-P95    明显恐慌
        > P95      极度恐慌

    参数：
        percentile: RV20 历史分位（0-100）

    返回：
        恐慌等级中文标签；percentile 为 NaN 返回 "数据不可用"。
    """
    if pd.isna(percentile):
        return "数据不可用"
    for threshold, label in PANIC_LEVELS:
        if percentile < threshold:
            return label
    return PANIC_LEVELS[-1][1]


# iVIX 绝对值恐慌等级（与方法论研报 §1.1 VIX 等级表 7 档对齐，2026-07-08 统一）
IVIX_LEVELS = [
    (12, "极度自满"),     # < 12 长期牛市顶部、波动性卖方过度拥挤
    (18, "平静健康"),     # 12-18 牛市中段、低波慢牛
    (22, "略有担忧"),     # 18-22 经济数据分歧、临近重大事件
    (30, "明显恐慌"),     # 22-30 衰退预期升温、地缘冲突升级
    (40, "高度恐慌"),     # 30-40 熊市、流动性危机初期
    (60, "极度恐慌"),     # 40-60 金融危机、流行病
    (101, "系统性崩溃"),  # > 60 2008/2020/2024 级别
]


def classify_ivix_level(ivix_value: float) -> str:
    """根据 iVIX 绝对值判定恐慌等级（方法论研报 §1.1 VIX 等级表 7 档）。

    与 CBOE VIX 长期统计 + 历史事件校准对齐：
        < 12      极度自满
        12-18     平静健康
        18-22     略有担忧
        22-30     明显恐慌
        30-40     高度恐慌
        40-60     极度恐慌
        > 60      系统性崩溃

    参数：
        ivix_value: iVIX 绝对值（年化 %）

    返回：
        恐慌等级中文标签；ivix_value 为 NaN 返回 "数据不可用"。
    """
    if pd.isna(ivix_value):
        return "数据不可用"
    for threshold, label in IVIX_LEVELS:
        if ivix_value < threshold:
            return label
    return IVIX_LEVELS[-1][1]


# ── Layer 3：涨跌停行为代理（与方法论文档 §2.3 对齐）──────────────────


def analyze_limit_behavior(
    limit_up_count: int,
    broken_count: int,
    limit_down_count: int,
) -> dict[str, Any]:
    """涨跌停行为代理分析（Layer 3，方法论研报 §2.3）。

    A 股有 ±10%/±20% 涨跌停制度，这是美股没有的特征。涨跌停数据反映
    散户情绪极端化程度，是 RV（Layer 1）的重要补充——尤其能修正
     "跌停锁死=低波假象"的 RV 失真。

    方法论文档 §2.3 实证发现：炸板数与 RV20 相关性最强（+0.263），
    跌停占比几乎无关（+0.026，因跌停锁死后无法交易=流动性冻结=假性低波）。

    参数：
        limit_up_count: 涨停数
        broken_count: 炸板数（打开过涨停/跌停的股票）
        limit_down_count: 跌停数

    返回：
        {
            "limit_up", "broken", "limit_down",
            "up_down_ratio": 涨跌停比（>3 偏热, <0.5 偏冷）,
            "broken_rate": 炸板率（>30% 多空分歧极端）,
            "down_ratio": 跌停占比（>50% 系统性恐慌抛售）,
            "behavior_signal": 行为信号定性,
        }
    """
    total_up_broken = limit_up_count + broken_count
    total_up_down = limit_up_count + limit_down_count

    up_down_ratio = round(limit_up_count / limit_down_count, 2) if limit_down_count > 0 else float("inf")
    broken_rate = round(broken_count / total_up_broken * 100, 1) if total_up_broken > 0 else 0.0
    down_ratio = round(limit_down_count / total_up_down * 100, 1) if total_up_down > 0 else 0.0

    # 行为信号判定（方法论文档 §2.3 阈值）
    signals: list[str] = []
    if up_down_ratio < 0.5:
        signals.append("恐慌性抛售（涨跌停比<0.5）")
    elif up_down_ratio > 3:
        signals.append("恐慌性追涨/FOMO（涨跌停比>3）")

    if broken_rate > 30:
        signals.append(f"多空分歧极端（炸板率{broken_rate:.0f}%>30%）")
    elif broken_rate < 10 and total_up_broken > 10:
        signals.append("单边市（炸板率低，倾向一致预期）")

    if down_ratio > 50:
        signals.append(f"系统性恐慌（跌停占比{down_ratio:.0f}%>50%）")

    if not signals:
        signals.append("行为指标正常")

    return {
        "limit_up": limit_up_count,
        "broken": broken_count,
        "limit_down": limit_down_count,
        "up_down_ratio": up_down_ratio if up_down_ratio != float("inf") else None,
        "broken_rate": broken_rate,
        "down_ratio": down_ratio,
        "behavior_signal": "；".join(signals),
    }


# ── 单指数分析（Layer 1+2）───────────────────────────────────────────


def analyze_single_index(ts_code: str, days: int = 1300) -> dict[str, Any]:
    """分析单个指数的波动率与历史分位。

    参数：
        ts_code: 指数代码
        days: 回溯交易日数（默认 1300 ≈ 5 年）

    返回：
        {
            "ts_code", "name",
            "rv20", "rv60",             # 当前 RV（年化 %）
            "rv20_pct", "rv60_pct",     # 历史分位（0-100）
            "panic_level",              # 恐慌等级（基于 rv20_pct）
            "latest_date", "close",
            "status",                   # success / 数据不可用
        }
    """
    name = INDEX_NAMES.get(ts_code, ts_code)
    close_df = fetch_index_history(ts_code, days=days)
    if close_df.empty:
        return {
            "ts_code": ts_code,
            "name": name,
            "status": "数据不可用",
            "error": "fetch_index_history returned empty",
        }

    close = close_df["close"]
    rv20_series = realized_vol(close, window=20)
    rv60_series = realized_vol(close, window=60)

    rv20 = float(rv20_series.iloc[-1])
    rv60 = float(rv60_series.iloc[-1])
    rv20_pct = percentile_rank(rv20, rv20_series)
    rv60_pct = percentile_rank(rv60, rv60_series)

    return {
        "ts_code": ts_code,
        "name": name,
        "status": "success",
        "latest_date": close.index[-1].strftime("%Y-%m-%d"),
        "close": round(float(close.iloc[-1]), 2),
        "rv20": rv20,
        "rv60": rv60,
        "rv20_pct": rv20_pct,
        "rv60_pct": rv60_pct,
        "panic_level": classify_panic_level(rv20_pct),
    }


# ── 市场全局 IV/RV 分析（Layer 5）────────────────────────────────────


def analyze_iv_rv_basis(
    ivix_series: pd.Series,
    rv50_current: float,
    days: int = 1300,
) -> dict[str, Any]:
    """计算 iVIX 隐含波动率、历史分位与 V/R 比率（市场全局层）。

    参数：
        ivix_series: iVIX 历史 close 序列（fetch_ivix_history 返回值）
        rv50_current: 上证 50 近似 RV20（用于 V/R 比率分母）
        days: 分位计算窗口（仅用于标记，实际用全序列）

    返回：
        {
            "ivix_current", "ivix_pct", "ivix_panic_level",
            "rv_sse_approx", "vr_ratio",
            "latest_date", "status",
        }
    """
    if ivix_series.empty:
        return {
            "status": "数据不可用",
            "error": "fetch_ivix_history returned empty",
        }

    ivix_current = float(ivix_series.iloc[-1])
    ivix_pct = percentile_rank(ivix_current, ivix_series)
    vr = vr_ratio(ivix_current, rv50_current)

    # iVIX 恐慌等级阈值（与方法论研报 §1.1 VIX 等级表 7 档对齐，2026-07-08 统一）
    ivix_level = classify_ivix_level(ivix_current)

    return {
        "status": "success",
        "latest_date": ivix_series.index[-1].strftime("%Y-%m-%d"),
        "ivix_current": round(ivix_current, 2),
        "ivix_pct": ivix_pct,
        "ivix_panic_level": ivix_level,
        "rv_sse_approx": round(rv50_current, 2) if not pd.isna(rv50_current) else None,
        "vr_ratio": vr if not pd.isna(vr) else None,
    }


# ── Summary 文本生成（纯统计，无 AI）─────────────────────────────────


def _build_summary(
    indices_results: dict[str, dict],
    market: dict,
    limit_behavior: dict | None = None,
) -> str:
    """生成整体波动率定性摘要（纯事实陈述，不含行动建议）。

    2026-07-08 修正：
    1. 修复排序文案错乱（原 coldest/hottest 与"最恐慌/最平静"语义反了）
    2. 去掉"关注左侧机会""分批建仓"等行动建议——数据层只出事实，
       行动建议交由 decision-matrix 在人工判断后输出
    3. 2026-07-08 新增 Layer 3：涨跌停行为代理（联读 limit_up，§2.3）
    """
    ok = [r for r in indices_results.values() if r.get("status") == "success"]
    if not ok:
        return "波动率数据全部不可用"

    # 按分位升序排序，分位最高 = 最恐慌，分位最低 = 最平静
    by_pct = sorted(
        [r for r in ok if not pd.isna(r.get("rv20_pct"))],
        key=lambda r: r["rv20_pct"],
    )
    if not by_pct:
        return "波动率分位数据不可用"

    least_panic = by_pct[0]   # 分位最低 = 最平静
    most_panic = by_pct[-1]   # 分位最高 = 最恐慌
    panic_n = sum(1 for r in by_pct if r["rv20_pct"] >= 90)

    parts = [
        f"最平静：{least_panic['name']} P{least_panic['rv20_pct']:.0f}（RV20={least_panic['rv20']:.1f}%，{least_panic['panic_level']}）",
        f"最恐慌：{most_panic['name']} P{most_panic['rv20_pct']:.0f}（RV20={most_panic['rv20']:.1f}%，{most_panic['panic_level']}）",
    ]

    if panic_n > 0:
        parts.append(f"{panic_n}/{len(by_pct)} 指数处于 P90+ 恐慌区")
        # 只陈述恐慌结构类型（系统性 vs 结构性），不给行动建议
        # 系统性判定标准：宽基（上证/沪深300）≥1 个 P90+，或 ≥4/5 指数 P90+
        sse_panic = any(r["name"] == "上证指数" and r.get("rv20_pct", 0) >= 90 for r in by_pct)
        hs300_panic = any(r["name"] == "沪深300" and r.get("rv20_pct", 0) >= 90 for r in by_pct)
        if (sse_panic or hs300_panic) or panic_n >= 4:
            parts.append("属系统性恐慌（宽基承压）")
        else:
            parts.append("属结构性恐慌（恐慌集中在部分指数）")
    else:
        parts.append("无指数处于 P90+ 恐慌区")

    # 市场层（iVIX + V/R）——只陈述事实，不附交易含义
    if market.get("status") == "success":
        ivix = market.get("ivix_current")
        vr = market.get("vr_ratio")
        parts.append(f"iVIX={ivix:.1f}（{market.get('ivix_panic_level')}）")
        if vr is not None:
            parts.append(
                f"V/R={vr:.2f}（{'期权偏贵' if vr > 1.1 else '定价合理' if vr > 0.9 else '期权便宜'}）"
            )

    # Layer 3：涨跌停行为代理（联读 limit_up，方法论文档 §2.3）
    if limit_behavior and limit_behavior.get("status") == "success":
        parts.append(f"行为面：{limit_behavior.get('behavior_signal', '')}")

    return "，".join(parts)


def _fetch_and_analyze_limits(date: str) -> dict[str, Any]:
    """Layer 3：从 DB 读取 limit_up 数据，算涨跌停行为代理指标。

    联读 daily_data 表的 limit_up_pool / broken_pool / limit_down_pool，
    计算：涨跌停比、炸板率、跌停占比、行为信号（方法论文档 §2.3）。

    返回：
        {
            "status": "success" / "数据不可用",
            "limit_up", "broken", "limit_down",
            "up_down_ratio", "broken_rate", "down_ratio",
            "behavior_signal",
        }
    """
    try:
        from stockhot.storage.database import get_daily_data
        data = get_daily_data(date)
        lu = data.get("limit_up_pool") or []
        bp = data.get("broken_pool") or []
        ld = data.get("limit_down_pool") or []
        if not lu and not bp and not ld:
            return {"status": "数据不可用", "reason": "limit_up 未采集"}
        result = analyze_limit_behavior(len(lu), len(bp), len(ld))
        result["status"] = "success"
        logger.info(
            f"  Layer3 行为: 涨停{len(lu)}/炸板{len(bp)}/跌停{len(ld)} "
            f"→ {result['behavior_signal']}"
        )
        return result
    except Exception as e:
        return {"status": "数据不可用", "reason": f"读取失败: {e}"}


# ── 与 index_technical 联动（双确认信号）─────────────────────────────


# 技术面阶段 → 是否算"价格极端"（与波动率 P90+ 叠加才算双确认）
# 方法论文档 §7.2：RV≥P90（情绪极端）+ 技术面超跌阶段（价格极端）= 高概率反弹区
_PANIC_TECHNICAL_STAGES = {"主跌浪", "低位筑底", "下跌中反弹"}
_OVERHEAT_TECHNICAL_STAGES = {"主升浪", "高位震荡筑顶"}


def _cross_check_technical(date: str, indices_results: dict[str, dict]) -> dict[str, Any]:
    """读当日 index_technical 数据，输出波动率 × 技术面双确认信号。

    ⚠️ **2026-07-08 实证修正**：方法论文档 §7.2 原假设"RV≥P90 + 技术面超跌
    = 高概率反弹区"已被事件研究回测证伪（双确认胜率 54.5% < 单 RV 85.7%）。
    现语义调整为：双确认 = **趋势仍在下行，风险更高**（非机会信号）。

    正确用法（方法论 §5.3）：RV≥P90 是关注信号，但不要因技术面超跌就加仓；
    应等待政策底（降准/降息/重要会议）+ RV 见顶回落确认。

    返回：
        {
            "status": "success" / "数据不可用",
            "panic_confirmed": [...],   # RV≥P90 + 技术面超跌（风险信号，非机会）
            "overheat_confirmed": [...], # RV≤P10 + 技术面过热（见顶风险）
            "summary": "...",
        }
    """
    try:
        from stockhot.storage.database import get_daily_data
        tech_data = get_daily_data(date).get("index_technical")
        if not tech_data or tech_data.get("status") != "success":
            return {"status": "数据不可用", "reason": "index_technical 未采集"}
    except Exception as e:
        return {"status": "数据不可用", "reason": f"读取失败: {e}"}

    tech_indices = tech_data.get("indices", {})

    panic_confirmed: list[dict] = []
    overheat_confirmed: list[dict] = []
    for ts_code, vol_r in indices_results.items():
        if vol_r.get("status") != "success":
            continue
        tech_r = tech_indices.get(ts_code, {})
        if tech_r.get("status") != "success":
            continue
        rv_pct = vol_r.get("rv20_pct")
        stage = tech_r.get("stage", "")
        entry = {
            "ts_code": ts_code,
            "name": vol_r.get("name"),
            "rv20_pct": rv_pct,
            "stage": stage,
            "technical_score": tech_r.get("technical_score"),
        }
        # 恐慌双确认：RV≥P90 + 技术面超跌阶段
        if rv_pct is not None and rv_pct >= 90 and stage in _PANIC_TECHNICAL_STAGES:
            panic_confirmed.append(entry)
        # 自满双确认：RV≤P10 + 技术面过热阶段
        elif rv_pct is not None and rv_pct <= 10 and stage in _OVERHEAT_TECHNICAL_STAGES:
            overheat_confirmed.append(entry)

    parts: list[str] = []
    if panic_confirmed:
        names = "、".join(e["name"] for e in panic_confirmed)
        # 2026-07-08 实证修正：双确认=趋势仍在下行（风险信号），非反弹机会
        parts.append(f"恐慌双确认（{names}：RV≥P90 + 技术面超跌，趋势仍下行，风险更高）")
    if overheat_confirmed:
        names = "、".join(e["name"] for e in overheat_confirmed)
        parts.append(f"见顶风险双确认（{names}：RV≤P10 + 技术面过热）")
    if not parts:
        parts.append("无双确认信号（波动率与技术面未极端共振）")

    return {
        "status": "success",
        "panic_confirmed": panic_confirmed,
        "overheat_confirmed": overheat_confirmed,
        "summary": "；".join(parts),
    }


# ── 入口（符合 run_*_analysis(date) -> dict 约定）────────────────────


def run_volatility_analysis(
    date: str | None = None,
    indices: list[str] | None = None,
    days: int = 1300,
) -> dict[str, Any]:
    """波动率分析入口（供 daily-market-scan Wave 2 调用）。

    参数：
        date: 日期字符串 YYYY-MM-DD（仅用于结果标记，实际取最新数据）
        indices: 指数代码列表，None 则用 DEFAULT_INDICES
        days: 回溯交易日数（默认 1300 ≈ 5 年）

    返回：
        {
            "date": "2026-07-06",
            "status": "success" / "no_data",
            "indices": {ts_code: {单指数结果}},
            "market": {iVIX/V/R 全局结果},
            "summary": "最恐慌：... 最平静：... iVIX=... V/R=...",
        }

    单个指数失败不影响其他指数（遵循 daily-market-scan 的 try/except 隔离原则）。
    成功后通过 ``save_daily_data({"date": date, "volatility": result})`` 持久化。
    """
    indices = indices or DEFAULT_INDICES
    date = date or datetime.now().strftime("%Y-%m-%d")

    logger.info(f"run_volatility_analysis: date={date}, indices={indices}, days={days}")

    indices_results: dict[str, dict] = {}
    for ts_code in indices:
        try:
            result = analyze_single_index(ts_code, days=days)
            indices_results[ts_code] = result
            if result.get("status") == "success":
                logger.info(
                    f"  {ts_code} ({result['name']}): "
                    f"RV20={result['rv20']:.1f}% P{result['rv20_pct']:.0f} ({result['panic_level']})"
                )
            else:
                logger.warning(f"  {ts_code} 数据不可用: {result.get('error')}")
        except Exception as e:
            indices_results[ts_code] = {
                "ts_code": ts_code,
                "name": INDEX_NAMES.get(ts_code, ts_code),
                "status": "数据不可用",
                "error": f"{type(e).__name__}: {e}",
            }
            logger.error(f"  {ts_code} 分析失败: {type(e).__name__}: {e}")

    # Layer 5：iVIX + V/R
    # V/R 分母用上证指数 RV20（最接近上证 50ETF 的免费代理——A 股无上证 50
    # 指数代码，上证综指以大盘蓝筹为主，比沪深 300 含成长股更贴近 50ETF）。
    # 2026-07-08 修正：此前用沪深 300 RV20(24.9) 导致 V/R 被压低至 0.82，
    # 改用上证 RV20(16.9) 后 V/R=1.21，与方法论文档 1.15-1.35 吻合。
    market: dict[str, Any] = {"status": "数据不可用"}
    try:
        ivix_series = fetch_ivix_history(days=days)
        rv_sse = indices_results.get("000001.SH", {}).get("rv20")
        rv_sse = rv_sse if rv_sse and not pd.isna(rv_sse) else float("nan")
        market = analyze_iv_rv_basis(ivix_series, rv_sse, days=days)
        if market.get("status") == "success":
            logger.info(
                f"  iVIX={market['ivix_current']:.1f} P{market['ivix_pct']:.0f} "
                f"V/R={market.get('vr_ratio')}"
            )
    except Exception as e:
        market = {"status": "数据不可用", "error": f"{type(e).__name__}: {e}"}
        logger.error(f"  iVIX/V/R 分析失败: {type(e).__name__}: {e}")

    # Layer 3：涨跌停行为代理（联读 limit_up 数据，方法论 §2.3）
    limit_behavior = _fetch_and_analyze_limits(date)

    summary = _build_summary(indices_results, market, limit_behavior)
    success_n = sum(1 for r in indices_results.values() if r.get("status") == "success")

    # ── 与 index_technical 联动：双确认信号 ──
    # 方法论文档 §7.2：RV≥P90（情绪极端）+ 技术面"主跌浪/低位筑底"（价格极端）
    # = 高概率反弹区。单独 RV 高不触发信号。
    cross_signal = _cross_check_technical(date, indices_results)

    result: dict[str, Any] = {
        "date": date,
        "status": "success" if success_n > 0 else "no_data",
        "indices": indices_results,
        "market": market,
        "limit_behavior": limit_behavior,
        "cross_signal": cross_signal,
        "summary": summary,
    }

    # 持久化（至少 1 个指数成功才入库）
    if success_n > 0:
        try:
            save_daily_data({"date": date, "volatility": result})
            logger.info(f"  持久化 volatility → daily_data[{date}]")
        except Exception as e:
            logger.error(f"  持久化失败: {type(e).__name__}: {e}")

    return result
