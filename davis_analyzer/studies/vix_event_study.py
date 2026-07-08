"""
A股波动率观察框架 - 补充研究
1. 行为代理指标：涨跌停比例、成交额异动、行业分化度
2. 期权隐含波动率估算（50ETF 平值期权）
3. 复合恐慌指数构建
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

out_dir = Path(__file__).resolve().parent / "output"


def behavior_proxy():
    """行为代理指标：全市场涨跌停比、跌停占比"""
    print("=" * 70)
    print("行为代理指标：涨跌停比 + 跌停占比（2024-2026）")
    print("=" * 70)
    # 用 daily 取全市场快照（按交易日）
    dates = pro.trade_cal(exchange="SSE", start_date="20240101", end_date="20260708", is_open="1")
    dates = dates["cal_date"].tolist()
    print(f"交易日数: {len(dates)}")

    # 用 daily 接口取全市场涨跌停统计太慢，改用 limit_list
    # limit_list_d 用 lgt（涨停）/ dzj（跌停）
    records = []
    sample_dates = dates[::5]  # 每 5 天取一次以加速
    print(f"采样 {len(sample_dates)} 个交易日...")
    for i, d in enumerate(sample_dates):
        try:
            # 涨停池
            up = pro.limit_list_d(trade_date=d, limit_type="U")
            n_up = len(up) if up is not None else 0
            # 跌停池
            down = pro.limit_list_d(trade_date=d, limit_type="D")
            n_down = len(down) if down is not None else 0
            # 炸板
            bomb = pro.limit_list_d(trade_date=d, limit_type="Z")
            n_bomb = len(bomb) if bomb is not None else 0
            records.append({"date": d, "涨停": n_up, "跌停": n_down, "炸板": n_bomb})
            if (i + 1) % 20 == 0:
                print(f"  ...{i+1}/{len(sample_dates)} done")
        except Exception as e:
            print(f"  {d} fail: {e}")

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df["跌停占比"] = df["跌停"] / (df["涨停"] + df["跌停"] + 1) * 100
    df = df.sort_values("date").reset_index(drop=True)

    # 恐慌判定：跌停 > 涨停
    panic = df[df["跌停"] > df["涨停"] * 0.5].sort_values("跌停", ascending=False).head(15)
    print(f"\n跌停数 ≥ 涨停数×0.5 的恐慌日（前 15）:")
    print(panic[["date", "涨停", "跌停", "炸板", "跌停占比"]].to_string(index=False))

    df.to_csv(out_dir / "behavior_proxy.csv", index=False)
    print(f"\n已保存: output/behavior_proxy.csv")
    return df


def etf_option_iv():
    """上证50ETF 平值期权隐含波动率近似"""
    print("\n" + "=" * 70)
    print("上证50ETF 期权隐含波动率近似（ATM 隐含波动率）")
    print("=" * 70)
    # 取最新交易日
    cal = pro.trade_cal(exchange="SSE", end_date="20260708", is_open="1")
    latest = cal["cal_date"].iloc[-1]
    print(f"最新交易日: {latest}")

    # 50ETF 基准价
    etf = pro.fund_daily(ts_code="510050.SH", trade_date=latest)
    if etf is None or len(etf) == 0:
        etf = pro.fund_daily(ts_code="510050.SH", start_date="20260601").head(5)
    spot = etf["close"].iloc[0] if len(etf) > 0 else None
    print(f"50ETF 收盘价: {spot}")

    # 取近月期权基础信息
    basic = pro.opt_basic(exchange="SSE", fields="ts_code,name,opt_type,call_put,exercise_price,list_date,delist_date")
    # 只看华夏上证50ETF 期权
    basic = basic[basic["name"].str.contains("华夏上证50ETF", na=False)]
    # 排除已下市
    basic = basic[basic["delist_date"] > latest]
    # 找近月（最早到期的）
    basic = basic.sort_values("delist_date")
    near_term = basic["delist_date"].iloc[0] if len(basic) > 0 else None
    print(f"近月期权到期日: {near_term}")
    if near_term is None:
        print("无可用期权数据")
        return None

    near = basic[basic["delist_date"] == near_term].copy()
    # 找 ATM（行权价最接近现货的）
    near["diff"] = abs(near["exercise_price"] - spot)
    near = near.sort_values("diff")
    atm = near.head(4)  # 取最近的几个
    print(f"\nATM 期权合约:")
    print(atm[["ts_code", "name", "call_put", "exercise_price"]].to_string(index=False))

    # 取这些合约的日线（收盘价、隐含波动率？）
    for _, row in atm.iterrows():
        daily = pro.opt_daily(ts_code=row["ts_code"], trade_date=latest)
        if daily is not None and len(daily) > 0:
            d = daily.iloc[0]
            print(f"  {row['name']}: settle={d.get('settle', 'N/A')}, close={d.get('close', 'N/A')}, vol={d.get('vol', 'N/A')}, oi={d.get('oi', 'N/A')}")
    return None


def correlation_rv_vixlike():
    """计算 RV 与行为指标的相关性"""
    print("\n" + "=" * 70)
    print("RV 与行为指标的相关性（上证）")
    print("=" * 70)
    rv = pd.read_csv(out_dir / "rv_上证指数.csv")
    rv["trade_date"] = pd.to_datetime(rv["trade_date"])
    beh = pd.read_csv(out_dir / "behavior_proxy.csv")
    beh["date"] = pd.to_datetime(beh["date"])

    merged = pd.merge(rv[["trade_date", "rv20", "pct_chg"]], beh, left_on="trade_date", right_on="date", how="inner")
    if len(merged) > 10:
        print(f"样本数: {len(merged)}")
        # 取 RV20 abs(pct_chg)
        merged["abs_chg"] = merged["pct_chg"].abs()
        print("\n相关系数:")
        for col in ["涨停", "跌停", "炸板", "跌停占比"]:
            r = merged["rv20"].corr(merged[col])
            print(f"  RV20 vs {col}: {r:+.3f}")
        print(f"  RV20 vs |当日涨跌幅|: {merged['rv20'].corr(merged['abs_chg']):+.3f}")
    return merged


def main():
    behavior_proxy()
    etf_option_iv()
    correlation_rv_vixlike()


if __name__ == "__main__":
    main()
