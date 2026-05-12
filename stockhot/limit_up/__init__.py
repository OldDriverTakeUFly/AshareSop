"""涨停板分析模块 — 识别涨停股、封板强度、连板梯队、板块联动

NOTE: ST stocks and 科创板 (STAR Market, 688xxx) are excluded by the 东方财富 API.
"""

import akshare as ak
import pandas as pd
from stockhot.core.rate_limiter import safe_akshare_call
from stockhot.core.utils import to_akshare_date, safe_float, safe_text
from stockhot.storage.database import save_daily_data, save_analysis_result
from stockhot.core.logging import logger


def run_limit_up_analysis(date: str) -> dict:
    """Run full limit-up analysis for a given date.

    Returns: {date, status, data: {limit_up_pool, broken_pool, limit_down_pool,
              consecutive_boards, sector_correlation, summary}}
    """
    logger.info(f"[LimitUp] 分析日期: {date}")

    pool = fetch_limit_up_pool(date)
    broken = fetch_broken_pool(date)
    limit_down = fetch_limit_down_pool(date)

    if not pool and not broken and not limit_down:
        logger.info(f"[LimitUp] {date} 无涨停数据（可能非交易日）")
        return {"date": date, "status": "no_data"}

    consecutive = find_consecutive_boards(pool)
    sector_corr = analyze_sector_correlation(pool)
    seal = analyze_seal_strength(pool)
    summary = generate_summary(pool, broken, limit_down, consecutive, sector_corr)

    # Save data
    data = {
        "date": date,
        "limit_up_pool": pool,
        "broken_pool": broken,
        "limit_down_pool": limit_down,
    }
    save_daily_data(data)

    analysis = {
        "consecutive_boards": consecutive,
        "sector_correlation": sector_corr,
        "seal_strength_ranking": seal,
        "summary": summary,
    }
    save_analysis_result(date, "limit_up_analysis", analysis)

    logger.info(f"[LimitUp] 分析完成: {len(pool)} 涨停, {len(broken)} 炸板, {len(limit_down)} 跌停")
    return {
        "date": date,
        "status": "success",
        "data": {
            "limit_up_pool": pool,
            "broken_pool": broken,
            "limit_down_pool": limit_down,
            "consecutive_boards": consecutive,
            "sector_correlation": sector_corr,
            "seal_strength_ranking": seal,
            "summary": summary,
        }
    }


def fetch_limit_up_pool(date: str) -> list[dict]:
    """Fetch today's limit-up pool from AkShare.
    Uses: ak.stock_zt_pool_em(date) — YYYYMMDD format
    """
    ak_date = to_akshare_date(date)
    df = safe_akshare_call(ak.stock_zt_pool_em, date=ak_date)
    if df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        result.append({
            "code": safe_text(row.get("代码")),
            "name": safe_text(row.get("名称")),
            "change_pct": safe_float(row.get("涨跌幅")),
            "seal_amount": safe_float(row.get("封板资金")),
            "max_board": safe_float(row.get("最高板")),
            "consecutive_boards": safe_float(row.get("连板数")),
            "sector": safe_text(row.get("所属行业")),
            "broken_count": safe_float(row.get("炸板次数")),
            "first_seal_time": safe_text(row.get("首次封板时间")),
            "last_seal_time": safe_text(row.get("最后封板时间")),
            "turnover_rate": safe_float(row.get("换手率")),
        })
    return result


def fetch_broken_pool(date: str) -> list[dict]:
    """Fetch broken-board pool (炸板池). Note: 30-day lookback limit."""
    ak_date = to_akshare_date(date)
    df = safe_akshare_call(ak.stock_zt_pool_zbgc_em, date=ak_date)
    if df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        result.append({
            "code": safe_text(row.get("代码")),
            "name": safe_text(row.get("名称")),
            "change_pct": safe_float(row.get("涨跌幅")),
            "broken_count": safe_float(row.get("炸板次数")),
            "sector": safe_text(row.get("所属行业")),
        })
    return result


def fetch_limit_down_pool(date: str) -> list[dict]:
    """Fetch limit-down pool (跌停池). Note: 30-day lookback limit."""
    ak_date = to_akshare_date(date)
    df = safe_akshare_call(ak.stock_zt_pool_dtgc_em, date=ak_date)
    if df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        result.append({
            "code": safe_text(row.get("代码")),
            "name": safe_text(row.get("名称")),
            "change_pct": safe_float(row.get("涨跌幅")),
            "sector": safe_text(row.get("所属行业")),
        })
    return result


def analyze_seal_strength(pool: list[dict]) -> list[dict]:
    """Analyze seal strength for each stock. Sorted by strength (strongest first).

    Stronger = higher seal_amount, lower broken_count.
    Score = seal_amount / (broken_count + 1)
    """
    ranked = []
    for stock in pool:
        seal = safe_float(stock.get("seal_amount"))
        broken = safe_float(stock.get("broken_count"))
        score = seal / (broken + 1)
        ranked.append({
            "code": stock.get("code", ""),
            "name": stock.get("name", ""),
            "seal_amount": seal,
            "broken_count": broken,
            "score": round(score, 2),
        })
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


def find_consecutive_boards(pool: list[dict]) -> list[dict]:
    """Find stocks on consecutive limit-up days (连板梯队).
    Groups by consecutive_boards count, sorted descending.
    """
    if not pool:
        return []

    boards = {}
    for stock in pool:
        count = int(safe_float(stock.get("consecutive_boards", 1)))
        if count < 2:
            continue
        if count not in boards:
            boards[count] = []
        boards[count].append({"code": stock.get("code", ""), "name": stock.get("name", "")})

    result = [{"board_count": k, "stocks": v} for k, v in sorted(boards.items(), reverse=True)]
    return result


def analyze_sector_correlation(pool: list[dict]) -> list[dict]:
    """Analyze which sectors have the most limit-up stocks."""
    if not pool:
        return []

    sectors = {}
    for stock in pool:
        sector = stock.get("sector", "未知")
        if sector not in sectors:
            sectors[sector] = {"name": sector, "count": 0, "stocks": []}
        sectors[sector]["count"] += 1
        sectors[sector]["stocks"].append(stock.get("name", ""))

    result = sorted(sectors.values(), key=lambda x: x["count"], reverse=True)
    return result


def generate_summary(pool, broken, limit_down, consecutive, sector_corr) -> str:
    """Generate text summary — pure statistical, no AI."""
    lines = []
    lines.append(f"涨停 {len(pool)} 只，炸板 {len(broken)} 只，跌停 {len(limit_down)} 只")

    if consecutive:
        top = consecutive[0]
        lines.append(f"最高连板: {top['board_count']}板 ({', '.join(s['name'] for s in top['stocks'][:3])})")

    if sector_corr:
        lines.append(f"板块联动: {sector_corr[0]['name']}({sector_corr[0]['count']}只涨停)")

    return "；".join(lines)
