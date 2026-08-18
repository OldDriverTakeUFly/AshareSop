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

# candidate_context 摘要键：涨停家数/晋级率/开盘溢价/指数多空/regime 档位
_CONTEXT_KEYS = ("limit_up_count", "promo_12", "premium", "index_ma_bull",
                 "regime_label")


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

def data_readiness(conn: sqlite3.Connection, day: str) -> str:
    """Data readiness for a candidate day: ok / no_pool / incomplete_prices.

    incomplete_prices 判定：当日涨停池个股在 daily_price 的覆盖率 <80%
    （全市场日线未完成落库时池股普遍无价，事件必然被滤光——须显式区分于
    「无候选」，不用陈旧数据产出误导性空清单）。
    """
    pool = db.read_limit_pool(conn, day, day)
    if pool.empty:
        return "no_pool"
    codes = sorted(set(pool["ts_code"]))
    ph = ",".join("?" * len(codes))
    n_have = conn.execute(
        f"SELECT COUNT(DISTINCT ts_code) FROM daily_price "
        f"WHERE trade_date=? AND ts_code IN ({ph})",
        (day, *codes),
    ).fetchone()[0]
    return "ok" if n_have >= 0.8 * len(codes) else "incomplete_prices"


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

    # 数据新鲜度防线：当日涨停池无行 → 告警 + 空帧（价格完备性防线在 CLI 层，
    # data_readiness —— 库消费方（如模拟盘策略）对空帧本就优雅降级）
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

    涨停家数/晋级率（promo_12）/开盘溢价/指数多空（index_ma_bull）/
    regime_label；当日无 regime 行（日历缺日/空库）→
    {"trade_date": date, "regime_label": "无数据"}，缺失轴置 None 而非
    NaN（供 CLI 直接格式化）。
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


# ── markdown 渲染（candidates CLI 报告，规格 §2.3 五节结构）──

# §3.2.2 盘中纪律的每候选定制文本（人工执行参考，机器规则仍 open_next）。
# 2026-08-18 评审中性化裁决：T 日 fill_prob/封档是事前特征，不能预判
# T+1 盘中情形 → 不输出无条件卖出指令；所有候选统一条件式持有纪律
# （保留 A 桶一字持有期权：持有 +18.9% vs 开盘卖 +10.5%）。
HOLD_HINT = "开盘若=涨停价可持有观察，炸板立即卖出；低开走弱则开盘卖出"
WEAK_SEAL_NOTE = (
    "封板质量偏弱（易成交形态），T+1 走弱概率较高；"
    "开盘未封死直接卖，若开盘=涨停价仍可持有观察+炸板即卖"
)
DISCLAIMER = "基于日线近似（EOD 特征做当日决策），与回测同口径；enhanced 为研究标注非过滤。"

# 百分比化显示列（其余列原样字符串化，NaN 一律 "—"）
_PCT_COLUMNS = ("seal_ratio", "lg_sell_share", "fill_prob")


def execution_hint(fill_prob: float) -> str:
    """次日执行提示文本（§3.2.2，函数化便于测试）.

    fill_prob<0.35（一字/早盘硬板，成交概率低=封得死）→ 纯条件式持有
    纪律；fill_prob>=0.35（炸板回封/尾盘封等易成交形态）→ 同样的条件式
    纪律前附加风险注记（信息量保留，但不下无条件卖出指令）。
    """
    if not (float(fill_prob) < 0.35):
        return WEAK_SEAL_NOTE
    return HOLD_HINT


def empty_candidates_message(ctx: dict[str, object]) -> str:
    """空候选 + regime 正常时的 CLI/报告共用户案."""
    cnt = ctx.get("limit_up_count")
    return f"当日无候选（regime={ctx.get('regime_label')}，" \
           f"涨停 {cnt if cnt is not None else '—'} 家）"


def _fmt_cell(col: str, v: object) -> str:
    if pd.isna(v):
        return "—"
    if col in _PCT_COLUMNS:
        return f"{float(v) * 100:.1f}%"
    if col == "enhanced":
        return "✓" if bool(v) else "—"
    return str(v)


def _fmt_axis(v: object) -> str:
    """摘要轴：None/NaN → "—"，比率轴百分比化."""
    if v is None or pd.isna(v):
        return "—"
    return f"{float(v) * 100:.1f}%"


def _fmt_bull(v: object) -> str:
    """指数多空轴：True→多 / False→空 / None/NaN→"—"（§2.3.1 三轴之一）."""
    if v is None or pd.isna(v):
        return "—"
    return "多" if bool(v) else "空"


def _summary_line(ctx: dict[str, object]) -> str:
    cnt = ctx.get("limit_up_count")
    return (
        f"情绪档位：{ctx.get('regime_label', '—')} ｜ "
        f"涨停家数 {cnt if cnt is not None else '—'} ｜ "
        f"晋级率 promo_12 {_fmt_axis(ctx.get('promo_12'))} ｜ "
        f"开盘溢价 {_fmt_axis(ctx.get('premium'))} ｜ "
        f"指数多空 {_fmt_bull(ctx.get('index_ma_bull'))}"
    )


def _cand_ref(row: pd.Series) -> str:
    return f"{row.get('ts_code', '—')} {row.get('name', '')}".rstrip()


def render_candidates_md(
    cands: pd.DataFrame,
    ctx: dict[str, object],
    top: int = 10,
    note: str | None = None,
) -> str:
    """五节候选报告 md：①标题+三轴摘要 ②候选表 ③增强标注 ④执行提示 ⑤免责.

    空态自动注明原因：regime=="无数据" → 数据缺失文案；cands 空 →
    「当日无候选（regime=X，涨停 Y 家）」；note 显式覆盖（CLI 的
    limit_pool 缺数场景优先用）。空帧列缺失时仍可渲染（契约列容错）。
    """
    day = str(ctx.get("trade_date", ""))
    shown = cands.head(top) if top > 0 else cands.iloc[0:0]
    if note is None:
        if ctx.get("regime_label") == "无数据":
            note = f"{day} 当日 regime 无数据（日历缺日/空库），无法生成候选清单。"
        elif shown.empty:
            note = empty_candidates_message(ctx)

    parts = [f"# 打板候选清单 {day}", "", _summary_line(ctx), ""]

    # 节 2：候选表（CANDIDATE_COLUMNS 契约列，seal_ratio 降序由上游保证）
    parts += [f"## 候选表（按封单比降序，前 {len(shown)} 条 / 共 {len(cands)} 条）", ""]
    if shown.empty:
        parts += [note or empty_candidates_message(ctx), ""]
    else:
        cols = [c for c in CANDIDATE_COLUMNS if c in shown.columns]
        parts += [
            "| " + " | ".join(cols) + " |",
            "|" + "|".join(["---"] * len(cols)) + "|",
        ]
        for _, row in shown.iterrows():
            parts.append("| " + " | ".join(_fmt_cell(c, row.get(c)) for c in cols) + " |")
        parts.append("")

    # 节 3：增强标注（enhanced=True 单独列出，无则「今日无」）
    parts += ["## 增强标注（大单主导 lg_sell_share≥50% × 强封单 seal_ratio≥5%）", ""]
    enh = (shown[shown["enhanced"].fillna(False).astype(bool)]
           if "enhanced" in shown else shown.iloc[0:0])
    if enh.empty:
        parts += ["今日无", ""]
    else:
        for _, row in enh.iterrows():
            parts.append(
                f"- {_cand_ref(row)}：lg_sell_share "
                f"{_fmt_cell('lg_sell_share', row.get('lg_sell_share'))} × "
                f"seal_ratio {_fmt_cell('seal_ratio', row.get('seal_ratio'))}"
            )
        parts.append("")

    # 节 4：每候选次日执行提示（§3.2.2 定制文本）
    parts += ["## 次日执行提示（§3.2.2 盘中纪律，人工执行）", ""]
    if shown.empty:
        parts += ["无候选，无执行提示。", ""]
    else:
        for _, row in shown.iterrows():
            fp = row.get("fill_prob")
            fp_val = float("nan") if fp is None or pd.isna(fp) else float(fp)
            parts.append(
                f"- {_cand_ref(row)}（封档={_fmt_cell('封档', row.get('封档'))}，"
                f"fill_prob {_fmt_cell('fill_prob', fp)}）："
                f"{execution_hint(fp_val)}"
            )
        parts.append("")

    # 节 5：免责声明
    parts += ["## 免责声明", "", DISCLAIMER, ""]
    return "\n".join(parts)
