"""化工×AI 参与度研报——2026 中报横切面取数脚本。

对约 30 家化工×AI 链上 A 股标的，拉取：
  - 26H1/25H1/26Q1 营收与归母净利（income，report_type=1 合并报表，按 ann_date 去重取最新）
  - 26H1 业绩预告类型（forecast，提前信号）
  - 最新 daily_basic 估值（pe_ttm/pb/total_mv）

输出 JSON 到 studies/chem_ai_h1_2026.json，供研报引用（来源标签：tushare income/daily_basic/forecast, 拉取日 20260830）。
"""
import json
import os
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

import pandas as pd

from stockhot.tushare_config import get_pro_api

# (ts_code, name, segment)  segment = AI 链五段划分
TARGETS = [
    # ── 段1 晶圆制造电子化学品 ──
    ("688549.SH", "中巨芯", "晶圆-湿化学品"),
    ("603078.SH", "江化微", "晶圆-湿化学品"),
    ("300655.SZ", "晶瑞电材", "晶圆-湿化学品/光刻胶"),
    ("688545.SH", "兴福电子", "晶圆-湿化学品"),
    ("603931.SH", "格林达", "晶圆-湿化学品"),
    ("002407.SZ", "多氟多", "晶圆-湿化学品"),
    ("300346.SZ", "南大光电", "晶圆-光刻胶/特气"),
    ("603650.SH", "彤程新材", "晶圆-光刻胶"),
    ("688019.SH", "安集科技", "晶圆-CMP"),
    ("300054.SZ", "鼎龙股份", "晶圆-CMP/封装PI"),
    ("688268.SH", "华特气体", "晶圆-特气"),
    ("688106.SH", "金宏气体", "晶圆-特气"),
    ("688146.SH", "中船特气", "晶圆-特气"),
    ("600378.SH", "昊华科技", "晶圆-特气/氟材料"),
    ("002409.SZ", "雅克科技", "晶圆-前驱体/特气"),
    # ── 段2 先进封装材料 ──
    ("688535.SH", "华海诚科", "封装-EMC"),
    ("688035.SH", "德邦科技", "封装-DAF/底填/TIM"),
    ("688720.SH", "艾森股份", "封装-电镀液/PSPI"),
    ("300398.SZ", "飞凯材料", "封装-平台(锡球/胶)"),
    ("688300.SH", "联瑞新材", "封装/CCL-球硅填料"),
    ("301373.SZ", "凌玮科技", "CCL-球硅填料"),
    # ── 段3 树脂/基板材料 ──
    ("605589.SH", "圣泉集团", "树脂-PPO/碳氢"),
    ("601208.SH", "东材科技", "树脂-碳氢/膜"),
    ("603002.SH", "宏昌电子", "树脂-环氧"),
    ("603010.SH", "万盛股份", "树脂-阻燃剂BDP"),
    # ── 段4 散热/氟材料 ──
    ("600160.SH", "巨化股份", "散热-氟化液/制冷剂"),
    ("603379.SH", "三美股份", "散热-制冷剂/电子HF"),
    ("300037.SZ", "新宙邦", "散热-氟化液/电解液"),
    ("605020.SH", "永和股份", "散热-氟化工"),
    # ── 段5 其他功能材料 ──
    ("688323.SH", "瑞华泰", "PI膜"),
]

PERIODS = {"20260630": "h1_26", "20260331": "q1_26", "20250630": "h1_25", "20250331": "q1_25"}


def _pick_periods(df: pd.DataFrame) -> dict:
    """按报告期取最新披露的合并报表行，返回 {period: (rev, np, ann_date)}。"""
    out = {}
    if df is None or not len(df):
        return out
    df = df[df["report_type"] == "1"].copy()
    for end_date, tag in PERIODS.items():
        sub = df[df["end_date"] == end_date]
        if not len(sub):
            continue
        sub = sub.sort_values("ann_date").iloc[-1]
        rev = float(sub["total_revenue"]) if pd.notna(sub["total_revenue"]) else None
        np_ = float(sub["n_income"]) if pd.notna(sub["n_income"]) else None
        out[tag] = {"rev": rev, "np": np_, "ann_date": sub["ann_date"]}
    return out


def _yoy(cur, prev):
    if cur is None or prev is None or prev == 0:
        return None
    return round((cur / prev - 1.0) * 100, 2)


def main():
    pro = get_pro_api(timeout=30)
    results = {}

    for code, name, seg in TARGETS:
        row = {"name": name, "segment": seg}
        print(f"--- {name} ({code}) ---", flush=True)

        # 1) income 四个报告期
        try:
            inc = pro.income(
                ts_code=code,
                start_date="20250101",
                end_date="20261231",
                fields="ts_code,ann_date,end_date,f_ann_date,report_type,total_revenue,n_income",
            )
            periods = _pick_periods(inc)
            h1_26, h1_25 = periods.get("h1_26"), periods.get("h1_25")
            q1_26, q1_25 = periods.get("q1_26"), periods.get("q1_25")
            if h1_26:
                row["h1_26_rev"] = h1_26["rev"]
                row["h1_26_np"] = h1_26["np"]
                row["h1_26_ann"] = h1_26["ann_date"]
                row["h1_26_rev_yoy"] = _yoy(h1_26["rev"], h1_25["rev"] if h1_25 else None)
                row["h1_26_np_yoy"] = _yoy(h1_26["np"], h1_25["np"] if h1_25 else None)
                # 26Q2 单季 = H1 - Q1
                if q1_26 and h1_26["rev"] is not None and q1_26["rev"] is not None:
                    q2_rev = h1_26["rev"] - q1_26["rev"]
                    q2_np = (h1_26["np"] or 0) - (q1_26["np"] or 0)
                    if q1_25 and h1_25:
                        q2_rev_25 = (h1_25["rev"] or 0) - (q1_25["rev"] or 0)
                        q2_np_25 = (h1_25["np"] or 0) - (q1_25["np"] or 0)
                        row["q2_26_rev_yoy"] = _yoy(q2_rev, q2_rev_25 if q2_rev_25 else None)
                        row["q2_26_np_yoy"] = _yoy(q2_np, q2_np_25 if q2_np_25 else None)
                    row["q2_26_rev"] = round(q2_rev, 2)
                    row["q2_26_np"] = round(q2_np, 2)
        except Exception as e:
            row["income_err"] = str(e)

        # 2) 26H1 业绩预告
        try:
            fc = pro.forecast(
                ts_code=code, period="20260630",
                fields="ts_code,ann_date,type,p_change_min,p_change_max",
            )
            if fc is not None and len(fc):
                f = fc.sort_values("ann_date").iloc[-1]
                row["forecast_26h1"] = f["type"]
                row["forecast_ann"] = f["ann_date"]
        except Exception:
            pass

        # 3) 最新估值
        try:
            db = pro.daily_basic(ts_code=code, limit=1)
            if db is not None and len(db):
                d = db.iloc[0]
                row["pe_ttm"] = round(float(d["pe_ttm"]), 1) if pd.notna(d["pe_ttm"]) else None
                row["pb"] = round(float(d["pb"]), 2) if pd.notna(d["pb"]) else None
                row["total_mv_yi"] = round(float(d["total_mv"]) / 10000, 1) if pd.notna(d["total_mv"]) else None
                row["db_trade_date"] = d["trade_date"]
        except Exception as e:
            row["db_err"] = str(e)

        results[code] = row

    out_path = os.path.join(os.path.dirname(__file__), "chem_ai_h1_2026.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n已写入 {out_path}，共 {len(results)} 家")

    # 摘要打印（营收/净利 YoY + 估值）
    print(f"\n{'名称':<6}{'段':<14}{'26H1营收(亿)':>12}{'YoY%':>9}{'归母(亿)':>10}{'YoY%':>9}{'PE':>8}{'PB':>7}{'市值亿':>9}")
    for code, r in results.items():
        rev = r.get("h1_26_rev")
        rev = round(rev / 1e8, 2) if rev is not None else None
        np_ = r.get("h1_26_np")
        np_ = round(np_ / 1e8, 2) if np_ is not None else None
        ry = r.get("h1_26_rev_yoy")
        ny = r.get("h1_26_np_yoy")
        fc = r.get("forecast_26h1", "")
        print(
            f"{r['name']:<6}{r['segment']:<14}{str(rev):>12}{str(ry):>9}{str(np_):>10}{str(ny):>9}"
            f"{str(r.get('pe_ttm')):>8}{str(r.get('pb')):>7}{str(r.get('total_mv_yi')):>9} {fc}"
        )


if __name__ == "__main__":
    main()
