"""温度预测力校准:rank IC / 五分组价差 / walk-forward 三档权重(spec §8)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from scipy import stats

from davis_analyzer.config import THERMOMETER_REPORTS_DIR
from davis_analyzer.constants import THERMOMETER_CALIBRATION_TARGETS

_FAMILY_COLS = ["mom_score", "flow_score", "vol_score", "trend_score", "limit_score"]


# ── 面板构建 ───────────────────────────────────────────────────────────

def build_eval_panel(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """温度历史 + 前向收益(sw_daily close 口径)合并面板."""
    th = pd.read_sql_query(
        "SELECT trade_date, level, index_code, temperature, composite_z, "
        "mom_score, flow_score, vol_score, trend_score, limit_score "
        "FROM thermometer_sector WHERE trade_date>=? AND trade_date<=?",
        conn, params=(start, end))
    px = pd.read_sql_query(
        "SELECT ts_code, trade_date, close FROM sw_daily WHERE trade_date>=? AND trade_date<=? ",
        conn, params=(start, end)).rename(columns={"ts_code": "index_code"})
    for h in (5, 10, 20):
        px[f"fwd{h}"] = px.groupby("index_code")["close"].transform(
            lambda s, h=h: s.shift(-h) / s - 1)
    return th.merge(px[["index_code", "trade_date", "fwd5", "fwd10", "fwd20"]],
                    on=["index_code", "trade_date"], how="inner")


# ── 检验统计 ───────────────────────────────────────────────────────────

def daily_rank_ic(panel: pd.DataFrame, horizon: int) -> pd.Series:
    """逐日横截面 spearman(temperature, fwd{horizon}) 的时序."""
    col = f"fwd{horizon}"

    def _ic(g: pd.DataFrame) -> float:
        if len(g) < 5 or g[col].nunique() < 2 or g["temperature"].nunique() < 2:
            return np.nan
        return float(stats.spearmanr(g["temperature"], g[col]).statistic)

    return panel.groupby("trade_date").apply(_ic, include_groups=False).dropna()


def quintile_report(panel: pd.DataFrame, horizon: int) -> dict:
    """逐日温度五分位组的前向收益均值 + top-bottom 价差单边 t 检验."""
    col = f"fwd{horizon}"

    def _q(g: pd.DataFrame) -> pd.Series:
        try:
            q = pd.qcut(g["temperature"], 5, labels=False, duplicates="drop")
        except ValueError:
            return pd.Series(dtype=float)
        return g.groupby(q)[col].mean()

    qs = panel.groupby("trade_date").apply(_q, include_groups=False).reset_index()
    out = {f"q{i + 1}": float(qs[i].mean()) for i in range(5) if i in qs.columns}
    tagged = panel.assign(q=panel.groupby("trade_date")["temperature"].transform(
        lambda s: pd.qcut(s, 5, labels=False, duplicates="drop")))
    t = tagged[tagged["q"] == 4][col].dropna()
    b = tagged[tagged["q"] == 0][col].dropna()
    if len(t) > 30 and len(b) > 30:
        res = stats.ttest_ind(t, b, equal_var=False)
        out["spread"] = float(t.mean() - b.mean())
        # 单边:t>0 且 p/2 < alpha 才显著
        out["spread_p"] = float(res.pvalue / 2 if res.statistic > 0
                                else 1 - res.pvalue / 2)
    return out


# ── walk-forward 三档权重(spec §8.3) ───────────────────────────────────

def _eval_score_ic(va: pd.DataFrame, score: pd.Series) -> list[float]:
    """验证段逐日 spearman(score, fwd10)."""
    tmp = va.assign(_s=score)

    def _ic(g: pd.DataFrame) -> float:
        if len(g) < 5 or g["_s"].nunique() < 2:
            return np.nan
        return float(stats.spearmanr(g["_s"], g["fwd10"]).statistic)

    vals = tmp.groupby("trade_date").apply(_ic, include_groups=False)
    return [v for v in vals if v == v]  # 滤 NaN


def walk_forward(panel: pd.DataFrame,
                 train_days: int = 486, valid_days: int = 126) -> dict:
    """滚动 2年训练/6月验证;返回 ①先验 ②IC加权 ③ridge 三档的 OOS IC 汇总."""
    from davis_analyzer.constants import THERMOMETER_WEIGHTS

    dates = sorted(panel["trade_date"].unique())
    oof: dict[str, list[float]] = {"prior": [], "ic_weighted": [], "ridge": []}
    i = train_days
    while i + valid_days <= len(dates):
        tr = panel[panel["trade_date"].isin(dates[i - train_days:i])]
        va = panel[panel["trade_date"].isin(dates[i:i + valid_days])]
        if len(va) == 0 or len(tr) == 0:
            i += valid_days
            continue
        # ① 先验权重(即已部署温度)
        oof["prior"] += _eval_score_ic(va, va["temperature"].rank())
        # ② IC 加权族权重(train 段各族分 spearman IC,∝max(IC,0) 归一)
        ic_w = {}
        for c in _FAMILY_COLS:
            r = tr.groupby("trade_date").apply(
                lambda g, c=c: (stats.spearmanr(g[c], g["fwd10"]).statistic
                                if len(g) >= 5 and g[c].nunique() > 1 else np.nan),
                include_groups=False)
            ic_w[c] = max(float(r.mean()) if r.notna().any() else 0.0, 0.0)
        s = sum(ic_w.values())
        if s > 0:
            comp = sum(va[c] * (ic_w[c] / s) for c in _FAMILY_COLS)
            oof["ic_weighted"] += _eval_score_ic(va, comp)
        # ③ 去均值截面 ridge(train 拟合,OOS 应用)
        Xtr = tr[_FAMILY_COLS].to_numpy(dtype=float)
        ytr = tr["fwd10"].to_numpy(dtype=float)
        ok = np.isfinite(Xtr).all() and np.isfinite(ytr).all()
        if ok:
            mu_x, mu_y = Xtr.mean(0), ytr.mean()
            beta, *_ = np.linalg.lstsq(Xtr - mu_x, ytr - mu_y, rcond=None)
            Xva = va[_FAMILY_COLS].to_numpy(dtype=float)
            if np.isfinite(Xva).all():
                oof["ridge"] += _eval_score_ic(
                    va, pd.Series((Xva - mu_x) @ beta, index=va.index))
        i += valid_days

    def _sum(vals: list[float]) -> dict:
        ser = pd.Series(vals, dtype=float)
        if ser.empty:
            return {"oos_ic_mean": float("nan"), "oos_icir": float("nan"),
                    "n_days": 0}
        return {"oos_ic_mean": float(ser.mean()),
                "oos_icir": float(ser.mean() / ser.std()) if ser.std() > 0 else 0.0,
                "n_days": int(len(ser))}

    return {k: _sum(v) for k, v in oof.items() if v}


def _verdict(r: dict, spread_p: float) -> bool:
    """达标线判定(spec §8.2):OOS IC/ICIR + 分组价差联合通过."""
    tgt = THERMOMETER_CALIBRATION_TARGETS
    return (r["oos_ic_mean"] >= tgt["min_ic"]
            and r["oos_icir"] >= tgt["min_icir"]
            and spread_p < tgt["spread_pvalue"])


# ── 主入口 ─────────────────────────────────────────────────────────────

def run_calibration(conn: sqlite3.Connection, start: str, end: str) -> Path:
    """全窗口校准 → 报告落 REPORTS_DIR;未达标报告明示「回到用户决策」."""
    panel = build_eval_panel(conn, start, end)
    if panel.empty:
        raise RuntimeError("评估面板为空:先 backfill 产出温度历史")
    ic5, ic10, ic20 = (daily_rank_ic(panel, h) for h in (5, 10, 20))
    quint = quintile_report(panel, 10)
    wf = walk_forward(panel)
    verdict = {name: _verdict(r, quint.get("spread_p", 1.0)) for name, r in wf.items()}

    q_str = ", ".join(f"q{i}: {quint.get(f'q{i}', float('nan')):.5f}"
                      for i in range(1, 6) if f"q{i}" in quint)
    lines = [
        f"# 温度计校准报告 [{start} → {end}]",
        "",
        f"- 样本: {len(panel):,} 行 / {panel['trade_date'].nunique()} 个截面日",
        f"- 全样本 rank IC: 5日 {ic5.mean():.4f}(IR {ic5.mean() / ic5.std() if ic5.std() > 0 else 0:.2f})"
        f" / 10日 {ic10.mean():.4f}(IR {ic10.mean() / ic10.std() if ic10.std() > 0 else 0:.2f})"
        f" / 20日 {ic20.mean():.4f}(IR {ic20.mean() / ic20.std() if ic20.std() > 0 else 0:.2f})",
        f"- 五分组 10日均值: {q_str}",
        f"- top-bottom 价差 {quint.get('spread', float('nan')):.5f},"
        f" 单边 p={quint.get('spread_p', float('nan')):.4f}",
        "",
        "## walk-forward 样本外(2年训练/6月验证滚动)",
    ]
    for name, r in wf.items():
        lines.append(
            f"- {name}: IC {r['oos_ic_mean']:.4f} / ICIR {r['oos_icir']:.2f} / "
            f"{r['n_days']} 验证日 → {'达标' if verdict[name] else '未达标'}")
    tgt = THERMOMETER_CALIBRATION_TARGETS
    passed = any(verdict.values())
    conclusion = ("通过(以上任一档达标即视为温度具备样本外预测力)" if passed
                  else "未通过——按 spec §8.4 停止并回到用户决策,不静默降级")
    lines += [
        "",
        f"验收线: IC≥{tgt['min_ic']}, ICIR≥{tgt['min_icir']}, 价差 p<{tgt['spread_pvalue']}",
        f"结论: {conclusion}",
    ]
    out = THERMOMETER_REPORTS_DIR / f"{start}-{end}_校准报告.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    logger.info("校准报告: {}", out)
    return out
