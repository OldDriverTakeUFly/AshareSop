"""首板事件 × davis 动量分层研究 (OPEN_NEXT 语义 T+1 开盘溢价).

研究问题
--------
首板打板信号池内, 按事件日 davis 绝对动量分 (60/120/250 日混合) 十分位分层,
T+1 开盘卖出的溢价是否单调? —— 回答「首板池内选 davis 动量高的标的有无帮助」.

口径
----
- 动量分: analyze_momentum().absolute_momentum_score, 事件日 as_of 截面,
  纯 daily_price 缓存 fast path (不打 Tushare API); 不含 RS 百分位混合
  (RS 需逐事件日全宇宙截面, 成本过高, 对分层单调性影响有限).
- 溢价: T+1 开盘 / T+1 的 pre_close - 1 (交易所除权口径, 含送转分红),
  绕开 daily_price.adj_factor —— 该列在 2026-07/08 被增量回补污染
  (相邻日跳变>3倍共 16568 行/5533 票), 2026-06-30 前历史干净,
  故默认事件截止 2026-06-30 (--end 可改).
- 买入: 事件日收盘 (≈涨停价, pct_chg 达板 sanity 过滤); 卖出: T+1 开盘.
- T+1 开盘跌停 (悲观语义拒卖): 主分层剔除, 单独按层统计占比与溢价.
- 成本: 双边 45bps = commission 2.5×2 + stamp 10 + slippage 10×2
  (与 board_chasing_first 适配器 cost_model 同款).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path("/home/leo/Projects/CodeAgentDashboard")
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from loguru import logger
from scipy import stats

from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.tushare_client import TushareClient

DB_PATH = PROJECT_ROOT / "storage/database/market_data.db"
DEFAULT_OUT = PROJECT_ROOT / "docs/回测记录/首板动量分层研究_2026-08-22.md"
DETAIL_CSV = Path("/tmp/first_board_momentum_detail.csv")

# 双边成本 45bps (commission 2.5×2 + stamp 10 + slippage 10×2)
TOTAL_COST = 0.0045
# T+1 与事件日间隔超过 15 个自然日视为长停牌, 剔除
MAX_GAP_DAYS = 15
# adj_factor 干净边界(2026-07 起被污染, 见模块 docstring)
CLEAN_END = "20260630"


# ── 代码后缀与涨停幅度 ──

def to_suffixed(code: str) -> str:
    if "." in code:
        return code
    if code[:2] in ("60", "68"):
        return code + ".SH"
    if code[:2] in ("00", "30"):
        return code + ".SZ"
    return code + ".BJ"


def limit_ratio(code: str) -> float:
    """当日有效涨停幅度(近似): 创业/科创 20%, 北交 30%, 主板 10%."""
    if code[:2] in ("30", "68"):
        return 0.20
    if code[:2] in ("82", "92") or code[0] in ("8", "4"):
        return 0.30
    return 0.10


# ── 数据装载 ──

def load_first_board_events(conn: sqlite3.Connection, end: str) -> pd.DataFrame:
    rows = conn.execute(
        "SELECT ts_code, REPLACE(trade_date, '-', '') AS d "
        "FROM limit_pool "
        "WHERE pool_kind='limit_up' AND consecutive_boards=1 "
        "AND trade_date >= '2021-01-01' AND REPLACE(trade_date, '-', '') <= ?",
        (end,),
    ).fetchall()
    df = pd.DataFrame(rows, columns=["raw_code", "event_date"]).drop_duplicates()
    df["ts_code"] = df["raw_code"].map(to_suffixed)
    df["year"] = df["event_date"].str[:4]
    return df


def score_momentum(
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], float]:
    """逐 (code, event_date) 打 davis 绝对动量分; 数据不足返回缺省."""
    client = TushareClient()
    out: dict[tuple[str, str], float | None] = {}
    t0 = time.time()
    for i, (code, d) in enumerate(pairs):
        try:
            sig = analyze_momentum(
                client, code, today=datetime.strptime(d, "%Y%m%d").date())
            out[(code, d)] = sig.absolute_momentum_score if sig else None
        except Exception as e:  # 单票失败不中断研究
            logger.warning("momentum 失败 {} {}: {}", code, d, e)
            out[(code, d)] = None
        if (i + 1) % 5000 == 0:
            rate = (i + 1) / (time.time() - t0)
            logger.info("momentum 进度 {}/{} ({:.0f}/s)", i + 1, len(pairs), rate)
    client._cache_conn.close()
    return out


def enrich_prices(
    conn: sqlite3.Connection, events: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """为每事件取 T 收盘(买价)与 T+1 开盘(卖价), 算溢价与跌停拒卖标记."""
    recs: list[dict] = []
    drop = {"无T行": 0, "无T+1行": 0, "长停牌": 0, "炸板/未达板": 0, "溢价超限幅": 0}
    cur = conn.cursor()
    for r in events.itertuples(index=False):
        rows = cur.execute(
            "SELECT trade_date, open, close, pre_close, adj_factor, pct_chg "
            "FROM daily_price "
            "WHERE ts_code=? AND trade_date>=? ORDER BY trade_date LIMIT 2",
            (r.ts_code, r.event_date),
        ).fetchall()
        if not rows or rows[0][0] != r.event_date:
            drop["无T行"] += 1
            continue
        if len(rows) < 2:
            drop["无T+1行"] += 1
            continue
        (_, _, close_t, _, _, pct_chg), (d2, open2, _, pre_close2, _, _) = rows
        if close_t is None or pct_chg is None or open2 is None or pre_close2 is None:
            drop["无T行"] += 1
            continue
        # sanity: 事件日确以接近涨停幅度收盘(尾部炸板入库等剔除)
        thr = limit_ratio(r.ts_code) * 100 - 1.1
        if float(pct_chg) < thr:
            drop["炸板/未达板"] += 1
            continue
        gap = (datetime.strptime(d2, "%Y%m%d") -
               datetime.strptime(r.event_date, "%Y%m%d")).days
        if gap > MAX_GAP_DAYS:
            drop["长停牌"] += 1
            continue
        ratio = limit_ratio(r.ts_code)
        # 溢价 = T+1开盘 / T+1的pre_close - 1 (交易所除权口径, 绕开被污染的 adj_factor)
        prem_gross = float(open2) / float(pre_close2) - 1.0
        # 开盘涨跌幅受 ±板块限幅约束, 超限即坏行
        if abs(prem_gross) > ratio + 0.02:
            drop["溢价超限幅"] += 1
            continue
        # T+1 开盘跌停(悲观语义拒卖)
        limit_down = prem_gross <= -(ratio - 0.006)
        recs.append({
            "ts_code": r.ts_code, "event_date": r.event_date, "year": r.year,
            "score": r.score, "prem_gross": prem_gross,
            "prem_net": prem_gross - TOTAL_COST, "limit_down": limit_down,
        })
    return pd.DataFrame(recs), drop


# ── 统计 ──

def decile_table(df: pd.DataFrame) -> pd.DataFrame:
    valid = df[~df["limit_down"]].copy()
    valid["decile"] = pd.qcut(valid["score"], 10, labels=False, duplicates="drop") + 1
    g = valid.groupby("decile")
    tab = pd.DataFrame({
        "样本": g.size(),
        "动量分均值": g["score"].mean().round(1),
        "毛溢价均值": (g["prem_gross"].mean() * 100).round(2),
        "净溢价均值": (g["prem_net"].mean() * 100).round(2),
        "毛溢价中位": (g["prem_gross"].median() * 100).round(2),
        "胜率(毛>0)": (g["prem_gross"].apply(lambda s: (s > 0).mean()) * 100).round(1),
    })
    # 跌停拒卖率按同一十分位边界统计(含跌停样本)
    df2 = df.copy()
    valid2 = df2[~df2["limit_down"]]
    _, bins = pd.qcut(valid2["score"], 10, retbins=True)
    df2["decile"] = pd.cut(
        df2["score"], bins=bins, labels=False, include_lowest=True) + 1
    tab["T+1跌停率%"] = (df2.groupby("decile")["limit_down"].mean() * 100).round(1)
    return tab


def yearly_stability(df: pd.DataFrame) -> pd.DataFrame:
    valid = df[~df["limit_down"]].copy()
    rows = []
    for yr, g in valid.groupby("year"):
        rho, p = stats.spearmanr(g["score"], g["prem_gross"])
        hi = g[g["score"] >= g["score"].quantile(0.9)]["prem_net"].mean() * 100
        lo = g[g["score"] <= g["score"].quantile(0.1)]["prem_net"].mean() * 100
        rows.append({"年份": yr, "样本": len(g), "Spearman": round(rho, 4),
                     "p值": f"{p:.2e}", "D10净%": round(hi, 2), "D1净%": round(lo, 2),
                     "D10-D1": round(hi - lo, 2)})
    return pd.DataFrame(rows)


def md_table(df: pd.DataFrame) -> str:
    return df.to_markdown(index=False)


def write_report(out_path: Path, tab: pd.DataFrame, yearly: pd.DataFrame,
                 valid: pd.DataFrame, drop: dict[str, int],
                 rho: float, p: float, n_total: int, sampled: bool,
                 end_str: str) -> None:
    hi = valid[valid["score"] >= valid["score"].quantile(0.90)]
    lo = valid[valid["score"] <= valid["score"].quantile(0.10)]
    lines = [
        "# 首板 × davis 动量分层研究 (T+1 开盘溢价)",
        "",
        f"- 事件范围: 2021-01 → {end_str} 全历史首板"
        f"(limit_up 池, consecutive_boards=1)"
        f"{'，本研究为抽样烟测' if sampled else ''}，事件对 {n_total} 条",
        f"- 口径: 溢价=T+1开盘/T+1的pre_close-1(交易所除权口径, 绕开被污染的 adj_factor);",
        "  双边成本 45bps; 动量分=事件日 davis 绝对动量(60/120/250 日混合, 不含 RS);",
        "  T+1 跌停开盘样本剔出主表",
        f"- 剔除: {drop}",
        f"- 全样本 Spearman(动量分, 毛溢价) = {rho:.4f} (p={p:.2e}, n={len(valid)})",
        "",
        "## 十分位分层(主表, 剔 T+1 跌停开盘)",
        "",
        md_table(tab),
        "",
        "## 分年稳健性",
        "",
        md_table(yearly),
        "",
        "## 高/低十分位对比(净溢价)",
        "",
        f"- D10(高动量): 均值 {hi['prem_net'].mean()*100:.2f}%, 中位 {hi['prem_net'].median()*100:.2f}%, n={len(hi)}",
        f"- D1(低动量): 均值 {lo['prem_net'].mean()*100:.2f}%, 中位 {lo['prem_net'].median()*100:.2f}%, n={len(lo)}",
        "",
        "> 诚实边界: 分层相关性≠因果; 未控制板块效应/流通市值等混杂;",
        "> OPEN_NEXT 一日持有不涉 regime 过滤, 与 first_board preset(回暖/高潮)实盘口径有差。",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("报告已写入: {}", out_path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0, help="抽样烟测条数, 0=全量")
    ap.add_argument("--end", type=str, default=CLEAN_END,
                    help="事件截止日 YYYYMMDD (默认 adj_factor 干净边界)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    ev = load_first_board_events(conn, args.end)
    if args.sample:
        ev = ev.sample(min(args.sample, len(ev)), random_state=7).reset_index(drop=True)
        logger.info("抽样模式: {} 事件对", len(ev))
    logger.info("首板事件对: {}", len(ev))

    pairs = list(zip(ev["ts_code"], ev["event_date"]))
    scores = score_momentum(pairs)
    ev["score"] = [scores[(c, d)] for c, d in pairs]
    n_no_score = int(ev["score"].isna().sum())
    ev = ev.dropna(subset=["score"])
    logger.info("动量分缺失剔除: {} (剩余 {})", n_no_score, len(ev))

    df, drop = enrich_prices(conn, ev)
    conn.close()
    logger.info("价格剔除: {} → 有效事件 {}", drop, len(df))

    valid = df[~df["limit_down"]]
    rho, p = stats.spearmanr(valid["score"], valid["prem_gross"])
    tab = decile_table(df)
    yearly = yearly_stability(df)
    print("\n== 十分位分层(剔 T+1 跌停开盘) ==")
    print(tab.to_string())
    print("\n== 分年稳健性 ==")
    print(yearly.to_string())
    print(f"\nSpearman(动量, 毛溢价) = {rho:.4f} (p={p:.2e}, n={len(valid)})")

    df.to_csv(DETAIL_CSV, index=False)
    logger.info("明细已落盘: {}", DETAIL_CSV)
    write_report(args.out, tab, yearly, valid, drop, rho, p,
                 n_total=len(ev) + n_no_score, sampled=bool(args.sample),
                 end_str=args.end)


if __name__ == "__main__":
    main()
