"""QVIX 可信度验证（3 个角度）。

验证角度：
1. 与上证 50ETF 涨跌幅的相关性（恐慌指数应在暴跌时飙升）
2. 与上证 RV 的领先滞后关系（IV 应领先 RV，前瞻性验证）
3. 与学术文献/历史报道的 iVIX 关键点位对照（2015 股灾 60+、2018 停发前 15-20）

输出：qvix_validation_report.md
"""
import os
from dotenv import load_dotenv
load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

import numpy as np
import pandas as pd
import akshare as ak
from datetime import date, timedelta
from stockhot.core.tushare_client_safe import safe_tushare_call
from stockhot.volatility.analyzer import realized_vol

print("=" * 70)
print("QVIX 可信度验证")
print("=" * 70)

# 1. 取 QVIX 全历史
print("\n[1] 取 QVIX 全历史...")
qvix = ak.index_option_50etf_qvix()
qvix["date"] = pd.to_datetime(qvix["date"])
qvix = qvix.set_index("date").sort_index()
print(f"QVIX: {len(qvix)} 行, {qvix.index.min().date()} ~ {qvix.index.max().date()}")

# 2. 取上证 50ETF（510050.SH）日线用于相关性验证
print("\n[2] 取上证 50ETF 日线...")
etf = safe_tushare_call("fund_daily", ts_code="510050.SH",
                        start_date="20150201", end_date="20260708")
if etf is not None and not etf.empty:
    etf = etf.rename(columns={"trade_date": "date"})
    etf["date"] = pd.to_datetime(etf["date"], format="%Y%m%d")
    etf = etf.set_index("date").sort_index()
    etf["pct_chg"] = etf["close"].pct_change() * 100
    print(f"50ETF: {len(etf)} 行, {etf.index.min().date()} ~ {etf.index.max().date()}")

# 3. 取上证指数日线算 RV
print("\n[3] 取上证指数日线算 RV...")
idx = safe_tushare_call("index_daily", ts_code="000001.SH",
                        start_date="20150201", end_date="20260708")
if idx is not None and not idx.empty:
    idx = idx.rename(columns={"trade_date": "date"})
    idx["date"] = pd.to_datetime(idx["date"], format="%Y%m%d")
    idx = idx.set_index("date").sort_index()
    idx["rv20"] = realized_vol(idx["close"], window=20)
    print(f"上证 RV: {len(idx)} 行")

# ── 验证角度 1：QVIX vs 50ETF 涨跌幅相关性 ──
print("\n" + "=" * 70)
print("[验证 1] QVIX vs 50ETF 日涨跌幅相关性")
print("=" * 70)
merged = qvix[["close"]].rename(columns={"close": "qvix"}).join(
    etf[["pct_chg"]], how="inner"
).dropna()

corr_all = merged["qvix"].corr(merged["pct_chg"])
# 暴跌日（<-2%）vs 暴涨日（>+2%）
down_days = merged[merged["pct_chg"] < -2]
up_days = merged[merged["pct_chg"] > 2]
flat_days = merged[abs(merged["pct_chg"]) <= 1]

print(f"全样本相关系数: {corr_all:+.3f}")
print(f"暴跌日（<-2%）QVIX 均值: {down_days['qvix'].mean():.2f}（n={len(down_days)}）")
print(f"暴涨日（>+2%）QVIX 均值: {up_days['qvix'].mean():.2f}（n={len(up_days)}）")
print(f"平静日（|x|<=1%）QVIX 均值: {flat_days['qvix'].mean():.2f}（n={len(flat_days)}）")
print(f"暴跌日 QVIT 变化（当日 vs 前5日均值）: {((down_days['qvix'] / down_days['qvix'].rolling(5).mean().shift(1) - 1)*100).mean():+.1f}%")
print(f"暴涨日 QVIX 变化: {((up_days['qvix'] / up_days['qvix'].rolling(5).mean().shift(1) - 1)*100).mean():+.1f}%")
print()
print("判定标准：")
print("  ✅ 暴跌日 QVIX 应显著高于平静日（恐慌飙升）")
print("  ✅ 暴跌日 QVIX 变化应为正（飙升），暴涨日变化应较小或为负（非对称性）")

# ── 验证角度 2：QVIX（IV）vs RV 领先滞后 ──
print("\n" + "=" * 70)
print("[验证 2] QVIX(IV) vs 上证 RV20 领先滞后关系（前瞻性验证）")
print("=" * 70)
merged2 = qvix[["close"]].rename(columns={"close": "qvix"}).join(
    idx[["rv20"]], how="inner"
).dropna()

# QVIX 与未来 1/5/10/20 日 RV 的相关系数（前瞻性：IV 预测未来 RV）
for lead in [0, 1, 5, 10, 20]:
    future_rv = merged2["rv20"].shift(-lead)
    corr = merged2["qvix"].corr(future_rv)
    label = "同步" if lead == 0 else f"领先 {lead} 日"
    print(f"  corr(QVIX_t, RV_t+{lead}): {corr:+.3f}  ({label})")

print()
print("判定标准：")
print("  ✅ IV 应与未来 RV 正相关（前瞻性：IV 高 → 未来波动确实大）")
print("  ✅ 领先相关系数应 > 同步相关系数（IV 预测能力 > 当下反映）")

# ── 验证角度 3：关键事件点位对照 ──
print("\n" + "=" * 70)
print("[验证 3] 关键事件 QVIX 点位 vs 学术文献/报道的 iVIX 点位")
print("=" * 70)

events = [
    ("2015-06-29", "股灾 1.0（千股跌停）", "iVIX 报道 60+", None),
    ("2015-08-24", "股灾 2.0（黑色星期一）", "iVIX 报道 55-60", None),
    ("2018-06-29", "iVIX 停发前最后交易日", "iVIX 约 15-18（当时低波）", None),
    ("2024-09-30", "9·24 政策暴涨", "QVIX 应飙升（政策驱动高波）", None),
    ("2024-10-08", "9·24 后高开低走", "QVIX 应持续高位", None),
    ("2026-07-02", "本轮科技股暴跌", "QVIX 应升高", None),
]
print(f"{'日期':<14}{'事件':<28}{'文献/报道 iVIX':<24}{'QVIX 实测':<10}")
print("-" * 80)
for d_str, event, ref, _ in events:
    d = pd.Timestamp(d_str)
    # 找最近一个交易日
    mask = abs((qvix.index - d).days) <= 3
    nearby = qvix[mask]
    if not nearby.empty:
        qvix_val = nearby["close"].iloc[0]
        actual_date = nearby.index[0].date()
        print(f"{d_str:<14}{event:<28}{ref:<24}{qvix_val:>6.2f}（{actual_date}）")
    else:
        print(f"{d_str:<14}{event:<28}{ref:<24}无数据")

print()
print("判定标准：")
print("  ✅ QVIX 在 2015 股灾期间应达 50-65（与 iVIX 报道吻合）")
print("  ✅ QVIX 在 2018 停发前应在 15-20（当时低波，与报道吻合）")

# ── 总结 ──
print("\n" + "=" * 70)
print("总结判定")
print("=" * 70)
checks = []
# 检查 1: 暴跌日 QVIX > 平静日
if down_days["qvix"].mean() > flat_days["qvix"].mean() * 1.15:
    checks.append(("暴跌日 QVIX 显著高于平静日", "✅"))
else:
    checks.append(("暴跌日 QVIX 显著高于平静日", "❌"))
# 检查 2: IV 领先 RV
corr_lead5 = merged2["qvix"].corr(merged2["rv20"].shift(-5))
corr_sync = merged2["qvix"].corr(merged2["rv20"])
if corr_lead5 > 0.3:
    checks.append((f"IV 领先 5 日 RV 相关系数 {corr_lead5:+.2f} > 0.3", "✅"))
else:
    checks.append((f"IV 领先 5 日 RV 相关系数 {corr_lead5:+.2f} > 0.3", "❌"))
# 检查 3: 2015 股灾 QVIX > 50
crash_qvix = qvix.loc["2015-06-01":"2015-09-30", "close"]
if crash_qvix.max() > 50:
    checks.append((f"2015 股灾 QVIX 最高 {crash_qvix.max():.1f} > 50（与 iVIX 吻合）", "✅"))
else:
    checks.append((f"2015 股灾 QVIX 最高 {crash_qvix.max():.1f} > 50", "❌"))

for desc, mark in checks:
    print(f"  {mark} {desc}")

passed = sum(1 for _, m in checks if "✅" in m)
print(f"\n通过 {passed}/{len(checks)} 项验证")
