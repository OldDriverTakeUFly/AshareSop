"""锁仓因子（缩量/低换手/早盘/硬板）验证：OOS 复核 + 扰动 + 组合回测三档.

流程对齐既有增强过滤验证协议（phase2 结论纪律）：
1. IS(2021-01→2025-06) / OOS(2025-07→) 方向一致性
2. 阈值 ±20% 扰动 dir_stable
3. first_board 预设 × 各过滤变体 三档成交敏感性对照
输出 davis_analyzer/limitup/reports/lockup_factor_validation.md
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.limitup import db, patterns
from davis_analyzer.limitup.engine import LimitupBacktestConfig, run_sensitivity
from davis_analyzer.limitup.events import build_events
from davis_analyzer.limitup.sentiment import build_market_regime
from davis_analyzer.limitup.strategies import PRESETS, apply_preset

START, END = "20210104", "20260814"
OOS_START = "20250701"
MIN_N = 50


def factor_edge(df: pd.DataFrame, mask: pd.Series, name: str) -> dict:
    """命中组 vs 未命中组的 T+1 开盘均值差（组内样本足才计）。"""
    hit, miss = df[mask], df[~mask]
    return {
        "因子": name, "命中n": len(hit), "未命中n": len(miss),
        "命中均值": hit["ret_open_1"].mean() if len(hit) else np.nan,
        "未命中均值": miss["ret_open_1"].mean() if len(miss) else np.nan,
        "差": (hit["ret_open_1"].mean() - miss["ret_open_1"].mean())
        if len(hit) and len(miss) else np.nan,
        "命中晋级率": hit["promoted"].mean() if len(hit) else np.nan,
    }


def dir_stable(base: float, lo: float, hi: float) -> bool:
    return bool(np.sign(base) == np.sign(lo) == np.sign(hi) and np.sign(base) != 0)


def main() -> None:
    conn = db.connect()
    try:
        logger.info("构建事件 [{} → {}]", START, END)
        ev = build_events(conn, START, END)
        ev = patterns.attach_pattern_features(ev, conn, START, END)
        fb = ev[ev["consecutive_boards"] == 1].copy()
        fb = fb[fb["ret_open_1"].notna()].copy()
        mf = pd.read_sql_query(
            "SELECT trade_date, ts_code, sell_lg_amount, sell_elg_amount, "
            "sell_sm_amount, sell_md_amount FROM moneyflow "
            "WHERE trade_date>=? AND trade_date<=?",
            conn, params=(START, END),
        )
        sell_total = (mf["sell_sm_amount"].fillna(0) + mf["sell_md_amount"].fillna(0)
                      + mf["sell_lg_amount"].fillna(0) + mf["sell_elg_amount"].fillna(0))
        mf["lg_sell_share"] = ((mf["sell_lg_amount"].fillna(0)
                                + mf["sell_elg_amount"].fillna(0))
                               / sell_total.where(sell_total > 0))
        fb = fb.merge(mf[["ts_code", "trade_date", "lg_sell_share"]],
                      on=["ts_code", "trade_date"], how="left")

        is_fb = fb[fb["trade_date"] < OOS_START]
        oos_fb = fb[fb["trade_date"] >= OOS_START]
        parts = [f"# 锁仓因子验证 [{START} → {END}]（OOS 自 {OOS_START}）\n",
                 f"首板样本 全={len(fb)} / IS={len(is_fb)} / OOS={len(oos_fb)}\n"]

        # ── 1. 四因子 IS/OOS 边际 ──
        def masks(df: pd.DataFrame) -> dict[str, pd.Series]:
            return {
                "缩量 vol_ratio<1": df["vol_ratio_20"] < 1.0,
                "低换手 turnover<5%": df["turnover_rate"] < 5.0,
                "早盘封板": df["first_seal_time"].fillna("999999") < "100000",
                "硬板未炸": df["broken_count"] == 0,
                "大单主导": df["lg_sell_share"] >= 0.50,
                "强封单 seal>=5%": df["seal_ratio"] >= 0.05,
            }

        rows = []
        for name, m in masks(fb).items():
            r_all = factor_edge(fb, m, name)
            r_is = factor_edge(is_fb, masks(is_fb)[name], "")
            r_oos = factor_edge(oos_fb, masks(oos_fb)[name], "")
            rows.append({**r_all, "IS差": r_is["差"], "OOS差": r_oos["差"],
                         "OOS命中n": r_oos["命中n"],
                         "IS/OOS同向": bool(np.sign(r_is["差"]) == np.sign(r_oos["差"])
                                            and np.sign(r_is["差"]) != 0)})
        t1 = pd.DataFrame(rows)
        parts.append("## 1. 单因子边际（命中−未命中 的 T+1 开盘均值差）\n\n"
                     + t1.to_markdown(index=False, floatfmt=".4f") + "\n")

        # ── 2. 缩量阈值 ±20% 扰动 ──
        rows = []
        for thr in (0.8, 1.0, 1.2):
            m = fb["vol_ratio_20"] < thr
            r = factor_edge(fb, m, f"vol_ratio<{thr}")
            rows.append({"阈值": f"<{thr}", **{k: v for k, v in r.items() if k != "因子"}})
        pert = pd.DataFrame(rows)
        base_edge = pert.loc[pert["阈值"] == "<1.0", "差"].iloc[0]
        parts.append("## 2. 缩量阈值扰动（±20%）\n\n" + pert.to_markdown(index=False, floatfmt=".4f")
                     + f"\n\ndir_stable = **{dir_stable(base_edge, pert.loc[0, '差'], pert.loc[2, '差'])}**\n")

        # ── 3. 重叠度 ──
        m_vol = fb["vol_ratio_20"] < 1.0
        m_enh = (fb["lg_sell_share"] >= 0.50) & (fb["seal_ratio"] >= 0.05)
        both = (m_vol & m_enh).sum()
        parts.append(
            "## 3. 锁仓(缩量) × 现有增强(大单×强封) 重叠\n\n"
            f"缩量命中 {m_vol.sum()}，增强命中 {m_enh.sum()}，交集 {both}（占缩量 "
            f"{both / max(m_vol.sum(), 1):.1%}）——"
            f"{'低重叠=正交，可叠加' if both < 0.3 * max(m_vol.sum(), 1) else '高重叠，择一即可'}\n")

        # ── 4. 组合回测三档 ──
        regime = build_market_regime(conn, START, END)
        cands_all = apply_preset(ev, PRESETS["first_board"], regime=regime)
        # preset 输出自带 vol_ratio_20/turnover_rate/seal_ratio，仅补 lg_sell_share
        cands_all = cands_all.merge(
            fb[["ts_code", "trade_date", "lg_sell_share"]],
            on=["ts_code", "trade_date"], how="left")
        variants: dict[str, pd.DataFrame] = {
            "基准(预设原样)": cands_all,
            "+缩量(vol<1)": cands_all[cands_all["vol_ratio_20"] < 1.0],
            "+大单×强封": cands_all[(cands_all["lg_sell_share"] >= 0.50)
                                    & (cands_all["seal_ratio"] >= 0.05)],
            "+缩量+强封": cands_all[(cands_all["vol_ratio_20"] < 1.0)
                                    & (cands_all["seal_ratio"] >= 0.05)],
        }
        prices = db.read_daily_prices(
            conn, sorted(pd.concat(list(variants.values()))["ts_code"].unique()),
            START, END)
        cfg = LimitupBacktestConfig()
        lines = ["| 变体 | scenario | 总收益% | 年化% | 夏普 | 回撤% | 胜率% | 笔数 | 日均信号 |",
                 "|---|---|---|---|---|---|---|---|---|"]
        for vname, cands in variants.items():
            if cands.empty:
                continue
            sens = run_sensitivity(cands, prices, PRESETS["first_board"], cfg)
            per_day = len(cands) / 1345
            for scen, st in sens.items():
                if scen == "always":
                    continue
                lines.append(f"| {vname} | {scen} | {st.total_return_pct:.0f} | "
                             f"{st.annualized_return_pct:.1f} | {st.sharpe_ratio:.2f} | "
                             f"{st.max_drawdown_pct:.1f} | {st.win_rate_pct:.1f} | "
                             f"{st.num_trades} | {per_day:.2f} |")
        parts.append("## 4. 组合回测三档敏感性（全窗口）\n\n" + "\n".join(lines) + "\n")

        out = "davis_analyzer/limitup/reports/lockup_factor_validation.md"
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))
        print(f"报告已生成: {out}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
