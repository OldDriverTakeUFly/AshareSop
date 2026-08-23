# 创新药细分板块标的轻量筛选（选题讨论用）
# 输出: /tmp/pharma_screen_result.txt  供 2026-08-22 选题讨论
# 口径: 估值3年分位(get_daily_basic, 校验行数), 成长(最新季报同比), 业绩预告, 60/250d涨幅(未复权,误差注明)
import os
import sys

sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")
os.chdir("/home/leo/Projects/CodeAgentDashboard")
from dotenv import load_dotenv

load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()  # 防 .env 的 /app 值破坏 stockhot mkdir

import warnings
from datetime import date, timedelta

import pandas as pd

warnings.filterwarnings("ignore")

from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.tushare_client import TushareClient

POOL = {
    # ts_code: (名称, 细分板块)
    "688506.SH": ("百利天恒", "ADC/双抗ADC"),
    "688331.SH": ("荣昌生物", "ADC+自免"),
    "688266.SH": ("泽璟制药", "多靶点小分子/双抗"),
    "002422.SZ": ("科伦药业", "ADC(控股科伦博泰)"),
    "600276.SH": ("恒瑞医药", "综合龙头/GLP-1"),
    "000963.SZ": ("华东医药", "GLP-1/医美"),
    "688166.SH": ("博瑞医药", "GLP-1 BGM0504"),
    "603087.SH": ("甘李药业", "胰岛素/GLP-1"),
    "688253.SH": ("智翔金力", "自免单抗"),
    "688336.SH": ("三生国健", "自免双抗"),
    "600196.SH": ("复星医药", "CAR-T/综合"),
    "603259.SH": ("药明康德", "CXO龙头"),
    "002821.SZ": ("凯莱英", "CXO/CDMO"),
    "300347.SZ": ("泰格医药", "CXO/临床CRO"),
    "688131.SH": ("皓元医药", "CXO/前端+工具"),
    "603127.SH": ("昭衍新药", "CXO/安评"),
    "300363.SZ": ("博腾股份", "CXO/CDMO"),
    "600535.SH": ("天士力", "中药创新"),
    "600557.SH": ("康缘药业", "中药创新"),
    "002603.SZ": ("以岭药业", "中药创新"),
    "688235.SH": ("百济神州", "Biotech平台"),
    "688192.SH": ("迪哲医药", "Biotech平台"),
    "688578.SH": ("艾力斯", "肺癌精准"),
    "300558.SZ": ("贝达药业", "肺癌精准"),
    "688222.SH": ("成都先导", "AI/DEL制药"),
    "300725.SZ": ("药石科技", "AI/分子砌块"),
    "688356.SH": ("键凯科技", "上游PEG"),
    "688690.SH": ("纳微科技", "上游填料"),
    "688293.SH": ("奥浦迈", "上游培养基"),
}

client = TushareClient()
from stockhot.tushare_config import get_pro_api

pro = get_pro_api(timeout=30)

end = date.today().strftime("%Y%m%d")
start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
px_start = (date.today() - timedelta(days=420)).strftime("%Y%m%d")


def pct(series: pd.Series) -> tuple[float | None, float | None]:
    """返回(最新值, 3年分位%)。分位基于有效值>=100个, 否则None。"""
    s = series.dropna()
    if len(s) == 0:
        return None, None
    cur = float(s.iloc[-1])
    p = (s < cur).sum() / len(s) * 100 if len(s) >= 100 else None
    return cur, p


rows = []
for ts_code, (name, seg) in POOL.items():
    rec: dict = {"代码": ts_code, "名称": name, "板块": seg}
    # ── 估值 ──
    try:
        db = client.get_daily_basic(ts_code, start, end)
        db = db.sort_values("trade_date").reset_index(drop=True)
        rec["估值天数"] = len(db)
        rec["市值亿"] = round(float(pd.to_numeric(db["total_mv"], errors="coerce").iloc[-1]) / 1e4, 0)
        pe, pe_p = pct(pd.to_numeric(db["pe_ttm"], errors="coerce"))
        pb, pb_p = pct(pd.to_numeric(db["pb"], errors="coerce"))
        ps, ps_p = pct(pd.to_numeric(db["ps"], errors="coerce"))
        rec["PE"] = round(pe, 0) if pe else None
        rec["PE分位"] = round(pe_p, 0) if pe_p is not None else None
        rec["PB"] = round(pb, 1) if pb else None
        rec["PB分位"] = round(pb_p, 0) if pb_p is not None else None
        rec["PS"] = round(ps, 1) if ps else None
        rec["PS分位"] = round(ps_p, 0) if ps_p is not None else None
    except Exception as e:
        rec["err_valuation"] = str(e)[:40]
    # ── 财务(最新季报) ──
    try:
        fin = fetch_financial_data(client, ts_code, periods=5)
        if fin:
            f0 = fin[0]
            rec["最新期"] = f0.report_period
            rec["营收yoy%"] = round(f0.yoy_revenue_growth * 100, 1) if f0.yoy_revenue_growth is not None else None
            rec["净利yoy%"] = round(f0.yoy_profit_growth * 100, 1) if f0.yoy_profit_growth is not None else None
            rec["净利亿"] = round(float(f0.net_profit) / 1e8, 2) if f0.net_profit is not None else None
    except Exception as e:
        rec["err_fin"] = str(e)[:40]
    # ── 业绩预告 ──
    try:
        fc = pro.forecast(ts_code=ts_code, fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
        if len(fc):
            r = fc.iloc[0]
            rec["预告"] = f"{r['end_date'][:4]}{'H1' if r['end_date'][4:6]=='06' else 'FY'} {r['type']} {r['p_change_min']}~{r['p_change_max']}%"
    except Exception:
        pass
    # ── 涨幅(未复权, 配息股有误差) ──
    try:
        d = pro.daily(ts_code=ts_code, start_date=px_start, end_date=end)
        d = d.sort_values("trade_date").reset_index(drop=True)
        c = d["close"]
        rec["60d%"] = round((c.iloc[-1] / c.iloc[-61] - 1) * 100, 0) if len(c) > 61 else None
        rec["250d%"] = round((c.iloc[-1] / c.iloc[-251] - 1) * 100, 0) if len(c) > 251 else None
    except Exception:
        pass
    rows.append(rec)
    print(f"done {name}", flush=True)

df = pd.DataFrame(rows)
out = "/tmp/pharma_screen_result.txt"
with open(out, "w", encoding="utf-8") as fh:
    fh.write(f"# 创新药细分标的轻量筛选 {date.today()} (未复权涨幅,配息股误差; 分位=3年)\n\n")
    fh.write(df.to_string(index=False))
    fh.write("\n")
df.to_csv("/tmp/pharma_screen_result.csv", index=False)
print("saved", out)
