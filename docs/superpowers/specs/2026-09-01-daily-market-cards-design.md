# 每日盘面复盘卡片(连板天梯 + 龙虎榜)设计

日期:2026-09-01
状态:已与用户逐节确认,待终审
关联:`docs/superpowers/specs/2026-08-28-cardgen-design.md`(cardgen 主设计)、`scripts/daily_bulletin.py`(日报生成器先例)、`stockhot/limit_up`、`stockhot/dragon_tiger`(数据采集层)

## 一、背景与定位

cardgen 已覆盖产业链/个股/方法论等研究型卡片。用户提出扩展**每日数据复盘卡**品类:盘后发布当日《连板天梯》与《龙虎榜》两张独立卡片,消费 daily-market-scan 每日采集、已落库的数据,填补"当日盘面情绪+资金"内容空档。

数据现状(2026-09-01 实测,`storage/database/stockhot.db`):

| 数据 | 表 | 键 | 状态 |
|---|---|---|---|
| 连板梯队 | `analysis_results` | `(trade_date, 'limit_up_analysis')` → JSON key `consecutive_boards` | ✅ 今日 5 档,最高 7 板 |
| 涨停池明细 | `daily_data` | `(trade_date, 'limit_up_pool')` | ✅ 83 只,含封单/封板时间/换手/板块/炸板次数 |
| 龙虎榜个股明细 | `daily_data` | `(trade_date, 'dragon_tiger_detail')` | ✅ 68 只,含上榜原因/净买卖额 |
| 活跃营业部 | `analysis_results` | `(trade_date, 'dragon_tiger')` → key `brokers` | ✅ 180 行 |
| 机构席位 | 同上 → key `institutional` | ⚠️ **采集缺陷:`inst_name` 与 `net_amount` 缺失**(Tushare `top_inst` 的 `exile` 列未映射),须前置修复 |

合规现状:`涨停/连板/龙虎榜/游资`等品类词**不在**敏感词表;但`追高/上车/抄作业/标的/庄家/主力/拉盘/内幕`等情绪叙事高频词**全部在表内**。结论:品类可做,口吻必须是**数据复盘**,禁止交易指向。

## 二、方案对比(用户已确认 A)

| 方案 | 形态 | 取舍 |
|---|---|---|
| **A(采用)** | 新增独立生成器脚本(参照 daily_bulletin.py),读 stockhot.db 生成日期化工程的 facts+spec,走 cardgen 现有四道闸;cardgen 核心近零改动 | 95% 内容是确定性数据,天然模板化;合规风险最高的品类恰恰最需要机器闸;与公告日报先例同构 |
| B | cardgen 核心加 `--daily` 日报品类原生支持 | 一体化,但 cardgen 膨胀,违背其"创作型流程"定位,改动面大 |
| C | 轻量脚本直出 PNG,绕过 cardgen | 代码最少,但失去数字溯源与合规闸——本品类合规风险最高,不可接受 |

## 三、工程组织

每日一个**日期化工程**(镜像公告日报的日期文件模式;每天的卡是新内容,不做滚动工程,避免 version 无限 bump 污染修订链语义):

```
docs/小红书卡片/未发布/连板天梯/2026-09-01/
  facts.json / cards.spec.json / output/ / RELEASE.json
docs/小红书卡片/未发布/龙虎榜/2026-09-01/
  同构
```

- cardgen 台账 topic 主键 = `连板天梯/<YYYY-MM-DD>`、`龙虎榜/<YYYY-MM-DD>`(日期路径即主键,每天新纪录)。
- 发布后 `sync` 照常把工程挪入 `已发布/连板天梯/2026-09-01/`(实现时验证 sync 对嵌套日期目录兼容,不兼容则改 sync)。

## 四、卡片内容结构

### 4.1 《今日连板天梯》(5 页)

| 页 | 内容 | 数据来源 |
|---|---|---|
| 封面 | 今日情绪温度:最高板 N 板(股名)+ 涨停/连板/炸板家数 stats | summary + consecutive_boards |
| 天梯主卡 | 梯队表:按板数降序,板数→个股名称串 | consecutive_boards 全量 |
| 高度明细 | 最高板个股:封单金额/首次封板时间/换手率/所属板块;vs 昨日高度(晋级/断板/持平,措辞由脚本按数据判定) | limit_up_pool + 前一日 analysis_results |
| 板块联动 | 涨停家数居前板块 + 代表股 | limit_up_pool 按 sector 聚合 |
| 尾卡 | 免责话术 + 数据来源 | — |

### 4.2 《今日龙虎榜》(6 页)

| 页 | 内容 | 数据来源 |
|---|---|---|
| 封面 | 上榜 N 家/整体净买额/上榜 ∩ 连板股数 | detail 聚合 + consecutive_boards |
| 个股净买 Top10 | 名称/涨跌幅/净买额/上榜原因(截断) | dragon_tiger_detail |
| 净卖 Top5 | 资金流出侧 | 同上反向 |
| 活跃营业部 | 净买额居前营业部 Top5(全名截断排版) | dragon_tiger.brokers |
| 机构席位 | 机构净买卖动向 | dragon_tiger.institutional(**依赖前置修复**) |
| 交叉+尾卡 | 上榜的连板股一览(呼应天梯卡)+ 免责 | detail ∩ consecutive_boards |

**空维度降级**:机构席位或交叉视角当日无数据时,该页降级为事实性说明句(如"今日无机构席位上榜"),不阻塞整卡生成;核心页(梯队/净买榜)无数据才整体拒绝。

### 4.3 叙事纪律(关键决策)

两类卡**没有研报锚点**,故 v1 叙事层 = **零观点、纯数据描述句**("最高连板 7,较昨日 +1"式事实句),所有数字全部走 `$fact` 结构化引用。不写"怎么看情绪周期"类解读——那属于方法论研报选题,须先立研报再蒸馏,不在本设计范围。此边界同时满足 agent 纪律"观点须指回研报"与模板全自动生成不自造观点的要求。

## 五、数据层与事实溯源

- **source.kind 新增 `"stockhot"`**:现有 `{report, tushare, manual}` 均不适用(数据是 Tushare 优先、东财 AKShare 兜底的混源,落库 stockhot.db 而非 davis tushare 缓存)。指纹格式:`stockhot.db:analysis_results:limit_up_analysis@2026-09-01:consecutive_boards`,支持回放验证(重查同一行比对)。validator 的 kind 枚举校验同步放行(小改)。
- **foot 来源标注诚实化**:每卡 foot 统一 `数据来源:沪深交易所/东方财富,经 stockhot 采集`,不写 Tushare。
- **expires**:全部 fact 统一 `T+1 00:00`,RELEASE.expires_at 即当日有效;次日 enqueue 自动拒绝(复盘卡过时不发,故意设计)。
- **前置修复(独立小改动)**:`stockhot/dragon_tiger` 机构席位 `inst_name`(Tushare `top_inst` 的 `exile` 列)映射缺失,须先修并用当日数据验证。

## 六、生成器脚本与 cron

- 单脚本 `scripts/daily_market_cards.py --type {ladder,lhb,all}`,共用 stockhot.db 读取层;读库→产工程→自检,无 LLM。
- 流程:读当日数据 → **完整性自检**(ladder 需 `limit_up_analysis`+`limit_up_pool`;lhb 需 `dragon_tiger_detail`+`dragon_tiger` 分析行;缺任一→拒绝生成、非零退出、不硬造)→ 写 facts.json + cards.spec.json → 登记 cardgen 台账 → `validate` → `build`,一键到 rendered。
- **cron 20:30(工作日)**:龙虎榜数据约 17:00-18:00 落库,留足余量;数据未就绪或法定节假日无数据→报告空转,不重试(对齐公告日报 cron 纪律)。
- **生成不发布**:enqueue 与发布留人工——责任分层里"值不值得发"归人。cron 仅产出 rendered 卡并汇报。
- 叙事模板(含晋级/断板/持平措辞分支)在脚本内,随 git 迭代。

## 七、合规设计

- 模板措辞规范(沉淀入脚本注释+方法论):只用事实词汇(N连板/净买额/换手/封单);禁用表内词(追高/上车/抄作业/标的/庄家/主力/拉盘/内幕/赌);规避诱导句式正则(买的是什么/你应该/下一个动作等)。
- **金额口径陷阱**:敏感词表含二字词`买入`/`卖出`——龙虎榜金额措辞一律用"净买额/净卖额"口径,禁用"买入额/卖出额"字样;营业部页只呈现 net_amount。
- 每卡 foot 来源 + 尾卡"不构成投资建议"——validator 强制,模板预置。
- validate 失败即阻塞 build,四道闸一道不少。

## 八、测试与验收

pytest(对齐 `davis_analyzer/tests/test_cardgen_*` 惯例):

- 生成器:fixture 小库→生成 facts/spec→断言 $fact 引用与 facts 一一对应;数据缺失→拒绝生成;措辞分支(晋级/断板)按数据正确选择。
- 端到端:生成→validate 全过→build 渲染成功。
- 兼容:cardgen `sync` 对嵌套日期目录挪动正确。

**验收**:用 2026-09-01 真实数据出两张卡,validate 全过,`vision.py` 目检 5/5 + 6/6 页通过,发布留人工。

## 九、关键设计决策记录

1. **每日复盘卡定位**(用户确认):盘后发当日数据,非方法论教学卡。
2. **两张独立卡**(用户确认):《今日连板天梯》与《今日龙虎榜》各自成工程、各自发布。
3. **脚本全自动生产**(用户确认):生成器产 facts+spec,agent 只跑 validate/build/enqueue;叙事模板化可迭代。
4. **龙虎榜三维度全做**(用户确认):个股+营业部+机构,机构采集缺陷前置修复纳入范围。
5. **零观点叙事**:无研报锚点即无观点句,v1 纯数据描述。
6. **日期化工程而非滚动工程**:每日新 topic 新纪录,修订链语义不混淆。
7. **source.kind=stockhot**:诚实标注混源,指纹可回放。
8. **expires=T+1**:复盘卡过时不发。
9. **生成不发布**:cron 到 rendered 为止,enqueue/发布留人工。

## 十、范围外(明确不做)

- 营业部游资画像/游资故事叙事(仅呈现净买额数据);
- 情绪周期方法论解读卡(需先立研报);
- publisher 侧任何改动;
- card_factory 改动(禁改,对接只读)。
