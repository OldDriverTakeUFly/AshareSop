"""铜冠铜箔 301217.SZ 研报取数脚本（research-report skill Phase 2）。

输出：财务/估值分位/景气度/5 补充因子/股东户数/时效校验/相对估值。
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd  # noqa: E402

from davis_analyzer.tushare_client import TushareClient  # noqa: E402
from davis_analyzer.financial_fetcher import fetch_financial_data  # noqa: E402
from davis_analyzer.prosperity import calculate_prosperity_score  # noqa: E402
from davis_analyzer.prosperity_sector import classify_stock_stage  # noqa: E402
from davis_analyzer.momentum import analyze_momentum  # noqa: E402
from davis_analyzer.dividend import analyze_dividend  # noqa: E402
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision  # noqa: E402
from davis_analyzer.holder_concentration import analyze_holder_concentration  # noqa: E402
from davis_analyzer.profitability import analyze_profitability_quality  # noqa: E402
from stockhot.tushare_config import get_pro_api  # noqa: E402

TS_CODE = "301217.SZ"
NAME = "铜冠铜箔"

pro = get_pro_api(timeout=60)
client = TushareClient()

# ── 0. 代码核对 ──
basic = pro.stock_basic(ts_code=TS_CODE, fields="ts_code,name,industry,area,list_date")
print("== stock_basic ==")
print(basic.to_string())

# ── 1. 财务 12 期 ──
fin = fetch_financial_data(client, TS_CODE, periods=12)
print(f"\n== 财务 {len(fin)} 期, 最新 {fin[0].report_period}, ts_code={fin[0].ts_code} ==")
for f in fin:
    rev = f.revenue / 1e8 if isinstance(f.revenue, (int, float)) else float("nan")
    np_ = f.net_profit / 1e8 if isinstance(f.net_profit, (int, float)) else float("nan")
    yr = f.yoy_revenue_growth if f.yoy_revenue_growth is not None else float("nan")
    yp = f.yoy_profit_growth if f.yoy_profit_growth is not None else float("nan")
    print(f"{f.report_period}: 营收={rev:.2f}亿 同比{yr*100 if abs(yr)<10 else yr:.1f}% "
          f"归母={np_:.3f}亿 同比{yp*100 if abs(yp)<10 else yp:.1f}% ROE={f.roe:.2f}% "
          f"OCF={f.operating_cf/1e8:.2f}亿 毛利率={getattr(f,'grossprofit_margin',None)}")

# ── 2. 估值：分段直连 pro.daily_basic（3 年）──
end = date.today().strftime("%Y%m%d")
start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
frames = []
cur = start
while cur < end:
    nxt = (pd.to_datetime(cur) + pd.Timedelta(days=490)).strftime("%Y%m%d")
    if nxt > end:
        nxt = end
    frames.append(pro.daily_basic(ts_code=TS_CODE, start_date=cur, end_date=nxt,
                                  fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv"))
    cur = nxt
db = pd.concat(frames).drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)
print(f"\n== daily_basic {len(db)} 交易日, {db['trade_date'].iloc[0]} ~ {db['trade_date'].iloc[-1]} ==")
for col in ["pe_ttm", "pb", "ps"]:
    s = pd.to_numeric(db[col], errors="coerce").dropna()
    if len(s) == 0:
        print(f"{col}: 全空")
        continue
    cur_v = s.iloc[-1]
    pct = (s < cur_v).sum() / len(s) * 100
    qs = {p: s.quantile(p / 100) for p in [10, 25, 50, 75, 90, 95]}
    print(f"{col}: 当前={cur_v:.2f} 分位={pct:.1f}% "
          f"分位值10/25/50/75/90/95={qs[10]:.2f}/{qs[25]:.2f}/{qs[50]:.2f}/{qs[75]:.2f}/{qs[90]:.2f}/{qs[95]:.2f} "
          f"最新日={db['trade_date'].iloc[-1]}")
mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
print(f"总市值: {mv.iloc[-1]/1e4:.1f}亿 (最新日同上)")

# ── 3. 景气度 ──
pscore = calculate_prosperity_score(fin)
stage = classify_stock_stage(pscore)
print(f"\n== 景气度 == composite={pscore.composite_score:.1f} ΔG={pscore.delta_g} "
      f"营收分={pscore.revenue_score} 利润分={pscore.profit_score} 斜率={pscore.slope_score} "
      f"持续={pscore.duration_score} 阶段={stage}")

# ── 4. 5 补充因子 ──
print("\n== 补充因子 ==")
mom = analyze_momentum(client, TS_CODE)
if mom:
    print(f"momentum: score={mom.momentum_score} abs={mom.absolute_momentum_score} "
          f"rs_pct={mom.rs_percentile} windows={mom.window_returns}")
div = analyze_dividend(client, TS_CODE)
print(f"dividend: score={div.dividend_score} 连续{div.consecutive_years}年 "
      f"股息率={div.latest_yield_pct}%")
fc = analyze_forecast(client, TS_CODE, pscore)
print(f"forecast: {fc}")
rev = analyze_forecast_revision(client, TS_CODE)
print(f"revision: {rev}")
hc = analyze_holder_concentration(client, TS_CODE)
if hc:
    print(f"holder_conc: score={hc.concentration_score} trend={hc.trend} "
          f"latest_chg={hc.latest_chg_pct} counts={hc.holder_counts} periods={hc.periods}")
pq = analyze_profitability_quality(fin)
print(f"profitability: score={pq.quality_score} 毛利率={pq.latest_gross_margin} "
      f"毛利率Δ={pq.gross_margin_delta} 研发强度={pq.latest_rd_intensity}")

# ── 5. 股东户数（裸接口 + NaN 过滤）──
h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num")
h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(8)
print("\n== 股东户数 ==")
prev = None
for _, r in h.iterrows():
    num = int(r["holder_num"])
    chg = f"{(num-prev)/prev*100:+.1f}%" if prev else "基期"
    print(f"{r['end_date']} (ann {r['ann_date']}): {num:,} ({chg})")
    prev = num

# ── 6. 时效校验 + 业绩预告 ──
print("\n== 时效校验 ==")
inc = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date", limit=1)
print(f"income 最新报告期: {inc.iloc[0]['end_date']}, 披露 {inc.iloc[0]['ann_date']}")
fcdf = pro.forecast(ts_code=TS_CODE,
                    fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,"
                           "net_profit_min,net_profit_max")
if len(fcdf):
    fcdf = fcdf[pd.to_numeric(fcdf["ann_date"]) >= 20250101].sort_values("end_date")
    print(fcdf.to_string())
else:
    print("无 forecast 记录")

# ── 7. 相对估值 ──
try:
    from stockhot.valuation import analyze_relative_valuation
    rv = analyze_relative_valuation(TS_CODE)
    print(f"\n== 相对估值 == pe_ratio_pct={rv.pe_ratio_pct} stock_pe_pct={rv.stock_pe_pct} "
          f"index_pe_pct={rv.index_pe_pct} erp={rv.erp} quadrant={rv.quadrant} "
          f"risk_free={rv.risk_free_rate}")
    print(f"signals: {rv.signals}")
except Exception as e:  # noqa: BLE001
    print(f"相对估值失败: {e}")

# ── 8. 十大流通股东（可选，列裁剪防御）──
try:
    t10 = pro.top10_floatholders(ts_code=TS_CODE)
    if "ratio" in t10.columns:
        t10["end_date"] = t10["end_date"].astype(str)
        g = t10.sort_values("end_date").groupby("end_date")["ratio"].sum()
        print("\n== 十大流通股东合计比例 ==")
        print(g.tail(5).to_string())
    else:
        print("\ntop10_floatholders 无 ratio 列（端点裁剪），跳过")
except Exception as e:  # noqa: BLE001
    print(f"top10 失败: {e}")
