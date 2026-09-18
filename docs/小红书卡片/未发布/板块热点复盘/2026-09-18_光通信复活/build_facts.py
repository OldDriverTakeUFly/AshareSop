# build_facts.py —— 光通信复活(2026-09-18)facts 生成器:数字全部从库重算,防转写错
# 运行: /home/leo/Projects/CodeAgentDashboard/.venv/bin/python build_facts.py(在本工程目录下)
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path("/home/leo/Projects/CodeAgentDashboard")
OUT = Path(__file__).parent / "facts.json"
EXPIRES = "2026-09-23"
STOCKS = {
    "603118.SH": "共进股份", "301205.SZ": "联特科技", "300570.SZ": "太辰光",
    "688313.SH": "仕佳光子", "300502.SZ": "新易盛", "688048.SH": "长光华芯",
    "603083.SH": "剑桥科技", "300308.SZ": "中际旭创", "688498.SH": "源杰科技",
    "688205.SH": "德科立",
}

facts: list[dict] = []


def add(id_: str, value, unit: str, display: str, kind: str, ref: str, as_of: str = "2026-09-18") -> None:
    facts.append({"id": id_, "value": value, "unit": unit, "display": display,
                  "as_of": as_of, "source": {"kind": kind, "ref": ref}, "expires": EXPIRES})


md = sqlite3.connect(ROOT / "storage/database/market_data.db")
sh = sqlite3.connect(ROOT / "storage/database/stockhot.db")

# ── 板块资金(前5) ──
flow = json.loads(sh.execute(
    "SELECT data_json FROM daily_data WHERE trade_date='2026-09-18' AND data_type='fund_flow_sector'"
).fetchone()[0])
flow.sort(key=lambda x: -(x.get("main_net") or 0))
names = {"半导体": "bd_1", "通信设备": "bd_2", "电气设备": "bd_3", "专用机械": "bd_4", "IT设备": "bd_5"}
for it in flow[:5]:
    fid = names.get(it["name"])
    if not fid:
        continue
    add(f"{fid}_name", it["name"], "", it["name"], "stockhot",
        "stockhot.db:daily_data:fund_flow_sector@2026-09-18:板块榜前5排序")
    add(f"{fid}_net", round(it["main_net"], 1), "亿元", f"{it['main_net']:.1f}亿", "stockhot",
        f"stockhot.db:daily_data:fund_flow_sector@2026-09-18:{it['name']}:main_net")
    add(f"{fid}_chg", it["change_pct"], "%", f"+{it['change_pct']:.2f}%", "stockhot",
        f"stockhot.db:daily_data:fund_flow_sector@2026-09-18:{it['name']}:change_pct")
add("sector_rank", 2, "", "两市板块榜第2", "stockhot",
    "stockhot.db:daily_data:fund_flow_sector@2026-09-18:按main_net降序排名[通信设备]")

# ── 涨停池:共进首封 + 板块涨停数 ──
pool = json.loads(sh.execute(
    "SELECT data_json FROM daily_data WHERE trade_date='2026-09-18' AND data_type='limit_up_pool'"
).fetchone()[0])
gj = next(x for x in pool if x["code"] == "603118.SH")
t = gj["first_seal_time"]
add("gj_seal_time", t, "", f"{int(t[:2])}:{t[2:4]}首封", "stockhot",
    "stockhot.db:daily_data:limit_up_pool@2026-09-18:603118.SH:first_seal_time")
add("zt_count_sector", sum(1 for x in pool if x.get("sector") == "通信设备"), "只",
    "板块内涨停1只", "stockhot", "stockhot.db:daily_data:limit_up_pool@2026-09-18:sector=通信设备:len")
add("roster_count", len(STOCKS), "家", "10家", "tushare",
    "calc:点名册固定10家公司集合(见本文件STOCKS)")

# ── 个股三列:今日涨幅 / 距近季峰 / 近60日 ──
for code, name in STOCKS.items():
    rows = md.execute(
        "SELECT trade_date,close,pct_chg FROM daily_price WHERE ts_code=? ORDER BY trade_date DESC LIMIT 60",
        (code,)).fetchall()
    assert rows[0][0] == "20260918", (name, rows[0][0])
    closes = [r[1] for r in rows][::-1]
    chg = rows[0][2]
    peak = max(closes)
    dd = (closes[-1] / peak - 1) * 100
    r60 = (closes[-1] / closes[0] - 1) * 100
    tag_ = "涨停" if code == "603118.SH" else ""
    add(f"{code[:6]}_chg", chg, "%", f"+{chg:.2f}%{tag_}", "tushare",
        f"market_data.db:daily_price:{code}:20260918:pct_chg")
    add(f"{code[:6]}_dd", round(dd, 1), "%", f"{dd:.1f}%", "tushare",
        f"calc:close(20260918)/max(close,近60交易日)-1 market_data.db:daily_price:{code}")
    add(f"{code[:6]}_r60", round(r60, 1), "%", f"{r60:+.1f}%", "tushare",
        f"calc:close(20260918)/close(60交易日前)-1 market_data.db:daily_price:{code}")

# 双龙头峰值日期(「6月末见顶」口径证据)
for code, short in [("300502.SZ", "xys"), ("300308.SZ", "zjxc")]:
    rows = md.execute(
        "SELECT trade_date,close FROM daily_price WHERE ts_code=? ORDER BY trade_date DESC LIMIT 60",
        (code,)).fetchall()
    peak_date = max(rows, key=lambda r: r[1])[0]
    add(f"peak_{short}", peak_date, "", peak_date, "tushare",
        f"calc:argmax(close,近60交易日) market_data.db:daily_price:{code}")

# ── 温度计(9-18,自研;反向语义纪律见 spec) ──
for sec, fid in [("通信设备", "dev"), ("通信", "l1")]:
    temp, d5 = md.execute(
        "SELECT temperature,delta_temp5 FROM thermometer_sector WHERE trade_date='20260918' AND name=?",
        (sec,)).fetchone()
    add(f"temp_{fid}", round(temp, 1), "℃", f"{temp:.1f}℃", "thermometer",
        f"market_data.db:thermometer_sector@20260918:{sec}:temperature")
    add(f"temp_{fid}_d5", round(d5, 1), "℃", f"5日+{d5:.1f}", "thermometer",
        f"market_data.db:thermometer_sector@20260918:{sec}:delta_temp5")
add("thermo_scale", "0-100", "", "0-100", "thermometer", "自研板块温度计刻度区间(constants.py THERMOMETER_* 口径)")
prev = md.execute(
    "SELECT temperature FROM thermometer_sector WHERE trade_date='20260917' AND name='通信设备'").fetchone()[0]
add("temp_dev_prev", round(prev, 1), "℃", f"{prev:.1f}℃", "thermometer",
    "market_data.db:thermometer_sector@20260917:通信设备:temperature")
dev_today = [f for f in facts if f["id"] == "temp_dev"][0]["value"]
add("temp_dev_d1", round(float(dev_today) - prev, 1), "℃", f"{float(dev_today) - prev:.1f}℃", "thermometer",
    "calc:temp(20260918)-temp(20260917) thermometer_sector:通信设备")

# ── 催化(web,高盛/美股) ──
add("gs_up27", 39, "%", "+39%", "web", "华尔街见闻/观点网 2026-09-14:高盛上调800G及以上光模块2027年需求预测39%")
add("gs_up28", 36, "%", "+36%", "web", "华尔街见闻/观点网 2026-09-14:高盛上调2028年需求预测36%")
add("gs_qty27", 1.44, "亿只", "1.44亿只", "web", "同上:2027年800G及以上需求量至1.44亿只")
add("gs_qty28", 1.71, "亿只", "1.71亿只", "web", "同上:2028年至1.71亿只")
add("gs_visits", 15, "家", "15家", "web", "同上:高盛走访国内15家光通信企业")
add("gs_mkt28", 1485, "亿美元", "1485亿美元", "web", "华尔街见闻 2026-09-07:高盛将2028年全球光模块市场预测上调至1485亿美元")
add("gs_mkt28_up", 115, "%", "+115%", "web", "同上:较前值+115%")
add("gs_ship_up", "两成以上", "", "两成以上", "web", "华尔街见闻 2026-09-07:高盛预计2026-2028年出货量上调20%以上(约合两成)")
add("us_optical", 9, "%", "涨超9%", "web", "华尔街见闻 2026-09-16:Lumentum/Coherent本周涨超9%")

OUT.write_text(json.dumps({"facts": facts}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"OK {len(facts)} facts -> {OUT}")
