"""数据加载:market_data.db → 前复权数组(实验0008)。

改编自 scripts/washout_research/detect_washout.py 的 build_arrays(不跨目录 import,
其模块级 os.chdir 有副作用);数据坑沉淀见该文件注释:adj_factor 缺失按股 ffill/bfill、
日历用全市场 daily_price 日期并集、universe 剔除现名含 ST/退 与北交所。
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

DB = "storage/database/market_data.db"
OUT_DIR = "studies/output/trend_exit"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


@dataclass
class MarketData:
    cal: np.ndarray                       # int yyyymmdd 升序
    cal_pos: dict[int, int]
    stocks: dict[str, dict]
    valid_pos: dict[str, np.ndarray]
    aux_inf: dict[str, dict]              # 2021-01 起;更早窗口由 SQL 结果自然缺失对应股票
    aux_mf: dict[str, dict]


def load_market(start_date: int = 20140601, end_date: int = 20260826,
                codes_limit: int = 0) -> MarketData:
    con = sqlite3.connect(DB)
    log("加载 stock_basic ...")
    sb = pd.read_sql("SELECT ts_code, name FROM stock_basic", con)
    log("加载 daily_price ...")
    dp = pd.read_sql(
        f"SELECT ts_code, trade_date, open, high, low, close, vol, amount, adj_factor "
        f"FROM daily_price WHERE trade_date>={start_date} AND trade_date<={end_date} "
        f"ORDER BY ts_code, trade_date", con)
    dp["trade_date"] = dp["trade_date"].astype(int)
    log("加载 intraday_feature ...")
    inf = pd.read_sql(
        "SELECT ts_code, trade_date, upper_shadow, close_position, amplitude "
        "FROM intraday_feature", con)
    inf["trade_date"] = inf["trade_date"].astype(int)
    log("加载 moneyflow ...")
    mf = pd.read_sql(
        "SELECT ts_code, trade_date, buy_lg_amount, sell_lg_amount, "
        "buy_elg_amount, sell_elg_amount FROM moneyflow", con)
    mf["trade_date"] = mf["trade_date"].astype(int)
    con.close()

    cal = np.sort(dp["trade_date"].unique())
    cal_pos = {int(d): i for i, d in enumerate(cal)}
    n_cal = len(cal)
    log(f"交易日历 {cal[0]}~{cal[-1]} 共 {n_cal} 天(全市场日期并集)")

    valid_prefix = ("60", "00", "30", "68")
    sb = sb[sb["ts_code"].str[:2].isin(valid_prefix)]
    bad = sb["name"].astype(str).str.contains("ST|退", na=False)
    universe = set(sb.loc[~bad, "ts_code"])
    if codes_limit:
        universe = set(sorted(universe)[:codes_limit])
    log(f"股票池 {len(universe)} 只(现名无 ST/退,含已退市,北交所剔除)")

    dp = dp[dp["ts_code"].isin(universe)]
    dp["adj_factor"] = dp.groupby("ts_code")["adj_factor"].ffill()
    dp["adj_factor"] = dp.groupby("ts_code")["adj_factor"].bfill()
    dp["adj_factor"] = dp["adj_factor"].fillna(1.0)
    last_adj = dp.groupby("ts_code")["adj_factor"].last()
    dp["k"] = dp["ts_code"].map(last_adj)
    for col in ("open", "high", "low", "close"):
        dp[col] = (dp[col] * dp["adj_factor"] / dp["k"]).astype(np.float32)
    dp["vol"] = dp["vol"].astype(np.float32)
    dp["amount"] = dp["amount"].astype(np.float32)

    stocks: dict[str, dict] = {}
    valid_pos: dict[str, np.ndarray] = {}
    for code, g in dp.groupby("ts_code", sort=False):
        pos = np.searchsorted(cal, g["trade_date"].to_numpy())
        arrs = {}
        for key, col in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"),
                         ("v", "vol"), ("a", "amount")):
            a = np.full(n_cal, np.nan, np.float32)
            a[pos] = g[col].to_numpy()
            arrs[key] = a
        arrs["end"] = int(pos[-1])
        stocks[code] = arrs
        valid_pos[code] = np.flatnonzero(np.isfinite(arrs["c"]))
    log(f"构建每股数组 {len(stocks)} 只")

    def build_aux(df: pd.DataFrame, cols: list[str]) -> dict[str, dict]:
        df = df[df["ts_code"].isin(universe)].copy()
        df["pos"] = df["trade_date"].map(cal_pos)
        df = df.dropna(subset=["pos"])
        df["pos"] = df["pos"].astype(np.int32)
        df = df.sort_values(["ts_code", "pos"])
        out: dict[str, dict] = {}
        for c_, g in df.groupby("ts_code", sort=False):
            d = {"pos": g["pos"].to_numpy(np.int32)}
            for col in cols:
                d[col] = g[col].to_numpy(np.float32)
            out[c_] = d
        return out

    aux_inf = build_aux(inf, ["upper_shadow", "close_position", "amplitude"])
    aux_mf = build_aux(mf, ["buy_lg_amount", "sell_lg_amount",
                            "buy_elg_amount", "sell_elg_amount"])
    log("数据准备完成")
    return MarketData(cal, cal_pos, stocks, valid_pos, aux_inf, aux_mf)
