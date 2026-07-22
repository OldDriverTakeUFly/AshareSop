"""全市场主升浪潜伏期筛选 V2.0（补强理论）。

理论 V2.0 条件（基于50个历史主升浪样本回测）：

【硬条件——全部满足】
T1. 累计换手>80%（原100%略放宽，命中率82%→更强）
T2. 横盘振幅<45%（原30%放宽，历史中位39%）
T3. 启动前40日涨幅 -15%~+10%（平台整理态）
T4. 均线粘合度<6%（MA5-MA20离散度）

【加分条件——满足越多越好】
B1. 量超120均量≥2天（近3天中至少2天）
B2. 周线MACD的DIF在零轴附近（将金叉/刚金叉）
B3. 周线放量比>0.9（近4周/前8周，接近放量）
B4. 股东人数环比减少（筹码集中）
B5. 日线MACD的DIF在零轴附近

【排除条件——满足任一则剔除】
E1. ST/退市风险股
E2. 近60日涨幅>25%（已在主升浪高位，不是"前夜"）
E3. 近60日跌幅>20%（仍在主跌浪）
E4. 日均成交额<3000万（流动性不足）
E5. 上市不足1年（次新股，缺乏横盘基础）
"""
import os
from dotenv import load_dotenv
load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import tushare as ts
from stockhot.core.tushare_client_safe import safe_tushare_call

pro = ts.pro_api()

def fetch_daily(ts_code, days=350):
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=int(days*1.8))).strftime("%Y%m%d")
    df = ts.pro_bar(ts_code=ts_code, adj="qfq", start_date=start, end_date=end)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"trade_date":"date","vol":"volume"})
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.set_index("date").sort_index()
    return df[["open","high","low","close","volume"]].astype(float)


def screen_v2(ts_code, name=""):
    """补强版 V2.0 筛选"""
    df = fetch_daily(ts_code, days=350)
    if df.empty or len(df) < 130:
        return None

    close = df["close"]
    volume = df["volume"]
    launch_close = float(close.iloc[-1])

    # === 均线 ===
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma120_vol = volume.rolling(120).mean() if len(volume) >= 120 else volume.rolling(len(volume)).mean()

    ma5_v = float(ma5.iloc[-1]); ma10_v = float(ma10.iloc[-1])
    ma20_v = float(ma20.iloc[-1]); ma60_v = float(ma60.iloc[-1])

    # === 硬条件 ===

    # T1: 累计换手>80%
    db = safe_tushare_call("daily_basic", ts_code=ts_code, limit=1)
    turnover_120 = 0
    avg_amount = 0
    float_share = 0
    if db is not None and not db.empty:
        try:
            fs_val = db.iloc[0]["float_share"]
            if pd.notna(fs_val) and float(fs_val) > 0:
                float_share = float(fs_val)
            total_vol = float(volume.tail(120).sum())
            if float_share > 0:
                turnover_120 = total_vol * 100 / (float_share * 10000) * 100
            avg_amount = float(db.iloc[0].get("amount", 0)) * 10  # 千元→万元
        except:
            pass
    # 用成交量×收盘价估算日均成交额（万元）
    recent_amount = float((volume.tail(20) * close.tail(20)).mean()) / 10

    # T2: 横盘振幅（近90日）
    plat = close.tail(90)
    platform_range = (plat.max() - plat.min()) / plat.min() * 100

    # T3: 近40日涨幅
    if len(close) >= 40:
        gain_40d = float(close.iloc[-1] / close.iloc[-40] - 1) * 100
    else:
        gain_40d = 999

    # T4: 均线粘合度
    ma_convergence = abs(ma5_v - ma20_v) / ma20_v * 100 if ma20_v > 0 else 999

    # === 排除条件 ===
    gain_60d = float(close.iloc[-1] / close.iloc[-60] - 1) * 100 if len(close) >= 60 else 0

    listing_info = safe_tushare_call("stock_basic", ts_code=ts_code, fields="ts_code,list_date,name")
    list_days = 999
    if listing_info is not None and not listing_info.empty:
        try:
            ld = str(listing_info.iloc[0]["list_date"])
            list_date = datetime.strptime(ld, "%Y%m%d")
            list_days = (datetime.now() - list_date).days
        except:
            pass

    excluded = []
    if "ST" in name.upper() or "*ST" in name.upper():
        excluded.append("E1_ST")
    if gain_60d > 25:
        excluded.append(f"E2_高涨幅{gain_60d:.0f}%")
    if gain_60d < -20:
        excluded.append(f"E3_深跌{gain_60d:.0f}%")
    if recent_amount < 3000:
        excluded.append(f"E4_低流动性{recent_amount:.0f}万")
    if list_days < 365:
        excluded.append(f"E5_次新{list_days}天")

    # 硬条件
    hard = {
        "T1_turnover": turnover_120 > 80,
        "T2_platform": platform_range < 45,
        "T3_range": -15 <= gain_40d <= 10,
        "T4_ma_conv": ma_convergence < 6,
    }
    hard_pass = all(hard.values()) and not excluded

    # === 加分条件 ===
    vol_above = 0
    for i in range(-1, -4, -1):
        if abs(i) <= len(volume):
            v = float(volume.iloc[i])
            m = float(ma120_vol.iloc[i]) if not np.isnan(ma120_vol.iloc[i]) else 0
            if v > m:
                vol_above += 1

    # B2: 周线 MACD DIF 零轴附近
    weekly_close = close.resample("W").last().dropna()
    weekly_dif_pct = 999
    if len(weekly_close) >= 30:
        ema12_w = weekly_close.ewm(span=12, adjust=False).mean()
        ema26_w = weekly_close.ewm(span=26, adjust=False).mean()
        weekly_dif = float((ema12_w - ema26_w).iloc[-1])
        weekly_dif_pct = weekly_dif / launch_close * 100

    # B3: 周线放量比
    weekly_vol = volume.resample("W").sum()
    vol_ratio = 0
    if len(weekly_vol) >= 12:
        recent_4w = float(weekly_vol.tail(4).mean())
        prior_8w = float(weekly_vol.iloc[-12:-4].mean())
        vol_ratio = recent_4w / prior_8w if prior_8w > 0 else 0

    # B4: 股东人数
    hn = safe_tushare_call("stk_holdernumber", ts_code=ts_code, fields="ts_code,end_date,holder_num")
    holder_trend = 0
    if hn is not None and not hn.empty:
        hn = hn.dropna(subset=["holder_num"]).sort_values("end_date").tail(4)
        if len(hn) >= 2:
            latest = int(hn.iloc[-1]["holder_num"])
            prev = int(hn.iloc[-2]["holder_num"])
            holder_trend = (latest - prev) / prev * 100

    # B5: 日线 MACD DIF 零轴附近
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    daily_dif = float((ema12 - ema26).iloc[-1])
    daily_dif_pct = daily_dif / launch_close * 100

    bonus = {
        "B1_vol2d": vol_above >= 2,
        "B2_weekly_macd_near0": abs(weekly_dif_pct) < 3 if weekly_dif_pct != 999 else False,
        "B3_vol_ratio": vol_ratio > 0.9,
        "B4_holder_dec": holder_trend < 0,
        "B5_daily_macd_near0": abs(daily_dif_pct) < 2,
    }
    bonus_score = sum(bonus.values())

    if hard_pass:
        total_score = 40 + bonus_score * 12
    else:
        hard_passed = sum(hard.values())
        total_score = hard_passed * 8 + bonus_score * 5

    return {
        "ts_code": ts_code,
        "name": name,
        "close": launch_close,
        "hard_pass": hard_pass,
        "hard": hard,
        "excluded": excluded,
        "bonus": bonus,
        "bonus_score": bonus_score,
        "total_score": total_score,
        "details": {
            "turnover_120": turnover_120,
            "platform_range": platform_range,
            "gain_40d": gain_40d,
            "gain_60d": gain_60d,
            "ma_conv": ma_convergence,
            "vol_above": vol_above,
            "weekly_dif_pct": weekly_dif_pct if weekly_dif_pct != 999 else None,
            "vol_ratio": vol_ratio,
            "holder_trend": holder_trend,
            "daily_dif_pct": daily_dif_pct,
            "recent_amount": recent_amount,
            "list_days": list_days,
            "ma5": ma5_v, "ma10": ma10_v, "ma20": ma20_v, "ma60": ma60_v,
        }
    }


def get_stock_universe():
    """获取全市场股票池"""
    df = safe_tushare_call("stock_basic", list_status="L", fields="ts_code,symbol,name,industry,list_date")
    if df is None or df.empty:
        return []
    df = df[~df["name"].str.contains("ST", na=False)]
    df = df[~df["name"].str.contains("退", na=False)]
    df = df[df["ts_code"].str.startswith(("00","30","60","68"))]
    return df[["ts_code","name","industry"]].values.tolist()


if __name__ == "__main__":
    import sys
    import loguru
    loguru.logger.remove()
    loguru.logger.add(sys.stderr, level="ERROR")

    print("=" * 75)
    print("全市场主升浪潜伏期筛选 V2.0（补强理论）")
    print("=" * 75)

    universe = get_stock_universe()
    print(f"\n股票池：{len(universe)} 只\n")

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(universe)
    universe = universe[:limit]

    results = []
    passed = []
    near_miss = []

    for i, (ts_code, name, industry) in enumerate(universe):
        if (i+1) % 100 == 0:
            print(f"  进度: {i+1}/{len(universe)}  通过={len(passed)}  接近={len(near_miss)}", file=sys.stderr, flush=True)

        try:
            r = screen_v2(ts_code, name)
            if r is None:
                continue

            r["industry"] = industry
            results.append(r)

            if r["hard_pass"]:
                passed.append(r)
            elif r["bonus_score"] >= 3 and not r["excluded"]:
                near_miss.append(r)

        except Exception as e:
            continue

    passed.sort(key=lambda x: x["total_score"], reverse=True)
    near_miss.sort(key=lambda x: x["total_score"], reverse=True)

    print(f"\n{'='*75}")
    print(f"筛选完成：{len(results)} 只有效数据中")
    print(f"  ✅ 硬条件全通过：{len(passed)} 只")
    print(f"  ⚠ 接近通过(≥3加分)：{len(near_miss)} 只")
    print(f"{'='*75}")

    if passed:
        print(f"\n{'='*75}")
        print(f"★ 硬条件全通过标的（主升浪潜伏期候选）")
        print(f"{'='*75}")
        for i, r in enumerate(passed[:30], 1):
            d = r["details"]
            wdif = d['weekly_dif_pct']
            wdif_str = f"{wdif:.1f}%" if wdif is not None else "N/A"
            print(f"\n{i}. 【{r['name']}】{r['ts_code']}  {r['industry']}")
            print(f"   close={r['close']:.2f}  总分={r['total_score']}  加分={r['bonus_score']}/5")
            print(f"   T1换手={d['turnover_120']:.0f}%  T2振幅={d['platform_range']:.1f}%  "
                  f"T3_40d涨={d['gain_40d']:.1f}%  T4均线粘合={d['ma_conv']:.1f}%")
            print(f"   B1量超={d['vol_above']}/3  B2周DIF={wdif_str}  "
                  f"B3量比={d['vol_ratio']:.2f}x  B4股东={d['holder_trend']:.1f}%")
            bonus_labels = []
            for k, v in r["bonus"].items():
                if v:
                    bonus_labels.append(k.split("_")[1])
            print(f"   加分明细: {', '.join(bonus_labels) if bonus_labels else '无'}")

    if near_miss:
        print(f"\n{'='*75}")
        print(f"◇ 接近通过标的（加分≥3，观察池）")
        print(f"{'='*75}")
        for i, r in enumerate(near_miss[:20], 1):
            d = r["details"]
            print(f"  {i}. {r['name']:6s} {r['ts_code']}  {r['industry']}")
            print(f"     总分={r['total_score']}  加分={r['bonus_score']}/5  "
                  f"换手={d['turnover_120']:.0f}%  振幅={d['platform_range']:.0f}%  "
                  f"40d={d['gain_40d']:.1f}%  粘合={d['ma_conv']:.1f}%")
            failed = [k for k, v in r["hard"].items() if not v]
            if failed:
                print(f"     未过: {', '.join(failed)}")
