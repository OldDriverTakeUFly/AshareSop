"""市场环境三轴（指数趋势/市场宽度/情绪周期）与 regime 四档.

阈值为先验固定常量（规格 §6.5），研究期一次校准后冻结，禁止滚动拟合。
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.limitup import db

REGIME_FREEZE = -0.02   # premium < -2% → 冰点候选
REGIME_COLD_COUNT = 30
REGIME_HOT_BOARDS = 7
REGIME_HOT_COUNT = 120
REGIME_COOL_PREMIUM = 0.0
REGIME_COOL_PROMO12 = 0.30


def classify_regime(row: pd.Series) -> str:
    if _lt(row.get("premium"), REGIME_FREEZE) or _le(row.get("limit_up_count"), REGIME_COLD_COUNT):
        return "冰点"
    if _ge(row.get("max_boards"), REGIME_HOT_BOARDS) or _ge(row.get("limit_up_count"), REGIME_HOT_COUNT):
        return "高潮"
    if _lt(row.get("premium"), REGIME_COOL_PREMIUM) or _lt(row.get("promo_12"), REGIME_COOL_PROMO12):
        return "退潮"
    return "回暖"


def _lt(v: object, thr: float) -> bool:
    return bool(pd.notna(v) and float(v) < thr)  # type: ignore[arg-type]


def _le(v: object, thr: float) -> bool:
    return bool(pd.notna(v) and float(v) <= thr)  # type: ignore[arg-type]


def _ge(v: object, thr: float) -> bool:
    return bool(pd.notna(v) and float(v) >= thr)  # type: ignore[arg-type]


def _limit_axes(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    lp = db.read_limit_pool(conn, start, end, pool_kind="limit_up")
    broken = db.read_limit_pool(conn, start, end, pool_kind="broken")
    rows: dict[str, dict] = {}
    for d, g in lp.groupby("trade_date"):
        rows.setdefault(d, {})["limit_up_count"] = len(g)
        rows.setdefault(d, {})["lianban_count"] = int((g["consecutive_boards"] >= 2).sum())
        rows.setdefault(d, {})["max_boards"] = int(g["consecutive_boards"].max())
    for d, g in broken.groupby("trade_date"):
        rows.setdefault(d, {})["broken_n"] = len(g)
    if not rows:
        return pd.DataFrame(
            columns=["trade_date", "limit_up_count", "lianban_count", "max_boards",
                     "broken_rate"]
        )
    df = pd.DataFrame([{"trade_date": d, **v} for d, v in sorted(rows.items())])
    for c in ("limit_up_count", "lianban_count", "max_boards", "broken_n"):
        if c not in df.columns:
            df[c] = np.nan
    total = (df["limit_up_count"].fillna(0) + df["broken_n"].fillna(0)).replace(0, np.nan)
    df["broken_rate"] = df["broken_n"].fillna(0) / total
    df = df.drop(columns=["broken_n"], errors="ignore")
    return df


def _promotion_axes(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """promo_12/23/34 by pairing each pool row with its next trading day row.

    T 日池的晋级结果在 T+1 才可观测，按交易日映射归属到 T+1 日期
    （与 _premium_axes 的 nxt 映射同法），消除 regime 打板决策的前视偏差；
    窗口末日的晋级结果不可观测，直接丢弃。
    """
    lp = db.read_limit_pool(conn, start, end, pool_kind="limit_up")
    if lp.empty:
        return pd.DataFrame(columns=["trade_date", "promo_12", "promo_23", "promo_34"])
    cal = db.trading_dates(conn, start, end)
    nxt = {d: cal[i + 1] for i, d in enumerate(cal[:-1])}
    rank = {d: i for i, d in enumerate(cal)}
    lp["rank"] = lp["trade_date"].map(rank)
    lp = lp.sort_values(["ts_code", "rank"])
    g = lp.groupby("ts_code", sort=False)
    lp["next_boards"] = g["consecutive_boards"].shift(-1)
    lp["next_rank"] = g["rank"].shift(-1)
    ok = (lp["next_rank"] == lp["rank"] + 1) & (
        lp["next_boards"] == lp["consecutive_boards"] + 1
    )
    out_rows = []
    for d, g2 in lp.groupby("trade_date"):
        target = nxt.get(d)
        if target is None:
            continue  # 窗口末日或日历外日期：T+1 不可观测
        row = {"trade_date": target}
        for base in (1, 2, 3):
            sub = g2[g2["consecutive_boards"] == base]
            row[f"promo_{base}{base + 1}"] = (
                float(ok.loc[sub.index].mean()) if len(sub) else np.nan
            )
        out_rows.append(row)
    if not out_rows:
        # 单日窗口等场景：所有池日均无可观测 T+1 → 带列空帧（merge 不炸）
        return pd.DataFrame(columns=["trade_date", "promo_12", "promo_23", "promo_34"])
    return pd.DataFrame(out_rows)


def _premium_axes(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """昨日涨停池今日开盘溢价/红盘率，归属到 T+1 日期.

    limit_pool.ts_code 无后缀而 daily_price.ts_code 带后缀，join 前
    在 CTE 内按 db.to_suffixed_code 同规则补全代码后缀。
    """
    sql = (
        "WITH pool AS (SELECT trade_date, ts_code || CASE "
        "WHEN substr(ts_code,1,2) IN ('60','68') THEN '.SH' "
        "WHEN substr(ts_code,1,2) IN ('00','30') THEN '.SZ' "
        "WHEN substr(ts_code,1,2) = '92' THEN '.BJ' "
        "WHEN substr(ts_code,1,1) IN ('8','4') THEN '.BJ' "
        "ELSE '' END AS code FROM limit_pool "
        "WHERE pool_kind = 'limit_up' AND trade_date >= ? AND trade_date <= ?) "
        "SELECT lp.trade_date AS d0, COUNT(*) AS n, "
        "AVG(1.0 * dp1.open / dp0.close - 1) AS premium, "
        "AVG(CASE WHEN dp1.open > dp0.close THEN 1.0 ELSE 0 END) AS red_rate "
        "FROM pool lp "
        "JOIN daily_price dp0 ON dp0.ts_code = lp.code "
        "  AND dp0.trade_date = REPLACE(lp.trade_date, '-', '') "
        "JOIN daily_price dp1 ON dp1.ts_code = lp.code AND dp1.trade_date = "
        "  (SELECT MIN(x.trade_date) FROM daily_price x "
        "   WHERE x.ts_code = lp.code AND x.trade_date > REPLACE(lp.trade_date,'-','')) "
        "GROUP BY lp.trade_date"
    )
    raw = pd.read_sql_query(sql, conn, params=(db.to_dash_date(start), db.to_dash_date(end)))
    if raw.empty:
        return pd.DataFrame(columns=["trade_date", "premium", "red_rate"])
    cal = db.trading_dates(conn, start, end)
    nxt = {}
    for i, d in enumerate(cal[:-1]):
        nxt[db.to_dash_date(d)] = cal[i + 1]
    rows = []
    for _, r in raw.iterrows():
        target = nxt.get(r["d0"])
        if target:
            rows.append({"trade_date": target, "premium": r["premium"],
                         "red_rate": r["red_rate"]})
    if not rows:
        return pd.DataFrame(columns=["trade_date", "premium", "red_rate"])
    return pd.DataFrame(rows)


def _breadth_axes(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    breadth = pd.read_sql_query(
        "SELECT trade_date, "
        "SUM(CASE WHEN close > pre_close THEN 1 ELSE 0 END) AS up_cnt, "
        "COUNT(*) AS total, SUM(amount) AS amount_sum "
        "FROM daily_price WHERE trade_date >= ? AND trade_date <= ? "
        "GROUP BY trade_date",
        conn, params=(db.normalize_date(start), db.normalize_date(end)),
    )
    breadth["up_down_ratio"] = breadth["up_cnt"] / breadth["total"]
    nh = pd.read_sql_query(
        "SELECT trade_date, AVG(CASE WHEN close >= hh20 THEN 1.0 ELSE 0 END) "
        "AS new_high_ratio FROM ("
        "  SELECT trade_date, close, MAX(close) OVER "
        "  (PARTITION BY ts_code ORDER BY trade_date ROWS 19 PRECEDING) AS hh20 "
        "  FROM daily_price WHERE trade_date >= ? AND trade_date <= ?) "
        "GROUP BY trade_date",
        conn, params=(db.normalize_date(start), db.normalize_date(end)),
    )
    df = breadth.merge(nh, on="trade_date", how="left").drop(columns=["up_cnt", "total"])
    return df


def _index_axes(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    frames = []
    for code in ("000001.SH", "399001.SZ", "399006.SZ"):
        idx = db.read_index_daily(conn, code, start, end)
        if idx.empty:
            continue
        ma20 = idx["close"].rolling(20).mean()
        ma60 = idx["close"].rolling(60).mean()
        idx["bull"] = (idx["close"] > ma20) & (ma20 > ma60)
        frames.append(
            idx[["trade_date", "bull"]].rename(columns={"bull": f"bull_{len(frames)}"})
        )
    if not frames:
        return pd.DataFrame(columns=["trade_date", "index_ma_bull"])
    merged = frames[0]
    for f in frames[1:]:
        merged = merged.merge(f, on="trade_date", how="outer")
    bull_cols = [c for c in merged.columns if c.startswith("bull")]
    merged["index_ma_bull"] = merged[bull_cols].sum(axis=1) >= (len(bull_cols) / 2)
    return merged[["trade_date", "index_ma_bull"]]


def build_market_regime(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    cal = db.trading_dates(conn, start, end)
    base = pd.DataFrame({"trade_date": cal})
    parts = [
        _limit_axes(conn, start, end),
        _promotion_axes(conn, start, end),
        _premium_axes(conn, start, end),
        _breadth_axes(conn, start, end),
        _index_axes(conn, start, end),
    ]
    df = base
    for p in parts:
        df = df.merge(p, on="trade_date", how="left")
    df["regime_label"] = df.apply(classify_regime, axis=1)
    logger.info("market regime: {} 天 [{} → {}]", len(df), start, end)
    return df
