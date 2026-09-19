"""退市股财务数据回补 — 激活「买入并承受退市」的完整幸存者代价通道.

0011 遗留 §6.2 / 0012 遗留 §6.3: 退市股此前只回补了价格+adj_factor, 无财务数据
→ 过不了因子闸 → 回测中从未被买入, 幸存者代价只经「名额挤占」通道传导(滚动口径
-2.4pp 为下界)。本脚本为 255 只有行情的退市股回补 8 个财务端点, 使其在研究上下文
(MARKET_DB_ATTACH_DELISTED=1)中可被打分/可被买入。

数据落点: TushareClient 缓存通道 → market_data.db 的 daily_basic/financial 等缓存表
(死代码的行对实盘惰性——实盘只查询活股代码; 0011 refix 腿已有同型先例)。

端点: daily_basic(估值分位)/dividend/forecast/stk_holdernumber/
      income/balancesheet/cashflow/fina_indicator
用量: 255 × 8 ≈ 2040 次 API(限流 400/min ≈ 5-6 分钟), 幂等可续跑。
用法: .venv/bin/python scripts/backfill/backfill_delisted_financials.py [--limit N]
输出: logs/backfill_delisted_financials.json(逐端点行数汇总)
"""
import os, sys, time, json
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")

import sqlite3
from stockhot.data_layer.market_db import get_connection, DELISTED_DB_PATH
from stockhot.storage.database import init_database
from davis_analyzer.core.tushare_client import TushareClient

init_database()
client = TushareClient()
START, END = "20150105", "20260912"
SUMMARY_PATH = "logs/backfill_delisted_financials.json"

ENDPOINTS = [
    ("daily_basic", "get_daily_basic"),
    ("dividend", "get_dividend"),
    ("forecast", "get_forecast"),
    ("stk_holdernumber", "get_stk_holdernumber"),
    ("income", "get_income"),
    ("balancesheet", "get_balancesheet"),
    ("cashflow", "get_cashflow"),
    ("fina_indicator", "get_fina_indicator"),
]


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    # 目标: 研究库里有行情的退市股(84 只 2015 前老代码无意义)
    with sqlite3.connect(DELISTED_DB_PATH) as c:
        codes = [r[0] for r in c.execute(
            "SELECT DISTINCT ts_code FROM daily_price_delisted ORDER BY ts_code")]
    if limit:
        codes = codes[:limit]
    print(f"目标退市股 {len(codes)} 只 × {len(ENDPOINTS)} 端点 ≈ {len(codes)*len(ENDPOINTS)} 次 API "
          f"(预估 {len(codes)*len(ENDPOINTS)/400:.1f} 分钟)")

    summary = {name: {"ok": 0, "empty": 0, "fail": []} for name, _ in ENDPOINTS}
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        for ep_name, getter in ENDPOINTS:
            try:
                df = getattr(client, getter)(code, START, END)
                if df is None or df.empty:
                    summary[ep_name]["empty"] += 1
                else:
                    summary[ep_name]["ok"] += 1
            except Exception as e:
                summary[ep_name]["fail"].append(code)
        if i % 25 == 0:
            print(f"  {i}/{len(codes)} ({time.time()-t0:.0f}s)", flush=True)
    summary["_meta"] = {"n_codes": len(codes), "elapsed_min": round((time.time()-t0)/60, 1),
                        "finished_at": time.strftime("%F %T")}
    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    for name, _ in ENDPOINTS:
        s = summary[name]
        print(f"  {name:<18} ok={s['ok']:>3} empty={s['empty']:>3} fail={len(s['fail'])}")
    print(f"完成 → {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
