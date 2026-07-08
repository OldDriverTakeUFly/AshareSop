"""
中国版 VIX 核心：上证50ETF 期权 ATM 隐含波动率计算
用 Black-Scholes 模型反算隐含波动率
"""
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tushare as ts
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
pro = ts.pro_api()


def bs_price(S, K, T, r, sigma, opt_type="C"):
    """Black-Scholes 期权定价"""
    if T <= 0:
        return max(S - K, 0) if opt_type == "C" else max(K - S, 0)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if opt_type == "C":
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def implied_vol(market_price, S, K, T, r, opt_type="C"):
    """用 Brent 法反算隐含波动率"""
    try:
        return brentq(
            lambda sigma: bs_price(S, K, T, r, sigma, opt_type) - market_price,
            1e-4, 5.0,  # 0.01% ~ 500%
            xtol=1e-6,
        )
    except Exception:
        return np.nan


def main():
    latest = "20260708"

    # 50ETF 现货
    etf = pro.fund_daily(ts_code="510050.SH", start_date="20260601")
    spot = etf["close"].iloc[0]
    print(f"50ETF 现货价: {spot} ({latest})")

    # 无风险利率：用 1 年期国债到期收益率近似
    try:
        tf = pro.yc_cb(
            curve_type="0SCTSY",
            trade_date=latest,
            fields="trade_date,curve_id,1y",
        )
        r = float(tf["1y"].iloc[0]) / 100 if len(tf) > 0 else 0.02
    except Exception:
        r = 0.018  # 中国 1Y 国债近年均值
    print(f"无风险利率: {r*100:.2f}%")

    # 近月合约
    basic = pro.opt_basic(exchange="SSE", fields="ts_code,name,opt_type,call_put,exercise_price,list_date,delist_date")
    basic = basic[basic["name"].str.contains("华夏上证50ETF", na=False)]
    basic = basic[basic["delist_date"] > latest]
    near_term_date = basic["delist_date"].min()
    near = basic[basic["delist_date"] == near_term_date].copy()

    # 计算到期时间（按 ACT/365）
    today = datetime.strptime(latest, "%Y%m%d")
    expire = datetime.strptime(str(near_term_date), "%Y%m%d")
    T = (expire - today).days / 365
    print(f"近月到期日: {near_term_date}, T = {T:.4f} 年 ({(expire-today).days} 天)")

    # 取所有 ATM 期权（行权价在现货 ±10% 内）
    near["diff"] = abs(near["exercise_price"] - spot)
    near = near[near["diff"] <= spot * 0.15]
    near = near.sort_values(["exercise_price", "call_put"])

    print(f"\n期权链（现货={spot}，行权价 ±15%）：")
    results = []
    for _, row in near.iterrows():
        daily = pro.opt_daily(ts_code=row["ts_code"], trade_date=latest)
        if daily is None or len(daily) == 0:
            continue
        price = daily["close"].iloc[0]
        iv = implied_vol(price, spot, row["exercise_price"], T, r, row["call_put"])
        results.append({
            "name": row["name"],
            "type": row["call_put"],
            "K": row["exercise_price"],
            "price": price,
            "iv": iv,
            "oi": daily["oi"].iloc[0] if "oi" in daily.columns else None,
            "vol": daily["vol"].iloc[0] if "vol" in daily.columns else None,
        })

    df = pd.DataFrame(results)
    # 过滤 IV 异常（<5% 或 >200%）
    df = df[(df["iv"] > 0.05) & (df["iv"] < 2.0)]
    df = df.dropna(subset=["iv"])
    print(df[["name", "type", "K", "price", "iv", "oi", "vol"]].to_string(index=False))

    # ATM 隐含波动率（行权价最接近现货的 C+P 平均）
    atm = df.loc[df["K"].sub(spot).abs().idxmin()] if len(df) > 0 else None
    if atm is not None:
        # 同一行权价的 C 和 P
        same_k = df[df["K"] == atm["K"]]
        atm_iv = same_k["iv"].mean()
        print(f"\n=== ATM（K={atm['K']}）隐含波动率 ===")
        print(f"  IV = {atm_iv*100:.2f}%")
        print(f"  VIX-近似 = {atm_iv*100:.1f}")

    # 波动率微笑
    print(f"\n=== 波动率微笑 ===")
    pivot = df.pivot_table(index="K", columns="type", values="iv").reset_index()
    pivot["avg_iv"] = pivot[["C", "P"]].mean(axis=1)
    pivot["moneyness"] = (pivot["K"] / spot - 1) * 100  # 价外度
    print(pivot[["K", "moneyness", "C", "P", "avg_iv"]].to_string(index=False))

    # 加权平均 IV（按持仓量）
    df["weight"] = df["oi"]
    weighted_iv = (df["iv"] * df["weight"]).sum() / df["weight"].sum()
    print(f"\n持仓量加权 IV = {weighted_iv*100:.2f}%")

    # OTM 加权（VIX 风格：K<S 的 Put + K>S 的 Call）
    otm = pd.concat([
        df[(df["type"] == "P") & (df["K"] < spot)],
        df[(df["type"] == "C") & (df["K"] > spot)],
    ])
    if len(otm) > 0:
        otm_weighted = (otm["iv"] * otm["oi"]).sum() / otm["oi"].sum()
        print(f"OTM 加权 IV（VIX 风格）= {otm_weighted*100:.2f}%")

    # 保存
    out = Path(__file__).resolve().parent / "output"
    df.to_csv(out / "etf_iv_snapshot.csv", index=False)
    pivot.to_csv(out / "iv_smile.csv", index=False)
    print(f"\n已保存: output/etf_iv_snapshot.csv, output/iv_smile.csv")


if __name__ == "__main__":
    main()
