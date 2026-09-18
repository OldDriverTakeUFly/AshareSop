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

**已知缺口(设计为「待查」标注,不阻塞)**: 并购重组、转型、定增公告、经营爆雷(审计意见/商誉减值警示)无结构化数据源——corp_event 无此类事件,公告类接口积分不够。报告深析段列出「叙事待查」清单,由人工/会话 agent web 检索补,不在无人值守脚本中联网。

**数据纪律**: 不向 `daily_basic` 回补历史(30 天滚动缓存纪律沿用);pledge 事件仅更新至 20251231,引用时标注数据陈旧。

## 3. 架构

```
davis_analyzer/surge/
  __init__.py
  cli.py        # argparse: run / backfill / status
  db.py         # 表管理(挂 market_data.db) + 日期规范化
  chips.py      # cyq_perf 拉取与 cyq_perf_cache 维护
  factors.py    # 九维指标计算(纯函数,DataFrame in/out)
  screen.py     # 编排:筛选 >7% → 装配九维 → 综合分 → 入库
  report.py     # md 报告生成(模板化,无 LLM)
  reports/      # surge_YYYYMMDD.md 输出目录
```

依赖方向: cli → screen → factors/chips → db。factors 为纯计算不触网,便于单测。遵循项目风格:`from __future__ import annotations`、loguru、Decimal 仅用于金额聚合处(指标比率用 float,frozen 先例同口径)。

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
  composite REAL, rank INTEGER,
  fetched_at REAL,
  PRIMARY KEY (trade_date, ts_code));
```

幂等: 同日重跑先 DELETE 当日行再 INSERT(limitup 先例)。

快照表只存报告表格所需关键列;cost_15pct/cost_85pct 等中间量不入快照,深析段现读 cyq_perf_cache。

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
| 行业动量强 | 所属申万 L2(sw_member 匹配,未匹配回退 L1)指数 60 日涨幅在全行业截面分位 ≥70% |
| 行业底部拐点 | 行业指数 250 日价格分位 <20% 且 20 日涨幅 >0 |
| 量价齐升 | 20 日涨幅 >15% 且 20 日均量/前 20 日均量 >1.5 |
| 研报覆盖热 | research 近 90 日 ≥3 家机构 |
| 待查-并购重组/转型 | 固定占位标签,深析时人工/会话检索 |

### 5.9 扫雷 risk_flags

| 标签 | 口径 |
|---|---|
| 减持 | holder_trade direction=negative,近 90 日 |
| 解禁 | share_float 事件,近 90 日(含 magnitude 解禁比例) |
| 质押率高 | 最新 pledge magnitude >50%(数据至 20251231,标注陈旧) |
| 持续亏损 | 最近 2 个年报 + 最新定期报告归母净利均 <0(financial/income) |
| ST | is_st |
| 行业下行 | 行业指数 60 日涨幅截面分位 ≤30% 且 20 日涨幅 <0 |
| 周期顶部 | 行业指数 250 日分位 ≥80% 且 20 日涨幅 <0 |
| 待查-定增/爆雷 | 固定占位标签(定增可用近 180 日解禁事件旁证,报告注明为代理) |

### 5.10 综合分(先验,未校准)

各维映射 0~100 后加权,`SURGE_WEIGHTS` 进 constants.py:

```
money 0.20 | chips 0.15 | winner 0.10 | position 0.10
resist_support 0.10 | hype 0.20 | risk 0.15
```

- hype 得分 = min(100, 25×命中数(不含待查));risk 得分 = max(0, 100-20×命中数(不含待查))
- resist_support 得分 = 100×clip(resistance_dist/0.20, 0, 1)(压力越远上方空间越大;support_dist 只入报告不入分)
- 台账沉淀 ≥60 交易日后可按 thermometer calibrate 模式做 IC/walk-forward 校准(本期不实施)

## 6. 报告

`surge/reports/surge_YYYYMMDD.md`:

1. **头部**: 日期、命中数、市场环境(thermometer_market 大盘五维引用,注明温度反向语义)
2. **全量表**: 按综合分排序,列 = 代码/名称/行业/涨幅/位置分位/超大单净额/获利盘%/winner Δ5d/压力距/支撑距/hype 数/risk 数/综合分
3. **Top 12 深析**: 每股一段——九维逐项数字 + 近 90 日事件时间线(日期+事件+规模)+ 叙事待查清单
4. **尾部**: 口径说明、缺失标注(pledge 陈旧/当日 cyqperf 回退/次新 NaN)、免责声明

## 7. 调度与防御

- user systemd timer `surge-run`,工作日 19:30(19:20 daily_refresh 之后,19:35 thermometer 之前空档)
- 当日 cyq_perf 未出 → 回退最近一日缓存并在报告标注;backfill 幂等跳过已有日期
- `backfill`: cyq_perf 按日期回补近 30 个交易日(winner_delta_5d 最低需 6 日,留裕量);可重复执行
- 非交易日/当日 daily_price 未更新 → 自检退出并记 log
- 限流: cyq_perf 每日新增仅 1 次调用;run 全程触网仅此一处

## 8. 测试

- `tests/test_surge_factors.py`: 合成日线 fixture 验证 pos_250d/压力支撑选取/量价齐升口径
- 报告空数据防线: 0 命中/全 NaN 单照常出报告
- 权重一致性: test_doc_consistency 增 SURGE_WEIGHTS 与 SOP.md 同步校验

## 9. 不做的事(范围外)

- 不联网检索(并购重组/定增/爆雷叙事=待查占位,人工/会话补)
- 不做 IC 校准(留台账沉淀后)
- 不接 cardgen/飞书推送(先跑通日报,后续按需)
- 不改现有表与既有子系统;不动 daily_basic
