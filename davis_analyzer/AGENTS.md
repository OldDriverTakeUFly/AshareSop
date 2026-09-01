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

**回测子系统**(相对独立):`backtest.py`(周期再平衡主循环) → `backtest_factors.py`(横截面因子评分) → `backtest_report.py`(收益/夏普/回撤 + CSV 导出)。

**日内做T研究子系统**(独立沙盒):`intraday/`(db 独立研究库 + backfill baostock 分钟线断点回补 → engine 闭环回转引擎[T+1 卖出池/次bar成交/涨跌停拒单/收盘竞价] → features 因果特征 → strategies 朴素+增强策略族 → report 对账与汇总 → paper_shadow 模拟盘影子验证[cron 19:55 盘后回放,真实底仓,台账 intraday_shadow_trade/run + 数据增强三表:universe 每日全宇宙特征快照与状态分类(含 near_miss/被过滤样本,防静默排除)/exit_alt 收盘竞价退出反事实与 MAE/mkt 市场环境 regime],CLI: python -m davis_analyzer.intraday {backfill|status|verify|run|shadow|shadow-report|shadow-enrich})。注意:回补按月块记账,当月数据未收盘不完整——增量更新需先删当月 backfill_chunk 记录再重跑,**每月 1 日 cron 例行删上月块+重跑 backfill(宇宙=当前持仓,自动扩容,防新持仓 vol_ratio1=None 被静默过滤)**;影子验证依赖 19:20 daily_refresh 先行,pre_close 自行推导不依赖当日日线完整性;shadow-enrich 只回填快照类台账,不动成交台账(历史底仓不可重建,shares 列为当前快照口径);**历史日补跑前必须核对 daily_price 已含该日与前一日两日行(当日 19:20 daily_refresh 之后),否则 pre_close 用陈旧锚——8/19 台账 10 笔中 6 笔即此病(见 docs/回测记录/做T影子验证数据增强与首期数据体检_2026-08-28.md §四)**。结论见 docs/回测记录/日内做T引擎首测_2026-08-19.md、做T隔夜退出校验_2026-08-25.md。

**金融卡片生成子系统**(独立):`cardgen/`(facts 事实清单溯源 → $fact 物化 → validator 四道机器闸[数字全量核对/合规敏感词+免责/完整性/事实自检] → builder 渲染[复用 scripts/card_factory]→ RELEASE.json 发布包,台账 storage/database/content_cards.db,CLI: python -m davis_analyzer.cardgen {init|ingest|validate|build|status|enqueue|sync})。**双文件夹约定(2026-09-01)**:工程按发布状态归档于 `docs/小红书卡片/未发布/` 与 `已发布/`(init 建在未发布/,根目录不再放新工程,存量工程可解析);发布成功(执行 `queue.py mark <id> published`)后必须跑 `python -m davis_analyzer.cardgen sync` 把对应工程挪入已发布/——sync 只读消费 publisher 的 publish_queue 表(不改 content_publisher 代码),幂等可重复;`build --bump` 会把已发布工程自动挪回未发布/(待重发)。**agent 纪律**:①卡片上任何数字必须登记 facts.json 且带来源锚点(研报#章节/Tushare查询指纹),叙事观点必须能指回研报章节;②已 rendered 版本变更须 --bump --reason,过期(expires_at)卡片禁止 enqueue;③不得修改 scripts/card_factory 与 scripts/content_publisher(对接只读;配图经 builder 后处理注入,不改 card_factory);④配图纪律(2026-08-30):只用公有领域/CC授权图(人物=美国政府官方照,实物=Wikimedia Commons 并 API 核实授权),image 字段 src/license/credit 三必填且 license 文本禁带数字,布局按「密集卡=corner/留白卡=底部/封面不放」选,详见方法论§8。设计 spec:docs/superpowers/specs/2026-08-28-cardgen-design.md

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
