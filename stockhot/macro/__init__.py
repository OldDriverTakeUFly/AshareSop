"""Macroeconomic indicators module for stockhot.

Fetches key macro indicators from Tushare and derives a macro prosperity
score that feeds into the after-hours-review skill. All data is read-only
from Tushare; no AKShare / scraping involved.

Indicators collected:
- PMI (制造业采购经理指数)            → cn_pmi
- CPI / PPI (通胀)                   → cn_cpi / cn_ppi
- M0 / M1 / M2 (货币供应)            → cn_m
- Shibor (银行间流动性)              → shibor
- LPR (贷款基准利率)                 → shibor_lpr
- 社会融资 (由 M2 + 新增贷款代理)     → cn_m + loan

The macro prosperity score combines these into a single 0-100 reading that
characterises whether the macro backdrop is expansionary or contractionary
for equity markets.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts
from dotenv import load_dotenv

from stockhot.core.logging import logger


def _get_pro_api():
    """Return the unified TushareGateway (replaces ts.pro_api SDK calls).

    2026-07-15 统一架构调整：改用 stockhot.data_layer 的统一网关，
    走新端点 api.tushare.pro/dataapi（绕过旧版 waditu.com），并复用
    gateway 的线程安全限频 + 错误分类。__getattr__ 代理让 ``gw.cn_pmi(...)``
    风格的代码无需改动。
    """
    try:
        from stockhot.data_layer import get_gateway
        return get_gateway()
    except Exception as e:
        logger.warning(f"macro: gateway init failed: {e}")
        return None


def _latest_month_str(lookback: int = 12) -> str:
    """Return YYYYMM for the current month minus lookback."""
    return (datetime.now() - timedelta(days=lookback * 31)).strftime("%Y%m")


def fetch_pmi(pro, lookback_months: int = 12) -> pd.DataFrame:
    """制造业 PMI 历史序列. PMI020100 is 制造业 PMI (headline number)."""
    start = _latest_month_str(lookback_months)
    df = pro.cn_pmi(start_m=start)
    if df is None or df.empty:
        return pd.DataFrame(columns=["month", "pmi"])
    out = pd.DataFrame({
        "month": df["MONTH"],
        "pmi": pd.to_numeric(df["PMI020100"], errors="coerce"),
    })
    return out.dropna(subset=["pmi"]).sort_values("month").reset_index(drop=True)


def fetch_cpi(pro, lookback_months: int = 12) -> pd.DataFrame:
    """CPI 同比/环比序列."""
    start = _latest_month_str(lookback_months)
    df = pro.cn_cpi(start_m=start)
    if df is None or df.empty:
        return pd.DataFrame(columns=["month", "cpi_yoy", "cpi_mom"])
    return pd.DataFrame({
        "month": df["month"],
        "cpi_yoy": pd.to_numeric(df["nt_yoy"], errors="coerce"),
        "cpi_mom": pd.to_numeric(df["nt_mom"], errors="coerce"),
    }).sort_values("month").reset_index(drop=True)


def fetch_ppi(pro, lookback_months: int = 12) -> pd.DataFrame:
    """PPI 同比序列."""
    start = _latest_month_str(lookback_months)
    df = pro.cn_ppi(start_m=start)
    if df is None or df.empty:
        return pd.DataFrame(columns=["month", "ppi_yoy"])
    return pd.DataFrame({
        "month": df["month"],
        "ppi_yoy": pd.to_numeric(df["ppi_yoy"], errors="coerce"),
    }).sort_values("month").reset_index(drop=True)


def fetch_money_supply(pro, lookback_months: int = 12) -> pd.DataFrame:
    """M0/M1/M2 同比序列."""
    start = _latest_month_str(lookback_months)
    df = pro.cn_m(start_m=start)
    if df is None or df.empty:
        return pd.DataFrame(columns=["month", "m0_yoy", "m1_yoy", "m2_yoy"])
    return pd.DataFrame({
        "month": df["month"],
        "m0_yoy": pd.to_numeric(df["m0_yoy"], errors="coerce"),
        "m1_yoy": pd.to_numeric(df["m1_yoy"], errors="coerce"),
        "m2_yoy": pd.to_numeric(df["m2_yoy"], errors="coerce"),
    }).sort_values("month").reset_index(drop=True)


def fetch_shibor(pro, days: int = 30) -> pd.DataFrame:
    """Shibor 利率序列 (隔夜 / 1周 / 1月 / 1年)."""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    df = pro.shibor(start_date=start, end_date=end)
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "on", "1w", "1m", "1y"])
    keep = ["date", "on", "1w", "1m", "1y"]
    return df[[c for c in keep if c in df.columns]].sort_values("date").reset_index(drop=True)


def fetch_lpr(pro, lookback_months: int = 12) -> pd.DataFrame:
    """LPR 贷款市场报价利率 (1年 / 5年)."""
    start = (datetime.now() - timedelta(days=lookback_months * 31)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    df = pro.shibor_lpr(start_date=start, end_date=end)
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "1y", "5y"])
    return df.sort_values("date").reset_index(drop=True)


@dataclass
class MacroSnapshot:
    """A point-in-time snapshot of all macro indicators + derived score."""
    pmi: float | None = None
    pmi_month: str = ""
    cpi_yoy: float | None = None
    ppi_yoy: float | None = None
    inflation_month: str = ""
    m1_yoy: float | None = None
    m2_yoy: float | None = None
    money_month: str = ""
    shibor_on: float | None = None
    shibor_1y: float | None = None
    lpr_1y: float | None = None
    lpr_5y: float | None = None
    cpi_ppi_scissors: float | None = None
    m1_m2_gap: float | None = None
    prosperity_score: float = 50.0
    prosperity_label: str = "中性"
    signals: list[str] = field(default_factory=list)


def collect_macro_snapshot(pro=None) -> MacroSnapshot:
    """Collect all macro indicators and compute the prosperity score."""
    if pro is None:
        pro = _get_pro_api()
    if pro is None:
        return MacroSnapshot(signals=["TUSHARE_TOKEN 未配置，宏观数据不可用"])

    snap = MacroSnapshot()

    try:
        pmi_df = fetch_pmi(pro)
        if not pmi_df.empty:
            latest = pmi_df.iloc[-1]
            snap.pmi = latest["pmi"]
            snap.pmi_month = str(int(latest["month"]))
    except Exception as e:
        logger.warning(f"macro: PMI fetch failed: {e}")

    try:
        cpi_df = fetch_cpi(pro)
        ppi_df = fetch_ppi(pro)
        if not cpi_df.empty:
            snap.cpi_yoy = cpi_df.iloc[-1]["cpi_yoy"]
            snap.inflation_month = str(cpi_df.iloc[-1]["month"])
        if not ppi_df.empty:
            snap.ppi_yoy = ppi_df.iloc[-1]["ppi_yoy"]
            if not snap.inflation_month:
                snap.inflation_month = str(ppi_df.iloc[-1]["month"])
    except Exception as e:
        logger.warning(f"macro: CPI/PPI fetch failed: {e}")

    try:
        m_df = fetch_money_supply(pro)
        if not m_df.empty:
            latest = m_df.iloc[-1]
            snap.m1_yoy = latest["m1_yoy"]
            snap.m2_yoy = latest["m2_yoy"]
            snap.money_month = str(int(latest["month"]))
    except Exception as e:
        logger.warning(f"macro: money supply fetch failed: {e}")

    try:
        shib_df = fetch_shibor(pro)
        if not shib_df.empty:
            snap.shibor_on = shib_df.iloc[-1].get("on")
            snap.shibor_1y = shib_df.iloc[-1].get("1y")
    except Exception as e:
        logger.warning(f"macro: Shibor fetch failed: {e}")

    try:
        lpr_df = fetch_lpr(pro)
        if not lpr_df.empty:
            snap.lpr_1y = lpr_df.iloc[-1].get("1y")
            snap.lpr_5y = lpr_df.iloc[-1].get("5y")
    except Exception as e:
        logger.warning(f"macro: LPR fetch failed: {e}")

    score, signals = _compute_prosperity_score(snap)
    snap.prosperity_score = score
    snap.prosperity_label = _score_label(score)
    snap.signals = signals

    if snap.cpi_yoy is not None and snap.ppi_yoy is not None:
        snap.cpi_ppi_scissors = snap.cpi_yoy - snap.ppi_yoy
    if snap.m1_yoy is not None and snap.m2_yoy is not None:
        snap.m1_m2_gap = snap.m1_yoy - snap.m2_yoy

    return snap


def _compute_prosperity_score(snap: MacroSnapshot) -> tuple[float, list[str]]:
    """Compute a 0-100 macro prosperity score from the snapshot.

    Weights (total 100):
    - PMI          35 pts  (50 breakeven, >50 expansionary)
    - M1-M2 gap    20 pts  (positive = active corporate spending)
    - M2 growth    15 pts  (high = loose liquidity)
    - CPI-PPI gap  15 pts  (wide positive = consumer demand recovery)
    - Shibor/LPR   15 pts  (low rates = accommodative)
    """
    score = 0.0
    signals: list[str] = []

    # PMI (35 pts): 47→0, 50→17.5, 53→35
    if snap.pmi is not None:
        pmi_pts = max(0, min(35, (snap.pmi - 47) / 6 * 35))
        score += pmi_pts
        if snap.pmi >= 51:
            signals.append(f"PMI {snap.pmi:.1f} 处于扩张区间（>51），制造业景气偏强")
        elif snap.pmi >= 50:
            signals.append(f"PMI {snap.pmi:.1f} 略高于荣枯线，制造业温和扩张")
        else:
            signals.append(f"PMI {snap.pmi:.1f} 低于荣枯线 50，制造业仍处收缩")
    else:
        score += 17.5  # neutral default

    # M1-M2 gap (20 pts): -5→0, 0→10, +5→20
    if snap.m1_yoy is not None and snap.m2_yoy is not None:
        gap = snap.m1_yoy - snap.m2_yoy
        gap_pts = max(0, min(20, (gap + 5) / 10 * 20))
        score += gap_pts
        if gap > 0:
            signals.append(f"M1({snap.m1_yoy:.1f}%)-M2({snap.m2_yoy:.1f}%) 剪刀差 +{gap:.1f}pp，企业活期存款增速快，资金活化度高")
        else:
            signals.append(f"M1({snap.m1_yoy:.1f}%)-M2({snap.m2_yoy:.1f}%) 剪刀差 {gap:.1f}pp，资金倾向定期储蓄，活化度偏低")
    else:
        score += 10

    # M2 growth (15 pts): 6%→3, 9%→9, 12%→15
    if snap.m2_yoy is not None:
        m2_pts = max(3, min(15, (snap.m2_yoy - 6) / 6 * 12 + 3))
        score += m2_pts
    else:
        score += 9

    # CPI-PPI scissors (15 pts): -3→0, 0→7.5, +5→15
    if snap.cpi_yoy is not None and snap.ppi_yoy is not None:
        scissors = snap.cpi_yoy - snap.ppi_yoy
        sc_pts = max(0, min(15, (scissors + 3) / 8 * 15))
        score += sc_pts
        if abs(scissors) > 2:
            signals.append(f"CPI({snap.cpi_yoy:.1f}%)-PPI({snap.ppi_yoy:.1f}%) 剪刀差 {scissors:+.1f}pp，{'消费需求强于生产端' if scissors > 0 else '工业品价格承压'}")
    else:
        score += 7.5

    # Shibor/LPR (15 pts): LPR 3.5→7, 3.0→12, 2.5→15
    if snap.lpr_1y is not None:
        lpr_pts = max(0, min(15, (3.5 - snap.lpr_1y) / 1.0 * 8 + 7))
        score += lpr_pts
    else:
        score += 8

    score = max(0, min(100, score))
    return round(score, 1), signals


def _score_label(score: float) -> str:
    """Map a 0-100 score to a qualitative label."""
    if score >= 70:
        return "扩张（利好权益）"
    elif score >= 55:
        return "温和扩张"
    elif score >= 45:
        return "中性"
    elif score >= 30:
        return "偏弱"
    else:
        return "收缩（利空权益）"


def format_macro_section(snap: MacroSnapshot) -> str:
    """Render the MacroSnapshot as a markdown section for the post-market report."""
    lines = [
        f"## 宏观景气度背景\n",
        f"> **综合宏观景气度评分：{snap.prosperity_score}/100 — {snap.prosperity_label}**\n",
        f"> 数据来源：Tushare 宏观接口（PMI/CPI/PPI/M2/Shibor/LPR），数据月份以最新公布为准\n",
    ]

    lines.append("### 核心宏观指标\n")
    lines.append("| 指标 | 最新值 | 月份 | 含义 |")
    lines.append("|------|:---:|:---:|------|")
    if snap.pmi is not None:
        lines.append(f"| **制造业 PMI** | **{snap.pmi:.1f}** | {snap.pmi_month} | {'扩张（>50）' if snap.pmi >= 50 else '收缩（<50）'} |")
    if snap.cpi_yoy is not None:
        lines.append(f"| CPI 同比 | {snap.cpi_yoy:.1f}% | {snap.inflation_month} | 居民消费价格 |")
    if snap.ppi_yoy is not None:
        lines.append(f"| PPI 同比 | {snap.ppi_yoy:.1f}% | {snap.inflation_month} | 工业生产者价格 |")
    if snap.cpi_ppi_scissors is not None:
        lines.append(f"| CPI-PPI 剪刀差 | {snap.cpi_ppi_scissors:+.1f}pp | — | 上下游需求温差 |")
    if snap.m1_yoy is not None:
        lines.append(f"| M1 同比 | {snap.m1_yoy:.1f}% | {snap.money_month} | 企业活期存款（资金活化度） |")
    if snap.m2_yoy is not None:
        lines.append(f"| M2 同比 | {snap.m2_yoy:.1f}% | {snap.money_month} | 广义货币（流动性总量） |")
    if snap.m1_m2_gap is not None:
        lines.append(f"| M1-M2 增速差 | {snap.m1_m2_gap:+.1f}pp | — | {'资金活化' if snap.m1_m2_gap > 0 else '资金沉淀'} |")
    if snap.shibor_on is not None:
        lines.append(f"| Shibor 隔夜 | {snap.shibor_on:.3f}% | 最新 | 银行间超短期流动性 |")
    if snap.shibor_1y is not None:
        lines.append(f"| Shibor 1年 | {snap.shibor_1y:.3f}% | 最新 | 银行间中期利率 |")
    if snap.lpr_1y is not None:
        lines.append(f"| LPR 1年 | {snap.lpr_1y:.1f}% | 最新 | 贷款基准利率 |")
    if snap.lpr_5y is not None:
        lines.append(f"| LPR 5年 | {snap.lpr_5y:.1f}% | 最新 | 中长期贷款利率（房贷锚） |")
    lines.append("")

    if snap.signals:
        lines.append("### 信号解读\n")
        for s in snap.signals:
            lines.append(f"- {s}")
        lines.append("")

    lines.append("### 评分构成\n")
    lines.append(
        f"宏观景气度综合评分 {snap.prosperity_score}/100 "
        f"({snap.prosperity_label})，由 5 个维度加权：\n"
        f"- PMI（35%）：制造业景气度的核心先行指标\n"
        f"- M1-M2 增速差（20%）：企业资金活化程度\n"
        f"- M2 增速（15%）：整体流动性松紧\n"
        f"- CPI-PPI 剪刀差（15%）：上下游需求温差\n"
        f"- LPR/Shibor 利率（15%）：货币政策宽松度\n"
    )

    return "\n".join(lines)
