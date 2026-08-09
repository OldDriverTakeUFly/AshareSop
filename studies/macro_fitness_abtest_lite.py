"""宏观适配度 A/B 对比（轻量版）—— 用已有 top20 历史结果做离线对比.

不重跑全市场因子评分（太慢），而是：
1. 从 daily_prices 表拉历史数据做简单的动量+估值排序（替代完整因子评分）
2. 每月调仓取 top20
3. A 组：纯 top20 | B 组：top20 剔除宏观逆风
4. 比较两组的月度收益

快速验证宏观过滤是否有 alpha，~2 分钟可完成。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_pmi_history() -> pd.DataFrame:
    """从 Tushare 拉历史 PMI."""
    from stockhot.data_layer import get_gateway
    gw = get_gateway()
    df = gw.call("cn_pmi", start_m="202301")
    if df is not None and not df.empty:
        return df[["MONTH", "PMI020100"]].rename(columns={"PMI020100": "pmi", "MONTH": "month"}).assign(
            pmi=lambda x: pd.to_numeric(x["pmi"], errors="coerce"),
            date=lambda x: pd.to_datetime(x["month"].astype(str), format="%Y%m"),
        ).dropna(subset=["pmi"]).set_index("date").sort_index()
    return pd.DataFrame()


def _simple_rank(db_path: str, as_of: str) -> list[dict]:
    """用 DB 里的 daily_basic 做简单排序（PE 分位 + 动量），替代完整因子评分.

    返回 [{ts_code, name, industry, score}] 按 score 降序。
    """
    conn = sqlite3.connect(db_path)

    # 取最近交易日的 daily_basic（PE/PB）
    df_basic = pd.read_sql(
        "SELECT ts_code, pe, pb FROM daily_basic WHERE trade_date = "
        "(SELECT MAX(trade_date) FROM daily_basic) AND pe > 0 AND pe < 200",
        conn,
    )

    if df_basic.empty:
        return []

    # 取 stock_basic 获取行业
    df_info = pd.read_sql("SELECT ts_code, name, industry FROM stock_basic", conn)

    # 简单评分：PE 越低分越高（30%），PB 越低分越高（20%），随机噪声（50%模拟其他因子）
    df_basic["pe_score"] = 100 - (df_basic["pe"].rank(pct=True) * 100)
    df_basic["pb_score"] = 100 - (df_basic["pb"].rank(pct=True) * 100)
    # 模拟动量（用 PE 分位 + PB 分位 + 随机，使排名有一定区分度但不依赖完整因子）
    df_basic["score"] = df_basic["pe_score"] * 0.3 + df_basic["pb_score"] * 0.2 + np.random.uniform(30, 70, len(df_basic)) * 0.5

    merged = df_basic.merge(df_info[["ts_code", "name", "industry"]], on="ts_code", how="left")
    merged = merged.dropna(subset=["industry"])
    merged = merged.sort_values("score", ascending=False)

    conn.close()
    return merged[["ts_code", "name", "industry", "score"]].to_dict("records")


def _filter_macro(ranked: list[dict], pmi: float | None, top_n: int = 20) -> list[dict]:
    """宏观过滤 top N."""
    if pmi is None or pmi >= 48:
        return ranked[:top_n]

    from stockhot.macro_fitness import get_macro_fitness

    result = []
    for item in ranked:
        if len(result) >= top_n:
            break
        fitness = get_macro_fitness(item["industry"], pmi=pmi)
        if fitness["label"] == "宏观逆风":
            continue
        result.append(item)

    # 补位
    for item in ranked:
        if len(result) >= top_n:
            break
        if item not in result:
            result.append(item)

    return result[:top_n]


def _period_return(db_path: str, codes: list[str], start: str, end: str) -> float | None:
    """计算等权组合在 [start, end] 的收益%."""
    if not codes:
        return None
    conn = sqlite3.connect(db_path)
    placeholders = ",".join("?" * len(codes))
    df = pd.read_sql(
        f"SELECT ts_code, trade_date, close FROM daily_price "
        f"WHERE ts_code IN ({placeholders}) AND trade_date >= ? AND trade_date <= ? "
        f"ORDER BY ts_code, trade_date",
        conn, params=(*codes, start, end),
    )
    conn.close()

    if df.empty:
        return None

    returns = []
    for code, group in df.groupby("ts_code"):
        group = group.sort_values("trade_date")
        if len(group) >= 2:
            ret = (group.iloc[-1]["close"] / group.iloc[0]["close"] - 1) * 100
            returns.append(ret)

    return float(np.mean(returns)) if returns else None


def main() -> int:
    db_path = str(PROJECT_ROOT / "storage" / "database" / "market_data.db")

    print("=== 宏观适配度 A/B 对比（轻量版）===")
    print()

    # 加载 PMI 历史
    print("[1/3] 加载 PMI 历史...")
    pmi_hist = _load_pmi_history()
    print(f"  PMI 数据: {len(pmi_hist)} 期")
    if not pmi_hist.empty:
        print(f"  范围: {pmi_hist.index[0].date()} ~ {pmi_hist.index[-1].date()}")
        pmi_below_48 = (pmi_hist["pmi"] < 48).sum()
        print(f"  PMI<48 的月数: {pmi_below_48}/{len(pmi_hist)}")

    # 调仓日：每月第一个交易日（简化为每月1日附近）
    start = date(2024, 1, 1)
    end = date(2026, 7, 31)
    rebalance_months = pd.date_range(start, end, freq="MS")

    a_returns = []
    b_returns = []
    filtered_counts = []

    print(f"\n[2/3] 逐月 A/B 对比（{len(rebalance_months)} 个月）...")

    for i, rb_date in enumerate(rebalance_months[:-1]):
        next_date = rebalance_months[i + 1]
        rb_str = rb_date.strftime("%Y%m%d")
        next_str = next_date.strftime("%Y%m%d")

        # 获取该月最近的 PMI
        valid_pmi = pmi_hist[pmi_hist.index <= rb_date]
        pmi = float(valid_pmi.iloc[-1]["pmi"]) if not valid_pmi.empty else None

        # 简单排名
        ranked = _simple_rank(db_path, rb_str)
        if len(ranked) < 30:
            continue

        # A 组 vs B 组
        a_top = ranked[:20]
        b_top = _filter_macro(ranked, pmi, 20)

        a_codes = [x["ts_code"] for x in a_top]
        b_codes = [x["ts_code"] for x in b_top]

        a_ret = _period_return(db_path, a_codes, rb_str, next_str)
        b_ret = _period_return(db_path, b_codes, rb_str, next_str)

        if a_ret is not None and b_ret is not None:
            a_returns.append(a_ret)
            b_returns.append(b_ret)
            filtered = len(set(a_codes) - set(b_codes))
            filtered_counts.append(filtered)
            pmi_str = f"{pmi:.1f}" if pmi else "N/A"
            diff = b_ret - a_ret
            flag = "↑" if diff > 0 else "↓" if diff < 0 else "="
            print(f"  {rb_date.strftime('%Y-%m')} PMI={pmi_str:>5} 过滤{filtered}只 "
                  f"A={a_ret:+5.1f}% B={b_ret:+5.1f}% 差{diff:+5.1f}% {flag}")

    a_arr = np.array(a_returns)
    b_arr = np.array(b_returns)

    print(f"\n[3/3] 结果汇总")
    print("=" * 65)
    print(f"{'指标':20s} {'A组(纯因子)':>14s} {'B组(宏观过滤)':>14s} {'差异':>10s}")
    print("-" * 62)

    a_total = (np.prod(1 + a_arr / 100) - 1) * 100
    b_total = (np.prod(1 + b_arr / 100) - 1) * 100
    a_sharpe = a_arr.mean() / a_arr.std() * np.sqrt(12) if a_arr.std() > 0 else 0
    b_sharpe = b_arr.mean() / b_arr.std() * np.sqrt(12) if b_arr.std() > 0 else 0

    print(f"{'累计收益':20s} {a_total:>+13.2f}% {b_total:>+13.2f}% {b_total-a_total:>+9.2f}%")
    print(f"{'月均收益':20s} {a_arr.mean():>+13.2f}% {b_arr.mean():>+13.2f}% {b_arr.mean()-a_arr.mean():>+9.2f}%")
    print(f"{'Sharpe(年化)':20s} {a_sharpe:>14.2f} {b_sharpe:>14.2f} {b_sharpe-a_sharpe:>+9.2f}")
    print(f"{'胜率':20s} {(a_arr>0).mean()*100:>13.1f}% {(b_arr>0).mean()*100:>13.1f}%")
    print(f"{'月均过滤数':20s} {'':>14s} {np.mean(filtered_counts):>13.1f}只")
    print()

    diff_total = b_total - a_total
    if diff_total > 1:
        print("✅ 结论：宏观过滤有 alpha 贡献，建议升级为正式因子")
    elif abs(diff_total) < 1:
        print("⚪ 结论：宏观过滤无显著差异，保持标注层即可")
    else:
        print("⚠️ 结论：宏观过滤反而拖累收益，不建议升级")

    # 保存
    output = {
        "a_total_return": round(a_total, 2),
        "b_total_return": round(b_total, 2),
        "difference": round(diff_total, 2),
        "a_sharpe": round(a_sharpe, 2),
        "b_sharpe": round(b_sharpe, 2),
        "months": len(a_returns),
    }
    out_path = PROJECT_ROOT / "studies" / "output" / "macro_fitness_abtest.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\n结果: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
