"""数据时效性校验脚本（freshness check）。

写研报前先跑这个，确认引擎取到的数据新鲜度，避免「报告日 6/27 但数据是 Q1」
却未标注的陷阱。可复用模板见 engine-usage.md §10。

对每只标的检查：
  - daily_basic 最新交易日（估值数据的时效）
  - income 最新报告期 end_date + 披露日 ann_date（财务数据的时效）
  - forecast 最新业绩预告（H1 预告常先于半年报披露，是更早的信号）
"""
import os
from dotenv import load_dotenv
# override=True: shell 可能导出 stale token，强制 .env 的新 token 生效。
load_dotenv("/home/leo/Projects/CodeAgentDashboard/.env", override=True)
# Re-pin PROJECT_ROOT: .env 里是 Docker 值 /app，会破坏 stockhot.core.config 的 mkdir。
os.environ["PROJECT_ROOT"] = "/home/leo/Projects/CodeAgentDashboard"

from datetime import date

from stockhot.tushare_config import get_pro_api

TARGETS = [
    ("688146.SH", "中船特气"),
    ("688549.SH", "中巨芯"),
    ("300346.SZ", "南大光电"),
    ("002971.SZ", "和远气体"),
    ("600378.SH", "昊华科技"),
]

REPORT_DATE = date(2026, 6, 27)


def freshness_check():
    pro = get_pro_api(timeout=30)
    today_str = REPORT_DATE.strftime("%Y%m%d")
    print(f"=== 数据时效性校验（报告日 {REPORT_DATE}）===\n")

    for code, nm in TARGETS:
        print(f"--- {nm} ({code}) ---")
        # 1. daily_basic 最新交易日（估值时效）
        try:
            db = pro.daily_basic(ts_code=code, limit=1)
            if len(db):
                td = db.iloc[0]["trade_date"]
                print(f"  [估值] daily_basic 最新交易日: {td}")
            else:
                print("  [估值] daily_basic 无数据")
        except Exception as e:
            print(f"  [估值] daily_basic 查询失败: {e}")

        # 2. income 最新财报（财务时效）
        try:
            inc = pro.income(
                ts_code=code, fields="ts_code,ann_date,end_date,f_ann_date", limit=1
            )
            if len(inc):
                r = inc.iloc[0]
                print(
                    f"  [财报] 最新报告期 end_date={r['end_date']} "
                    f"披露日 ann_date={r['ann_date']}"
                )
            else:
                print("  [财报] income 无数据")
        except Exception as e:
            print(f"  [财报] income 查询失败: {e}")

        # 3. forecast 最新业绩预告（领先信号）
        try:
            fc = pro.forecast(
                ts_code=code,
                fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max",
            )
            if len(fc):
                r = fc.iloc[0]
                print(
                    f"  [预告] {r['type']} ann_date={r['ann_date']} "
                    f"end_date={r['end_date']} "
                    f"同比=[{r['p_change_min']}, {r['p_change_max']}]%"
                )
            else:
                print("  [预告] 无业绩预告")
        except Exception as e:
            print(f"  [预告] forecast 查询失败: {e}")
        print()


if __name__ == "__main__":
    freshness_check()
