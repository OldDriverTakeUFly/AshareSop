#!/usr/bin/env python3
"""
财务深度分析模板 — 8指标结构化数据提取 (Option A: 数据提取, 无叙述)

本模板计算8指标结构化数据，叙述分析由agent根据数据撰写。
基于 T1 缓存的 tushare JSON，计算 8 个扩展指标 + 盈亏拐点信号。
所有叙述性 analysis 字段设为 TODO，由 agent 根据 JSON 数据撰写。
独立研究脚本：不修改任何 davis_analyzer 源码。

The 8 indicators computed:
  1. gross_margin — 毛利率（单季+累计趋势）
  2. rd_ratio — 研发费率（逆周期投入强度）
  3. expense_ratio — 销售+管理+财务费用率分解
  4. contract_liab — 合同负债（领先订单信号）
  5. cip — 在建工程（产能扩张节奏）
  6. inventory_turnover — 存货周转率（营运效率）
  7. burn_rate — 现金消耗率（money_cap / 季度亏损 → 存活季度数）
  8. capex_intensity — 资本开支强度（|inv_cashflow| / revenue）

输入: {OUTPUT_DIR}/t1-tushare-data.json   输出: {OUTPUT_DIR}/t4-financial-deep.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from davis_analyzer.config import PROJECT_ROOT

# ========== CONFIG: 填入你的标的 ==========
TARGET_CODE = "000000.SH"       # 目标股票ts_code
PEER_CODES = ["000001.SH"]      # 同业ts_code列表（≥3个）
OUTPUT_DIR = "output"           # JSON输出目录
# ==========================================

ALL_CODES = [TARGET_CODE] + PEER_CODES
T1_PATH = PROJECT_ROOT / OUTPUT_DIR / "t1-tushare-data.json"
OUT_PATH = PROJECT_ROOT / OUTPUT_DIR / "t4-financial-deep.json"
TODO = "TODO: agent fills narrative based on indicator data"
_T1_FETCHED = "unknown"

# ── helpers ──

def _f(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, 4)

def dedupe(records):
    seen, out = set(), []
    for r in records:
        if r.get("end_date") not in seen:
            seen.add(r["end_date"])
            out.append(r)
    return out

def sort_d(records):
    return sorted(records, key=lambda r: r.get("end_date") or "")

_QMAP = {"03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4"}

def qsuf(ed):
    if not ed or len(ed) < 8:
        return ed
    return f"{ed[:4]}{_QMAP.get(ed[4:6], ed[4:6])}"

def last_n(records, n=8):
    return sort_d(records)[-n:]

def latest_q4(series):
    for s in reversed(series):
        if str(s.get("quarter", "")).endswith("Q4"):
            return s
    return {}

def sq_from_cum(records, field):
    out, pv, pd = [], None, None
    for r in records:
        ed, val = r.get("end_date"), r.get(field)
        if val is None:
            out.append({"end_date": ed, "value": None})
        elif ed and ed[4:6] == "03":
            out.append({"end_date": ed, "value": val})
        elif pv is not None and pd and ed and ed[:4] == pd[:4]:
            out.append({"end_date": ed, "value": val - pv})
        else:
            out.append({"end_date": ed, "value": None})
        pv, pd = val, ed
    return out

def stag(ep):
    return f"来源: tushare {ep} (fetched_at={_T1_FETCHED})"

def new_result(ep):
    return {"_source": stag(ep), "target": [], "peers": {}, "trend": "", "analysis": TODO}

def assign(res, code, series):
    if code == TARGET_CODE:
        res["target"] = series
    else:
        res["peers"][code] = series

# ── 8 indicator builders (data extraction only) ──

def build_gross_margin(data):
    """1. 毛利率趋势 (q_gsprofit_margin + grossprofit_margin)."""
    res = new_result("fina_indicator")
    for code in ALL_CODES:
        last8 = last_n(dedupe(data["fina_indicator"][code]))
        s = [{"period": r["end_date"], "quarter": qsuf(r["end_date"]),
              "q_gross_margin": _f(r.get("q_gsprofit_margin")),
              "cum_gross_margin": _f(r.get("grossprofit_margin"))} for r in last8]
        assign(res, code, s)
    gm = [x["q_gross_margin"] for x in res["target"] if x["q_gross_margin"] is not None]
    if len(gm) >= 2:
        if min(gm) != gm[-1] and gm.index(min(gm)) >= len(gm) - 4 and gm[-1] > min(gm):
            res["trend"] = "触底回升"
        elif gm[-1] > gm[-2]:
            res["trend"] = "回升"
        elif gm[-1] < gm[-2]:
            res["trend"] = "下降"
        else:
            res["trend"] = "稳定"
    return res

def build_rd_ratio(data):
    """2. 研发费率 = rd_exp / revenue."""
    res = new_result("income")
    for code in ALL_CODES:
        last8 = last_n(dedupe(data["income"][code]))
        s = []
        for r in last8:
            rev, rd = r.get("revenue"), r.get("rd_exp")
            ratio = round(rd / rev * 100, 2) if rev and rd is not None and rev != 0 else None
            s.append({"period": r["end_date"], "quarter": qsuf(r["end_date"]),
                      "rd_exp": _f(rd), "revenue": _f(rev), "rd_ratio_pct": ratio})
        assign(res, code, s)
    rd = [x["rd_ratio_pct"] for x in res["target"] if x["rd_ratio_pct"] is not None]
    if len(rd) >= 2:
        res["trend"] = "上升" if rd[-1] > rd[0] else ("下降" if rd[-1] < rd[0] else "稳定")
    return res

def build_expense_ratio(data):
    """3. 费用率 = (sell + admin + fin) / revenue, 三费分解."""
    res = new_result("income")
    for code in ALL_CODES:
        last8 = last_n(dedupe(data["income"][code]))
        s = []
        for r in last8:
            rev = r.get("revenue")
            sell, admin, fin = r.get("sell_exp") or 0, r.get("admin_exp") or 0, r.get("fin_exp") or 0
            total = sell + admin + fin
            pct = lambda v: round(v / rev * 100, 2) if rev and rev != 0 else None
            s.append({"period": r["end_date"], "quarter": qsuf(r["end_date"]),
                      "sell_exp": _f(sell), "admin_exp": _f(admin), "fin_exp": _f(fin),
                      "total_3exp": _f(total), "revenue": _f(rev),
                      "expense_ratio_pct": pct(total), "sell_ratio_pct": pct(sell),
                      "admin_ratio_pct": pct(admin), "fin_ratio_pct": pct(fin)})
        assign(res, code, s)
    exp = [x["expense_ratio_pct"] for x in res["target"] if x["expense_ratio_pct"] is not None]
    if len(exp) >= 2:
        res["trend"] = "下降" if exp[-1] < exp[0] else ("上升" if exp[-1] > exp[0] else "稳定")
    return res

def build_contract_liab(data):
    """4. 合同负债趋势 — QoQ + YoY."""
    res = new_result("balancesheet")
    for code in ALL_CODES:
        last8 = last_n(dedupe(data["balancesheet"][code]))
        s = []
        for i, r in enumerate(last8):
            cl = r.get("contract_liab")
            qoq = round((cl - last8[i-1]["contract_liab"]) / last8[i-1]["contract_liab"] * 100, 2) \
                if i > 0 and cl is not None and last8[i-1].get("contract_liab") and last8[i-1]["contract_liab"] != 0 else None
            yoy = round((cl - last8[i-4]["contract_liab"]) / last8[i-4]["contract_liab"] * 100, 2) \
                if i >= 4 and cl is not None and last8[i-4].get("contract_liab") and last8[i-4]["contract_liab"] != 0 else None
            s.append({"period": r["end_date"], "quarter": qsuf(r["end_date"]),
                      "contract_liab": _f(cl), "qoq_pct": qoq, "yoy_pct": yoy})
        assign(res, code, s)
    qoq = res["target"][-1].get("qoq_pct") if res["target"] else None
    res["trend"] = "环比转正" if qoq and qoq > 0 else ("环比下降" if qoq and qoq < 0 else "待观察")
    return res

def build_cip(data):
    """5. 在建工程 (cip) — 产能扩张节奏."""
    res = new_result("balancesheet")
    for code in ALL_CODES:
        last8 = last_n(dedupe(data["balancesheet"][code]))
        s = []
        for i, r in enumerate(last8):
            cip, fa = r.get("cip"), r.get("fix_assets")
            cip_fa = round(cip / fa * 100, 2) if cip and fa and fa != 0 else None
            qoq = round((cip - last8[i-1]["cip"]) / last8[i-1]["cip"] * 100, 2) \
                if i > 0 and cip is not None and last8[i-1].get("cip") and last8[i-1]["cip"] != 0 else None
            s.append({"period": r["end_date"], "quarter": qsuf(r["end_date"]),
                      "cip": _f(cip), "fix_assets": _f(fa), "cip_to_fix_assets_pct": cip_fa, "qoq_pct": qoq})
        assign(res, code, s)
    cip = [x["cip"] for x in res["target"] if x["cip"] is not None]
    if len(cip) >= 2:
        res["trend"] = "扩张中" if cip[-1] > cip[0] else ("收缩（转固）" if cip[-1] < cip[0] else "稳定")
    return res

def build_inventory_turnover(data):
    """6. 存货/营收 + 应收/营收 (营运效率代理)."""
    res = new_result("balancesheet + income")
    for code in ALL_CODES:
        inc_map = {r["end_date"]: r for r in dedupe(data["income"][code])}
        last8 = last_n(dedupe(data["balancesheet"][code]))
        s = []
        for r in last8:
            ed = r["end_date"]
            inv, ar, rev = r.get("inventories"), r.get("accounts_receiv"), inc_map.get(ed, {}).get("revenue")
            s.append({"period": ed, "quarter": qsuf(ed), "inventories": _f(inv),
                      "accounts_receiv": _f(ar), "ytd_revenue": _f(rev),
                      "inv_to_rev_pct": round(inv / rev * 100, 2) if inv and rev and rev != 0 else None,
                      "ar_to_rev_pct": round(ar / rev * 100, 2) if ar and rev and rev != 0 else None})
        assign(res, code, s)
    ann = [x for x in res["target"] if x["quarter"].endswith("Q4")]
    ir = [x["inv_to_rev_pct"] for x in ann if x["inv_to_rev_pct"] is not None]
    if len(ir) >= 2:
        res["trend"] = "存货堆积" if ir[-1] > ir[-2] else ("存货去化" if ir[-1] < ir[-2] else "稳定")
    return res

def build_burn_rate(data):
    """7. 货币资金 + 经营CF → burn rate, runway = money_cap / |季度净亏|."""
    res = new_result("balancesheet + income + cashflow")
    for code in ALL_CODES:
        inc_map = {r["end_date"]: r for r in dedupe(data["income"][code])}
        cf_map = {r["end_date"]: r for r in dedupe(data["cashflow"][code])}
        last8 = last_n(dedupe(data["balancesheet"][code]))
        s = [{"period": r["end_date"], "quarter": qsuf(r["end_date"]),
              "money_cap": _f(r.get("money_cap")),
              "ytd_n_income": _f(inc_map.get(r["end_date"], {}).get("n_income")),
              "ytd_ocf": _f(cf_map.get(r["end_date"], {}).get("n_cashflow_act"))} for r in last8]
        assign(res, code, s)
    # runway (target only)
    ty_inc = sort_d(dedupe(data["income"][TARGET_CODE]))
    sq_ni = sq_from_cum(ty_inc, "n_income")
    mc = res["target"][-1].get("money_cap") if res["target"] else None
    sq_ni_val, sq_ni_per = None, None
    for item in reversed(sq_ni):
        if item["value"] is not None:
            sq_ni_val, sq_ni_per = item["value"], item["end_date"]
            break
    annual_ni = next((r.get("n_income") for r in ty_inc if str(r.get("end_date", ""))[4:6] == "12"), None)
    runway = round(mc / abs(sq_ni_val), 1) if mc and sq_ni_val and sq_ni_val < 0 else None
    res["money_cap_latest"] = mc
    res["latest_quarter_net_income"] = sq_ni_val
    res["latest_quarter_period"] = sq_ni_per
    res["latest_annual_net_income"] = annual_ni
    res["quarters_runway"] = runway
    mc_s = [x["money_cap"] for x in res["target"] if x["money_cap"] is not None]
    if len(mc_s) >= 2:
        res["trend"] = "大幅增厚" if mc_s[-1] > mc_s[-2] * 1.3 else ("增厚" if mc_s[-1] > mc_s[-2] else ("消耗中" if mc_s[-1] < mc_s[-2] else "稳定"))
    return res

def build_capex(data):
    """8. 资本开支强度 = |n_cashflow_inv_act| / revenue."""
    res = new_result("cashflow (n_cashflow_inv_act proxy)")
    for code in ALL_CODES:
        inc_map = {r["end_date"]: r for r in dedupe(data["income"][code])}
        last8 = last_n(dedupe(data["cashflow"][code]))
        s = []
        for r in last8:
            ed = r["end_date"]
            inv_cf, ocf = r.get("n_cashflow_inv_act"), r.get("n_cashflow_act")
            fcf = r.get("free_cashflow")
            if fcf is None and ocf is not None and inv_cf is not None:
                fcf = ocf + inv_cf
            rev = inc_map.get(ed, {}).get("revenue")
            s.append({"period": ed, "quarter": qsuf(ed), "inv_cashflow": _f(inv_cf), "ocf": _f(ocf),
                      "free_cashflow": _f(fcf), "ytd_revenue": _f(rev),
                      "capex_intensity_pct": round(abs(inv_cf) / rev * 100, 2) if inv_cf is not None and rev and rev != 0 else None})
        assign(res, code, s)
    ann = [x for x in res["target"] if x["quarter"].endswith("Q4")]
    ci = [x["capex_intensity_pct"] for x in ann if x["capex_intensity_pct"] is not None]
    if len(ci) >= 2:
        res["trend"] = "扩张加速" if ci[-1] > ci[-2] else ("扩张放缓" if ci[-1] < ci[-2] else "稳定")
    return res

# ── inflection signals (generic thresholds) ──

def build_inflection_signals(gm, rd, cl, burn, capex):
    """盈亏拐点信号 — 通用阈值, 无标的特定叙述."""
    signals = []
    ty_gm = gm["target"]
    lg = ty_gm[-1].get("q_gross_margin") if ty_gm else None
    if lg is not None:
        if lg >= 25:
            signals.append(f"[GM_HIGH] gross_margin >= 25% ({lg}%) → turnaround imminent")
        elif lg >= 19:
            signals.append(f"[GM_RISING] gross_margin {lg}% (approaching breakeven zone)")
        else:
            signals.append(f"[GM_LOW] gross_margin {lg}%, below breakeven line")
    ty_cl = cl["target"]
    qoq = ty_cl[-1].get("qoq_pct") if ty_cl else None
    if qoq is not None:
        signals.append(f"[CL_{'POS' if qoq > 0 else 'NEG'}] contract_liab QoQ {qoq}% → "
                       f"{'order recovery signal' if qoq > 0 else 'order recovery not confirmed'}")
    runway = burn.get("quarters_runway")
    sq_ni = burn.get("latest_quarter_net_income")
    if runway is not None:
        signals.append(f"[CASH_OK] runway {runway} quarters (money_cap / quarterly net loss)")
    elif sq_ni is not None and sq_ni >= 0:
        signals.append("[PROFIT] latest quarter net income >= 0 → breakeven may have passed")
    ocf = latest_q4(burn["target"]).get("ytd_ocf")
    if ocf is not None and ocf > 0:
        signals.append(f"[OCF_POS] annual OCF positive ({ocf}) → operational break-even (cash basis)")
    rd_r = latest_q4(rd["target"]).get("rd_ratio_pct")
    if rd_r and rd_r >= 10:
        signals.append(f"[RD_HIGH] rd_ratio {rd_r}% (annual) → counter-cyclical R&D investment")
    peer_gm = {p: s[-1].get("q_gross_margin") for p, s in gm["peers"].items() if s}
    if lg and peer_gm and all(lg >= (v or -999) for v in peer_gm.values()):
        signals.append(f"[PEER_BEST] target gross_margin {lg}% highest among peers")
    return signals

# ── main ──

def main():
    global _T1_FETCHED
    with open(T1_PATH, encoding="utf-8") as f:
        data = json.load(f)
    _T1_FETCHED = data.get("fetched_at", "unknown")
    print(f"Loaded T1 data, fetched_at={_T1_FETCHED}", file=sys.stderr)
    gm = build_gross_margin(data)
    rd = build_rd_ratio(data)
    exp = build_expense_ratio(data)
    cl = build_contract_liab(data)
    cip = build_cip(data)
    inv = build_inventory_turnover(data)
    burn = build_burn_rate(data)
    capex = build_capex(data)
    signals = build_inflection_signals(gm, rd, cl, burn, capex)
    output = {
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "data_source": f"{T1_PATH.name} (fetched_at={_T1_FETCHED})",
        "target": TARGET_CODE, "peers": PEER_CODES,
        "note": "8个扩展深度指标+盈亏拐点信号。所有analysis字段待agent填写。"
                "c_pay_acquisition_fixed缺失时资本开支用n_cashflow_inv_act代理。",
        "gross_margin": gm, "rd_ratio": rd, "expense_ratio": exp, "contract_liab": cl,
        "cip": cip, "inventory_turnover": inv, "burn_rate": burn, "capex": capex,
        "inflection_signals": signals, "summary": TODO,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ Written {OUT_PATH} ({OUT_PATH.stat().st_size} bytes)", file=sys.stderr)
    for ind in ["gross_margin", "rd_ratio", "expense_ratio", "contract_liab",
                "cip", "inventory_turnover", "burn_rate", "capex"]:
        d = output[ind]
        ok = "✅" if d.get("target") and d.get("peers") else "❌"
        print(f"  {ok} {ind}: target={len(d.get('target', []))}期, peers={len(d.get('peers', {}))}", file=sys.stderr)

if __name__ == "__main__":
    main()
