"""盘中恐慌信号检测器 — 三大信号独立检测.

⚠️ **数据使用原则（重要）**：
  本模块是**盘中实时预警**功能，使用实时数据（盘中价替换今日点）是合理的——
  预警的目的就是提前感知。但**盘后分析（after-hours-review）的 RV20 必须只用
  收盘数据**，不能用盘中价凑。两个场景的数据策略不同：
    - 盘中预警（本模块）：实时价替换今日点，提供盘中预警
    - 盘后分析（daily_scan 算的 daily_volatility_index）：只用收盘价，数据不全则不算

数据源（盘中实时）：
- RV20：DAL index_daily 历史 + AKShare stock_zh_index_spot_em 实时价替换今日点
  （注：这是盘中预警的近似，std 对单点变动不敏感，尾盘趋近精确；
   严格的收盘 RV20 由 daily_scan 17:30 跑的 volatility 模块计算）
- 涨跌停：AKShare stock_zt_pool_em / stock_zt_pool_zbgc_em / stock_zt_pool_dtgc_em（传今日 date）
- iVIX：AKShare index_option_50etf_min_qvix（分时实时）

阈值（与波动率方法论对齐）：
- 系统性恐慌：≥3 个指数 RV20 P90+
- 行为面恐慌抛售：涨跌停比 < 0.5 或 跌停占比 > 50%
- iVIX 极端：iVIX > 25（明显恐慌上限）
- V/R 极端：V/R > 1.3（期权极贵）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from stockhot.core.logging import logger

# 预警阈值
_RV_PCT_THRESHOLD = 90  # RV20 历史分位阈值
_RV_PCT_MIN_INDICES = 3  # 达标的最少指数数（系统性恐慌定义）
_LIMIT_UP_DOWN_RATIO_THRESHOLD = 0.5  # 涨跌停比阈值（< 此值 = 恐慌抛售）
_DOWN_RATIO_THRESHOLD = 0.50  # 跌停占比阈值（> 此值 = 系统性恐慌）
_IVIX_THRESHOLD = 25.0  # iVIX 明显恐慌上限
_VR_RATIO_THRESHOLD = 1.3  # V/R 期权极贵阈值

# 剂量效应阈值（2026-07-31 回测固化）
# 回测发现：P99+ 极端高波后 20 日胜率仅 58%（vs P90-95 的 88%），
# 尤其叠加 60 日均线破位时接近"接飞刀"。P99+ 触发剂量警示。
_RV_PCT_EXTREME = 99             # 极端高波分位阈值
_RV_PCT_EXTREMIN_N = 3          # ≥3 指数 P99+ 才触发（避免单指数噪音）
_BREAKDOWN_MA_WINDOW = 60        # 趋势破位判定均线窗口
_BREAKDOWN_THRESHOLD = 0.95     # 收盘 < MA60 × 0.95 视为破位

# 方向维度权重（_detect_direction 综合方向分）
# direction_score = sign(当日涨跌)×0.4 + sign(涨跌停结构-1)×0.3 + sign(5日累计)×0.3
_DIR_WEIGHT_TODAY = 0.4      # 当日涨跌（最即时）
_DIR_WEIGHT_LIMIT = 0.3      # 涨跌停结构（行为确认）
_DIR_WEIGHT_CUM5D = 0.3      # 5 日累计（趋势背景）
# 涨跌停结构比的中性阈值：ratio > 1 偏多，< 1 偏空（与行为面恐慌的 0.5 阈值不冲突）
_LIMIT_RATIO_NEUTRAL = 1.0

# 强度分：象限专属公式（2026-07-28 修订）
#
# 设计原则：强度 = 该象限特征的显著程度（与方向无关）
# - 🔴 下跌恐慌：跌幅 + 跌停占比贡献 → 恐慌显著
# - 🟠 逼空过热：涨幅 + 涨停占比贡献 → 逼空显著
# - 🟡 阴跌预警：温和跌幅 + 跌停占比 → 阴跌持续
# - 🟢 强势上涨：涨幅 + 涨停占比 → 强势确立
#
# 每个象限的公式都让"该象限的标志性特征"正向贡献分数。
# 这样强度高在所有象限都表示"特征显著"，不再有"涨日低分=强势"的歧义。
_INTENSITY_DROP_MULTIPLIER = 10.0  # 把涨/跌幅%放大到与 P 分位可比的量级
# 基础分：低波象限（🟡🟢）的 RV 贡献天然低，加 15 分基础分让分数量级可比
_INTENSITY_LOW_VOL_BASE = 15.0

# 监控的指数（与 volatility 模块一致）
_INDICES = ["000001.SH", "399001.SZ", "000300.SH", "399006.SZ", "000688.SH"]
_INDEX_NAMES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "000300.SH": "沪深300",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
}


@dataclass
class IndexVolatility:
    """单个指数的盘中波动率读数."""

    ts_code: str
    name: str
    rv20: float
    rv20_pct: float
    panic_level: str


@dataclass
class LimitBehaviorReading:
    """涨跌停行为面结构化读数（_detect_limit_behavior 内部用，传给方向维度复用）."""

    limit_up: int = 0
    limit_down: int = 0
    broken: int = 0
    up_down_ratio: float | None = None  # up / max(down, 1)
    down_ratio: float | None = None     # down / (up + down)
    available: bool = False             # 数据是否可用（up+down>0）


@dataclass
class DirectionReading:
    """方向维度读数（4 维聚合）.

    RV20 是标准差，只衡量波幅不衡量方向。本 dataclass 叠加方向维度，
    用于把"系统性恐慌"细化为四象限：下跌恐慌/逼空过热/阴跌预警/强势上涨。
    """

    sse_pct_chg: float | None = None     # 上证当日涨跌幅 %
    hs300_pct_chg: float | None = None   # 沪深300 当日涨跌幅 %
    cum_5d_pct: float | None = None      # 上证近 5 日累计涨跌 %
    rv20_delta_5d: float | None = None   # RV20 5 日变化（上升速率）
    limit_up: int | None = None          # 涨停数（从行为面信号复用）
    limit_down: int | None = None        # 跌停数
    broken: int | None = None            # 炸板数
    limit_ratio: float | None = None     # 涨跌停结构比（up / max(down,1)）
    direction_score: float = 0.0         # 综合方向分（负=下跌，正=上涨）
    direction_label: str = "中性"        # "上涨" / "下跌" / "中性"
    available: bool = False              # 至少一个维度有数据


@dataclass
class SectorStrength:
    """单个板块的强弱读数（多源合并）.

    数据来源（时效不同，消息里会标注）：
    - pct_change: sw_daily 最近可得交易日（盘中可能是昨日，盘后是当日）
    - limit_up/down/broken: zt_pool 所属行业聚合（盘中实时）
    - main_net: fund_flow_sector 表（上一交易日，Tushare moneyflow 非实时）
    """

    name: str                              # 板块名（申万行业）
    pct_change: float | None = None        # 涨跌幅 %
    limit_up: int = 0                      # 涨停数（盘中实时）
    limit_down: int = 0                    # 跌停数
    broken: int = 0                        # 炸板数
    main_net: float | None = None          # 主力净额（亿元，截至上一交易日）
    strength_score: float = 0.0            # 综合强弱分（用于排序，正强负弱）


@dataclass
class SectorStructure:
    """板块结构读数（top N 强弱排名）."""

    strong: list[SectorStrength] = field(default_factory=list)   # 综合强势 top 3
    weak: list[SectorStrength] = field(default_factory=list)     # 综合弱势 top 3
    pct_change_as_of: str = ""            # 涨跌幅数据时效标注（如"07-28"或"上一交易日"）
    available: bool = False


@dataclass
class DoseWarning:
    """剂量效应警示（2026-07-31 回测固化）.

    回测发现 P99+ 极端高波与 P90-95 普通高波的远期收益差异巨大：
      P90-95 + 超跌 → 20 日胜率 100%（黄金组合）
      P99+ + 破位   → 20 日胜率 43%（接飞刀）
    当检测到 P99+ 极端高波时触发本警示，提示用户区分"健康恐慌释放"
    与"趋势性崩盘"。
    """

    extreme_pct_n: int = 0          # P99+ 的指数数量
    breakdown_indices: list[str] = field(default_factory=list)  # 60日均线破位的指数名
    triggered: bool = False         # 是否触发警示（P99+ 数 ≥ 阈值）
    is_breakdown: bool = False      # 是否伴随趋势破位（更危险）


@dataclass
class SignalResult:
    """单个信号的检测结果."""

    name: str  # 信号名（系统性恐慌/行为面恐慌/期权面极端）
    triggered: bool  # 是否达标
    detail: str  # 读数详情（用于消息格式化）
    available: bool = True  # 数据是否可用


@dataclass
class PanicReport:
    """盘中恐慌综合报告."""

    trade_date: str
    timestamp: str
    signals: list[SignalResult] = field(default_factory=list)
    volatility_indices: list[IndexVolatility] = field(default_factory=list)
    ivix_value: float | None = None
    vr_ratio: float | None = None
    # 四象限细化（2026-07-27）：方向 + 象限 + 强度分
    direction: DirectionReading | None = None
    quadrant: str = ""                            # "下跌恐慌"/"逼空过热"/"阴跌预警"/"强势上涨"
    intensity_score: float = 0.0                  # 0-100
    intensity_label: str = ""                     # "极低/偏低/中等/偏高/极高"
    # 板块结构（2026-07-28）：强势/弱势板块 top N
    sectors: SectorStructure | None = None
    # 剂量警示（2026-07-31）：P99+ 极端高波 + 趋势破位检测
    dose_warning: DoseWarning | None = None

    @property
    def any_triggered(self) -> bool:
        """是否有任一信号达标."""
        return any(s.triggered for s in self.signals if s.available)

    @property
    def triggered_names(self) -> list[str]:
        """触发的信号名列表."""
        return [s.name for s in self.signals if s.triggered and s.available]


# ═══════════════════════════════════════════════════════════════════
# 信号 1：RV20 历史分位（盘中实时版）
# ═══════════════════════════════════════════════════════════════════


def _classify_rv_level(pct: float) -> str:
    """RV20 分位 → 恐慌等级（与 volatility analyzer 一致）."""
    if pct >= 95:
        return "极度恐慌"
    if pct >= 90:
        return "明显恐慌"
    if pct >= 80:
        return "偏高"
    if pct >= 50:
        return "正常"
    return "平静"


def _fetch_realtime_index_prices() -> dict[str, dict[str, float]]:
    """从 AKShare 获取实时指数行情（盘中用）.

    双源降级：东财源（stock_zh_index_spot_em）优先 → 新浪源
    （stock_zh_index_spot_sina）兜底。东财 push2 主机常被屏蔽，
    新浪源稳定且直接提供涨跌幅字段。

    返回 {ts_code: {"price": 最新价, "pct_chg": 当日涨跌幅%}}。
    任一字段不可得时为 None。
    """
    import akshare as ak
    from stockhot.core.rate_limiter import safe_akshare_call

    result: dict[str, dict[str, float]] = {}

    # ── 源 1：东财（提供最新价，无涨跌幅）──
    df_em = safe_akshare_call(ak.stock_zh_index_spot_em, symbol="沪深重要指数")
    if df_em is not None and not df_em.empty:
        for _, row in df_em.iterrows():
            code = str(row.get("代码", ""))
            price = pd.to_numeric(row.get("最新价"), errors="coerce")
            if pd.isna(price):
                continue
            ts_code = _em_code_to_ts_code(code)
            if ts_code and ts_code not in result:
                result[ts_code] = {"price": float(price), "pct_chg": None}

    # ── 源 2：新浪（提供最新价 + 涨跌幅，东财未覆盖时补全）──
    # 新浪源代码格式：sh000001 / sz399001
    df_sina = safe_akshare_call(ak.stock_zh_index_spot_sina)
    if df_sina is not None and not df_sina.empty:
        for _, row in df_sina.iterrows():
            sina_code = str(row.get("代码", "")).lower()  # sh000001
            ts_code = _sina_code_to_ts_code(sina_code)
            if not ts_code:
                continue
            price = pd.to_numeric(row.get("最新价"), errors="coerce")
            pct = pd.to_numeric(row.get("涨跌幅"), errors="coerce")
            if ts_code not in result:
                result[ts_code] = {
                    "price": float(price) if not pd.isna(price) else None,
                    "pct_chg": float(pct) if not pd.isna(pct) else None,
                }
            else:
                # 东财已有 price，补全新浪的 pct_chg
                if result[ts_code].get("pct_chg") is None and not pd.isna(pct):
                    result[ts_code]["pct_chg"] = float(pct)

    return result


def _em_code_to_ts_code(em_code: str) -> str | None:
    """东财代码（000001/399001）→ ts_code（000001.SH/399001.SZ）."""
    em_code = em_code.strip()
    if em_code.startswith("000300"):
        return "000300.SH"
    if em_code.startswith("000688"):
        return "000688.SH"
    if em_code.startswith("000001"):
        return "000001.SH"
    if em_code.startswith("399001"):
        return "399001.SZ"
    if em_code.startswith("399006"):
        return "399006.SZ"
    return None


def _sina_code_to_ts_code(sina_code: str) -> str | None:
    """新浪代码（sh000001/sz399001）→ ts_code."""
    sina_code = sina_code.strip().lower()
    if sina_code in ("sh000001", "sh000300", "sh000688"):
        return sina_code.replace("sh", "") + ".SH"
    if sina_code in ("sz399001", "sz399006"):
        return sina_code.replace("sz", "") + ".SZ"
    return None


def _detect_rv_volatility() -> tuple[list[IndexVolatility], SignalResult, dict[str, dict[str, float]]]:
    """检测 RV20 历史分位（盘中实时）.

    用 DAL index_daily 拿历史 250 日 close，最后一点替换为实时价，算 RV20 + 分位。
    返回 (indices_vol, signal, realtime_data)：实时行情回传给方向维度复用。
    """
    from stockhot.data_layer import get_repository

    repo = get_repository()
    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=400)).strftime("%Y%m%d")

    # 拿实时行情（含 price + pct_chg）
    try:
        realtime_data = _fetch_realtime_index_prices()
    except Exception as e:
        logger.warning(f"[panic] realtime index prices failed: {e}")
        realtime_data = {}

    indices_vol: list[IndexVolatility] = []

    for ts_code in _INDICES:
        try:
            df = repo.get_index_daily(ts_code, start_date, end_date)
            if df.empty or len(df) < 30:
                continue
            closes = np.array(df["close"].astype(float).values, dtype=float)  # 可写副本

            # 盘中：替换最后一点为实时价（若可得）
            if ts_code in realtime_data:
                rt = realtime_data[ts_code].get("price")
                if rt is not None and rt > 0:
                    closes[-1] = rt

            # 算 RV20：log return 的 20 日滚动 std × √242
            log_returns = np.diff(np.log(closes))
            if len(log_returns) < 20:
                continue
            rv20 = np.std(log_returns[-20:]) * np.sqrt(242) * 100

            # 历史分位：用滚动 20 日 std 的分位
            rolling_std = pd.Series(log_returns).rolling(20).std() * np.sqrt(242) * 100
            valid = rolling_std.dropna()
            if len(valid) < 50:
                pct = 50.0  # 数据不足给中性
            else:
                pct = (valid <= rv20).mean() * 100

            indices_vol.append(IndexVolatility(
                ts_code=ts_code,
                name=_INDEX_NAMES.get(ts_code, ts_code),
                rv20=round(rv20, 1),
                rv20_pct=round(pct, 0),
                panic_level=_classify_rv_level(pct),
            ))
        except Exception as e:
            logger.warning(f"[panic] RV20 for {ts_code} failed: {e}")

    # 判断系统性恐慌
    panic_n = sum(1 for i in indices_vol if i.rv20_pct >= _RV_PCT_THRESHOLD)
    triggered = panic_n >= _RV_PCT_MIN_INDICES

    if indices_vol:
        detail_parts = [f"{i.name} P{i.rv20_pct:.0f}({i.panic_level})" for i in indices_vol]
        detail = f"{panic_n}/{len(indices_vol)} 指数 P{_RV_PCT_THRESHOLD}+；" + "；".join(detail_parts)
    else:
        detail = "数据不可用"

    signal = SignalResult(
        name="系统性恐慌",
        triggered=triggered,
        detail=detail,
        available=bool(indices_vol),
    )
    return indices_vol, signal, realtime_data


# ═══════════════════════════════════════════════════════════════════
# 信号 2：涨跌停行为面
# ═══════════════════════════════════════════════════════════════════


def _detect_limit_behavior() -> tuple[SignalResult, LimitBehaviorReading, dict[str, dict]]:
    """检测涨跌停行为面（盘中实时，AKShare 东财源）.

    返回三元组 (SignalResult, LimitBehaviorReading, sector_counts)：
      - SignalResult: 用于消息格式化
      - LimitBehaviorReading: 结构化读数，传给 _detect_direction 复用
      - sector_counts: {板块名: {limit_up, limit_down, broken}} 涨跌停按行业聚合，
        传给 _detect_sector_structure 复用（零额外 API，源自同一批 pool 接口）
    """
    import akshare as ak
    from collections import defaultdict
    from stockhot.core.rate_limiter import safe_akshare_call

    today = date.today().strftime("%Y%m%d")
    n_up = n_broken = n_down = 0
    df_up = df_down = df_broken = None
    sector_counts: dict[str, dict] = defaultdict(lambda: {"limit_up": 0, "limit_down": 0, "broken": 0})

    try:
        df_up = safe_akshare_call(ak.stock_zt_pool_em, date=today)
        if df_up is not None and not df_up.empty:
            n_up = len(df_up)
    except Exception as e:
        logger.warning(f"[panic] zt_pool failed: {e}")

    try:
        df_down = safe_akshare_call(ak.stock_zt_pool_dtgc_em, date=today)
        if df_down is not None and not df_down.empty:
            n_down = len(df_down)
    except Exception as e:
        logger.warning(f"[panic] dt_pool failed: {e}")

    try:
        df_broken = safe_akshare_call(ak.stock_zt_pool_zbgc_em, date=today)
        if df_broken is not None and not df_broken.empty:
            n_broken = len(df_broken)
    except Exception as e:
        logger.warning(f"[panic] broken_pool failed: {e}")

    # 按所属行业聚合（零额外 API，复用已拉取的 DataFrame）
    # AKShare zt_pool 系列返回中文列名"所属行业"
    for df, key in [(df_up, "limit_up"), (df_down, "limit_down"), (df_broken, "broken")]:
        if df is None or df.empty:
            continue
        # 兼容中文字段名"所属行业"和英文字段名"industry"
        industry_col = None
        for col in ("所属行业", "industry"):
            if col in df.columns:
                industry_col = col
                break
        if industry_col is None:
            continue
        for val in df[industry_col].dropna():
            industry = str(val).strip()
            if industry and industry not in ("None", "nan", "-"):
                sector_counts[industry][key] += 1

    if n_up + n_down == 0:
        return (
            SignalResult(name="行为面恐慌", triggered=False, detail="数据不可用", available=False),
            LimitBehaviorReading(available=False),
            dict(sector_counts),
        )

    ratio = n_up / max(n_down, 1)
    down_ratio = n_down / (n_up + n_down)
    triggered = ratio < _LIMIT_UP_DOWN_RATIO_THRESHOLD or down_ratio > _DOWN_RATIO_THRESHOLD

    detail = (f"涨停{n_up}/跌停{n_down}/炸板{n_broken}，"
              f"涨跌停比{ratio:.2f}{'(<0.5 恐慌抛售)' if ratio < _LIMIT_UP_DOWN_RATIO_THRESHOLD else ''}，"
              f"跌停占比{down_ratio:.0%}{f'(>50% 系统性恐慌)' if down_ratio > _DOWN_RATIO_THRESHOLD else ''}")

    signal = SignalResult(
        name="行为面恐慌抛售",
        triggered=triggered,
        detail=detail,
    )
    reading = LimitBehaviorReading(
        limit_up=n_up, limit_down=n_down, broken=n_broken,
        up_down_ratio=ratio, down_ratio=down_ratio,
        available=True,
    )
    return signal, reading, dict(sector_counts)


# ═══════════════════════════════════════════════════════════════════
# 信号 3：iVIX / V-R 期权面
# ═══════════════════════════════════════════════════════════════════


def _classify_ivix_level(ivix_value: float) -> str:
    """iVIX 绝对值 → 恐慌等级（复用 volatility analyzer 的 7 档）."""
    if pd.isna(ivix_value):
        return "数据不可用"
    if ivix_value < 12:
        return "极度自满"
    if ivix_value < 18:
        return "平静健康"
    if ivix_value < 22:
        return "略有担忧"
    if ivix_value < 30:
        return "明显恐慌"
    if ivix_value < 40:
        return "高度恐慌"
    if ivix_value < 60:
        return "极度恐慌"
    return "系统性崩溃"


def _detect_ivix_vr() -> tuple[float | None, float | None, SignalResult]:
    """检测 iVIX 和 V/R 比率（盘中实时分时 iVIX）."""
    import akshare as ak
    from stockhot.core.rate_limiter import safe_akshare_call

    ivix_value = None
    ivix_source = ""  # 数据来源标注（实时/上一交易日）

    # 路径 1：分时 iVIX（盘中实时，AKShare index_option_50etf_min_qvix）
    try:
        df = safe_akshare_call(ak.index_option_50etf_min_qvix)
        if df is not None and not df.empty:
            raw = float(df.iloc[-1]["qvix"])
            # AKShare 可能返回 NaN（盘外/数据缺失），过滤
            if not pd.isna(raw):
                ivix_value = raw
                ivix_source = "实时"
    except Exception as e:
        logger.warning(f"[panic] intraday iVIX failed: {e}")

    # 路径 2：历史日线回退（分时失败/NaN 时，用上一交易日收盘 iVIX）
    # index_option_50etf_qvix 返回 2015 至今完整日频，稳定可靠
    if ivix_value is None:
        try:
            df_hist = safe_akshare_call(ak.index_option_50etf_qvix)
            if df_hist is not None and not df_hist.empty:
                # 取最后一行（最新交易日）的 close
                last_close = pd.to_numeric(df_hist.iloc[-1]["close"], errors="coerce")
                if not pd.isna(last_close):
                    ivix_value = float(last_close)
                    ivix_source = "上一交易日"
                    logger.info(f"[panic] iVIX 回退到历史日线: {ivix_value} ({ivix_source})")
        except Exception as e:
            logger.warning(f"[panic] iVIX history fallback failed: {e}")

    # V/R = iVIX / 上证 RV20
    vr_ratio = None
    if ivix_value is not None:
        try:
            from stockhot.data_layer import get_repository
            repo = get_repository()
            end_date = date.today().strftime("%Y%m%d")
            start_date = (date.today() - timedelta(days=400)).strftime("%Y%m%d")
            df_idx = repo.get_index_daily("000001.SH", start_date, end_date)

            # 盘中实时价替换
            try:
                rt_data = _fetch_realtime_index_prices()
                sse_closes = np.array(df_idx["close"].astype(float).values, dtype=float)
                if "000001.SH" in rt_data:
                    rt_price = rt_data["000001.SH"].get("price")
                    if rt_price is not None and rt_price > 0:
                        sse_closes[-1] = rt_price
            except Exception:
                sse_closes = np.array(df_idx["close"].astype(float).values, dtype=float)

            log_ret = np.diff(np.log(sse_closes))
            rv_sse = np.std(log_ret[-20:]) * np.sqrt(242) * 100
            if rv_sse > 0:
                vr_ratio = ivix_value / rv_sse
        except Exception as e:
            logger.warning(f"[panic] V/R ratio failed: {e}")

    # 判断
    if ivix_value is None:
        return None, None, SignalResult(name="期权面极端", triggered=False, detail="数据不可用", available=False)

    ivix_triggered = ivix_value > _IVIX_THRESHOLD
    vr_triggered = vr_ratio is not None and vr_ratio > _VR_RATIO_THRESHOLD
    triggered = ivix_triggered or vr_triggered

    parts = [f"iVIX={ivix_value:.1f}({_classify_ivix_level(ivix_value)})"]
    if ivix_source and ivix_source != "实时":
        parts.append(f"[{ivix_source}]")  # 非实时时标注时效
    if vr_ratio is not None:
        vr_label = "期权极贵" if vr_ratio > _VR_RATIO_THRESHOLD else ("合理" if vr_ratio > 0.9 else "期权便宜")
        parts.append(f"V/R={vr_ratio:.2f}({vr_label})")
    detail = "；".join(parts)
    if ivix_triggered:
        detail += f"（iVIX>{_IVIX_THRESHOLD}）"
    if vr_triggered:
        detail += f"（V/R>{_VR_RATIO_THRESHOLD}）"

    return ivix_value, vr_ratio, SignalResult(
        name="期权面极端",
        triggered=triggered,
        detail=detail,
    )


# ═══════════════════════════════════════════════════════════════════
# 信号 4：方向维度（四象限细化）
# RV20 是标准差只衡量波幅不衡量方向，需叠加方向维度才能区分
# "高波 + 上涨（逼空/强势）" vs "高波 + 下跌（真恐慌）"
# ═══════════════════════════════════════════════════════════════════


def _compute_realtime_pct_chg(
    ts_code: str,
    realtime_data: dict[str, dict[str, float]],
) -> float | None:
    """获取当日涨跌幅（盘中优先实时源，盘后回退 DB）.

    三级优先级：
    1. 新浪/东财源的 pct_chg 字段（盘中实时，直接提供）
    2. 实时价 ÷ DB 昨收 - 1（仅有价格时回退计算）
    3. DB index_daily.pct_chg（仅当最后一行是今日时，盘后场景）
    """
    from stockhot.data_layer import get_repository

    today_str = date.today().strftime("%Y%m%d")
    today_iso = date.today().isoformat()

    # 路径 1：实时源直接提供 pct_chg（最优，新浪源稳定）
    if ts_code in realtime_data:
        pct = realtime_data[ts_code].get("pct_chg")
        if pct is not None and not pd.isna(pct):
            return round(float(pct), 3)

    try:
        repo = get_repository()
        end_date = today_str
        start_date = (date.today() - timedelta(days=20)).strftime("%Y%m%d")
        df = repo.get_index_daily(ts_code, start_date, end_date)
        if df.empty:
            return None

        # 路径 2：实时价 / 昨收 - 1（仅有价格时）
        if ts_code in realtime_data:
            rt = realtime_data[ts_code].get("price")
            if rt is not None and rt > 0 and len(df) >= 2:
                prev_close = float(df["close"].iloc[-2])
                if prev_close > 0:
                    return round((rt / prev_close - 1) * 100, 3)

        # 路径 3：DB pct_chg 回退——仅当最后一行 trade_date == 今日时才可信
        # 否则 DB 数据滞后（今日 daily_scan 尚未跑），返回 None 比取昨日的值安全
        last_trade_date = str(df["trade_date"].iloc[-1])
        if last_trade_date in (today_str, today_iso):
            latest_pct = df["pct_chg"].iloc[-1]
            if pd.notna(latest_pct):
                return round(float(latest_pct), 3)

        return None
    except Exception as e:
        logger.warning(f"[panic] realtime pct_chg for {ts_code} failed: {e}")
        return None


def _compute_cum_5d_pct(ts_code: str) -> float | None:
    """计算近 5 个交易日累计涨跌幅（近似 ∑pct_chg，DB 已收盘数据精确）."""
    from stockhot.data_layer import get_repository

    try:
        repo = get_repository()
        end_date = date.today().strftime("%Y%m%d")
        start_date = (date.today() - timedelta(days=15)).strftime("%Y%m%d")
        df = repo.get_index_daily(ts_code, start_date, end_date)
        if df.empty:
            return None
        recent = df.tail(5)
        if recent.empty:
            return None
        return round(float(recent["pct_chg"].sum()), 2)
    except Exception as e:
        logger.warning(f"[panic] cum_5d_pct for {ts_code} failed: {e}")
        return None


def _compute_rv20_delta_5d(ts_code: str) -> float | None:
    """计算 RV20 在近 5 日的变化（上升速率）.

    从 DAL 拉近 30 日 close，分别算 5 日前和今日的 RV20 取差。
    正值=波动加速，负值=波动衰减。
    """
    from stockhot.data_layer import get_repository

    try:
        repo = get_repository()
        end_date = date.today().strftime("%Y%m%d")
        start_date = (date.today() - timedelta(days=60)).strftime("%Y%m%d")
        df = repo.get_index_daily(ts_code, start_date, end_date)
        if df.empty or len(df) < 30:
            return None
        closes = np.array(df["close"].astype(float).values, dtype=float)
        log_returns = np.diff(np.log(closes))
        if len(log_returns) < 25:
            return None
        # 今日 RV20（最后 20 个 log return）
        rv_today = np.std(log_returns[-20:]) * np.sqrt(242) * 100
        # 5 日前 RV20（往前推 5 个 log return，再取 20 个）
        if len(log_returns) < 25:
            return None
        rv_5d_ago = np.std(log_returns[-25:-5]) * np.sqrt(242) * 100
        return round(float(rv_today - rv_5d_ago), 2)
    except Exception as e:
        logger.warning(f"[panic] rv20_delta_5d for {ts_code} failed: {e}")
        return None


def _detect_direction(
    limit_reading: LimitBehaviorReading,
    realtime_data: dict[str, dict[str, float]] | None = None,
) -> DirectionReading:
    """方向维度综合检测（4 维聚合）.

    参数：
        limit_reading: 行为面结构化读数（涨跌停数 + 结构比）
        realtime_data: AKShare 实时指数行情（含 price + pct_chg）；None 时只靠 DB

    返回：
        DirectionReading，direction_score 聚合三维度符号（负空正多）
    """
    realtime_data = realtime_data or {}

    # 维度 1：指数当日涨跌幅（上证 + 沪深300）
    sse_chg = _compute_realtime_pct_chg("000001.SH", realtime_data)
    hs300_chg = _compute_realtime_pct_chg("000300.SH", realtime_data)

    # 维度 2：涨跌停结构比（已由 _detect_limit_behavior 采集）
    limit_ratio = limit_reading.up_down_ratio if limit_reading.available else None

    # 维度 3：近 5 日累计涨跌（上证）
    cum_5d = _compute_cum_5d_pct("000001.SH")

    # 维度 4：RV20 5 日变化（上证，仅作注解，不参与 direction_score）
    rv20_delta = _compute_rv20_delta_5d("000001.SH")

    # ── 综合方向分：加权符号聚合 ──
    # 用 sign 而非原始值，避免某维度数值过大主导；权重反映即时性
    score = 0.0
    weight_used = 0.0
    if sse_chg is not None:
        score += _DIR_WEIGHT_TODAY * (1 if sse_chg > 0 else (-1 if sse_chg < 0 else 0))
        weight_used += _DIR_WEIGHT_TODAY
    if limit_ratio is not None:
        # ratio > 1 偏多，< 1 偏空（与行为面恐慌阈值 0.5 不冲突）
        sign = 1 if limit_ratio > _LIMIT_RATIO_NEUTRAL else (-1 if limit_ratio < _LIMIT_RATIO_NEUTRAL else 0)
        score += _DIR_WEIGHT_LIMIT * sign
        weight_used += _DIR_WEIGHT_LIMIT
    if cum_5d is not None:
        score += _DIR_WEIGHT_CUM5D * (1 if cum_5d > 0 else (-1 if cum_5d < 0 else 0))
        weight_used += _DIR_WEIGHT_CUM5D

    # 归一化到 [-1, 1]：避免某维度缺失导致分数系统性偏低
    direction_score = round(score / weight_used, 3) if weight_used > 0 else 0.0

    if direction_score > 0.001:
        label = "上涨"
    elif direction_score < -0.001:
        label = "下跌"
    else:
        label = "中性"

    available = any(v is not None for v in [sse_chg, hs300_chg, limit_ratio, cum_5d])

    return DirectionReading(
        sse_pct_chg=sse_chg,
        hs300_pct_chg=hs300_chg,
        cum_5d_pct=cum_5d,
        rv20_delta_5d=rv20_delta,
        limit_up=limit_reading.limit_up if limit_reading.available else None,
        limit_down=limit_reading.limit_down if limit_reading.available else None,
        broken=limit_reading.broken if limit_reading.available else None,
        limit_ratio=limit_ratio,
        direction_score=direction_score,
        direction_label=label,
        available=available,
    )


def _classify_quadrant(
    direction: DirectionReading | None,
    indices_vol: list[IndexVolatility],
) -> str:
    """四象限判定：波动率（高/低）× 方向（上/下）.

    返回 ""（空串）表示数据不足以判定（方向维度完全不可用 + 无 RV20 数据）。

    象限定义：
        高波 P90+ × 下跌 → "下跌恐慌"   🔴
        高波 P90+ × 上涨 → "逼空过热"   🟠
        低波 P90- × 下跌 → "阴跌预警"   🟡
        低波 P90- × 上涨 → "强势上涨"   🟢
    """
    if not indices_vol:
        return ""
    # 高波定义：≥3 指数 P90+（与系统性恐慌阈值一致）
    panic_n = sum(1 for i in indices_vol if i.rv20_pct >= _RV_PCT_THRESHOLD)
    high_vol = panic_n >= _RV_PCT_MIN_INDICES

    if direction is None or not direction.available:
        # 方向维度不可用时，仅按波动率粗分（高波=恐慌，低波=平静）
        return "下跌恐慌" if high_vol else "强势上涨"

    is_up = direction.direction_score >= 0  # 中性（=0）按上涨处理（避免无端恐慌）

    if high_vol and not is_up:
        return "下跌恐慌"
    if high_vol and is_up:
        return "逼空过热"
    if not high_vol and not is_up:
        return "阴跌预警"
    return "强势上涨"


def _detect_dose_warning(
    indices_vol: list[IndexVolatility],
    high_vol: bool,
) -> DoseWarning:
    """剂量效应警示检测（2026-07-31 回测固化）.

    回测发现高波内部并非均匀：
      P90-95（普通高波）+ 超跌 → 20 日胜率 100%（黄金组合）
      P99+（极端高波）+ 破位   → 20 日胜率 43%（接飞刀）

    本函数检测两个危险信号：
    1. P99+ 极端高波：≥3 指数 P99+ 时触发（均值回归可能失效）
    2. 60 日均线破位：宽基指数收盘 < MA60 × 0.95（趋势性下跌）

    仅在高波区间（high_vol=True）检测——低波无剂量问题。
    非高波返回 triggered=False 的空警示。

    参数：
        indices_vol: 各指数 RV20 读数
        high_vol: 是否处于高波区间（≥3 指数 P90+）

    返回：
        DoseWarning，triggered=True 时消息会附加警示文案
    """
    warning = DoseWarning()

    # 非高波区间无剂量问题
    if not high_vol:
        return warning

    # 1. P99+ 极端高波检测
    extreme_n = sum(1 for i in indices_vol if i.rv20_pct >= _RV_PCT_EXTREME)
    warning.extreme_pct_n = extreme_n
    warning.triggered = extreme_n >= _RV_PCT_EXTREMIN_N

    # 2. 60 日均线破位检测（仅 P99+ 触发时才查，避免额外 DB 调用）
    if warning.triggered:
        try:
            from stockhot.data_layer import get_repository
            repo = get_repository()
            end_date = date.today().strftime("%Y%m%d")
            start_date = (date.today() - timedelta(days=90)).strftime("%Y%m%d")
            for idx in indices_vol:
                df = repo.get_index_daily(idx.ts_code, start_date, end_date)
                if df.empty or len(df) < _BREAKDOWN_MA_WINDOW:
                    continue
                ma = df["close"].tail(_BREAKDOWN_MA_WINDOW).mean()
                latest_close = float(df["close"].iloc[-1])
                if latest_close < ma * _BREAKDOWN_THRESHOLD:
                    warning.breakdown_indices.append(idx.name)
            warning.is_breakdown = bool(warning.breakdown_indices)
        except Exception as e:
            logger.warning(f"[panic] dose warning breakdown check failed: {e}")

    return warning


def _compute_intensity(
    indices_vol: list[IndexVolatility],
    direction: DirectionReading | None,
    quadrant: str = "",
) -> tuple[float, str]:
    """计算象限专属强度分（0-100）+ 等级标签.

    设计原则（2026-07-28 修订）：强度 = 该象限特征的显著程度，与方向无关。
    每个象限用专属公式，让"标志性特征"正向贡献分数：
      🔴 下跌恐慌 = RV×0.5 + 跌幅×10×0.3 + 跌停占比×0.2
      🟠 逼空过热 = RV×0.5 + 涨幅×10×0.3 + 涨停占比×0.2
      🟡 阴跌预警 = RV×0.3 + 跌幅×10×0.4 + 跌停占比×0.2 + 15基础分
      🟢 强势上涨 = RV×0.2 + 涨幅×10×0.4 + 涨停占比×0.3 + 15基础分

    低波象限（🟡🟢）RV 贡献天然低，加 15 分基础分让分数量级可比。

    参数：
        indices_vol: 各指数 RV20 读数（取最高分位作为波动率基准）
        direction: 方向维度读数（涨跌幅 + 涨跌停结构）
        quadrant: 当前象限标签（决定用哪个公式）

    返回：
        (score 0-100, 等级标签)
    """
    rv_max_pct = max((i.rv20_pct for i in indices_vol), default=0.0)
    sse_chg = direction.sse_pct_chg if direction else None
    limit_up = direction.limit_up if direction else None
    limit_down = direction.limit_down if direction else None

    # 涨/跌幅贡献（按象限方向取正）
    chg = sse_chg if sse_chg is not None else 0.0
    # 涨跌停占比
    if limit_up is not None and limit_down is not None and (limit_up + limit_down) > 0:
        total = limit_up + limit_down
        up_share = limit_up / total * 100
        down_share = limit_down / total * 100
    else:
        up_share = down_share = 0.0

    # ── 按象限选公式 ──
    if quadrant == "逼空过热":
        # 🟠 涨幅 + 涨停占比贡献
        up_contrib = max(0.0, chg) * _INTENSITY_DROP_MULTIPLIER * 0.3
        score = rv_max_pct * 0.5 + up_contrib + up_share * 0.2
    elif quadrant == "强势上涨":
        # 🟢 涨幅 + 涨停占比贡献 + 基础分
        up_contrib = max(0.0, chg) * _INTENSITY_DROP_MULTIPLIER * 0.4
        score = rv_max_pct * 0.2 + up_contrib + up_share * 0.3 + _INTENSITY_LOW_VOL_BASE
    elif quadrant == "阴跌预警":
        # 🟡 温和跌幅 + 跌停占比贡献 + 基础分
        drop_contrib = max(0.0, -chg) * _INTENSITY_DROP_MULTIPLIER * 0.4
        score = rv_max_pct * 0.3 + drop_contrib + down_share * 0.2 + _INTENSITY_LOW_VOL_BASE
    else:
        # 🔴 下跌恐慌（默认）：跌幅 + 跌停占比贡献
        drop_contrib = max(0.0, -chg) * _INTENSITY_DROP_MULTIPLIER * 0.3
        score = rv_max_pct * 0.5 + drop_contrib + down_share * 0.2

    score = max(0.0, min(100.0, score))
    score = round(score, 1)

    # 等级标签（5 档，跨象限统一）
    if score >= 75:
        label = "极高"
    elif score >= 55:
        label = "偏高"
    elif score >= 35:
        label = "中等"
    elif score >= 20:
        label = "偏低"
    else:
        label = "极低"

    return score, label


# ═══════════════════════════════════════════════════════════════════
# 信号 5：板块结构（强势/弱势板块）
# 三源合并：zt_pool 涨跌停聚合（盘中实时）+ sw_daily 涨跌幅（最近交易日）
#         + fund_flow_sector 主力净额（上一交易日）
# ═══════════════════════════════════════════════════════════════════


# 板块结构展示的 top N
_SECTOR_TOP_N = 3
# 综合强弱分权重（用于 strong/weak 排序）
# strength_score = 涨停数×1.0 - 跌停数×1.0 + 涨跌幅×0.5 + 主力净额×0.1
# 涨停/跌停是结构主信号（权重最高），涨跌幅和资金是辅助确认
_SECTOR_W_LIMIT = 1.0
_SECTOR_W_PCT = 0.5
_SECTOR_W_MAIN = 0.1


def _fetch_sw_daily_pct() -> tuple[dict[str, float], str]:
    """从 Tushare sw_daily 拿最近可得交易日的板块涨跌幅.

    返回 (板块涨跌幅字典, 数据日期标注)。
    盘中调用时当日数据可能未更新，自动回退到上一交易日。
    只取申万一级（ts_code 格式 801xx0.SI），避免二级三级噪音。
    """
    from stockhot.data_layer import get_gateway

    try:
        gw = get_gateway()
        # 尝试近 5 天，找到第一个有数据的交易日
        for back in range(0, 6):
            d = (date.today() - timedelta(days=back)).strftime("%Y%m%d")
            df = gw.call("sw_daily", trade_date=d)
            if df is None or df.empty:
                continue
            # 只取申万一级（ts_code 格式 801010.SI：801 + 2位 + 0，共6位数字末位为0）
            # 申万一级共 31 个，末位固定为 0（农林牧渔801010、基础化工801030、钢铁801040...）
            # 二级末位非 0（农产品加工801012、饲料801014...）
            df_l1 = df[df["ts_code"].str.match(r"^801\d{2}0\.SI$")].copy()
            if df_l1.empty:
                continue
            pct_map = {}
            for _, row in df_l1.iterrows():
                name = str(row.get("name", "")).strip()
                pct = row.get("pct_change")
                if name and pct is not None and not pd.isna(pct):
                    pct_map[name] = float(pct)
            if pct_map:
                # 日期标注：MM-DD 格式
                label = f"{d[4:6]}-{d[6:8]}"
                logger.info(f"[panic] sw_daily 板块涨跌幅: {len(pct_map)} 个（{label}）")
                return pct_map, label
        logger.warning("[panic] sw_daily 近 6 天无数据")
        return {}, ""
    except Exception as e:
        logger.warning(f"[panic] sw_daily 涨跌幅获取失败: {e}")
        return {}, ""


def _fetch_sector_main_net() -> dict[str, float]:
    """从 fund_flow_sector 表拿最近交易日的板块主力净额.

    返回 {板块名: 主力净额(亿元)}。数据来源是 Tushare moneyflow 聚合，
    时效为上一交易日（非实时）。

    注意：fund_flow_sector 表的 trade_date 是 ISO 格式（2026-07-31），
    与 index_daily 的紧凑格式（20260731）不同。
    """
    try:
        from stockhot.data_layer import get_repository
        repo = get_repository()
        # 尝试近 6 天找有数据的交易日
        for back in range(0, 6):
            d = (date.today() - timedelta(days=back))
            # fund_flow_sector 表用 ISO 格式（2026-07-31）
            d_iso = d.isoformat()
            rows = repo.get_fund_flow_sector(d_iso)
            if not rows:
                # 也尝试紧凑格式（兼容其他表）
                d_compact = d.strftime("%Y%m%d")
                rows = repo.get_fund_flow_sector(d_compact)
            if not rows:
                continue
            net_map = {}
            for r in rows:
                name = r.get("sector_name", "").strip()
                net = r.get("main_net")
                if name and net is not None:
                    net_map[name] = float(net)
            if net_map:
                return net_map
        return {}
    except Exception as e:
        logger.warning(f"[panic] fund_flow_sector 获取失败: {e}")
        return {}


def _detect_sector_structure(sector_counts: dict[str, dict]) -> SectorStructure:
    """板块结构检测：三源合并 → top N 强弱排名.

    参数：
        sector_counts: _detect_limit_behavior 聚合的 {板块: {limit_up, limit_down, broken}}

    返回：
        SectorStructure，strong/weak 各 top N
    """
    # 数据源 1：板块涨跌幅（sw_daily，最近交易日）
    pct_map, pct_as_of = _fetch_sw_daily_pct()
    # 数据源 2：板块主力净额（fund_flow_sector，上一交易日）
    net_map = _fetch_sector_main_net()

    # ── 板块名归一化：三个数据源统一到申万一级口径 ──
    # zt_pool 用东财细分（"元件""半导体"），fund_flow 用 Tushare 细分（"化学制药"），
    # sw_daily 用申万一级（"电子""医药生物"）。归一化后才能按同一板块合并。
    from stockhot.alert.sector_mapping import normalize_sector_name

    # 按归一化名聚合 sector_counts（多个细分 → 同一一级，涨跌停数累加）
    norm_counts: dict[str, dict] = {}
    for raw_name, counts in sector_counts.items():
        norm = normalize_sector_name(raw_name)
        if norm not in norm_counts:
            norm_counts[norm] = {"limit_up": 0, "limit_down": 0, "broken": 0}
        norm_counts[norm]["limit_up"] += counts.get("limit_up", 0)
        norm_counts[norm]["limit_down"] += counts.get("limit_down", 0)
        norm_counts[norm]["broken"] += counts.get("broken", 0)

    # pct_map 和 net_map 的 key 已经是/接近申万一级，直接用归一化名查找
    # （sw_daily 一级本身就是目标口径；fund_flow 细分需归一化）
    norm_net: dict[str, float] = {}
    for raw_name, net in net_map.items():
        norm = normalize_sector_name(raw_name)
        # 同一一级下多个细分的净额累加
        norm_net[norm] = norm_net.get(norm, 0.0) + net

    # 合并所有出现过的归一化板块名
    all_sectors = set(norm_counts.keys()) | set(pct_map.keys()) | set(norm_net.keys())
    if not all_sectors:
        return SectorStructure(available=False)

    # 构造 SectorStrength 列表（用归一化名）
    strengths: list[SectorStrength] = []
    for name in all_sectors:
        counts = norm_counts.get(name, {})
        lu = counts.get("limit_up", 0)
        ld = counts.get("limit_down", 0)
        br = counts.get("broken", 0)
        pct = pct_map.get(name)  # sw_daily 一级名直接匹配
        net = norm_net.get(name)

        # 综合强弱分（用于排序参考）：涨停正、跌停负，涨跌幅和资金辅助
        score = 0.0
        score += lu * _SECTOR_W_LIMIT
        score -= ld * _SECTOR_W_LIMIT
        if pct is not None:
            score += pct * _SECTOR_W_PCT
        if net is not None:
            score += net * _SECTOR_W_MAIN

        strengths.append(SectorStrength(
            name=name, pct_change=pct,
            limit_up=lu, limit_down=ld, broken=br,
            main_net=net, strength_score=round(score, 2),
        ))

    # ── 强弱分类：行为信号（涨跌停）优先，避免数据源口径不一致导致误判 ──
    # 强势：有涨停的板块，按涨停数降序（行为信号最直接）
    # 弱势：有跌停的板块，按跌停数降序（行为信号最直接）
    # 若无涨跌停数据（盘中 zt_pool 全失败），回退到 strength_score 排序
    has_limit_data = any(s.limit_up + s.limit_down > 0 for s in strengths)

    if has_limit_data:
        strong = sorted(
            [s for s in strengths if s.limit_up > 0],
            key=lambda x: (-x.limit_up, -x.strength_score),
        )[:_SECTOR_TOP_N]
        weak = sorted(
            [s for s in strengths if s.limit_down > 0],
            key=lambda x: (-x.limit_down, x.strength_score),
        )[:_SECTOR_TOP_N]

        # 回退补全：若 weak 为空（全市场无跌停）但 sw_daily 涨跌幅可用，
        # 用涨幅最低（跌幅最大）的板块补全，避免"无弱势"信息缺失
        if not weak and pct_map:
            weak = sorted(
                [s for s in strengths if s.pct_change is not None and s.strength_score < 0],
                key=lambda x: x.strength_score,
            )[:_SECTOR_TOP_N]

        # 同理：若 strong 为空（全市场无涨停）但涨跌幅可用，用涨幅最大补全
        if not strong and pct_map:
            strong = sorted(
                [s for s in strengths if s.pct_change is not None and s.strength_score > 0],
                key=lambda x: -x.strength_score,
            )[:_SECTOR_TOP_N]
    else:
        # 回退：纯按涨跌幅排序（盘后或 zt_pool 失败场景）
        strong = sorted(
            [s for s in strengths if s.strength_score > 0],
            key=lambda x: -x.strength_score,
        )[:_SECTOR_TOP_N]
        weak = sorted(
            [s for s in strengths if s.strength_score < 0],
            key=lambda x: x.strength_score,
        )[:_SECTOR_TOP_N]

    # 至少要有 1 个 strong 或 1 个 weak 才算 available
    available = bool(strong or weak)

    return SectorStructure(
        strong=strong, weak=weak,
        pct_change_as_of=pct_as_of,
        available=available,
    )


# ═══════════════════════════════════════════════════════════════════
# 综合检测 + 消息格式化
# ═══════════════════════════════════════════════════════════════════


def detect_panic_signals() -> PanicReport:
    """盘中恐慌信号综合检测（三大信号 + 方向维度独立）.

    每个信号独立 try/except，单源失败降级为"数据不可用"，不影响其他信号。
    最后聚合方向维度 → 四象限 + 强度分。
    """
    report = PanicReport(
        trade_date=date.today().isoformat(),
        timestamp=time.strftime("%H:%M"),
    )

    # 行为面结构化读数（信号 2 内部采集，传给方向维度复用）
    limit_reading = LimitBehaviorReading(available=False)
    # 板块涨跌停聚合（信号 2 内部采集，传给板块结构检测复用，零额外 API）
    sector_counts: dict[str, dict] = {}
    # 实时指数行情（信号 1 已采集，传给方向维度复用，避免重复拉 AKShare）
    realtime_data: dict[str, dict[str, float]] = {}

    # 信号 1：RV20
    try:
        indices_vol, sig_rv, rt_prices = _detect_rv_volatility()
        report.volatility_indices = indices_vol
        report.signals.append(sig_rv)
        realtime_data = rt_prices
    except Exception as e:
        logger.error(f"[panic] RV20 detection error: {e}")
        report.signals.append(SignalResult("系统性恐慌", False, f"检测异常: {e}", available=False))

    # 信号 2：涨跌停行为（返回 3 元组：信号 + 结构化读数 + 板块聚合）
    try:
        sig_limit, limit_reading, sector_counts = _detect_limit_behavior()
        report.signals.append(sig_limit)
    except Exception as e:
        logger.error(f"[panic] limit behavior error: {e}")
        report.signals.append(SignalResult("行为面恐慌抛售", False, f"检测异常: {e}", available=False))

    # 信号 3：iVIX/V-R
    try:
        ivix, vr, sig_ivix = _detect_ivix_vr()
        report.ivix_value = ivix
        report.vr_ratio = vr
        report.signals.append(sig_ivix)
    except Exception as e:
        logger.error(f"[panic] iVIX detection error: {e}")
        report.signals.append(SignalResult("期权面极端", False, f"检测异常: {e}", available=False))

    # 信号 4：方向维度 → 四象限 + 强度分
    try:
        direction = _detect_direction(limit_reading, realtime_data)
        report.direction = direction
        report.quadrant = _classify_quadrant(direction, report.volatility_indices)
        # 强度分用象限专属公式（必须先定象限再算强度）
        score, label = _compute_intensity(
            report.volatility_indices, direction, report.quadrant
        )
        report.intensity_score = score
        report.intensity_label = label
    except Exception as e:
        logger.error(f"[panic] direction detection error: {e}")
        # 方向失败不影响三大信号，但象限会降级为空（按波动率粗分）
        report.quadrant = _classify_quadrant(None, report.volatility_indices)

    # 信号 4.5：剂量效应警示（仅高波区间检测）
    # 🟠逼空过热/🔴下跌恐慌 是高波；🟢强势上涨/🟡阴跌预警 是低波
    is_high_vol = report.quadrant in ("逼空过热", "下跌恐慌")
    try:
        report.dose_warning = _detect_dose_warning(report.volatility_indices, is_high_vol)
    except Exception as e:
        logger.error(f"[panic] dose warning error: {e}")

    # 信号 5：板块结构（强势/弱势 top N）
    # 复用信号 2 的 sector_counts + sw_daily 涨跌幅 + fund_flow 主力资金
    try:
        report.sectors = _detect_sector_structure(sector_counts)
    except Exception as e:
        logger.error(f"[panic] sector structure error: {e}")

    return report


# 四象限元数据：emoji + 行动参考文案 + 强度词（用于 format_alert_message）
# intensity_word：强度的主语，明确"什么强"——避免"强度"歧义
_QUADRANT_META: dict[str, dict[str, str]] = {
    "下跌恐慌": {
        "emoji": "🔴",
        "subtitle": "高波 × 方向↓ → 减仓信号",
        "intensity_word": "恐慌",   # 强度高 = 恐慌很剧烈
        "disclaimer": "⚠️ 减仓信号：高波 + 下跌共振，减仓决策结合持仓与风控。",
    },
    "逼空过热": {
        "emoji": "🟠",
        "subtitle": "高波 × 方向↑ → 防回撤",
        "intensity_word": "逼空",   # 强度高 = 逼空很猛烈
        "disclaimer": "⚠️ 高波强势：方向向上但波动大，注意热点轮动和回撤风险。",
    },
    "阴跌预警": {
        "emoji": "🟡",
        "subtitle": "低波 × 方向↓ → 谨慎观望",
        "intensity_word": "阴跌",   # 强度高 = 阴跌持续性强
        "disclaimer": "⚠️ 风险累积：低波阴跌，趋势偏弱。警惕破位加速下行。",
    },
    "强势上涨": {
        "emoji": "🟢",
        "subtitle": "低波 × 方向↑ → 加仓机会",
        "intensity_word": "上涨",   # 强度高 = 上涨动能足
        "disclaimer": "⚠️ 加仓机会：方向向上 + 结构健康。注意仓位控制、不盲目追高。",
    },
}


def _format_pct(value: float | None, suffix: str = "%") -> str:
    """格式化百分比，None 显示为 'N/A'."""
    if value is None:
        return "N/A"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}{suffix}"


def _format_direction_section(direction: DirectionReading) -> list[str]:
    """格式化方向拆解章节（4 维读数）."""
    lines = ["【方向拆解】"]

    # 当日涨跌（上证 + 沪深300）
    sse = _format_pct(direction.sse_pct_chg)
    hs300 = _format_pct(direction.hs300_pct_chg)
    lines.append(f"  上证当日  {sse:8s}  沪深300  {hs300}")

    # 5 日累计
    cum5 = _format_pct(direction.cum_5d_pct)
    lines.append(f"  上证5日  {cum5:8s}  ({direction.direction_label})")

    # 涨跌停结构
    if direction.limit_up is not None and direction.limit_down is not None:
        lu, ld = direction.limit_up, direction.limit_down
        ratio = direction.limit_ratio if direction.limit_ratio is not None else (lu / max(ld, 1))
        # 结构定性：综合考虑 ratio 和跌停占比
        # ratio 反映相对强弱，跌停占比反映抛售广度（>30% 算偏激烈）
        down_share = ld / (lu + ld) if (lu + ld) > 0 else 0
        if ratio > 3 and down_share < 0.1:
            bias = "强势追涨"
        elif ratio > 1:
            bias = "偏多" + (f"（跌停占比{down_share:.0%}偏高）" if down_share >= 0.3 else "")
        elif ratio > 0.5:
            bias = "偏空"
        else:
            bias = "恐慌抛售"
        broken_str = f"/炸板{direction.broken}" if direction.broken is not None else ""
        lines.append(f"  涨跌停    涨{lu}/跌{ld}{broken_str} = {ratio:.1f}（{bias}）")

    # RV20 5 日变化（波动加速/衰减）
    if direction.rv20_delta_5d is not None:
        delta = direction.rv20_delta_5d
        trend = "波动加速" if delta > 0.5 else ("波动衰减" if delta < -0.5 else "波动稳定")
        lines.append(f"  RV20速率  {_format_pct(delta, suffix='')}% （{trend}）")

    lines.append("")
    return lines


def _format_sector_strength(s: SectorStrength, show_pct: bool) -> str:
    """格式化单个板块行.

    show_pct: 是否显示涨跌幅列（sw_daily 数据可用时为 True）。
    当涨跌停数都为 0 时（涨跌幅回退选出的板块），隐藏涨跌停数避免"涨0/跌0"噪音。
    """
    parts = [f"{s.name[:6]:6s}"]  # 板块名截断到 6 字符对齐
    if show_pct and s.pct_change is not None:
        parts.append(f"{_format_pct(s.pct_change):>7s}")
    # 仅当有实际涨跌停数据时才显示（避免涨跌幅回退场景的"涨0/跌0"噪音）
    if s.limit_up > 0 or s.limit_down > 0:
        parts.append(f"涨{s.limit_up}/跌{s.limit_down}")
        if s.broken:
            parts.append(f"炸{s.broken}")
    if s.main_net is not None:
        parts.append(f"主力{_format_main_net(s.main_net)}")
    return "  ".join(parts)


def _format_main_net(value: float) -> str:
    """格式化主力净额（亿元，带正负号）."""
    if value == 0:
        return "0"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}亿"


def _format_sector_section(sectors: SectorStructure) -> list[str]:
    """格式化板块结构章节.

    布局：
      【板块结构】（涨跌幅截至 07-28）
        🟢 强势：消费电子  电子  食品饮料
        🔴 弱势：房地产  建筑装饰  钢铁
    """
    if not sectors.available:
        return []

    lines = ["【板块结构】"]
    # 涨跌幅时效标注
    if sectors.pct_change_as_of:
        lines[0] += f"（涨跌幅截至 {sectors.pct_change_as_of}）"

    show_pct = bool(sectors.pct_change_as_of)

    if sectors.strong:
        lines.append("  🟢 强势板块：")
        for s in sectors.strong:
            lines.append("    " + _format_sector_strength(s, show_pct))
    if sectors.weak:
        lines.append("  🔴 弱势板块：")
        for s in sectors.weak:
            lines.append("    " + _format_sector_strength(s, show_pct))

    lines.append("")
    return lines


def _format_dose_warning_section(warning: DoseWarning) -> list[str]:
    """格式化剂量效应警示章节.

    仅在 P99+ 极端高波触发时渲染。文案区分两种情况：
    - 单纯 P99+（无破位）：提示历史胜率低，警惕均值回归失效
    - P99+ + 破位：强警示"接飞刀"风险
    """
    if not warning.triggered:
        return []

    lines = ["【剂量警示】"]

    # P99+ 计数
    lines.append(f"  ⚠️ 极端高波：{warning.extreme_pct_n} 个指数 RV20 分位 ≥ P99")

    if warning.is_breakdown:
        # 最危险组合：P99+ + 趋势破位
        idx_str = "、".join(warning.breakdown_indices)
        lines.append(f"  🔴 趋势破位：{idx_str} 收盘 < 60日均线×95%")
        lines.append("  → 历史回测：P99+ + 破位后 20 日胜率仅 43%（接飞刀）")
        lines.append("  → 此信号已非「健康恐慌释放」，警惕趋势性崩盘延续")
    else:
        # 单纯极端高波（未破位）
        lines.append("  → 历史回测：P99+ 后 20 日胜率 58%（vs P90-95 的 88%）")
        lines.append("  → 均值回归动力减弱，不宜机械抄底")

    lines.append("")
    return lines


def format_alert_message(report: PanicReport) -> str:
    """格式化恐慌预警消息（飞书文本）.

    标题用四象限 emoji + 行动参考；正文包含波动率温度、方向拆解、强度分。
    象限不可用时降级为原"恐慌预警"标题。
    """
    lines: list[str] = []

    # ── 标题：四象限 emoji + 强度（带主语，避免歧义）──
    meta = _QUADRANT_META.get(report.quadrant)
    if meta:
        emoji = meta["emoji"]
        title = report.quadrant
        subtitle = meta["subtitle"]
        intensity_word = meta["intensity_word"]
        # 强度带主语：如"逼空 75/100 极高"而不是笼统的"强度 75"
        lines.append(
            f"{emoji} {title} [{report.trade_date} {report.timestamp}]  "
            f"{intensity_word}强度 {report.intensity_score:.0f}/100 {report.intensity_label}"
        )
        # 触发信号名合并到副标题行（紧凑展示，省一行）
        triggered = "/".join(report.triggered_names) if report.triggered_names else "无信号触发"
        lines.append(f"象限：{subtitle} ｜ 信号：{triggered}")
        # 行动参考前置（结论先行）：盘中扫一眼即知该怎么做，数据详情在后支撑
        lines.append(meta["disclaimer"])
    else:
        # 数据全部不可用降级
        lines.append(f"⚪ 市场读数 [{report.trade_date} {report.timestamp}]")
        lines.append("（数据不足，无法判定象限）")
        lines.append("⚠️ 信号仅提示市场状态，不构成交易建议。")
    lines.append("")

    # ── 波动率温度（紧凑 2 行格式，省 4 行）──
    # 去掉 RV20 绝对值和等级标签（与 P分位 + 柱状条信息重复）
    # 保留 P分位（核心数值）+ 简化柱状条（视觉冲击）
    if report.volatility_indices:
        # 指数简称映射（避免机械截断）
        short_names = {
            "上证指数": "上证", "深证成指": "深成指", "沪深300": "沪深300",
            "创业板指": "创业板", "科创50": "科创50",
        }
        sorted_vol = sorted(report.volatility_indices, key=lambda x: -x.rv20_pct)
        lines.append("【波动率温度】")
        items = []
        for i in sorted_vol:
            short = short_names.get(i.name, i.name[:3])
            bar = "█" * max(1, int(i.rv20_pct / 25))
            items.append(f"{short}P{i.rv20_pct:.0f} {bar}")
        # 分 2 行展示（前 3 + 后 2）
        mid = (len(items) + 1) // 2
        lines.append("  " + "  ".join(items[:mid]))
        if len(items) > mid:
            lines.append("  " + "  ".join(items[mid:]))
        lines.append("")

    # ── 方向拆解 ──
    if report.direction is not None and report.direction.available:
        lines.extend(_format_direction_section(report.direction))

    # ── 板块结构（强势/弱势 top N）──
    if report.sectors is not None and report.sectors.available:
        lines.extend(_format_sector_section(report.sectors))

    # ── 剂量警示（P99+ 极端高波时才显示）──
    if report.dose_warning is not None and report.dose_warning.triggered:
        lines.extend(_format_dose_warning_section(report.dose_warning))

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 趋势分析 + ASCII 图表格式化
# ═══════════════════════════════════════════════════════════════════


def _ascii_bar(value: float, max_value: float, width: int = 10) -> str:
    """生成 ASCII 柱状条（用于横向对比）."""
    if max_value <= 0 or value is None:
        return "▏" * 0
    ratio = min(value / max_value, 1.0)
    filled = int(ratio * width)
    return "█" * filled + "▏" * (width - filled)


def _ascii_sparkline(values: list[float]) -> str:
    """生成简易折线（用 block 字符表示趋势）."""
    if not values:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    vmin, vmax = min(values), max(values)
    if vmax == vmin:
        return blocks[4] * len(values)  # 全平，中间高度
    return "".join(
        blocks[min(int((v - vmin) / (vmax - vmin) * 7), 7)] for v in values
    )


def format_trend_section(
    today_history: list[dict],
    yesterday_close: dict | None,
    multi_day: list[dict],
) -> str:
    """格式化趋势部分（ASCII 图表）.

    参数：
        today_history: 当日各时点检测记录（repo.get_panic_history_today）
        yesterday_close: 昨日收盘波动率（repo.get_volatility_market(yesterday)）
        multi_day: 近 N 日收盘数据列表（含 trade_date, ivix_current, limit_down, limit_up）

    返回：
        趋势部分的文本（不含免责声明，由调用方拼接）
    """
    lines = ["【趋势】"]

    # ── 1. 今日盘中变化（至少 2 个时点才显示趋势）──
    if today_history and len(today_history) >= 2:
        times = [h["check_time"] for h in today_history]
        ivix_vals = [h.get("ivix_current") for h in today_history if h.get("ivix_current")]
        lu_vals = [h.get("limit_up") for h in today_history if h.get("limit_up") is not None]
        ld_vals = [h.get("limit_down") for h in today_history if h.get("limit_down") is not None]

        lines.append("📊 今日盘中变化：")
        if ivix_vals and len(ivix_vals) >= 2:
            spark = _ascii_sparkline(ivix_vals)
            lines.append(f"  iVIX  {spark} {ivix_vals[0]:.1f}→{ivix_vals[-1]:.1f}")
        if lu_vals and len(lu_vals) >= 2:
            spark_u = _ascii_sparkline([float(x) for x in lu_vals])
            lines.append(f"  涨停  {spark_u} {'→'.join(str(x) for x in lu_vals)}")
        if ld_vals and len(ld_vals) >= 2:
            spark_d = _ascii_sparkline([float(x) for x in ld_vals])
            lines.append(f"  跌停  {spark_d} {'→'.join(str(x) for x in ld_vals)}")
        lines.append(f"  时点  {' '.join(t[-5:] for t in times)}")
        lines.append("")

    # ── 2. vs 昨日收盘 ──
    if yesterday_close and today_history:
        latest = today_history[-1]
        lines.append("📊 vs 昨日收盘：")
        y_ivix = yesterday_close.get("ivix_current")
        t_ivix = latest.get("ivix_current")
        if y_ivix and t_ivix:
            max_v = max(y_ivix, t_ivix)
            lines.append(f"  iVIX    昨{y_ivix:.1f}{_ascii_bar(y_ivix, max_v)} → 今{t_ivix:.1f}{_ascii_bar(t_ivix, max_v)}")
        y_ld = yesterday_close.get("limit_down")
        t_ld = latest.get("limit_down")
        if y_ld is not None and t_ld is not None:
            max_v = max(y_ld, t_ld, 1)
            lines.append(f"  跌停    昨{y_ld}{_ascii_bar(float(y_ld), float(max_v))} → 今{t_ld}{_ascii_bar(float(t_ld), float(max_v))}")
        lines.append("")

    # ── 3. 近 N 日趋势 ──
    if multi_day and len(multi_day) >= 2:
        lines.append("📊 近期收盘恐慌趋势：")
        # 按时间正序
        days = list(reversed(multi_day))
        dates = [d["trade_date"][5:] for d in days]  # MM-DD
        ld_series = [d.get("limit_down", 0) or 0 for d in days]
        ivix_series = [d.get("ivix_current", 0) or 0 for d in days]

        # 跌停数趋势
        spark_ld = _ascii_sparkline([float(x) for x in ld_series])
        lines.append(f"  跌停  {spark_ld}")
        lines.append(f"        {' '.join(str(x).rjust(3) for x in ld_series)}")

        # iVIX 趋势
        spark_iv = _ascii_sparkline([float(x) for x in ivix_series])
        lines.append(f"  iVIX  {spark_iv}")
        lines.append(f"        {' '.join(f'{x:3.0f}' for x in ivix_series)}")
        lines.append(f"  日期  {' '.join(d for d in dates)}")

    return "\n".join(lines)
