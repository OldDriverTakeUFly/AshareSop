"""盘后候选清单构建（first_board 实践腿，规格 §2）.

与回测严格同源：复用 build_events + attach_pattern_features +
build_market_regime + apply_preset，不另起过滤口径；卖出结构
（大单主导×强封单）与风险列（成交概率/封档）为标注信息，默认不参与
过滤——增强过滤仅在调用方显式 enhanced_filter=True 时生效（双臂对照）。
"""

from __future__ import annotations

import sqlite3

import pandas as pd
from loguru import logger

from davis_analyzer.limitup import db
from davis_analyzer.limitup.engine import fill_probability
from davis_analyzer.limitup.events import build_events
from davis_analyzer.limitup.patterns import attach_pattern_features
from davis_analyzer.limitup.sentiment import build_market_regime
from davis_analyzer.limitup.strategies import PRESETS, apply_preset

# ── 冻结先验阈值（Phase 1 调研同源，禁调参）──

LG_DOMINANT_MIN = 0.50   # 大单卖出占比 ≥0.50 → 大单主导
STRONG_SEAL_MIN = 0.05   # 封单比 ≥0.05 → 强封单档
SEAL_BAND_EDGES = [-1.0, 0.02, 0.05, 100.0]   # 封档分箱（右闭）
SEAL_BAND_LABELS = ["弱", "中", "强"]

# 输出契约列（空数据防线返回的空帧也带这些列，CLI 可直接渲染）
CANDIDATE_COLUMNS = [
    "ts_code", "name", "sector", "pattern_label", "seal_ratio", "封档",
    "first_seal_band", "broken_count", "lg_sell_share", "enhanced", "fill_prob",
]

# candidate_context 摘要键：涨停家数/晋级率/开盘溢价/regime 档位
_CONTEXT_KEYS = ("limit_up_count", "promo_12", "premium", "regime_label")


def _shift_day(ymd: str, days: int) -> str:
    dt = pd.to_datetime(ymd, format="%Y%m%d") + pd.Timedelta(days=days)
    return dt.strftime("%Y%m%d")


def _empty_candidates() -> pd.DataFrame:
    return pd.DataFrame(columns=CANDIDATE_COLUMNS)


# ── sell structure (moneyflow join) ──

def _attach_sell_structure(
    cands: pd.DataFrame, conn: sqlite3.Connection, day: str
) -> pd.DataFrame:
    """Join 当日全市场 moneyflow（单次查询），标注 lg_sell_share.

    lg_sell_share = (大单+特大单卖出额)/总卖出额；sell_total<=0
    （无卖出/数据缺失）→ NaN，增强标注自然落 False（宁缺毋错）。
    """
    mf = pd.read_sql_query(
        "SELECT trade_date, ts_code, sell_sm_amount, sell_md_amount, "
        "sell_lg_amount, sell_elg_amount FROM moneyflow WHERE trade_date=?",
        conn, params=(db.normalize_date(day),),
    )
    if mf.empty:
        cands = cands.copy()
        cands["lg_sell_share"] = float("nan")
        return cands
    else:
        sell_lg = mf["sell_lg_amount"].fillna(0) + mf["sell_elg_amount"].fillna(0)
        sell_total = (
            mf["sell_sm_amount"].fillna(0) + mf["sell_md_amount"].fillna(0)
            + mf["sell_lg_amount"].fillna(0) + mf["sell_elg_amount"].fillna(0)
        )
        mf["lg_sell_share"] = sell_lg / sell_total.where(sell_total > 0)
    return cands.merge(
        mf[["ts_code", "trade_date", "lg_sell_share"]],
        on=["ts_code", "trade_date"], how="left",
    )


# ── main entry ──

def build_candidates(
    conn: sqlite3.Connection,
    date: str,
    *,
    enhanced_filter: bool = False,
    lookback_days: int = 60,
) -> pd.DataFrame:
    """盘后 first_board 候选清单（按 seal_ratio 降序）.

    流程：数据新鲜度防线 → build_events（date 左移 lookback 自然日窗口，
    保证 prev_limit_count_60/形态窗口正确）→ 筛当日 → first_board 预设
    过滤（regime 传当日单行）→ 卖出结构/增强标注 → 风险列
    （fill_prob 引擎 base 档 / 封档 弱中强）。enhanced_filter=True 时
    仅返回 enhanced（大单主导×强封）子集。
    """
    day = db.normalize_date(date)

    # 数据新鲜度防线：当日涨停池无行 → 告警 + 空帧（CLI 据此退出，不用陈旧数据）
    if db.read_limit_pool(conn, day, day).empty:
        logger.warning(
            "candidates: {} 当日 limit_pool 无数据（daily 刷新失败?），返回空清单", day
        )
        return _empty_candidates()

    start = _shift_day(day, -lookback_days)
    ev = build_events(conn, start, day)
    ev = attach_pattern_features(ev, conn, start, day)
    ev = ev[ev["trade_date"] == day].reset_index(drop=True)
    if ev.empty:
        logger.warning(
            "candidates: {} 当日无有效涨停事件（未通过股票池/涨停价校验）", day
        )
        return _empty_candidates()

    regime = build_market_regime(conn, start, day)
    regime_day = regime[regime["trade_date"] == day]
    cands = apply_preset(ev, PRESETS["first_board"], regime=regime_day)
    if cands.empty:
        label = regime_day["regime_label"].iloc[0] if len(regime_day) else "无数据"
        logger.info("candidates: {} first_board 口径过滤后为空（regime={}）", day, label)
        return _empty_candidates()

    cands = _attach_sell_structure(cands, conn, day)
    cands["enhanced"] = (
        (cands["lg_sell_share"] >= LG_DOMINANT_MIN)
        & (cands["seal_ratio"] >= STRONG_SEAL_MIN)
    )
    if enhanced_filter:
        cands = cands[cands["enhanced"]]

    cands["封档"] = pd.cut(
        cands["seal_ratio"], SEAL_BAND_EDGES, labels=SEAL_BAND_LABELS
    )
    cands["fill_prob"] = cands.apply(
        lambda r: fill_probability(r, "base"), axis=1
    )
    out = cands.sort_values("seal_ratio", ascending=False).reset_index(drop=True)
    logger.info(
        "candidates[{}]: {} 条（enhanced {} 条）",
        day, len(out), int(out["enhanced"].sum()),
    )
    return out


def candidate_context(
    conn: sqlite3.Connection, date: str, lookback_days: int = 60
) -> dict[str, object]:
    """当日 regime 摘要（candidates CLI 报告头）.

    涨停家数/晋级率（promo_12）/开盘溢价/regime_label；当日无 regime 行
    （日历缺日/空库）→ {"trade_date": date, "regime_label": "无数据"}，
    缺失轴置 None 而非 NaN（供 CLI 直接格式化）。
    """
    day = db.normalize_date(date)
    regime = build_market_regime(conn, _shift_day(day, -lookback_days), day)
    row = regime[regime["trade_date"] == day]
    if row.empty:
        return {"trade_date": day, "regime_label": "无数据"}
    out: dict[str, object] = {"trade_date": day}
    for key in _CONTEXT_KEYS:
        v = row.iloc[0].get(key)
        out[key] = None if pd.isna(v) else v
    return out
