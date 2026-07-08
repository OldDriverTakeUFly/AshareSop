---
name: after-hours-review
description: A 股盘后总结生成 skill。读取 daily-market-scan 已采集的 SQLite 数据（板块涨幅/涨停池/连板梯队/龙虎榜），分析当日热点方向，结合 web 搜索找出领涨催化原因，生成结构化的盘后总结 markdown 报告。Use whenever the user mentions 盘后总结, 今日复盘, 盘后复盘, 今日热点, 板块涨幅, after hours review, daily market recap, or wants a same-day market summary after close.
---

# A 股盘后总结

本 skill 是 `daily-market-scan` 的**下游消费者**——读取其已采集到 SQLite 的盘面数据，结合 web 搜索催化消息，生成结构化的盘后总结报告。不自己采集数据，不做买卖建议。

## 1. 前置条件

**必须先运行 `daily-market-scan` skill** 采集当日数据。本 skill 只读不写数据库。

若用户触发盘后总结但数据未采集，提示："请先运行 daily-market-scan skill 采集今日盘面数据（涨停/龙虎榜/资金流/风险提示），然后再做盘后总结。"

## 2. 执行流程（4 步）

### Step 1：读已采集数据（从 SQLite，不采集）

```python
from stockhot.storage.database import get_daily_data

date = "2026-06-25"  # 当日日期
data = get_daily_data(date)
```

`get_daily_data(date)` 返回 `{data_type: list[dict]}`，本 skill 需要读的 key：

| key | 字段 | 用途 |
|-----|------|------|
| `fund_flow_sector` | name, change_pct, main_net, main_pct | **板块涨幅排名 + 资金流** |
| `limit_up_pool` | name, code, sector, seal_amount, consecutive_boards | **涨停个股** |
| `limit_up_analysis` | 嵌套 sector_correlation（name, count, stocks）+ consecutive_boards（board_count, stocks） | **板块联动 + 连板梯队** |
| `dragon_tiger_detail` | 营业部/机构/游资 | **游资动向** |
| `fund_flow_market` | main_net, direction | **大盘资金流方向** |
| `broken_pool` | name, code | **炸板池** |
| `limit_down_pool` | name, code | **跌停池** |
| `index_technical` | indices{ts_code→{name,close,pct_chg,technical_score,technical_state,stage,stage_confidence,reasons,expected_action,ma5/10/20/60,support,resistance}}, summary | **大盘技术面（4 指数 6 阶段趋势识别）** |
| `volatility` | indices{ts_code→{name,rv20,rv20_pct,panic_level}}, market{ivix_current,ivix_pct,ivix_panic_level,vr_ratio,rv_sse_approx}, cross_signal{panic_confirmed,overheat_confirmed}, summary | **波动率温度（中国版 VIX：RV 分位 + iVIX + V/R 比率 + 技术面双确认）** |
| `sector_volatility` | sectors{name→{sector_rv20,sector_rv20_pct,panic_level,member_count}}, cross_section_ranking, summary | **板块情绪温度（31 申万一级成分股等权 RV + 各板块自身历史分位）**。⚠️ 计算量大，独立 CLI/cron 触发（`python -m stockhot.sector_volatility analyze`），不在 daily-market-scan Wave 内，可能未采集 |

**数据缺失处理**：若某个 key 不存在或为空，对应报告节标注"数据不可用"，不停止。若**全部数据为空**，停止并提示用户先跑 daily-market-scan。

### Step 2：分析热点方向

1. **板块涨幅排名**：从 `fund_flow_sector` 按 `change_pct` 降序排列 → Top 10
2. **板块涨停密度**：从 `limit_up_analysis.sector_correlation` 按 `count` 降序 → 涨停数最多的板块
3. **交叉比对**：将涨幅 Top 板块与涨停密度 Top 板块交叉——**同时出现在两个榜的板块**是当日核心热点。选出 2-3 个核心热点方向。
4. **领涨个股提取**：对每个热点方向，从 `limit_up_pool` 中按 sector 过滤出该板块的涨停个股，优先展示连板股（consecutive_boards > 1）和大封单股。

### Step 3：web 搜索催化原因

对每个热点方向及其 Top 2-3 领涨个股，用 WebSearch 搜索催化消息：

**搜索策略**（每个热点方向搜 2-3 次）：
- `"{板块名} 涨停 原因 {date}"` — 板块级催化
- `"{领涨个股名} 利好 消息 {date}"` — 个股级催化
- `"{热点主题} 政策 催化 2026"` — 政策/行业事件

**催化分类**（标注类型）：
- 🏛 政策催化（部委文件/规划/补贴）
- 📦 订单/业绩催化（大单/超预期财报）
- 🔬 技术突破催化（新产品/验证/量产）
- 🌐 行业事件催化（海外映射/峰会/展会）
- 💰 资金推动（游资接力/机构买入，从龙虎榜交叉验证）

**诚实原则**：
- 搜索不到明确催化的，标注"⚠️ 未找到明确催化，可能为资金推动/技术面反弹"
- **不编造催化消息**——每条催化必须附 web 来源 URL
- 龙虎榜数据可与催化交叉验证（如游资集中买入 + 政策催化 = 确认型热点）

### Step 4：生成总结报告

输出到 `docs/盘后总结/{YYYY-MM-DD}_盘后总结.md`，使用下方模板。

### Step 5：情绪温度计（日历效应校准）

在写"市场概览"的情绪判断时，**对照当前月份的历史基准**判断情绪冷暖。基准数据见 `references/calendar-effect-baseline.md`。

判定逻辑：
- 查当前月份的历史均值和胜率（如 6 月：均值 +0.72%、胜率 60%，偏热）
- 对比今日实际涨跌/涨停数
- 判定：**符合季节性** / **反季节异常**（后者需在报告中标注原因）

例如：7 月某日大跌 1.5%，但 7 月历史均值 +1.05%（偏热），则标注"⚠️ 反季节异常——7 月本应偏热却大跌，关注突发利空"。反之 1 月大跌（1 月历史 -2.56%，全年最差），则标注"符合季节性偏冷，不必过度恐慌"。

### Step 5b：波动率温度（中国版 VIX 校准）

在写"市场概览"时，**读取 `get_daily_data(date)['volatility']`**，输出波动率层面的恐慌温度。与方法论研报（`docs/方法论/A股波动率观察框架方法论深度研报.md`）的"五层观察体系"对应。

判定逻辑：
- 读 5 大指数的 RV20 历史分位（rv20_pct）与恐慌等级（panic_level）
- 找最恐慌（rv20_pct 最高）与最平静（rv20_pct 最低）指数
- 若 ≥3 个指数 rv20_pct ≥ 90 → "系统性恐慌"；若仅成长股（创业板/科创）高 → "结构性恐慌"
- 读 iVIX（market.ivix_current）与 V/R 比率（market.vr_ratio）：V/R > 1.3 → "期权极贵，市场过度恐慌"；V/R < 0.9 → "期权便宜，市场低估波动"
- 与情绪温度计（Step 5）交叉印证：日历效应偏冷 + 波动率 P90+ = 真恐慌；日历效应偏冷但波动率低 = 阴跌未恐慌

报告输出格式（插入市场概览的情绪温度计旁）：
```
**波动率温度**：最恐慌 {name} RV20={X}%（P{Y}，{level}），最平静 {name} P{Y}；
iVIX={X}（{level}），V/R={X}（{期权偏贵/合理/便宜}）
```

数据缺失时（`volatility` key 不存在或 status=数据不可用）标注"波动率数据不可用"，不停止。

### Step 6（新增）：采集宏观景气度背景

调用 `stockhot.macro` 模块拉取 Tushare 宏观数据（PMI/CPI/PPI/M2/Shibor/LPR），
生成宏观景气度评分（0-100）和 markdown section，插入报告的"市场概览"之前。

```python
from stockhot.macro import collect_macro_snapshot, format_macro_section
snap = collect_macro_snapshot()
macro_md = format_macro_section(snap)  # 直接插入报告
```

宏观 section 提供 PMI/货币/通胀/利率的基准背景，用于校准当日市场表现的
相对意义（如：宏观偏弱时逆势上涨更具含金量）。

## 3. 报告模板

```markdown
# {YYYY-MM-DD} 盘后总结

> **生成时间**：{YYYY-MM-DD HH:MM} | **数据来源**：daily-market-scan SQLite + web 搜索 + Tushare 宏观

{宏观景气度 section — 由 stockhot.macro.format_macro_snapshot() 生成}

## 一、市场概览

- 涨停 **{N}** 只，炸板 **{M}** 只，跌停 **{K}** 只
- 大盘主力**{净流入/净流出} {X}** 亿
- 市场情绪：{强/中/弱}（涨停>50 且炸板率<30% 为强；涨停<20 为弱）
- **情绪温度计**：{月份}月历史均值{X}%、胜率{Y}%（{偏热/中性/偏冷}），今日{符合季节性/反季节异常}
- **波动率温度**：最恐慌 {name} RV20={X}%（P{Y}，{panic_level}），最平静 {name} P{Y}；iVIX={X}（{level}），V/R={X}（{期权偏贵/合理/便宜}）。{系统性恐慌/结构性恐慌/无恐慌区}

### 大盘技术面（由 stockhot.index_technical 生成）

> 数据来源：`get_daily_data(date)['index_technical']`（由 daily-market-scan Wave 2 采集）

**指数技术面一览**（4 大指数 6 阶段趋势识别）：

| 指数 | 收盘 | 涨跌% | 技术评分 | 状态 | **阶段** | 置信度 | 盘前预期 |
|------|:---:|:---:|:---:|:---:|:---:|:---:|------|
| 上证指数 | {close} | {pct}% | {score} | {state} | **{stage}** | {conf}% | {expected_action} |
| 深证成指 | ... | | | | | | |
| 创业板指 | ... | | | | | | |
| 科创50 | ... | | | | | | |

**整体技术面定性**：{summary（如"上证高位震荡筑顶/创业板主跌浪/科创50上涨中回调，整体偏弱，盘前不建议重仓"）}

> **核心洞察**：技术面与情绪温度计的交叉印证——若情绪偏热但技术面"高位震荡筑顶/主跌浪"，警惕破位风险；若情绪偏冷但技术面"低位筑底/主升浪"，可能是左侧机会。技术面是情绪的"实证校验"。

## 二、板块涨幅排名

| 排名 | 板块 | 涨幅 | 主力净流入（亿） | 涨停数 |
|:---:|------|:---:|:---:|:---:|
| 1 | {板块} | {X}% | {Y} | {Z} |
| 2 | {板块} | ... | ... | ... |
| ... | （Top 10） | | | |

## 三、热点方向深度

### 热点 1：{方向名}（板块涨幅 {X}%，涨停 {N} 只）

**领涨个股**：

| 代码 | 名称 | 连板 | 封单（亿） | 催化 |
|------|------|:---:|:---:|------|
| {code} | {name} | {N}板 | {X} | {一句话} |

**催化原因**（web 搜索）：

- 🏛 {政策/订单/技术/事件催化描述}（来源：{url}，{日期}）
- 💰 龙虎榜验证：{游资/机构}买入 {X} 万（来源：SQLite dragon_tiger_detail）

### 热点 2：{方向名}
（同上格式）

### 热点 3：{方向名}
（同上格式）

## 四、连板梯队

| 连板数 | 个股 |
|:---:|------|
| {N}板 | {name1}、{name2} |
| {N-1}板 | {name3}、{name4} |
| 2板 | {name5}、... |

## 五、游资动向

（龙虎榜亮点：最大买入营业部、机构席位、游资接力模式）

## 六、总结结论

**今日市场特征**：
{2-3 句总结：涨跌结构、资金方向、热点轮动特征}

**明日关注方向**：
1. {方向 1}（{理由}）
2. {方向 2}（{理由}）
3. {方向 3}（{理由}）

---

> **免责声明**：盘后总结仅为市场数据梳理与公开信息汇总，不构成任何投资建议。催化原因来自 web 搜索，可能不完整或有时效性偏差。市场有风险，投资需谨慎。
```

## 4. 关键规则

- **只读 SQLite**——不调 AKShare，不采集数据，不改数据库
- **催化必须附来源 URL**——无来源的催化不可接受，搜不到就标注"未找到明确催化"
- **催化必须标注真实发布日期**——引用格式中的 `{日期}` 是 WebSearch 结果的**实际发布日期**，不是报告日；拿不到确切日期时按最保守（偏旧）估计，并标注「日期待核」
- **催化有时效性**——≥4 天前的旧闻**不作当日催化依据**（催化须能解释当日上涨）；若确有证据表明旧事件仍在发酵（如近期连续政策落地、订单持续兑现），须附**最近走势/讨论证据**并标注「旧事件持续发酵」方可引用
- **不编造**——信息缺乏处标注，不猜测
- **不做买卖建议**——只呈现数据和分析
- **热点方向 2-3 个**——不要罗列所有板块，聚焦真正的核心热点（涨幅+涨停密度交叉确认）
- **连板梯队是情绪温度计**——高连板（≥4 板）个股是市场情绪的风向标，必须在报告中突出

## 5. 与其他 skill 的关系

| Skill | 关系 |
|-------|------|
| `daily-market-scan` | **上游**——本 skill 消费其采集的 SQLite 数据 |
| `invest-sop-pre-market` | 不重叠——盘前报告读 invest_* 表，本 skill 读 daily_data 表 |
| `research-report` | 不重叠——研报是深度分析，盘后总结是当日速览 |

## 6. 输出位置

- 报告目录：`docs/盘后总结/`
- 文件名：`{YYYY-MM-DD}_盘后总结.md`
- 不更新 `docs/README.md` 索引（盘后总结是每日产物，非研报）

## Source of Truth

如果本 skill 与实际数据结构冲突，以 `stockhot/storage/database.py` 的 `get_daily_data` 返回格式为准。
