"""全市场箱体突破筛选器。

基于 48 个历史主升浪样本验证的有效突破定义：

【硬条件——判定"今日是否刚突破"】
D1. 今日收盘 > 过去60日最高价 × 1.01（突破箱体上沿至少1%）
D2. 今日涨幅 ≥ 3%
D3. 今日量比 ≥ 2.0x（vs 120日均量）

【箱体质量条件——确认是"有效箱体"】
B1. 过去60日箱体振幅 < 45%
B2. 箱体触顶次数 ≥ 3次（上沿经多次测试）
B3. 过去60日累计换手 > 80%

【加分条件——突破强度】
S1. 涨幅 ≥ 5%（满分25）
S2. 量比 ≥ 3.0x（满分25）
S3. 超上沿 ≥ 3%（满分25）
S4. 周线MACD零轴上方或金叉（满分25）

【突破时间窗口】
- 今日突破 = D 条件全部满足
- 近3日突破 = 过去3天中至少1天满足D条件（且当前未跌回箱体）
- 近5日突破 = 同上

输出：今日/近3日/近5日突破标的，按突破强度排序
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
from stockhot.core.tushare_client_safe import safe_tushare_call


def fetch_daily(ts_code, days=200):
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=int(days*1.8))).strftime("%Y%m%d")
    df = ts.pro_bar(ts_code=ts_code, adj="qfq", start_date=start, end_date=end)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"trade_date":"date","vol":"volume"})
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.set_index("date").sort_index()
    return df[["open","high","low","close","volume"]].astype(float)


def check_breakout(ts_code, name=""):
    """检查是否在近5日内发生有效突破"""
    df = fetch_daily(ts_code, days=200)
    if df.empty or len(df) < 130:
        return None

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # 120日均量
    ma120_vol = volume.rolling(120).mean() if len(volume) >= 120 else volume.rolling(len(volume)).mean()

    # 快速预筛：近5天有没有同时满足 涨幅≥3%+量比≥1.5 的日子，没有就跳过
    last5 = df.iloc[-5:]
    has_candidate = False
    for idx_pos in range(len(last5)):
        global_idx = len(df) - 5 + idx_pos
        if global_idx < 1:
            continue
        g = (float(close.iloc[global_idx]) - float(close.iloc[global_idx-1])) / float(close.iloc[global_idx-1]) * 100
        vr = float(volume.iloc[global_idx]) / float(ma120_vol.iloc[global_idx]) if not np.isnan(ma120_vol.iloc[global_idx]) and float(ma120_vol.iloc[global_idx]) > 0 else 0
        if g >= 3.0 and vr >= 1.5:
            has_candidate = True
            break
    if not has_candidate:
        return None

    # 检查最近5天是否出现有效突破
    breakouts = []

    # daily_basic 只调一次
    db = safe_tushare_call("daily_basic", ts_code=ts_code, limit=1)
    float_share = 0
    if db is not None and not db.empty:
        try:
            fs = db.iloc[0]["float_share"]
            if pd.notna(fs) and float(fs) > 0:
                float_share = float(fs)
        except:
            pass

    for d_offset in range(0, min(5, len(df))):
        check_idx = len(df) - 1 - d_offset  # 从今天往回数
        if check_idx < 61:
            break

        # 箱体 = check_idx 之前60日
        box_start = check_idx - 60
        box_high = float(high.iloc[box_start:check_idx].max())
        box_low = float(low.iloc[box_start:check_idx].min())
        box_range_pct = (box_high - box_low) / box_low * 100

        # 箱体触顶次数
        box_data = df.iloc[box_start:check_idx]
        near_top = int((box_data["high"] >= box_high * 0.97).sum())

        # 当日特征
        check_close = float(close.iloc[check_idx])
        prev_close = float(close.iloc[check_idx-1])
        check_gain = (check_close - prev_close) / prev_close * 100
        check_vol = float(volume.iloc[check_idx])
        check_vol_ratio = check_vol / float(ma120_vol.iloc[check_idx]) if not np.isnan(ma120_vol.iloc[check_idx]) and float(ma120_vol.iloc[check_idx]) > 0 else 0

        # 超上沿幅度
        vs_high = (check_close - box_high) / box_high * 100

        # D条件：突破判定（放宽量比到1.5x看更多候选）
        d1 = check_close > box_high * 1.01
        d2 = check_gain >= 3.0
        d3 = check_vol_ratio >= 1.5

        if d1 and d2 and d3:
            # 箱体质量
            b1 = box_range_pct < 45
            b2 = near_top >= 3

            # 累计换手
            turnover_60 = 0
            if float_share > 0:
                total_vol = float(volume.iloc[box_start:check_idx].sum())
                turnover_60 = total_vol * 100 / (float_share * 10000) * 100
            b3 = turnover_60 > 40

            # 突破强度评分
            s1 = min(25, max(0, (check_gain - 3) / (8 - 3) * 25))  # 3%~8%映射到0~25
            s2 = min(25, max(0, (check_vol_ratio - 2) / (4 - 2) * 25))  # 2x~4x映射到0~25
            s3 = min(25, max(0, (vs_high - 1) / (6 - 1) * 25))  # 1%~6%映射到0~25

            # 周线MACD
            weekly_close = close.iloc[:check_idx+1].resample("W").last().dropna()
            s4 = 0
            weekly_signal = "未确认"
            if len(weekly_close) >= 30:
                ema12 = weekly_close.ewm(span=12, adjust=False).mean()
                ema26 = weekly_close.ewm(span=26, adjust=False).mean()
                wdif = ema12 - ema26
                wdif_val = float(wdif.iloc[-1])
                wdif_prev = float(wdif.iloc[-2]) if len(wdif) >= 2 else 0
                if wdif_val > 0:
                    s4 = 25
                    weekly_signal = "零上"
                elif wdif_val > wdif_prev and abs(wdif_val / check_close * 100) < 3:
                    s4 = 20
                    weekly_signal = "零轴附近↑"

            strength = s1 + s2 + s3 + s4

            # 排除ST、低流动性
            if "ST" in name.upper():
                continue

            # 后续走势（如果是几天前突破的，看现在是否还在箱体上方）
            if d_offset > 0:
                post_data = df.iloc[check_idx+1:]
                if len(post_data) > 0:
                    current_close = float(close.iloc[-1])
                    # 如果已经跌回箱体内，跳过（假突破）
                    if current_close < box_high * 0.98:
                        continue

            box_quality = sum([b1, b2, b3])

            breakouts.append({
                "ts_code": ts_code,
                "name": name,
                "breakout_date": df.index[check_idx].strftime("%Y-%m-%d"),
                "days_ago": d_offset,
                "check_close": check_close,
                "check_gain": round(check_gain, 1),
                "check_vol_ratio": round(check_vol_ratio, 2),
                "vs_high": round(vs_high, 1),
                "box_range_pct": round(box_range_pct, 1),
                "near_top": near_top,
                "turnover_60": round(turnover_60, 0),
                "box_quality": box_quality,
                "b1": b1, "b2": b2, "b3": b3,
                "strength": round(strength, 1),
                "weekly_signal": weekly_signal,
                "s1": round(s1, 1), "s2": round(s2, 1), "s3": round(s3, 1), "s4": s4,
                "current_close": float(close.iloc[-1]),
            })

    return breakouts if breakouts else None


def get_stock_universe():
    df = safe_tushare_call("stock_basic", list_status="L", fields="ts_code,symbol,name,industry,list_date")
    if df is None or df.empty:
        return []
    df = df[~df["name"].str.contains("ST", na=False)]
    df = df[~df["name"].str.contains("退", na=False)]
    df = df[df["ts_code"].str.startswith(("00","30","60","68"))]
    return df[["ts_code","name","industry"]].values.tolist()


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    print("=" * 80)
    print("全市场箱体突破筛选器")
    print("=" * 80)

    universe = get_stock_universe()
    print(f"股票池: {len(universe)} 只\n")

    if limit > 0:
        universe = universe[:limit]

    all_breakouts = []

    for i, (ts_code, name, industry) in enumerate(universe):
        if (i+1) % 200 == 0:
            print(f"  进度: {i+1}/{len(universe)}  找到突破: {len(all_breakouts)}", file=sys.stderr, flush=True)

        try:
            bks = check_breakout(ts_code, name)
            if bks:
                for b in bks:
                    b["industry"] = industry
                    all_breakouts.append(b)
        except:
            continue

    # 排序
    all_breakouts.sort(key=lambda x: (x["strength"], -x["days_ago"]), reverse=True)

    # 分类
    today_bk = [b for b in all_breakouts if b["days_ago"] == 0]
    recent_3d = [b for b in all_breakouts if b["days_ago"] <= 2]
    recent_5d = all_breakouts

    print(f"\n{'═'*80}")
    print(f"筛选完成：找到 {len(all_breakouts)} 个有效突破信号")
    print(f"  今日突破: {len(today_bk)}")
    print(f"  近3日突破: {len(recent_3d)}")
    print(f"  近5日突破: {len(recent_5d)}")
    print(f"{'═'*80}")

    def print_list(title, data, top_n=20):
        if not data:
            print(f"\n◇ {title}: 无")
            return
        print(f"\n{'═'*80}")
        print(f"★ {title}（按突破强度排序，TOP{min(top_n, len(data))}）")
        print(f"{'═'*80}")
        print(f"\n{'排名':>3} {'名称':6s} {'代码':12s} {'行业':8s} {'突破日':12s} {'强度':>5} │"
              f"{'涨幅':>5} {'量比':>5} {'超上沿':>6} {'箱体振幅':>8} {'触顶':>4} {'换手':>5} {'周MACD'}")
        print("─" * 100)
        for i, b in enumerate(data[:top_n], 1):
            quality_mark = "★" if b["box_quality"] == 3 else ("◇" if b["box_quality"] == 2 else "○")
            print(f"{quality_mark}{i:2d} {b['name']:6s} {b['ts_code']:12s} {b['industry']:8s} "
                  f"{b['breakout_date']:12s} {b['strength']:5.1f} │"
                  f"{b['check_gain']:4.1f}% {b['check_vol_ratio']:4.1f}x {b['vs_high']:5.1f}% "
                  f"{b['box_range_pct']:7.1f}% {b['near_top']:4d} {b['turnover_60']:4.0f}% {b['weekly_signal']}")
        print(f"\n  (★=箱体高质量3/3  ◇=2/3  ○=1/3)")

    print_list("今日箱体突破", today_bk)
    print_list("近3日箱体突破", recent_3d)
    print_list("近5日箱体突破", recent_5d, top_n=30)

    # 行业分布
    if all_breakouts:
        print(f"\n{'═'*80}")
        print("突破标的行业分布")
        print(f"{'═'*80}")
        from collections import Counter
        ind_count = Counter(b["industry"] for b in all_breakouts)
        for ind, cnt in ind_count.most_common(10):
            print(f"  {ind:12s}: {cnt} 只")
