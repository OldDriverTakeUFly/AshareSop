# 板块热点复盘·光通信复活(2026-09-18)试验长图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 出品首个「板块热点复盘」系列长图卡(深蓝黑+琥珀金新肤),试验品为 2026-09-18 光通信板块集体反弹,四件套工程完整落地。

**Architecture:** 手工 HTML(剪刀差骨架)+ facts.json 数字溯源(脚本从库重算生成,防转写错)+ 现有 render/check 工具链注册渲染 + 敏感词/诱导句式零命中闸 + 文案笔记。不改任何管线代码,只在两个工具脚本的 PROJECTS 列表各加一行。

**Tech Stack:** 纯 HTML/CSS(750px)、playwright(现有脚本)、sqlite(market_data.db/stockhot.db)、cardgen 敏感词表(scripts/card_factory/sensitive_words.txt + cardgen/compliance.py INDUCEMENT_PATTERNS)。

## Global Constraints

- 工程目录:`docs/小红书卡片/未发布/板块热点复盘/2026-09-18_光通信复活/`,四件套=长图.html/长图.png/文案.md/facts.json
- 配色铁律:底 `#0a0f1e` 深蓝黑 / 卡 `#131a30` / 边 `#26335c` / 主强调琥珀金 `#f5b942` / 次强调冰蓝 `#6db9ff`;行情涨跌=红涨 `#ff6b6b` 绿跌 `#4ade80`;定性正负=青绿 `#6ee7b7` 粉红 `#fda4af`
- 敏感词全表零命中(含:`买入 卖出 增持 减持 抄底 逃顶 满仓 加仓 建仓 止盈 止损 目标价 翻倍 十倍 稳赚 必涨 暴涨 躺赢 财富密码 内幕 消息面利好 主力吸筹 庄家 拉盘 仓位 追高 上车 下车 抄作业 梭哈 布局 埋伏 闭眼 无脑 最看好 值得买 性价比 标的 开户 带单 跟着买 赔率 下注 押注`——个股用「公司/成员」,安排用「规划」等替代);诱导句式正则零命中(`买(的)?(是|什么)`、`赌X`、`下一个(动作|步骤)`、`你(应该|可以|要|不妨)`);foot 必含「不构成投资建议」
- 温度计反向语义纪律:温度只解读为关注度/拥挤度,不得写成操作依据;严禁「人声鼎沸=机会」表述
- 全部数字登记 facts.json 且 expires ≤ 2026-09-23;发稿文案标「数据截至9-18」;tags 为文案最后一行
- 渲染:750px viewport × 2x,超 9000px 才拆上下(预计 ~6000px,整张)
- 不改:cardgen/daily.py/daily_market_cards/card_factory/content_publisher/日更两卡

---

### Task 1: 建工程目录 + build_facts.py 生成 facts.json

**Files:**
- Create: `docs/小红书卡片/未发布/板块热点复盘/2026-09-18_光通信复活/facts.json`
- Create: `docs/小红书卡片/未发布/板块热点复盘/2026-09-18_光通信复活/build_facts.py`(工具,生成后保留供复核)

**Interfaces:**
- Produces: `facts.json` 的 fact 结构 `{"id","value","unit","display","as_of","source":{"kind","ref"},"expires"}`(与长文图卡 facts 同构);后续 Task 2 的 HTML 数字必须与 display 字段逐字一致

- [ ] **Step 1: 写 build_facts.py(从两个库重算,禁止手抄数字)**

```python
# build_facts.py —— 光通信复活(2026-09-18)facts 生成器:数字全部从库重算,防转写错
# 运行: /home/leo/Projects/CodeAgentDashboard/.venv/bin/python build_facts.py(在本工程目录下)
from __future__ import annotations
import json, sqlite3
from pathlib import Path

ROOT = Path("/home/leo/Projects/CodeAgentDashboard")
OUT = Path(__file__).parent / "facts.json"
EXPIRES = "2026-09-23"
STOCKS = {  # 点名册10家(按今日涨幅降序在Step2排)
    "603118.SH": "共进股份", "301205.SZ": "联特科技", "300570.SZ": "太辰光",
    "688313.SH": "仕佳光子", "300502.SZ": "新易盛", "688048.SH": "长光华芯",
    "603083.SH": "剑桥科技", "300308.SZ": "中际旭创", "688498.SH": "源杰科技",
    "688205.SH": "德科立",
}
facts = []
def add(id_, value, unit, display, kind, ref, as_of="2026-09-18"):
    facts.append({"id": id_, "value": value, "unit": unit, "display": display,
                  "as_of": as_of, "source": {"kind": kind, "ref": ref}, "expires": EXPIRES})

md = sqlite3.connect(ROOT / "storage/database/market_data.db")
sh = sqlite3.connect(ROOT / "storage/database/stockhot.db")

# ── 板块资金(前5) ──
flow = json.loads(sh.execute("SELECT data_json FROM daily_data WHERE trade_date='2026-09-18' AND data_type='fund_flow_sector'").fetchone()[0])
flow.sort(key=lambda x: -(x.get("main_net") or 0))
names = {"半导体": "bd_1", "通信设备": "bd_2", "电气设备": "bd_3", "专用机械": "bd_4", "IT设备": "bd_5"}
for it in flow[:5]:
    fid = names.get(it["name"])
    if fid:
        add(f"{fid}_name", it["name"], "", it["name"], "stockhot", "stockhot.db:daily_data:fund_flow_sector@2026-09-18:板块榜前5排序")
        add(f"{fid}_net", round(it["main_net"], 1), "亿元", f"{it['main_net']:.1f}亿", "stockhot", f"stockhot.db:daily_data:fund_flow_sector@2026-09-18:{it['name']}:main_net")
        add(f"{fid}_chg", it["change_pct"], "%", f"+{it['change_pct']:.2f}%", "stockhot", f"stockhot.db:daily_data:fund_flow_sector@2026-09-18:{it['name']}:change_pct")
add("sector_rank", 2, "", "两市板块榜第2", "stockhot", "stockhot.db:daily_data:fund_flow_sector@2026-09-18:按main_net降序排名[通信设备]")

# ── 涨停池:共进首封 ──
pool = json.loads(sh.execute("SELECT data_json FROM daily_data WHERE trade_date='2026-09-18' AND data_type='limit_up_pool'").fetchone()[0])
gj = next(x for x in pool if x["code"] == "603118.SH")
t = gj["first_seal_time"]
add("gj_seal_time", t, "", f"{int(t[:2])}:{t[2:4]}首封", "stockhot", "stockhot.db:daily_data:limit_up_pool@2026-09-18:603118.SH:first_seal_time")
add("zt_count_sector", sum(1 for x in pool if x.get("sector") == "通信设备"), "只", "板块内涨停1只", "stockhot", "stockhot.db:daily_data:limit_up_pool@2026-09-18:sector=通信设备:len")

# ── 个股三列:今日涨幅 / 距近季峰 / 近60日 ──
for code, name in STOCKS.items():
    rows = md.execute("SELECT trade_date,close,pct_chg FROM daily_price WHERE ts_code=? ORDER BY trade_date DESC LIMIT 60", (code,)).fetchall()
    assert rows[0][0] == "20260918", (name, rows[0][0])
    closes = [r[1] for r in rows][::-1]
    chg = rows[0][2]; peak = max(closes); dd = (closes[-1] / peak - 1) * 100; r60 = (closes[-1] / closes[0] - 1) * 100
    tag_ = "涨停" if code == "603118.SH" else ""
    add(f"{code[:6]}_chg", chg, "%", f"+{chg:.2f}%{tag_}", "tushare", f"market_data.db:daily_price:{code}:20260918:pct_chg")
    add(f"{code[:6]}_dd", round(dd, 1), "%", f"{dd:.1f}%", "tushare", f"calc:close(20260918)/max(close,近60交易日)-1 market_data.db:daily_price:{code}")
    add(f"{code[:6]}_r60", round(r60, 1), "%", f"{r60:+.1f}%", "tushare", f"calc:close(20260918)/close(60交易日前)-1 market_data.db:daily_price:{code}")

# 峰值日期(双龙头6-30见顶的口径证据)
for code, name in [("300502.SZ", "xys"), ("300308.SZ", "zjxc")]:
    rows = md.execute("SELECT trade_date,close FROM daily_price WHERE ts_code=? ORDER BY trade_date DESC LIMIT 60", (code,)).fetchall()
    peak_date = max(rows, key=lambda r: r[1])[0]
    add(f"peak_{name}", peak_date, "", peak_date, "tushare", f"calc:argmax(close,近60交易日) market_data.db:daily_price:{code}")

# ── 温度计(9-18,自研) ──
for sec, fid in [("通信设备", "dev"), ("通信", "l1")]:
    r = md.execute("SELECT temperature,delta_temp5 FROM thermometer_sector WHERE trade_date='20260918' AND name=?", (sec,)).fetchone()
    add(f"temp_{fid}", round(r[0], 1), "℃", f"{r[0]:.1f}℃", "thermometer", f"market_data.db:thermometer_sector@20260918:{sec}:temperature")
    add(f"temp_{fid}_d5", round(r[1], 1), "℃", f"5日+{r[1]:.1f}", "thermometer", f"market_data.db:thermometer_sector@20260918:{sec}:delta_temp5")
prev = md.execute("SELECT temperature FROM thermometer_sector WHERE trade_date='20260917' AND name='通信设备'").fetchone()[0]
add("temp_dev_prev", round(prev, 1), "℃", f"{prev:.1f}℃", "thermometer", "market_data.db:thermometer_sector@20260917:通信设备:temperature")
d1 = json.loads([f for f in facts if f["id"] == "temp_dev"][0]["value"]) if False else None
dev_today = [f for f in facts if f["id"] == "temp_dev"][0]["value"]
add("temp_dev_d1", round(float(dev_today) - prev, 1), "℃", f"单日+{float(dev_today)-prev:.1f}", "thermometer", "calc:temp(20260918)-temp(20260917) thermometer_sector:通信设备")

# ── 催化(web,高盛/美股) ──
add("gs_up27", 39, "%", "+39%", "web", "华尔街见闻/观点网 2026-09-14:高盛上调800G及以上光模块2027年需求预测39%")
add("gs_up28", 36, "%", "+36%", "web", "华尔街见闻/观点网 2026-09-14:高盛上调2028年需求预测36%")
add("gs_qty27", 1.44, "亿只", "1.44亿只", "web", "同上:2027年800G及以上需求量至1.44亿只")
add("gs_qty28", 1.71, "亿只", "1.71亿只", "web", "同上:2028年至1.71亿只")
add("gs_visits", 15, "家", "15家", "web", "同上:高盛走访国内15家光通信企业")
add("gs_mkt28", 1485, "亿美元", "1485亿美元", "web", "华尔街见闻 2026-09-07:高盛将2028年全球光模块市场预测上调至1485亿美元")
add("gs_mkt28_up", 115, "%", "+115%", "web", "同上:较前值+115%")
add("us_optical", 9, "%", "涨超9%", "web", "华尔街见闻 2026-09-16:Lumentum/Coherent本周涨超9%")

OUT.write_text(json.dumps({"facts": facts}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"OK {len(facts)} facts -> {OUT}")
```

- [ ] **Step 2: 运行并抽查**

Run: `cd docs/小红书卡片/未发布/板块热点复盘/2026-09-18_光通信复活 && /home/leo/Projects/CodeAgentDashboard/.venv/bin/python build_facts.py`
Expected: `OK 5x facts`;抽查 6 张关键 display:`+2.62%` / `60.5亿` / `+43.x%?否——新易盛dd≈-43.4` / `源杰dd≈-2.7` / `46.0℃` / `单日+12.9`

- [ ] **Step 3: Commit**

```bash
git add docs/小红书卡片/未发布/板块热点复盘/
git commit -m "feat(card): 板块热点复盘首个工程facts——光通信复活2026-09-18(脚本重算50+数字)"
```

---

### Task 2: 撰写 长图.html(深蓝黑+琥珀金,剪刀差骨架)

**Files:**
- Create: `docs/小红书卡片/未发布/板块热点复盘/2026-09-18_光通信复活/长图.html`

**Interfaces:**
- Consumes: Task 1 facts.json 的 display 字符串(逐字使用)
- Produces: 可被 render_longpics.py 按 `BASE/板块热点复盘/2026-09-18_光通信复活/长图.html` 寻址的文件

- [ ] **Step 1: 写入完整 HTML**

```html
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<style>
  * { margin:0; padding:0; box-sizing:border-box; font-family:"PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif; }
  body { width:750px; background:#0a0f1e; color:#eef2ff; padding:36px 30px; }
  .tag { display:inline-block; background:#3a2e12; color:#f5c96a; border-radius:6px; padding:4px 10px; font-size:20px; }
  h1 { font-size:44px; margin:16px 0 6px; line-height:1.28; background:linear-gradient(90deg,#f5b942,#6db9ff); -webkit-background-clip:text; color:transparent; font-weight:800; }
  .sub { color:#8a96c4; font-size:22px; margin-bottom:24px; line-height:1.55; }
  .hook { border:2px solid #f5b942; background:linear-gradient(135deg,#1a1830,#101626); border-radius:16px; padding:22px; margin-bottom:20px; }
  .hook .h { font-size:25px; color:#f5b942; margin-bottom:12px; }
  .hook p { font-size:20px; line-height:1.75; color:#dbe4ff; }
  .stats { display:flex; gap:14px; margin-top:16px; }
  .stat { flex:1; background:#131a30; border:1px solid #26335c; border-radius:12px; padding:14px 8px; text-align:center; }
  .stat .v { font-size:31px; font-weight:800; color:#f5b942; }
  .stat .v.ice { color:#6db9ff; }
  .stat .k { font-size:16px; color:#8a96c4; margin-top:4px; }
  .card { background:#131a30; border:1px solid #26335c; border-radius:16px; padding:22px; margin-bottom:20px; }
  .card h2 { font-size:26px; color:#f5b942; margin-bottom:6px; }
  .card .st { color:#8a96c4; font-size:18px; margin-bottom:12px; }
  table { width:100%; border-collapse:collapse; font-size:18px; }
  th { color:#6db9ff; text-align:left; padding:8px 6px; border-bottom:1px solid #26335c; }
  td { padding:8px 6px; border-bottom:1px solid #1c2748; color:#dbe4ff; line-height:1.5; }
  td.up { color:#ff6b6b; } td.down { color:#4ade80; }
  .pos { color:#6ee7b7; } .neg { color:#fda4af; }
  .note { font-size:16px; color:#7a86b5; margin-top:10px; line-height:1.65; }
  .insight { border-left:4px solid #f5b942; background:#1a1f3d; padding:14px 16px; border-radius:0 10px 10px 0; font-size:20px; line-height:1.75; margin-bottom:20px; color:#e6ebff; }
  .rows li { font-size:19px; line-height:1.8; color:#dbe4ff; margin-left:20px; margin-bottom:8px; }
  .rows b { color:#f5b942; }
  .tier { border-radius:10px; padding:12px 16px; font-size:19px; line-height:1.55; margin-bottom:10px; border:1px solid; }
  .t1 { background:#3a2410; border-color:#8a5a1a; width:100%; }
  .t2 { background:#182742; border-color:#2a5a8a; width:84%; }
  .t3 { background:#1a1f3d; border-color:#3a4470; width:68%; }
  .tier b { font-size:20px; color:#f5b942; }
  .vs { display:flex; gap:14px; }
  .vs .side { flex:1; background:#101626; border-radius:12px; padding:16px; }
  .vs .side h3 { font-size:21px; margin-bottom:8px; }
  .vs .a h3 { color:#6ee7b7; } .vs .b h3 { color:#fda4af; }
  .vs p { font-size:17px; line-height:1.7; color:#c6d2f2; }
  .foot { font-size:16px; color:#6b78a5; line-height:1.7; margin-top:8px; padding:16px; background:#0d1428; border-radius:12px; }
</style>
</head>
<body>
  <span class="tag">板块热点复盘 · 2026-09-18</span>
  <h1>最深跌掉四成的光模块<br>今晚一起活了</h1>
  <div class="sub">6月末板块见顶、龙头最深回撤超四成之后,光通信今日集体回弹——钱也回来了:通信设备主力净流入60.5亿,居两市板块榜第二。</div>

  <div class="hook">
    <div class="h">今晚发生了什么(9-18收盘)</div>
    <div class="stats">
      <div class="stat"><div class="v">+2.62%</div><div class="k">通信设备板块涨幅</div></div>
      <div class="stat"><div class="v ice">60.5亿</div><div class="k">主力净流入·两市第二</div></div>
      <div class="stat"><div class="v">涨停</div><div class="k">共进股份 +10.03%</div></div>
    </div>
    <p style="margin-top:14px">点名册里的10家光通信公司今晚全部收红,共进股份11:13封板。导火索是高盛本周把800G及以上光模块2027年需求预测上调了39%——燃料放在第三节,先把横截面摆出来。</p>
  </div>

  <div class="card">
    <h2>一、今晚的点名册</h2>
    <div class="st">10家代表性公司,按今日涨幅降序</div>
    <table>
      <tr><th>公司</th><th>今日涨幅</th><th>距近季峰</th><th>近60日</th></tr>
      <tr><td>共进股份</td><td class="up">+10.03% 涨停</td><td class="down">-4.6%</td><td class="up">+38.4%</td></tr>
      <tr><td>联特科技</td><td class="up">+7.74%</td><td class="down">-10.4%</td><td class="up">+2.5%</td></tr>
      <tr><td>太辰光</td><td class="up">+6.19%</td><td class="down">-18.4%</td><td class="down">-2.0%</td></tr>
      <tr><td>仕佳光子</td><td class="up">+6.09%</td><td class="down">-16.2%</td><td class="down">-5.2%</td></tr>
      <tr><td>新易盛</td><td class="up">+4.87%</td><td class="down">-43.4%</td><td class="down">-21.4%</td></tr>
      <tr><td>长光华芯</td><td class="up">+4.24%</td><td class="down">-31.9%</td><td class="down">-13.6%</td></tr>
      <tr><td>剑桥科技</td><td class="up">+4.09%</td><td class="down">-13.1%</td><td class="down">-7.3%</td></tr>
      <tr><td>中际旭创</td><td class="up">+3.40%</td><td class="down">-33.0%</td><td class="down">-26.1%</td></tr>
      <tr><td>源杰科技</td><td class="up">+2.82%</td><td class="down">-2.7%</td><td class="up">+3.3%</td></tr>
      <tr><td>德科立</td><td class="up">+2.69%</td><td class="down">-25.8%</td><td class="up">+4.4%</td></tr>
    </table>
    <div class="note">口径:Tushare日线收盘;「距近季峰」=现价距近60个交易日最高收盘价的落差;「近60日」=60个交易日区间涨跌幅。红=涨,绿=跌。</div>
  </div>

  <div class="insight">复活是分层的:源杰科技距峰值只剩-2.7%,基本收复失地;共进股份也只差-4.6%。但双龙头还在深坑里——新易盛-43.4%、中际旭创-33.0%。同一个晚上,有人叫回归,有人只能叫反弹。</div>

  <div class="card">
    <h2>二、钱和温度</h2>
    <div class="st">两市板块主力净流入榜(9-18,前五)</div>
    <table>
      <tr><th>板块</th><th>涨幅</th><th>主力净流入</th></tr>
      <tr><td>半导体</td><td class="up">+2.95%</td><td>+179.5亿</td></tr>
      <tr><td>通信设备</td><td class="up">+2.62%</td><td>+60.5亿</td></tr>
      <tr><td>电气设备</td><td class="up">+1.66%</td><td>+30.9亿</td></tr>
      <tr><td>专用机械</td><td class="up">+2.13%</td><td>+28.1亿</td></tr>
      <tr><td>IT设备</td><td class="up">+2.13%</td><td>+18.8亿</td></tr>
    </table>
    <div class="stats" style="margin-top:16px">
      <div class="stat"><div class="v ice">46.0℃</div><div class="k">通信设备温度(昨33.1℃)</div></div>
      <div class="stat"><div class="v">+12.9</div><div class="k">单日升温</div></div>
      <div class="stat"><div class="v">+19.4</div><div class="k">五日升温</div></div>
    </div>
    <div class="note">温度=板块拥挤度(自研温度计,0-100):低温=无人问津,高温=人声鼎沸。通信设备46℃仍在中低温区,大类「通信」54.8℃、五日+29.0。单日升温12.9℃只说明注意力在快速回流,不构成任何操作依据;按纪律,真到人声鼎沸的高温区,反而要小心预期打得太满。</div>
  </div>

  <div class="card">
    <h2>三、燃料是什么</h2>
    <div class="stats" style="margin-bottom:16px">
      <div class="stat"><div class="v">+39%</div><div class="k">高盛上修2027年800G+需求</div></div>
      <div class="stat"><div class="v ice">1.44亿只</div><div class="k">上修后2027年需求量</div></div>
      <div class="stat"><div class="v">+115%</div><div class="k">2028年市场空间上修幅度</div></div>
    </div>
    <ul class="rows">
      <li>高盛9月14日报告:走访国内15家光通信企业后,把800G及以上光模块2027/2028年需求预测上修39%/36%,至1.44亿只/1.71亿只——行业约束已从「需求够不够」转向「产能够不够」,供应链紧张或成2027年最大瓶颈</li>
      <li>更早的9月7日,高盛已把2028年全球光模块市场空间上调到1485亿美元(较前值+115%),并预计2026-2028年出货量再上调两成以上</li>
      <li>美股先行:本周Lumentum、Coherent涨超9%;光博会上厂商开始「锁订单、锁物料」——供给紧张从研报语言变成产业动作</li>
    </ul>
  </div>

  <div class="card">
    <h2>四、成色检验:修复还是逆转</h2>
    <div class="vs">
      <div class="side a">
        <h3>支持延续的证据</h3>
        <p>燃料是产业逻辑而非情绪:需求上修+供给约束双确认;主力净流入60.5亿、两市第二,资金是真的回来了;温度46℃仍在中低温区,拥挤度没到极端。</p>
      </div>
      <div class="side b">
        <h3>值得警惕的信号</h3>
        <p>修复严重分化——源杰已收复,新易盛/中际旭创距峰还有四成/三成;单日升温12.9℃偏快,注意力来得快去得也快;若只是深跌后的修复,上方套牢盘会压制修复高度。</p>
      </div>
    </div>
    <div class="note">本节是判别清单,不是结论。数据会说话,纪律不猜方向。</div>
  </div>

  <div class="foot">信源:Tushare日线与板块资金流(截至2026-09-18收盘)、自研板块温度计(2026-09-18);催化部分:华尔街见闻/观点网(高盛报告,2026-09-14)、华尔街见闻(2026-09-07/09-16)。分析为原创框架,仅为研究分享,不构成投资建议,市场有风险,决策需独立。</div>
</body>
</html>
```

- [ ] **Step 2: 数字一致性核对**

Run: `cd docs/小红书卡片/未发布/板块热点复盘/2026-09-18_光通信复活 && /home/leo/Projects/CodeAgentDashboard/.venv/bin/python - <<'EOF'
import json,re
html=open('长图.html',encoding='utf-8').read()
text=re.sub(r'<[^>]+>','',html)
facts={f['display'] for f in json.load(open('facts.json',encoding='utf-8'))['facts']}
# 图上出现的数字串(百分比/亿/℃/亿只等)必须能对上 facts display(允许拼接前缀+/-)
import sys
nums=set(re.findall(r'[+-]?\d+(?:\.\d+)?(?:%|亿[只元]?|℃)',text))
missing=[n for n in nums if n.lstrip('+') not in ' '.join(facts)]
print('图上数字串:',len(nums),'未锚定:',missing or '无')
EOF`
Expected: `未锚定: 无`(`10家/15家`等计数词单独处理,在 facts 有对应 id)

- [ ] **Step 3: Commit**

```bash
git add docs/小红书卡片/未发布/板块热点复盘/2026-09-18_光通信复活/长图.html
git commit -m "feat(card): 光通信复活长图html——深蓝黑琥珀金板块热点复盘新肤首作"
```

---

### Task 3: 工具注册(PROJECTS 各加一行)

**Files:**
- Modify: `docs/小红书卡片/未发布/render_longpics.py:11`
- Modify: `docs/小红书卡片/未发布/check_overflow.py:11`

- [ ] **Step 1: 两处 PROJECTS 列表末尾追加**

```python
PROJECTS = ["长文图卡_镍出海链", "长文图卡_碳价CBAM", "长文图卡_电解家族", "长文图卡_AI基建涨价", "长文图卡_全球龙头对照", "长文图卡_国产替代梯度", "板块热点复盘/2026-09-18_光通信复活"]
```
(两个文件同一行同样改法)

- [ ] **Step 2: Commit**

```bash
git add docs/小红书卡片/未发布/render_longpics.py docs/小红书卡片/未发布/check_overflow.py
git commit -m "feat(card): 长图工具链注册板块热点复盘工程"
```

---

### Task 4: 渲染 + 溢出检测

- [ ] **Step 1: 渲染**

Run: `cd /home/leo/Projects/CodeAgentDashboard/docs/小红书卡片/未发布 && /home/leo/Projects/CodeAgentDashboard/.venv/bin/python check_overflow.py 2>&1 | grep 光通信`
Expected: `板块热点复盘/2026-09-18_光通信复活: PASS | 高~5000-6500px 表格2张`

- [ ] **Step 2: 截图**

Run: `/home/leo/Projects/CodeAgentDashboard/.venv/bin/python render_longpics.py 2>&1 | grep 光通信`
Expected: `板块热点复盘/2026-09-18_光通信复活: NNNNpx -> 整张 长图.png`(若>9000px 拆上下,属预期外,回到 Task 2 精简)
注意:render 会重渲全部 PROJECTS 内旧工程,耗时正常;若只想渲新工程,临时把 PROJECTS 缩为单项后跑(跑完还原)。

- [ ] **Step 3: 溢出修复循环(如 FAIL)**

定位输出中 `overflow h/w` 的元素 → 对应收紧字号/行高/单元格 padding 或缩短文案 → 重跑 Step 1 至 PASS。

- [ ] **Step 4: Commit(含 PNG)**

```bash
git add docs/小红书卡片/未发布/板块热点复盘/2026-09-18_光通信复活/长图.png
git commit -m "feat(card): 光通信复活长图渲染成片"
```

---

### Task 5: 敏感词 + 诱导句式 + 免责 闸

- [ ] **Step 1: 扫描 html+文案(零命中)**

Run: `cd docs/小红书卡片/未发布/板块热点复盘/2026-09-18_光通信复活 && /home/leo/Projects/CodeAgentDashboard/.venv/bin/python - <<'EOF'
import re,sys
sys.path.insert(0,'/home/leo/Projects/CodeAgentDashboard/davis_analyzer')
from davis_analyzer.cardgen.compliance import load_words, INDUCEMENT_PATTERNS
words=load_words()
for fn in ['长图.html','文案.md']:
    t=re.sub(r'<[^>]+>','',open(fn,encoding='utf-8').read())
    hits=[w for w in words if w in t]
    induce=[lbl for pat,lbl in INDUCEMENT_PATTERNS if pat.search(t)]
    ok_disclaimer='不构成投资建议' in t
    print(fn,'敏感词:',hits or '零命中','| 诱导:',induce or '零命中','| 免责:',ok_disclaimer)
EOF`
Expected: 两文件均 `敏感词: 零命中 | 诱导: 零命中 | 免责: True`(文案.md 在 Task 6 后复跑一次)

- [ ] **Step 2: 命中处理**

只改措辞不豁免:`标的→个股`,`布局→规划/落子`,`追高→追热度`,`增持→提高配置比例` 等。

---

### Task 6: 文案.md

**Files:**
- Create: `docs/小红书卡片/未发布/板块热点复盘/2026-09-18_光通信复活/文案.md`

- [ ] **Step 1: 写入(标题钩子+正文+tags末行+图片清单+锚点)**

内容骨架(正文数字与 facts 一致,tags 最后一行):

```
# 光通信复活长图卡(图文笔记版)

> 用途:「板块热点复盘」系列首作(2026-09-18 光通信集体回弹),长图卡载体。
> 编辑注:数字全部锚定 facts.json(Tushare/stockhot/自研温度计指纹 + 高盛报告公开报道);敏感词全表+诱导句式零命中(脚本复核);时效闸:数据截至9-18,窗口至9-23。

## 一、来源锚点
- 行情/资金:stockhot.db daily_data@2026-09-18(fund_flow_sector/limit_up_pool);Tushare daily_price(近60交易日窗口)
- 温度:market_data.db thermometer_sector@20260918(反向语义纪律见 spec)
- 催化:华尔街见闻/观点网 2026-09-14(高盛上修39%/36%)、2026-09-07(1485亿美元/+115%)、2026-09-16(Lumentum/Coherent)

## 二、终版发稿文案(v1)

**标题**:最深跌掉四成的光模块,今晚一起活了

**tags**:#光通信 #CPO #盘后复盘 #A股

**正文**:

6月末见顶之后,光通信双龙头最深回撤超过四成。今晚,这个板块集体回弹了。

通信设备+2.62%,主力净流入60.5亿、居两市板块榜第二;共进股份涨停,联特科技+7.74%,太辰光、仕佳光子都涨超6%——点名册里的10家公司全部收红。

但最有意思的不是涨幅,是分化的处境:光芯片的源杰科技距前高只剩-2.7%,基本收复失地;而新易盛还趴在-43.4%的深坑里。同一个晚上,有人叫回归,有人只能叫反弹。

燃料是高盛:本周他把800G及以上光模块2027年需求预测上调39%(走访了国内15家企业),行业约束从「需求够不够」变成「产能够不够」;更早一周,2028年市场空间已被上调到1485亿美元、较前值+115%。美股的Lumentum、Coherent本周涨超9%打头阵。

自研温度计显示通信设备单日升温12.9℃——注意力回流很快,但按纪律,这只描述拥挤度变化,不构成任何操作依据。

完整的点名册、资金榜、温度与成色检验做成了长图👇

信源:Tushare/板块资金流(截至2026-09-18收盘)、自研板块温度计;催化:华尔街见闻/观点网(高盛报告)。仅为研究分享,不构成投资建议,市场有风险,决策需独立。

## 三、图片清单

| 图 | 文件 | 尺寸 | 校验 |
|----|------|------|------|
| 图1(长图) | 长图.png | 1500×NNNN | 溢出检测PASS/敏感词零命中 |
```

- [ ] **Step 2: 复跑 Task 5 Step 1 扫描(含文案.md)** → 零命中
- [ ] **Step 3: Commit**

```bash
git add docs/小红书卡片/未发布/板块热点复盘/2026-09-18_光通信复活/文案.md
git commit -m "feat(card): 光通信复活发稿文案+锚点"
```

---

### Task 7: 目检 + 交付

- [ ] **Step 1: vision.py 目检(视觉任务规范)**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python scripts/content_publisher/vision.py "docs/小红书卡片/未发布/板块热点复盘/2026-09-18_光通信复活/长图.png" --prompt "金融长图卡目检:1)排版是否整齐无文字溢出/截断 2)深蓝黑+琥珀金配色观感 3)表格红涨绿跌是否正确 4)钩子首屏是否抓人 5)footer信源免责是否完整。返回JSON {pass, issues[]}"`(以 vision.py 实际 CLI 签名为准,先 --help)
Expected: pass=true;issues 逐条修复后重渲。

- [ ] **Step 2: 向用户交付**

汇报:工程路径、长图 PNG 链接、facts 数量、四道闸结果(溢出/数字锚定/敏感词/免责)、发布提醒(发布永远人工,push_longpics 可选)。

---

## Self-Review 记录

1. **Spec 覆盖**:spec §六 四节结构=html 四卡+insight ✓;§五 合规=Task5 ✓;§四 facts=Task1 ✓;§七 DoD=Task4/5/7 ✓;§三 工具注册=Task3 ✓。
2. **占位符**:无 TBD;NNNN(px)为渲染产出占位,非设计占位。
3. **类型/命名一致**:`板块热点复盘/2026-09-18_光通信复活` 全文一致;facts display 与 html 数字由 Task2 Step2 脚本核验。
4. 已知风险:render/check 会重跑全部旧工程(耗时);共进股份 +10.03% 涨停与 display `+10.03%涨停` 的拼接需 facts 核对脚本放行(脚本 Step2 用 `in` 包含匹配,已兼容)。
