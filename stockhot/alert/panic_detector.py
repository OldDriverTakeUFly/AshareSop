"""盘中恐慌信号检测器 — 三大信号独立检测.

数据源（盘中实时）：
- RV20：DAL index_daily 历史 + AKShare stock_zh_index_spot_em 实时价替换今日点
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


def _fetch_realtime_index_prices() -> dict[str, float]:
    """从 AKShare 获取实时指数价（盘中用）.

    返回 {ts_code: 最新价}。
    """
    import akshare as ak
    from stockhot.core.rate_limiter import safe_akshare_call

    df = safe_akshare_call(ak.stock_zh_index_spot_em, symbol="沪深重要指数")
    if df is None or df.empty:
        return {}

    # AKShare 返回"代码"列如 000001/399001，需映射到 ts_code
    price_map = {}
    for _, row in df.iterrows():
        code = str(row.get("代码", ""))
        price = pd.to_numeric(row.get("最新价"), errors="coerce")
        if pd.isna(price):
            continue
        # 代码 → ts_code
        if code.startswith("000300"):
            price_map["000300.SH"] = float(price)
        elif code.startswith("000688"):
            price_map["000688.SH"] = float(price)
        elif code.startswith("000001") and code not in price_map:
            price_map["000001.SH"] = float(price)
        elif code.startswith("399001"):
            price_map["399001.SZ"] = float(price)
        elif code.startswith("399006"):
            price_map["399006.SZ"] = float(price)
    return price_map


def _detect_rv_volatility() -> tuple[list[IndexVolatility], SignalResult]:
    """检测 RV20 历史分位（盘中实时）.

    用 DAL index_daily 拿历史 250 日 close，最后一点替换为实时价，算 RV20 + 分位。
    """
    from stockhot.data_layer import get_repository

    repo = get_repository()
    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=400)).strftime("%Y%m%d")

    # 拿实时价
    try:
        realtime_prices = _fetch_realtime_index_prices()
    except Exception as e:
        logger.warning(f"[panic] realtime index prices failed: {e}")
        realtime_prices = {}

    indices_vol: list[IndexVolatility] = []

    for ts_code in _INDICES:
        try:
            df = repo.get_index_daily(ts_code, start_date, end_date)
            if df.empty or len(df) < 30:
                continue
            closes = df["close"].astype(float).values

            # 盘中：替换最后一点为实时价（若可得）
            if ts_code in realtime_prices:
                rt = realtime_prices[ts_code]
                if rt > 0:
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
    return indices_vol, signal


# ═══════════════════════════════════════════════════════════════════
# 信号 2：涨跌停行为面
# ═══════════════════════════════════════════════════════════════════


def _detect_limit_behavior() -> SignalResult:
    """检测涨跌停行为面（盘中实时，AKShare 东财源）."""
    import akshare as ak
    from stockhot.core.rate_limiter import safe_akshare_call

    today = date.today().strftime("%Y%m%d")
    n_up = n_broken = n_down = 0

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

    if n_up + n_down == 0:
        return SignalResult(name="行为面恐慌", triggered=False, detail="数据不可用", available=False)

    ratio = n_up / max(n_down, 1)
    down_ratio = n_down / (n_up + n_down)
    triggered = ratio < _LIMIT_UP_DOWN_RATIO_THRESHOLD or down_ratio > _DOWN_RATIO_THRESHOLD

    detail = (f"涨停{n_up}/跌停{n_down}/炸板{n_broken}，"
              f"涨跌停比{ratio:.2f}{'(<0.5 恐慌抛售)' if ratio < _LIMIT_UP_DOWN_RATIO_THRESHOLD else ''}，"
              f"跌停占比{down_ratio:.0%}{f'(>50% 系统性恐慌)' if down_ratio > _DOWN_RATIO_THRESHOLD else ''}")

    return SignalResult(
        name="行为面恐慌抛售",
        triggered=triggered,
        detail=detail,
    )


# ═══════════════════════════════════════════════════════════════════
# 信号 3：iVIX / V-R 期权面
# ═══════════════════════════════════════════════════════════════════


def _classify_ivix_level(ivix_value: float) -> str:
    """iVIX 绝对值 → 恐慌等级（复用 volatility analyzer 的 7 档）."""
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

    # 分时 iVIX（盘中实时）
    try:
        df = safe_akshare_call(ak.index_option_50etf_min_qvix)
        if df is not None and not df.empty:
            ivix_value = float(df.iloc[-1]["qvix"])
    except Exception as e:
        logger.warning(f"[panic] intraday iVIX failed: {e}")

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
                rt_prices = _fetch_realtime_index_prices()
                if "000001.SH" in rt_prices:
                    sse_closes = df_idx["close"].astype(float).values
                    sse_closes[-1] = rt_prices["000001.SH"]
                else:
                    sse_closes = df_idx["close"].astype(float).values
            except Exception:
                sse_closes = df_idx["close"].astype(float).values

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
# 综合检测 + 消息格式化
# ═══════════════════════════════════════════════════════════════════


def detect_panic_signals() -> PanicReport:
    """盘中恐慌信号综合检测（三大信号独立）.

    每个信号独立 try/except，单源失败降级为"数据不可用"，不影响其他信号。
    """
    report = PanicReport(
        trade_date=date.today().isoformat(),
        timestamp=time.strftime("%H:%M"),
    )

    # 信号 1：RV20
    try:
        indices_vol, sig_rv = _detect_rv_volatility()
        report.volatility_indices = indices_vol
        report.signals.append(sig_rv)
    except Exception as e:
        logger.error(f"[panic] RV20 detection error: {e}")
        report.signals.append(SignalResult("系统性恐慌", False, f"检测异常: {e}", available=False))

    # 信号 2：涨跌停行为
    try:
        report.signals.append(_detect_limit_behavior())
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

    return report


def format_alert_message(report: PanicReport) -> str:
    """格式化恐慌预警消息（飞书文本）."""
    lines = []
    lines.append(f"🔴 恐慌预警 [{report.trade_date} {report.timestamp}]")
    lines.append("")

    # 触发条件
    if report.triggered_names:
        lines.append(f"触发条件：{' / '.join(report.triggered_names)}")
    else:
        lines.append("（当前无信号触发）")
    lines.append("")

    # 波动率温度
    if report.volatility_indices:
        lines.append("【波动率温度】")
        for i in sorted(report.volatility_indices, key=lambda x: -x.rv20_pct):
            bar = "█" * max(1, int(i.rv20_pct / 25))
            lines.append(f"  {i.name:8s} RV20={i.rv20:5.1f}% P{i.rv20_pct:2.0f} {bar} {i.panic_level}")
        lines.append("")

    # 各信号详情
    for sig in report.signals:
        mark = "🔴" if sig.triggered else ("⚪" if not sig.available else "🟢")
        lines.append(f"{mark} {sig.name}：{sig.detail}")
    lines.append("")

    lines.append("⚠️ 信号仅提示恐慌升温，不构成交易建议。减仓决策请结合持仓与风控。")
    return "\n".join(lines)
