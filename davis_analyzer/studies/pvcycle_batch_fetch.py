# ── 光伏周期 8 家标的批量取数(估值分位 + 资产负债 + 现金流 + 业绩预告 + 回购) ──
# 输出 JSON,供《光伏周期底部确认清单与标的组合配置_202608》引用
# 坑点规避:pro.daily_basic 直连分段(≤500天/段);forecast net_profit_min/max 单位万元
import os, sys, json, time

os.environ.setdefault("PROJECT_ROOT", "/home/leo/Projects/CodeAgentDashboard")
sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv
load_dotenv('/home/leo/Projects/CodeAgentDashboard/.env', override=True)

from stockhot.tushare_config import get_pro_api

pro = get_pro_api()

TARGETS = {
    "600438.SH": "通威股份",
    "601012.SH": "隆基绿能",
    "002129.SZ": "TCL中环",
    "002459.SZ": "晶澳科技",
    "688223.SH": "晶科能源",
    "688599.SH": "天合光能",
    "688303.SH": "大全能源",
    "688472.SH": "阿特斯",
}

out = {}

def pct_rank(series, cur):
    s = [x for x in series if x is not None and x == x]  # drop nan
    if not s or cur is None or cur != cur:
        return None
    return round(100.0 * sum(1 for x in s if x <= cur) / len(s), 1)

def fetch_daily_basic_segments(code, start, end):
    """pro.daily_basic 直连分段(≤500天/段),concat 后按日期升序,校验行数"""
    import pandas as pd
    segs = []
    seg_start = start
    from datetime import datetime, timedelta
    d0 = datetime.strptime(start, "%Y%m%d")
    d1 = datetime.strptime(end, "%Y%m%d")
    while d0 <= d1:
        d2 = min(d0 + timedelta(days=499), d1)
        segs.append((d0.strftime("%Y%m%d"), d2.strftime("%Y%m%d")))
        d0 = d2 + timedelta(days=1)
    frames = []
    for s, e in segs:
        df = pro.daily_basic(ts_code=code, start_date=s, end_date=e,
                             fields="trade_date,close,total_mv,pb,pe_ttm,ps_ttm")
        if df is not None and len(df) > 0:
            frames.append(df)
        time.sleep(0.2)
    if not frames:
        return None
    all_df = pd.concat(frames).drop_duplicates(subset="trade_date").reset_index(drop=True)
    all_df = all_df.sort_values("trade_date").reset_index(drop=True)
    return all_df

for code, name in TARGETS.items():
    rec = {"name": name}
    # 1) 3 年 daily_basic 历史 -> PB/PE/PS 分位 + 最新快照(分段直连,校验行数≥700)
    try:
        df = fetch_daily_basic_segments(code, "20230814", "20260814")
        if df is not None and len(df) >= 700:
            last = df.iloc[-1]
            rec["snapshot"] = {
                "trade_date": str(last["trade_date"]),
                "close": float(last["close"]),
                "total_mv_yi": round(float(last["total_mv"]) / 10000.0, 1),
                "pb": None if last["pb"] != last["pb"] else round(float(last["pb"]), 2),
                "pb_pct": None if last["pb"] != last["pb"] else pct_rank(df["pb"].tolist(), float(last["pb"])),
                "pe_ttm": None if last["pe_ttm"] != last["pe_ttm"] else round(float(last["pe_ttm"]), 1),
                "pe_pct": None if last["pe_ttm"] != last["pe_ttm"] else pct_rank(df["pe_ttm"].tolist(), float(last["pe_ttm"])),
                "ps_ttm": None if last["ps_ttm"] != last["ps_ttm"] else round(float(last["ps_ttm"], ), 2),
                "ps_pct": None if last["ps_ttm"] != last["ps_ttm"] else pct_rank(df["ps_ttm"].tolist(), float(last["ps_ttm"])),
                "n_days": len(df),
            }
        else:
            rec["snapshot_err"] = f"insufficient rows: {0 if df is None else len(df)}"
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

    # 4) income 2026Q1 + 2025FY:营收/净利
    for tag, period in [("income_2026q1", "20260331"), ("income_2025fy", "20251231")]:
        try:
            i = pro.income(ts_code=code, period=period, fields="end_date,revenue,n_income")
            if len(i) > 0:
                rec[tag] = {
                    "revenue_yi": round(float(i.iloc[0]["revenue"]) / 1e8, 1),
                    "n_income_yi": round(float(i.iloc[0]["n_income"]) / 1e8, 2),
                }
        except Exception as e:
            rec[tag + "_err"] = str(e)
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

    # 7) repurchase 回购记录(近2年)
    try:
        rp = pro.repurchase(ts_code=code, fields="ts_code,ann_date,proc,exp_share,exp_amount,amount")
        if rp is not None and len(rp) > 0:
            rp = rp.sort_values("ann_date", ascending=False).head(8)
            rec["repurchase_recent"] = [
                {"ann_date": str(r["ann_date"]), "proc": r["proc"],
                 "amount_yi": None if r["amount"] != r["amount"] else round(float(r["amount"]) / 1e8, 2)}
                for _, r in rp.iterrows()
            ]
        else:
            rec["repurchase_recent"] = []
    except Exception as e:
        rec["repurchase_err"] = str(e)
    time.sleep(0.2)

    out[code] = rec
    print(f"done {code} {name}", file=sys.stderr)

with open("/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/pvcycle_data.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print("saved to studies/pvcycle_data.json", file=sys.stderr)
