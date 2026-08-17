# ── 猪周期 8 家标的批量取数(估值分位 + 资产负债 + 现金流 + 业绩预告) ──
# 输出 JSON 到 stdout,供《猪周期底部确认清单与标的组合配置》引用
import os, sys, json, time

os.environ.setdefault("PROJECT_ROOT", "/home/leo/Projects/CodeAgentDashboard")
sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv
load_dotenv('/home/leo/Projects/CodeAgentDashboard/.env', override=True)

from stockhot.tushare_config import get_pro_api

pro = get_pro_api()

TARGETS = {
    "002714.SZ": "牧原股份",
    "300498.SZ": "温氏股份",
    "000876.SZ": "新希望",
    "002124.SZ": "天邦食品",
    "605296.SH": "神农集团",
    "603477.SH": "巨星农牧",
    "002840.SZ": "华统股份",
    "001201.SZ": "东瑞股份",
}

out = {}

def pct_rank(series, cur):
    s = [x for x in series if x is not None and x == x]  # drop nan
    if not s or cur is None or cur != cur:
        return None
    return round(100.0 * sum(1 for x in s if x <= cur) / len(s), 1)

for code, name in TARGETS.items():
    rec = {"name": name}
    # 1) 3 年 daily_basic 历史 -> PB/PE/PS 分位 + 最新快照
    try:
        df = pro.daily_basic(ts_code=code, start_date="20230814", end_date="20260814",
                             fields="trade_date,close,total_mv,pb,pe_ttm,ps_ttm")
        df = df.sort_values("trade_date").reset_index(drop=True)
        last = df.iloc[-1]
        rec["snapshot"] = {
            "trade_date": str(last["trade_date"]),
            "close": float(last["close"]),
            "total_mv_yi": round(float(last["total_mv"]) / 10000.0, 1),  # 万元 -> 亿元
            "pb": round(float(last["pb"]), 2),
            "pb_pct": pct_rank(df["pb"].tolist(), float(last["pb"])),
            "pe_ttm": None if last["pe_ttm"] != last["pe_ttm"] else round(float(last["pe_ttm"]), 1),
            "ps_ttm": None if last["ps_ttm"] != last["ps_ttm"] else round(float(last["ps_ttm"]), 2),
            "ps_pct": pct_rank(df["ps_ttm"].tolist(), float(last["ps_ttm"])),
            "mv_pct": pct_rank(df["total_mv"].tolist(), float(last["total_mv"])),
            "n_days": len(df),
        }
    except Exception as e:
        rec["snapshot_err"] = str(e)
    time.sleep(0.2)

    # 2) balancesheet 2026Q1 + 2025FY:负债率/货币资金/短期借款
    for tag, period in [("bs_2026q1", "20260331"), ("bs_2025fy", "20251231")]:
        try:
            b = pro.balancesheet(ts_code=code, period=period,
                                 fields="end_date,total_assets,total_liab,money_cap,st_loan,note_account_recv,inventory")
            if len(b) > 0:
                r = b.iloc[0]
                rec[tag] = {
                    "total_assets_yi": round(float(r["total_assets"]) / 1e8, 1) if r["total_assets"] == r["total_assets"] else None,
                    "total_liab_yi": round(float(r["total_liab"]) / 1e8, 1) if r["total_liab"] == r["total_liab"] else None,
                    "debt_ratio_pct": round(100.0 * float(r["total_liab"]) / float(r["total_assets"]), 1)
                        if (r["total_liab"] == r["total_liab"] and r["total_assets"] == r["total_assets"]) else None,
                    "money_cap_yi": round(float(r["money_cap"]) / 1e8, 1) if r["money_cap"] == r["money_cap"] else None,
                    "st_loan_yi": round(float(r["st_loan"]) / 1e8, 1) if r["st_loan"] == r["st_loan"] else None,
                }
        except Exception as e:
            rec[tag + "_err"] = str(e)
        time.sleep(0.2)

    # 3) fina_indicator 2026Q1 + 2025FY:ROE/毛利率
    for tag, period in [("fi_2026q1", "20260331"), ("fi_2025fy", "20251231")]:
        try:
            f = pro.fina_indicator(ts_code=code, period=period, fields="end_date,roe,roe_dt,grossprofit_margin,netprofit_margin")
            if len(f) > 0:
                r = f.iloc[0]
                rec[tag] = {k: (round(float(r[k]), 2) if r[k] == r[k] else None) for k in ["roe", "grossprofit_margin"]}
        except Exception as e:
            rec[tag + "_err"] = str(e)
        time.sleep(0.2)

    # 4) income 2026Q1:营收/净利
    try:
        i = pro.income(ts_code=code, period="20260331", fields="end_date,revenue,n_income")
        if len(i) > 0:
            rec["income_2026q1"] = {
                "revenue_yi": round(float(i.iloc[0]["revenue"]) / 1e8, 1),
                "n_income_yi": round(float(i.iloc[0]["n_income"]) / 1e8, 2),
            }
    except Exception as e:
        rec["income_err"] = str(e)
    time.sleep(0.2)

    # 5) cashflow 2025FY + 2026Q1:经营现金流
    for tag, period in [("cf_2025fy", "20251231"), ("cf_2026q1", "20260331")]:
        try:
            c = pro.cashflow(ts_code=code, period=period, fields="end_date,n_cashflow_act")
            if len(c) > 0:
                v = c.iloc[0]["n_cashflow_act"]
                rec[tag] = round(float(v) / 1e8, 1) if v == v else None
        except Exception as e:
            rec[tag + "_err"] = str(e)
        time.sleep(0.2)

    # 6) forecast 2026H1:类型/净利润区间(万元!)/变动幅度
    try:
        fc = pro.forecast(ts_code=code, period="20260630",
                          fields="ts_code,ann_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max,last_np")
        if len(fc) > 0:
            r = fc.iloc[0]
            rec["forecast_2026h1"] = {
                "ann_date": str(r["ann_date"]),
                "type": r["type"],
                "p_change_pct": [None if r["p_change_min"] != r["p_change_min"] else round(float(r["p_change_min"]), 1),
                                 None if r["p_change_max"] != r["p_change_max"] else round(float(r["p_change_max"]), 1)],
                # 坑点14:net_profit_min/max 单位是万元
                "net_profit_yi": [None if r["net_profit_min"] != r["net_profit_min"] else round(float(r["net_profit_min"]) / 1e4, 1),
                                  None if r["net_profit_max"] != r["net_profit_max"] else round(float(r["net_profit_max"]) / 1e4, 1)],
            }
    except Exception as e:
        rec["forecast_err"] = str(e)
    time.sleep(0.2)

    out[code] = rec
    print(f"done {code} {name}", file=sys.stderr)

print(json.dumps(out, ensure_ascii=False, indent=1))
