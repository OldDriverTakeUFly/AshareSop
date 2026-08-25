"""持仓洗盘概率检查 + 破位提醒(盘后日报,日线口径)。

依赖 detect_washout.py 的数据管线与状态机。流程:
1. 复用全市场数据加载,为 low/mid/high 三组锚点构建 episodes;
2. 用「结局前逐日状态」构建条件续涨率查表:key=(组, 回调第k天段, 距平台段, 回撤段),
   只统计结局确定(continue/breakdown)样本的结局前交易日 → 无前视偏差;
3. 读真实底仓(paper_positions 全账户合计,与影子验证同口径);
4. 每只持仓找最近 40 交易日内的涨停锚,跑到数据末端:
   - 回调中(open)→ 查表 P(续涨) + 量能/资金辅助信号 + 破位提醒价位
   - 已续涨/已破位 → 报结局;无锚 → 纯 MA 支撑提醒;
5. 输出 markdown 报告(console + studies/output/washout/holdings_check_<date>.md)。

用法:.venv/bin/python scripts/washout_research/holdings_check.py
"""
from __future__ import annotations

import os
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import detect_washout as dw  # noqa: E402

OUT_DIR = "studies/output/washout"
ANCHOR_LOOKBACK = 40   # 持仓锚点回看交易日数


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ── 条件概率查表 ──

def kbucket(k: int) -> str:
    if k <= 3:
        return "k1-3"
    if k <= 7:
        return "k4-7"
    return "k8+"


def supbucket(sup: float) -> str:
    if sup >= 0.05:
        return "S5+"
    if sup >= 0.02:
        return "S2-5"
    return "S0-2"


def ddbucket(dd: float) -> str:
    return "Dshal" if dd > -0.10 else "Ddeep"


def build_lookup(episodes: list[dict], stocks: dict) -> dict:
    """逐 episode 走结局前每一天,累计 (key → [n_continue, n])。"""
    table: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    for e in episodes:
        if e["outcome"] not in ("continue", "breakdown"):
            continue
        s = stocks[e["ts_code"]]
        c = s["c"]
        peak, support = e["_peak"], e["_support"]
        for i in range(e["peak_pos"] + 1, e["end_pos"]):
            if not np.isfinite(c[i]):
                continue
            sup = c[i] / support - 1.0
            if sup < 0:
                continue  # 收盘破位即终局,walk 内不应出现(防御)
            key = (e["group"], kbucket(i - e["peak_pos"]),
                   supbucket(sup), ddbucket(c[i] / peak - 1.0))
            table[key][1] += 1
            if e["outcome"] == "continue":
                table[key][0] += 1
    return table


def lookup_p(table: dict, key: tuple) -> tuple[float, int]:
    """层级回退:n<30 时退到 (组,k,sup),再退到 (组,k)。"""
    for k in (key, key[:3], key[:2]):
        if k in table and table[k][1] >= 30:
            n_con, n = table[k]
            return n_con / n * 100.0, n
    if key[:2] in table:
        n_con, n = table[key[:2]]
        return n_con / n * 100.0, n
    return np.nan, 0


# ── 主流程 ──

def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    sb, idx, dp, lp, inf, mf = dw.load_data()
    (cal, cal_pos, stocks, limit_pos, lp_turnover_map, valid_pos,
     aux_inf, aux_mf, idx_df) = dw.build_arrays(sb, idx, dp, lp, inf, mf)
    n_cal = len(cal)
    today_pos = n_cal - 1
    log(f"数据末端 {cal[-1]},today_pos={today_pos}")

    an_lo = int(np.searchsorted(cal, dw.ANCHOR_START))
    an_hi = today_pos

    # ── 三组锚点(low ≤30 / mid 30-70 / high ≥70 且前60日涨≥30%) ──
    anchors: list[dict] = []
    for code, (poss, boards) in limit_pos.items():
        s = stocks.get(code)
        if s is None:
            continue
        vp = valid_pos[code]
        for k in range(len(poss)):
            t0 = int(poss[k])
            if t0 < an_lo or t0 > an_hi or t0 - 1 < an_lo:
                continue
            if today_pos - t0 > 400:  # 查表只需历史,不限近端
                pass
            hist = vp[vp < t0]
            if hist.size < dw.HIST_MIN:
                continue
            pre = s["c"][t0 - 1]
            if not np.isfinite(pre):
                continue
            win = s["c"][hist[-250:]]
            win = win[np.isfinite(win)]
            if win.size < 120:
                continue
            pos_pct = dw.pct_rank(float(pre), win)
            if pos_pct <= 30:
                grp = "low"
            elif pos_pct >= 70:
                grp = "high"
            else:
                grp = "mid"
            anchors.append({"code": code, "t0": t0, "group": grp})
    # 同股去重
    by_stock: dict[str, list] = defaultdict(list)
    for a in anchors:
        by_stock[a["code"]].append(a)
    deduped = []
    for code, lst in by_stock.items():
        lst.sort(key=lambda x: x["t0"])
        last = -10**9
        for a in lst:
            if a["t0"] - last >= dw.DEDUP_DAYS:
                deduped.append(a)
                last = a["t0"]
    log(f"锚点(三组合计,去重后) {len(deduped)}")

    episodes = []
    done = 0
    for a in deduped:
        s = stocks[a["code"]]
        r = dw.scan_episode(s, a["t0"], aux_inf, aux_mf, a["code"])
        done += 1
        if done % 5000 == 0:
            log(f"  episodes 扫描 {done}/{len(deduped)}")
        if r is None:
            continue
        r["ts_code"] = a["code"]
        r["group"] = a["group"]
        episodes.append(r)
    log(f"episodes {len(episodes)},构建查表 ...")
    table = build_lookup(episodes, stocks)

    # 查表概览
    for grp in ("low", "mid", "high"):
        sub = [e for e in episodes if e["group"] == grp and e["outcome"] in ("continue", "breakdown")]
        if sub:
            p = sum(e["outcome"] == "continue" for e in sub) / len(sub) * 100
            log(f"  组基率 {grp}: n={len(sub)}, P续涨={p:.1f}%")

    # ── 真实底仓 ──
    import sqlite3
    from stockhot.core.config import DB_PATH as STOCKHOT_DB
    con = sqlite3.connect(STOCKHOT_DB)
    hold_rows = con.execute(
        "SELECT ts_code, SUM(shares) sh, SUM(shares*avg_cost)/SUM(shares) avg_cost "
        "FROM paper_positions WHERE shares>0 GROUP BY ts_code"
    ).fetchall()
    names_db = dict(con.execute("SELECT ts_code, name FROM paper_positions").fetchall())
    con.close()
    log(f"真实底仓 {len(hold_rows)} 只")

    # ── 逐持仓评估 ──
    import sqlite3
    raw_con = sqlite3.connect("storage/database/market_data.db")
    date_of = {i: int(d) for d, i in cal_pos.items()}

    def raw_close_at(code: str, pos: int) -> float:
        d = str(date_of.get(pos, ""))
        r2 = raw_con.execute(
            "SELECT close FROM daily_price WHERE ts_code=? AND trade_date=?",
            (code, d)).fetchone()
        return float(r2[0]) if r2 else np.nan

    report_rows = []
    for code, shares, avg_cost in hold_rows:
        s = stocks.get(code)
        if s is None:
            report_rows.append({"code": code, "name": names_db.get(code, "?"),
                                "状态": "无数据(非沪深/ST名)", "警报": "-"})
            continue
        c, v = s["c"], s["v"]
        last = c[today_pos]
        # MA 与 250 日位置
        def ma(win: int) -> float:
            seg = c[today_pos - win + 1:today_pos + 1]
            seg = seg[np.isfinite(seg)]
            return float(seg.mean()) if seg.size else np.nan
        ma10, ma20 = ma(10), ma(20)
        hist = valid_pos.get(code, np.array([]))
        hist = hist[hist <= today_pos][-250:]
        win = c[hist]
        win = win[np.isfinite(win)]
        pos_pct = dw.pct_rank(float(last), win) if win.size > 60 else np.nan

        # 最近锚
        lp_pos = limit_pos.get(code, (np.array([]), np.array([])))[0]
        recent = lp_pos[(lp_pos <= today_pos) & (lp_pos >= today_pos - ANCHOR_LOOKBACK)]
        row = {"code": code, "name": names_db.get(code, "?"), "pos_pct": pos_pct,
               "avg_cost": avg_cost, "状态": "无近期涨停锚", "警报": ""}

        if len(recent):
            t0 = int(recent[-1])
            r = dw.scan_episode(s, t0, aux_inf, aux_mf, code)
            if r is None:
                row["状态"] = f"启动运行中(锚{date_of[t0]},未见-5%回调)"
            elif r["outcome"] == "continue":
                row["状态"] = f"已续涨确认({date_of[r['end_pos']]})"
            elif r["outcome"] == "breakdown":
                row["状态"] = f"⚠️已破位({date_of[r['end_pos']]})"
                row["警报"] = "破位"
            elif r["outcome"] == "timeout":
                row["状态"] = f"超时未决(锚{date_of[t0]})"
            else:  # open:回调进行中 → 查表
                k_now = today_pos - r["peak_pos"]
                sup_now = float(last / r["_support"] - 1.0)
                dd_now = float(last / r["_peak"] - 1.0)
                grp = "low" if pos_pct <= 30 else ("high" if pos_pct >= 70 else "mid")
                key = (grp, kbucket(k_now), supbucket(sup_now), ddbucket(dd_now))
                p, n = lookup_p(table, key)
                # 量能与资金辅助(近3日)
                w = v[r["peak_pos"] + 1:today_pos + 1]
                w = w[np.isfinite(w)]
                lv = v[t0:r["peak_pos"] + 1]
                lv = lv[np.isfinite(lv)]
                volr = float(np.nanmean(w) / np.nanmean(lv)) if w.size and lv.size else np.nan
                mf3 = dw.mf_net_intensity(aux_mf, code, today_pos - 2, today_pos, s["a"])
                row.update({
                    "状态": f"回调第{k_now}天(锚{date_of[t0]},峰{date_of[r['peak_pos']]})",
                    "P续涨": p, "n": n, "dd": dd_now, "sup": sup_now,
                    "volr": volr, "mf3": mf3, "group": grp, "k_now": k_now,
                    "平台价": raw_close_at(code, t0 - 1),
                })
        row.setdefault("P续涨", np.nan)
        row["MA10"] = ma10
        row["MA20"] = ma20
        # 破位警报判定(与锚无关)
        alerts = []
        if np.isfinite(ma10) and last < ma10 and np.isfinite(ma20) and last < ma20:
            alerts.append("价<MA10&MA20")
        if np.isfinite(row.get("sup", np.nan)) and row["sup"] < 0.02:
            alerts.append("贴平台")
        if (np.isfinite(pos_pct) and pos_pct >= 70
                and row.get("k_now", 0) >= 4):
            alerts.append("高位慢回调")
        row["警报"] = row.get("警报", "") + ("/".join(alerts) if alerts else "")
        report_rows.append(row)

    # ── 输出 ──
    df = pd.DataFrame(report_rows)
    sev = {"⚠️已破位": 0, "回调第": 1, "超时未决": 2, "启动运行中": 3, "已续涨确认": 4, "无近期涨停锚": 5}
    df["_sev"] = df["状态"].map(lambda s_: next((v for k, v in sev.items() if k in str(s_)), 9))
    df = df.sort_values(["_sev", "P续涨"], na_position="last")

    lines = [f"# 持仓洗盘概率与破位提醒 — 数据截至 {cal[-1]} 收盘", "",
             f"> 底仓 {len(hold_rows)} 只(paper_positions 全账户合计) | "
             f"查表样本:三组 episodes {len(episodes)} | P续涨=条件续涨率(历史同状态样本,n≥30 层级回退)", ""]
    in_wash = df[df["状态"].str.contains("回调第", na=False)]
    lines.append("## 一、回调进行中(洗盘概率查表)")
    if len(in_wash):
        lines.append("| 代码 | 名称 | 位置分位 | 回调状态 | P续涨 | n | 回撤 | 距平台 | 量比 | 近3日主力 | 警报 |")
        lines.append("|------|------|---------|---------|-------|---|------|--------|------|----------|------|")
        for _, r in in_wash.iterrows():
            p = f"{r['P续涨']:.0f}%" if np.isfinite(r.get("P续涨", np.nan)) else "—"
            dd = f"{r.get('dd', np.nan)*100:.1f}%" if np.isfinite(r.get("dd", np.nan)) else "—"
            sup = f"{r.get('sup', np.nan)*100:+.1f}%" if np.isfinite(r.get("sup", np.nan)) else "—"
            volr = f"{r.get('volr', np.nan):.2f}" if np.isfinite(r.get("volr", np.nan)) else "—"
            mf3 = f"{r.get('mf3', np.nan)*100:+.1f}%" if np.isfinite(r.get("mf3", np.nan)) else "—"
            pos = f"{r['pos_pct']:.0f}" if np.isfinite(r.get("pos_pct", np.nan)) else "—"
            lines.append(f"| {r['code']} | {r['name']} | {pos} | {r['状态']} | **{p}** | {r.get('n','-')} | "
                         f"{dd} | {sup} | {volr} | {mf3} | {r.get('警报','') or '—'} |")
    else:
        lines.append("(无回调进行中的持仓)")

    lines += ["", "## 二、已破位 / 已续涨 / 超时"]
    resolved = df[df["状态"].str.contains("已破位|已续涨|超时", na=False)]
    if len(resolved):
        lines.append("| 代码 | 名称 | 状态 | 位置分位 |")
        lines.append("|------|------|------|---------|")
        for _, r in resolved.iterrows():
            pos = f"{r['pos_pct']:.0f}" if np.isfinite(r.get("pos_pct", np.nan)) else "—"
            lines.append(f"| {r['code']} | {r['name']} | {r['状态']} | {pos} |")
    else:
        lines.append("(无)")

    lines += ["", "## 三、全持仓支撑位速览(原始价位)"]
    lines.append("| 代码 | 名称 | 现价(adj) | 距MA10 | 距MA20 | 250日分位 | 警报 |")
    lines.append("|------|------|---------|--------|--------|----------|------|")
    for _, r in df.iterrows():
        last = stocks.get(r["code"], {}).get("c", [np.nan])[-1] if r["code"] in stocks else np.nan
        d10 = (last / r["MA10"] - 1) * 100 if np.isfinite(r.get("MA10", np.nan)) and r["MA10"] else np.nan
        d20 = (last / r["MA20"] - 1) * 100 if np.isfinite(r.get("MA20", np.nan)) and r["MA20"] else np.nan
        pos = f"{r['pos_pct']:.0f}" if np.isfinite(r.get("pos_pct", np.nan)) else "—"
        lines.append(f"| {r['code']} | {r['name']} | {last:.2f} | "
                     f"{d10:+.1f}% | {d20:+.1f}% | {pos} | {r.get('警报','') or '—'} |")

    md = "\n".join(lines)
    out_path = f"{OUT_DIR}/holdings_check_{cal[-1]}.md"
    with open(out_path, "w") as f:
        f.write(md)
    print(md)
    log(f"报告已写 {out_path}")


if __name__ == "__main__":
    main()
