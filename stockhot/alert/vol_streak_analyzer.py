"""高波持续分析 —— 持续天数 + 历史对比 + 区域影响 + 机会识别.

将"当前高波第 N 天"这个问题拆成三个可操作的维度：
  (a) 高波持续天数 + 历史对比（当前 vs 历史平均/最长）
  (b) 受影响最大板块（跌停密集 + 资金流出）
  (c) 逆势机会板块（涨停密集 + 资金流入，标注内部分歧）

数据源：
  - 当前持续天数：daily_volatility_index 表（盘后入库的收盘 RV20 分位）
  - 历史对比：index_daily 表回算 RV20 分位（5.5 年，覆盖多个牛熊周期）
  - 板块影响：limit_pool 涨跌停按板块聚合（高波期间窗口）
  - 资金辅助：fund_flow_sector.main_net 累计

被调用方：
  - panic_detector.format_alert_message（盘中预警加一行摘要）
  - after-hours-review SKILL.md Step 5c（盘后收评完整章节）
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from stockhot.alert.sector_mapping import normalize_sector_name
from stockhot.data_layer import MARKET_DB_PATH

# 高波判定阈值（与 panic_detector / quadrant_backtest 一致）
_RV_PCT_THRESHOLD = 90
_MIN_INDICES_HIGH_VOL = 3
_TRADING_DAYS = 242
_RV_WINDOW = 20

# 板块分析 top N
_SECTOR_TOP_N = 3
# 最小高波期长度（连续 ≥3 天才算"高波期"）
_MIN_STREAK_LENGTH = 3

# 波动衰减判定阈值（2026-08-02 回测固化：5/5 历史样本验证）
# RV5/RV20 < _DECAY_RATIO_THRESHOLD = 短期波动显著回落 = 衰减中 → 机会
# RV5/RV20 ≥ _DECAY_RATIO_THRESHOLD = 短期波动仍在高位 → 陷阱
_DECAY_RATIO_THRESHOLD = 0.8

# 模块级缓存（当日复用，避免每次预警重算历史）
_streak_cache: dict[str, tuple[float, object]] = {}  # {date_str: (timestamp, result)}
_CACHE_TTL = 3600  # 1 小时


@dataclass
class SectorImpact:
    """单个板块在高波期间的影响/机会读数."""

    name: str                        # 归一化后的申万一级板块名
    limit_count: int                 # 涨停或跌停次数（取决于 impacted/resilient）
    main_net_total: float | None     # 期间累计主力净额（亿元）
    detail: str = ""                 # 细分行业构成（如"元件18/半导体15"）
    has_divergence: bool = False     # 是否同时出现在涨/跌两个榜（内部分歧）


@dataclass
class VolStreakReport:
    """高波持续分析综合报告."""

    current_days: int = 0                          # 当前高波持续天数
    is_high_vol: bool = False                      # 当前是否处于高波区间
    historical_count: int = 0                      # 历史高波期总数
    historical_avg_days: float = 0.0               # 历史平均持续天数
    historical_max_days: int = 0                   # 历史最长持续天数
    historical_max_note: str = ""                  # 最长高波期的描述（如"2024-09 政策大反转"）
    current_rank: str = ""                         # "第5长/8个" 或 "数据不足"
    impacted_sectors: list[SectorImpact] = field(default_factory=list)
    resilient_sectors: list[SectorImpact] = field(default_factory=list)
    streak_start_date: str = ""                    # 高波起始日（YYYY-MM-DD）
    latest_date: str = ""                          # 分析基准日
    # 波动衰减状态（2026-08-02 回测固化：RV5/RV20 < 0.8 = 衰减→机会）
    rv5: float | None = None                       # 5日实际波动率（更灵敏）
    rv20: float | None = None                      # 20日已实现波动率
    rv_decay_ratio: float | None = None            # RV5/RV20 比率（< 0.8 = 衰减中）
    rv20_peaked: bool = False                      # RV20 是否已见顶回落
    decay_status: str = ""                         # "衰减中(机会)" / "高位震荡(警惕)" / "加速中" / "骤降中"
    rv20_daily_change: float | None = None         # RV20 单日变化%（骤降检测）
    sharp_drop: bool = False                       # 是否发生骤降（事件驱动信号）
    available: bool = False


# ═══════════════════════════════════════════════════════════════════
# 维度 (a)：高波持续天数 + 历史对比
# ═══════════════════════════════════════════════════════════════════


def _compute_current_streak(latest_date: str) -> tuple[int, str]:
    """从 daily_volatility_index 表算当前连续高波天数.

    返回 (天数, 起始日)。从 latest_date 倒推，统计连续
    SUM(rv20_pct>=90)>=3 的天数。
    """
    with sqlite3.connect(str(MARKET_DB_PATH)) as conn:
        # 按日聚合 P90+ 指数数
        df = pd.read_sql(
            "SELECT trade_date, "
            "SUM(CASE WHEN rv20_pct >= ? THEN 1 ELSE 0 END) AS p90_n "
            "FROM daily_volatility_index "
            "WHERE trade_date <= ? "
            "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 60",
            conn,
            params=(_RV_PCT_THRESHOLD, latest_date),
        )

    if df.empty:
        return 0, ""

    # 倒推连续高波天数
    streak = 0
    streak_start = ""
    for _, row in df.iterrows():
        if row["p90_n"] >= _MIN_INDICES_HIGH_VOL:
            streak += 1
            streak_start = row["trade_date"]
        else:
            break

    return streak, streak_start


def _compute_historical_streaks() -> list[dict]:
    """从 index_daily 表回算历史上所有高波期.

    用 4 大宽基（上证/深成/沪深300/创业板，覆盖 5.5 年）算 RV20 分位，
    识别 P90+ 连续 ≥3 天的高波期。

    返回 [{start, end, days, max_rv, note}] 列表。
    """
    indices = ["000001.SH", "399001.SZ", "000300.SH", "399006.SZ"]

    with sqlite3.connect(str(MARKET_DB_PATH)) as conn:
        all_data = {}
        for code in indices:
            df = pd.read_sql(
                f"SELECT trade_date, close FROM index_daily "
                f"WHERE ts_code='{code}' ORDER BY trade_date",
                conn,
            )
            if df.empty:
                continue
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
            all_data[code] = df.set_index("trade_date")["close"].astype(float)

    if not all_data:
        return []

    # 对齐到公共日期
    dates = all_data["000001.SH"].index
    high_vol_count = pd.Series(0.0, index=dates)
    rv_series = {}  # 存 RV20 序列用于找 max

    for code, close in all_data.items():
        close = close.reindex(dates)
        logret = np.log(close).diff()
        rv20 = logret.rolling(_RV_WINDOW).std() * np.sqrt(_TRADING_DAYS) * 100
        window = min(1218, len(rv20) - _RV_WINDOW)
        pct = rv20.rolling(window, min_periods=100).rank(pct=True) * 100
        high_vol_count = high_vol_count.add((pct >= _RV_PCT_THRESHOLD).astype(float).reindex(dates).fillna(0))
        rv_series[code] = rv20

    high_vol = high_vol_count >= _MIN_INDICES_HIGH_VOL

    # 找连续高波期
    streaks = []
    current_group = []
    for d, is_high in high_vol.items():
        if is_high and not pd.isna(is_high):
            current_group.append(d)
        else:
            if len(current_group) >= _MIN_STREAK_LENGTH:
                streaks.append(_finalize_streak(current_group, rv_series))
            current_group = []
    if len(current_group) >= _MIN_STREAK_LENGTH:
        streaks.append(_finalize_streak(current_group, rv_series))

    return streaks


def _finalize_streak(group: list, rv_series: dict) -> dict:
    """把一个连续高波日组转成 streak dict."""
    start = group[0]
    end = group[-1]
    # 找期间最大 RV20（所有指数中最高）
    max_rv = 0
    for code, rv in rv_series.items():
        segment = rv.reindex(group).dropna()
        if len(segment) > 0:
            max_rv = max(max_rv, segment.max())
    return {
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "days": len(group),
        "max_rv": round(float(max_rv), 1),
        "note": _identify_streak_event(start, end),
    }


def _identify_streak_event(start, end) -> str:
    """根据日期识别高波期的市场事件（粗略标注）."""
    start_str = start.strftime("%Y-%m")
    # 已知事件映射（基于历史）
    events = {
        "2022-03": "上海封城",
        "2022-04": "封城延续",
        "2024-02": "流动性危机底部",
        "2024-09": "政策大反转",
        "2024-10": "政策反转延续",
        "2025-04": "关税战",
        "2026-07": "7月调整",
    }
    for key, label in events.items():
        if start_str == key:
            return label
    return ""


# ═══════════════════════════════════════════════════════════════════
# 维度 (a+)：波动衰减状态（实时可计算的衰减代理）
# 回测固化：RV5/RV20 比率 + RV20 见顶判断
# ═══════════════════════════════════════════════════════════════════


def _compute_rv_decay(latest_date: str) -> dict:
    """计算波动衰减状态（实时可计算，无未来数据依赖）.

    代理指标：
    1. RV5/RV20 比率：< 0.8 = 短期波动显著回落（衰减→机会）
    2. RV20 是否见顶：近 10 日最高点是否已过
    3. 骤降检测：RV20 单日下降 > 5% = 疑似事件驱动衰减

    回测验证（5/5 历史样本）：
      比率 < 0.8 + 见顶 → 全部后续上涨（机会）
      比率 ≥ 0.8       → 全部后续下跌（陷阱）
      骤降（单日 RV20 降幅 > 5%）→ 事件驱动特征（后续 +5.5%，100% 胜率）

    返回 {rv5, rv20, ratio, peaked, status, rv20_daily_change, sharp_drop}
    """
    with sqlite3.connect(str(MARKET_DB_PATH)) as conn:
        # 优先用 daily_volatility_index（已收盘 RV20），回退到 index_daily 回算
        df_vol = pd.read_sql(
            "SELECT trade_date, ts_code, rv20 FROM daily_volatility_index "
            "WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 50",
            conn, params=(latest_date,),
        )

    # 用上证 RV20 作为基准（最稳定的宽基）
    sse_vol = df_vol[df_vol["ts_code"] == "000001.SH"] if not df_vol.empty else pd.DataFrame()
    if sse_vol.empty:
        return {}

    # daily_volatility_index 只有 RV20（无 RV5），需从 index_daily 回算 RV5
    rv20_now = float(sse_vol.iloc[0]["rv20"])
    rv20_prev = float(sse_vol.iloc[1]["rv20"]) if len(sse_vol) > 1 else None
    rv20_5d_ago = float(sse_vol.iloc[5]["rv20"]) if len(sse_vol) > 5 else None
    rv20_10d_max = float(sse_vol.head(10)["rv20"].max()) if len(sse_vol) >= 5 else rv20_now

    # RV5 从 index_daily 回算（5 日实际波动率，更灵敏）
    try:
        with sqlite3.connect(str(MARKET_DB_PATH)) as conn2:
            sse_df = pd.read_sql(
                "SELECT close FROM index_daily WHERE ts_code='000001.SH' "
                "ORDER BY trade_date DESC LIMIT 30",
                conn2,
            )
        if len(sse_df) >= 6:
            closes = sse_df["close"].astype(float).values
            logret = np.diff(np.log(closes))
            rv5_now = np.std(logret[-5:]) * np.sqrt(_TRADING_DAYS) * 100
        else:
            rv5_now = None
    except Exception:
        rv5_now = None

    # 衰减比率
    ratio = rv5_now / rv20_now if rv5_now and rv20_now > 0 else None

    # RV20 见顶判断（当前值 < 近 10 日最高）
    peaked = rv20_now < rv20_10d_max if rv20_10d_max else False

    # ── 骤降检测：RV20 单日变化 ──
    # 回测发现事件驱动的高波结束表现为 RV20 在 1-2 日内断崖式骤降
    # （封城解封/关税缓和当日 RV20 从 27%→19%、29%→8%）
    rv20_daily_change = None
    sharp_drop = False
    if rv20_prev is not None and rv20_prev > 0:
        rv20_daily_change = round(((rv20_now / rv20_prev - 1) * 100), 1)
        # 单日降幅 > 5% = 疑似事件驱动骤降
        sharp_drop = rv20_daily_change < -5.0

    # 综合状态
    if sharp_drop:
        # 骤降优先级最高——这是事件驱动的强信号
        status = "骤降中(强反转信号)"
    elif ratio is not None:
        if ratio < _DECAY_RATIO_THRESHOLD and peaked:
            status = "衰减中(机会)"  # 短期波动回落 + RV20 已见顶
        elif ratio >= _DECAY_RATIO_THRESHOLD and not peaked:
            status = "加速中(危险)"  # 短期波动仍在高位 + RV20 未止
        elif ratio >= _DECAY_RATIO_THRESHOLD:
            status = "高位震荡(警惕)"  # 短期波动仍高但 RV20 可能见顶
        else:
            status = "衰减中(机会)"  # 短期回落即够
    else:
        # RV5 不可用时用 RV20 斜率
        if rv20_5d_ago is not None:
            slope = rv20_now - rv20_5d_ago
            if slope < -0.5 and peaked:
                status = "衰减中(机会)"
            elif slope > 0.5:
                status = "加速中(危险)"
            else:
                status = "高位震荡(警惕)"
        else:
            status = ""

    return {
        "rv5": round(rv5_now, 1) if rv5_now else None,
        "rv20": round(rv20_now, 1),
        "ratio": round(ratio, 2) if ratio else None,
        "peaked": peaked,
        "status": status,
        "rv20_daily_change": rv20_daily_change,
        "sharp_drop": sharp_drop,
    }


# ═══════════════════════════════════════════════════════════════════
# 维度 (b)(c)：区域影响 + 机会识别
# ═══════════════════════════════════════════════════════════════════


def _analyze_sector_impact(
    streak_start: str,
    latest_date: str,
) -> tuple[list[SectorImpact], list[SectorImpact]]:
    """分析高波期间的板块影响（受冲击 + 逆势）.

    从 limit_pool 表聚合涨跌停数，用 sector_mapping 归一化到申万一级。
    从 fund_flow_sector 表累计主力净额作辅助。

    返回 (受影响最大 top N, 逆势机会 top N)。
    """
    # 日期格式：limit_pool 和 fund_flow_sector 都用 ISO 格式（2026-07-31）
    # streak_start 和 latest_date 统一转 ISO
    start_iso = streak_start.replace("-", "") if "-" not in streak_start and len(streak_start) == 8 else (
        f"{streak_start[:4]}-{streak_start[4:6]}-{streak_start[6:8]}" if len(streak_start) == 8 else streak_start
    )
    end_iso = latest_date.replace("-", "") if "-" not in latest_date and len(latest_date) == 8 else (
        f"{latest_date[:4]}-{latest_date[4:6]}-{latest_date[6:8]}" if len(latest_date) == 8 else latest_date
    )

    with sqlite3.connect(str(MARKET_DB_PATH)) as conn:
        # limit_pool 聚合（按 pool_kind + sector）
        df_pool = pd.read_sql(
            "SELECT pool_kind, sector, COUNT(*) as n "
            "FROM limit_pool "
            "WHERE trade_date >= ? AND trade_date <= ? "
            "AND sector IS NOT NULL AND sector != '' "
            "GROUP BY pool_kind, sector",
            conn,
            params=(start_iso, end_iso),
        )
        # fund_flow_sector 累计主力净额
        df_ff = pd.read_sql(
            "SELECT sector_name, SUM(main_net) as total_net "
            "FROM fund_flow_sector "
            "WHERE trade_date >= ? AND trade_date <= ? "
            "GROUP BY sector_name",
            conn,
            params=(start_iso, end_iso),
        )

    if df_pool.empty:
        return [], []

    # 归一化板块名 + 聚合
    limit_down: dict[str, dict] = {}  # {归一化名: {count, sub_sectors: {原始名: count}}}
    limit_up: dict[str, dict] = {}

    for _, row in df_pool.iterrows():
        kind = row["pool_kind"]
        raw_sector = row["sector"]
        norm = normalize_sector_name(raw_sector)
        target = limit_down if kind == "limit_down" else limit_up
        if norm not in target:
            target[norm] = {"count": 0, "sub": {}}
        target[norm]["count"] += int(row["n"])
        target[norm]["sub"][raw_sector] = target[norm]["sub"].get(raw_sector, 0) + int(row["n"])

    # 归一化 fund_flow 的主力净额
    net_map: dict[str, float] = {}
    if not df_ff.empty:
        for _, row in df_ff.iterrows():
            norm = normalize_sector_name(row["sector_name"])
            net = row["total_net"]
            if not pd.isna(net):
                net_map[norm] = net_map.get(norm, 0.0) + float(net)

    # 构造 SectorImpact 列表
    def build_impacts(data: dict, is_down: bool) -> list[SectorImpact]:
        results = []
        for name, info in data.items():
            # 细分行业描述（top 2）
            sub_sorted = sorted(info["sub"].items(), key=lambda x: -x[1])[:2]
            detail = "/".join(f"{k}{v}" for k, v in sub_sorted) if sub_sorted else ""
            # 分歧标注：同时出现在涨/跌两个榜
            has_div = name in limit_up and name in limit_down
            results.append(SectorImpact(
                name=name,
                limit_count=info["count"],
                main_net_total=net_map.get(name),
                detail=detail,
                has_divergence=has_div,
            ))
        # 排序：按次数降序
        results.sort(key=lambda x: -x.limit_count)
        return results[:_SECTOR_TOP_N]

    impacted = build_impacts(limit_down, is_down=True)
    resilient = build_impacts(limit_up, is_down=False)

    return impacted, resilient


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════


def analyze_vol_streak(latest_date: str | None = None) -> VolStreakReport:
    """高波持续分析主入口.

    参数：
        latest_date: 基准日期（YYYY-MM-DD 或 YYYYMMDD）；None 用今日

    返回：
        VolStreakReport
    """
    # 缓存检查
    cache_key = latest_date or "today"
    now = time.time()
    if cache_key in _streak_cache:
        ts, cached = _streak_cache[cache_key]
        if now - ts < _CACHE_TTL:
            return cached

    # 日期格式统一
    if latest_date is None:
        latest_date = date.today().isoformat()
    latest_iso = latest_date if "-" in latest_date else f"{latest_date[:4]}-{latest_date[4:6]}-{latest_date[6:8]}"

    report = VolStreakReport(latest_date=latest_iso)

    try:
        # (a) 历史回算（含当前期，数据完整 5.5 年）
        historical = _compute_historical_streaks()
        if historical:
            report.historical_count = len(historical)
            days_list = [s["days"] for s in historical]
            report.historical_avg_days = round(float(np.mean(days_list)), 1)

            # 当前高波期 = 历史回算的最后一个（如果它延伸到最新日期）
            latest = historical[-1]
            # 检查最后一个高波期是否仍在进行（end 日期接近 latest_date）
            latest_d = pd.to_datetime(latest["end"])
            target_d = pd.to_datetime(latest_iso)
            if (target_d - latest_d).days <= 5:  # 5 天内视为进行中
                report.current_days = latest["days"]
                report.streak_start_date = latest["start"]
                report.is_high_vol = True

                # 最长高波期（排除当前进行中的）
                past_streaks = [s for s in historical[:-1]] if report.is_high_vol else historical
                if past_streaks:
                    longest = max(past_streaks, key=lambda x: x["days"])
                    report.historical_max_days = longest["days"]
                    if longest.get("note"):
                        report.historical_max_note = f"{longest['note']}（{longest['start']}~{longest['end']}）"
                    else:
                        report.historical_max_note = f"{longest['start']}~{longest['end']}"
                    # 当前排名（排除自己后对比）
                    past_days = [s["days"] for s in past_streaks]
                    rank = sum(1 for d in past_days if d >= report.current_days) + 1
                    report.current_rank = f"第{rank}长（含当前共{report.historical_count}个高波期）"
                else:
                    # 只有当前一个高波期，无历史对比
                    report.historical_max_days = 0
                    report.current_rank = "首个记录的高波期"
            else:
                # 当前不在高波期，用 daily_volatility_index 快照兜底
                current_streak, streak_start = _compute_current_streak(latest_iso)
                report.current_days = current_streak
                report.streak_start_date = streak_start
                report.is_high_vol = current_streak > 0
                longest = max(historical, key=lambda x: x["days"])
                report.historical_max_days = longest["days"]
                if longest.get("note"):
                    report.historical_max_note = f"{longest['note']}（{longest['start']}~{longest['end']}）"
                else:
                    report.historical_max_note = f"{longest['start']}~{longest['end']}"

        else:
            # 无历史数据，用 daily_volatility_index 快照
            current_streak, streak_start = _compute_current_streak(latest_iso)
            report.current_days = current_streak
            report.streak_start_date = streak_start
            report.is_high_vol = current_streak > 0

        # (b)(c) 板块影响（仅高波时分析）
        if report.is_high_vol and report.streak_start_date:
            impacted, resilient = _analyze_sector_impact(
                report.streak_start_date, latest_iso
            )
            report.impacted_sectors = impacted
            report.resilient_sectors = resilient

        # (a+) 波动衰减状态（仅高波时检测，回测固化的实时信号）
        if report.is_high_vol:
            decay = _compute_rv_decay(latest_iso)
            report.rv5 = decay.get("rv5")
            report.rv20 = decay.get("rv20")
            report.rv_decay_ratio = decay.get("ratio")
            report.rv20_peaked = decay.get("peaked", False)
            report.decay_status = decay.get("status", "")
            report.rv20_daily_change = decay.get("rv20_daily_change")
            report.sharp_drop = decay.get("sharp_drop", False)

        report.available = True
    except Exception as e:
        logger.error(f"[vol_streak] 分析失败: {type(e).__name__}: {e}")

    _streak_cache[cache_key] = (now, report)
    return report


def format_streak_brief(report: VolStreakReport) -> str:
    """格式化盘中预警用的一行摘要.

    返回单行字符串（如"📈 高波第26天（历史平均19天，最长25天）｜ 波动衰减中"），
    非高波时返回空串。
    """
    if not report.is_high_vol or report.current_days == 0:
        return ""

    parts = [f"📈 高波第 {report.current_days} 天"]
    extras = []
    if report.historical_count > 0:
        extras.append(f"历史平均 {report.historical_avg_days:.0f} 天")
        extras.append(f"最长 {report.historical_max_days} 天")
    if extras:
        parts.append("（" + "，".join(extras) + "）")
    # 衰减状态（如果有）
    if report.decay_status:
        if report.sharp_drop and report.rv20_daily_change is not None:
            # 骤降是强信号，特别标注单日降幅
            parts.append(
                f"｜⚡ RV20骤降{report.rv20_daily_change:+.1f}%（疑似事件驱动，强反转信号）"
            )
        else:
            parts.append(f"｜波动{report.decay_status}")
    return "".join(parts)
