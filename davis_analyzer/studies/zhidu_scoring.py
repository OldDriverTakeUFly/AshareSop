import os
from dotenv import load_dotenv
load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

from datetime import date, timedelta
import pandas as pd

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.valuation import fetch_valuation_history, detect_cyclical
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality

from stockhot.tushare_config import get_pro_api

TS_CODE = "000676.SZ"
NAME = "智度股份"

client = TushareClient()
pro = get_pro_api(timeout=30)

print("=" * 70)
print(f"标的: {NAME} ({TS_CODE})")
print("=" * 70)

# ── 0. 时效性校验 ──
print("\n## 0. 时效性校验")
db0 = pro.daily_basic(ts_code=TS_CODE, limit=1)
inc0 = pro.income(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
fc0 = pro.forecast(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
print(f"  daily_basic 最新交易日: {db0.iloc[0]['trade_date'] if len(db0) else 'none'}")
print(f"  income 最新报告期: {inc0.iloc[0]['end_date'] if len(inc0) else 'none'}, 披露日: {inc0.iloc[0]['ann_date'] if len(inc0) else 'none'}")
if len(fc0):
    r = fc0.iloc[0]
    print(f"  业绩预告: {r['type']} ann={r['ann_date']} end={r['end_date']} 同比=[{r['p_change_min']}, {r['p_change_max']}]%")
else:
    print("  业绩预告: 无")

# ── 1. 财务 ──
print("\n## 1. 财务数据（近 12 期）")
fin = fetch_financial_data(client, TS_CODE, periods=12)
print(f"  期数: {len(fin)}, 最新报告期: {fin[0].report_period}, ts_code核对: {fin[0].ts_code}")
print(f"  {'报告期':<12}{'营收(亿)':<12}{'归母净利(亿)':<14}{'EPS':<8}{'ROE%':<8}{'营收同比':<10}{'净利同比':<10}{'毛利率%':<10}{'研发(亿)':<10}")
for f in fin:
    rev_yi = f.revenue / 1e8 if f.revenue else 0
    np_yi = float(f.net_profit) / 1e8 if f.net_profit else 0
    rev_yoy = f"{f.yoy_revenue_growth*100:+.1f}%" if f.yoy_revenue_growth is not None else "N/A"
    np_yoy = f"{f.yoy_profit_growth*100:+.1f}%" if f.yoy_profit_growth is not None else "N/A"
    gm = f.grossprofit_margin if hasattr(f, 'grossprofit_margin') and f.grossprofit_margin else 0
    rd = f.rd_exp / 1e8 if hasattr(f, 'rd_exp') and f.rd_exp else 0
    print(f"  {f.report_period:<12}{rev_yi:<12.2f}{np_yi:<14.2f}{f.eps:<8.3f}{f.roe:<8.2f}{rev_yoy:<10}{np_yoy:<10}{gm:<10.2f}{rd:<10.2f}")

# ── 2. 估值 ──
print("\n## 2. 估值数据（近 3 年 daily_basic）")
end = date.today().strftime("%Y%m%d")
start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
db = client.get_daily_basic(TS_CODE, start, end)
db = db.sort_values("trade_date")  # 必须升序，否则分位算反
pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna().sort_index()
pb = pd.to_numeric(db["pb"], errors="coerce").dropna().sort_index()
ps = pd.to_numeric(db["ps"], errors="coerce").dropna().sort_index()
mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna().sort_index()
print(f"  数据点: PE有效={len(pe)}, PB={len(pb)}, PS={len(ps)}")
print(f"  最新交易日: {db['trade_date'].iloc[-1]}, 最新市值: {mv.iloc[-1]/1e4:.1f}亿")
print(f"  当前 PE_TTM: {pe.iloc[-1]:.2f}, PB: {pb.iloc[-1]:.2f}, PS: {ps.iloc[-1]:.2f}")
if len(pe) > 0:
    print(f"  PE 分位: {(pe<pe.iloc[-1]).sum()/len(pe)*100:.1f}%")
if len(pb) > 0:
    print(f"  PB 分位: {(pb<pb.iloc[-1]).sum()/len(pb)*100:.1f}%")
if len(ps) > 0:
    print(f"  PS 分位: {(ps<ps.iloc[-1]).sum()/len(ps)*100:.1f}%")
print(f"\n  PE 分位值表:")
for p in [10,25,50,75,90,95]:
    print(f"    {p}%: {pe.quantile(p/100):.2f}")
print(f"\n  PB 分位值表:")
for p in [10,25,50,75,90,95]:
    print(f"    {p}%: {pb.quantile(p/100):.2f}")
print(f"\n  PS 分位值表:")
for p in [10,25,50,75,90,95]:
    print(f"    {p}%: {ps.quantile(p/100):.2f}")

# 周期股判定
try:
    basic = pro.stock_basic(ts_code=TS_CODE, fields="ts_code,name,industry")
    ind = basic.iloc[0]["industry"] if len(basic) else "未知"
    print(f"  Tushare 行业: {ind}, 周期股: {detect_cyclical(ind)}")
except Exception as e:
    print(f"  行业查询失败: {e}")

# ── 3. 景气度 ──
print("\n## 3. 景气度（G+ΔG）")
pscore = calculate_prosperity_score(fin)
stage = classify_stock_stage(pscore)
print(f"  composite_score: {pscore.composite_score:.1f}")
print(f"  delta_g (ΔG): {pscore.delta_g:.2f}")
print(f"  revenue_score: {pscore.revenue_score:.1f} (权重0.30)")
print(f"  profit_score: {pscore.profit_score:.1f} (权重0.30)")
print(f"  slope_score: {pscore.slope_score:.1f} (权重0.25)")
print(f"  duration_score: {pscore.duration_score:.1f} (权重0.15)")
print(f"  relative_delta_g: {pscore.relative_delta_g:.2f}")
print(f"  阶段: {stage}")

# ── 4. 补充因子 ──
print("\n## 4. 补充因子（5 引擎）")
try:
    mom = analyze_momentum(client, TS_CODE)
    if mom:
        print(f"  [动量] momentum_score={mom.momentum_score:.1f}, abs_score={mom.absolute_momentum_score:.1f}, rs_pct={mom.rs_percentile:.1f}")
        if hasattr(mom, 'window_returns') and mom.window_returns:
            print(f"         window_returns: {mom.window_returns}")
    else:
        print("  [动量] 数据不足")
except Exception as e:
    print(f"  [动量] 失败: {e}")

try:
    div = analyze_dividend(client, TS_CODE)
    print(f"  [分红] dividend_score={div.dividend_score:.1f}, 连续{div.consecutive_years}年, 股息率={div.latest_yield_pct:.2f}%")
except Exception as e:
    print(f"  [分红] 失败: {e}")

try:
    fc = analyze_forecast(client, TS_CODE, pscore.composite_score)
    if fc:
        print(f"  [预告] leading_score={fc.leading_score:.1f}, p_change_mid={getattr(fc,'p_change_mid',None)}, type={getattr(fc,'type',None)}, stale={getattr(fc,'is_stale',None)}")
    else:
        print("  [预告] 无")
except Exception as e:
    print(f"  [预告] 失败: {e}")

try:
    rev = analyze_forecast_revision(client, TS_CODE)
    if rev:
        print(f"  [预告修正] dir={rev.revision_direction}, pp={getattr(rev,'revision_pp',None)}, score={getattr(rev,'revision_score',None)}")
    else:
        print("  [预告修正] 无")
except Exception as e:
    print(f"  [预告修正] 失败: {e}")

try:
    hc = analyze_holder_concentration(client, TS_CODE)
    if hc:
        print(f"  [筹码集中] score={hc.score if hasattr(hc,'score') else 'N/A'}, trend={getattr(hc,'trend',None)}")
    else:
        print("  [筹码集中] 数据不足")
except Exception as e:
    print(f"  [筹码集中] 失败: {e}")

try:
    pq = analyze_profitability_quality(fin)
    print(f"  [盈利质量] {pq}")
except Exception as e:
    print(f"  [盈利质量] 失败: {e}")

# ── 5. 股东户数趋势 ──
print("\n## 5. 股东户数趋势（筹码集中度）")
try:
    h = pro.stk_holdernumber(ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num").sort_values("end_date").tail(8)
    print(f"  {'报告期':<12}{'户数':<14}{'环比':<10}")
    prev = None
    for _, r in h.iterrows():
        num = int(r["holder_num"])
        chg = f"{(num-prev)/prev*100:+.1f}%" if prev else "基期"
        print(f"  {r['end_date']:<12}{num:<14,}{chg:<10}")
        prev = num
except Exception as e:
    print(f"  失败: {e}")

# ── 6. 十大流通股东 ──
print("\n## 6. 十大流通股东（最新）")
try:
    top10 = pro.top10_floatholders(ts_code=TS_CODE).sort_values("end_date").tail(10)
    latest_period = top10["end_date"].iloc[0] if len(top10) else None
    if latest_period:
        latest = top10[top10["end_date"]==latest_period]
        total_pct = latest["hold_ratio"].sum() if len(latest) else 0
        print(f"  报告期: {latest_period}, top10合计持股: {total_pct:.2f}%")
        for _, r in latest.iterrows():
            print(f"    {r['holder_name']}: {r['hold_ratio']:.2f}%")
except Exception as e:
    print(f"  失败: {e}")

print("\n" + "=" * 70)
print("数据采集完成")
