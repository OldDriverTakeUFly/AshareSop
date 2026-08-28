"""做T隔夜残余退出校验（overnight study，2026-08-23）.

问题: 飞刀日（当日收盘 ≤ −5%）扛到收盘竞价是 gap 策略左尾的主源（首测
−282bps）。允许"当日不出、T+1/T+2 退出"能否修复左尾，隔夜跳空代价多大。

方法: 独立于引擎的逐笔仿真，严格复刻引擎成交约定——bar0 收盘生成信号、
bar1 开盘成交；滑点/佣金/印花税同 IntradayConfig；盘中跌停拒单回退当日
收盘竞价强平（locked 计数）。d0_1400 模式与引擎 GapDownSmart(0.03,
exit_time="14:00") 逐笔对账校准（net_bps 差 <1bps 的占比须 ≥95%）。

退出模式:
  d0_close   当日收盘竞价（引擎 EOD 语义）
  d0_1400    当日 14:00 触发、14:05 bar 开盘成交（现行 smart 行为）
  t1_close / t1_1400 / t2_close   T+1 / T+2 对应时点
  cond3_t1   14:00 检查点浮亏 >3% → 扛到 T+1 收盘；否则当日 14:00 退出
             （"不把飞刀砍在最低点"假设的直接检验）
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.intraday.engine import IntradayConfig
from davis_analyzer.intraday.features import build_features
from davis_analyzer.intraday.report import load_inputs

SPLIT_DATE = "20260501"
EXIT_BAR = "14:05"     # 14:00 bar 收盘触发、次 bar（14:05）开盘成交
CHECK_BAR = "14:00"    # 条件持有的检查点 bar
KNIFE_CLOSE = -0.05    # 飞刀日：当日收盘较昨收 ≤ −5%（对齐首测分桶）
MODES = ["d0_close", "d0_1400", "t1_close", "t1_1400", "t2_close", "cond3_t1"]


@dataclass
class DayBars:
    trade_date: str
    bars: list[tuple[str, float, float, float]]  # (time, open, close, low)
    pre_close: float
    close: float
    limit_up: float
    limit_down: float


@dataclass
class EntryLot:
    ts_code: str
    day_idx: int  # 在该股 DayBars 序列中的下标
    trade_date: str
    fill_px: float  # 含滑点
    shares: int
    buy_gross: float  # 含滑点成交额


# ── 数据结构 ──

def build_day_structures(
    minute: pd.DataFrame, daily: pd.DataFrame,
) -> dict[str, list[DayBars]]:
    """按股票组织交易日序列（仅保留有日线锚且有 bar 的日子）."""
    from davis_analyzer.limitup.events import limit_ratio_for

    daily = daily[(daily.pre_close > 0) & (daily.close > 0)]
    dmap = {(r.ts_code, r.trade_date): r for r in daily.itertuples(index=False)}
    out: dict[str, list[DayBars]] = {}
    for (code, day), g in minute.groupby(["ts_code", "trade_date"], sort=True):
        drow = dmap.get((code, day))
        if drow is None:
            continue
        ratio = limit_ratio_for(code)
        out.setdefault(code, []).append(DayBars(
            trade_date=day,
            bars=[(t.trade_time, t.open, t.close, t.low)
                  for t in g.sort_values("trade_time").itertuples(index=False)],
            pre_close=float(drow.pre_close), close=float(drow.close),
            limit_up=round(float(drow.pre_close) * (1 + ratio) + 1e-9, 2),
            limit_down=round(float(drow.pre_close) * (1 - ratio) + 1e-9, 2),
        ))
    return out


def _smart_mask(feats: pd.DataFrame) -> dict[tuple[str, str], bool]:
    """smart 入场过滤（trend_up 且 vol_ratio1 ≤ 2.5，缺失/NaN 不入场）."""
    mask: dict[tuple[str, str], bool] = {}
    for r in feats.itertuples(index=False):
        tu, vr = r.trend_up, r.vol_ratio1
        ok = (tu == tu) and bool(tu) and (vr == vr) and (vr <= 2.5)
        mask[(r.ts_code, r.trade_date)] = ok
    return mask


# ── 入场扫描（复刻引擎 GapDownSmart 语义） ──

def scan_entries(
    days: dict[str, list[DayBars]],
    smart: dict[tuple[str, str], bool] | None,
    gap: float,
    cfg: IntradayConfig,
) -> list[EntryLot]:
    slip = cfg.slippage_bps / 1e4
    lots: list[EntryLot] = []
    for code, seq in days.items():
        for i, d in enumerate(seq):
            if len(d.bars) < 2 or d.pre_close <= 0:
                continue
            base = int(cfg.per_stock_notional / d.pre_close / 100) * 100
            trade = int(base * cfg.trade_fraction / 100) * 100
            if trade < 100:
                continue
            if d.bars[0][1] > d.pre_close * (1 - gap):
                continue  # 未触发深低开
            if smart is not None and not smart.get((code, d.trade_date), False):
                continue  # smart 过滤未通过/特征缺失
            raw = d.bars[1][1]
            if raw >= d.limit_up - 1e-9:
                continue  # 盘中涨停拒单
            fill = raw * (1 + slip)
            lots.append(EntryLot(code, i, d.trade_date, fill, trade,
                                 trade * fill))
    return lots


# ── 退出定价 ──

def resolve_exit(
    mode: str, seq: list[DayBars], i: int, entry_fill: float,
) -> tuple[float, bool] | None:
    """返回 (卖出成交前价, 是否 locked)。无后续交易日返回 None（该模式弃权）."""
    def day(k: int) -> DayBars | None:
        return seq[i + k] if i + k < len(seq) else None

    def fill_close(d: DayBars) -> tuple[float, bool]:
        return d.close, d.close <= d.limit_down + 1e-9

    def fill_1400(d: DayBars) -> tuple[float, bool]:
        for t, o, _c, _l in d.bars:
            if t >= EXIT_BAR:
                if o <= d.limit_down + 1e-9:
                    return fill_close(d)  # 盘中跌停拒单 → 收盘竞价强平
                return o, False
        return fill_close(d)

    if mode == "d0_close":
        return fill_close(day(0))
    if mode == "d0_1400":
        return fill_1400(day(0))
    if mode == "t1_close":
        d = day(1)
        return fill_close(d) if d else None
    if mode == "t1_1400":
        d = day(1)
        return fill_1400(d) if d else None
    if mode == "t2_close":
        d = day(2)
        return fill_close(d) if d else None
    if mode == "cond3_t1":
        d0 = day(0)
        ck = next((c for t, _o, c, _l in d0.bars if t >= CHECK_BAR), d0.close)
        if ck < entry_fill * 0.97:
            d = day(1)
            return fill_close(d) if d else None
        return fill_1400(d0)
    raise ValueError(f"unknown mode: {mode}")


def lot_net_bps(
    lot: EntryLot, sell_raw: float, cfg: IntradayConfig,
) -> tuple[float, float]:
    """返回 (pnl¥, net_bps)，成本模型与引擎 DayRunner 一致."""
    fill = sell_raw * (1 - cfg.slippage_bps / 1e4)
    sell_gross = lot.shares * fill
    sell_fee = sell_gross * (cfg.commission_bps + cfg.stamp_tax_bps) / 1e4
    buy_fee = lot.buy_gross * cfg.commission_bps / 1e4
    pnl = sell_gross - sell_fee - lot.buy_gross - buy_fee
    notional = (lot.buy_gross + sell_gross) / 2
    return pnl, pnl / notional * 1e4


# ── 汇总 ──

def _rows(days, lots, cfg, mode, family) -> list[dict]:
    out = []
    for lot in lots:
        seq = days[lot.ts_code]
        d0 = seq[lot.day_idx]
        res = resolve_exit(mode, seq, lot.day_idx, lot.fill_px)
        if res is None:
            continue
        sell_raw, locked = res
        pnl, bps = lot_net_bps(lot, sell_raw, cfg)
        # 隔夜模式:持有期内的最差低点相对入场价(尾部风险观测)
        hold_days = 1 if mode in ("t1_close", "t1_1400") else (
            2 if mode == "t2_close" else None)
        if mode == "cond3_t1":
            ck = next((c for t, _o, c, _l in d0.bars if t >= CHECK_BAR),
                      d0.close)
            hold_days = 1 if ck < lot.fill_px * 0.97 else 0
        worst = None
        if hold_days:
            lows = [min(b[3] for b in seq[lot.day_idx + k].bars)
                    for k in range(hold_days + 1)
                    if lot.day_idx + k < len(seq)]
            worst = min(lows) / lot.fill_px - 1 if lows else None
        out.append({
            "ts_code": lot.ts_code, "trade_date": lot.trade_date,
            "family": family, "mode": mode, "net_bps": bps, "pnl": pnl,
            "locked": locked,
            "knife": d0.close / d0.pre_close - 1 <= KNIFE_CLOSE,
            "hold_low_bps": None if worst is None else worst * 1e4,
        })
    return out


def calibrate_vs_engine(
    minute: pd.DataFrame, daily: pd.DataFrame, days, lots, cfg,
) -> str:
    """d0_1400 vs 引擎 GapDownSmart(0.03, x14:00) 逐笔对账."""
    from davis_analyzer.intraday.engine import run_backtest
    from davis_analyzer.intraday.strategies import GapDownSmart

    feats = build_features(minute, daily)
    res = run_backtest(minute, daily, [GapDownSmart(0.03, exit_time="14:00")],
                       cfg, features_df=feats)
    eng = {(r.ts_code, r.trade_date): r.net_bps for r in res.itertuples()}
    mine = {(l.ts_code, l.trade_date): None for l in lots}
    diffs, missing = [], 0
    for lot in lots:
        seq = days[lot.ts_code]
        sell_raw, _ = resolve_exit("d0_1400", seq, lot.day_idx, lot.fill_px)
        _, bps = lot_net_bps(lot, sell_raw, cfg)
        ref = eng.get((lot.ts_code, lot.trade_date))
        if ref is None:
            missing += 1
            continue
        diffs.append(abs(bps - float(ref)))
    extra = len(set(eng) - set(mine))
    within = float(np.mean([d < 1.0 for d in diffs])) if diffs else 0.0
    return (
        f"校准: 引擎 {len(eng)} 笔 vs 仿真 {len(lots)} 笔 | "
        f"|Δnet_bps|<1bps 占比 {within:.1%} | 中位Δ {np.median(diffs):.2f}bps | "
        f"最大Δ {max(diffs):.2f}bps | 仿真多/缺 {extra}/{missing} 笔"
    )


def _perf(g: pd.DataFrame) -> dict:
    return {
        "n": len(g), "win%": round((g.pnl > 0).mean() * 100, 1),
        "mean": round(g.net_bps.mean(), 0), "med": round(g.net_bps.median(), 0),
        "P5": round(g.net_bps.quantile(0.05), 0),
    }


def _group_perf(df: pd.DataFrame, keys: list[str], extra: dict | None = None) -> pd.DataFrame:
    rows = []
    for k, g in df.groupby(keys, observed=True):
        if not isinstance(k, tuple):
            k = (k,)
        rows.append({**dict(zip(keys, map(str, k))), **_perf(g),
                     **(extra(g) if extra else {})})
    return pd.DataFrame(rows)


def run_study(
    db_path: str | None = None, out_dir=None, split: str = SPLIT_DATE,
    gap: float = 0.03,
) -> tuple[str, list]:
    import pathlib
    import time as _time

    cfg = IntradayConfig()
    minute, daily = load_inputs(db_path)
    feats = build_features(minute, daily)
    days = build_day_structures(minute, daily)
    smart = _smart_mask(feats)

    lines = ["=== 隔夜残余退出校验 ==="]
    all_rows: list[dict] = []
    for family, mask in (("plain(g3x)", None), ("smart", smart)):
        lots = scan_entries(days, mask, gap, cfg)
        if family.startswith("plain"):
            lines.append(calibrate_vs_engine(minute, daily, days, lots, cfg))
        lines.append(f"[{family}] 入场 {len(lots)} 笔")
        for mode in MODES:
            all_rows += _rows(days, lots, cfg, mode, family)

    df = pd.DataFrame(all_rows)
    df["window"] = np.where(df.trade_date < split, "train", "holdout")
    main = _group_perf(df, ["family", "mode", "window"])
    knife = _group_perf(
        df[df.knife], ["family", "mode"],
        extra=lambda g: {"locked%": round(g.locked.mean() * 100, 0)},
    ).rename(columns={"n": "n_knife"})
    lines += ["", "--- 主表: family × mode × window ---",
              main.to_string(index=False),
              "", "--- 飞刀日切片(当日收盘≤−5%) ---",
              knife.to_string(index=False)]
    ov = df[df["mode"].isin(["t1_close", "t1_1400", "t2_close", "cond3_t1"])]
    if not ov.empty:
        lines += [
            "", "--- 隔夜持有期最差低点相对入场价(bps, 尾部风险) ---",
            (ov.groupby(["family", "mode"])["hold_low_bps"]
               .describe(percentiles=[.05, .25, .5])[["count", "5%", "25%", "50%"]]
               .round(0).to_string()),
        ]

    stamp = _time.strftime("%Y%m%d_%H%M%S")
    out = pathlib.Path(out_dir) if out_dir else (
        pathlib.Path(__file__).parent / "reports")
    out.mkdir(parents=True, exist_ok=True)
    paths = [out / f"overnight_study_lots_{stamp}.csv",
             out / f"overnight_study_summary_{stamp}.csv"]
    df.to_csv(paths[0], index=False, encoding="utf-8-sig")
    main.to_csv(paths[1], index=False, encoding="utf-8-sig")
    for p in paths:
        logger.info("隔夜退出校验导出: {}", p)
    return "\n".join(lines), paths
