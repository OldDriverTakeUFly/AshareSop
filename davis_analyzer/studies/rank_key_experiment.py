"""rank_key 排序方案对照：封单比(现状) vs 缩量优先 vs 组合分（锁仓因子作为排序键）.

排序只影响「候选多于仓位时先买谁」，不减少可交易频率——与硬过滤的本质区别。
组合分 = 日内截面 pct_rank(seal_ratio) + (1 − pct_rank(vol_ratio_20))（先验等权，无跨期信息）。
输出 davis_analyzer/limitup/reports/rank_key_experiment.md
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
from loguru import logger

from davis_analyzer.limitup import db, patterns
from davis_analyzer.limitup.engine import LimitupBacktestConfig, run_sensitivity
from davis_analyzer.limitup.events import build_events
from davis_analyzer.limitup.sentiment import build_market_regime
from davis_analyzer.limitup.strategies import PRESETS, apply_preset

START, END = "20210104", "20260814"


def add_rank_columns(cands: pd.DataFrame) -> pd.DataFrame:
    c = cands.copy()
    # 日内截面分位（无跨期信息，排序键合法）
    g = c.groupby("trade_date")
    c["neg_vol"] = -c["vol_ratio_20"]
    c["lockup_score"] = (
        g["seal_ratio"].rank(pct=True)
        + (1 - g["vol_ratio_20"].rank(pct=True))
    )
    return c


def main() -> None:
    conn = db.connect()
    try:
        parts = ["# rank_key 排序方案对照（锁仓因子作排序键）\n"]
        cfg = LimitupBacktestConfig()
        for tag, start, end in (("全窗口", START, END), ("OOS", "20250701", END)):
            logger.info("构建 [{} → {}]", start, end)
            ev = build_events(conn, start, end)
            ev = patterns.attach_pattern_features(ev, conn, start, end)
            regime = build_market_regime(conn, start, end)
            cands = add_rank_columns(apply_preset(ev, PRESETS["first_board"], regime=regime))
            prices = db.read_daily_prices(conn, sorted(cands["ts_code"].unique()), start, end)
            lines = [
                f"## {tag} [{start}→{end}]（候选 {len(cands)}，日均 {len(cands)/1345 if tag=='全窗口' else len(cands)/280:.2f}）\n",
                "| 排序方案 | scenario | 总收益% | 年化% | 夏普 | 回撤% | 胜率% | 笔数 |",
                "|---|---|---|---|---|---|---|---|",
            ]
            for name, key in (("A 封单比降序(现状)", "seal_ratio"),
                              ("B 缩量优先", "neg_vol"),
                              ("C 组合分(强封+缩量)", "lockup_score")):
                preset = replace(PRESETS["first_board"], rank_key=key)
                sens = run_sensitivity(cands, prices, preset, cfg)
                for scen, st in sens.items():
                    if scen == "always":
                        continue
                    lines.append(f"| {name} | {scen} | {st.total_return_pct:.0f} | "
                                 f"{st.annualized_return_pct:.1f} | {st.sharpe_ratio:.2f} | "
                                 f"{st.max_drawdown_pct:.1f} | {st.win_rate_pct:.1f} | "
                                 f"{st.num_trades} |")
            parts += lines + [""]
        out = "davis_analyzer/limitup/reports/rank_key_experiment.md"
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))
        print(f"报告已生成: {out}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
