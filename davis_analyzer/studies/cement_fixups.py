#!/usr/bin/env python3
"""水泥双标的补充取数:修复首轮 JSON 的 5 个缺陷.

  1. profitability 字段名(latest_gross_margin/gross_margin_delta/rd_intensity_score)
  2. relative_valuation 字段名(quadrant/quadrant_label/composite_verdict)
  3. balancesheet 加 report_type=1 过滤(合并报表),取货币资金/有息负债 → 净现金
  4. momentum 用 pro.daily + adj_factor 手工复核 20/60/120/250d 复权收益
  5. dividend 分红总额 = dps × 总股本;附 top10_floatholders 交叉验证
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from loguru import logger

load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()

from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.tushare_client import TushareClient
from stockhot.tushare_config import get_pro_api
from stockhot.valuation import analyze_relative_valuation

OUT_DIR = Path(".sisyphus/evidence/cement")
TARGETS = ["600585.SH", "002233.SZ"]


def manual_returns(pro, ts_code: str) -> dict:
    """pro.daily + adj_factor 手工复核多窗口复权收益."""
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=420)).strftime("%Y%m%d")
    d = pro.daily(ts_code=ts_code, start_date=start, end_date=end, fields="ts_code,trade_date,close")
    af = pro.adj_factor(ts_code=ts_code, start_date=start, end_date=end, fields="ts_code,trade_date,adj_factor")
    df = d.merge(af, on=["ts_code", "trade_date"]).sort_values("trade_date").reset_index(drop=True)
    df["adj_close"] = df["close"] * df["adj_factor"]
    out = {"n_days": len(df), "last_date": str(df["trade_date"].iloc[-1]), "windows": {}}
    last = df["adj_close"].iloc[-1]
    for w in [20, 60, 120, 250]:
        if len(df) > w:
            base = df["adj_close"].iloc[-1 - w]
            out["windows"][str(w)] = round((last / base - 1) * 100, 2)
        else:
            out["windows"][str(w)] = None
    out["ytd_pct"] = None
    ytd_base = df[df["trade_date"] >= f"{date.today().year}0101"]
    if len(ytd_base) and len(ytd_base) < len(df):
        out["ytd_pct"] = round((last / ytd_base["adj_close"].iloc[0] - 1) * 100, 2)
    return out


def fixed_balance_sheet(pro, ts_code: str) -> dict:
    bs = pro.balancesheet(
        ts_code=ts_code,
        fields="ts_code,end_date,report_type,monetary_capital,tradable_fin_assets,"
        "other_equity_invest,st_borr,lt_borr,bonds_payable,non_cur_liab_1year,"
        "total_assets,total_liab",
        limit=45,
    )
    bs = bs[bs["report_type"] == "1"].sort_values("end_date")
    latest = bs.iloc[-1]
    cash = float(latest.get("monetary_capital") or 0)
    trad = float(latest.get("tradable_fin_assets") or 0)
    oth = float(latest.get("other_equity_invest") or 0)
    st_b = float(latest.get("st_borr") or 0)
    lt_b = float(latest.get("lt_borr") or 0)
    bond = float(latest.get("bonds_payable") or 0)
    ncl1y = float(latest.get("non_cur_liab_1year") or 0)
    interest = st_b + lt_b + bond + ncl1y
    return {
        "end_date": str(latest["end_date"]),
        "report_type": str(latest["report_type"]),
        "monetary_capital_yi": round(cash / 1e8, 1),
        "tradable_fin_yi": round(trad / 1e8, 1),
        "other_equity_invest_yi": round(oth / 1e8, 1),
        "st_borr_yi": round(st_b / 1e8, 1),
        "lt_borr_yi": round(lt_b / 1e8, 1),
        "bonds_yi": round(bond / 1e8, 1),
        "non_cur_1y_yi": round(ncl1y / 1e8, 1),
        "interest_bearing_debt_yi": round(interest / 1e8, 1),
        "net_cash_yi": round((cash + trad - interest) / 1e8, 1),
        "total_assets_yi": round(float(latest.get("total_assets") or 0) / 1e8, 1),
        "total_liab_yi": round(float(latest.get("total_liab") or 0) / 1e8, 1),
    }


def main() -> None:
    client = TushareClient()
    pro = get_pro_api(timeout=60)

    for ts_code in TARGETS:
        key = "hailuo" if "600585" in ts_code else "tapai"
        path = OUT_DIR / f"{key}_scoring.json"
        with open(path, encoding="utf-8") as f:
            result = json.load(f)

        # 1. profitability 字段修正
        fin = fetch_financial_data(client, ts_code, periods=12)
        pq = analyze_profitability_quality(fin)
        result["factors"]["profitability"] = {
            "quality_score": round(pq.quality_score, 2),
            "latest_gross_margin_pct": round(pq.latest_gross_margin, 2) if pq.latest_gross_margin is not None else None,
            "gross_margin_delta_pp": round(pq.gross_margin_delta, 2) if pq.gross_margin_delta is not None else None,
            "gross_margin_score": round(pq.gross_margin_score, 2),
            "latest_rd_intensity_pct": pq.latest_rd_intensity,
            "rd_intensity_score": round(pq.rd_intensity_score, 2),
            "data_sufficient": pq.data_sufficient,
        }

        # 2. relative_valuation 字段修正
        rv = analyze_relative_valuation(pro, ts_code, result["name"], lookback_years=3)
        result["relative_valuation"] = {
            "benchmark": rv.benchmark,
            "stock_pe": rv.stock_pe,
            "index_pe": rv.index_pe,
            "pe_ratio": rv.pe_ratio,
            "pe_ratio_pct": round(rv.pe_ratio_pct * 100, 1) if rv.pe_ratio_pct is not None else None,
            "pe_ratio_label": rv.pe_ratio_label,
            "earnings_yield_pct": rv.earnings_yield,
            "risk_free_rate_pct": rv.risk_free_rate,
            "erp_pct": rv.erp,
            "erp_label": rv.erp_label,
            "stock_pe_pct": round(rv.stock_pe_pct * 100, 1) if rv.stock_pe_pct is not None else None,
            "index_pe_pct": round(rv.index_pe_pct * 100, 1) if rv.index_pe_pct is not None else None,
            "quadrant": rv.quadrant,
            "quadrant_label": rv.quadrant_label,
            "composite_verdict": rv.composite_verdict,
            "signals": rv.signals,
        }

        # 3. balancesheet 修正(report_type=1)
        result["balance_sheet_fixed"] = fixed_balance_sheet(pro, ts_code)

        # 4. momentum 手工复核
        result["momentum_manual_check"] = manual_returns(pro, ts_code)

        # 5. dividend 总额(dps × 总股本) + top10 交叉验证
        db1 = pro.daily_basic(ts_code=ts_code, fields="ts_code,trade_date,total_share", limit=1)
        total_share_yi = float(db1.iloc[0]["total_share"]) / 1e4  # 万股 → 亿股
        for row in result.get("dividend_history", []):
            if isinstance(row, dict) and row.get("dps_pre_tax"):
                row["total_cash_yi"] = round(row["dps_pre_tax"] * total_share_yi, 2)
        result["total_share_yi"] = round(total_share_yi, 2)

        try:
            t10 = pro.top10_floatholders(ts_code=ts_code, period="20260630" if "002233" in ts_code else "20260331")
            if t10 is not None and len(t10):
                t10 = t10.sort_values("end_date").tail(10)
                result["top10_float_pct_sum"] = round(float(t10["ratio"].astype(float).sum()), 2)
                result["top10_period"] = str(t10["end_date"].iloc[-1])
        except Exception as e:
            result["top10_float_pct_sum"] = f"error: {e}"

        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        logger.success("✅ {} 补充字段已更新 → {}", ts_code, path)

    logger.info("全部完成")


if __name__ == "__main__":
    main()
