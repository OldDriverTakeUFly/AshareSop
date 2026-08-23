#!/usr/bin/env python3
"""长丝双雄(桐昆601233/新凤鸣603225)研报数据包采集脚本.

采集：时效校验 / 财务明细 / 3年估值(分段直连) / 完整四维评分 /
5因子引擎 / 股东户数 / 十大流通股东 / 分红 / 手工动量复核 / 相对估值 / 同业行情。
输出: .sisyphus/evidence/changsi/data_pack.json
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()
import sys  # noqa: E402

sys.path.insert(0, os.getcwd())

from stockhot.tushare_config import get_pro_api  # noqa: E402

TARGETS = {"601233.SH": "桐昆股份", "603225.SH": "新凤鸣"}
PEERS = ["600346.SH", "002493.SZ", "000703.SZ", "000301.SZ", "002064.SZ"]
OUT = Path(".sisyphus/evidence/changsi/data_pack.json")
END_D = date(2026, 8, 14)
START_D = END_D - timedelta(days=1120)

pro = get_pro_api(timeout=60)
result: dict = {}


def seg_daily_basic(ts_code: str, start: date, end: date) -> pd.DataFrame:
    """分段直连 daily_basic(≤480天/段), concat 后 reset_index, 规避 22 天增量坑."""
    frames = []
    cur = start
    while cur < end:
        seg_end = min(cur + timedelta(days=480), end)
        for attempt in range(3):
            try:
                seg = pro.daily_basic(
                    ts_code=ts_code,
                    start_date=cur.strftime("%Y%m%d"),
                    end_date=seg_end.strftime("%Y%m%d"),
                    fields="ts_code,trade_date,close,pe_ttm,pb,ps_ttm,total_mv,turnover_rate",
                )
                if len(seg) or attempt == 2:
                    if len(seg):
                        frames.append(seg)
                    break
            except Exception:
                time.sleep(1.5)
        cur = seg_end + timedelta(days=1)
        time.sleep(0.35)
    if not frames:
        return pd.DataFrame()
    return (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("trade_date")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )


def val_summary(db: pd.DataFrame) -> dict:
    out = {}
    mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
    for name in ["pb", "ps_ttm", "pe_ttm"]:
        s = pd.to_numeric(db[name], errors="coerce").dropna()
        if len(s):
            cur_v = s.iloc[-1]
            out[name] = {
                "latest": float(cur_v),
                "latest_date": str(db["trade_date"].iloc[-1]),
                "pct": float((s < cur_v).sum() / len(s) * 100),
                **{f"q{p}": float(s.quantile(p / 100)) for p in [10, 25, 50, 75, 90, 95]},
                "n": int(len(s)),
            }
    out["total_mv_yi"] = float(mv.iloc[-1] / 1e4)
    out["close_latest"] = float(db["close"].iloc[-1])
    out["rows"] = int(len(db))
    out["first_last"] = [str(db["trade_date"].iloc[0]), str(db["trade_date"].iloc[-1])]
    return out


def manual_momentum(ts_code: str) -> dict:
    """pro.daily + adj_factor 手工复权复核多窗口收益."""
    adj = pro.adj_factor(ts_code=ts_code, start_date=(END_D - timedelta(days=420)).strftime("%Y%m%d"),
                         end_date=END_D.strftime("%Y%m%d"))
    daily = pro.daily(ts_code=ts_code, start_date=(END_D - timedelta(days=420)).strftime("%Y%m%d"),
                      end_date=END_D.strftime("%Y%m%d"))
    for _ in range(3):
        if len(daily) > 100:
            break
        time.sleep(1.0)
        daily = pro.daily(ts_code=ts_code, start_date=(END_D - timedelta(days=420)).strftime("%Y%m%d"),
                          end_date=END_D.strftime("%Y%m%d"))
    if not len(daily) or not len(adj):
        return {}
    m = daily.merge(adj[["trade_date", "adj_factor"]], on="trade_date", how="left")
    m = m.sort_values("trade_date").reset_index(drop=True)
    m["qfq"] = m["close"] * m["adj_factor"]
    out = {}
    for w in [20, 60, 120, 250]:
        if len(m) > w:
            out[f"d{w}"] = float(m["qfq"].iloc[-1] / m["qfq"].iloc[-1 - w] - 1) * 100
    out["n_days"] = int(len(m))
    out["last_date"] = str(m["trade_date"].iloc[-1])
    return out


def holder_trend(ts_code: str, n: int = 10) -> dict:
    h = pro.stk_holdernumber(ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num")
    h = h.dropna(subset=["holder_num"]).sort_values("end_date").tail(n)
    rows = []
    prev = None
    for _, r in h.iterrows():
        num = int(r["holder_num"])
        chg = (num - prev) / prev * 100 if prev else None
        rows.append({"end_date": r["end_date"], "ann_date": r["ann_date"], "holder_num": num, "chg_pct": chg})
        prev = num
    return {"rows": rows}


def top10_history(ts_code: str) -> dict:
    t10 = pro.top10_floatholders(ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_name,hold_ratio")
    if not len(t10):
        return {}
    ends = sorted(t10["end_date"].unique())[-5:]
    hist = []
    for e in ends:
        sub = t10[t10["end_date"] == e]
        hist.append({
            "end_date": e,
            "total_ratio": float(pd.to_numeric(sub["hold_ratio"], errors="coerce").sum()),
        })
    latest_end = ends[-1]
    latest = t10[t10["end_date"] == latest_end]
    return {
        "history": hist,
        "latest_end_date": latest_end,
        "latest_holders": latest[["holder_name", "hold_ratio"]].to_dict("records"),
    }


for ts_code, name in TARGETS.items():
    print(f"\n===== {name} {ts_code} =====")
    pack: dict = {"name": name}

    # 1. 时效校验 + 快照
    db1 = pro.daily_basic(ts_code=ts_code, limit=3)
    pack["freshness"] = {
        "daily_basic_latest": db1.iloc[0]["trade_date"] if len(db1) else None,
        "snapshot": db1.head(1)[["trade_date", "close", "pe_ttm", "pb", "ps_ttm", "total_mv", "turnover_rate"]].to_dict("records"),
    }
    inc = pro.income(ts_code=ts_code, fields="ts_code,ann_date,end_date,total_revenue,n_income,n_income_attr_p,rd_exp", limit=12)
    pack["income_native"] = inc.to_dict("records")
    fc = pro.forecast(ts_code=ts_code, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max")
    pack["forecast"] = fc.to_dict("records")

    # 2. 财务指标
    fi = pro.fina_indicator(ts_code=ts_code, fields="ts_code,end_date,grossprofit_margin,netprofit_margin,roe,debt_to_assets,ocf_to_profit,rd_exp_to_revenue", limit=12)
    pack["fina_indicator"] = fi.to_dict("records")
    bs = pro.balancesheet(ts_code=ts_code, fields="ts_code,end_date,total_assets,total_liab,total_equity,inventory,fix_assets,cip,st_borr,lt_borr", limit=8)
    pack["balancesheet"] = bs.to_dict("records")
    cf = pro.cashflow(ts_code=ts_code, fields="ts_code,end_date,n_cashflow_act,n_cashflow_inv_act,n_cashflow_fnc_act,free_cashflow", limit=10)
    pack["cashflow"] = cf.to_dict("records")

    # 3. 3 年估值(分段直连) + 5 年 PB 参照
    db = seg_daily_basic(ts_code, START_D, END_D)
    pack["valuation_3y"] = val_summary(db)
    db5 = seg_daily_basic(ts_code, END_D - timedelta(days=1850), START_D)
    if len(db5):
        pb5 = pd.to_numeric(db5["pb"], errors="coerce").dropna()
        if len(pb5):
            cur = pack["valuation_3y"]["pb"]["latest"]
            pack["pb_5y_extra_pct"] = float((pb5 < cur).sum() / (len(pb5) + pack["valuation_3y"]["pb"]["n"]) * 100)
            pack["pb_5y_note"] = f"2021-2023 段 {len(pb5)} 点中低于当前值比例 {float((pb5 < cur).sum()/len(pb5)*100):.1f}%"

    # 4. 手工动量复核
    pack["momentum_manual"] = manual_momentum(ts_code)

    # 5. 股东户数 + 十大流通股东
    pack["holder_number"] = holder_trend(ts_code)
    pack["top10_float"] = top10_history(ts_code)

    # 6. 分红
    div = pro.dividend(ts_code=ts_code, fields="ts_code,end_date,ann_date,div_proc,cash_div_taxable,cash_div,base_share")
    pack["dividend"] = div.tail(14).to_dict("records") if len(div) else []

    result[ts_code] = pack

# ── 7. davis_analyzer 完整四维评分 + 5 因子 ──
print("\n===== davis_analyzer 引擎评分 =====")
from davis_analyzer.tushare_client import TushareClient  # noqa: E402
from davis_analyzer.financial_fetcher import fetch_financial_data  # noqa: E402
from davis_analyzer.prosperity import calculate_prosperity_score  # noqa: E402
from davis_analyzer.prosperity_sector import classify_stock_stage  # noqa: E402
from davis_analyzer.distress import calculate_distress_score  # noqa: E402
from davis_analyzer.scoring import calculate_davis_double_score  # noqa: E402
from davis_analyzer.valuation import calculate_valuation_score, detect_cyclical  # noqa: E402
from davis_analyzer.types import StockInfo, ValuationData  # noqa: E402
from davis_analyzer.momentum import analyze_momentum  # noqa: E402
from davis_analyzer.dividend import analyze_dividend  # noqa: E402
from davis_analyzer.forecast import analyze_forecast  # noqa: E402
from davis_analyzer.holder_concentration import analyze_holder_concentration  # noqa: E402
from davis_analyzer.profitability import analyze_profitability_quality  # noqa: E402
from davis_analyzer.trend import batch_trend  # noqa: E402

client = TushareClient()

for ts_code, name in TARGETS.items():
    print(f"-- engine {name}")
    eng: dict = {}
    fin = fetch_financial_data(client, ts_code, periods=12)
    eng["fin_periods"] = [
        {
            "report_period": f.report_period, "revenue_yi": float(f.revenue) / 1e8 if f.revenue else None,
            "net_profit_yi": float(f.net_profit) / 1e8 if f.net_profit else None,
            "eps": f.eps, "roe": f.roe,
            "operating_cf_yi": float(f.operating_cf) / 1e8 if f.operating_cf else None,
            "total_debt_yi": float(f.total_debt) / 1e8 if f.total_debt else None,
            "total_assets_yi": float(f.total_assets) / 1e8 if f.total_assets else None,
            "yoy_rev": f.yoy_revenue_growth, "yoy_prof": f.yoy_profit_growth,
        }
        for f in fin
    ]

    pscore = calculate_prosperity_score(fin)
    stage = classify_stock_stage(pscore)
    eng["prosperity"] = {
        "composite": pscore.composite_score, "delta_g": pscore.delta_g,
        "revenue_score": pscore.revenue_score, "profit_score": pscore.profit_score,
        "slope_score": pscore.slope_score, "duration_score": pscore.duration_score,
        "stage": str(stage),
    }

    # 估值历史(手工构造,规避 22 天坑): 用分段直连的 db
    db = seg_daily_basic(ts_code, START_D, END_D)
    val_rows = []
    for _, r in db.iterrows():
        if pd.notna(r["pb"]) and pd.notna(r["pe_ttm"]):
            val_rows.append(ValuationData(
                ts_code=ts_code, trade_date=str(r["trade_date"]),
                pe_ttm=float(r["pe_ttm"]), pb=float(r["pb"]),
                ps=float(r["ps_ttm"]) if pd.notna(r["ps_ttm"]) else None,
                total_mv=float(r["total_mv"]) if pd.notna(r["total_mv"]) else None,
            ))
    val_rows.sort(key=lambda v: v.trade_date, reverse=True)
    industry = ""
    try:
        stock_df = client.get_stock_list()
        row = stock_df[stock_df["ts_code"] == ts_code]
        if not row.empty:
            industry = str(row.iloc[0].get("industry", "") or "")
    except Exception:
        pass
    eng["industry"] = industry
    eng["is_cyclical"] = detect_cyclical(industry)
    sinfo = StockInfo(ts_code=ts_code, name=name, industry=industry, list_status="L", is_cyclical=eng["is_cyclical"])
    if val_rows:
        val_score, pe_pct, pb_pct = calculate_valuation_score(val_rows, sinfo.is_cyclical)
        eng["valuation_score"] = {"score": val_score, "pe_pct": pe_pct, "pb_pct": pb_pct, "n_points": len(val_rows)}

        # 趋势
        dates = pd.to_datetime([v.trade_date for v in val_rows][::-1], format="%Y%m%d")
        daily_pe = pd.Series([v.pe_ttm for v in val_rows][::-1], index=dates)
        daily_pb = pd.Series([v.pb for v in val_rows][::-1], index=dates)
        trend_map = batch_trend({ts_code: (daily_pe, daily_pb)}, {ts_code: sinfo})
        eng["trend_score"] = trend_map.get(ts_code, 50.0)

    # 困境
    latest = fin[0]
    total_debt = latest.total_debt or 0.0
    total_assets = latest.total_assets or 0.0
    operating_cf = latest.operating_cf or 0.0
    debt_ratio = total_debt / total_assets if total_assets > 0 else 0.0
    pe_pct_v = eng.get("valuation_score", {}).get("pe_pct", 0.5)
    pb_pct_v = eng.get("valuation_score", {}).get("pb_pct", 0.5)
    dscore = calculate_distress_score(
        eps_history=[f.eps for f in fin], pe_pct=pe_pct_v, pb_pct=pb_pct_v,
        debt_ratio=debt_ratio, operating_cf=operating_cf,
        total_debt=total_debt, total_assets=total_assets,
        roe_history=[f.roe for f in fin],
        revenue_history=[f.yoy_revenue_growth or 0.0 for f in fin],
        profit_history=[f.yoy_profit_growth or 0.0 for f in fin],
        delta_g=pscore.delta_g, ts_code=ts_code,
    )
    eng["distress"] = {"total": dscore.total_score, "l1": dscore.layer1_score, "l2": dscore.layer2_score, "l3": dscore.layer3_score}

    davis = calculate_davis_double_score(
        valuation_score=eng.get("valuation_score", {}).get("score", 50.0),
        prosperity_score=pscore.composite_score,
        distress_score=dscore.total_score,
        trend_score=eng.get("trend_score", 50.0),
        ts_code=ts_code, name=name,
    )
    eng["davis_double"] = {
        "final_score": davis.final_score, "rank": davis.rank,
        "valuation": davis.valuation_score, "prosperity": davis.prosperity_score,
        "distress": davis.distress_score, "trend": davis.trend_score,
    }

    # 5 因子
    try:
        mom = analyze_momentum(client, ts_code)
        eng["momentum_engine"] = {"score": mom.momentum_score, "window_returns": mom.window_returns} if mom else None
    except Exception as e:
        eng["momentum_engine"] = {"error": str(e)}
    try:
        divsig = analyze_dividend(client, ts_code)
        eng["dividend_engine"] = {"score": divsig.dividend_score, "years": divsig.consecutive_years, "yield": divsig.latest_yield_pct}
    except Exception as e:
        eng["dividend_engine"] = {"error": str(e)}
    try:
        fcsig = analyze_forecast(client, ts_code, pscore)
        eng["forecast_engine"] = {"score": fcsig.leading_score, "type": fcsig.type, "p_change_mid": fcsig.p_change_mid} if fcsig else None
    except Exception as e:
        eng["forecast_engine"] = {"error": str(e)}
    try:
        hc = analyze_holder_concentration(client, ts_code)
        eng["holder_engine"] = {"score": hc.concentration_score, "trend": hc.trend, "latest_chg": hc.latest_chg_pct} if hc else None
    except Exception as e:
        eng["holder_engine"] = {"error": str(e)}
    try:
        pq = analyze_profitability_quality(fin)
        eng["profitability_engine"] = {
            "score": pq.quality_score, "gross_margin": pq.latest_gross_margin,
            "gross_margin_delta": pq.gross_margin_delta, "rd_intensity": pq.latest_rd_intensity,
        }
    except Exception as e:
        eng["profitability_engine"] = {"error": str(e)}

    result[ts_code]["engine"] = eng

# ── 8. 相对估值 ──
print("\n===== 相对估值 =====")
from stockhot.valuation import analyze_relative_valuation  # noqa: E402

for ts_code, name in TARGETS.items():
    try:
        rv = analyze_relative_valuation(pro, ts_code, name)
        result[ts_code]["relative_valuation"] = {
            "pe_ratio": rv.pe_ratio, "pe_ratio_pct": rv.pe_ratio_pct,
            "erp": rv.erp, "quadrant": rv.quadrant, "quadrant_label": rv.quadrant_label,
            "composite_verdict": rv.composite_verdict, "signals": rv.signals,
            "index_pe": rv.index_pe, "index_pe_pct": rv.index_pe_pct,
            "risk_free_rate": rv.risk_free_rate,
        }
    except Exception as e:
        result[ts_code]["relative_valuation"] = {"error": str(e)}

# ── 9. 同业行情(20260814) ──
peer_rows = []
for p in list(TARGETS.keys()) + PEERS:
    try:
        r = pro.daily_basic(ts_code=p, trade_date="20260814")
        info = pro.stock_basic(ts_code=p, fields="ts_code,name,industry")
        if len(r):
            row = {
                "ts_code": p, "name": info.iloc[0]["name"] if len(info) else "",
                "close": float(r.iloc[0]["close"]),
                "pe_ttm": float(r.iloc[0]["pe_ttm"]) if pd.notna(r.iloc[0]["pe_ttm"]) else None,
                "pb": float(r.iloc[0]["pb"]),
                "total_mv_yi": float(r.iloc[0]["total_mv"]) / 1e4,
                "dv_ttm": float(r.iloc[0]["dv_ttm"]) if pd.notna(r.iloc[0].get("dv_ttm")) else None,
            }
            peer_rows.append(row)
    except Exception as e:
        peer_rows.append({"ts_code": p, "error": str(e)})
    time.sleep(0.4)
result["peers_20260814"] = peer_rows

# 基准指数
for tag, code in [("sh000001", "000001.SH"), ("hs300", "000300.SH")]:
    try:
        r = pro.index_daily(ts_code=code, start_date="20251231", end_date="20260814").sort_values("trade_date")
        if len(r):
            result[f"idx_{tag}"] = {"ytd_pct": float((r["close"].iloc[-1] / r["close"].iloc[0] - 1) * 100)}
    except Exception:
        pass

OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\n[saved] {OUT}")

# 摘要打印
for ts_code, name in TARGETS.items():
    p = result[ts_code]
    v3 = p.get("valuation_3y", {})
    eng = p.get("engine", {})
    print(f"\n### {name} {ts_code}")
    print(f"  快照日: {p['freshness']['daily_basic_latest']} close={v3.get('close_latest')} mv={v3.get('total_mv_yi', 0):.0f}亿")
    print(f"  PB: {v3.get('pb', {}).get('latest')} ({v3.get('pb', {}).get('pct', 0):.1f}%分位, n={v3.get('pb', {}).get('n')})")
    print(f"  PE_TTM: {v3.get('pe_ttm', {}).get('latest')} ({v3.get('pe_ttm', {}).get('pct', 0):.1f}%分位)")
    print(f"  动量手工: {p.get('momentum_manual')}")
    print(f"  景气度: {eng.get('prosperity')}")
    print(f"  davis: {eng.get('davis_double')}")
