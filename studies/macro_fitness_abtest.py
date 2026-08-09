"""宏观适配度 A/B 对比回测 — 验证宏观因子是否有 alpha 贡献.

对比：
  A 组（基准）：top20 纯因子选股（当前逻辑）
  B 组（宏观过滤）：top20 剔除"宏观逆风"标的后补位

如果 B 组 Sharpe/收益显著优于 A 组 → 宏观因子有 alpha，值得升级为正式因子。
如果无显著差异 → 保持标注层不动。

方法：
  不重跑完整回测（耗时长），而是用历史 score_universe_at 的结果做
  离线模拟——每个调仓日对 top20 做宏观过滤，比较两组的持有期收益。

Usage:
    .venv/bin/python studies/macro_fitness_abtest.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_macro_history() -> pd.DataFrame:
    """加载 macro_indicator 表的 PMI 历史序列（用于历史回测时的宏观状态判断）.

    如果 macro_indicator 表数据不足（刚开始持久化），从 Tushare 拉取历史 PMI。
    """
    import sqlite3
    from stockhot.data_layer import MARKET_DB_PATH

    with sqlite3.connect(str(MARKET_DB_PATH)) as conn:
        df = pd.read_sql(
            "SELECT report_date, value FROM macro_indicator WHERE indicator_name='PMI' "
            "ORDER BY report_date", conn
        )

    if len(df) >= 6:
        return df.rename(columns={"value": "pmi"}).assign(
            date=pd.to_datetime(df["report_date"])
        ).set_index("date")

    # 数据不足，从 Tushare 拉历史 PMI
    logger.info("macro_indicator 数据不足，从 Tushare 拉历史 PMI")
    try:
        from stockhot.data_layer import get_gateway
        gw = get_gateway()
        pmi_df = gw.call("cn_pmi", start_m="202101")
        if pmi_df is not None and not pmi_df.empty:
            pmi_df = pmi_df[["month", "PMI020100"]].rename(
                columns={"PMI020100": "pmi"}
            )
            pmi_df["pmi"] = pd.to_numeric(pmi_df["pmi"], errors="coerce")
            pmi_df["date"] = pd.to_datetime(pmi_df["month"].astype(str), format="%Y%m")
            return pmi_df.dropna(subset=["pmi"]).set_index("date").sort_index()
    except Exception as e:
        logger.warning(f"PMI 历史拉取失败: {e}")

    return pd.DataFrame()


def _get_pmi_for_date(d: date, pmi_history: pd.DataFrame) -> float | None:
    """获取某日期最近的 PMI 值."""
    if pmi_history.empty:
        return None
    target = pd.Timestamp(d)
    valid = pmi_history[pmi_history.index <= target]
    if valid.empty:
        return None
    return float(valid.iloc[-1]["pmi"])


def _filter_by_macro(
    ranked: list[str],
    stock_infos: dict,
    pmi: float | None,
    top_n: int = 20,
) -> list[str]:
    """用宏观适配度过滤 top N 列表.

    剔除"宏观逆风"的标的（PMI<48 时的顺周期行业），用后续排名补位。
    """
    if pmi is None or pmi >= 48:
        # 无 PMI 数据或 PMI 未到深度收缩 → 不过滤
        return ranked[:top_n]

    from stockhot.macro_fitness import get_macro_fitness

    result = []
    for code in ranked:
        if len(result) >= top_n:
            break
        industry = stock_infos.get(code, {}).industry if hasattr(stock_infos.get(code), "industry") else ""
        if not industry:
            # 无行业信息 → 放行
            result.append(code)
            continue
        fitness = get_macro_fitness(industry, pmi=pmi)
        if fitness["label"] == "宏观逆风":
            continue  # 剔除
        result.append(code)

    # 如果过滤后不足 top_n，用后续排名补位
    if len(result) < top_n:
        for code in ranked:
            if code not in result:
                result.append(code)
                if len(result) >= top_n:
                    break

    return result


def run_ab_test() -> dict:
    """运行 A/B 对比回测."""
    from davis_analyzer.backtest_factors import score_universe_at, FactorConfig
    from davis_analyzer.backtest import (
        BacktestConfig, _get_trading_calendar, _build_stock_infos,
        _all_cached_stock_codes,
    )
    from davis_analyzer.tushare_client import TushareClient

    # 配置
    start_date = date(2024, 1, 1)
    end_date = date(2026, 7, 31)
    top_n = 20
    rebalance_days = 20  # 月频
    cfg = FactorConfig()

    logger.info(f"A/B 测试: {start_date} → {end_date}, top_n={top_n}")

    # 加载宏观历史
    pmi_history = _load_macro_history()
    logger.info(f"PMI 历史数据: {len(pmi_history)} 条")

    # 初始化 client + 日历 + 股票池
    client = TushareClient()
    bt_cfg = BacktestConfig(start_date=start_date, end_date=end_date, top_n=top_n)
    cal = _get_trading_calendar(client, bt_cfg)
    if not cal:
        return {"error": "无法获取交易日历"}

    # 加载 stock_infos（全市场）
    universe_codes = _all_cached_stock_codes()
    stock_infos = _build_stock_infos(client, universe_codes)
    logger.info(f"股票池: {len(stock_infos)} 只")

    # 调仓日
    rebalance_dates = cal[::rebalance_days]

    # 逐调仓日评分
    a_returns = []  # A 组（无过滤）每月收益
    b_returns = []  # B 组（宏观过滤）每月收益
    a_holdings = []
    b_holdings = []

    for i, rb_date in enumerate(rebalance_dates[:-1]):
        next_date = rebalance_dates[i + 1] if i + 1 < len(rebalance_dates) else cal[-1]
        pmi = _get_pmi_for_date(rb_date, pmi_history)

        try:
            scores = score_universe_at(client, rb_date, stock_infos, cfg)
        except Exception as e:
            logger.warning(f"评分失败 {rb_date}: {e}")
            continue

        if not scores:
            continue

        ranked = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)

        # A 组：纯因子 top20
        a_target = ranked[:top_n]
        # B 组：宏观过滤 top20
        b_target = _filter_by_macro(ranked, stock_infos, pmi, top_n)

        # 计算持有期收益（等权）
        a_ret = _calc_period_return(client, a_target, rb_date, next_date)
        b_ret = _calc_period_return(client, b_target, rb_date, next_date)

        if a_ret is not None and b_ret is not None:
            a_returns.append(a_ret)
            b_returns.append(b_ret)
            a_holdings.append(len(a_target))
            b_holdings.append(len(b_target))

        # 过滤效果统计
        filtered = len(set(a_target) - set(b_target))
        pmi_str = f"{pmi:.1f}" if pmi else "N/A"
        logger.info(
            f"{rb_date}: PMI={pmi_str} | "
            f"A={len(a_target)} B={len(b_target)} 过滤{filtered}只 | "
            f"A收益{a_ret:+.2f}% B收益{b_ret:+.2f}%" if a_ret and b_ret else ""
        )

    a_arr = np.array(a_returns)
    b_arr = np.array(b_returns)

    result = {
        "period": f"{start_date} ~ {end_date}",
        "rebalances": len(a_returns),
        "A_pure_factor": {
            "total_return": round(float(np.prod(1 + a_arr / 100) - 1) * 100, 2),
            "avg_per_period": round(float(a_arr.mean()), 2),
            "sharpe": round(float(a_arr.mean() / a_arr.std() * np.sqrt(12)), 2) if a_arr.std() > 0 else 0,
            "win_rate": round(float((a_arr > 0).mean() * 100), 1),
        },
        "B_macro_filtered": {
            "total_return": round(float(np.prod(1 + b_arr / 100) - 1) * 100, 2),
            "avg_per_period": round(float(b_arr.mean()), 2),
            "sharpe": round(float(b_arr.mean() / b_arr.std() * np.sqrt(12)), 2) if b_arr.std() > 0 else 0,
            "win_rate": round(float((b_arr > 0).mean() * 100), 1),
        },
        "difference": {
            "total_return": round(float(np.prod(1 + b_arr / 100) - np.prod(1 + a_arr / 100)) * 100, 2),
            "sharpe_delta": round(float(b_arr.mean() / b_arr.std() * np.sqrt(12) - a_arr.mean() / a_arr.std() * np.sqrt(12)), 2) if a_arr.std() > 0 and b_arr.std() > 0 else 0,
        },
    }
    return result


def _calc_period_return(client, codes: list[str], start: date, end: date) -> float | None:
    """计算一组股票在 [start, end] 的等权收益（%）."""
    if not codes:
        return None

    returns = []
    for code in codes:
        try:
            start_str = start.strftime("%Y%m%d")
            end_str = end.strftime("%Y%m%d")
            df = client.get_daily_prices(code, start_str, end_str)
            if df is not None and not df.empty:
                df = df.sort_values("trade_date")
                start_close = float(df.iloc[0]["close"])
                end_close = float(df.iloc[-1]["close"])
                if start_close > 0:
                    returns.append((end_close / start_close - 1) * 100)
        except Exception:
            continue

    if not returns:
        return None
    return float(np.mean(returns))


def main() -> int:
    print("=== 宏观适配度 A/B 对比回测 ===")
    print()

    result = run_ab_test()

    if "error" in result:
        print(f"[ERROR] {result['error']}")
        return 1

    print(f"回测区间: {result['period']}")
    print(f"调仓次数: {result['rebalances']}")
    print()
    print(f"{'指标':20s} {'A组(纯因子)':>14s} {'B组(宏观过滤)':>14s} {'差异':>10s}")
    print("-" * 62)
    a = result["A_pure_factor"]
    b = result["B_macro_filtered"]
    d = result["difference"]
    print(f"{'累计收益':20s} {a['total_return']:>+13.2f}% {b['total_return']:>+13.2f}% {d['total_return']:>+9.2f}%")
    print(f"{'期均收益':20s} {a['avg_per_period']:>+13.2f}% {b['avg_per_period']:>+13.2f}%")
    print(f"{'Sharpe(年化)':20s} {a['sharpe']:>14.2f} {b['sharpe']:>14.2f} {d['sharpe_delta']:>+9.2f}")
    print(f"{'胜率':20s} {a['win_rate']:>13.1f}% {b['win_rate']:>13.1f}%")
    print()

    # 保存结果
    output_path = PROJECT_ROOT / "studies" / "output" / "macro_fitness_abtest.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已保存: {output_path}")

    # 结论
    print()
    if d["total_return"] > 1 and d["sharpe_delta"] > 0:
        print("✅ 结论：宏观过滤有 alpha 贡献，建议升级为正式因子")
    elif abs(d["total_return"]) < 1:
        print("⚪ 结论：宏观过滤无显著差异，保持标注层即可")
    else:
        print("⚠️ 结论：宏观过滤反而拖累收益，不建议升级")

    return 0


if __name__ == "__main__":
    sys.exit(main())
