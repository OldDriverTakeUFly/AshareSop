"""做T回测的数据装载、汇总与导出."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from loguru import logger

from davis_analyzer.intraday import db
from davis_analyzer.intraday.engine import IntradayConfig, run_backtest

RT_COST_BPS = 45.0  # 佣金2.5×2 + 印花10 + 滑点10×2


def load_inputs(
    db_path: str | None = None, freq: str = "5min"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """研究库分钟线 + 生产库日线（pre_close/close/high/low）."""
    rcon = db.connect(db_path)
    try:
        codes = [
            r[0] for r in rcon.execute(
                "SELECT DISTINCT ts_code FROM backfill_chunk ORDER BY ts_code"
            ).fetchall()
        ]
        rng = rcon.execute(
            "SELECT MIN(start_date), MAX(end_date) FROM backfill_chunk"
        ).fetchone()
        minute = db.read_bars(rcon, codes, rng[0], rng[1], freq=freq)
    finally:
        rcon.close()
    from davis_analyzer.limitup.db import connect as market_connect

    mcon = market_connect()
    try:
        ph = ",".join("?" * len(codes))
        daily = pd.read_sql_query(
            f"SELECT ts_code, trade_date, pre_close, close, high, low "
            f"FROM daily_price WHERE ts_code IN ({ph}) "
            "AND trade_date>=? AND trade_date<=?",
            mcon, params=(*codes, rng[0], rng[1]),
        )
    finally:
        mcon.close()
    daily = daily[(daily.pre_close > 0) & (daily.close > 0)]
    return minute, daily


def perfect_ceiling(minute: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """分钟口径的完美做T上限：max(close/分钟低, 分钟高/close) − 往返成本."""
    agg = minute.groupby(["ts_code", "trade_date"]).agg(
        m_high=("high", "max"), m_low=("low", "min")).reset_index()
    df = agg.merge(
        daily[["ts_code", "trade_date", "close"]], on=["ts_code", "trade_date"]
    )
    t_long = df["close"] / df["m_low"] - 1.0
    t_short = df["m_high"] / df["close"] - 1.0
    df["ceiling_net_bps"] = (
        pd.concat([t_long, t_short], axis=1).max(axis=1) * 1e4 - RT_COST_BPS
    )
    return df[["ts_code", "trade_date", "ceiling_net_bps"]]


def summarize(
    results: pd.DataFrame, minute: pd.DataFrame, daily: pd.DataFrame,
    config: IntradayConfig, n_codes: int,
) -> str:
    """生成文字版汇总（含与分钟完美上限的捕获率对照）."""
    if results.empty:
        return "回测无成交记录"
    ceil = perfect_ceiling(minute, daily)
    n_days = daily.groupby("ts_code").size().median()
    years = max(n_days / 250.0, 1e-9)
    lines = [
        f"底仓假设: 每股 {config.per_stock_notional:,.0f} 元市值 | "
        f"每次动用 {config.trade_fraction:.0%} | 往返成本 {RT_COST_BPS:.0f}bps",
        f"宇宙 {n_codes} 只 | 每股中位 {int(n_days)} 个交易日 | "
        f"完美上限中位 {ceil.ceiling_net_bps.median():.0f}bps\n",
    ]
    for name, g in results.groupby("strategy"):
        win = (g.pnl > 0).mean()
        active = g.groupby("ts_code").size().median()
        base_notional_total = n_codes * config.per_stock_notional
        annual_addon = g.pnl.sum() / base_notional_total / years
        m = g.merge(ceil, on=["ts_code", "trade_date"], how="left")
        capture = (
            m.net_bps.median() / m.ceiling_net_bps.median()
            if m.ceiling_net_bps.median() > 0 else float("nan")
        )
        lines.append(
            f"[{name}]\n"
            f"  交易股票日 {len(g):,} | 每股中位活跃 {active:.0f} 天 | 胜率 {win:.1%}\n"
            f"  单次净收益 bps: mean {g.net_bps.mean():+.0f} / median {g.net_bps.median():+.0f} "
            f"/ P25 {g.net_bps.quantile(.25):+.0f} / P75 {g.net_bps.quantile(.75):+.0f}\n"
            f"  总净利 ¥{g.pnl.sum():,.0f} | 相对底仓年化增厚 {annual_addon:+.2%} | "
            f"对完美上限捕获率 {capture:.0%}\n"
            f"  盘中涨跌停拒单 {int(g.n_rejected.sum())} 次 | "
            f"竞价强平越过涨跌停 {int(g.locked_eod_fill.sum())} 股\n"
        )
    return "\n".join(lines)


def export_csv(results: pd.DataFrame, out_dir: str | Path | None = None) -> Path:
    """明细导出 intraday/reports/，返回文件路径."""
    import time as _time

    out_dir = Path(out_dir) if out_dir else (
        Path(__file__).parent / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"tbacktest_{_time.strftime('%Y%m%d_%H%M%S')}.csv"
    results.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("做T回测明细导出: {}", path)
    return path


def run_and_report(
    strategies: list, config: IntradayConfig | None = None,
    db_path: str | None = None, out_dir: str | Path | None = None,
) -> tuple[pd.DataFrame, str, Path]:
    config = config or IntradayConfig()
    minute, daily = load_inputs(db_path)
    # 特征构建供 GapDownSmart 等过滤型策略使用（无过滤器策略不受影响）
    from davis_analyzer.intraday.features import build_features

    feats = build_features(minute, daily)
    results = run_backtest(minute, daily, strategies, config, features_df=feats)
    text = summarize(results, minute, daily, config, daily.ts_code.nunique())
    path = export_csv(results, out_dir)
    return results, text, path
