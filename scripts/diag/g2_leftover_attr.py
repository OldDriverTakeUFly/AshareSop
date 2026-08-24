"""G2 遗留归因三件套（实验 0005 诊断段）.

0001 固化 G2(bull_relaxed_buy_momentum=60) 后的三个遗留问题, 全部基于
stockhot.db 已有账户数据(fp_U0_main_pool vs gx_G2_bull60)零引擎成本诊断:

  Q1 2026 -8.2pp 机制拆解 —— G2/U0 交易集合 diff:
     g2_only(放宽带新增入场) vs u0_only(被挤占的原入场) 的 PnL 对比,
     判定"低动量挤占槽位" vs "震荡市参与代价"两个假设
  Q2 景气类卖出降权审视 —— 卖出原因回合归因(两账户) + 静态反事实
     (多持有 5/10/20 交易日, 以硬止损/高位放量为对照组)
  Q3 2024 只修复 1.1pp —— 2024 段交易 diff + 月度净值 + 放宽月度点火分布

输出: logs/g2_leftover_attr.json + 控制台摘要
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict

import numpy as np

STOCKHOT_DB = "/home/leo/Projects/CodeAgentDashboard/storage/database/stockhot.db"
MARKET_DB = "/home/leo/Projects/CodeAgentDashboard/storage/database/market_data.db"
OUT = "/home/leo/Projects/CodeAgentDashboard/logs/g2_leftover_attr.json"

U0, G2 = "fp_U0_main_pool", "gx_G2_bull60"


# ── 数据加载 ─────────────────────────────────────────────────────────────


def load_trades(account: str) -> list[dict]:
    conn = sqlite3.connect(STOCKHOT_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT t.trade_date, t.ts_code, t.name, t.action, t.shares, t.amount,
               t.cost, t.signal_reason
        FROM paper_trades t JOIN paper_accounts a ON t.account_id=a.id
        WHERE a.name=? ORDER BY t.id""", (account,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_monthly_nav(account: str) -> dict[str, float]:
    """月末权益 → 月度收益率%."""
    conn = sqlite3.connect(STOCKHOT_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT n.trade_date, n.total_equity FROM paper_nav_history n
        JOIN paper_accounts a ON n.account_id=a.id
        WHERE a.name=? ORDER BY n.trade_date""", (account,)).fetchall()
    conn.close()
    month_eq: dict[str, float] = {}
    for r in rows:
        month_eq[r["trade_date"][:6]] = float(r["total_equity"])
    months = sorted(month_eq)
    out: dict[str, float] = {}
    prev = None
    for m in months:
        if prev is not None:
            out[m] = round((month_eq[m] / month_eq[prev] - 1) * 100, 2)
        prev = m
    return out


# ── FIFO 交易回合（口径同 attribution_u0.py）────────────────────────────


def build_round_trips(trades: list[dict]) -> list[dict]:
    by_code: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_code[t["ts_code"]].append(t)
    trips = []
    for code, tl in by_code.items():
        outstanding, cur = 0, None
        for t in tl:
            if t["action"] == "BUY":
                if outstanding == 0:
                    cur = {"ts_code": code, "name": t["name"],
                           "entry_date": t["trade_date"], "buy_total": 0.0,
                           "sells": []}
                outstanding += t["shares"]
                cur["buy_total"] += t["amount"] + (t["cost"] or 0.0)
            else:
                if cur is None:
                    continue
                outstanding -= t["shares"]
                cur["sells"].append(t)
                if outstanding <= 0:
                    sell_net = sum(s["amount"] - (s["cost"] or 0.0) for s in cur["sells"])
                    final = cur["sells"][-1]
                    cur.update(
                        exit_date=t["trade_date"], reason=final["signal_reason"] or "",
                        pnl=sell_net - cur["buy_total"],
                        pnl_pct=(sell_net - cur["buy_total"]) / cur["buy_total"] * 100
                        if cur["buy_total"] > 0 else 0.0,
                        exit_price=final["amount"] / final["shares"] if final["shares"] else 0.0,
                        exit_amount=final["amount"],
                    )
                    trips.append(cur)
                    cur, outstanding = None, 0
    return trips


def reason_group(reason: str) -> str:
    r = reason or ""
    if "景气" in r:
        return "景气类(拐点+行业切换)"
    if "硬止损" in r:
        return "硬止损"
    if "高位放量" in r:
        return "高位放量"
    if r.startswith("T+减仓"):
        return "T+减仓(清仓)"
    if "止盈" in r:
        return "止盈"
    if "超跌反弹" in r:
        return "超跌反弹"
    if "动量" in r:
        return "动量走弱"
    return "其他:" + r[:10]


# ── Q2: 卖出原因归因 + 静态反事实 ───────────────────────────────────────


def sell_attribution(trips: list[dict]) -> dict:
    by: dict[str, dict] = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0,
                                               "by_year": defaultdict(lambda: [0, 0.0])})
    for t in trips:
        g = reason_group(t["reason"])
        d = by[g]
        d["n"] += 1
        d["pnl"] += t["pnl"]
        d["wins"] += t["pnl"] > 0
        y = t["exit_date"][:4]
        d["by_year"][y][0] += 1
        d["by_year"][y][1] += t["pnl"]
    return {g: {"n": v["n"], "pnl_wan": round(v["pnl"] / 1e4, 1),
                "win_rate": round(v["wins"] / v["n"] * 100, 1) if v["n"] else 0,
                "by_year": {y: [c, round(p / 1e4, 1)] for y, (c, p) in sorted(v["by_year"].items())}}
            for g, v in by.items()}


def counterfactual(trips: list[dict], groups: list[str], ks=(5, 10, 20)) -> dict:
    """对指定原因组的回合: 以最终清仓价为基, 多持有 k 交易日的额外收益(近似, 未计成本)."""
    conn = sqlite3.connect(MARKET_DB)
    out: dict[str, dict] = {}
    for g in groups:
        sel = [t for t in trips if reason_group(t["reason"]) == g and t["exit_date"]]
        stats = {k: [] for k in ks}
        amount_w = {k: 0.0 for k in ks}
        skipped = 0
        for t in sel:
            rows = conn.execute(
                "SELECT close FROM daily_price WHERE ts_code=? AND trade_date>? "
                "ORDER BY trade_date LIMIT 21", (t["ts_code"], t["exit_date"])).fetchall()
            if len(rows) < ks[-1] or not t["exit_price"]:
                skipped += 1
                continue
            for k in ks:
                extra = (rows[k - 1][0] / t["exit_price"] - 1) * 100
                stats[k].append(extra)
                amount_w[k] += extra / 100 * t["exit_amount"] / 1e4
        out[g] = {"n": len(sel) - skipped, "skipped": skipped,
                  "per_k": {f"hold{k}d": {
                      "mean_pct": round(float(np.mean(v)), 2) if v else None,
                      "median_pct": round(float(np.median(v)), 2) if v else None,
                      "extra_gt0_pct": round(float(np.mean(np.array(v) > 0)) * 100, 1) if v else None,
                      "amount_wan": round(amount_w[k], 1)} for k, v in stats.items()}}
    conn.close()
    return out


# ── Q1/Q3: 交易集合 diff ────────────────────────────────────────────────


def diff_year(u0_trips: list[dict], g2_trips: list[dict], year: str) -> dict:
    u0y = [t for t in u0_trips if t["entry_date"].startswith(year)]
    g2y = [t for t in g2_trips if t["entry_date"].startswith(year)]
    key = lambda t: (t["ts_code"], t["entry_date"])
    u0_map, g2_map = {key(t): t for t in u0y}, {key(t): t for t in g2y}
    common = set(u0_map) & set(g2_map)
    g2_only = [g2_map[k] for k in set(g2_map) - common]
    u0_only = [u0_map[k] for k in set(u0_map) - common]
    # 近邻匹配上下文（同票 ±5 自然日入场但不同日）
    from datetime import datetime as _dt
    near = 0
    g2_dates: dict[str, list] = defaultdict(list)
    for t in g2y:
        g2_dates[t["ts_code"]].append(_dt.strptime(t["entry_date"], "%Y%m%d").date())
    for t in u0_only:
        d0 = _dt.strptime(t["entry_date"], "%Y%m%d").date()
        if any(abs((d - d0).days) <= 5 for d in g2_dates.get(t["ts_code"], [])):
            near += 1
    def brief(ts, n=8):
        s = sorted(ts, key=lambda t: t["pnl"])
        return [{"code": t["ts_code"], "name": t["name"], "in": t["entry_date"],
                 "out": t.get("exit_date"), "pnl_wan": round(t["pnl"] / 1e4, 1),
                 "reason": reason_group(t["reason"])} for t in s[:n] + s[-n:]]
    return {
        "u0": {"n": len(u0y), "pnl_wan": round(sum(t["pnl"] for t in u0y) / 1e4, 1)},
        "g2": {"n": len(g2y), "pnl_wan": round(sum(t["pnl"] for t in g2y) / 1e4, 1)},
        "common": {"n": len(common),
                   "pnl_u0_wan": round(sum(u0_map[k]["pnl"] for k in common) / 1e4, 1),
                   "pnl_g2_wan": round(sum(g2_map[k]["pnl"] for k in common) / 1e4, 1)},
        "g2_only": {"n": len(g2_only),
                    "pnl_wan": round(sum(t["pnl"] for t in g2_only) / 1e4, 1),
                    "extremes": brief(g2_only)},
        "u0_only": {"n": len(u0_only),
                    "pnl_wan": round(sum(t["pnl"] for t in u0_only) / 1e4, 1),
                    "extremes": brief(u0_only)},
        "near_miss_entries": near,
    }


def main() -> None:
    u0_trips = build_round_trips(load_trades(U0))
    g2_trips = build_round_trips(load_trades(G2))
    print(f"回合数: U0={len(u0_trips)} G2={len(g2_trips)}")

    result = {
        "meta": {"u0": U0, "g2": G2, "note": "实验0005诊断段, 口径同 attribution_u0"},
        "q1_2026": {"diff": diff_year(u0_trips, g2_trips, "2026"),
                    "monthly_nav": {"u0": load_monthly_nav(U0), "g2": load_monthly_nav(G2)}},
        "q2_sell_attr": {"u0": sell_attribution(u0_trips), "g2": sell_attribution(g2_trips),
                         "counterfactual_u0": counterfactual(
                             u0_trips, ["景气类(拐点+行业切换)", "硬止损", "高位放量"]),
                         "counterfactual_g2": counterfactual(
                             g2_trips, ["景气类(拐点+行业切换)"])},
        "q3_2024": {"diff": diff_year(u0_trips, g2_trips, "2024")},
    }
    # 全期 g2_only 规模（放宽带总入场量上下文）
    allk = set((t["ts_code"], t["entry_date"]) for t in u0_trips)
    g2_all = [t for t in g2_trips if (t["ts_code"], t["entry_date"]) not in allk]
    result["context_all_years_g2_only"] = {
        "n": len(g2_all), "pnl_wan": round(sum(t["pnl"] for t in g2_all) / 1e4, 1)}

    with open(OUT, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)

    # ── 控制台摘要 ──
    for q, year in (("Q1", "2026"), ("Q3", "2024")):
        d = result["q1_2026" if q == "Q1" else "q3_2024"]["diff"]
        print(f"\n{'='*72}\n{q} {year} 交易 diff: U0 {d['u0']['pnl_wan']}万/{d['u0']['n']}笔"
              f" vs G2 {d['g2']['pnl_wan']}万/{d['g2']['n']}笔 (差 {d['g2']['pnl_wan']-d['u0']['pnl_wan']:+.1f}万)")
        print(f"  共同 {d['common']['n']}笔: U0 {d['common']['pnl_u0_wan']}万 vs G2 {d['common']['pnl_g2_wan']}万")
        print(f"  G2独有 {d['g2_only']['n']}笔 = {d['g2_only']['pnl_wan']}万"
              f" | U0独有 {d['u0_only']['n']}笔 = {d['u0_only']['pnl_wan']}万 (机会成本)"
              f" | 近邻入场 {d['near_miss_entries']}")
    print(f"\n{'='*72}\nQ2 卖出原因归因 (G2, 万):")
    for g, v in sorted(result["q2_sell_attr"]["g2"].items(), key=lambda x: x[1]["pnl_wan"]):
        print(f"  {g:<24} n={v['n']:>3} pnl={v['pnl_wan']:>+7.1f}万 胜率{v['win_rate']}%")
    print(f"\nQ2 静态反事实 U0 (多持有额外收益, 金额加权万 / 中位% / >0占比%):")
    for g, v in result["q2_sell_attr"]["counterfactual_u0"].items():
        row = " | ".join(f"k{k}: {d['amount_wan']:>+6.1f}万 med{d['median_pct']:>+5.1f}% "
                         f">0 {d['extra_gt0_pct']}%" for k, d in v["per_k"].items())
        print(f"  {g:<24} n={v['n']:<3} {row}")
    print(f"\n全期 G2 独有入场: {result['context_all_years_g2_only']}")
    print(f"\n结果: {OUT}")


if __name__ == "__main__":
    main()
