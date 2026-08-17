# ── 补数:balancesheet(修字段) + 温氏 forecast + 牧原回购/十大流通股东 ──
import os, sys, json, time

os.environ.setdefault("PROJECT_ROOT", "/home/leo/Projects/CodeAgentDashboard")
sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv
load_dotenv('/home/leo/Projects/CodeAgentDashboard/.env', override=True)

from stockhot.tushare_config import get_pro_api
pro = get_pro_api()

out = {}

# 1) balancesheet:去掉 st_loan,补 total_cur_liab
TARGETS = {
    "002714.SZ": "牧原股份", "300498.SZ": "温氏股份", "000876.SZ": "新希望",
    "002124.SZ": "天邦食品", "605296.SH": "神农集团", "603477.SH": "巨星农牧",
    "002840.SZ": "华统股份", "001201.SZ": "东瑞股份",
}
bs_out = {}
for code, name in TARGETS.items():
    rec = {}
    for tag, period in [("q1_2026", "20260331"), ("fy_2025", "20251231")]:
        try:
            b = pro.balancesheet(ts_code=code, period=period,
                                 fields="end_date,total_assets,total_liab,money_cap,total_cur_liab")
            if len(b) > 0:
                r = b.iloc[0]
                rec[tag] = {
                    "total_assets_yi": round(float(r["total_assets"]) / 1e8, 1),
                    "total_liab_yi": round(float(r["total_liab"]) / 1e8, 1),
                    "debt_ratio_pct": round(100.0 * float(r["total_liab"]) / float(r["total_assets"]), 1),
                    "money_cap_yi": round(float(r["money_cap"]) / 1e8, 1),
                    "cur_liab_yi": round(float(r["total_cur_liab"]) / 1e8, 1),
                }
        except Exception as e:
            rec[tag + "_err"] = str(e)
        time.sleep(0.2)
    bs_out[code] = {"name": name, **rec}
out["balancesheet"] = bs_out

# 2) 温氏全部近期 forecast
try:
    fc = pro.forecast(ts_code="300498.SZ", ann_date="", start_date="20260101", end_date="20260814",
                      fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
    out["wen_forecasts"] = fc.to_dict(orient="records") if len(fc) > 0 else "EMPTY"
except Exception as e:
    out["wen_forecasts_err"] = str(e)
time.sleep(0.2)

# 3) 牧原回购(repurchase)
try:
    rp = pro.repurchase(ts_code="002714.SZ", ann_date="", start_date="20250101", end_date="20260814")
    out["muyuan_repurchase"] = rp.to_dict(orient="records")[:8] if len(rp) > 0 else "EMPTY"
except Exception as e:
    out["muyuan_repurchase_err"] = str(e)
time.sleep(0.2)

# 4) 其余 7 家回购(信号⑨ 产业资本增持回购)
rp_out = {}
for code, name in TARGETS.items():
    if code == "002714.SZ":
        continue
    try:
        rp = pro.repurchase(ts_code=code, start_date="20250101", end_date="20260814")
        rp_out[code] = {"name": name, "n": len(rp),
                        "latest": rp.to_dict(orient="records")[:2] if len(rp) > 0 else None}
    except Exception as e:
        rp_out[code] = {"name": name, "err": str(e)}
    time.sleep(0.2)
out["repurchase_others"] = rp_out

# 5) 牧原十大流通股东(近 3 期,验证信号⑨)
try:
    t10 = pro.top10_floatholders(ts_code="002714.SZ", period="20260331")
    out["muyuan_top10_2026q1"] = t10.to_dict(orient="records")[:6] if len(t10) > 0 else "EMPTY"
except Exception as e:
    out["muyuan_top10_err"] = str(e)

print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
