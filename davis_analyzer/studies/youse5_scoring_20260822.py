# -*- coding: utf-8 -*-
"""5 标的个股研报批量取数脚本(华友钴业/山东黄金/天齐锂业/神火股份/中国铝业)。

每标的产出研报所需全部结构化数据:
财务 12 期 / 景气度+阶段 / 估值 3 年分位+分位值表 / 引擎动量+手工复权复核 /
股息 / 业绩预告+修正 / 筹码集中度+股东户数 8 期 / 盈利质量 / 相对市场锚定 /
困境分 / 戴维斯综合分 / 时效校验 / 十大流通股东。

坑点防护见 engine-usage.md:daily_basic 分段≥700 校验、forecast 传 ProsperityScore
对象、ann_date≥20260101 过滤、stk_holdernumber dropna、量纲(erp 小数/分位百分数)。
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
import os
os.chdir("/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality
from davis_analyzer.distress import calculate_distress_score
from davis_analyzer.scoring import calculate_davis_double_score

client = TushareClient()
pro = client._pro_api() if hasattr(client, "_pro_api") else None
if pro is None:
    from stockhot.tushare_config import get_pro_api
    pro = get_pro_api(timeout=60)

TARGETS = {
    "603799.SH": "华友钴业",
    "600547.SH": "山东黄金",
    "002466.SZ": "天齐锂业",
    "000933.SZ": "神火股份",
    "601600.SH": "中国铝业",
}


def seg_daily_basic(ts_code: str, start: str, end: str) -> pd.DataFrame:
    frames = []
    cur = date(int(start[:4]), int(start[4:6]), int(start[6:]))
    fin = date(int(end[:4]), int(end[4:6]), int(end[6:]))
    while cur <= fin:
        seg_end = min(cur + timedelta(days=499), fin)
        df = pro.daily_basic(
            ts_code=ts_code, start_date=cur.strftime("%Y%m%d"),
            end_date=seg_end.strftime("%Y%m%d"),
            fields="ts_code,trade_date,close,pe_ttm,pb,ps,total_mv,turnover_rate,dv_ttm")
        if len(df):
            frames.append(df)
        cur = seg_end + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)


def pct_rank(s: pd.Series, latest: float) -> float:
    s = s.dropna()
    return float((s < latest).sum() / len(s) * 100) if len(s) else float("nan")


def manual_returns(ts_code: str) -> dict:
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=460)).strftime("%Y%m%d")
    px = pro.daily(ts_code=ts_code, start_date=start, end_date=end, fields="ts_code,trade_date,close")
    af = pro.adj_factor(ts_code=ts_code, start_date=start, end_date=end, fields="ts_code,trade_date,adj_factor")
    if not len(px) or not len(af):
        return {}
    m = px.merge(af, on=["ts_code", "trade_date"]).sort_values("trade_date").reset_index(drop=True)
    m["adj"] = m["close"] * m["adj_factor"]
    last = m.iloc[-1]
    out = {"last_close": float(last["close"]), "last_date": str(last["trade_date"])}
    for w in (20, 60, 120, 250):
        if len(m) > w:
            out[f"ret_{w}d"] = round((last["adj"] / m.iloc[-1 - w]["adj"] - 1) * 100, 1)
    return out


def holder_number(ts_code: str) -> dict:
    h = pro.stk_holdernumber(ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num")
    if not len(h):
        return {}
    h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(8)
    rows, prev = [], None
    for _, r in h.iterrows():
        num = int(r["holder_num"])
        rows.append({"end_date": str(r["end_date"]), "holder_num": num,
                     "chg_pct": round((num - prev) / prev * 100, 1) if prev else None})
        prev = num
    if len(rows) >= 4:
        nums = [r["holder_num"] for r in rows]
        rows_trend = "集中(动能增强)" if nums[-1] < nums[0] else "分散(动能减弱)"
    else:
        rows_trend = "数据不足"
    return {"rows": rows, "trend": rows_trend}


def top10_float(ts_code: str) -> dict:
    try:
        t = pro.top10_floatholders(ts_code=ts_code, period="20260331")
        if not len(t):
            t = pro.top10_floatholders(ts_code=ts_code)
            if not len(t):
                return {}
            latest_period = t["end_date"].max()
            t = t[t["end_date"] == latest_period]
        else:
            pass
        latest_period = t["end_date"].max()
        t2 = t[t["end_date"] == latest_period]
        return {"period": str(latest_period), "top10_pct_sum": round(float(pd.to_numeric(t2["ratio"], errors="coerce").sum()), 2)}
    except Exception:  # noqa: BLE001
        return {}


def forecast_detail(ts_code: str) -> list:
    fc = pro.forecast(ts_code=ts_code, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
    if not len(fc):
        return []
    fc = fc[pd.to_numeric(fc["ann_date"]) >= 20250101].sort_values("ann_date")
    out = []
    for _, r in fc.iterrows():
        np_min = pd.to_numeric(r.get("net_profit_min"), errors="coerce")
        np_max = pd.to_numeric(r.get("net_profit_max"), errors="coerce")
        out.append({
            "ann_date": str(r["ann_date"]), "end_date": str(r["end_date"]), "type": r["type"],
            "p_change": f"[{r['p_change_min']}, {r['p_change_max']}]%",
            "net_profit_yi": (f"{np_min/1e4:.1f}~{np_max/1e4:.1f}"
                              if pd.notna(np_min) and pd.notna(np_max) else None)})
    return out


def freshness(ts_code: str) -> dict:
    db = pro.daily_basic(ts_code=ts_code, limit=1)
    inc = pro.income(ts_code=ts_code, fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
    return {
        "latest_trade_date": str(db.iloc[0]["trade_date"]) if len(db) else "none",
        "latest_report_period": str(inc.iloc[0]["end_date"]) if len(inc) else "none",
        "latest_ann_date": str(inc.iloc[0]["ann_date"]) if len(inc) else "none",
    }


def main() -> None:
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
    result = {}
    for ts_code, name in TARGETS.items():
        print(f"\n════ {ts_code} {name} ════", flush=True)
        row: dict = {"name": name}
        # 1. 财务 + 景气
        fin = fetch_financial_data(client, ts_code, periods=12)
        fin_rows = [{
            "period": f.report_period, "revenue_yi": round(f.revenue / 1e8, 2),
            "net_profit_yi": round(float(f.net_profit) / 1e8, 2) if f.net_profit is not None else None,
            "yoy_rev": round(f.yoy_revenue_growth * 100, 1) if f.yoy_revenue_growth is not None else None,
            "yoy_profit": round(f.yoy_profit_growth * 100, 1) if f.yoy_profit_growth is not None else None,
            "roe": f.roe, "op_cf_yi": round(f.operating_cf / 1e8, 2) if f.operating_cf else None,
            "gross_margin": getattr(f, "grossprofit_margin", None),
        } for f in fin]
        row["financials"] = fin_rows
        row["fin_ts_code_check"] = fin[0].ts_code if fin else None
        pscore = calculate_prosperity_score(fin)
        row["prosperity"] = {
            "composite": round(pscore.composite_score, 2), "delta_g": round(pscore.delta_g, 2),
            "revenue_score": round(pscore.revenue_score, 1), "profit_score": round(pscore.profit_score, 1),
            "slope_score": round(pscore.slope_score, 1), "duration_score": round(pscore.duration_score, 1)}
        row["stage"] = str(classify_stock_stage(pscore))
        # 2. 估值分位
        db = seg_daily_basic(ts_code, start, end)
        if len(db) >= 700:
            pe = pd.to_numeric(db["pe_ttm"], errors="coerce")
            pb = pd.to_numeric(db["pb"], errors="coerce")
            ps = pd.to_numeric(db["ps"], errors="coerce")
            dv = pd.to_numeric(db["dv_ttm"], errors="coerce")
            row["valuation"] = {
                "trade_date": str(db["trade_date"].iloc[-1]), "days": len(db),
                "close": float(db["close"].iloc[-1]),
                "pe": round(float(pe.iloc[-1]), 2), "pe_pct": round(pct_rank(pe, float(pe.iloc[-1])), 1),
                "pb": round(float(pb.iloc[-1]), 2), "pb_pct": round(pct_rank(pb, float(pb.iloc[-1])), 1),
                "ps": round(float(ps.iloc[-1]), 2), "ps_pct": round(pct_rank(ps, float(ps.iloc[-1])), 1),
                "mv_yi": round(float(pd.to_numeric(db["total_mv"], errors="coerce").iloc[-1]) / 1e4, 1),
                "dv_ttm": round(float(dv.iloc[-1]), 2) if pd.notna(dv.iloc[-1]) else None,
                "pb_quantiles": {str(p): round(float(pb.quantile(p / 100)), 2) for p in (10, 25, 50, 75, 90, 95)},
                "pe_quantiles": {str(p): round(float(pe.quantile(p / 100)), 2) for p in (10, 25, 50, 75, 90, 95) if pd.notna(pe.quantile(p / 100))},
            }
        else:
            row["valuation"] = {"warn": f"rows={len(db)}<700"}
        # 3. 动量(引擎+手工)
        try:
            mom = analyze_momentum(client, ts_code)
            row["momentum_engine"] = {"score": mom.momentum_score, "rs": mom.rs_percentile,
                                      "window_returns": {k: round(v, 1) for k, v in (mom.window_returns or {}).items()}}
        except Exception as e:  # noqa: BLE001
            row["momentum_engine"] = {"err": str(e)}
        row["momentum_manual"] = manual_returns(ts_code)
        # 4. 股息
        div = analyze_dividend(client, ts_code)
        row["dividend"] = {"score": div.dividend_score, "years": div.consecutive_years,
                           "yield_pct": div.latest_yield_pct}
        # 5. 预告
        try:
            fc = analyze_forecast(client, ts_code, pscore)
            row["forecast_signal"] = {"leading_score": fc.leading_score, "type": fc.type,
                                      "p_change_mid": fc.p_change_mid, "is_stale": fc.is_stale}
        except Exception as e:  # noqa: BLE001
            row["forecast_signal"] = {"err": str(e)}
        try:
            rev = analyze_forecast_revision(client, ts_code)
            row["forecast_revision"] = {"direction": rev.revision_direction, "pp": rev.revision_pp}
        except Exception as e:  # noqa: BLE001
            row["forecast_revision"] = {"err": str(e)}
        row["forecast_detail"] = forecast_detail(ts_code)
        # 6. 筹码
        try:
            hc = analyze_holder_concentration(client, ts_code)
            row["holder_conc"] = {"score": hc.concentration_score, "trend": hc.trend,
                                  "latest_chg": hc.latest_chg_pct}
        except Exception as e:  # noqa: BLE001
            row["holder_conc"] = {"err": str(e)}
        row["holder_number"] = holder_number(ts_code)
        row["top10_float"] = top10_float(ts_code)
        # 7. 盈利质量
        try:
            pq = analyze_profitability_quality(fin)
            row["profitability"] = {"score": pq.quality_score, "gm": pq.latest_gross_margin,
                                    "gm_delta": pq.gross_margin_delta, "rd": pq.latest_rd_intensity}
        except Exception as e:  # noqa: BLE001
            row["profitability"] = {"err": str(e)}
        # 8. 相对估值
        try:
            from stockhot.valuation import analyze_relative_valuation
            rv = analyze_relative_valuation(client, ts_code)
            row["relative_val"] = {
                "pe_ratio_pct": getattr(rv, "pe_ratio_pct", None),
                "erp": getattr(rv, "erp", None),
                "quadrant": getattr(rv, "quadrant", None),
                "index_pe": getattr(rv, "index_pe", None),
                "index_pe_pct": getattr(rv, "index_pe_pct", None),
                "risk_free_rate": getattr(rv, "risk_free_rate", None)}
        except Exception as e:  # noqa: BLE001
            row["relative_val"] = {"err": str(e)}
        # 9. 困境 + 综合(trend 用动量分代理,估值分用 100-分位 反算)
        try:
            pb_pct = row.get("valuation", {}).get("pb_pct")
            pe_pct = row.get("valuation", {}).get("pe_pct")
            debt_ratio = (fin[0].total_debt / fin[0].total_assets) if fin and fin[0].total_assets else 0.5
            dscore = calculate_distress_score(
                eps_history=[f.eps for f in fin],
                pe_pct=(pe_pct or 50) / 100, pb_pct=(pb_pct or 50) / 100,
                debt_ratio=debt_ratio, operating_cf=fin[0].operating_cf if fin else 0,
                total_debt=fin[0].total_debt if fin else 0,
                total_assets=fin[0].total_assets if fin else 1,
                roe_history=[f.roe for f in fin],
                revenue_history=[f.revenue for f in fin],
                profit_history=[float(f.net_profit) for f in fin],
                delta_g=pscore.delta_g, ts_code=ts_code)
            row["distress"] = {"total": round(dscore.total_score, 2),
                               "l1": round(dscore.layer1_score, 2), "l2": round(dscore.layer2_score, 2),
                               "l3": round(dscore.layer3_score, 2)}
            vscore = max(0.0, 100 - 2 * abs(50 - (pb_pct or 50)))  # PB 分位越居中偏高越稳,简化代理
            mom_score = row.get("momentum_engine", {}).get("score") or 50.0
            final = calculate_davis_double_score(
                valuation_score=float(vscore), prosperity_score=pscore,
                distress_score=dscore, trend_score=float(mom_score),
                ts_code=ts_code, name=name)
            row["davis"] = {"final": round(final.final_score, 2), "rank": final.rank}
        except Exception as e:  # noqa: BLE001
            row["distress_or_davis_err"] = str(e)
        # 10. 时效
        row["freshness"] = freshness(ts_code)
        result[ts_code] = row
        print(f"  景气 {row['prosperity']['composite']}/ΔG {row['prosperity']['delta_g']} "
              f"阶段 {row['stage']} | PB {row.get('valuation', {}).get('pb')} "
              f"({row.get('valuation', {}).get('pb_pct')}%) | 财报期 {row['freshness']['latest_report_period']}", flush=True)

    out_path = "/home/leo/Projects/CodeAgentDashboard/davis_analyzer/studies/youse5_scoring_20260822.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nJSON → {out_path}", flush=True)


if __name__ == "__main__":
    main()
