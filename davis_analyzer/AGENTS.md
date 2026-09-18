# AGENTS.md — davis_analyzer 项目 Agent 协作规范

> 本文件由 ZCode(及兼容 agent)自动加载,定义本项目的工作约定。所有 agent 在本项目内执行任务时遵循以下规范。

## 项目概述

**davis_analyzer** —— 基于「戴维斯双击」估值理论的 A 股选股分析器。通过 Tushare Pro 拉取行情/财报数据,计算 3 年历史估值分位(PE/PB)、综合景气度评分、三层困境反转信号,对低估值候选股排名并生成模板化深度研报。同时包含周期再平衡回测引擎和模拟交易子系统。

- **核心语言**:Python 3.11+(代码使用 `from __future__ import annotations` + PEP 604 联合类型)
- **回测/数据**:自研(基于 pandas/numpy),不依赖 Zipline/Backtrader
- **数据源**:Tushare Pro(唯一外部数据源)。**唯一例外**:`intraday/` 日内做T研究沙盒用 baostock 拉分钟线(2026-08-19 用户批准)——只落独立库 `storage/database/intraday_research.db`(表内标注 source),生产 pipeline 与 market_data.db 缓存不得读取;背景:Tushare stk_mins 当前积分档限频 1 次/小时、2 次/天,无法承担分钟回补。

## Agent 工作方式(Karpathy 四条,本地化版)

1. 动手前先想清楚方案,非平凡改动先出计划再写代码。
2. 从能解决问题的最简单方案开始,不过度设计。
3. 只做与任务直接相关的最小修改,不顺手重构。
4. 交互式会话中遇歧义先问再动手;**定时/无人值守任务**(盘面扫描、盘后总结、盘前报告等 cron 流程)不等待人工——按各 SOP 纪律记录异常后继续执行,事后在报告中说明。

## ⚠️ 关键架构事实(动手前必读)

这三条是新人/agent 最容易踩的坑,务必先理解:

1. **本包不是自包含的**:`tushare_client.py`、`paper_trading/*`、迁移脚本都 import 了父项目的 `stockhot.data_layer.market_db` / `stockhot.storage.database` / `stockhot.core.config`。
   - **必须从父仓库根目录** `/home/leo/Projects/CodeAgentDashboard/` 运行(`pip install -e .` 装的是父包 `stockhot`)。
   - **脱离父项目单独运行 davis_analyzer 会失败**。

2. **真实缓存不在 `cache/` 目录**(那只是个 `.gitkeep` 占位)。真正的缓存在父项目的 SQLite 数据库:
   - 路径:`storage/database/market_data.db`(与 `stockhot` 包共享)
   - 三张表:`stock_basic`(7天TTL)、`daily_basic`(24hTTL,增量)、`financial`(永久,按 `(ts_code, end_date, endpoint)` 唯一)
   - 找缓存数据去那里,别翻 `cache/`。

3. **import 有副作用**:`config.py` 在 import 时就会 `load_dotenv()` 并 `mkdir` 创建 `CACHE_DIR`/`STUDIES_DIR`。任何 import 链触到它,都会做文件系统 + 环境变量改动。

## 模块划分与依赖方向

```
cli.py / __main__.py          ← 入口(argparse: run / deep-research / rescore)
    │
    ▼
pipeline.py                   ← 8 步筛选编排器(核心调度)
    │
    ▼
各因子引擎:                     ← 评分模块(互相独立)
  valuation / valuation_forward   (估值)
  prosperity / prosperity_sector / prosperity_inflection (景气度)
  momentum / trend / distress / dividend
  forecast / profitability / holder_concentration
    │
    ▼
scoring.py                    ← 4 维综合 → 最终戴维斯双击分
    │
    ▼
tushare_client.py             ← 数据层(API + SQLite 缓存 + 限流 400/min + 重试)
```

涨停研究子系统(独立):`limitup/`(backfill 数据回补 → events/patterns/sentiment 事件与形态 → study 事件研究 → engine 事件驱动打板回测,CLI: python -m davis_analyzer.limitup)。

策略锦标赛子系统(相对独立):`tournament/`(adapters 参赛者适配 → judge 统一窗口评估 → scorecard/allocator 评分与权重分配 → replay 历史回放 → evolution CPCV-lite 参数进化战役 → champions 冠军存档与部署校验,台账写入共享 SQLite 的 tournament_ledger 表,CLI: python -m davis_analyzer.tournament {run|replay|evolve|champions})。

板块温度计子系统(独立):`thermometer/`(universe 申万L1/L2池与成分+概念层 → data sw_daily/ths_daily回补+refresh_recent盘后自举[直连Tushare补最近缺失日,不依赖stockhot采集链] → moneyflow_agg 成分自聚合主力净额[Decimal] → factors 五族因子[v2中期窗口60-120日,水平/斜率正交化+量价交互ret60方向] → scoring 截面分位温度+大盘五维 market_temp → calibrate IC/walk-forward硬验收 → report 日报 + cardgen thermo卡;表挂 market_data.db 九张,CLI: python -m davis_analyzer.thermometer {backfill|run|calibrate|report|status})。**反向语义(2026-09-17 用户拍板部署)**:温度=板块拥挤度,高温=预期打得过满注意风险(人声鼎沸处),低温=关注度低可跟踪左侧(无人问津时);校准证据=反向OOS IC+0.068/ICIR0.28,严禁把高温解读为买入信号,2026年效应减弱已知需跟踪;权重单一真相源在 constants.py THERMOMETER_WEIGHTS+THERMOMETER_WINDOWS。**调度**:user systemd 三 timer(thermometer-run 工作日19:35 / thermometer-card 19:45[长图链:daily_market_cards --type thermo --no-render 出工程过四道闸 → daily_longpic --kind thermo --enqueue --push 渲染750px长图+文案入池推群;**六页短卡2026-09-18停出**,cardgen工程仅作facts数字闸校验层] / thermometer-universe 周日08:00);本机定时体系是 systemd timer 不是 crontab(/var/spool/cron root-only)。**长图数字闸**:daily_longpic.numbers_gate 对工程facts零未锚定才放行(style块/属性豁免);全库长图审计工具 scripts/longpic_numbers_check.py(已知债务:六个产业链长文图卡facts为叙事锚格式,机器闸不可运行,改版时逐工程结构化)。**数据纪律**:①大盘资金维历史口径必须走 sector_moneyflow_daily L1 聚合沉淀(market_flow_from_sectors)——daily_basic 是 30 天滚动缓存(cleanup_expired_cache 每日删30天前行),严禁向 daily_basic 回补历史、严禁依赖它算历史分位;②卡片数字全 facts 溯源,发稿文案零数字,推送 tags 末行;设计spec见 docs/superpowers/specs/2026-09-13-thermometer-design.md(§15 v2窗口重构/§16 反向语义部署)。

**回测子系统**(相对独立):`backtest.py`(周期再平衡主循环) → `backtest_factors.py`(横截面因子评分) → `backtest_report.py`(收益/夏普/回撤 + CSV 导出)。

**日内做T研究子系统**(独立沙盒):`intraday/`(db 独立研究库 + backfill baostock 分钟线断点回补 → engine 闭环回转引擎[T+1 卖出池/次bar成交/涨跌停拒单/收盘竞价] → features 因果特征 → strategies 朴素+增强策略族 → report 对账与汇总 → paper_shadow 模拟盘影子验证[cron 19:55 盘后回放,真实底仓,台账 intraday_shadow_trade/run + 数据增强三表:universe 每日全宇宙特征快照与状态分类(含 near_miss/被过滤样本,防静默排除)/exit_alt 收盘竞价退出反事实与 MAE/mkt 市场环境 regime],CLI: python -m davis_analyzer.intraday {backfill|status|verify|run|shadow|shadow-report|shadow-enrich})。注意:回补按月块记账,当月数据未收盘不完整——增量更新需先删当月 backfill_chunk 记录再重跑,**每月 1 日 cron 例行删上月块+重跑 backfill(宇宙=当前持仓,自动扩容,防新持仓 vol_ratio1=None 被静默过滤)**;影子验证依赖 19:20 daily_refresh 先行,pre_close 自行推导不依赖当日日线完整性;shadow-enrich 只回填快照类台账,不动成交台账(历史底仓不可重建,shares 列为当前快照口径);**历史日补跑前必须核对 daily_price 已含该日与前一日两日行(当日 19:20 daily_refresh 之后),否则 pre_close 用陈旧锚——8/19 台账 10 笔中 6 笔即此病(见 docs/回测记录/做T影子验证数据增强与首期数据体检_2026-08-28.md §四)**。结论见 docs/回测记录/日内做T引擎首测_2026-08-19.md、做T隔夜退出校验_2026-08-25.md。

**金融卡片生成子系统**(独立):`cardgen/`(facts 事实清单溯源 → $fact 物化 → validator 四道机器闸[数字全量核对/合规敏感词+免责/完整性/事实自检] → builder 渲染[复用 scripts/card_factory]→ RELEASE.json 发布包,台账 storage/database/content_cards.db,CLI: python -m davis_analyzer.cardgen {init|ingest|validate|build|status|enqueue|sync})。**双文件夹约定(2026-09-01)**:工程按发布状态归档于 `docs/小红书卡片/未发布/` 与 `已发布/`(init 建在未发布/,根目录不再放新工程,存量工程可解析);发布成功(执行 `queue.py mark <id> published`)后必须跑 `python -m davis_analyzer.cardgen sync` 把对应工程挪入已发布/——sync 只读消费 publisher 的 publish_queue 表(不改 content_publisher 代码),幂等可重复;`build --bump` 会把已发布工程自动挪回未发布/(待重发)。**agent 纪律**:①卡片上任何数字必须登记 facts.json 且带来源锚点(研报#章节/Tushare查询指纹),叙事观点必须能指回研报章节;②已 rendered 版本变更须 --bump --reason,过期(expires_at)卡片禁止 enqueue;③不得修改 scripts/card_factory 与 scripts/content_publisher(对接只读;配图经 builder 后处理注入,不改 card_factory;例外:2026-09-04 用户授权 M5——queue.py/board.py 增「放弃」abandon 按钮,行+publish_log 归档进 content_publisher_archive.db 后活池删除,先归档后删除保原子,不新增状态;2026-09-05 用户授权修复 abandon 归档撞 id bug——合成审计行显式取 ≥9 亿保留 id 段(ABANDON_LOG_ID_BASE,勿走自增),回归测试 test_publisher_abandon.py 锁定);④配图纪律(2026-08-30):只用公有领域/CC授权图(人物=美国政府官方照,实物=Wikimedia Commons 并 API 核实授权),image 字段 src/license/credit 三必填且 license 文本禁带数字,布局按「密集卡=corner/留白卡=底部/封面不放」选,详见方法论§8。设计 spec:docs/superpowers/specs/2026-08-28-cardgen-design.md。**形态分层(2026-09-05 用户拍板)**:宏观研讨/方法论→长文笔记为默认发布形态(docs研报=真相源,长文md=发布载体,数字锚研报,叙事原型轮换);个股研报→长文优先,双闸=时效(估值/行情数字须刷新或5天窗口内发并标数据截至)+合规(结论只以区间/矩阵表述);产业链研报→**长图卡**(2026-09-18 用户拍板,观感验证良好后升级):750px宽html→playwright 2x截图长图(默认整张,超9000px才拆上下),长度不限、内容向研报密度看齐,但**首屏必须是钩子**(最大反差/核心背离/最响数字,黄金三秒);配文字笔记(摘要钩子+图)发稿,工程=长图.html+长图.png+文案.md+facts.json四件套,工具链固化 docs/小红书卡片/未发布/{render_longpics.py,check_overflow.py}(溢出检测必须全过);纪律沿用:数字锚facts.json/敏感词全表含html/时效闸5天/tags末行/发布永远人工;短卡重点式(4-6页)降为可选形态;**板块热点复盘(2026-09-18 新系列,首作光通信复活已过观感验证)**:事件驱动盘面热点单长图(深蓝黑#0a0f1e+琥珀金#f5b942+冰蓝#6db9ff系列肤),工程=板块热点复盘/{日期}_{事件短名}/四件套(结构同产业链长图卡),骨架规范与颜色语义(行情红涨绿跌/定性好坏青绿粉红)见 spec docs/superpowers/specs/2026-09-18-sector-hot-recap-longpic-design.md;**当日出品纪律:盘后例行刷新会重写口径(19:20前复权序列/19:35温度计重算前值),必须以库内现行值为准、措辞随数修正**;温度计反向语义铁律沿用;详见方法论§十一
每日复盘卡(2026-09-01,2026-09-02扩入发布规划):连板天梯/龙虎榜——`scripts/daily_market_cards.py --type {ladder,lhb,all} [--enqueue]` 采集+生成+渲染+入池(生成逻辑在 davis_analyzer/cardgen/daily.py,发稿文案在 daily.publish_copy 固定模板,零数字零敏感词由测试锁定),工作日 17:50 cron 自带采集无人值守,龙虎榜未披露则等≤40分钟;入池≠发布,发布永远人工;数据叙事+预审洞察库(2026-09-03:见解句零数字/敏感词由测试锁定,脚本按当日形态机械选用,锚点注释在 daily.py 洞察库段;卡片收束页 kbox 与发稿文案「盘后观察」双出口),facts source.kind=stockhot 指纹溯源,expires=当日(过时不发);转债混榜(名字「转N」/代码11x/12x段)已过滤。**废稿池约定(2026-09-02)**:过期未发工程终态归档于 `docs/小红书卡片/废稿/`(手工挪入,sync/归位逻辑跳过该文件夹,台账留档;需复活手工挪回未发布/);发稿池死行(非 published 且过 release_expires 的行)连同 publish_log 挪入同构库 `storage/database/content_publisher_archive.db`,活池只留可发内容——每交易日 23:40 日卡 cron 顺手执行,queue.py 状态机为闭合集不可新增「废稿」状态(勿改 content_publisher;人工放弃走 queue.py abandon——M5 2026-09-04 用户授权)。**运维群通知双脚本(2026-09-13)**:`scripts/pool_digest.py`(入池待办摘要→飞书红薯运营群 FEISHU_XHS_CHAT_ID,只读池库,手动/会话尾随跑)与 `scripts/content_publisher/prep_push.py`(已备料项图文推送,19:30 timer)——推送≠发布,发布永远人工;**群推送文案铁律(2026-09-15)**:任何含 tags 的推送文案,tags 必须是消息最后一行——话题标签后跟任何文字(如「— 已入池,发布人工」)都会使 # 失效;运营提示只放头部括号行或拆独立消息(daily_market_cards push_one / prep_push._push 已按此修正,hf-papers-trend push_feishu.py 拆消息为先例;2026-09-15 用户授权改 prep_push 此一处);**尽早推送机制(2026-09-15,当日 17:50 cron 因机器休眠被跳过、拖到 21:54 才人工补跑的事故沉淀)**:①补推哨兵 cron(工作日 18-23 点每 20 分钟查当日天梯/lhb 推送锁,缺锁即按 17:50 流程补跑——规格落盘 docs/superpowers/prompts/card_sentinel_cron_prompt.md,建好后按文件头约定消费删除);②在线窗口防休眠:`scripts/after_hours_inhibit.sh`(双模式:after/trading)+ user systemd 双 timer——`after-hours-inhibit.timer`(工作日 17:30 持 sleep:idle block 锁至 24:00)与 `trading-hours-inhibit.timer`(工作日 09:25 持锁至 15:05,保盘中 14:40 轮动/实时行情,2026-09-16 用户授权,0915 白日停机致三影子全缺的事故沉淀);Persistent 支持唤醒后补拉,周末/过窗自检退出;抑制类型含 sleep:idle+handle-lid-switch(2026-09-16 用户授权合盖纳入窗口保护;GNOME 自管合盖场景需实测,当前外接屏下 gsd-power 本就持锁);人为显式关机/挂起不拦;节假日误持锁无害;

**复盘视频子系统**(独立):`recap/`(每晚NBA解说式热点复盘短视频:选片→剧本→录制单→人工手机App盘口回放录屏→原料包,CLI: python -m davis_analyzer.recap {run|select|script|sheet|audio|post|status})。选片=「节目效果分」非投资分,权重单一真相源 constants.py RECAP_DRAMA_WEIGHTS(2026-09-18 spec);数字全锚 facts(cardgen Fact 复用+unmatched_tokens 机器闸),解说=资讯复盘不荐股、末段必含「不构成投资建议」、敏感词=cardgen 词表+recap 附加词(不复用 cardgen 第二人称正则,解说文体允许「你看」);素材文件名协议 `{YYYYMMDD}_{ts_code}_{NN}.mp4` 一票一文件丢 recap/inbox/{day}/;**调度**:user systemd timer recap-run 工作日19:40(select→script→sheet→飞书推单,发布永远人工);**跳过某晚自动 run 的正确姿势:停 timer 后须等过次日再恢复——当晚恢复会被 Persistent=true 判定「错过的 19:40」立即补跑,2026-09-18 20:30 恢复时实锤触发补跑覆盖定制 episode(靠急停 service 止损)**;台账号 market_data.db recap_episodes(模式B自管表)。娱乐版(2026-09-18 v2「五佳球模式」):BGM 人声闪避(自备 mp3 丢 recap/assets/bgm/ 优先,缺省合成节拍)+SFX+段首排名冲击卡+五佳倒计时横幅+REPLAY 角标,字幕 ASS 烧录;成片=episodes/{day}/final/。设计spec见 docs/superpowers/specs/2026-09-18-recap-video-design.md,实施计划 docs/superpowers/plans/2026-09-18-recap-phase1.md。

**输出层**:`report_generator.py` + `templates.py`(模板化研报,无 LLM);`checklist_generator.py` + `rescorer.py`(深度调研清单循环,人工定性调整)。

**配置与类型**:`config.py`(路径/token)、`constants.py`(评分权重与阈值,单一真相源)、`types.py`(7 个纯数据 dataclass)。

## rtk 使用规范(节省 token)

本项目已本地部署 **rtk**(CLI 代理,压缩命令输出)。**按任务类型区分使用**,与根目录 `AGENTS.md` 口径一致:

**用 rtk**(工程类,输出"扫一眼找信息"):

- git 操作:`rtk git status` / `rtk git log` / `rtk git diff`
- 目录/搜索:`rtk ls` / `rtk find ...` / `rtk grep ...` / `rtk rg ...`
- 测试:`rtk pytest`(只看失败摘要)
- 依赖安装:`rtk pip install -e .`
- 读大文件扫信息:`rtk read <file>`

**用原生命令**(研报/取数类,输出要"精读消化"):

- davis_analyzer 引擎取数脚本(完整 JSON 进研报,压缩会丢数字)
- tushare/stockhot 数据库查询输出
- 研报模板、财务表格、checklist 等需完整读取的内容

**无需 rtk**:短命令(`mkdir`/`mv`/`echo`)、修改系统状态的命令(`rm`/`git commit`)。

**判定原则**:输出要精读消化 → 原生命令;扫一眼找信息 → rtk。不确定时用原生命令并加 `| head -50` 截断(原生命令永远准确,rtk 只是优化层)。

## 代码约定

### Python 风格

- **命名**:`snake_case`(函数/变量)、`PascalCase`(类)、`_camelCase`(私有助手)。**带完整类型注解**,返回类型尤其严格(`-> float` / `-> DavisDoubleScore`)。
- **日志**:统一用 **`loguru`**,不用 stdlib `logging`。`print()` 只允许在 `cli.py`(用户可见 CLI 输出)和迁移脚本里出现。
- **数据结构**:纯数据用 `@dataclass`(`types.py` 里的 7 个,以及 `BacktestConfig`/`BacktestResult`/`PerformanceStats`)。
- **Docstring/注释风格**:docstring 英文,金融领域术语用中文(景气度/困境反转/合同负债)。模块分隔用框线注释 `# ── ... ──`。
- **测试**:用 `pytest`,`tests/conftest.py` 提供 DataFrame fixture(`sample_income_df` 等)和 `MagicMock` 的 `mock_client`。

### 金融领域铁律

- **金额/价格计算用 `decimal`**,不用 `float`(量化场景下浮点误差会累积成实盘事故)。
- **回测日历**:从锚定股票 `000001.SH`(上证指数)的缓存日线推导,**不调专用交易日历 API**。如果锚定股票在回测窗口的缓存不全,日历会静默缩水。
- **权重单一真相源**:`constants.py` 里的 `PROSPERITY_WEIGHTS`、`DAVIS_DOUBLE_WEIGHTS` 是评分权重的唯一权威。`SOP.md` 声称权威但实际以代码为准——`tests/test_doc_consistency.py` 在校验两者一致性。**改动权重务必两边同步**。
- **可变全局字典**:`constants.py` 的权重是模块级 mutable dict,`scoring.py` 按引用读取。**别在运行时修改它**,会静默改变评分行为。

## 视觉任务规范(2026-08-29 固化)

凡需要「看图」的任务——卡片目检、截图诊断、UI 元素描述、图片内容分析——**一律调用 `scripts/content_publisher/vision.py`**(glm-5.3-flash,配置走根目录 .env 的 `LLM_API_KEY/LLM_BASE_URL`,模型可用 `LLM_VISION_MODEL` 覆盖),要求返回结构化 JSON,主模型只消费结论,不直接目检图片。例外:精度要求高于 ±20px 的关键动作(如发布按钮点击)**不得单独依赖视觉模型**,必须用确定性检测优先(publisher._find_red_button 的 PIL 颜色游程先例)。依据:docs/方法论/小红书金融卡片生产方法论_2026-08-29.md §四。

## 浏览器任务规范(2026-09-16 固化)

凡经 Browser Use(ZCode 内置浏览器 IAB)执行的任务——发布操作、页面验证、网页抓取——**收尾必须管标签页**:任务结束时以 `await browser.tabs.list()` 为准,把本任务创建的所有标签页逐个显式 `await tab.close()`。背景与依据(2026-09-16 调研实测):IAB 标签页生命周期 = ZCode 进程存活期,turn 结束(`turnEnded`)与**会话关闭(`closeSession`)都不关标签页**;ZCode 桌面为长驻 Electron 应用,每个未关标签页常驻一个 renderer 进程(实测空页 ~90MB,业务页 100-300MB),不活跃页换入 swap 后不再换回,内存/swap 单调上涨直到进程退出(上启动周期 8/25~9/16 即此病,9/14 内核 swap_reclaim 连续告警)。注意:`browser.tabs.finalize({ keep })` 只做标记,**keep 不等于关闭、unlisted 页也不会自动关**;需保留给用户看的结果页用 `markDeliverable`,确需跨 turn 续用才 `markHandoff`,其余一律关。补救:标签堆积后完全退出 ZCode 应用即可释放,无需重启整机。

## 配置与运行

- **Python 解释器**:统一用父仓库根目录的 `.venv/bin/python`(系统 `python` 不存在、`python3` 缺 pandas,直接调用必然报错)。
- **Token**:`TUSHARE_TOKEN` 环境变量(从父仓库根目录 `.env` 读)。
- **输出位置**:研报写入 `STUDIES_DIR`(`davis_analyzer/studies/`),文件名 `{rank}_{ts_code}_{name}_深度研报.md`。回测结果导出为 CSV(交易明细 + 权益曲线)。
- **入口**:
  - 主程序:`python -m davis_analyzer {run|deep-research|rescore}`
  - 模拟交易:`python -m davis_analyzer.paper_trading {init|run|backfill|report|list}`

## 长回测运行规范(硬性,2026-08-20 事故沉淀)

单次预计超 30 分钟的回测/A/B(五年全期 ≈2.5h/变体;短窗口按 ~6-12 秒/交易日折算,窗口越靠后数据越密越慢)一律按以下执行。背景事故:0003 首跑用 `run_in_background` 启动,会话关闭进程被连带杀掉,死在 trial 4,前 3 个 trial 结果一并丢失。

1. **脱离会话启动**:
   ```
   cd /home/leo/Projects/CodeAgentDashboard && setsid nohup .venv/bin/python scripts/abx/xxx.py > logs/xxx_run.log 2>&1 &
   ```
   启动后必须验证脱离:`ps -o pid,ppid,pgid,sid -p <PID>`,**SID=PGID=自身**才算安全(仍在原会话组=没脱离)。`run_in_background` 只用于会话存续期内能收尾的短任务(烟测/单段回测)。
2. **逐段落盘**:结果 JSON 逐 trial/逐变体完成即 dump,禁止跑完一次性写——中断可保住已完成部分。账户按变体命名且脚本入口 reset,重跑自动覆盖,无需手动清理。
3. **启动即排收尾**:按估算耗时(偏保守 +30min 余量)立刻设一次性 cron 收结果/填实验日志(`docs/回测记录/实验日志/`);cron prompt 写明三分支:完成→分析+归档+commit/push;未完→只报进度;进程死→报死亡位置,**不自动重跑**(等人工决定)。
4. **中途不改依赖**:运行期间不改动其 import 的脚本、constants 权重、DB schema;确需改动等跑完。
5. **进度检查**:`tail -5 logs/xxx_run.log` 找 trial 标记行,或查 DB 账户 nav 最新日期(`paper_accounts` 按前缀过滤)。

## 协作流程

- **不擅自扩大范围**:严格按现有 pipeline 步骤实施,新增因子先讨论再落地。
- **动权重前先读 SOP**:`SOP.md` + `constants.py` 必须同步。
- **提交规范**(如启用 git):Conventional Commits 中文 scope,如 `feat(backtest): 实现周频再平衡主循环`。

## 已知技术债(可清理但别复现)

- `run_output.log` 是 4.9MB 的提交进 git 的日志,应加 `.gitignore`。
- `cli.py` 的 `_DEFAULT_CHECKLIST_DIR` 是相对路径,依赖调用方工作目录——改它要小心。
- README 声称"Python 3.12+",但 `pyproject.toml` 目标是 `py311` / `requires-python >= 3.11`。以 pyproject 为准。
