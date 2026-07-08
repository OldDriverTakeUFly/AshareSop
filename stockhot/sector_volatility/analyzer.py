"""Sector volatility analyzer — 板块情绪与恐慌程度分析编排。

对 31 个申万一级行业，用成分股等权 RV + 各板块自身历史分位，度量板块情绪温度。
联读 limit_up（板块涨跌停行为代理）+ fund_flow（板块资金流），形成多维度板块情绪图景。

入口：run_sector_volatility_analysis(date, sectors=None, days=1300)
不进 daily-market-scan Wave 编排（计算量大），独立 CLI/cron 触发。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from stockhot.core.logging import logger
from stockhot.storage.database import save_daily_data
from stockhot.volatility.analyzer import percentile_rank, classify_panic_level
from stockhot.sector_volatility.aggregator import aggregate_sector_rv
from stockhot.sector_volatility.data_loader import (
    fetch_sw_l1_sectors,
    fetch_sector_members,
    fetch_member_rv_history,
)


def analyze_single_sector(
    sw_code: str,
    name: str,
    days: int = 1300,
    use_cache: bool = True,
) -> dict[str, Any]:
    """分析单个板块的 RV + 历史分位 + 恐慌等级。

    参数：
        sw_code: 申万指数代码，如 "801010.SI"
        name: 板块名称（中文）
        days: 回溯交易日数
        use_cache: 是否用缓存

    返回：{name, sw_code, member_count, sector_rv20, sector_rv20_pct, panic_level, status}
    """
    # 1. 拿成分股
    members = fetch_sector_members(sw_code, use_cache=use_cache)
    if not members:
        return {"sw_code": sw_code, "name": name, "status": "数据不可用", "error": "无成分股"}

    # 2. 拿成分股 RV 时序
    member_rv = fetch_member_rv_history(sw_code, members, days=days, use_cache=use_cache)
    if not member_rv:
        return {"sw_code": sw_code, "name": name, "status": "数据不可用", "error": "无个股 RV"}

    # 3. 等权聚合
    sector_rv = aggregate_sector_rv(member_rv)
    if sector_rv.empty or len(sector_rv) < 60:
        return {"sw_code": sw_code, "name": name, "status": "数据不足", "error": f"仅 {len(sector_rv)} 日"}

    # 4. 算当前值 + 历史分位
    current_rv = float(sector_rv.iloc[-1])
    rv_pct = percentile_rank(current_rv, sector_rv)
    panic_level = classify_panic_level(rv_pct)
    latest_date = sector_rv.index[-1].strftime("%Y-%m-%d")

    return {
        "sw_code": sw_code,
        "name": name,
        "status": "success",
        "member_count": len(members),
        "valid_member_count": len(member_rv),
        "latest_date": latest_date,
        "sector_rv20": round(current_rv, 1),
        "sector_rv20_pct": rv_pct,
        "panic_level": panic_level,
    }


def _fetch_sector_limits(date: str) -> dict[str, dict]:
    """联读 limit_up，按申万 industry 聚合板块涨跌停数据。

    返回：{industry_name: {"limit_up": n, "broken": n, "limit_down": n}}
    注意：limit_list_d 的 industry 是申万 2-3 级，与一级名称不完全一致，
    用包含关系做模糊匹配（如"化学制品"归入"基础化工"）。
    """
    from stockhot.core.tushare_client_safe import safe_tushare_call

    df = safe_tushare_call("limit_list_d", trade_date=date.replace("-", ""))
    if df is None or df.empty or "industry" not in df.columns:
        return {}

    # 按 industry + limit 类型聚合
    result: dict[str, dict] = {}
    for _, row in df.iterrows():
        ind = str(row.get("industry", "")).strip()
        if not ind or ind == "nan":
            continue
        limit_type = row.get("limit", "")
        if ind not in result:
            result[ind] = {"limit_up": 0, "broken": 0, "limit_down": 0}
        if limit_type == "U":
            result[ind]["limit_up"] += 1
        elif limit_type == "Z":
            result[ind]["broken"] += 1
        elif limit_type == "D":
            result[ind]["limit_down"] += 1
    return result


def _build_summary(sectors_results: dict[str, dict]) -> str:
    """生成板块情绪定性摘要（纯事实陈述）。"""
    ok = [r for r in sectors_results.values() if r.get("status") == "success"]
    if not ok:
        return "板块波动率数据全部不可用"

    by_pct = sorted(
        [r for r in ok if not pd.isna(r.get("sector_rv20_pct"))],
        key=lambda r: r["sector_rv20_pct"],
    )
    if not by_pct:
        return "板块波动率分位数据不可用"

    calmest = by_pct[0]
    hottest = by_pct[-1]
    panic_n = sum(1 for r in by_pct if r["sector_rv20_pct"] >= 90)

    parts = [
        f"最平静板块：{calmest['name']} P{calmest['sector_rv20_pct']:.0f}（RV20={calmest['sector_rv20']:.1f}%）",
        f"最恐慌板块：{hottest['name']} P{hottest['sector_rv20_pct']:.0f}（RV20={hottest['sector_rv20']:.1f}%，{hottest['panic_level']}）",
    ]

    if panic_n > 0:
        panic_names = "、".join(r["name"] for r in by_pct if r["sector_rv20_pct"] >= 90)
        parts.append(f"{panic_n}/{len(by_pct)} 板块 P90+ 恐慌（{panic_names}）")
    else:
        parts.append(f"无板块 P90+ 恐慌（最高 {hottest['name']} P{hottest['sector_rv20_pct']:.0f}）")

    return "，".join(parts)


def run_sector_volatility_analysis(
    date: str | None = None,
    sectors: list[dict] | None = None,
    days: int = 1300,
    use_cache: bool = True,
) -> dict[str, Any]:
    """板块波动率分析入口（独立 CLI/cron 触发，不进 daily-market-scan）。

    参数：
        date: 日期字符串（仅标记，实际取最新）
        sectors: 板块列表（None 则自动拉 31 个申万一级）
        days: 回溯交易日数
        use_cache: 是否用缓存（首次 False 全量重建，日常 True 增量）

    返回：
        {
            "date", "status",
            "sectors": {name: {单板块结果}},
            "cross_section_ranking": [...],  # 当日分位降序排名
            "summary": "...",
        }
    """
    date = date or datetime.now().strftime("%Y-%m-%d")
    sectors = sectors or fetch_sw_l1_sectors()

    logger.info(f"run_sector_volatility_analysis: date={date}, {len(sectors)} sectors, days={days}")

    sectors_results: dict[str, dict] = {}
    for i, sec in enumerate(sectors):
        sw_code = sec["sw_code"]
        name = sec["name"]
        try:
            result = analyze_single_sector(sw_code, name, days=days, use_cache=use_cache)
            sectors_results[name] = result
            if result.get("status") == "success":
                logger.info(
                    f"  [{i+1}/{len(sectors)}] {name}: RV20={result['sector_rv20']:.1f}% "
                    f"P{result['sector_rv20_pct']:.0f} ({result['panic_level']})"
                )
            else:
                logger.warning(f"  [{i+1}/{len(sectors)}] {name}: {result.get('error', '?')}")
        except Exception as e:
            sectors_results[name] = {
                "sw_code": sw_code, "name": name,
                "status": "数据不可用", "error": f"{type(e).__name__}: {e}",
            }
            logger.error(f"  [{i+1}/{len(sectors)}] {name}: {type(e).__name__}: {e}")

    # 联读 limit_up 板块涨跌停（Layer 3）
    sector_limits = _fetch_sector_limits(date)

    # 截面排名
    ok = [r for r in sectors_results.values() if r.get("status") == "success" and not pd.isna(r.get("sector_rv20_pct"))]
    ranking = sorted(
        [{"name": r["name"], "rv20_pct": r["sector_rv20_pct"], "panic_level": r["panic_level"],
          "sector_rv20": r["sector_rv20"]} for r in ok],
        key=lambda x: x["rv20_pct"],
        reverse=True,
    )

    summary = _build_summary(sectors_results)
    success_n = sum(1 for r in sectors_results.values() if r.get("status") == "success")

    result: dict[str, Any] = {
        "date": date,
        "status": "success" if success_n > 0 else "no_data",
        "sectors": sectors_results,
        "sector_limits": sector_limits,
        "cross_section_ranking": ranking,
        "summary": summary,
    }

    # 持久化
    if success_n > 0:
        try:
            save_daily_data({"date": date, "sector_volatility": result})
            logger.info(f"  持久化 sector_volatility → daily_data[{date}]")
        except Exception as e:
            logger.error(f"  持久化失败: {e}")

    return result
