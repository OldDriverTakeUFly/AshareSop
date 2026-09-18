"""九维指标纯函数计算（DataFrame in/out，不触网不触库）.

口径冻结于 spec §5：位置/均线用后复权价；压力支撑用未复权现价口径
（与 cyq_perf 成本价同口径）；资金流金额单位万元、daily amount 千元。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_MIN_HISTORY = 120
_NAN = float("nan")


# ── 5.2 相对位置（后复权）──

def _adj(px: pd.DataFrame) -> pd.DataFrame:
    out = px.copy()
    for col in ("open", "high", "low", "close"):
        out[col + "_adj"] = out[col] * out["adj_factor"]
    return out


def compute_position(px: pd.DataFrame) -> dict[str, float]:
    a = _adj(px)
    close = float(a["close_adj"].iloc[-1])
    if len(a) < _MIN_HISTORY:
        return {k: _NAN for k in
                ("pos_250d", "dist_ma20", "dist_ma60", "dist_ma120",
                 "dist_ma250", "dd_high_250")}
    h250 = a["high_adj"].tail(250)
    l250 = a["low_adj"].tail(250)
    rng = float(h250.max() - l250.min())
    pos = (close - float(l250.min())) / rng if rng > 0 else _NAN
    dists: dict[str, float] = {}
    for n in (20, 60, 120, 250):
        ma = float(a["close_adj"].tail(n).mean())
        dists[f"dist_ma{n}"] = close / ma - 1 if ma > 0 else _NAN
    return {"pos_250d": pos, **dists,
            "dd_high_250": close / float(h250.max()) - 1}


# ── 5.3 资金流入（moneyflow 万元;daily amount 千元,×10 对齐）──

def compute_moneyflow(mf_hist: pd.DataFrame, amount_today_k: float) -> dict[str, float]:
    nan = {k: _NAN for k in
           ("elg_net_d0", "lg_net_5d", "net_ratio_d0", "consec_net_days")}
    if mf_hist is None or mf_hist.empty:
        return nan
    m = mf_hist.tail(5)
    elg_net = (m["buy_elg_amount"].fillna(0) - m["sell_elg_amount"].fillna(0))
    lg_net = ((m["buy_lg_amount"].fillna(0) + m["buy_elg_amount"].fillna(0))
              - (m["sell_lg_amount"].fillna(0) + m["sell_elg_amount"].fillna(0)))
    last = m.iloc[-1]
    elg_d0 = float(last["buy_elg_amount"] or 0) - float(last["sell_elg_amount"] or 0) \
        if pd.notna(last["buy_elg_amount"]) or pd.notna(last["sell_elg_amount"]) else _NAN
    streak = 0
    for v in elg_net[::-1]:
        if v > 0:
            streak += 1
        else:
            break
    ratio = _NAN
    if amount_today_k and amount_today_k > 0 and pd.notna(last["net_mf_amount"]):
        ratio = float(last["net_mf_amount"]) / (amount_today_k * 10)
    return {"elg_net_d0": elg_d0, "lg_net_5d": float(lg_net.sum()),
            "net_ratio_d0": ratio, "consec_net_days": float(streak)}


# ── 5.6/5.7 压力支撑(未复权现价口径;均线后复权计算后换算对齐) ──

def _ma_adj(a: pd.DataFrame, n: int) -> float:
    tail = a["close_adj"].tail(n)
    return float(tail.mean()) if len(tail) == n else _NAN


def compute_resistance_support(
    px: pd.DataFrame, cyq: pd.Series | None
) -> dict[str, float | str]:
    """候选链: 筹码分位/成本线(失守转压)/均线/缺口/滚动高低/历史高(spec §5.6/5.7)."""
    close = float(px["close"].iloc[-1])
    res: list[tuple[str, float]] = []
    sup: list[tuple[str, float]] = []

    def add(label: str, value: float, *, above: bool) -> None:
        if value != value:  # NaN 跳过
            return
        (res if above else sup).append((label, value))

    if cyq is not None:
        for label in ("cost_85pct", "cost_95pct"):
            if pd.notna(cyq.get(label)):
                add(label, float(cyq[label]), above=True)
        for label in ("cost_15pct", "cost_5pct"):
            if pd.notna(cyq.get(label)):
                add(label, float(cyq[label]), above=False)
        wa = cyq.get("weight_avg")
        if pd.notna(wa):
            add("weight_avg", float(wa), above=close < float(wa))
        if pd.notna(cyq.get("his_high")):
            add("his_high", float(cyq["his_high"]), above=True)

    win120 = px.tail(120)
    add("high_120d", float(win120["high"].max()), above=True)
    add("low_120d", float(win120["low"].min()), above=False)

    # 均线(后复权均值 / 当日adj_factor → 未复权口径)
    a = _adj(px)
    adj_today = float(px["adj_factor"].iloc[-1])
    for n in (60, 120, 250, 20):
        ma_raw = _ma_adj(a, n) / adj_today
        add(f"MA{n}", ma_raw, above=close < ma_raw)

    # 缺口(120日窗口内最近一个;向上缺口=支撑,向下缺口=阻力)
    lo = max(0, len(px) - 120)
    for i in range(len(px) - 1, lo, -1):
        prev_high = float(px["high"].iloc[i - 1])
        prev_low = float(px["low"].iloc[i - 1])
        cur_high = float(px["high"].iloc[i])
        cur_low = float(px["low"].iloc[i])
        if cur_low > prev_high:
            add("gap_up", prev_high, above=False)
            break
        if cur_high < prev_low:
            add("gap_down", prev_low, above=True)
            break

    res_ok = sorted([t for t in res if t[1] > close * 1.005], key=lambda t: t[1])
    sup_ok = sorted([t for t in sup if t[1] < close * 0.995], key=lambda t: -t[1])
    rp = float(res_ok[0][1]) if res_ok else _NAN
    sp = float(sup_ok[0][1]) if sup_ok else _NAN
    ladder = " | ".join(f"{k}@{v:.2f}({v / close - 1:+.1%})" for k, v in res_ok)
    return {"resistance_price": rp,
            "resistance_dist": rp / close - 1 if res_ok else _NAN,
            "support_price": sp,
            "support_dist": sp / close - 1 if sup_ok else _NAN,
            "resistance_ladder": ladder}


# ── 5.8/5.9 炒作预期与扫雷标签 ──

def industry_momentum(sw_daily_all: pd.DataFrame) -> pd.DataFrame:
    """全行业截面(spec §5.8): 每指数 ret20/ret60/截面分位/250日位置."""
    cols = ["index_code", "ret20", "ret60", "pct_rank60", "pos_250"]
    if sw_daily_all is None or sw_daily_all.empty:
        return pd.DataFrame(columns=cols)
    g = sw_daily_all.sort_values("trade_date").groupby("index_code")["close"]

    def _ret(s: pd.Series, n: int) -> float:
        return float(s.iloc[-1] / s.iloc[-1 - n] - 1) if len(s) > n else _NAN

    rows = []
    for code, s in g:
        if len(s) < 21:
            continue
        pos = _NAN
        if len(s) >= 120:
            tail = s.tail(250)
            rng = float(tail.max() - tail.min())
            pos = (float(s.iloc[-1]) - float(tail.min())) / rng if rng > 0 else _NAN
        rows.append({"index_code": code, "ret20": _ret(s, 20), "ret60": _ret(s, 60),
                     "pos_250": pos})
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df["pct_rank60"] = df["ret60"].rank(pct=True)
    return df


def classify_hype_risk(
    *, corp_events: pd.DataFrame, major_events: pd.DataFrame,
    pledge_ratio: float | None, fin_consecutive_loss: bool, is_st: bool,
    industry_row: pd.Series | None, vol_price_ok: bool, research_count: int,
    day: str, event_window_days: int = 90, major_window_days: int = 180,
) -> tuple[list[str], list[str]]:
    """标签装配(spec §5.8/§5.9): 窗口按自然日回推;宁缺毋错."""
    from datetime import datetime, timedelta

    d0 = datetime.strptime(day, "%Y%m%d")
    corp_start = (d0 - timedelta(days=event_window_days)).strftime("%Y%m%d")
    major_start = (d0 - timedelta(days=major_window_days)).strftime("%Y%m%d")
    hype: list[str] = []
    risk: list[str] = []

    def _in(df: pd.DataFrame, start: str) -> pd.DataFrame:
        if df is None or df.empty or "ann_date" not in df.columns:
            return pd.DataFrame()
        return df[(df["ann_date"] >= start) & (df["ann_date"] <= day)]

    corp_w = _in(corp_events, corp_start)
    if not corp_w.empty:
        if ((corp_w["event_type"] == "holder_trade")
                & (corp_w["direction"] == "positive")).any():
            hype.append("增持")
        if ((corp_w["event_type"] == "holder_trade")
                & (corp_w["direction"] == "negative")).any():
            risk.append("减持")
        if (corp_w["event_type"] == "repurchase").any():
            hype.append("回购")
        if (corp_w["event_type"] == "share_float").any():
            risk.append("解禁")

    major_w = _in(major_events, major_start)
    if not major_w.empty:
        et = set(major_w["event_type"])
        if "ma" in et:
            hype.append("并购重组")
        if "divest" in et:
            hype.append("转型线索")
        if "refinance" in et:
            risk.append("定增")
        if "distress" in et:
            risk.append("爆雷监管")
        if "ma_halt" in et:
            risk.append("重组终止")

    if pledge_ratio is not None and pledge_ratio > 50:
        risk.append("质押率高")
    if fin_consecutive_loss:
        risk.append("持续亏损")
    if is_st:
        risk.append("ST")

    if industry_row is not None:
        rank = industry_row.get("pct_rank60", _NAN)
        ret20 = industry_row.get("ret20", _NAN)
        pos250 = industry_row.get("pos_250", _NAN)
        if rank == rank:
            if rank >= 0.70:
                hype.append("行业动量强")
            if rank <= 0.30 and ret20 == ret20 and ret20 < 0:
                risk.append("行业下行")
        if pos250 == pos250:
            if pos250 < 0.20 and ret20 == ret20 and ret20 > 0:
                hype.append("行业底部拐点")
            if pos250 >= 0.80 and ret20 == ret20 and ret20 < 0:
                risk.append("周期顶部")

    if vol_price_ok:
        hype.append("量价齐升")
    if research_count >= 3:
        hype.append("研报覆盖热")
    return hype, risk


def check_consecutive_loss(income_rows: list[tuple[str, dict]]) -> bool:
    """持续实质亏损(spec §5.9): 最近2个年报+最新一期归母净利均<0.

    income_rows: [(end_date, {n_income: ...})] 任意序;数据不足→False(宁缺毋错).
    """
    if not income_rows:
        return False
    annuals = sorted([d for d, _ in income_rows if d.endswith("1231")])
    latest2 = annuals[-2:]
    if len(latest2) < 2:
        return False
    lookup = dict(income_rows)
    vals = []
    for d in latest2:
        v = lookup[d].get("n_income")
        if v is None:
            return False
        vals.append(v)
    latest_any = max(income_rows, key=lambda t: t[0])
    v_latest = latest_any[1].get("n_income")
    if v_latest is None:
        return False
    return all(v < 0 for v in vals) and v_latest < 0


# ── 5.10 综合分 ──

def _clip01(x: float) -> float:
    return 0.0 if x != x or x < 0 else (1.0 if x > 1 else x)  # NaN→0


def compute_composite(
    *, money: dict, chips: dict, winner: dict, position: dict, rs: dict,
    hype: list[str], risk: list[str],
) -> float:
    """各维 0~100 加权(SURGE_WEIGHTS 单一真相源);NaN 安全."""
    from davis_analyzer.constants import SURGE_WEIGHTS as W

    def s_money() -> float:
        r = _clip01(money.get("net_ratio_d0", _NAN) / 0.15)
        s = _clip01(money.get("consec_net_days", 0) / 5.0)
        n5 = money.get("lg_net_5d", _NAN)
        v = _clip01(n5 / 30000.0) if n5 == n5 else 0.0
        return 100 * (0.4 * r + 0.3 * s + 0.3 * v)

    def s_chips() -> float:
        wa = chips.get("weight_avg", _NAN)
        c50 = chips.get("cost_50pct", _NAN)
        if wa != wa:
            return 50.0
        profit = wa / c50 - 1 if (c50 == c50 and c50 > 0) else 0.0
        return 100 * _clip01((profit + 0.10) / 0.30)

    def s_winner() -> float:
        wr = winner.get("winner_rate", _NAN)
        if wr != wr:
            return 50.0
        if 20 <= wr <= 60:
            return 100.0
        return 100 - (wr - 60) * 2.0 if wr > 60 else wr / 20 * 100

    def s_position() -> float:
        p = position.get("pos_250d", _NAN)
        if p != p:
            return 50.0
        return 100 * (1 - abs(p - 0.35) / 0.65)  # 0.35 分位最优先验

    def s_rs() -> float:
        d = rs.get("resistance_dist", _NAN)
        return 100 * _clip01(d / 0.20) if d == d else 50.0

    total = (W["money"] * s_money() + W["chips"] * s_chips()
             + W["winner"] * s_winner() + W["position"] * s_position()
             + W["resist_support"] * s_rs()
             + W["hype"] * min(100.0, 25.0 * len(hype))
             + W["risk"] * max(0.0, 100.0 - 20.0 * len(risk)))
    return float(max(0.0, min(100.0, total)))
