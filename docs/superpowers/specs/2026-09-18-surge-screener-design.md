# Surge Screener 涨幅7%+筛选分析子系统 设计Spec

- **日期**: 2026-09-18
- **状态**: 待用户审阅
- **决策**: 方案A——独立子包 `davis_analyzer/surge/`,纯脚本 cron 无人值守(2026-09-18 用户确认)
- **定位**: 每日数据更新后,筛选当日涨幅 >7% 的个股,九维综合分析(位置/资金/筹码/获利盘/压力/支撑/炒作预期/扫雷),全量入台账 + Top N 深析报告

## 1. 背景与目标

现有体系中 limitup 面向涨停事件研究、thermometer 面向板块温度,均不覆盖「大涨但未涨停」的中间地带。本子系统在每日盘后数据更新后,对当日 `pct_chg > 7%` 的个股(近数日 60~204 只/日)做结构化九维分析:

1. 相对位置
2. 资金流入情况
3. 主力筹码价格
4. 获利盘情况
5. 前方压力与阻力
6. 下方支撑
7. 个股炒作预期(转型/并购重组/经营项目景气度高量价齐升/行业景气度高/行业底部拐点/近期增持)
8. 个股扫雷(近期减持/行业下行/逆周期/周期顶部/近期定增/经营爆雷/持续实质亏损)
9. 综合分(Top N 深析排序依据)

**输出形态(用户拍板)**: 全量入 SQLite 台账(九维指标每股一行),md 报告只对综合分 Top 12 做深析段落,其余以汇总表格列出。不遗漏、报告可读。

**因子研究定位(2026-09-18 用户补充)**: 本系统同时是一套**待测因子库**——`surge_snapshot` 每个指标列即一个候选因子,台账持续沉淀供后续优化与有效性验证(§10)。所有外部获取的数据一律先落库再消费,原始层全量留存,规则层可由原始层重放重建。

## 2. 数据基础(全部已验证)

| 数据 | 表/接口 | 覆盖(2026-09-18 核实) | 用途 |
|---|---|---|---|
| 日线 | `daily_price` | 全A 5553 只,至当日,含 adj_factor | 位置/压力支撑/量价 |
| 个股资金流 | `moneyflow` | 2021-01 至今 5774 只 | 资金流入 |
| 筹码 | Tushare `cyq_perf` | **有权限**,按 trade_date 一次拉全市场 | 获利盘/主力成本/筹码峰压力支撑 |
| 公司事件 | `corp_event` | 2023 至今:增减持/回购/解禁/质押 | 炒作预期+扫雷 |
| 财报 | `financial` | 永久缓存 | 持续亏损 |
| 行业行情 | `sw_daily` + `sw_member` | 2022 至今 439 指数 | 行业景气/下行代理 |
| 板块温度 | `thermometer_sector` | 每日 | 行业拥挤度佐证(反向语义) |
| 涨停池 | `limit_pool` | 每日 | 天梯地位标注 |
| 研报 | `research` | 持续 | 覆盖热度标注 |
| 公告 | **巨潮资讯 cninfo**(新授权,2026-09-18 用户提出) | topSearch 查 orgId + hisAnnouncement 按股/日期窗口拉公告标题;沪深深北交所均实测可用 | 并购重组/定增/爆雷等大事,沉淀本地 `major_events` 表 |

**巨潮数据源授权记录**: 本项目外部数据源纪律为「Tushare 唯一,例外须用户批准」(intraday/baostock 先例)。2026-09-18 用户主动提出并批准接入巨潮公告检索,仅限公告标题列表拉取(只读、无鉴权、白名单域 cninfo.com.cn),落本地表后分析只读本地,与 baostock 模式同构。

**已知缺口(收窄)**: 「转型」无标准公告词,用重组/资产出售类事件作代理标注;「经营项目景气度」维持量价齐升量化代理。

**数据纪律**: 不向 `daily_basic` 回补历史(30 天滚动缓存纪律沿用);pledge 事件仅更新至 20251231,引用时标注数据陈旧。

## 3. 架构

```
davis_analyzer/surge/
  __init__.py
  cli.py        # argparse: run / backfill / status
  db.py         # 表管理(挂 market_data.db) + 日期规范化
  chips.py      # cyq_perf 拉取与 cyq_perf_cache 维护
  cninfo.py     # 巨潮拉取: orgId 映射 + 公告标题分页 + 关键词规则匹配 → major_events
  factors.py    # 九维指标计算(纯函数,DataFrame in/out)
  screen.py     # 编排:筛选 >7% → 巨潮拉取(命中池) → 装配九维 → 综合分 → 入库
  report.py     # md 报告生成(模板化,无 LLM)
  reports/      # surge_YYYYMMDD.md 输出目录
```

依赖方向: cli → screen → factors/cninfo/chips → db。factors 为纯计算不触网,便于单测。cninfo/chips 为唯一两个触网模块。遵循项目风格:`from __future__ import annotations`、loguru、Decimal 仅用于金额聚合处(指标比率用 float,frozen 先例同口径)。

权重单一真相源: `constants.py` 新增 `SURGE_WEIGHTS`(§5.9),SOP 同步。

## 4. 数据表(market_data.db 新增,日期 YYYYMMDD 对齐日线表)

```sql
CREATE TABLE IF NOT EXISTS cyq_perf_cache (
  ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
  his_low REAL, his_high REAL,
  cost_5pct REAL, cost_15pct REAL, cost_50pct REAL,
  cost_85pct REAL, cost_95pct REAL,
  weight_avg REAL, winner_rate REAL,
  fetched_at REAL,
  PRIMARY KEY (ts_code, trade_date));

CREATE TABLE IF NOT EXISTS surge_snapshot (
  trade_date TEXT NOT NULL, ts_code TEXT NOT NULL,
  name TEXT, industry TEXT, pct_chg REAL, amount_k REAL,
  pos_250d REAL, dist_ma20 REAL, dist_ma60 REAL,
  dist_ma120 REAL, dist_ma250 REAL, dd_high_250 REAL,
  ladder_label TEXT, is_st INTEGER, is_new INTEGER,
  elg_net_d0 REAL, lg_net_5d REAL, net_ratio_d0 REAL,
  consec_net_days INTEGER,
  cost_5pct REAL, cost_50pct REAL, cost_95pct REAL, weight_avg REAL,
  winner_rate REAL, winner_delta_5d REAL,
  resistance_price REAL, resistance_dist REAL,
  support_price REAL, support_dist REAL,
  hype_tags TEXT,    -- JSON 数组,如 ["增持","行业动量强"]
  risk_flags TEXT,   -- JSON 数组,如 ["减持","行业下行"]
  hype_count INTEGER, risk_flag_count INTEGER,  -- 数值化(因子验证用,综合分中间量)
  composite REAL, rank INTEGER,
  fetched_at REAL,
  PRIMARY KEY (trade_date, ts_code));
```

幂等: 同日重跑先 DELETE 当日行再 INSERT(limitup 先例)。

快照表只存报告表格所需关键列;cost_15pct/cost_85pct 等中间量不入快照,深析段现读 cyq_perf_cache。

### 4.1 个股大事库(巨潮沉淀,surge 自管;原始层+规则层两级)

```sql
-- 原始层: 巨潮拉取的公告标题全量落库(含未命中任何规则的,因子研发的原始素材)
CREATE TABLE IF NOT EXISTS cninfo_announcement (
  ts_code TEXT NOT NULL, ann_date TEXT NOT NULL,
  title TEXT NOT NULL,            -- 公告原文标题(去高亮标签)
  fetched_at REAL,
  PRIMARY KEY (ts_code, ann_date, title));

-- 规则层: 冻结规则匹配结果,可由原始层重放重建(不依赖重新请求巨潮)
CREATE TABLE IF NOT EXISTS major_events (
  ts_code TEXT NOT NULL, ann_date TEXT NOT NULL,
  event_type TEXT NOT NULL,   -- ma/divest/refinance/distress/ma_halt
  title TEXT,                 -- 公告原文标题
  direction TEXT,             -- positive/negative/neutral
  source TEXT,                -- 'cninfo'
  fetched_at REAL,
  PRIMARY KEY (ts_code, ann_date, event_type, title));

CREATE TABLE IF NOT EXISTS cninfo_org_map (
  ts_code TEXT PRIMARY KEY, org_id TEXT, updated_at REAL);
```

- 两级设计理由: 用户定位本系统为待测因子库——MAJOR_EVENT_RULES 是先验规则,后续优化迭代规则时由 `cninfo_announcement` 重放重建 `major_events`,无需重新请求巨潮;新事件类型的研发也直接从原始层起步。
- 拉取策略: 每日对命中池(当日 >7% 个股)按股拉近 180 日全部公告标题(分页 pageSize=30),**全量入原始层**,再由代码内冻结规则表 `MAJOR_EVENT_RULES` 匹配归类入规则层。噪音可审计(原始层可查未命中标题)。
- `column` 参数: 沪深用 `szse`、北交用 `bj`(920xxx,实测通过);沪市返回口径实施首日验证。
- orgId 经 topSearch 查询并缓存于 `cninfo_org_map`,避免重复调用。
- 限速 0.2s/请求 + 失败重试 1 次;单股失败降级为「该股事件维度缺失」,不阻塞整批。

`MAJOR_EVENT_RULES`(冻结先验,方向为标注默认值):

| event_type | 标题正则(示例口径,实施冻结) | direction |
|---|---|---|
| ma(并购重组) | 重大资产重组\|发行股份.*购买资产\|吸收合并\|重大资产购买 | positive |
| divest(资产出售,转型线索) | 重大资产出售\|出售.*股权\|转让控股权 | neutral |
| refinance(定增) | 向特定对象发行股票\|非公开发行 | negative |
| distress(爆雷/监管) | 立案\|警示函\|监管函\|问询函\|关注函\|处罚\|诉讼\|仲裁\|商誉减值\|终止上市 | negative |
| ma_halt(重组终止/终止发行) | 终止.*(重组\|发行\|购买) | negative |

## 5. 九维指标口径(冻结定义)

### 5.1 筛选与标注(不硬性剔除)

- 入池: 当日 `daily_price.pct_chg > 7.0`(含 BSE/科创/创业;ST 不剔除只标注,与 limitup「标注不参与过滤」精神一致)
- `is_st`: stock_basic 名称含 ST;`is_new`: 上市不足 60 交易日(daily_price 首见日推)
- `ladder_label`: limit_pool 命中则「N连板」,否则「非涨停」

### 5.2 相对位置

- 本维度用**后复权价**(close×adj_factor,除权日不断点)
- `pos_250d = (close - min(low,250)) / (max(high,250) - min(low,250))`;历史 <120 交易日 → NaN(次新天然缺失)
- `dist_maN = close/MA_N - 1`,N=20/60/120/250
- `dd_high_250 = close / max(high,250) - 1`

### 5.3 资金流入

- `elg_net_d0 = buy_elg_amount - sell_elg_amount`(万元)
- `lg_net_5d`: 近 5 交易日 (大单+超大单) 净额合计(万元)
- `net_ratio_d0 = net_mf_amount / (amount×10)`(**单位陷阱: moneyflow 万元,daily_price amount 千元,×10 对齐**)
- `consec_net_days`: elg_net 连续为正天数
- moneyflow 当日缺失 → 全列 NaN,宁缺毋错

### 5.4 主力筹码价格(cyq_perf)

- 落库 cost_5/15/50/85/95pct、weight_avg
- 深析呈现: 现价对 cost_5pct 的获利深度 `close/cost_5pct - 1`(低位主力筹码浮盈,衡量拉升安全垫)

### 5.5 获利盘

- `winner_rate`(cyq_perf 原值,百分数)
- `winner_delta_5d = winner_rate(t) - winner_rate(t-5)`(5 交易日变化;缺历史 → NaN)
- 先验档位(未校准,报告标注): ≥85 获利盘拥挤 / 60~85 高位 / 20~60 健康 / <20 低位

### 5.6 压力(取高于现价最近的档位)

- 本维度与 5.7 统一用**未复权现价口径**(与 cyq_perf 成本价及交易软件显示价位同口径,严禁与后复权价混用)
- 候选: cost_85pct、cost_95pct、his_high、max(high,120)(120 日滚动最高)
- `resistance_price = min(候选中 > close×1.005)`;`resistance_dist = resistance_price/close - 1`;全候选缺失 → NaN

### 5.7 支撑(取低于现价最近的档位)

- 候选: cost_15pct、cost_5pct、weight_avg、min(low,120)(120 日滚动最低)
- `support_price = max(候选中 < close×0.995)`;`support_dist = support_price/close - 1`

### 5.8 炒作预期 hype_tags(命中即列,不配分权重)

| 标签 | 口径 |
|---|---|
| 增持 | corp_event holder_trade direction=positive,近 90 日 |
| 回购 | corp_event repurchase,近 90 日 |
| 并购重组 | major_events ma 类,近 180 日(重组进行中的预期发酵窗口) |
| 转型线索 | major_events divest 类,近 180 日(资产出售代理,深析段结合 ma 时间线人工判断) |
| 行业动量强 | 所属申万 L2(sw_member 匹配,未匹配回退 L1)指数 60 日涨幅在全行业截面分位 ≥70% |
| 行业底部拐点 | 行业指数 250 日价格分位 <20% 且 20 日涨幅 >0 |
| 量价齐升 | 20 日涨幅 >15% 且 20 日均量/前 20 日均量 >1.5 |
| 研报覆盖热 | research 近 90 日 ≥3 家机构 |

### 5.9 扫雷 risk_flags

| 标签 | 口径 |
|---|---|
| 减持 | holder_trade direction=negative,近 90 日 |
| 解禁 | share_float 事件,近 90 日(含 magnitude 解禁比例) |
| 定增 | major_events refinance 类,近 180 日 |
| 爆雷/监管 | major_events distress 类,近 180 日(立案/监管函/诉讼/商誉减值/终止上市等) |
| 重组终止 | major_events ma_halt 类,近 180 日 |
| 质押率高 | 最新 pledge magnitude >50%(数据至 20251231,标注陈旧) |
| 持续亏损 | 最近 2 个年报 + 最新定期报告归母净利均 <0(financial/income) |
| ST | is_st |
| 行业下行 | 行业指数 60 日涨幅截面分位 ≤30% 且 20 日涨幅 <0 |
| 周期顶部 | 行业指数 250 日分位 ≥80% 且 20 日涨幅 <0 |

### 5.10 综合分(先验,未校准)

各维映射 0~100 后加权,`SURGE_WEIGHTS` 进 constants.py:

```
money 0.20 | chips 0.15 | winner 0.10 | position 0.10
resist_support 0.10 | hype 0.20 | risk 0.15
```

- hype 得分 = min(100, 25×命中数);risk 得分 = max(0, 100-20×命中数)
- resist_support 得分 = 100×clip(resistance_dist/0.20, 0, 1)(压力越远上方空间越大;support_dist 只入报告不入分)
- 因子验证路线见 §10(本期只落台账,calibrate 后续另立任务)

## 6. 报告

`surge/reports/surge_YYYYMMDD.md`:

1. **头部**: 日期、命中数、市场环境(thermometer_market 大盘五维引用,注明温度反向语义)
2. **全量表**: 按综合分排序,列 = 代码/名称/行业/涨幅/位置分位/超大单净额/获利盘%/winner Δ5d/压力距/支撑距/hype 数/risk 数/综合分
3. **Top 12 深析**: 每股一段——九维逐项数字 + 事件时间线(corp_event 近 90 日 + major_events 近 180 日,日期+事件+规模/标题)+ 叙事待查清单(仅剩「转型判断」需人工)
4. **尾部**: 口径说明、缺失标注(pledge 陈旧/当日 cyqperf 回退/次新 NaN)、免责声明

## 7. 调度与防御

- user systemd timer `surge-run`,工作日 19:30(19:20 daily_refresh 之后,19:35 thermometer 之前空档)
- run 流程: 筛选 >7% → 命中池巨潮拉取(约 60~204 只 × 2~4 请求,0.2s 限速 ≈ 2~4 分钟) → cyq_perf 当日按日期拉取(1 次) → 九维装配 → 入库 → 报告
- 当日 cyq_perf 未出 → 回退最近一日缓存并在报告标注;backfill 幂等跳过已有日期
- `backfill`: ① cyq_perf 按日期回补近 30 个交易日(winner_delta_5d 最低需 6 日,留裕量);② major_events 按指定日期的命中池重拉巨潮;③ `--replay N` 历史截面回放:对最近 N 个交易日逐日重建 surge_snapshot(数据全部来自本地已有表+cyq_perf 按日期回补;巨潮默认**不**回放,`--with-cninfo` 显式开启且仅对回放日命中池拉取——防巨潮请求量失控);可重复执行
- 巨潮整批失败(网络/接口变更) → 事件维度降级标注「巨潮未取到」,主流程照常出报告;巨潮接口属网页 API,字段变更风险高于 Tushare,cninfo.py 单点隔离 + 响应结构断言
- 非交易日/当日 daily_price 未更新 → 自检退出并记 log
- 限流: 触网仅 cyq_perf(每日 1 次)与巨潮(命中池逐股)两处

## 8. 测试

- `tests/test_surge_factors.py`: 合成日线 fixture 验证 pos_250d/压力支撑选取/量价齐升口径
- `tests/test_surge_cninfo.py`: 规则表匹配(重组/定增/爆雷标题样例、终止类优先于 ma 类)、orgId 缓存、响应结构断言与降级路径、原始层→规则层重放幂等
- 报告空数据防线: 0 命中/全 NaN/巨潮全失败单照常出报告
- `backfill --replay` 幂等与快照纯度(回放行不含未来信息列)
- 权重一致性: test_doc_consistency 增 SURGE_WEIGHTS 与 SOP.md 同步校验

## 9. 不做的事(范围外)

- 联网仅限巨潮公告标题白名单域,不做通用 web 搜索/新闻聚合(叙事深挖仍可由人工或会话 agent 补充)
- 公告只取标题做规则匹配,不解析 PDF 全文
- 不做 IC 校准(留台账沉淀后)
- 不接 cardgen/飞书推送(先跑通日报,后续按需)
- 不改现有表与既有子系统;不动 daily_basic 缓存与 corp_event(巨潮事件独立落 major_events,不与 corp_event 混写)
- calibrate 因子验证子命令不在本期实施(§10 里程碑,表结构本期保证不堵路)

## 10. 因子研究路线(后续里程碑,2026-09-18 用户定位补充)

本系统按「待测因子库」标准设计,后续优化与验证遵循:

- **台账即因子库**: `surge_snapshot` 每个指标列即一个候选因子(列名=因子名,口径冻结于本 spec §5,改口径须 --bump 式记录);`hype_count`/`risk_flag_count` 为数值化汇总,hype_tags/risk_flags 明细可展开为哑变量因子。
- **快照纯度纪律(防前视偏差)**: snapshot 只存 T 日及以前的截面信息,严禁落任何未来数据列(前向收益、未来事件);验证所需前向收益在分析时 join daily_price 动态计算,不回写 snapshot。
- **验证方式**(thermometer calibrate 先例): 截面 Rank IC / ICIR / 五分位分组前向收益(5/10/20 日) / walk-forward 硬验收;验证对象为各单因子与 composite。
- **历史样本获取**: `backfill --replay N` 逐日回放生成历史 snapshot(cyq_perf 按日期回补,每历史日 1 次调用;巨潮默认不回放),因子验证不必等待实时沉淀。
- **规则层重放**: MAJOR_EVENT_RULES 迭代后由 cninfo_announcement 原始层重建 major_events 并重放受影响日期的 snapshot,规则实验与数据获取解耦。
