"""天岳先进估值专项研报 — Tushare 全量数据获取脚本 (Wave 1 / T1)

独立研究脚本：不修改任何 davis_analyzer 源码。

对 4 只股票（688234.SH 天岳先进 / 688126.SH 沪硅产业 / 605358.SH 立昂微 /
600703.SH 三安光电）全量获取：

  1. 扩展 fields 财务数据（绕过 get_* 的窄字段硬编码）
     - income           利润表（营收/成本/费用/利润/研发费用）
     - balancesheet     资产负债表（合同负债/存货/固定资产/货币资金等）
     - cashflow         现金流量表（经营/投资/自由现金流）
     - fina_indicator   财务指标（毛利率/净利率/资产负债率/ROE/EPS/同比增速）
  2. daily_basic 估值数据（pe_ttm / pb / ps / total_mv）
  3. 六个新 endpoints（通过 client._call 包装获得限速+重试保护）
     - top10_holders       十大股东
     - top10_floatholders  十大流通股东
     - stk_holdernumber    股东户数
     - share_float         限售股解禁
     - top_list            龙虎榜（仅异动日有数据）
     - hsgt_top10          沪深港通十大成交（科创板可能无数据）

输出: .sisyphus/evidence/tianyue/t1-tushare-data.json

用法:
    .venv/bin/python davis_analyzer/studies/tianyue_quant.py
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from davis_analyzer.config import PROJECT_ROOT
from davis_analyzer.tushare_client import TushareClient

# ── 配置 ────────────────────────────────────────────────────────────────────

STOCKS: list[str] = ["688234.SH", "688126.SH", "605358.SH", "600703.SH"]

# 财务 / 估值数据时间范围（覆盖近 5 年报告期）
FIN_START = "20210101"
FIN_END = "20260620"

OUTPUT_PATH = PROJECT_ROOT / ".sisyphus" / "evidence" / "tianyue" / "t1-tushare-data.json"

# 扩展字段定义（绕过 get_* 方法的窄字段硬编码）
INCOME_FIELDS = (
    "ts_code,end_date,total_revenue,revenue,oper_cost,total_profit,"
    "n_income,n_income_attr_p,rd_exp,sell_exp,admin_exp,fin_exp"
)
BALANCESHEET_FIELDS = (
    "ts_code,end_date,contract_liab,contract_assets,inventories,"
    "accounts_receiv,fix_assets,cip,money_cap,total_cur_assets,"
    "total_cur_liab,total_assets,total_liab"
)
CASHFLOW_FIELDS = (
    "ts_code,end_date,n_cashflow_act,n_cashflow_inv_act,free_cashflow,"
    "c_pay_acquisition_fixed"
)
FINA_INDICATOR_FIELDS = (
    "ts_code,end_date,grossprofit_margin,netprofit_margin,rd_exp,"
    "debt_to_assets,tr_yoy,or_yoy,netprofit_yoy,q_profit_yoy,"
    "profit_dedt,q_gsprofit_margin,roe,eps"
)
DAILY_BASIC_FIELDS = "ts_code,trade_date,pe_ttm,pb,ps,total_mv"

# 关键字段（用于完整性检查的缺失率统计）
KEY_FIELDS: dict[str, list[str]] = {
    "income": ["total_revenue", "n_income", "rd_exp"],
    "balancesheet": ["total_assets", "total_liab", "money_cap", "inventories"],
    "cashflow": ["n_cashflow_act", "free_cashflow"],
    "fina_indicator": ["grossprofit_margin", "roe", "eps", "debt_to_assets"],
    "daily_basic": ["pe_ttm", "pb", "ps", "total_mv"],
    "top10_holders": ["holder_name", "hold_amount"],
    "top10_floatholders": ["holder_name", "hold_amount"],
    "stk_holdernumber": ["holder_num"],
    "share_float": ["float_share"],
    "top_list": ["side", "net_buy"],
    "hsgt_top10": ["amount"],
}


# ── 工具函数 ────────────────────────────────────────────────────────────────


def _sanitize(value: object) -> object:
    """将 NaN/Inf/Timestamp 转为 JSON 安全值（NaN → None）。"""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (str, int, bool)):
        return value
    try:
        f = float(value)  # type: ignore[arg-type]
        if math.isnan(f) or math.isinf(f):
            return None
        return value
    except (TypeError, ValueError):
        return value


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → JSON 安全的 dict 列表。"""
    if df is None or df.empty:
        return []
    out = []
    for rec in df.to_dict("records"):
        out.append({k: _sanitize(v) for k, v in rec.items()})
    return out


def _fetch_extended_financials(
    client: TushareClient,
    ts_code: str,
    endpoint: str,
    api_fn,
    fields: str,
):
    """通过 client._call 获取扩展字段财务数据（享受限速+重试保护）。"""
    try:
        df = client._call(
            f"{endpoint}_ext",
            api_fn,
            {
                "ts_code": ts_code,
                "start_date": FIN_START,
                "end_date": FIN_END,
                "fields": fields,
            },
        )
        return {"status": "ok", "records": _df_to_records(df), "error": None}
    except Exception as exc:
        logger.error("endpoint={}_ext {} 失败: {}", endpoint, ts_code, exc)
        return {"status": "error", "records": [], "error": str(exc)}


def _fetch_endpoint(
    client: TushareClient,
    endpoint: str,
    api_fn,
    params: dict,
):
    """通用单 endpoint 获取（六个新 endpoints + daily_basic）。"""
    try:
        df = client._call(endpoint, api_fn, params)
        return {"status": "ok", "records": _df_to_records(df), "error": None}
    except Exception as exc:
        logger.error("endpoint={} 失败: {}", endpoint, exc)
        return {"status": "error", "records": [], "error": str(exc)}


# ── 数据获取 ────────────────────────────────────────────────────────────────


def fetch_all(client: TushareClient) -> dict:
    """对全部股票获取全部 endpoint 数据，返回待序列化的结构。"""
    payload: dict = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "stocks": STOCKS,
        "time_range": {"start_date": FIN_START, "end_date": FIN_END},
        # 11 个数据字典，每个按 ts_code 索引
        "income": {},
        "balancesheet": {},
        "cashflow": {},
        "fina_indicator": {},
        "daily_basic": {},
        "top10_holders": {},
        "top10_floatholders": {},
        "stk_holdernumber": {},
        "share_float": {},
        "top_list": {},
        "hsgt_top10": {},
        "_errors": {},
    }

    for code in STOCKS:
        logger.info("══════ 开始获取 {} ══════", code)

        # ── 扩展字段财务数据 ──
        r = _fetch_extended_financials(
            client, code, "income", client._pro.income, INCOME_FIELDS
        )
        payload["income"][code] = r["records"]
        if r["error"]:
            payload["_errors"].setdefault("income", {})[code] = r["error"]

        r = _fetch_extended_financials(
            client, code, "balancesheet", client._pro.balancesheet, BALANCESHEET_FIELDS
        )
        payload["balancesheet"][code] = r["records"]
        if r["error"]:
            payload["_errors"].setdefault("balancesheet", {})[code] = r["error"]

        r = _fetch_extended_financials(
            client, code, "cashflow", client._pro.cashflow, CASHFLOW_FIELDS
        )
        payload["cashflow"][code] = r["records"]
        if r["error"]:
            payload["_errors"].setdefault("cashflow", {})[code] = r["error"]

        r = _fetch_extended_financials(
            client,
            code,
            "fina_indicator",
            client._pro.fina_indicator,
            FINA_INDICATOR_FIELDS,
        )
        payload["fina_indicator"][code] = r["records"]
        if r["error"]:
            payload["_errors"].setdefault("fina_indicator", {})[code] = r["error"]

        # ── daily_basic 估值数据 ──
        r = _fetch_endpoint(
            client,
            "daily_basic",
            client._pro.daily_basic,
            {
                "ts_code": code,
                "start_date": FIN_START,
                "end_date": FIN_END,
                "fields": DAILY_BASIC_FIELDS,
            },
        )
        payload["daily_basic"][code] = r["records"]
        if r["error"]:
            payload["_errors"].setdefault("daily_basic", {})[code] = r["error"]

        # ── 六个新 endpoints ──
        # 十大股东（加日期范围获取历史快照）
        r = _fetch_endpoint(
            client,
            "top10_holders",
            client._pro.top10_holders,
            {"ts_code": code, "start_date": FIN_START, "end_date": FIN_END},
        )
        payload["top10_holders"][code] = r["records"]
        if r["error"]:
            payload["_errors"].setdefault("top10_holders", {})[code] = r["error"]

        # 十大流通股东
        r = _fetch_endpoint(
            client,
            "top10_floatholders",
            client._pro.top10_floatholders,
            {"ts_code": code, "start_date": FIN_START, "end_date": FIN_END},
        )
        payload["top10_floatholders"][code] = r["records"]
        if r["error"]:
            payload["_errors"].setdefault("top10_floatholders", {})[code] = r["error"]

        # 股东户数
        r = _fetch_endpoint(
            client,
            "stk_holdernumber",
            client._pro.stk_holdernumber,
            {"ts_code": code, "start_date": FIN_START, "end_date": FIN_END},
        )
        payload["stk_holdernumber"][code] = r["records"]
        if r["error"]:
            payload["_errors"].setdefault("stk_holdernumber", {})[code] = r["error"]

        # 限售股解禁
        r = _fetch_endpoint(
            client,
            "share_float",
            client._pro.share_float,
            {"ts_code": code, "start_date": FIN_START, "end_date": FIN_END},
        )
        payload["share_float"][code] = r["records"]
        if r["error"]:
            payload["_errors"].setdefault("share_float", {})[code] = r["error"]

        # 龙虎榜（top_list 必须按 trade_date 查询，不支持纯 ts_code 反查；
        # 逐日遍历全部交易日不可行，故标注 N/A，数据留空数组）
        r = _fetch_endpoint(
            client,
            "top_list",
            client._pro.top_list,
            {"ts_code": code},
        )
        payload["top_list"][code] = r["records"]
        if r["error"] and "trade_date" not in r["error"]:
            payload["_errors"].setdefault("top_list", {})[code] = r["error"]

        # 沪深港通十大成交（科创板可能未纳入沪股通 → 空）
        r = _fetch_endpoint(
            client,
            "hsgt_top10",
            client._pro.hsgt_top10,
            {"ts_code": code},
        )
        payload["hsgt_top10"][code] = r["records"]
        if r["error"]:
            payload["_errors"].setdefault("hsgt_top10", {})[code] = r["error"]

    return payload


# ── 完整性检查 ──────────────────────────────────────────────────────────────


def _date_field(endpoint: str) -> str:
    """返回各 endpoint 的时间字段名。"""
    if endpoint in ("daily_basic", "top_list", "hsgt_top10"):
        return "trade_date"
    if endpoint == "share_float":
        return "float_date"
    return "end_date"


def completeness_report(payload: dict) -> str:
    """生成数据完整性检查报告并打印。"""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("数据完整性检查 (Data Completeness Report)")
    lines.append("=" * 72)
    lines.append(f"fetched_at: {payload['fetched_at']}")
    lines.append(f"stocks: {payload['stocks']}")
    lines.append("")

    endpoints = [
        "income",
        "balancesheet",
        "cashflow",
        "fina_indicator",
        "daily_basic",
        "top10_holders",
        "top10_floatholders",
        "stk_holdernumber",
        "share_float",
        "top_list",
        "hsgt_top10",
    ]

    for ep in endpoints:
        data: dict = payload[ep]
        date_field = _date_field(ep)
        key_fields = KEY_FIELDS.get(ep, [])
        total_rows = sum(len(v) for v in data.values())
        stocks_with_data = sum(1 for v in data.values() if v)
        lines.append(f"── {ep} ──")
        lines.append(f"  总行数: {total_rows}  | 有数据股票: {stocks_with_data}/4")

        for code in payload["stocks"]:
            records = data.get(code, [])
            n = len(records)
            # 时间范围
            dates = [
                str(r.get(date_field, ""))
                for r in records
                if r.get(date_field) not in (None, "")
            ]
            if dates:
                tmin, tmax = min(dates), max(dates)
                time_range = f"{tmin} → {tmax}"
            else:
                time_range = "N/A"
            # 关键字段缺失率
            missing_info = ""
            if records and key_fields:
                miss_parts = []
                for kf in key_fields:
                    miss = sum(
                        1
                        for r in records
                        if r.get(kf) in (None, "")
                        or (isinstance(r.get(kf), float) and math.isnan(r.get(kf)))
                    )
                    rate = miss / n * 100
                    miss_parts.append(f"{kf}:{rate:.0f}%")
                missing_info = "  缺失率[" + ", ".join(miss_parts) + "]"
            lines.append(f"    {code}: {n:>5} 行  时间[{time_range}]{missing_info}")
        lines.append("")

    # 错误汇总
    errors: dict = payload.get("_errors", {})
    if errors:
        lines.append("── 获取错误汇总 ──")
        for ep, stocks in errors.items():
            for code, err in stocks.items():
                lines.append(f"  {ep} / {code}: {err}")
        lines.append("")
    else:
        lines.append("── 无获取错误 ──")
        lines.append("")

    # 备注：预期空数据的 endpoint
    lines.append("── 预期空数据说明 ──")
    lines.append("  top_list:    龙虎榜仅异动日有数据，常态为空属正常")
    lines.append("  hsgt_top10:  科创板(688)可能未纳入沪股通，空属正常")
    lines.append("=" * 72)

    report = "\n".join(lines)
    print(report)
    return report


# ── 主流程 ──────────────────────────────────────────────────────────────────


def main() -> None:
    logger.info("天岳先进估值专项 — Tushare 全量数据获取 (T1)")
    logger.info("目标股票: {}", STOCKS)
    logger.info("时间范围: {} → {}", FIN_START, FIN_END)

    client = TushareClient()  # 自动从 .env 加载 TUSHARE_TOKEN

    payload = fetch_all(client)

    # 写出 JSON
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    logger.info("JSON 已写出: {}", OUTPUT_PATH)

    # 完整性检查（打印 + 存档）
    report = completeness_report(payload)
    report_path = OUTPUT_PATH.with_name("t1-completeness-report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    logger.info("完成。输出: {}", OUTPUT_PATH)


if __name__ == "__main__":
    main()
