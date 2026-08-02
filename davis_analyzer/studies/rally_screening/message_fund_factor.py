"""资金面+消息面因子：龙虎榜+连板+大单+板块联动。

新增因子：
1. 游资因子：龙虎榜净买入额、是否上榜
2. 连板因子：前N日涨停次数（连板密度）
3. 大单因子：超大单/大单净流入金额
4. 板块联动因子：所在行业当日涨停数
5. 封板质量：首次封板时间、开板次数（涨停股池数据）
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
from collections import defaultdict

from davis_analyzer.studies.rally_screening.utils import (
    get_trade_dates as _get_trade_dates,
    load_daily_batch,
    load_top_list_batch,
    load_moneyflow_batch,
    get_stock_basic_df,
)

START_DATE = "20251201"
END_DATE = "20260725"


def get_trade_dates():
    return _get_trade_dates(START_DATE, END_DATE)


def load_all_daily():
    """加载全市场daily（从SQLite批量读）"""
    dates = get_trade_dates()
    return load_daily_batch(dates)


def main():
    print("=" * 90)
    print("资金面+消息面因子构建与命中率验证")
    print("=" * 90)

    trade_dates = get_trade_dates()
    print(f"交易日: {len(trade_dates)}")

    # === 2. 加载daily大表（从SQLite批量读）===
    print("\n加载daily大表...")
    big_df = load_all_daily()
    basic = get_stock_basic_df()
    name_map = dict(zip(basic["ts_code"], basic["name"]))
    industry_map = dict(zip(basic["ts_code"], basic["industry"]))

    big_df["name"] = big_df["ts_code"].map(name_map).fillna("?")
    big_df["industry"] = big_df["ts_code"].map(industry_map).fillna("?")
    big_df = big_df[big_df["ts_code"].str.startswith(("00", "30", "60", "68"))]
    big_df = big_df[~big_df["name"].str.contains("ST", na=False)]
    big_df = big_df[~big_df["name"].str.contains("退", na=False)]
    big_df = big_df[big_df["amount"] > 10000]

    print(f"  daily行数: {len(big_df):,}")

    # === 3. 构建资金面因子 ===
    print("\n构建资金面/消息面因子...")

    # 因子A: 龙虎榜因子（从 SQLite 批量读）
    print("  A. 龙虎榜因子...")
    tl_all = load_top_list_batch(trade_dates)
    if not tl_all.empty:
        tl_all["on_top_list"] = 1
        tl_all["top_net_amount"] = tl_all["net_amount"]
        tl_all = tl_all[["ts_code", "trade_date", "on_top_list", "top_net_amount"]]
        big_df = big_df.merge(tl_all, on=["ts_code", "trade_date"], how="left")
    else:
        big_df["on_top_list"] = 0
        big_df["top_net_amount"] = 0
    big_df["on_top_list"] = big_df["on_top_list"].fillna(0).astype(int)
    big_df["top_net_amount"] = big_df["top_net_amount"].fillna(0)

    # 因子B: 连板因子——用 daily_price 的 pct_chg 自己算涨停（不依赖 limit_list_d）
    print("  B. 连板因子（从daily_price的pct_chg推导）...")
    big_df = big_df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    def _is_limit(row):
        code = row["ts_code"]
        threshold = 19.9 if code.startswith(("30", "68")) else 9.9
        return int(row["pct_chg"] >= threshold) if pd.notna(row["pct_chg"]) else 0
    big_df["is_limit_today"] = big_df.apply(_is_limit, axis=1)
    big_df["limit_times"] = big_df["is_limit_today"]  # 简化：涨停=1
    big_df["open_times"] = 0  # limit_pool 的 broken_count 不覆盖全量，暂不填
    big_df["pre5_limit_count"] = big_df.groupby("ts_code")["is_limit_today"].transform(
        lambda x: x.rolling(5, min_periods=1).sum().shift(1)
    ).fillna(0)

    # 因子C: 大单/超大单净流入（从 SQLite moneyflow 表批量读）
    print("  C. 大单净流入因子...")
    mf_all = load_moneyflow_batch(trade_dates)
    if not mf_all.empty:
        mf_all = mf_all[["ts_code", "trade_date", "big_net_total", "net_mf_amount"]]
        big_df = big_df.merge(mf_all, on=["ts_code", "trade_date"], how="left")
    else:
        big_df["big_net_total"] = 0
        big_df["net_mf_amount"] = 0
    big_df["big_net_total"] = big_df["big_net_total"].fillna(0)
    big_df["net_mf_amount"] = big_df["net_mf_amount"].fillna(0)

    # 因子D: 板块联动（所在行业当日涨停数）
    print("  D. 板块联动因子...")
    # 用daily数据计算每个行业每日涨停数
    def is_lu(row):
        code = row["ts_code"]
        threshold = 19.9 if code.startswith(("30", "68")) else 9.9
        return row["pct_chg"] >= threshold

    big_df["is_lu_today"] = big_df.apply(is_lu, axis=1).astype(int)
    industry_lu = big_df.groupby(["trade_date", "industry"])["is_lu_today"].sum().reset_index()
    industry_lu.columns = ["trade_date", "industry", "industry_limit_count"]
    big_df = big_df.merge(industry_lu, on=["trade_date", "industry"], how="left")
    big_df["industry_limit_count"] = big_df["industry_limit_count"].fillna(0)

    # === 4. 命中率分析 ===
    print("\n计算次日涨停...")
    big_df = big_df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    big_df["next_pct_chg"] = big_df.groupby("ts_code")["pct_chg"].shift(-1)

    def is_next_limit(row):
        if pd.isna(row["next_pct_chg"]):
            return 0
        code = row["ts_code"]
        threshold = 19.9 if code.startswith(("30", "68")) else 9.9
        return int(row["next_pct_chg"] >= threshold)

    big_df["next_limit_up"] = big_df.apply(is_next_limit, axis=1)

    # 过滤有效样本（有次日数据的）
    valid = big_df.dropna(subset=["next_pct_chg"]).copy()
    base_rate = valid["next_limit_up"].mean() * 100

    print(f"\n{'='*90}")
    print("资金面/消息面因子命中率分析")
    print(f"{'='*90}")
    print(f"\n  全市场平均次日涨停率: {base_rate:.2f}%")
    print(f"  总样本: {len(valid):,}")

    # === 各因子单独命中率 ===
    print(f"\n  === 单因子命中率 ===\n")
    print(f"  {'因子条件':<35} {'样本数':>8} {'涨停数':>6} {'命中率':>8} {'vs基准':>8} {'提升':>6}")
    print("  " + "-" * 80)

    factors_to_test = [
        # 龙虎榜
        ("龙虎榜上榜", valid["on_top_list"] == 1),
        ("龙虎榜净买入>0", (valid["on_top_list"] == 1) & (valid["top_net_amount"] > 0)),
        ("龙虎榜净买入>5000万", (valid["on_top_list"] == 1) & (valid["top_net_amount"] > 50000000)),
        ("龙虎榜净买入>1亿", (valid["on_top_list"] == 1) & (valid["top_net_amount"] > 100000000)),
        # 连板
        ("今日涨停(首板)", (valid["is_limit_today"] == 1) & (valid["limit_times"] <= 1)),
        ("今日涨停(连板≥2)", (valid["is_limit_today"] == 1) & (valid["limit_times"] >= 2)),
        ("今日涨停(连板≥3)", (valid["is_limit_today"] == 1) & (valid["limit_times"] >= 3)),
        ("前5日涨停≥1次", valid["pre5_limit_count"] >= 1),
        ("前5日涨停≥2次", valid["pre5_limit_count"] >= 2),
        # 大单
        ("超大单+大单净流入>0", valid["big_net_total"] > 0),
        ("超大单+大单净流入>5000万", valid["big_net_total"] > 50000000),
        ("超大单+大单净流入>1亿", valid["big_net_total"] > 100000000),
        # 板块联动
        ("同行业涨停≥3", valid["industry_limit_count"] >= 3),
        ("同行业涨停≥5", valid["industry_limit_count"] >= 5),
        ("同行业涨停≥10", valid["industry_limit_count"] >= 10),
    ]

    factor_hits = {}
    for label, mask in factors_to_test:
        group = valid[mask]
        if len(group) == 0:
            continue
        hits = group["next_limit_up"].sum()
        rate = hits / len(group) * 100
        vs = rate - base_rate
        mult = rate / base_rate if base_rate > 0 else 0
        factor_hits[label] = rate
        print(f"  {label:<35} {len(group):>8,} {hits:>6} {rate:>7.2f}% {vs:>+7.2f}% {mult:>5.2f}x")

    # === 组合因子 ===
    print(f"\n  === 组合因子命中率 ===\n")
    print(f"  {'组合条件':<55} {'样本':>6} {'涨停':>5} {'命中率':>7} {'提升':>6}")
    print("  " + "-" * 85)

    combos = [
        ("连板≥2 + 龙虎榜净买入>0",
         (valid["limit_times"] >= 2) & (valid["on_top_list"] == 1) & (valid["top_net_amount"] > 0)),
        ("连板≥2 + 大单净流入>5000万",
         (valid["limit_times"] >= 2) & (valid["big_net_total"] > 50000000)),
        ("首板 + 同行业涨停≥3",
         (valid["is_limit_today"] == 1) & (valid["limit_times"] <= 1) & (valid["industry_limit_count"] >= 3)),
        ("龙虎榜净买入>5000万 + 大单>5000万",
         (valid["top_net_amount"] > 50000000) & (valid["big_net_total"] > 50000000)),
        ("连板≥2 + 同行业涨停≥5",
         (valid["limit_times"] >= 2) & (valid["industry_limit_count"] >= 5)),
        ("龙虎榜+连板+板块 三合一",
         (valid["on_top_list"] == 1) & (valid["limit_times"] >= 2) & (valid["industry_limit_count"] >= 3)),
        ("首板+龙虎榜净>1亿+大单>1亿+行业涨停≥3",
         (valid["limit_times"] <= 1) & (valid["is_limit_today"] == 1) &
         (valid["top_net_amount"] > 100000000) & (valid["big_net_total"] > 100000000) &
         (valid["industry_limit_count"] >= 3)),
    ]

    for label, mask in combos:
        group = valid[mask]
        if len(group) == 0:
            print(f"  {label:<55} {'0':>6}")
            continue
        hits = group["next_limit_up"].sum()
        rate = hits / len(group) * 100 if len(group) > 0 else 0
        mult = rate / base_rate if base_rate > 0 else 0
        print(f"  {label:<55} {len(group):>6} {hits:>5} {rate:>6.2f}% {mult:>5.2f}x")

    # 保存
    _output_dir = os.path.dirname(os.path.abspath(__file__))
    valid.to_pickle(f"{_output_dir}/full_factor_data.pkl")
    print(f"\n  完整因子数据已保存")


if __name__ == "__main__":
    main()
