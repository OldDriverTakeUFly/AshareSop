"""日内振幅 × 做T胜率研究（2026-08-23）.

问题: 日内振幅对做T盈利的影响是否显著、能否用 T-1 可得的振幅代理
（prev_amp / atr5 / atr20）构建门控体系提升胜率.

三段:
A. 可预测性 —— 当日振幅（事后）能否被 T-1 振幅代理预测
B. 机会结构 —— 按当日振幅分桶的完美上限与基线策略表现（描述性，非因果）
C. 因果门控 —— 按 T-1 振幅代理分桶的策略表现 + 门控变体回测，
   训练窗 < SPLIT_DATE 扫描、留出窗 >= SPLIT_DATE 只终验（沿用第二轮纪律）
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.intraday.engine import IntradayConfig, run_backtest
from davis_analyzer.intraday.features import build_features
from davis_analyzer.intraday.report import load_inputs, perfect_ceiling
from davis_analyzer.intraday.strategies import (
    AmplitudeGrid,
    GapDownLongT,
    GapDownSmart,
)

SPLIT_DATE = "20260501"  # 训练/留出切分（与第二轮 gap_smart 验证一致）


# ── 振幅表（T 日事后 + T-1 可得代理） ──

def build_amplitude_table(daily_df: pd.DataFrame) -> pd.DataFrame:
    """amp_t=当日振幅(事后)；prev_amp/atr5/atr20=昨日及以前信息(因果可得)."""
    d = daily_df.sort_values(["ts_code", "trade_date"]).copy()
    amp = (d["high"] - d["low"]) / d["pre_close"]
    d["amp_t"] = amp
    g = amp.groupby(d["ts_code"])
    d["prev_amp"] = g.shift(1)
    d["atr5"] = g.transform(lambda s: s.rolling(5).mean().shift(1))
    d["atr20"] = g.transform(lambda s: s.rolling(20).mean().shift(1))
    return d[["ts_code", "trade_date", "amp_t", "prev_amp", "atr5", "atr20"]]


# ── 变体族（单次引擎跑完全部，避免重复装载数据） ──

def build_variants() -> list:
    smart_base = {"trend_up": True, "vol_ratio1_max": 2.5}
    g3x = GapDownSmart(0.03, exit_time="14:00")
    smart = GapDownSmart(0.03, exit_time="14:00", require=smart_base)
    variants: list = [
        GapDownLongT(0.02),
        GapDownLongT(0.03),
        g3x,   # gap3% + 14:00 退出，无过滤（振幅门控的对照基线）
        smart,  # 现行获胜配置（趋势+量比过滤）
    ]
    # 现行获胜配置 + 振幅门控（atr20 = 前20日平均振幅）
    for th in (0.015, 0.02, 0.025, 0.03):
        variants.append(GapDownSmart(0.03, exit_time="14:00",
                       require={**smart_base, "atr20_min": th}))
    # 换因果代理：前一日振幅
    for th in (0.03, 0.05):
        variants.append(GapDownSmart(0.03, exit_time="14:00",
                       require={**smart_base, "prev_amp_min": th}))
    # 纯振幅门控（不加趋势/量比过滤）——隔离振幅单因子贡献
    for th in (0.02, 0.03):
        variants.append(GapDownSmart(0.03, exit_time="14:00",
                       require={"atr20_min": th}))
    # 标准化跳空门控（norm_gap=gap/atr20，下跳空为负；min 为"浅于该 σ 深度"）
    for th in (-0.7, -1.0, -1.5):
        variants.append(GapDownSmart(0.03, exit_time="14:00",
                       require={"norm_gap_min": th}))
    for th in (-1.0, -1.5):
        variants.append(GapDownSmart(0.03, exit_time="14:00",
                       require={**smart_base, "norm_gap_min": th}))
    # 网格策略本来就吃前日振幅参数——阈值/步长/档数定向扫描
    variants.append(AmplitudeGrid(0.05, 0.015, 2))
    for th in (0.08, 0.10, 0.15):
        for step in (0.025, 0.05):
            for rungs in (2, 3):
                variants.append(AmplitudeGrid(th, step, rungs))
    return variants


def base_names() -> list[str]:
    """B/C 分桶展示用的基线策略名（与 build_variants 前四项一致）."""
    return [s.name for s in build_variants()[:4]] + [
        AmplitudeGrid(0.05, 0.015, 2).name]


# ── 统计助手 ──

def _quintile(s: pd.Series) -> pd.Series:
    """五分位分桶（重复值自动并档，档数可能 <5）."""
    n = pd.qcut(s, q=5, duplicates="drop").cat.categories.size
    labels = [f"Q{i + 1}" for i in range(n)]
    return pd.qcut(s, q=5, labels=labels, duplicates="drop")


def boot_ci(x: pd.Series, n_boot: int = 2000, seed: int = 7) -> tuple[float, float]:
    """均值 的 bootstrap 95% CI（判定门控增量是否稳）。"""
    arr = x.to_numpy(dtype=float)
    if len(arr) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = arr[rng.integers(0, len(arr), size=(n_boot, len(arr)))].mean(axis=1)
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def _perf_rows(g: pd.DataFrame) -> dict:
    ci_lo, ci_hi = boot_ci(g["net_bps"])
    return {
        "n": len(g),
        "win%": round((g["pnl"] > 0).mean() * 100, 1),
        "mean_bps": round(g["net_bps"].mean(), 0),
        "med_bps": round(g["net_bps"].median(), 0),
        "pnl¥": round(g["pnl"].sum(), 0),
        "ci95_lo": round(ci_lo, 0),
        "ci95_hi": round(ci_hi, 0),
    }


def _bucket_perf_table(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows = []
    for k, g in df.groupby(keys, observed=True):
        if not isinstance(k, tuple):
            k = (k,)
        rows.append({**dict(zip(keys, map(str, k))), **_perf_rows(g)})
    return pd.DataFrame(rows)


# ── A. 可预测性 ──

def predictability_text(amp: pd.DataFrame, ceil: pd.DataFrame) -> str:
    a = amp.dropna(subset=["amp_t"]).merge(ceil, on=["ts_code", "trade_date"])
    q = a["amp_t"].quantile([0.25, 0.5, 0.75, 0.9, 0.95])
    lines = [
        "=== A. 振幅可预测性（当日振幅能否被 T-1 信息预测）===",
        f"样本 {len(a):,} 股票日 | amp_t 分位: P25 {q[0.25]:.2%} / "
        f"P50 {q[0.5]:.2%} / P75 {q[0.75]:.2%} / P90 {q[0.9]:.2%} / "
        f"P95 {q[0.95]:.2%}",
    ]
    for col in ("prev_amp", "atr5", "atr20"):
        pair = a[["amp_t", col]].dropna()
        rho = pair["amp_t"].corr(pair[col], method="spearman")
        lines.append(
            f"  spearman(amp_t, {col}) = {rho:+.3f}  (n={len(pair):,})"
        )
    for key in ("atr20", "prev_amp"):
        sub = a.dropna(subset=[key]).copy()
        sub["bucket"] = _quintile(sub[key])
        t = sub.groupby("bucket", observed=True).agg(
            n=("amp_t", "size"), mean_amp=("amp_t", "mean"),
            p_ge2=("amp_t", lambda s: (s >= 0.02).mean()),
            p_ge3=("amp_t", lambda s: (s >= 0.03).mean()),
            med_ceil=("ceiling_net_bps", "median"),
        )
        lines.append(f"\n按 {key} 五分位（T-1 可得）→ 次日机会:")
        lines.append(t.to_string(
            formatters={"mean_amp": "{:.2%}".format, "p_ge2": "{:.0%}".format,
                        "p_ge3": "{:.0%}".format, "med_ceil": "{:+.0f}".format}))
    return "\n".join(lines)


# ── B. 当期振幅与做T机会（描述性） ──

def contemporaneous_text(days: pd.DataFrame, show: list[str]) -> str:
    lines = ["", "=== B. 当期振幅分桶 × 策略表现（事后分桶，非因果）==="]
    sub = days[days["strategy"].isin(show)].dropna(subset=["amp_t"]).copy()
    sub["bucket"] = _quintile(sub["amp_t"])
    t = _bucket_perf_table(sub, ["bucket", "strategy"])
    lines.append(t.to_string(index=False))
    return "\n".join(lines)


# ── C. 因果门控 ──

def causal_text(days: pd.DataFrame, show: list[str]) -> str:
    lines = ["", "=== C1. T-1 振幅代理（atr20）分桶 × 基线策略（因果分桶）==="]
    sub = days[days["strategy"].isin(show)].dropna(subset=["atr20"]).copy()
    sub["bucket"] = _quintile(sub["atr20"])
    t = _bucket_perf_table(sub, ["bucket", "strategy"])
    lines.append(t.to_string(index=False))
    return "\n".join(lines)


def variants_text(days: pd.DataFrame, split: str) -> pd.DataFrame:
    """C2: 全部变体 × 训练/留出/全窗口 汇总（含均值 bootstrap CI）."""
    rows = []
    d2 = days[days["trade_date"] >= split]
    d1 = days[days["trade_date"] < split]
    for s in days["strategy"].unique():
        for label, df in (("train", d1), ("holdout", d2), ("all", days)):
            g = df[df["strategy"] == s]
            if g.empty:
                continue
            row = {"strategy": s, "window": label, **_perf_rows(g)}
            rows.append(row)
    return pd.DataFrame(rows)


# ── 主流程 ──

def run_study(
    db_path: str | None = None, out_dir: str | Path | None = None,
    split: str = SPLIT_DATE,
) -> tuple[str, list[Path]]:
    """装载数据 → 单次引擎跑全部变体 → A/B/C 分析 → 导出 CSV."""
    minute, daily = load_inputs(db_path)
    feats = build_features(minute, daily)
    variants = build_variants()
    results = run_backtest(minute, daily, variants, IntradayConfig(),
                           features_df=feats)
    if results.empty:
        return "回测无成交记录", []

    amp = build_amplitude_table(daily)
    ceil = perfect_ceiling(minute, daily)
    days = (results
            .merge(amp, on=["ts_code", "trade_date"], how="left")
            .merge(ceil, on=["ts_code", "trade_date"], how="left"))
    days["window"] = np.where(days["trade_date"] < split, "train", "holdout")

    show = base_names()
    summary = variants_text(days, split)
    text = "\n".join([
        predictability_text(amp, ceil),
        contemporaneous_text(days, show),
        causal_text(days, show),
        "", "=== C2. 变体 × 窗口汇总（train 扫描 / holdout 终验）===",
        summary.to_string(index=False),
    ])

    import time as _time
    stamp = _time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(out_dir) if out_dir else (Path(__file__).parent / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        out_dir / f"amp_study_days_{stamp}.csv",
        out_dir / f"amp_study_summary_{stamp}.csv",
    ]
    days.to_csv(paths[0], index=False, encoding="utf-8-sig")
    summary.to_csv(paths[1], index=False, encoding="utf-8-sig")
    for p in paths:
        logger.info("振幅研究导出: {}", p)
    return text, paths
