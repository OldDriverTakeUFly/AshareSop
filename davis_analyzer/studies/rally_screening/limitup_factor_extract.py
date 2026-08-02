"""涨停追高因子提取——从上帝视角156笔交易中提取涨停前夜特征。

核心逻辑：
1. 对每笔涨停交易，提取涨停前1-5天的技术特征（事前可观测）
2. 同时提取同期"非涨停但涨幅前10%"的标的作为对照组
3. 对比两组特征分布，找出显著差异 → 追高因子

提取的特征维度：
A. 涨停前5日动能：前5日累计涨幅、最大单日涨幅、连阳天数
B. 量价关系：前5日量比(vs 60日均量)、量价齐升天数
C. 均线位置：close vs MA5/10/20/60、均线多头排列
D. 技术指标：MACD柱状、RSI、KDJ
E. 筹码/波动：20日波动率、ATR、获利盘比例
F. 规模/流动性：市值、成交额、换手率
G. 缺口/形态：前5日跳空缺口、涨停次数(近期)
"""
import os
from dotenv import load_dotenv
load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

import sys
import loguru
loguru.logger.remove()

import numpy as np
import pandas as pd
from datetime import datetime

from davis_analyzer.studies.rally_screening.utils import (
    load_daily_by_date,
    fetch_daily_qfq_from_db,
)

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
TRADES_FILE = f"{OUTPUT_DIR}/god_view_trades.csv"


def get_daily_cache(trade_date):
    """获取某日全市场日线（从SQLite读）"""
    return load_daily_by_date(trade_date)


def get_individual_daily(ts_code, end_date, days=70):
    """获取单只股票的日线（前复权），涨停前N日（从SQLite读）"""
    df = fetch_daily_qfq_from_db(ts_code, end_date=str(end_date), days=days)
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.set_index("date").sort_index()
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def extract_features(ts_code, limit_date, name=""):
    """提取涨停前夜的技术特征。limit_date 是涨停日"""
    df = get_individual_daily(ts_code, limit_date, days=70)
    if df.empty or len(df) < 30:
        return None

    # 找到涨停日在 df 中的位置
    limit_dt = pd.to_datetime(limit_date, format="%Y%m%d")
    if limit_dt not in df.index:
        # 可能是停牌或数据缺失
        return None
    idx = df.index.get_loc(limit_dt)

    # 涨停前 5 天的数据
    pre5_start = max(0, idx - 5)
    pre5 = df.iloc[pre5_start:idx]  # 不含涨停日
    pre1 = df.iloc[max(0, idx-1):idx]  # 涨停前1天
    if len(pre5) < 3:
        return None

    close = df["close"].iloc[:idx+1]  # 含涨停日
    volume = df["volume"].iloc[:idx+1]
    high = df["high"].iloc[:idx+1]
    low = df["low"].iloc[:idx+1]

    # === A. 涨停前5日动能 ===
    # 前5日累计涨幅
    if len(pre5) >= 2:
        pre5_return = (pre5["close"].iloc[-1] / pre5["close"].iloc[0] - 1) * 100
    else:
        pre5_return = 0
    # 前5日最大单日涨幅
    pre5_daily_returns = pre5["close"].pct_change().dropna() * 100
    pre5_max_daily = pre5_daily_returns.max() if len(pre5_daily_returns) > 0 else 0
    # 连阳天数（涨停前最后连续上涨的天数）
    consecutive_up = 0
    for i in range(len(pre5)-1, -1, -1):
        if i > 0 and pre5["close"].iloc[i] > pre5["close"].iloc[i-1]:
            consecutive_up += 1
        elif i == 0:
            break
        else:
            break

    # === B. 量价关系 ===
    # 前5日量比（vs 60日均量）
    vol_60 = volume.rolling(60).mean() if len(volume) >= 60 else volume.rolling(len(volume)).mean()
    pre5_vol_ratio = float(pre5["volume"].mean() / vol_60.iloc[idx-1]) if not np.isnan(vol_60.iloc[idx-1]) and vol_60.iloc[idx-1] > 0 else 1
    # 量价齐升天数（前5日中量增+价涨的天数）
    vol_price_up = 0
    for i in range(1, len(pre5)):
        if pre5["volume"].iloc[i] > pre5["volume"].iloc[i-1] and pre5["close"].iloc[i] > pre5["close"].iloc[i-1]:
            vol_price_up += 1

    # === C. 均线位置（涨停前1天）===
    if len(close) >= 60:
        ma5 = close.rolling(5).mean().iloc[idx-1]
        ma10 = close.rolling(10).mean().iloc[idx-1]
        ma20 = close.rolling(20).mean().iloc[idx-1]
        ma60 = close.rolling(60).mean().iloc[idx-1]
    else:
        ma5 = close.rolling(5).mean().iloc[idx-1]
        ma10 = close.rolling(10).mean().iloc[idx-1] if len(close) >= 10 else ma5
        ma20 = close.rolling(min(20, len(close))).mean().iloc[idx-1]
        ma60 = ma20

    pre1_close = float(close.iloc[idx-1])
    above_ma5 = pre1_close > ma5
    above_ma20 = pre1_close > ma20
    above_ma60 = pre1_close > ma60
    # 均线多头排列
    bullish_ma = (ma5 > ma10 > ma20) if not any(np.isnan([ma5, ma10, ma20])) else False
    # close vs ma20 偏离度
    dev_ma20 = (pre1_close - ma20) / ma20 * 100 if ma20 > 0 else 0

    # === D. 技术指标 ===
    # MACD（前1天）
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_hist = (dif - dea) * 2
    macd_positive = float(macd_hist.iloc[idx-1]) > 0
    macd_golden_cross = float(dif.iloc[idx-1]) > float(dea.iloc[idx-1]) and float(dif.iloc[idx-2] if idx >= 2 else 0) <= float(dea.iloc[idx-2] if idx >= 2 else 0)

    # RSI(14)
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi_val = float(rsi.iloc[idx-1]) if not np.isnan(rsi.iloc[idx-1]) else 50

    # KDJ
    low_9 = low.rolling(9).min()
    high_9 = high.rolling(9).max()
    rsv = (close - low_9) / (high_9 - low_9).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1/3, adjust=False).mean()
    d = k.ewm(alpha=1/3, adjust=False).mean()
    j = 3 * k - 2 * d
    k_val = float(k.iloc[idx-1]) if not np.isnan(k.iloc[idx-1]) else 50
    d_val = float(d.iloc[idx-1]) if not np.isnan(d.iloc[idx-1]) else 50

    # === E. 筹码/波动 ===
    # 20日波动率
    ret_20 = close.pct_change().rolling(20).std() * np.sqrt(252)
    vol_20 = float(ret_20.iloc[idx-1]) * 100 if not np.isnan(ret_20.iloc[idx-1]) else 30
    # 获利盘比例（close vs 近90日所有价格）
    hist_90 = close.iloc[max(0, idx-90):idx]
    profit_ratio = (hist_90 <= pre1_close).sum() / len(hist_90) * 100 if len(hist_90) > 0 else 50

    # === F. 规模/流动性 ===
    # 涨停前1天成交额（用当日 daily cache）
    daily_df = get_daily_cache(limit_date)
    if daily_df is not None and not daily_df.empty:
        # 取前一天的
        pre_limit_data = daily_df[daily_df["ts_code"] == ts_code]
        if not pre_limit_data.empty:
            pre_amount = float(pre_limit_data.iloc[0].get("amount", 0)) * 1000  # 千元→元
            pre_turnover = float(pre_limit_data.iloc[0].get("vol", 0))
        else:
            pre_amount = 0
            pre_turnover = 0
    else:
        pre_amount = 0
        pre_turnover = 0

    # === G. 缺口/形态 ===
    # 近5日跳空缺口次数
    gaps_5d = 0
    for i in range(1, len(pre5)):
        if pre5["low"].iloc[i] > pre5["high"].iloc[i-1]:
            gaps_5d += 1
    # 近10日涨停次数
    limit_up_count_10d = 0
    if len(close) >= 11:
        for i in range(max(1, idx-10), idx):
            if close.iloc[i] / close.iloc[i-1] - 1 >= 0.099:
                limit_up_count_10d += 1

    # === 涨停当日特征（事后，用于分类不用于因子）===
    limit_day_return = float(close.iloc[idx] / close.iloc[idx-1] - 1) * 100
    # 涨停封板时间（用 amount/vol 代理）— 暂无

    return {
        "ts_code": ts_code, "name": name, "limit_date": limit_date,
        "limit_day_return": limit_day_return,
        # A 动能
        "pre5_return": pre5_return,
        "pre5_max_daily": pre5_max_daily,
        "consecutive_up": consecutive_up,
        # B 量价
        "pre5_vol_ratio": pre5_vol_ratio,
        "vol_price_up_days": vol_price_up,
        # C 均线
        "above_ma5": above_ma5,
        "above_ma20": above_ma20,
        "above_ma60": above_ma60,
        "bullish_ma": bullish_ma,
        "dev_ma20": dev_ma20,
        # D 技术指标
        "macd_positive": macd_positive,
        "macd_golden_cross": macd_golden_cross,
        "rsi": rsi_val,
        "k_val": k_val,
        "d_val": d_val,
        # E 筹码
        "vol_20d": vol_20,
        "profit_ratio": profit_ratio,
        # F 规模
        "pre_amount_yi": pre_amount / 1e8,
        # G 形态
        "gaps_5d": gaps_5d,
        "limit_up_10d": limit_up_count_10d,
    }


def get_control_group(limit_date, ts_code_exclude, n=20):
    """获取对照组：同日涨幅前10%-30%但未涨停的标的"""
    df = get_daily_cache(limit_date)
    if df.empty:
        return []
    # 涨幅 5%~9.9% 的（有表现但未涨停）
    candidates = df[(df["pct_chg"] >= 5) & (df["pct_chg"] < 9.9)].copy()
    candidates = candidates[candidates["ts_code"] != ts_code_exclude]
    candidates = candidates[candidates["ts_code"].str.startswith(("00", "30", "60", "68"))]
    if len(candidates) > n:
        candidates = candidates.sample(n, random_state=42)
    return candidates["ts_code"].tolist()


def main():
    print("=" * 95)
    print("涨停追高因子提取——从上帝视角156笔涨停交易提取涨停前夜特征")
    print("=" * 95)

    trades = pd.read_csv(TRADES_FILE)
    # 只看标准涨停板（排除新股上市首日的极端涨幅）
    limit_trades = trades[(trades["pct_chg_day"] >= 19.9) & (trades["pct_chg_day"] <= 20.1)].copy()
    print(f"\n标准涨停板交易: {len(limit_trades)} 笔（排除新股首日极端涨幅）")

    # === 提取涨停组特征 ===
    print(f"\n提取涨停组特征...")
    limit_features = []
    for _, trade in limit_trades.iterrows():
        f = extract_features(trade["ts_code"], trade["date"], trade["name"])
        if f:
            f["group"] = "limit_up"
            limit_features.append(f)

    print(f"  成功提取: {len(limit_features)} / {len(limit_trades)}")

    # === 提取对照组特征 ===
    print(f"\n提取对照组特征（当日涨幅5%-9.9%但未涨停）...")
    control_features = []
    # 从涨停交易日中取10个日期，每个取对照组
    sample_dates = limit_trades["date"].unique()[:30]
    for d in sample_dates:
        exclude_code = limit_trades[limit_trades["date"] == d]["ts_code"].iloc[0]
        controls = get_control_group(d, exclude_code, n=3)
        for code in controls:
            f = extract_features(code, d, "")
            if f:
                f["group"] = "control"
                control_features.append(f)

    print(f"  对照组样本: {len(control_features)}")

    all_features = limit_features + control_features
    df = pd.DataFrame(all_features)

    # === 特征对比 ===
    print(f"\n{'='*95}")
    print("涨停组 vs 对照组特征对比（寻找显著差异）")
    print(f"{'='*95}")

    limit_df = df[df["group"] == "limit_up"]
    control_df = df[df["group"] == "control"]

    numeric_features = ["pre5_return", "pre5_max_daily", "consecutive_up", "pre5_vol_ratio",
                        "vol_price_up_days", "dev_ma20", "rsi", "k_val", "d_val",
                        "vol_20d", "profit_ratio", "pre_amount_yi", "gaps_5d", "limit_up_10d"]
    bool_features = ["above_ma5", "above_ma20", "above_ma60", "bullish_ma",
                     "macd_positive", "macd_golden_cross"]

    print(f"\n{'特征':<22} {'涨停组均值':>12} {'对照组均值':>12} {'差异':>10} {'显著性':>8}")
    print("-" * 75)

    significant_factors = []

    for feat in numeric_features:
        if feat not in df.columns:
            continue
        l_val = limit_df[feat].dropna()
        c_val = control_df[feat].dropna()
        if len(l_val) == 0 or len(c_val) == 0:
            continue
        l_mean = l_val.mean()
        c_mean = c_val.mean()
        diff = l_mean - c_mean
        # 简单 t 检验近似
        l_std = l_val.std()
        c_std = c_val.std()
        pooled_std = np.sqrt((l_std**2 + c_std**2) / 2) if l_std + c_std > 0 else 1
        cohen_d = diff / pooled_std if pooled_std > 0 else 0
        sig = "★★★" if abs(cohen_d) >= 0.8 else ("★★" if abs(cohen_d) >= 0.5 else ("★" if abs(cohen_d) >= 0.3 else ""))
        print(f"{feat:<22} {l_mean:>12.2f} {c_mean:>12.2f} {diff:>+10.2f} {sig:>8} d={cohen_d:.2f}")
        significant_factors.append((feat, l_mean, c_mean, cohen_d, "numeric"))

    print()
    for feat in bool_features:
        if feat not in df.columns:
            continue
        l_rate = limit_df[feat].mean() * 100
        c_rate = control_df[feat].mean() * 100
        diff = l_rate - c_rate
        sig = "★★★" if abs(diff) >= 30 else ("★★" if abs(diff) >= 20 else ("★" if abs(diff) >= 10 else ""))
        print(f"{feat:<22} {l_rate:>11.1f}% {c_rate:>11.1f}% {diff:>+9.1f}% {sig:>8}")
        significant_factors.append((feat, l_rate, c_rate, diff, "bool"))

    # === 最具区分力的因子排序 ===
    print(f"\n{'='*95}")
    print("★ 最具区分力的追高因子排序（按效应量 Cohen's d 降序）")
    print(f"{'='*95}")

    sig_numeric = [(f, lm, cm, d) for f, lm, cm, d, t in significant_factors if t == "numeric"]
    sig_numeric.sort(key=lambda x: abs(x[3]), reverse=True)

    print(f"\n{'排名':>3} {'因子':<22} {'涨停组':>10} {'对照组':>10} {'Cohen d':>10} {'解读'}")
    print("-" * 90)
    for i, (feat, lm, cm, d) in enumerate(sig_numeric[:12], 1):
        if d > 0:
            interp = f"涨停组更高 → 追高因子正向"
        else:
            interp = f"涨停组更低 → 追高因子负向（反向信号）"
        print(f"{i:>3} {feat:<22} {lm:>10.2f} {cm:>10.2f} {d:>+10.2f}  {interp}")

    # 保存特征数据
    df.to_csv(f"{CACHE_DIR}/limit_up_features.csv", index=False)
    print(f"\n特征数据已保存: {CACHE_DIR}/limit_up_features.csv")
    print(f"涨停组: {len(limit_df)}  对照组: {len(control_df)}")


if __name__ == "__main__":
    main()
