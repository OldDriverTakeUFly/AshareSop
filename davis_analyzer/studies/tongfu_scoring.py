#!/usr/bin/env python3
"""通富微电 (002156.SZ) 研报取数脚本：四维评分 + 5 因子 + 股东户数 + 相对估值 + 时效校验."""
from __future__ import annotations
import json, os, sys
from datetime import date, timedelta
import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()
sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.valuation import fetch_valuation_history, detect_cyclical
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.scoring import calculate_davis_double_score
from davis_analyzer.trend import batch_trend
from davis_analyzer.types import StockInfo
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality

TS_CODE = "002156.SZ"
NAME = "通富微电"

client = TushareClient()
pro = client._get_api() if hasattr(client, "_get_api") else None
if pro is None:
    from stockhot.tushare_config import get_pro_api
    pro = get_pro_api(timeout=30)

out = {}

# 0. 核对代码
basic = pro.stock_basic(ts_code=TS_CODE, fields="ts_code,name,industry,actual_controller")
print("== stock_basic ==")
print(basic.to_string())

# 1. 财务
fin = fetch_financial_data(client, TS_CODE, periods=12)
print(f"\n== 财务 {len(fin)} 期, fin[0].ts_code={fin[0].ts_code} ==")
for f in fin:
    print(f"{f.report_period}: rev={f.revenue/1e8:.2f}亿 np={float(f.net_profit)/1e8:.3f}亿 eps={f.eps} roe={f.roe}% "
          f"yoy_rev={f.yoy_revenue_growth if f.yoy_revenue_growth is None else round(f.yoy_revenue_growth*100,2)}% "
          f"yoy_np={f.yoy_profit_growth if f.yoy_profit_growth is None else round(f.yoy_profit_growth*100,2)}% "
          f"gm={getattr(f,'grossprofit_margin',None)} rd={getattr(f,'rd_exp',None)} ocf={f.operating_cf/1e8 if f.operating_cf else None}亿")

# 2. 估值（先 fetch_valuation_history，行数不足则直连分段）
val_history = fetch_valuation_history(client, TS_CODE)
print(f"\n== 估值历史 fetch_valuation_history: {len(val_history)} 点 ==")
db = None
if len(val_history) < 700:
    frames = []
    end = date.today()
    start = end - timedelta(days=1150)
    seg_start = start
    while seg_start < end:
        seg_end = min(seg_start + timedelta(days=480), end)
        d = pro.daily_basic(ts_code=TS_CODE, start_date=seg_start.strftime("%Y%m%d"),
                            end_date=seg_end.strftime("%Y%m%d"),
                            fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv")
        frames.append(d)
        seg_start = seg_end + timedelta(days=1)
    db = pd.concat(frames).drop_duplicates("trade_date").reset_index(drop=True)
    db = db.sort_values("trade_date").reset_index(drop=True)
    print(f"直连分段取数: {len(db)} 行, 末行 {db['trade_date'].iloc[-1]}")
    pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
    pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
    ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
    mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
    pe_pct = (pe < pe.iloc[-1]).sum() / len(pe) * 100
    pb_pct = (pb < pb.iloc[-1]).sum() / len(pb) * 100
    ps_pct = (ps < ps.iloc[-1]).sum() / len(ps) * 100
    print(f"PE_TTM={pe.iloc[-1]:.2f} ({pe_pct:.1f}%分位, n={len(pe)}), PB={pb.iloc[-1]:.2f} ({pb_pct:.1f}%分位), "
          f"PS={ps.iloc[-1]:.2f} ({ps_pct:.1f}%分位), 市值={mv.iloc[-1]/1e4:.1f}亿, 日期={db['trade_date'].iloc[-1]}")
    for p in [10, 25, 50, 75, 90, 95]:
        print(f"  分位 PE {p}%: {pe.quantile(p/100):.2f} | PB {p}%: {pb.quantile(p/100):.2f} | PS {p}%: {ps.quantile(p/100):.2f}")
    # YTD
    d_close = pro.daily(ts_code=TS_CODE, start_date="20251230", end_date=date.today().strftime("%Y%m%d"),
                        fields="trade_date,close,pre_close")
    d_close = d_close.sort_values("trade_date")
    ytd = (d_close["close"].iloc[-1] / d_close["pre_close"].iloc[0] - 1) * 100
    print(f"YTD 涨幅: {ytd:.1f}%")
    # 250日区间
    d250 = pro.daily(ts_code=TS_CODE, start_date=(date.today()-timedelta(days=400)).strftime("%Y%m%d"),
                     end_date=date.today().strftime("%Y%m%d"), fields="trade_date,close").sort_values("trade_date")
    print(f"近1年股价区间: {d250['close'].min()} ~ {d250['close'].max()}, 现价 {d250['close'].iloc[-1]}")

# 3. 景气度 + 阶段
pscore = calculate_prosperity_score(fin)
stage = classify_stock_stage(pscore)
print(f"\n== 景气度 == composite={pscore.composite_score} revenue={pscore.revenue_score} profit={pscore.profit_score} "
      f"slope={pscore.slope_score} duration={pscore.duration_score} delta_g={pscore.delta_g} 阶段={stage}")

# 4. 困境 + davis（用 fetch 的分位或自算）
if db is not None:
    pe_p, pb_p = pe_pct/100, pb_pct/100
else:
    from davis_analyzer.valuation import calculate_percentile
    pe_p = calculate_percentile(val_history[0].pe_ttm, [v.pe_ttm for v in val_history])
    pb_p = calculate_percentile(val_history[0].pb, [v.pb for v in val_history])

latest = fin[0]
debt_ratio = (latest.total_debt or 0) / (latest.total_assets or 1)
dscore = calculate_distress_score(
    eps_history=[f.eps for f in fin], pe_pct=pe_p, pb_pct=pb_p, debt_ratio=debt_ratio,
    operating_cf=latest.operating_cf or 0, total_debt=latest.total_debt or 0,
    total_assets=latest.total_assets or 0, roe_history=[f.roe for f in fin],
    revenue_history=[f.yoy_revenue_growth or 0 for f in fin],
    profit_history=[f.yoy_profit_growth or 0 for f in fin],
    delta_g=pscore.delta_g, ts_code=TS_CODE)
print(f"\n== 困境 == total={dscore.total_score} L1={dscore.layer1_score} L2={dscore.layer2_score} L3={dscore.layer3_score}")

# 趋势
trend_score = 50.0
if db is not None and len(db) >= 3:
    dates = pd.to_datetime(db["trade_date"], format="%Y%m%d")
    si = StockInfo(ts_code=TS_CODE, name=NAME, industry="半导体", list_status="L", is_cyclical=False)
    tm = batch_trend({TS_CODE: (pd.Series(pd.to_numeric(db["pe_ttm"], errors="coerce").values, index=dates),
                                 pd.Series(pd.to_numeric(db["pb"], errors="coerce").values, index=dates))}, {TS_CODE: si})
    trend_score = tm.get(TS_CODE, 50.0)

# valuation score 简化：非周期 100-分位混合
val_score = (100 - pe_p*100) * 0.7 + (100 - pb_p*100) * 0.3
davis = calculate_davis_double_score(valuation_score=val_score, prosperity_score=pscore.composite_score,
                                     distress_score=dscore.total_score, trend_score=trend_score,
                                     ts_code=TS_CODE, name=NAME)
print(f"== 戴维斯 == final={davis.final_score} (估值={val_score:.1f} 趋势={trend_score:.1f} 景气={pscore.composite_score:.1f} 困境={dscore.total_score:.1f})")

# 5. 五因子
print("\n== 5 因子 ==")
mom = analyze_momentum(client, TS_CODE)
if mom:
    print(f"momentum: score={mom.momentum_score} abs={mom.absolute_momentum_score} rs_pct={mom.rs_percentile} windows={mom.window_returns}")
div = analyze_dividend(client, TS_CODE)
print(f"dividend: score={div.dividend_score} years={div.consecutive_years} yield={div.latest_yield_pct}")
fc = analyze_forecast(client, TS_CODE, pscore)
print(f"forecast: {fc}")
rev = analyze_forecast_revision(client, TS_CODE)
print(f"revision: {rev}")
hc = analyze_holder_concentration(client, TS_CODE)
if hc:
    print(f"holder_conc: score={hc.concentration_score} trend={hc.trend} chg={hc.latest_chg_pct} counts={hc.holder_counts}")
pq = analyze_profitability_quality(fin)
print(f"profitability: score={pq.quality_score} gm={pq.latest_gross_margin} gm_delta={pq.gross_margin_delta} rd={pq.latest_rd_intensity}")

# 6. 股东户数
h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num").dropna(subset=["holder_num"])
h = h.sort_values("end_date").tail(10)
print("\n== 股东户数 ==")
print(h.to_string())

# 7. 时效校验
db1 = pro.daily_basic(ts_code=TS_CODE, limit=1)
inc = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date", limit=3)
fcv = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
print(f"\n== 时效 == 最新交易日: {db1.iloc[0]['trade_date'] if len(db1) else None}")
print(inc.to_string())
fcv = fcv[pd.to_numeric(fcv["ann_date"]) >= 20250101] if len(fcv) else fcv
print(fcv.to_string() if len(fcv) else "无近期业绩预告")

# 8. 相对估值
try:
    from stockhot.valuation import analyze_relative_valuation
    rv = analyze_relative_valuation(client, TS_CODE)
    print("\n== 相对估值 ==")
    print(f"pe_ratio={getattr(rv,'pe_ratio',None)} pe_ratio_pct={getattr(rv,'pe_ratio_pct',None)} "
          f"stock_pe_pct={getattr(rv,'stock_pe_pct',None)} index_pe={getattr(rv,'index_pe',None)} "
          f"index_pe_pct={getattr(rv,'index_pe_pct',None)} erp={getattr(rv,'erp',None)} "
          f"rf={getattr(rv,'risk_free_rate',None)} quadrant={getattr(rv,'quadrant',None)}")
    print(f"signals: {getattr(rv,'signals',None)}")
except Exception as e:
    print(f"相对估值失败: {e}")

# 9. 十大流通股东（持股变化）
try:
    t10 = pro.top10_floatholders(ts_code=TS_CODE, period="20260630")
    t10p = pro.top10_floatholders(ts_code=TS_CODE, period="20260331")
    def agg(df):
        if df is None or not len(df) or "ratio" not in df.columns:
            return None
        return pd.to_numeric(df["ratio"], errors="coerce").sum()
    print(f"\n== 十大流通股东合计 == 20260630={agg(t10)} 20260331={agg(t10p)}")
    if t10 is not None and len(t10):
        print(t10.head(12).to_string())
except Exception as e:
    print(f"十大流通股东失败: {e}")

print("\nDONE")
