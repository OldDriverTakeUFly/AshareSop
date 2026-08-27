"""实验0008 编排:加载→状态机→回调→特征→退出规则→bootstrap→汇总。

全量预估超 30 分钟(加载 ~6 分钟 + 扫描 + 15 规则回放 + 18 组敏感性),
按 AGENTS.md 长回测规范脱离会话运行;先 --codes-limit 20 烟测再全量。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bootstrap import run_bootstrap                          # noqa: E402
from common import OUT_DIR, load_market, log                  # noqa: E402
from exits import default_rules, run_exit_rule                # noqa: E402
from features import exante_features, pullback_features       # noqa: E402
from pullback import LabelerParams, find_pullbacks            # noqa: E402
from trend_machine import Episode, TrendParams, find_episodes  # noqa: E402


def scan(md, p: TrendParams, lp: LabelerParams, start: int, end: int,
         with_features: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """全市场扫描,返回 (episodes_df, pullbacks_df)。with_features=False 供敏感性复用。"""
    ep_rows, pb_rows = [], []
    t0 = time.time()
    date_of = {i: int(d) for d, i in md.cal_pos.items()}
    for k, (code, s) in enumerate(md.stocks.items(), 1):
        if k % 500 == 0:
            log(f"  扫描 {k}/{len(md.stocks)} 只, episodes={len(ep_rows)}, pullbacks={len(pb_rows)}")
        for ep in find_episodes(code, s, p):
            d0 = date_of[ep.entry_pos]
            if d0 < start or d0 > end:
                continue
            hist = md.valid_pos[code]
            hist = hist[hist < ep.entry_pos][-250:]
            prior = s["c"][hist]
            prior = prior[np.isfinite(prior)]
            pos_pct = (float((prior < s["c"][ep.entry_pos]).sum()) / prior.size * 100.0
                       if prior.size >= 120 else np.nan)
            ep_rows.append({"ts_code": code, "entry_date": d0,
                            "exit_date": date_of[min(ep.exit_pos, s["end"])],
                            "entry_pos": ep.entry_pos, "exit_pos": ep.exit_pos,
                            "exit_reason": ep.exit_reason, "peak_close": ep.peak_close,
                            "pos_pct_entry": pos_pct})
            for pb in find_pullbacks(code, s, ep, lp):
                row = {"ts_code": code, "ep_entry_pos": ep.entry_pos,
                       "ep_entry_date": d0, "idx": pb.idx,
                       "peak_pos": pb.peak_pos, "peak_date": date_of[pb.peak_pos],
                       "trough_pos": pb.trough_pos, "trough_date": date_of[pb.trough_pos],
                       "end_pos": pb.end_pos, "end_date": date_of[min(pb.end_pos, s["end"])],
                       "peak_px": pb.peak_px, "trough_px": pb.trough_px,
                       "outcome": pb.outcome}
                if with_features:
                    row.update(pullback_features(code, s, ep, pb,
                                                 md.aux_inf, md.aux_mf))
                    row.update(exante_features(code, s, ep, pb))
                pb_rows.append(row)
    log(f"扫描完成 episodes={len(ep_rows)} pullbacks={len(pb_rows)} "
        f"耗时 {(time.time() - t0) / 60:.1f} 分钟")
    return pd.DataFrame(ep_rows), pd.DataFrame(pb_rows)


def replay_exits(md, eps_df: pd.DataFrame, lp: LabelerParams) -> pd.DataFrame:
    """对每个 episode 回放全部规则(按 ts_code 分桶,避免 O(股票×episode))。"""
    rules = default_rules()
    eps_by_code: dict[str, list] = {}
    for r in eps_df.itertuples():
        eps_by_code.setdefault(r.ts_code, []).append(r)
    rows = []
    t0 = time.time()
    for code, ep_list in eps_by_code.items():
        s = md.stocks.get(code)
        if s is None:
            continue
        for r in ep_list:
            ep = Episode(code, int(r.entry_pos), int(r.exit_pos),
                         r.exit_reason, r.peak_close)
            for rule in rules:
                m = run_exit_rule(code, s, ep, rule, lp)
                m.update({"ts_code": code, "ep_entry_pos": int(r.entry_pos),
                          "ep_entry_date": int(r.entry_date),
                          "pos_pct_entry": getattr(r, "pos_pct_entry", np.nan),
                          "exit_date": int(md.cal[m["exit_pos"]])})
                rows.append(m)
        if len(rows) and len(rows) % 100000 < 15:
            log(f"  规则回放已产出 {len(rows)} 行")
    log(f"规则回放完成 rows={len(rows)} 耗时 {(time.time() - t0) / 60:.1f} 分钟")
    return pd.DataFrame(rows)


def sensitivity(md, start: int, end: int, out_dir: str) -> None:
    """参数敏感性网格:趋势机 9 组 + 标注器 9 组,逐组落盘。"""
    trend_grid = [(nh, dd) for nh in (40, 60, 120) for dd in (0.15, 0.20, 0.25)]
    for nh, dd in trend_grid:
        _, pb = scan(md, TrendParams(newhigh_win=nh, exit_dd=-dd),
                     LabelerParams(), start, end, with_features=False)
        vc = pb["outcome"].value_counts(normalize=True).to_dict()
        rec = {"newhigh_win": nh, "exit_dd": -dd, "n": len(pb),
               "continue": vc.get("continue"), "terminate": vc.get("terminate"),
               "timeout": vc.get("timeout")}
        _append_json(f"{out_dir}/sensitivity.json", rec)
        log(f"敏感性 趋势机 {nh}/{dd}: n={len(pb)} continue={vc.get('continue', 0):.3f}")
    lab_grid = [(td, lw) for td in (0.20, 0.25, 0.30) for lw in (30, 40, 60)]
    for td, lw in lab_grid:
        _, pb = scan(md, TrendParams(),
                     LabelerParams(term_dd=-td, term_low_win=lw), start, end,
                     with_features=False)
        vc = pb["outcome"].value_counts(normalize=True).to_dict()
        rec = {"term_dd": -td, "term_low_win": lw, "n": len(pb),
               "continue": vc.get("continue"), "terminate": vc.get("terminate")}
        _append_json(f"{out_dir}/sensitivity_labeler.json", rec)
        log(f"敏感性 标注器 {td}/{lw}: n={len(pb)} continue={vc.get('continue', 0):.3f}")


def _append_json(path: str, rec: dict) -> None:
    items = []
    if os.path.exists(path):
        with open(path) as f:
            items = json.load(f)
    items.append(rec)
    with open(path, "w") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=20150105)
    ap.add_argument("--end", type=int, default=20260826)
    ap.add_argument("--codes-limit", type=int, default=0)
    ap.add_argument("--sensitivity", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--skip-bootstrap", action="store_true")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()
    md = load_market(codes_limit=args.codes_limit)
    eps_df, pb_df = scan(md, TrendParams(), LabelerParams(), args.start, args.end)
    ex_df = replay_exits(md, eps_df, LabelerParams())

    eps_df.to_csv(f"{OUT_DIR}/episodes.csv", index=False)
    pb_df.to_csv(f"{OUT_DIR}/pullbacks.csv", index=False)
    ex_df.to_csv(f"{OUT_DIR}/exits.csv", index=False)
    np.savetxt(f"{OUT_DIR}/calendar.txt", md.cal, fmt="%d")
    with open(f"{OUT_DIR}/universe.txt", "w") as f:
        f.write("\n".join(sorted(md.stocks)))
    log(f"明细落盘完成 耗时 {(time.time() - t0) / 60:.1f} 分钟")

    if args.sensitivity and not args.codes_limit:
        for p in ("sensitivity.json", "sensitivity_labeler.json"):
            fp = f"{OUT_DIR}/{p}"
            if os.path.exists(fp):
                os.remove(fp)          # 重跑覆盖
        sensitivity(md, args.start, args.end, OUT_DIR)

    if not args.skip_bootstrap and len(pb_df):
        bdf = run_bootstrap(pb_df, ex_df, md.cal)
        bdf.to_csv(f"{OUT_DIR}/robustness.csv", index=False)
        bdf.attrs["summary"].to_csv(f"{OUT_DIR}/robustness_summary.csv", index=False)
        log("bootstrap 完成")

    import analyze
    analyze.main(OUT_DIR)
    log(f"run_all 总耗时 {(time.time() - t0) / 60:.1f} 分钟")


if __name__ == "__main__":
    main()
