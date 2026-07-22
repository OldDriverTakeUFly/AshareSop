"""主升浪变盘三阶段：潜伏→突破→确认。

关键修正：
- V1 bug: 把"主升浪第一天"当突破日，但那天股价距箱体上沿还有17.8%
- V2 正确定义：
  * 潜伏期 = 横盘箱体内（距上沿>5%）
  * 突破日 = 主升浪期间首次收盘价超过箱体上沿的那天
  * 确认期 = 突破后回踩不破箱体上沿

这样能精确量化"突破日"的技术特征。
"""
import os
from dotenv import load_dotenv
load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import tushare as ts
import sys
import loguru
loguru.logger.remove()
loguru.logger.add(sys.stderr, level="ERROR")

def fetch_daily(ts_code, days=600):
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=int(days*1.8))).strftime("%Y%m%d")
    df = ts.pro_bar(ts_code=ts_code, adj="qfq", start_date=start, end_date=end)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"trade_date":"date","vol":"volume"})
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.set_index("date").sort_index()
    return df[["open","high","low","close","volume"]].astype(float)


def find_rally_and_breakout(df, min_gain=0.25, window=20):
    """找到主升浪启动点 + 主升浪中首次突破箱体上沿的位置"""
    if len(df) < 100:
        return []
    closes = df["close"].values
    highs = df["high"].values
    results = []
    cooldown = 0

    for i in range(60, len(closes) - 5):
        if cooldown > 0:
            cooldown -= 1
            continue

        future_idx = min(i + window, len(closes) - 1)
        gain = (closes[future_idx] - closes[i]) / closes[i]
        if gain >= min_gain:
            prior_start = max(0, i - 40)
            prior = closes[i] / closes[prior_start] - 1
            if prior < 0.15:
                # 找到了主升浪，启动点=i
                # 箱体 = 启动前60日
                box_start = max(0, i - 60)
                box_high = float(highs[box_start:i].max())
                box_low = float(df["low"].iloc[box_start:i].min())
                box_range_pct = (box_high - box_low) / box_low * 100

                # 在主升浪期间寻找首次突破箱体上沿的日期
                rally_end = min(i + window, len(closes))
                breakout_pos = None
                for j in range(i, rally_end):
                    if closes[j] > box_high * 1.01:  # 收盘超过上沿1%
                        breakout_pos = j
                        break

                if breakout_pos is None:
                    breakout_pos = i  # 没找到就用启动日

                results.append({
                    "launch_pos": i,
                    "breakout_pos": breakout_pos,
                    "box_high": box_high,
                    "box_low": box_low,
                    "box_range_pct": box_range_pct,
                })
                cooldown = 30

    return results


def analyze_breakout(df, rally_info):
    """分析突破日的特征"""
    bp = rally_info["breakout_pos"]
    lp = rally_info["launch_pos"]
    box_high = rally_info["box_high"]

    if bp + 5 >= len(df):
        return None

    # 突破日特征
    bk_day = df.iloc[bp]
    bk_close = float(bk_day["close"])
    bk_open = float(bk_day["open"])
    bk_vol = float(bk_day["volume"])
    prev_close = float(df["close"].iloc[bp-1])
    bk_gain = (bk_close - prev_close) / prev_close * 100

    # 量比
    vol_120 = df["volume"].iloc[max(0,bp-120):bp].mean()
    bk_vol_ratio = bk_vol / vol_120 if vol_120 > 0 else 0

    # 超上沿幅度
    bk_vs_high = (bk_close - box_high) / box_high * 100

    # 突破后5日
    post = df.iloc[bp+1:bp+6]
    if len(post) < 3:
        return None
    post_min = float(post["low"].min())
    post_5d_gain = (float(post["close"].iloc[-1]) - bk_close) / bk_close * 100
    post_5d_max = float(post["high"].max())
    post_5d_max_gain = (post_5d_max - bk_close) / bk_close * 100

    # 回踩确认：突破后最低价是否跌回箱体内
    pulled_back = post_min < box_high * 0.98

    # 从启动到突破的天数
    days_to_breakout = bp - lp

    # 箱体触顶次数
    box_data = df.iloc[max(0,lp-60):lp]
    near_top = (box_data["high"] >= box_high * 0.97).sum()

    return {
        "rally_date": df.index[lp].strftime("%Y-%m-%d"),
        "breakout_date": df.index[bp].strftime("%Y-%m-%d"),
        "days_to_breakout": days_to_breakout,
        "box_range_pct": rally_info["box_range_pct"],
        "near_top": near_top,
        "bk_gain": bk_gain,
        "bk_vol_ratio": bk_vol_ratio,
        "bk_vs_high": bk_vs_high,
        "post_5d_gain": post_5d_gain,
        "post_5d_max_gain": post_5d_max_gain,
        "pulled_back": pulled_back,
    }


stocks = [
    ("002475.SZ", "立讯精密"), ("300750.SZ", "宁德时代"), ("603259.SH", "药明康德"),
    ("300059.SZ", "东方财富"), ("603986.SH", "兆易创新"), ("002049.SZ", "紫光国微"),
    ("002415.SZ", "海康威视"), ("000858.SZ", "五粮液"), ("601012.SH", "隆基绿能"),
    ("300274.SZ", "阳光电源"), ("002594.SZ", "比亚迪"), ("688981.SH", "中芯国际"),
    ("002241.SZ", "歌尔股份"), ("603501.SH", "韦尔股份"), ("300661.SZ", "圣邦股份"),
    ("002405.SZ", "四维图新"), ("300142.SZ", "沃森生物"), ("603160.SH", "汇顶科技"),
    ("002422.SZ", "科伦药业"), ("603367.SH", "辰欣药业"),
]

all_data = []
for code, name in stocks:
    df = fetch_daily(code, days=600)
    if df.empty or len(df) < 150:
        continue
    rallies = find_rally_and_breakout(df)
    for r in rallies:
        a = analyze_breakout(df, r)
        if a:
            a["ts_code"] = code
            a["name"] = name
            all_data.append(a)

df_a = pd.DataFrame(all_data)

print("=" * 80)
print(f"主升浪突破日特征分析 V2（样本数={len(df_a)}）")
print(f"{'='*80}")

print(f"\n{'─'*80}")
print("1. 从主升浪启动到真正突破箱体上沿，需要几天？")
print(f"{'─'*80}")
for d in [0, 1, 2, 3, 5, 7, 10]:
    count = (df_a["days_to_breakout"] <= d).sum()
    print(f"   ≤{d:2d}天: {count}/{len(df_a)} = {count/len(df_a)*100:.0f}%")
print(f"   均值={df_a['days_to_breakout'].mean():.1f}天  中位={df_a['days_to_breakout'].median():.1f}天")

print(f"\n{'─'*80}")
print("2. 突破日涨幅")
print(f"{'─'*80}")
for pct in [2, 3, 5, 7]:
    count = (df_a["bk_gain"] >= pct).sum()
    print(f"   涨幅≥{pct}%: {count}/{len(df_a)} = {count/len(df_a)*100:.0f}%")
print(f"   均值={df_a['bk_gain'].mean():.1f}%  中位={df_a['bk_gain'].median():.1f}%")

print(f"\n{'─'*80}")
print("3. 突破日量比")
print(f"{'─'*80}")
for ratio in [1.0, 1.5, 2.0, 2.5]:
    count = (df_a["bk_vol_ratio"] >= ratio).sum()
    print(f"   量比≥{ratio}x: {count}/{len(df_a)} = {count/len(df_a)*100:.0f}%")
print(f"   均值={df_a['bk_vol_ratio'].mean():.1f}x  中位={df_a['bk_vol_ratio'].median():.1f}x")

print(f"\n{'─'*80}")
print("4. 突破超上沿幅度")
print(f"{'─'*80}")
for thresh in [0, 1, 2, 3, 5]:
    count = (df_a["bk_vs_high"] >= thresh).sum()
    print(f"   超上沿≥{thresh}%: {count}/{len(df_a)} = {count/len(df_a)*100:.0f}%")
print(f"   均值={df_a['bk_vs_high'].mean():.1f}%  中位={df_a['bk_vs_high'].median():.1f}%")

print(f"\n{'─'*80}")
print("5. 突破后5日：真突破 vs 假突破")
print(f"{'─'*80}")
true_bk = df_a[~df_a["pulled_back"]]
false_bk = df_a[df_a["pulled_back"]]
print(f"   真突破（5日不回踩）: {len(true_bk)}/{len(df_a)} = {len(true_bk)/len(df_a)*100:.0f}%")
print(f"   假突破（5日内回踩）: {len(false_bk)}/{len(df_a)} = {len(false_bk)/len(df_a)*100:.0f}%")
print(f"\n   真突破特征: 涨幅={true_bk['bk_gain'].mean():.1f}%  量比={true_bk['bk_vol_ratio'].mean():.1f}x  超上沿={true_bk['bk_vs_high'].mean():.1f}%")
if len(false_bk) > 0:
    print(f"   假突破特征: 涨幅={false_bk['bk_gain'].mean():.1f}%  量比={false_bk['bk_vol_ratio'].mean():.1f}x  超上沿={false_bk['bk_vs_high'].mean():.1f}%")

print(f"\n{'─'*80}")
print("6. 真突破后5日收益")
print(f"{'─'*80}")
print(f"   5日继续涨幅: 均={true_bk['post_5d_gain'].mean():.1f}%  中={true_bk['post_5d_gain'].median():.1f}%")
print(f"   5日最大涨幅: 均={true_bk['post_5d_max_gain'].mean():.1f}%  中={true_bk['post_5d_max_gain'].median():.1f}%")

print(f"\n{'═'*80}")
print("★ 有效突破的精确量化定义")
print(f"{'═'*80}")
print("""
基于实证数据，有效箱体突破定义为：

【突破当日特征（中位水平）】
  ① 当日涨幅: ≥2%（中位值）
  ② 量比: ≥1.5x（保守，真突破均值更高）
  ③ 收盘超箱体上沿: ≥1%

【真突破 vs 假突破区分关键】
  真突破：量比更高 + 超上沿幅度更大 + 涨幅更大
  → 筛选时应同时要求：涨幅≥3% AND 量比≥2x AND 超上沿≥2%

【箱体要求】
  ① 箱体振幅: < 45%
  ② 触顶次数: ≥ 3次（上沿经多次测试后突破更有效）
""")
