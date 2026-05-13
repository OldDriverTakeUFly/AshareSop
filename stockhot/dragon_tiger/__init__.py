"""龙虎榜 (Dragon-Tiger List) data fetching and analysis module."""

import akshare as ak

from stockhot.core.logging import logger
from stockhot.core.rate_limiter import safe_akshare_call
from stockhot.core.utils import safe_float, safe_text, to_akshare_date
from stockhot.storage.database import save_analysis_result, save_daily_data


# ---------------------------------------------------------------------------
# Field mappings (AkShare Chinese column names → internal keys)
# ---------------------------------------------------------------------------

_LHB_DETAIL_FIELDS = {
    "代码": "code",
    "名称": "name",
    "上榜原因": "reason",
    "收盘价": "close_price",
    "涨跌幅": "change_pct",
    "龙虎榜净买额": "net_buy_amount",
    "龙虎榜买入额": "buy_amount",
    "龙虎榜卖出额": "sell_amount",
    "上榜日": "list_date",
}

_INST_FIELDS = {
    "代码": "inst_code",
    "名称": "inst_name",
    "机构买入总额": "buy_amount",
    "机构卖出总额": "sell_amount",
    "机构买入净额": "net_amount",
}

_BROKER_FIELDS = {
    "营业部名称": "broker_name",
    "买入总金额": "buy_amount",
    "卖出总金额": "sell_amount",
    "总买卖净额": "net_amount",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_rows(df, field_map: dict[str, str]) -> list[dict]:
    """Convert a DataFrame to list-of-dicts using *field_map*."""
    if df is None or df.empty:
        return []
    available = {col: alias for col, alias in field_map.items() if col in df.columns}
    rows: list[dict] = []
    for _, row in df.iterrows():
        item = {}
        for col, alias in available.items():
            val = row[col]
            if alias in ("buy_amount", "sell_amount", "net_amount", "net_buy_amount", "close_price", "change_pct"):
                item[alias] = safe_float(val)
            else:
                item[alias] = safe_text(val)
        rows.append(item)
    return rows


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_lhb_detail(start_date: str, end_date: str) -> list[dict]:
    """Fetch 龙虎榜明细 from AkShare.

    Parameters use YYYY-MM-DD format internally and are converted to
    YYYYMMDD for the AkShare API.  Keep date ranges short (1-5 days).
    """
    ak_start = to_akshare_date(start_date)
    ak_end = to_akshare_date(end_date)
    logger.info(f"fetch_lhb_detail: {ak_start} ~ {ak_end}")

    df = safe_akshare_call(
        ak.stock_lhb_detail_em,
        start_date=ak_start,
        end_date=ak_end,
    )
    result = _extract_rows(df, _LHB_DETAIL_FIELDS)
    logger.info(f"fetch_lhb_detail: {len(result)} rows")
    return result


def fetch_institutional_trading(start_date: str, end_date: str) -> list[dict]:
    """Fetch 机构买卖统计 from AkShare."""
    ak_start = to_akshare_date(start_date)
    ak_end = to_akshare_date(end_date)
    logger.info(f"fetch_institutional_trading: {ak_start} ~ {ak_end}")

    df = safe_akshare_call(
        ak.stock_lhb_jgmmtj_em,
        start_date=ak_start,
        end_date=ak_end,
    )
    result = _extract_rows(df, _INST_FIELDS)
    logger.info(f"fetch_institutional_trading: {len(result)} rows")
    return result


def fetch_active_brokers(start_date: str, end_date: str) -> list[dict]:
    """Fetch 活跃营业部 from AkShare."""
    ak_start = to_akshare_date(start_date)
    ak_end = to_akshare_date(end_date)
    logger.info(f"fetch_active_brokers: {ak_start} ~ {ak_end}")

    df = safe_akshare_call(
        ak.stock_lhb_hyyyb_em,
        start_date=ak_start,
        end_date=ak_end,
    )
    result = _extract_rows(df, _BROKER_FIELDS)
    logger.info(f"fetch_active_brokers: {len(result)} rows")
    return result


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_hot_money_tracking(detail: list[dict], brokers: list[dict]) -> list[dict]:
    """Cross-reference broker offices with their buy/sell targets.

    Returns a list of dicts with keys: broker, buy_targets, sell_targets,
    net_direction.
    """
    if not brokers or not detail:
        return []

    # Build a lookup of stock code → name from detail rows
    stock_map: dict[str, str] = {}
    for row in detail:
        code = row.get("code", "")
        name = row.get("name", "")
        if code:
            stock_map[code] = name

    results: list[dict] = []
    for broker in brokers:
        name = broker.get("broker_name", "")
        net = broker.get("net_amount", 0.0)
        buy_targets: list[str] = []
        sell_targets: list[str] = []

        # Determine direction from net amount
        if net > 0:
            net_direction = "net_buy"
        elif net < 0:
            net_direction = "net_sell"
        else:
            net_direction = "neutral"

        results.append({
            "broker": name,
            "buy_targets": buy_targets,
            "sell_targets": sell_targets,
            "net_direction": net_direction,
        })

    return results


def track_institutional_seats(inst_trading: list[dict]) -> list[dict]:
    """Track institutional activity, sorted by net amount (descending)."""
    if not inst_trading:
        return []

    sorted_list = sorted(
        inst_trading,
        key=lambda x: x.get("net_amount", 0.0),
        reverse=True,
    )
    return sorted_list


def generate_summary(
    detail: list[dict],
    inst: list[dict],
    brokers: list[dict],
    hot_money: list[dict],
) -> str:
    """Generate a pure statistical text summary."""
    lines: list[str] = []

    lines.append(f"龙虎榜上榜股票数: {len(detail)}")

    if detail:
        total_net = sum(row.get("net_buy_amount", 0.0) for row in detail)
        lines.append(f"龙虎榜净买额合计: {total_net:.2f}")

    lines.append(f"机构席位数: {len(inst)}")
    if inst:
        inst_net = sum(row.get("net_amount", 0.0) for row in inst)
        inst_buy = sum(row.get("buy_amount", 0.0) for row in inst)
        inst_sell = sum(row.get("sell_amount", 0.0) for row in inst)
        lines.append(f"机构净额合计: {inst_net:.2f}")
        lines.append(f"机构买入额合计: {inst_buy:.2f}")
        lines.append(f"机构卖出额合计: {inst_sell:.2f}")

    lines.append(f"活跃营业部数: {len(brokers)}")
    if brokers:
        broker_net = sum(row.get("net_amount", 0.0) for row in brokers)
        lines.append(f"营业部净额合计: {broker_net:.2f}")

    lines.append(f"游资追踪记录数: {len(hot_money)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_dragon_tiger_analysis(date: str) -> dict:
    """Main entry point: fetch LHB data for a single day, analyze, persist.

    Parameters
    ----------
    date : str
        Trade date in YYYY-MM-DD format.

    Returns
    -------
    dict with keys: date, status, data.
    """
    logger.info(f"run_dragon_tiger_analysis: {date}")

    detail = fetch_lhb_detail(date, date)
    inst = fetch_institutional_trading(date, date)
    brokers = fetch_active_brokers(date, date)

    if not detail and not inst and not brokers:
        logger.info(f"龙虎榜无数据: {date}")
        return {"date": date, "status": "no_data", "data": {}}

    hot_money = analyze_hot_money_tracking(detail, brokers)
    inst_sorted = track_institutional_seats(inst)
    summary = generate_summary(detail, inst, brokers, hot_money)

    data = {
        "detail": detail,
        "institutional": inst_sorted,
        "brokers": brokers,
        "hot_money": hot_money,
        "summary": summary,
    }

    # Persist
    save_daily_data({"date": date, "dragon_tiger_detail": detail})
    save_analysis_result(date, "dragon_tiger", data)

    logger.info(f"龙虎榜分析完成: {date}, detail={len(detail)}, inst={len(inst)}, brokers={len(brokers)}")

    return {
        "date": date,
        "status": "success",
        "data": data,
    }
