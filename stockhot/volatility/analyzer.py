"""Volatility analyzer — 五层观察体系的 Layer 1/2/5 计算 + run_volatility_analysis 入口。

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
            "rv50_approx", "vr_ratio",
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

    # iVIX 恐慌等级阈值（与方法论研报 §1.1 VIX 等级对齐，绝对值而非分位）
    if ivix_current < 15:
        ivix_level = "平静"
    elif ivix_current < 22:
        ivix_level = "正常"
    elif ivix_current < 30:
        ivix_level = "偏高"
    elif ivix_current < 40:
        ivix_level = "明显恐慌"
    else:
        ivix_level = "极度恐慌"

    return {
        "status": "success",
        "latest_date": ivix_series.index[-1].strftime("%Y-%m-%d"),
        "ivix_current": round(ivix_current, 2),
        "ivix_pct": ivix_pct,
        "ivix_panic_level": ivix_level,
        "rv50_approx": round(rv50_current, 2) if not pd.isna(rv50_current) else None,
        "vr_ratio": vr if not pd.isna(vr) else None,
    }


# ── Summary 文本生成（纯统计，无 AI）─────────────────────────────────


def _build_summary(indices_results: dict[str, dict], market: dict) -> str:
    """生成整体波动率定性摘要（纯统计拼接）。"""
    ok = [r for r in indices_results.values() if r.get("status") == "success"]
    if not ok:
        return "波动率数据全部不可用"

    # 找最恐慌与最平静
    by_pct = sorted(
        [r for r in ok if not pd.isna(r.get("rv20_pct"))],
        key=lambda r: r["rv20_pct"],
    )
    if not by_pct:
        return "波动率分位数据不可用"

    coldest = by_pct[0]
    hottest = by_pct[-1]
    panic_n = sum(1 for r in by_pct if r["rv20_pct"] >= 90)

    parts = [
        f"最恐慌：{coldest['name']} RV20={coldest['rv20']:.1f}%（P{coldest['rv20_pct']:.0f}，{coldest['panic_level']}）",
        f"最平静：{hottest['name']} RV20={hottest['rv20']:.1f}%（P{hottest['rv20_pct']:.0f}，{hottest['panic_level']}）",
    ]

    if panic_n > 0:
        parts.append(f"{panic_n}/{len(by_pct)} 指数处于 P90+ 恐慌区")
        if panic_n >= len(by_pct) - 1:
            parts.append("系统性恐慌，关注左侧机会")
        else:
            parts.append("结构性恐慌，关注风格切换")
    else:
        parts.append("无指数处于恐慌区")

    # 市场层（iVIX + V/R）
    if market.get("status") == "success":
        ivix = market.get("ivix_current")
        vr = market.get("vr_ratio")
        parts.append(f"iVIX={ivix:.1f}（{market.get('ivix_panic_level')}）")
        if vr is not None:
            parts.append(
                f"V/R={vr:.2f}（{'期权偏贵' if vr > 1.1 else '定价合理' if vr > 0.9 else '期权便宜'}）"
            )

    return "，".join(parts)


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

    # Layer 5：iVIX + V/R（用上证 50 的 RV 近似——A 股无 50 指数代码，
    # 用沪深 300 的 RV 作为大盘蓝筹代理）
    market: dict[str, Any] = {"status": "数据不可用"}
    try:
        ivix_series = fetch_ivix_history(days=days)
        # 用沪深 300（最贴近上证 50 的可取指数）的 rv20 作 V/R 分母
        rv300 = indices_results.get("000300.SH", {}).get("rv20")
        rv300 = rv300 if rv300 and not pd.isna(rv300) else float("nan")
        market = analyze_iv_rv_basis(ivix_series, rv300, days=days)
        if market.get("status") == "success":
            logger.info(
                f"  iVIX={market['ivix_current']:.1f} P{market['ivix_pct']:.0f} "
                f"V/R={market.get('vr_ratio')}"
            )
    except Exception as e:
        market = {"status": "数据不可用", "error": f"{type(e).__name__}: {e}"}
        logger.error(f"  iVIX/V/R 分析失败: {type(e).__name__}: {e}")

    summary = _build_summary(indices_results, market)
    success_n = sum(1 for r in indices_results.values() if r.get("status") == "success")

    result: dict[str, Any] = {
        "date": date,
        "status": "success" if success_n > 0 else "no_data",
        "indices": indices_results,
        "market": market,
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
