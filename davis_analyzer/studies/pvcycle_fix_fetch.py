# ── 光伏取数修复:snapshot 回退有效行 + balancesheet 可选字段 + income 归母口径 ──
import os, sys, json, time
os.environ.setdefault("PROJECT_ROOT", "/home/leo/Projects/CodeAgentDashboard")
sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv
load_dotenv('/home/leo/Projects/CodeAgentDashboard/.env', override=True)

import pandas as pd
from stockhot.tushare_config import get_pro_api
pro = get_pro_api()

PATH = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/pvcycle_data.json"
with open(PATH, encoding="utf-8") as f:
    out = json.load(f)

TARGETS = {
    "600438.SH": "通威股份", "601012.SH": "隆基绿能", "002129.SZ": "TCL中环",
    "002459.SZ": "晶澳科技", "688223.SH": "晶科能源", "688599.SH": "天合光能",
    "688303.SH": "大全能源", "688472.SH": "阿特斯",
}

def pct_rank(series, cur):
    s = [x for x in series if x is not None and x == x]
    if not s or cur is None or cur != cur:
        return None
    return round(100.0 * sum(1 for x in s if x <= cur) / len(s), 1)

def fetch_db_segments(code, start, end):
    from datetime import datetime, timedelta
    frames = []
    d0 = datetime.strptime(start, "%Y%m%d"); d1 = datetime.strptime(end, "%Y%m%d")
    while d0 <= d1:
        d2 = min(d0 + timedelta(days=499), d1)
        df = pro.daily_basic(ts_code=code, start_date=d0.strftime("%Y%m%d"), end_date=d2.strftime("%Y%m%d"),
                             fields="trade_date,close,total_mv,pb,pe_ttm,ps_ttm")
        if df is not None and len(df) > 0:
            frames.append(df)
        time.sleep(0.18)
        d0 = d2 + timedelta(days=1)
    if not frames: return None
    all_df = pd.concat(frames).drop_duplicates(subset="trade_date").reset_index(drop=True)
    return all_df.sort_values("trade_date").reset_index(drop=True)

def num(x, nd=2):
    return None if (x is None or x != x) else round(float(x), nd)

# ── Fix 1: snapshot(7 家失败重拉;阿特斯成功也重拉统一口径 20260814 回退) ──
for code, name in TARGETS.items():
    try:
        df = fetch_db_segments(code, "20230814", "20260814")
        if df is None or len(df) < 700:
            out[code]["snapshot_err"] = f"insufficient rows: {0 if df is None else len(df)}"
            continue
        # 从最后往前找 close/pb 均有效的行
        idx = len(df) - 1
        while idx >= 0 and (df.iloc[idx]["close"] != df.iloc[idx]["close"] or df.iloc[idx]["pb"] != df.iloc[idx]["pb"]):
            idx -= 1
        last = df.iloc[idx]
        out[code]["snapshot"] = {
            "trade_date": str(last["trade_date"]),
            "close": num(last["close"]),
            "total_mv_yi": num(last["total_mv"], 1) and round(float(last["total_mv"]) / 1e4, 1),
            "pb": num(last["pb"]),
            "pb_pct": pct_rank(df["pb"].tolist(), float(last["pb"])),
            "pe_ttm": num(last["pe_ttm"], 1),
            "pe_pct": pct_rank([x for x in df["pe_ttm"].tolist()], float(last["pe_ttm"])) if last["pe_ttm"] == last["pe_ttm"] else None,
            "ps_ttm": num(last["ps_ttm"]),
            "ps_pct": pct_rank(df["ps_ttm"].tolist(), float(last["ps_ttm"])) if last["ps_ttm"] == last["ps_ttm"] else None,
            "n_days": int(len(df)),
        }
        out[code].pop("snapshot_err", None)
        print(f"snapshot ok {code} {out[code]['snapshot']['trade_date']} pb={out[code]['snapshot']['pb']} pct={out[code]['snapshot']['pb_pct']}", file=sys.stderr)
    except Exception as e:
        out[code]["snapshot_err"] = str(e)
        print(f"snapshot fail {code}: {e}", file=sys.stderr)
    time.sleep(0.2)

# ── Fix 2: balancesheet(全部 16 家次;st_loan 改为可选列) ──
for code, name in TARGETS.items():
    for tag, period in [("bs_2026q1", "20260331"), ("bs_2025fy", "20251231")]:
        try:
            b = pro.balancesheet(ts_code=code, period=period,
                                 fields="end_date,total_assets,total_liab,money_cap,st_loan")
            if b is not None and len(b) > 0:
                r = b.iloc[0]
                rec = {
                    "total_assets_yi": round(float(r["total_assets"]) / 1e8, 1) if r["total_assets"] == r["total_assets"] else None,
                    "total_liab_yi": round(float(r["total_liab"]) / 1e8, 1) if r["total_liab"] == r["total_liab"] else None,
                    "debt_ratio_pct": round(100.0 * float(r["total_liab"]) / float(r["total_assets"]), 1)
                        if (r["total_liab"] == r["total_liab"] and r["total_assets"] == r["total_assets"]) else None,
                    "money_cap_yi": round(float(r["money_cap"]) / 1e8, 1) if r["money_cap"] == r["money_cap"] else None,
                }
                if "st_loan" in b.columns and r.get("st_loan") is not None:
                    rec["st_loan_yi"] = round(float(r["st_loan"]) / 1e8, 1) if r["st_loan"] == r["st_loan"] else None
                out[code][tag] = rec
                out[code].pop(tag + "_err", None)
        except Exception as e:
            out[code][tag + "_err"] = str(e)
        time.sleep(0.18)

# ── Fix 3: income 归母口径 n_income_attr_p ──
for code, name in TARGETS.items():
    for tag, period in [("income_2026q1", "20260331"), ("income_2025fy", "20251231")]:
        try:
            i = pro.income(ts_code=code, period=period, fields="end_date,revenue,n_income,n_income_attr_p")
            if i is not None and len(i) > 0:
                r = i.iloc[0]
                out[code][tag] = {
                    "revenue_yi": round(float(r["revenue"]) / 1e8, 1) if r["revenue"] == r["revenue"] else None,
                    "n_income_yi": round(float(r["n_income"]) / 1e8, 2) if r["n_income"] == r["n_income"] else None,
                    "n_income_attr_yi": round(float(r["n_income_attr_p"]) / 1e8, 2) if r["n_income_attr_p"] == r["n_income_attr_p"] else None,
                }
        except Exception as e:
            out[code][tag + "_err"] = str(e)
        time.sleep(0.18)

# ── Fix 4: forecast 补拉(晶科/大全无预告则试 ann_date 降序取最新一条) ──
for code in ["688223.SH", "688303.SH"]:
    try:
        fc = pro.forecast(ts_code=code, period="20260630",
                          fields="ts_code,ann_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max,last_np")
        if fc is not None and len(fc) > 0:
            r = fc.iloc[0]
            out[code]["forecast_2026h1"] = {
                "ann_date": str(r["ann_date"]), "type": r["type"],
                "p_change_pct": [num(r["p_change_min"], 1), num(r["p_change_max"], 1)],
                "net_profit_yi": [num(r["net_profit_min"], 0) and round(float(r["net_profit_min"]) / 1e4, 1) if r["net_profit_min"] == r["net_profit_min"] else None,
                                  round(float(r["net_profit_max"]) / 1e4, 1) if r["net_profit_max"] == r["net_profit_max"] else None],
            }
        else:
            out[code]["forecast_2026h1"] = None  # 确认无 H1 预告
    except Exception as e:
        out[code]["forecast_err"] = str(e)
    time.sleep(0.18)

with open(PATH, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print("fixed saved", file=sys.stderr)
