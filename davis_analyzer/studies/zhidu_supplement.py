"""智度股份补取脚本：修复 forecast 传参 bug + 股东户数 NaN + 估值全量 + 财务交叉核实。

这是对 zhidu_scoring.py 主脚本的补充，只跑需要修复/核实的部分。
"""
import os
from dotenv import load_dotenv

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

from datetime import date, timedelta

import pandas as pd

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision

from stockhot.tushare_config import get_pro_api

TS_CODE = "000676.SZ"

client = TushareClient()
pro = get_pro_api(timeout=30)

# ── 1. 景气度（forecast 需要）──
fin = fetch_financial_data(client, TS_CODE, periods=12)
pscore = calculate_prosperity_score(fin)

# ── 2. 修复 forecast：传 pscore 对象 ──
print("## 修复1: analyze_forecast 传 ProsperityScore 对象")
try:
    fc = analyze_forecast(client, TS_CODE, pscore)
    if fc:
        print(f"  leading_score = {fc.leading_score}")
        for attr in dir(fc):
            if not attr.startswith("_"):
                try:
                    val = getattr(fc, attr)
                    if not callable(val):
                        print(f"    {attr} = {val}")
                except Exception:
                    pass
    else:
        print("  无预告信号")
except Exception as e:
    print(f"  失败: {type(e).__name__}: {e}")

print()

# ── 3. 股东户数：dropna + 完整趋势 ──
print("## 修复2: 股东户数 dropna 完整趋势")
try:
    h = (
        pro.stk_holdernumber(
            ts_code=TS_CODE, fields="ts_code,ann_date,end_date,holder_num"
        )
        .dropna(subset=["holder_num"])
        .sort_values("end_date")
        .tail(10)
    )
    print(f"  {'报告期':<12}{'披露日':<12}{'户数':<14}{'环比':<10}")
    prev = None
    for _, r in h.iterrows():
        num = int(r["holder_num"])
        chg = f"{(num - prev) / prev * 100:+.1f}%" if prev else "基期"
        print(f"  {r['end_date']:<12}{str(r['ann_date']):<12}{num:<14,}{chg:<10}")
        prev = num
    # 近4期趋势
    nums = [int(r["holder_num"]) for _, r in h.iterrows()]
    if len(nums) >= 4:
        recent4 = nums[-4:]
        trend = (
            "集中(动能增强✓)" if recent4[-1] < recent4[0] else "分散(动能减弱⚠)"
        )
        chg_total = (recent4[-1] - recent4[0]) / recent4[0] * 100
        print(f"  → 近4期: {recent4[0]:,} → {recent4[-1]:,} ({chg_total:+.1f}%), 趋势={trend}")
except Exception as e:
    print(f"  失败: {type(e).__name__}: {e}")

print()

# ── 4. 估值全量：直接 pro.daily_binary 绕过 client 缓存 ──
print("## 修复3: 估值全量（pro.daily_basic 直拉3年）")
try:
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
    db_raw = pro.daily_basic(
        ts_code=TS_CODE,
        start_date=start,
        end_date=end,
        fields="ts_code,trade_date,pe,pe_ttm,pb,ps,total_mv",
    )
    print(f"  pro.daily_basic 原始返回行数: {len(db_raw)}")
    if len(db_raw) > 0:
        db = db_raw.sort_values("trade_date")
        print(f"  日期范围: {db['trade_date'].iloc[0]} ~ {db['trade_date'].iloc[-1]}")
        for col in ["pe_ttm", "pe", "pb", "ps", "total_mv"]:
            s = pd.to_numeric(db[col], errors="coerce").dropna()
            if len(s) > 0:
                pct = (s < s.iloc[-1]).sum() / len(s) * 100
                print(
                    f"  {col}: 有效{len(s)}点, 当前={s.iloc[-1]:.2f}, "
                    f"分位={pct:.1f}%, "
                    f"[10/25/50/75/90]={s.quantile(0.1):.2f}/{s.quantile(0.25):.2f}/"
                    f"{s.quantile(0.5):.2f}/{s.quantile(0.75):.2f}/{s.quantile(0.9):.2f}"
                )
        mv_latest = pd.to_numeric(db["total_mv"], errors="coerce").dropna().iloc[-1]
        print(f"  最新市值: {mv_latest / 1e4:.1f}亿")
except Exception as e:
    print(f"  失败: {type(e).__name__}: {e}")

print()

# ── 5. 财务交叉核实：pro.income 原始 + fina_indicator ──
print("## 修复4: 财务数据交叉核实（pro.income 原始字段）")
try:
    inc = pro.income(
        ts_code=TS_CODE,
        fields="ts_code,ann_date,end_date,f_ann_date,update_flag,report_type,"
        "total_revenue,n_income,n_income_attr_p,net_profit_excl_min_int_inc,"
        "oper_profit,income_tax,operate_profit,total_cogs,oper_cost",
    ).sort_values(["end_date", "ann_date"])
    print(f"  pro.income 总行数: {len(inc)}")
    # 取合并报表（report_type==1 为合并）
    if "report_type" in inc.columns:
        merged = inc[inc["report_type"] == "1"]
        print(f"  合并报表(report_type=1)行数: {len(merged)}")
        recent = merged[merged["end_date"] >= "20241231"]
    else:
        recent = inc[inc["end_date"] >= "20241231"]
    print(
        f"  {'报告期':<12}{'披露日':<12}{'营收(亿)':<10}{'归母净利(亿)':<12}{'净利(含少数)':<12}"
    )
    for _, r in recent.iterrows():
        rev = float(r["total_revenue"]) / 1e8 if r["total_revenue"] else 0
        np_attr = float(r["n_income_attr_p"]) / 1e8 if r["n_income_attr_p"] else 0
        ni = float(r["n_income"]) / 1e8 if r["n_income"] else 0
        print(
            f"  {r['end_date']:<12}{str(r['ann_date']):<12}{rev:<10.2f}{np_attr:<12.2f}{ni:<12.2f}"
        )
except Exception as e:
    print(f"  失败: {type(e).__name__}: {e}")

print()

# ── 6. 毛利率/研发：fina_indicator ──
print("## 修复5: 毛利率/研发费用（pro.fina_indicator）")
try:
    fi = pro.fina_indicator(
        ts_code=TS_CODE,
        fields="ts_code,end_date,ann_date,grossprofit_margin,netprofit_margin,"
        "rd_exp,update_flag",
    ).sort_values("end_date")
    print(f"  fina_indicator 行数: {len(fi)}")
    recent = fi[fi["end_date"] >= "20231231"]
    print(f"  {'报告期':<12}{'毛利率%':<10}{'净利率%':<10}{'研发(亿)':<10}")
    for _, r in recent.iterrows():
        gm = r["grossprofit_margin"] if r["grossprofit_margin"] else 0
        nm = r["netprofit_margin"] if r["netprofit_margin"] else 0
        rd = float(r["rd_exp"]) / 1e8 if r["rd_exp"] else 0
        print(f"  {r['end_date']:<12}{gm:<10.2f}{nm:<10.2f}{rd:<10.2f}")
except Exception as e:
    print(f"  失败: {type(e).__name__}: {e}")

print()
print("=" * 60)
print("补取完成")
