# AI漫剧6标的批量数据采集(2026-08-31)
import os, sys, json
sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv
load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd
from stockhot.tushare_config import get_pro_api
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality

STOCKS = {
    "300364.SZ": "中文在线", "603533.SH": "掌阅科技", "000681.SZ": "视觉中国",
    "300418.SZ": "昆仑万维", "300624.SZ": "万兴科技", "605287.SH": "德才股份",
}
pro = get_pro_api(timeout=30)
client = TushareClient()
out = {}

def daily_basic_full(ts, start, end):
    dfs = []
    cur = pd.Timestamp(start); fin = pd.Timestamp(end)
    while cur <= fin:
        seg_end = min(cur + pd.Timedelta(days=490), fin)
        d = pro.daily_basic(ts_code=ts, start_date=cur.strftime("%Y%m%d"),
                            end_date=seg_end.strftime("%Y%m%d"),
                            fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv")
        dfs.append(d); cur = seg_end + pd.Timedelta(days=1)
    df = pd.concat(dfs).drop_duplicates("trade_date").reset_index(drop=True)
    return df.sort_values("trade_date").reset_index(drop=True)

for ts, name in STOCKS.items():
    rec = {"name": name}
    # 0. 代码核对
    try:
        basic = pro.stock_basic(ts_code=ts, fields="ts_code,name,industry,listing_date")
        rec["basic"] = basic.iloc[0].to_dict()
    except Exception as e:
        rec["basic_err"] = str(e)
    # 1. 时效
    try:
        db1 = pro.daily_basic(ts_code=ts, limit=1)
        rec["latest_trade"] = db1.iloc[0]["trade_date"]
        inc = pro.income(ts_code=ts, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
        rec["latest_period"] = inc.iloc[0]["end_date"]; rec["latest_ann"] = inc.iloc[0]["ann_date"]
    except Exception as e:
        rec["fresh_err"] = str(e)
    # 2. 估值分位 3y
    try:
        end = pd.Timestamp("2026-08-31"); start = end - pd.Timedelta(days=1095)
        db = daily_basic_full(ts, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        rec["val_days"] = len(db)
        last = db.iloc[-1]
        rec["snapshot"] = {k: (None if pd.isna(last[k]) else float(last[k])) for k in ["pe_ttm","pb","ps","total_mv"]}
        rec["snapshot"]["trade_date"] = last["trade_date"]
        for k in ["pe_ttm","pb","ps"]:
            s = pd.to_numeric(db[k], errors="coerce").dropna()
            if len(s) > 50 and rec["snapshot"][k] is not None:
                rec[f"{k}_pct"] = round((s < s.iloc[-1]).sum()/len(s)*100, 1)
                rec[f"{k}_quantiles"] = {f"p{p}": round(float(s.quantile(p/100)),2) for p in [10,50,90]}
            else:
                rec[f"{k}_pct"] = None
    except Exception as e:
        rec["val_err"] = str(e)
    # 3. 财务
    try:
        fin = fetch_financial_data(client, ts, periods=12)
        rec["fin"] = [{"period": f.report_period, "rev": f.revenue, "np": f.net_profit,
                       "roe": f.roe, "yoy_rev": f.yoy_revenue_growth, "yoy_np": f.yoy_profit_growth,
                       "ocf": f.operating_cf, "gm": getattr(f, "grossprofit_margin", None),
                       "rd": getattr(f, "rd_exp", None)} for f in fin]
        if len(fin) >= 4:
            ps_ = calculate_prosperity_score(fin)
            rec["prosperity"] = {"composite": ps_.composite_score, "delta_g": ps_.delta_g,
                                 "rev_score": ps_.revenue_score, "profit_score": ps_.profit_score,
                                 "slope": ps_.slope_score, "duration": ps_.duration_score,
                                 "stage": str(classify_stock_stage(ps_))}
            fc = analyze_forecast(client, ts, ps_)
            rec["forecast_signal"] = None if fc is None else {"leading": fc.leading_score, "type": fc.type, "pchg_mid": fc.p_change_mid, "stale": fc.is_stale}
            pq = analyze_profitability_quality(fin)
            rec["profit_quality"] = {"score": pq.quality_score if hasattr(pq,"quality_score") else None,
                                     "gm": getattr(pq,"latest_gross_margin",None), "gm_delta": getattr(pq,"gross_margin_delta",None),
                                     "rd": getattr(pq,"latest_rd_intensity",None)}
    except Exception as e:
        rec["fin_err"] = str(e)
    # 4. 动量/分红/持有人
    for key, fn in [("momentum", lambda: analyze_momentum(client, ts)),
                    ("dividend", lambda: analyze_dividend(client, ts)),
                    ("holder", lambda: analyze_holder_concentration(client, ts))]:
        try:
            o = fn()
            if o is None: rec[key] = None
            elif key == "momentum":
                wr = {}
                for k, v in (o.window_returns or {}).items():
                    bad = v is None or (isinstance(v, float) and (v != v or abs(v) > 50))
                    wr[k] = None if bad else round(v * 100, 1)
                rec[key] = {"score": o.momentum_score, "abs": o.absolute_momentum_score, "rs": o.rs_percentile, "wr": wr}
            elif key == "dividend":
                rec[key] = {"score": o.dividend_score, "years": o.consecutive_years, "yield": o.latest_yield_pct}
            else:
                rec[key] = {"score": o.concentration_score, "trend": o.trend, "latest_chg": o.latest_chg_pct}
        except Exception as e:
            rec[f"{key}_err"] = str(e)
    # 5. 股东户数(原始)
    try:
        h = pro.stk_holdernumber(ts_code=ts, fields="ts_code,ann_date,end_date,holder_num")
        h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(8)
        rec["holder_rows"] = [{"end_date": r["end_date"], "num": int(r["holder_num"])} for _, r in h.iterrows()]
    except Exception as e:
        rec["holder_raw_err"] = str(e)
    # 6. 预告原文(过滤陈旧)
    try:
        f2 = pro.forecast(ts_code=ts)
        f2 = f2[pd.to_numeric(f2["ann_date"]) >= 20250101].sort_values("ann_date")
        rows = []
        for _, r in f2.tail(3).iterrows():
            rows.append({k: (None if pd.isna(r.get(k)) else (float(r[k]) if k in ("p_change_min","p_change_max") else str(r.get(k)))) for k in ["ann_date","end_date","type","p_change_min","p_change_max"]})
        rec["forecasts_raw"] = rows
    except Exception as e:
        rec["fc_err"] = str(e)
    out[ts] = rec
    print(f"done {name}", flush=True)

with open("/tmp/mjdrama_data.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1, default=str)
print("ALL DONE -> /tmp/mjdrama_data.json")
