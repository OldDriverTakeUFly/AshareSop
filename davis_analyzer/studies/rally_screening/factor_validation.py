"""追高因子全市场命中率验证。

核心问题：评分>70的标的，次日涨停命中率是多少？vs 全市场平均命中率？

方法：
1. 用已缓存的157天全市场daily数据
2. 对每个交易日，计算所有标的的6因子评分
3. 统计次日涨停情况（次日pct_chg >= 9.9%）
4. 对比：高分(>70)命中率 vs 中分(50-70) vs 低分(<50) vs 全市场平均

简化：
- 用未复权日线计算因子（批量计算时效率高）
- KDJ/RSI/MACD 用简化版（直接在daily大表上算）
- 涨停判定：pct_chg >= 9.9%（主板）或 >= 19.9%（创业板/科创板）
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
    get_name_map as _get_name_map,
    get_trade_dates as _get_trade_dates,
    load_daily_batch,
)

START_DATE = "20251201"
END_DATE = "20260725"


def get_trade_dates():
    return _get_trade_dates(START_DATE, END_DATE)


def load_all_daily():
    """加载所有交易日daily数据为一个DataFrame（从SQLite批量读）"""
    dates = get_trade_dates()
    return load_daily_batch(dates)


def get_name_map():
    return _get_name_map()


def compute_factors(big_df, name_map):
    """在全市场大表上按 ts_code 分组计算因子。

    需要 KDJ/RSI/波动率/MA20偏离/前5日涨幅。
    """
    print("  按ts_code分组计算因子...")

    big_df = big_df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    big_df["name"] = big_df["ts_code"].map(name_map).fillna("?")

    # 过滤
    big_df = big_df[big_df["ts_code"].str.startswith(("00", "30", "60", "68"))]
    big_df = big_df[~big_df["name"].str.contains("ST", na=False)]
    big_df = big_df[~big_df["name"].str.contains("退", na=False)]

    results = []

    grouped = big_df.groupby("ts_code", sort=False)
    total = len(grouped)
    for i, (ts_code, g) in enumerate(grouped):
        if (i + 1) % 1000 == 0:
            print(f"    进度: {i+1}/{total}")

        g = g.sort_values("trade_date").reset_index(drop=True)
        if len(g) < 30:
            continue

        close = g["close"].astype(float)
        high = g["high"].astype(float)
        low = g["low"].astype(float)

        # MA20
        ma20 = close.rolling(20).mean()
        dev_ma20 = (close - ma20) / ma20 * 100

        # 前5日涨幅
        pre5_return = close.pct_change(5) * 100

        # RSI(14)
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))

        # KDJ
        low_9 = low.rolling(9).min()
        high_9 = high.rolling(9).max()
        rsv = (close - low_9) / (high_9 - low_9).replace(0, np.nan) * 100
        k = rsv.ewm(alpha=1/3, adjust=False).mean()
        d = k.ewm(alpha=1/3, adjust=False).mean()

        # 20日波动率
        vol_20 = close.pct_change().rolling(20).std() * np.sqrt(252) * 100

        # 下一日涨幅（用于命中率统计）
        next_pct_chg = g["pct_chg"].shift(-1)

        # 构造输出df
        out = pd.DataFrame({
            "ts_code": ts_code,
            "name": g["name"],
            "trade_date": g["trade_date"],
            "close": close,
            "pct_chg": g["pct_chg"],
            "next_pct_chg": next_pct_chg,
            "dev_ma20": dev_ma20,
            "pre5_return": pre5_return,
            "rsi": rsi,
            "k_val": k,
            "d_val": d,
            "vol_20": vol_20,
            "amount": g["amount"],
        })
        results.append(out)

    print(f"  因子计算完成，合并...")
    return pd.concat(results, ignore_index=True)


def score_factors(df):
    """计算追高评分（满分100）"""
    # 波动率分（30%）：vol_20 > 60 满分
    vol_score = np.clip((df["vol_20"] - 30) / (60 - 30) * 100, 0, 100)
    # KDJ-D 分（25%）：D < 45 满分，D > 65 得0
    d_score = np.clip(100 - np.abs(df["d_val"] - 50) * 4, 0, 100)
    # KDJ-K 分（15%）
    k_score = np.clip(100 - np.abs(df["k_val"] - 50) * 4, 0, 100)
    # RSI 中性分（10%）：RSI 45-55 满分
    rsi_score = np.clip(100 - np.abs(df["rsi"] - 50) * 3, 0, 100)
    # 均线贴合分（10%）：偏离<3% 满分
    ma_score = np.clip(100 - np.abs(df["dev_ma20"]) * 10, 0, 100)
    # 蓄势分（10%）：前5日涨幅<3% 满分
    pre5_score = np.clip(100 - np.abs(df["pre5_return"]) * 10, 0, 100)

    df["score"] = (
        vol_score * 0.30 + d_score * 0.25 + k_score * 0.15 +
        rsi_score * 0.10 + ma_score * 0.10 + pre5_score * 0.10
    )
    return df


def analyze_hit_rate(df):
    """分析命中率"""
    df = df.dropna(subset=["next_pct_chg", "score"])

    # 涨停判定：创业板/科创板 >= 19.9%，主板 >= 9.9%
    def is_limit_up(row):
        if pd.isna(row["next_pct_chg"]):
            return False
        code = row["ts_code"]
        threshold = 19.9 if code.startswith(("30", "68")) else 9.9
        return row["next_pct_chg"] >= threshold

    df["next_limit_up"] = df.apply(is_limit_up, axis=1)

    # 全市场平均涨停率（基准）
    base_rate = df["next_limit_up"].mean() * 100

    # 分组统计
    print(f"\n{'='*90}")
    print("追高因子命中率分析")
    print(f"{'='*90}")
    print(f"\n  全市场平均次日涨停率: {base_rate:.2f}%")
    print(f"  总样本: {len(df):,} 标的-日")

    # 按评分分组
    bins = [(0, 30), (30, 50), (50, 60), (60, 70), (70, 75), (75, 80), (80, 100)]
    print(f"\n  {'评分区间':<12} {'样本数':>8} {'涨停数':>6} {'命中率':>8} {'vs基准':>10} {'提升倍数':>10}")
    print("  " + "-" * 70)

    for lo, hi in bins:
        group = df[(df["score"] >= lo) & (df["score"] < hi)]
        if len(group) == 0:
            continue
        hits = group["next_limit_up"].sum()
        rate = hits / len(group) * 100
        vs_base = rate - base_rate
        multiple = rate / base_rate if base_rate > 0 else 0
        print(f"  {lo:>3}-{hi:<3}       {len(group):>8,} {hits:>6} {rate:>7.2f}% {vs_base:>+9.2f}% {multiple:>9.2f}x")

    # 更细的分位
    print(f"\n  === 按评分十分位 ===")
    df_sorted = df[df["score"].notna()].sort_values("score", ascending=False)
    n_total = len(df_sorted)
    for decile in range(10):
        start = int(n_total * decile / 10)
        end = int(n_total * (decile + 1) / 10)
        group = df_sorted.iloc[start:end]
        if len(group) == 0:
            continue
        hits = group["next_limit_up"].sum()
        rate = hits / len(group) * 100
        score_range = f"{group['score'].min():.0f}-{group['score'].max():.0f}"
        print(f"  Top{decile*10}-{(decile+1)*10}%  评分[{score_range:>8}]  命中率{rate:.2f}%  ({hits}/{len(group)})")

    # 因子单独IC（信息系数）
    print(f"\n  === 单因子IC分析（评分 vs 次日涨停的相关性）===")
    from scipy.stats import spearmanr
    try:
        valid = df.dropna(subset=["score", "next_pct_chg"])
        ic, p = spearmanr(valid["score"], valid["next_pct_chg"])
        print(f"  Spearman IC = {ic:.4f}  (p={p:.2e})")
    except ImportError:
        # 没有scipy用简单的
        valid = df.dropna(subset=["score", "next_pct_chg"])
        ic = valid["score"].corr(valid["next_pct_chg"], method="spearman")
        print(f"  Spearman IC = {ic:.4f}")

    # 各分量的IC
    for col in ["vol_20", "d_val", "k_val", "rsi", "dev_ma20", "pre5_return"]:
        valid = df.dropna(subset=[col, "next_pct_chg"])
        if len(valid) > 100:
            ic_single = valid[col].corr(valid["next_pct_chg"], method="spearman")
            print(f"  {col:<14} IC = {ic_single:+.4f}")

    return df


def main():
    print("=" * 90)
    print("追高因子全市场命中率验证")
    print("=" * 90)

    print("\n加载全市场daily数据...")
    big_df = load_all_daily()
    print(f"  总行数: {len(big_df):,}")

    name_map = get_name_map()
    trade_dates = get_trade_dates()
    print(f"  交易日数: {len(trade_dates)}")

    # 计算因子
    factor_df = compute_factors(big_df, name_map)
    print(f"  因子表行数: {len(factor_df):,}")

    # 过滤：成交额>1000万
    factor_df = factor_df[factor_df["amount"] > 10000]
    print(f"  过滤后(成交额>1000万): {len(factor_df):,}")

    # 评分
    factor_df = score_factors(factor_df)
    print(f"  评分完成，均值={factor_df['score'].mean():.1f} 中位={factor_df['score'].median():.1f}")

    # 命中率分析
    result_df = analyze_hit_rate(factor_df)

    # 按日期分析高分组的命中率趋势
    print(f"\n{'='*90}")
    print("高分(>70)标的次日涨停命中率——按月统计")
    print(f"{'='*90}\n")

    high_score = result_df[result_df["score"] >= 70].copy()
    high_score["month"] = high_score["trade_date"].str[:6]
    print(f"  {'月份':<8} {'高分样本':>8} {'涨停数':>6} {'命中率':>8} {'vs基准':>10}")
    print("  " + "-" * 50)
    for month, group in high_score.groupby("month"):
        hits = group["next_limit_up"].sum()
        rate = hits / len(group) * 100 if len(group) > 0 else 0
        # 同月全市场基准
        month_all = result_df[result_df["trade_date"].str[:6] == month]
        base = month_all["next_limit_up"].mean() * 100 if len(month_all) > 0 else 0
        print(f"  {month:<8} {len(group):>8,} {hits:>6} {rate:>7.2f}% {rate-base:>+9.2f}%")

    # 保存
    # 为了控制大小，只保存高分样本和统计
    high_score.to_csv(f"{CACHE_DIR}/high_score_stocks.csv", index=False)
    print(f"\n  高分标的已保存: {CACHE_DIR}/high_score_stocks.csv")


if __name__ == "__main__":
    main()
