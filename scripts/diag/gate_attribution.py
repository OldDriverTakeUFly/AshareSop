"""2024-2025 牛市踏空诊断: 量化三个仓位压制机制各"压制"了多少天.

机制 (与 strategy.py 生产配置一致):
  - HMM regime = bear       → 不开新仓
  - 量能比 vol_ratio_250>1.2 → effective_max 减半 (5→2)
  - iVIX>25 且非 bear       → 暂停买入
对照实际暴露 (从 attribution_u0.json 的交易回合重建).
"""
import os, sys, json
from collections import defaultdict

import numpy as np

PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="ERROR")

INITIAL_CAPITAL = 1_000_000


def main():
    # ── 实际暴露 (来自 U0 回合) ──
    with open("logs/attribution_u0.json") as f:
        data = json.load(f)
    trips = data["round_trips"]

    conn = __import__("sqlite3").connect("storage/database/market_data.db")
    idx = conn.execute(
        "SELECT trade_date, close, vol FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date"
    ).fetchall()
    ivix_rows = conn.execute("SELECT trade_date, close FROM ivix_history ORDER BY trade_date").fetchall()
    conn.close()
    ivix = {r[0]: r[1] for r in ivix_rows}

    dates = [r[0] for r in idx]
    vols = np.array([r[2] for r in idx], dtype=float)

    # vol_ratio_250 = 20日均量 / 250日均量 (与 executor._compute_vol_ratio_250 同口径)
    vr = {}
    for i, d in enumerate(dates):
        if i >= 250:
            vr[d] = vols[i - 19:i + 1].mean() / vols[i - 249:i + 1].mean()

    from davis_analyzer.market_regime import get_market_regime
    regime = {}
    for d in dates:
        if d >= "20230101":
            try:
                regime[d] = get_market_regime(d)
            except Exception:
                regime[d] = "?"

    dset = {d: i for i, d in enumerate(dates)}
    expo = np.zeros(len(dates))
    for t in trips:
        i0 = dset.get(t["entry_date"])
        i1 = dset.get(t["exit_date"], len(dates) - 1) if t["exit_date"] else len(dates) - 1
        if i0 is not None:
            expo[i0:i1 + 1] += 1

    print(f"\n{'=' * 92}")
    print(f"  2024-2025 仓位压制机制归因 (对照实际暴露)")
    print(f"{'=' * 92}")
    print(f"  {'月份':<7} {'实际暴露':>7} {'bear%':>6} {'vr>1.2%':>8} {'ivix>25%':>9} {'三闸全开%':>9} {'指数月收益':>9}")
    mon = defaultdict(list)
    for i, d in enumerate(dates):
        if d >= "20240101":
            mon[d[:6]].append(i)
    idx_close = {r[0]: r[1] for r in idx}
    prev_close = None
    for mk in sorted(mon):
        idxs = mon[mk]
        ex = expo[idxs].mean()
        bears = np.mean([regime.get(dates[i]) == "bear" for i in idxs]) * 100
        vrg = np.mean([vr.get(dates[i], 0) > 1.2 for i in idxs]) * 100
        ivg = np.mean([(ivix.get(dates[i]) or 0) > 25 for i in idxs]) * 100
        open_gates = np.mean([
            (regime.get(dates[i]) != "bear") and (vr.get(dates[i], 0) <= 1.2)
            and ((ivix.get(dates[i]) or 0) <= 25) for i in idxs]) * 100
        first, last = dates[idxs[0]], dates[idxs[-1]]
        mret = (idx_close[last] / prev_close - 1) * 100 if prev_close else float("nan")
        prev_close = idx_close[last]
        print(f"  {mk:<7} {ex:>7.2f} {bears:>5.0f}% {vrg:>7.0f}% {ivg:>8.0f}% {open_gates:>8.0f}% {mret:>+8.1f}%")

    # 年度汇总
    print(f"\n  年度汇总:")
    for y in ["2021", "2022", "2023", "2024", "2025", "2026"]:
        idxs = [i for i, d in enumerate(dates) if d.startswith(y)]
        if not idxs: continue
        ex = expo[idxs].mean()
        bears = np.mean([regime.get(dates[i]) == "bear" for i in idxs]) * 100
        vrg = np.mean([vr.get(dates[i], 0) > 1.2 for i in idxs]) * 100
        ivg = np.mean([(ivix.get(dates[i]) or 0) > 25 for i in idxs]) * 100
        cap2 = np.mean([(vr.get(dates[i], 0) > 1.2) for i in idxs]) * 100
        print(f"    {y}: 暴露{ex:.2f}格 | bear {bears:>3.0f}% | 量能闸 {vrg:>3.0f}% | ivix闸 {ivg:>3.0f}%")

    # 关键问题: 三闸全开的日子, 暴露也没上去吗?
    print(f"\n  按闸门状态分组的实际暴露 (2024-2025):")
    groups = defaultdict(list)
    for i, d in enumerate(dates):
        if d >= "20240101" and d <= "20251231":
            g = (regime.get(d) == "bear", vr.get(d, 0) > 1.2, (ivix.get(d) or 0) > 25)
            groups[g].append(expo[i])
    for g, exs in sorted(groups.items(), key=lambda x: -len(x[1])):
        bear, vol, ivx = g
        label = f"{'bear' if bear else '非bear'} | 量能{'压' if vol else '开'} | ivix{'压' if ivx else '开'}"
        print(f"    {label:<28} {len(exs):>4} 天 平均暴露 {np.mean(exs):.2f}格")

    # 信号稀缺 vs 闸门压制: 三闸全开日暴露也只有 1.5 格的话, 是选股信号问题
    open_days = [expo[i] for i, d in enumerate(dates)
                 if "20240101" <= d <= "20251231"
                 and regime.get(d) != "bear" and vr.get(d, 0) <= 1.2 and (ivix.get(d) or 0) <= 25]
    if open_days:
        print(f"\n  三闸全开日 ({len(open_days)}天): 平均暴露 {np.mean(open_days):.2f}格, "
              f"空仓率 {np.mean([e == 0 for e in open_days]) * 100:.0f}%, ≥4格率 {np.mean([e >= 4 for e in open_days]) * 100:.0f}%")


if __name__ == "__main__":
    main()
