"""
A股波动率观察框架实证研究脚本
计算：
1. 上证/深成/创业板/科创50 的已实现波动率（RV）历史
2. RV 分位数（恐慌温度计）
3. RV spike 事件与后续市场表现
4. 上证50ETF期权数据可用性
"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

import tushare as ts
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
pro = ts.pro_api()

INDICES = {
    "上证指数": "000001.SH",
    "深证成指": "399001.SZ",
    "沪深300": "000300.SH",
    "创业板指": "399006.SZ",
    "科创50": "000688.SH",
}


def realized_vol(series_close, window=20):
    """年化已实现波动率（对数收益率 × sqrt(242)）"""
    logret = np.log(series_close).diff().dropna()
    return logret.rolling(window).std() * np.sqrt(242) * 100


def fetch_index(name, code):
    print(f"\n=== {name} ({code}) ===")
    df = pro.index_daily(ts_code=code, start_date="20150101")
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["rv20"] = realized_vol(df["close"], 20)
    df["rv60"] = realized_vol(df["close"], 60)
    df["pct_chg"] = df["close"].pct_change() * 100
    print(f"数据范围: {df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}, 共 {len(df)} 行")
    return df


def analyze_quantiles(df, name):
    rv = df["rv20"].dropna()
    latest = rv.iloc[-1]
    pct = (rv < latest).mean() * 100
    print(f"\n[{name}] 最近 RV20 = {latest:.2f}%")
    print(f"  → 历史分位 = {pct:.1f}%")
    print(f"  分位表:")
    for q in [5, 10, 25, 50, 75, 90, 95, 99]:
        print(f"    P{q:2d} = {rv.quantile(q/100):.2f}%")


def spike_events(df, name, threshold_pct=95):
    """RV 达到 P95 以上的恐慌事件"""
    rv = df["rv20"].dropna()
    thr = rv.quantile(threshold_pct / 100)
    spikes = df[df["rv20"] >= thr].copy()
    spikes = spikes[["trade_date", "close", "rv20", "pct_chg"]].dropna()
    print(f"\n[{name}] RV ≥ P{threshold_pct}（阈值 {thr:.1f}%）的恐慌事件，共 {len(spikes)} 日:")
    if len(spikes) > 0:
        # 每次尖峰取最高点附近（聚类）
        spikes = spikes.sort_values("trade_date").reset_index(drop=True)
        # 取前 15 个独立事件（按 30 日窗口聚类）
        events = []
        last_date = None
        for _, row in spikes.iterrows():
            if last_date is None or (row["trade_date"] - last_date).days > 30:
                events.append(row)
                last_date = row["trade_date"]
        print(f"  独立恐慌事件（30日聚类）: {len(events)} 次")
        for e in events[:12]:
            print(f"    {e['trade_date'].date()} | RV={e['rv20']:.1f}% | 收盘={e['close']:.1f} | 当日{e['pct_chg']:+.2f}%")
    return spikes


def rebound_after_spike(df, name, threshold_pct=90, hold_days=20):
    """恐慌事件后 20 个交易日的反弹表现"""
    rv = df["rv20"].dropna()
    thr = rv.quantile(threshold_pct / 100)
    df = df.dropna(subset=["rv20"]).reset_index(drop=True)
    spike_idx = df.index[df["rv20"] >= thr].tolist()
    # 聚类：相邻 20 日内合并
    clustered = []
    for i in spike_idx:
        if not clustered or i - clustered[-1] > 20:
            clustered.append(i)
    results = []
    for i in clustered:
        if i + hold_days < len(df):
            entry = df.loc[i, "close"]
            exit_ = df.loc[i + hold_days, "close"]
            ret = (exit_ / entry - 1) * 100
            results.append({
                "date": df.loc[i, "trade_date"].date(),
                "rv": df.loc[i, "rv20"],
                "ret_20d": ret,
            })
    rdf = pd.DataFrame(results)
    if len(rdf) > 0:
        win = (rdf["ret_20d"] > 0).mean() * 100
        med = rdf["ret_20d"].median()
        print(f"\n[{name}] 恐慌事件（P{threshold_pct}）后 {hold_days} 交易日反弹统计:")
        print(f"  样本数: {len(rdf)}")
        print(f"  胜率: {win:.1f}%")
        print(f"  中位数收益: {med:+.2f}%")
        print(f"  均值收益: {rdf['ret_20d'].mean():+.2f}%")
        print(f"  最大盈利: {rdf['ret_20d'].max():+.2f}%")
        print(f"  最大亏损: {rdf['ret_20d'].min():+.2f}%")
    return rdf


def main():
    print("=" * 70)
    print("A股波动率观察框架实证研究")
    print("=" * 70)

    all_data = {}
    for name, code in INDICES.items():
        df = fetch_index(name, code)
        all_data[name] = df
        analyze_quantiles(df, name)

    print("\n" + "=" * 70)
    print("恐慌事件（尖峰）回测")
    print("=" * 70)
    for name, df in all_data.items():
        spike_events(df, name, threshold_pct=95)

    print("\n" + "=" * 70)
    print("恐慌后反弹统计（P90 阈值，20 交易日持有）")
    print("=" * 70)
    rebound_results = {}
    for name, df in all_data.items():
        rdf = rebound_after_spike(df, name, threshold_pct=90, hold_days=20)
        rebound_results[name] = rdf

    # 保存数据供后续报告使用
    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)
    for name, df in all_data.items():
        df.to_csv(out_dir / f"rv_{name}.csv", index=False)
        print(f"已保存: output/rv_{name}.csv")

    # 当前波动率快照
    print("\n" + "=" * 70)
    print("当前波动率快照（最新交易日）")
    print("=" * 70)
    snapshot = []
    for name, df in all_data.items():
        rv = df["rv20"].dropna()
        latest_rv = rv.iloc[-1]
        latest_date = df["trade_date"].iloc[-1].date()
        pct = (rv < latest_rv).mean() * 100
        snapshot.append({
            "指数": name,
            "最新日期": latest_date,
            "RV20(%)": round(latest_rv, 2),
            "历史分位": f"{pct:.1f}%",
        })
    snap_df = pd.DataFrame(snapshot)
    print(snap_df.to_string(index=False))
    snap_df.to_csv(out_dir / "rv_snapshot.csv", index=False)


if __name__ == "__main__":
    main()
