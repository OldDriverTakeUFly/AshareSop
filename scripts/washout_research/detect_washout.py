"""低位启动洗盘模式检测 — 全市场事件研究

研究问题
--------
用户假设:洗盘常发生在低位启动之后,目的是抬高平均持股成本、降低获利盘以稳定抛压。
本脚本从数据角度把「低位启动洗盘」变成可检测、可统计的事件,并同步构建高位对照组,
为「高位洗盘 vs 出货」的辨析提供实证基础。

可操作定义(预注册,2026-08-25)
--------
1. 低位启动锚(anchor t0): limit_pool 首板涨停(连板数=1),前 20 交易日无任何涨停,
   启动前收盘的 250 日分位 ≤ 30%,且上市满 120 个交易日。
2. 高位对照锚: 任意涨停,启动前收盘 250 日分位 ≥ 70% 且前 60 日涨幅 ≥ 30%。
3. 启动段(leg): 从 t0 向后跟踪运行峰值(adj high),回撤触及 -5% 且累计涨幅 ≥ 15%
   时进入洗盘段;40 日内未出现回撤则丢弃(不属于回调样本)。
4. 洗盘段(wash): 峰值→谷底。结束条件(先到先得):
   a) 收盘创前高 → outcome=continue(洗盘成功续涨)
   b) 收盘跌破启动平台(启动前收盘) → outcome=breakdown(破位/出货式失败)
   c) 峰值后 25 交易日未决 → outcome=timeout
5. 因果性: 所有判别特征只取 峰值→谷底 窗口,结局与后向收益在谷底之后度量。

价格口径: daily_price 的 close 为不复权价,pct_chg 为真实收益;所有价格几何
(回撤/前高/均线/分位)一律用 close×adj_factor/最新adj_factor 的前复权序列。

输出: studies/output/washout/episodes_low.csv, episodes_high.csv + summary.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

DB = "storage/database/market_data.db"
OUT_DIR = "studies/output/washout"
CAL_START = 20200601  # 日历起点(250日回看覆盖 2021-01 锚点)
ANCHOR_START = 20210101
ANCHOR_END = 20260824

# ── 参数(预注册) ──
P_LOW_POS_MAX = 30       # 低位组:启动前收盘 250 日分位上限(%)
P_HIGH_POS_MIN = 70      # 高位组:分位下限(%)
P_HIGH_PRERUN_MIN = 0.30 # 高位组:前 60 日涨幅下限
LEG_MIN_GAIN = 0.15      # 启动段最小涨幅
WASH_TRIGGER_DD = -0.05  # 进入洗盘段的回撤触发
LEG_MAX_DAYS = 40        # 锚点后最多跟踪日数
WASH_MAX_DAYS = 25       # 峰值后未决超时
MIN_DEPTH = -0.35        # 回撤深于 -35% 视为崩塌,丢弃
DEDUP_DAYS = 15          # 同股锚点去重窗口
CHIP_HALFLIFE = 58       # 筹码时间衰减半衰期(交易日)
HIST_MIN = 120           # 上市历史下限
FWD_HORIZONS = [5, 10, 20, 60]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def pct_rank(v: float, arr: np.ndarray) -> float:
    """v 在 arr(无 NaN)中的百分位 0-100。"""
    if arr.size == 0:
        return np.nan
    return float((arr < v).sum() / arr.size * 100.0)


def load_data() -> tuple:
    import sqlite3
    con = sqlite3.connect(DB)
    log("加载 stock_basic ...")
    sb = pd.read_sql(
        "SELECT ts_code, name, list_status, list_date FROM stock_basic", con)
    log("加载 index_daily (指数收盘) ...")
    idx = pd.read_sql(
        "SELECT trade_date, close FROM index_daily WHERE ts_code='000001.SH' "
        "ORDER BY trade_date", con)
    idx["trade_date"] = idx["trade_date"].astype(int)
    log("加载 daily_price ...")
    dp = pd.read_sql(
        "SELECT ts_code, trade_date, open, high, low, close, vol, amount, adj_factor "
        "FROM daily_price WHERE trade_date>='20200601' ORDER BY ts_code, trade_date", con)
    dp["trade_date"] = dp["trade_date"].astype(int)
    log("加载 limit_pool ...")
    lp = pd.read_sql(
        "SELECT trade_date, ts_code, consecutive_boards, turnover_rate FROM limit_pool "
        "WHERE pool_kind='limit_up'", con)
    lp["trade_date"] = lp["trade_date"].str.replace("-", "").astype(int)
    # limit_pool 历史数据 ts_code 无交易所后缀(如 000056),按前缀补齐与 daily_price 对齐
    # 注意 str.contains('.') 的 '.' 是正则通配符,必须 regex=False
    lp["ts_code"] = lp["ts_code"].astype(str).str.strip()
    bare = ~lp["ts_code"].str.contains(".", regex=False)
    log(f"limit_pool 无后缀代码 {bare.sum()}/{len(lp)} 行,按前缀补后缀")
    lp.loc[bare, "ts_code"] = lp.loc[bare, "ts_code"].map(
        lambda c: c + (".SH" if c.startswith(("60", "68", "9")) else ".SZ"))
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
    return sb, idx, dp, lp, inf, mf


def build_arrays(sb, idx, dp, lp, inf, mf):
    """构建日历对齐的每股数组与事件索引。"""
    # 交易日历:全市场 daily_price 日期并集(index_daily 的上证指数只有 2021+,
    # 会让 2021 年初锚点丢失 250 日回看)
    cal = np.sort(dp.loc[dp["trade_date"] >= CAL_START, "trade_date"].unique())
    cal_pos = {int(d): i for i, d in enumerate(cal)}
    n_cal = len(cal)
    # 指数收盘对齐到日历(2020 下半年缺失 → NaN)
    idx_close = np.full(n_cal, np.nan)
    for _, row in idx.iterrows():
        p = cal_pos.get(int(row["trade_date"]))
        if p is not None:
            idx_close[p] = row["close"]
    idx_df = pd.DataFrame({"trade_date": cal, "close": idx_close})
    log(f"交易日历 {cal[0]}~{cal[-1]} 共 {n_cal} 天(全市场日期并集)")

    # 股票池:沪深主板/创业板/科创板,剔除当前 ST/退市警示名
    valid_prefix = ("60", "00", "30", "68")
    sb = sb[sb["ts_code"].str[:2].isin(valid_prefix)]
    bad_name = sb["name"].astype(str).str.contains("ST|退", na=False)
    universe = set(sb.loc[~bad_name, "ts_code"])
    log(f"股票池 {len(universe)} 只(现名无 ST/退,含已退市,北交所剔除)")

    dp = dp[dp["ts_code"].isin(universe)]
    log(f"daily_price 过滤后 {len(dp)} 行")

    # 前复权:除以该股最新 adj_factor。
    # adj_factor 有约 15% 行缺失(历史回补批次),按股票 ffill/bfill 补齐(除权事件间恒定)
    dp["adj_factor"] = dp.groupby("ts_code")["adj_factor"].ffill()
    dp["adj_factor"] = dp.groupby("ts_code")["adj_factor"].bfill()
    dp["adj_factor"] = dp["adj_factor"].fillna(1.0)
    last_adj = dp.groupby("ts_code")["adj_factor"].last()
    dp["k"] = dp["ts_code"].map(last_adj)
    for col in ["open", "high", "low", "close"]:
        dp[col] = (dp[col] * dp["adj_factor"] / dp["k"]).astype(np.float32)
    dp["vol"] = dp["vol"].astype(np.float32)
    dp["amount"] = dp["amount"].astype(np.float32)

    stocks: dict[str, dict] = {}
    for code, g in dp.groupby("ts_code", sort=False):
        pos = np.searchsorted(cal, g["trade_date"].to_numpy())
        ao = np.full(n_cal, np.nan, np.float32)
        ah = np.full(n_cal, np.nan, np.float32)
        al = np.full(n_cal, np.nan, np.float32)
        ac = np.full(n_cal, np.nan, np.float32)
        av = np.full(n_cal, np.nan, np.float32)
        aa = np.full(n_cal, np.nan, np.float32)
        ao[pos], ah[pos], al[pos], ac[pos] = (
            g["open"].to_numpy(), g["high"].to_numpy(),
            g["low"].to_numpy(), g["close"].to_numpy())
        av[pos], aa[pos] = g["vol"].to_numpy(), g["amount"].to_numpy()
        stocks[code] = {"o": ao, "h": ah, "l": al, "c": ac, "v": av, "a": aa, "end": pos[-1]}
    log(f"构建每股数组 {len(stocks)} 只")

    # 涨停事件索引:每股排序位置数组 + 锚点换手率
    lp = lp[lp["ts_code"].isin(universe)]
    limit_pos: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    lp_turnover_map: dict[tuple[str, int], float] = {}
    lp["pos"] = lp["trade_date"].map(cal_pos)
    lp = lp.dropna(subset=["pos"])
    lp["pos"] = lp["pos"].astype(int)
    for code, g in lp.groupby("ts_code"):
        g = g.sort_values("pos")
        limit_pos[code] = (g["pos"].to_numpy(), g["consecutive_boards"].to_numpy())
        for _, row in g.iterrows():
            lp_turnover_map[(code, int(row["pos"]))] = float(row["turnover_rate"])

    # 每股有效交易日位置(用于上市历史长度判断)
    valid_pos = {c: np.flatnonzero(np.isfinite(s["c"])) for c, s in stocks.items()}

    # intraday / moneyflow:按 (code, pos) 排序,groupby 切片
    def build_aux(df, cols):
        df = df[df["ts_code"].isin(universe)].copy()
        df["pos"] = df["trade_date"].map(cal_pos)
        df = df.dropna(subset=["pos"])
        df["pos"] = df["pos"].astype(np.int32)
        df = df.sort_values(["ts_code", "pos"])
        out = {}
        for c, g in df.groupby("ts_code", sort=False):
            d = {"pos": g["pos"].to_numpy(np.int32)}
            for col in cols:
                d[col] = g[col].to_numpy(np.float32)
            out[c] = d
        return out

    log("对齐 intraday_feature ...")
    aux_inf = build_aux(inf, ["upper_shadow", "close_position", "amplitude"])
    log("对齐 moneyflow ...")
    aux_mf = build_aux(mf, ["buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount"])

    return cal, cal_pos, stocks, limit_pos, lp_turnover_map, valid_pos, aux_inf, aux_mf, idx_df


def chip_metrics(s: dict, i: int, halflife: float = CHIP_HALFLIFE) -> tuple[float, float]:
    """时间衰减筹码代理:返回 (平均成本, 获利盘%) 截至 i(不含 i 之后)。"""
    lo = max(0, i - 250)
    sl = slice(lo, i + 1)
    c, o, h, l, v = s["c"][sl], s["o"][sl], s["h"][sl], s["l"][sl], s["v"][sl]
    ok = np.isfinite(c) & np.isfinite(v) & (v > 0)
    if ok.sum() < 20:
        return np.nan, np.nan
    typ = (o[ok] + h[ok] + l[ok] + c[ok]) / 4.0
    age = ok.sum() - 1 - np.arange(ok.sum())  # 0=当天
    w = v[ok] * np.power(0.5, age / halflife)
    px = c[ok][-1]
    prof = float(w[typ < px].sum() / w.sum() * 100.0)
    avg = float((w * typ).sum() / w.sum())
    return avg, prof


def aux_window(aux: dict, code: str, i0: int, i1: int, cols: list[str]) -> dict:
    """取每股 aux 在日历位置 [i0, i1] 的列均值/计数。"""
    d = aux.get(code)
    if d is None:
        return {f"{c}_mean": np.nan for c in cols}
    p = d["pos"]
    m = (p >= i0) & (p <= i1)
    out = {}
    for c in cols:
        vals = d[c][m]
        out[f"{c}_mean"] = float(np.nanmean(vals)) if m.sum() else np.nan
    return out


def mf_net_intensity(aux: dict, code: str, i0: int, i1: int, amount: np.ndarray) -> float:
    """主力(大单+超大单)净流入强度 = Σ净额 / Σ成交额,窗口 [i0,i1]。"""
    d = aux.get(code)
    if d is None:
        return np.nan
    p = d["pos"]
    m = (p >= i0) & (p <= i1)
    if m.sum() == 0:
        return np.nan
    net = (d["buy_lg_amount"][m] + d["buy_elg_amount"][m]
           - d["sell_lg_amount"][m] - d["sell_elg_amount"][m]).sum()
    amt = np.nansum(amount[i0:i1 + 1])
    if not np.isfinite(amt) or amt <= 0:
        return np.nan
    return float(net / amt)


def scan_episode(s: dict, t0: int, aux_inf: dict, aux_mf: dict, code: str) -> dict | None:
    """从锚点 t0 扫描一次 启动→洗盘→结局。返回特征 dict 或 None。"""
    c, h, l, v = s["c"], s["h"], s["l"], s["v"]
    pre = c[t0 - 1]
    if not np.isfinite(pre):
        return None
    support = pre
    peak = h[t0]
    peak_i = t0
    phase = "leg"
    trough = np.nan
    trough_i = -1
    outcome = None
    end_i = -1
    last_i = t0
    scan_hi = min(t0 + 1 + LEG_MAX_DAYS, s["end"] + 1)

    for i in range(t0 + 1, scan_hi):
        last_i = i
        if not np.isfinite(c[i]):
            continue
        if phase == "leg":
            if h[i] > peak:
                peak, peak_i = h[i], i
            dd = l[i] / peak - 1.0
            gain = peak / pre - 1.0
            if dd <= WASH_TRIGGER_DD and gain >= LEG_MIN_GAIN:
                phase = "wash"
                trough, trough_i = l[i], i
            elif c[i] < support:
                return None  # 启动当日段即破位 → 非洗盘样本
        else:
            if l[i] < trough:
                trough, trough_i = l[i], i
            if c[i] > peak:
                outcome, end_i = "continue", i
                break
            if c[i] < support:
                outcome, end_i = "breakdown", i
                break
            if i - peak_i > WASH_MAX_DAYS:
                outcome, end_i = "timeout", i
                break

    if phase != "wash":
        return None  # 跟踪窗口内未出现有效回调
    if outcome is None:
        if last_i >= s["end"]:  # 数据走到尽头仍未决 → 保留为 open
            outcome, end_i = "open", last_i
        else:
            return None

    # 谷底只在 峰值→结局前一天 窗口内取(结局当天的K线属于突破/破位,不属于洗盘段)
    w_hi = end_i - 1 if outcome != "open" else end_i
    if w_hi > peak_i:
        seg = l[peak_i + 1:w_hi + 1]
        ok = np.isfinite(seg)
        if ok.any():
            trough = float(seg[ok].min())
            trough_i = peak_i + 1 + int(np.flatnonzero(ok)[np.argmin(seg[ok])])

    depth = float(trough / peak - 1.0)
    if not np.isfinite(c[trough_i]):
        return None

    # ── 因果特征:仅用 t0..trough_i 的数据 ──
    leg_v = v[t0:peak_i + 1]
    leg_v = leg_v[np.isfinite(leg_v)]
    wash_v = v[peak_i + 1:trough_i + 1]
    wash_v = wash_v[np.isfinite(wash_v)]
    vol_ratio = float(np.nanmean(wash_v) / np.nanmean(leg_v)) if leg_v.size and wash_v.size else np.nan
    base_v = v[max(0, t0 - 60):t0]
    base_v = base_v[np.isfinite(base_v)]
    vol_vs_base = float(np.nanmean(wash_v) / np.nanmean(base_v)) if base_v.size and wash_v.size else np.nan

    # 洗盘段下跌日 vs 上涨日量能
    wash_c = c[peak_i + 1:trough_i + 1]
    wash_v_raw = v[peak_i + 1:trough_i + 1]
    m_ok = np.isfinite(wash_c) & np.isfinite(wash_v_raw)
    wc, wv = wash_c[m_ok], wash_v_raw[m_ok]
    if wc.size >= 3 and (wc[1:] < wc[:-1]).any() and (wc[1:] > wc[:-1]).any():
        dn = wv[1:][wc[1:] < wc[:-1]].mean()
        up = wv[1:][wc[1:] > wc[:-1]].mean()
        down_vol_adv = float(dn / up) if up > 0 else np.nan
    else:
        down_vol_adv = np.nan

    # 日内特征(洗盘段)
    infd = aux_window(aux_inf, code, peak_i + 1, trough_i, ["upper_shadow", "close_position", "amplitude"])
    d = aux_inf.get(code)
    churn_cnt = np.nan
    if d is not None:
        p = d["pos"]
        m = (p >= peak_i + 1) & (p <= trough_i)
        if m.sum():
            # upper_shadow/close_position 均为 0-1 比率:上影占振幅过半 + 收盘位于日内区间下 1/3
            churn_cnt = float(((d["upper_shadow"][m] >= 0.5) & (d["close_position"][m] <= 0.35)).sum())

    # 主力资金强度:启动段 vs 洗盘段
    mf_leg = mf_net_intensity(aux_mf, code, t0, peak_i, s["a"])
    mf_wash = mf_net_intensity(aux_mf, code, peak_i + 1, trough_i, s["a"])

    # 均线支撑(谷底收盘相对 MA10/MA20)
    def ma(win: int, upto: int) -> float:
        seg = c[max(0, upto - win + 1):upto + 1]
        seg = seg[np.isfinite(seg)]
        return float(seg.mean()) if seg.size else np.nan
    ma10, ma20 = ma(10, trough_i), ma(20, trough_i)

    # 筹码代理(启动前 / 峰值 / 谷底)
    avg0, prof0 = chip_metrics(s, t0 - 1)
    avgp, profp = chip_metrics(s, peak_i)
    avgt, proft = chip_metrics(s, trough_i)

    return {
        "t0_pos": t0, "peak_pos": peak_i, "trough_pos": trough_i, "end_pos": end_i,
        "leg_gain": float(peak / pre - 1.0), "leg_days": int(peak_i - t0),
        "depth": depth, "wash_days": int(trough_i - peak_i),
        "wash_len": int(trough_i - peak_i), "total_days": int(end_i - t0),
        "vol_ratio": vol_ratio, "vol_vs_base": vol_vs_base, "down_vol_adv": down_vol_adv,
        "churn_days": churn_cnt,  # 冲高回落日数
        "upper_shadow_mean": infd.get("upper_shadow_mean", np.nan),
        "close_position_mean": infd.get("close_position_mean", np.nan),
        "amplitude_mean": infd.get("amplitude_mean", np.nan),
        "mf_leg": mf_leg, "mf_wash": mf_wash,
        "trough_vs_support": float(trough / support - 1.0),
        "close_vs_ma10": float(c[trough_i] / ma10 - 1.0) if np.isfinite(ma10) else np.nan,
        "close_vs_ma20": float(c[trough_i] / ma20 - 1.0) if np.isfinite(ma20) else np.nan,
        "trough_vs_t0low": float(trough / l[t0] - 1.0) if np.isfinite(l[t0]) else np.nan,
        "chip_avg_pre": avg0, "chip_prof_pre": prof0,
        "chip_avg_peak": avgp, "chip_prof_peak": profp,
        "chip_avg_trough": avgt, "chip_prof_trough": proft,
        "outcome": outcome,
        "_close_trough": float(c[trough_i]), "_peak": float(peak), "_support": float(support),
    }


def forward_returns(s: dict, cal: np.ndarray, idx_close: np.ndarray, trough_i: int) -> dict:
    out = {}
    c = s["c"]
    base = c[trough_i]
    for hz in FWD_HORIZONS:
        j = trough_i + hz
        if j > s["end"]:
            out[f"fwd{hz}"] = np.nan
            continue
        seg = c[trough_i + 1:j + 1]
        seg = seg[np.isfinite(seg)]
        out[f"fwd{hz}"] = float(seg[-1] / base - 1.0) if seg.size else np.nan
    # 后 20 日最大涨幅/最大回撤
    seg = c[trough_i + 1:trough_i + 21]
    seg = seg[np.isfinite(seg)]
    if seg.size:
        out["fwd20_maxup"] = float(seg.max() / base - 1.0)
        run = np.maximum.accumulate(seg)
        out["fwd20_maxdd"] = float((seg / run - 1.0).min())
    else:
        out["fwd20_maxup"] = out["fwd20_maxdd"] = np.nan
    # 同窗口指数收益(超额基准)
    if trough_i + 20 < len(idx_close):
        out["idx_fwd20"] = float(idx_close[trough_i + 20] / idx_close[trough_i] - 1.0)
    else:
        out["idx_fwd20"] = np.nan
    return out


def exante_check(s: dict, peak_i: int, t0: int, support: float, peak: float) -> dict:
    """完全事前的固定时点检验:峰值后第 3 个交易日收盘决策。

    所有输入在 peak+3 当天收盘可知,无谷底位置回看。
    """
    c, v = s["c"], s["v"]
    j = peak_i + 3
    if j > s["end"] or not np.isfinite(c[j]):
        return {}
    leg_v = v[t0:peak_i + 1]
    leg_v = leg_v[np.isfinite(leg_v)]
    w3 = v[peak_i + 1:j + 1]
    w3 = w3[np.isfinite(w3)]
    fwd20 = np.nan
    if j + 20 <= s["end"]:
        seg = c[j + 1:j + 21]
        seg = seg[np.isfinite(seg)]
        if seg.size:
            fwd20 = float(seg[-1] / c[j] - 1.0)
    return {
        "exante_dd3": float(c[j] / peak - 1.0),
        "exante_sup3": float(c[j] / support - 1.0),
        "exante_vol3": float(w3.mean() / leg_v.mean()) if leg_v.size and w3.size else np.nan,
        "exante_fwd20": fwd20,
    }


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    t_start = time.time()
    sb, idx, dp, lp, inf, mf = load_data()
    (cal, cal_pos, stocks, limit_pos, lp_turnover_map, valid_pos,
     aux_inf, aux_mf, idx_df) = build_arrays(sb, idx, dp, lp, inf, mf)
    idx_close = idx_df["close"].to_numpy(np.float64)
    log("数据准备完成,开始选锚 ...")

    an_lo = int(np.searchsorted(cal, ANCHOR_START))
    an_hi = int(np.searchsorted(cal, ANCHOR_END, side="right")) - 1

    anchors_low: list[tuple[str, int, float]] = []
    anchors_high: list[tuple[str, int, float]] = []
    for code, (poss, boards) in limit_pos.items():
        s = stocks.get(code)
        if s is None:
            continue
        vp = valid_pos[code]
        for k in range(len(poss)):
            t0 = int(poss[k])
            if t0 < an_lo or t0 > an_hi or t0 - 1 < an_lo:
                continue
            hist = vp[vp < t0]
            if hist.size < HIST_MIN:
                continue
            pre = s["c"][t0 - 1]
            if not np.isfinite(pre):
                continue
            win = s["c"][hist[-250:]]
            win = win[np.isfinite(win)]
            if win.size < 120:
                continue
            pos_pct = pct_rank(float(pre), win)

            # 前期涨停间隔:首板组要求前 20 日无涨停
            has_prior = k > 0 and (t0 - poss[k - 1]) <= 20

            # 高位组:分位≥70 且前 60 日涨幅≥30%
            c60 = s["c"][hist[-60:]]
            c60 = c60[np.isfinite(c60)]
            prerun = float(pre / np.nanmin(c60) - 1.0) if c60.size else np.nan

            if pos_pct <= P_LOW_POS_MAX and boards[k] == 1 and not has_prior:
                anchors_low.append((code, t0, lp_turnover_map.get((code, t0), np.nan)))
            if pos_pct >= P_HIGH_POS_MIN and np.isfinite(prerun) and prerun >= P_HIGH_PRERUN_MIN:
                anchors_high.append((code, t0, lp_turnover_map.get((code, t0), np.nan)))

    log(f"锚点:低位 {len(anchors_low)} / 高位 {len(anchors_high)}")

    def dedup(anchors):
        by_stock = defaultdict(list)
        for code, t0, tr in anchors:
            by_stock[code].append((t0, tr))
        out = []
        for code, lst in by_stock.items():
            lst.sort()
            last = -10**9
            for t0, tr in lst:
                if t0 - last >= DEDUP_DAYS:
                    out.append((code, t0, tr))
                    last = t0
        return out

    anchors_low = dedup(anchors_low)
    anchors_high = dedup(anchors_high)
    log(f"去重后:低位 {len(anchors_low)} / 高位 {len(anchors_high)}")

    date_of = {i: int(d) for d, i in cal_pos.items()}

    for group, anchors in [("low", anchors_low), ("high", anchors_high)]:
        rows = []
        n_done = 0
        for code, t0, tr in anchors:
            s = stocks[code]
            r = scan_episode(s, t0, aux_inf, aux_mf, code)
            n_done += 1
            if n_done % 2000 == 0:
                log(f"  [{group}] {n_done}/{len(anchors)} 扫描,命中 {len(rows)}")
            if r is None:
                continue
            r["ts_code"] = code
            r["t0_date"] = date_of[t0]
            r["trough_date"] = date_of[r["trough_pos"]]
            r["anchor_turnover"] = tr
            # 位置分位(启动前)
            hist = valid_pos[code]
            hist = hist[hist < t0][-250:]
            win = s["c"][hist]
            win = win[np.isfinite(win)]
            r["pos_pct_pre"] = pct_rank(float(s["c"][t0 - 1]), win)
            r.update(forward_returns(s, cal, idx_close, r["trough_pos"]))
            r.update(exante_check(s, r["peak_pos"], t0, r["_support"], r["_peak"]))
            # 确认日(end_pos=突破/破位当天,实时可观测)入场的前向收益
            c_ = s["c"]
            for hz in (10, 20, 60):
                j = r["end_pos"] + hz
                if j <= s["end"]:
                    seg = c_[r["end_pos"] + 1:j + 1]
                    seg = seg[np.isfinite(seg)]
                    r[f"fwd{hz}_confirm"] = float(seg[-1] / c_[r["end_pos"]] - 1.0) if seg.size else np.nan
                else:
                    r[f"fwd{hz}_confirm"] = np.nan
            rows.append(r)
        df = pd.DataFrame(rows)
        path = f"{OUT_DIR}/episodes_{group}.csv"
        df.to_csv(path, index=False)
        log(f"[{group}] episodes={len(df)} → {path}")
        if len(df):
            vc = df["outcome"].value_counts().to_dict()
            log(f"[{group}] outcome 分布: {vc}")

    log(f"总耗时 {(time.time()-t_start)/60:.1f} 分钟")


if __name__ == "__main__":
    main()
