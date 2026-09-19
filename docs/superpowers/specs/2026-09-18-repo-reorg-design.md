# 仓库物理重组设计(2026-09-18)

> 用户拍板:全范围整理、彻底物理重组、davis_analyzer 全包分层、三项顶层归档、docs 激进全重分、周六(2026-09-19)执行。
> 本 spec 是重组的唯一设计真相源;实施计划另出(plans/)。

## 一、背景与动机

仓库经数月生长,顶层 10+ 单元、`davis_analyzer/` 根平铺 30 个引擎 py + 12 个子系统目录、`scripts/` 研究脚本无分类堆积、`docs/` 16 个分类目录口径不一。定位与导航成本随子系统(recap/长图卡/板块热点复盘等近期新增)持续上升。

取证事实(2026-09-18 首扫,**2026-09-19 执行日复核更新**):

- 包外 98 个 py 文件 import `davis_analyzer.*`(复核口径:stockhot/scripts/tests/studies,不含待归档的 dashboard/webui)。
- systemd user units 16 路仓库内引用:3 个模块 CLI(recap/**surge(9-18 晚新部署,19:30 timer)**/thermometer)+ 11 个 scripts 路径 + content_publisher 4 个(不动);`davis-webui-backend.service` 仍 active、frontend inactive——归档前两者都要 stop+disable。
- `docs/小红书卡片` 被 4 个脚本 + cardgen 代码引用;两个支持 `CARDGEN_PROJECT_ROOT` 环境变量覆盖。
- 顶层闲置:dashboard/src/test-results(5月起)、davis_webui(7月起)、public(5月起);studies 8月仍有活动,保留。
- 执行窗口约束:周六无定时任务;**周日 08:00 thermometer-universe.timer** 是重组后首个触发的 timer,所有引用必须在周六内修复并 daemon-reload。
- 复核新增:①run_output.log 技术债已不存在(git 不再跟踪、文件已删,AGENTS.md 旧叙述重写时清除);②工作区积压 61 条(日报类未跟踪报告、docs_audio/.gitkeep 删除、storage 诊断图与 browser_profile_xhs/ 运行时产物——后者走 .gitignore 不提交);③`scripts/replay_rotation_close.py` 被 systemd 引用但**尚未跟踪**,归零提交时必须入库。

## 二、目标布局

### 2.1 仓库顶层

```
CodeAgentDashboard/
├── stockhot/            (不动)
├── davis_analyzer/      (全包分层,见 2.2)
├── scripts/             (分组,见 2.3)
├── docs/                (激进重分,见 2.4)
├── studies/             (保留,补 README 说明口径)
├── storage/  tests/     (不动)
├── archive/             ★ 新建归档区
│   ├── dashboard/  src/  test-results/   ← 2026-05 起闲置
│   ├── davis_webui/                       ← 2026-07 起闲置
│   ├── public/                            ← 早期静态前端
│   └── README.md   (归档原因+日期+复活方法=git mv 回原位)
└── 根清理:.coverage 与根 __pycache__ 删除;run_output.log(4.9MB 技术债)移除并
    gitignore;data/ 空壳删除;docker-compose.davis.yml 与隧道脚本
    (start/stop/verify-davis-quick-tunnel.sh)随 webui 归档;.env.davis 同行。
```

davis_webui 处置顺序:`systemctl --user stop + disable davis-webui-backend davis-webui-frontend` → git mv 进 archive/ → 删除对应 unit 文件 → daemon-reload。

### 2.2 davis_analyzer 全包分层

```
davis_analyzer/
├── __init__.py  __main__.py  cli.py    (入口留根,`python -m davis_analyzer` 形式不变)
├── core/
│   ├── config.py  constants.py  types.py
│   ├── tushare_client.py  financial_fetcher.py  stock_universe.py
│   └── pipeline.py  scoring.py  price_estimator.py
├── factors/
│   ├── valuation.py  valuation_forward.py
│   ├── prosperity.py  prosperity_sector.py  prosperity_inflection.py
│   ├── momentum.py  trend.py  distress.py  dividend.py
│   ├── forecast.py  profitability.py  holder_concentration.py
│   ├── quality_factor.py  sub_industry.py
│   ├── international_overlay.py  market_regime.py
│   └── sector_pipeline.py  strategy_signal.py  cyclical.py
├── report/
│   ├── templates.py  report_generator.py
│   └── checklist_generator.py  rescorer.py
├── backtest/
│   └── backtest.py  backtest_factors.py  backtest_report.py
├── systems/               ★ 九个子系统 + README.md 总导航(各子系统一句话+CLI+关键表)
│   ├── limitup/  tournament/  thermometer/  intraday/
│   ├── cardgen/  recap/  surge/  paper_trading/  metrics/
├── migrations/
│   └── migrate_cache.py  migrate_to_market_db.py
└── tests/  cache/  logs/  studies/  config/   (支撑目录原地)
```

import 策略:**不留兼容 shim**,全局机械改写,例:
- `from davis_analyzer.valuation import X` → `from davis_analyzer.factors.valuation import X`
- `from davis_analyzer.thermometer import X` → `from davis_analyzer.systems.thermometer import X`

覆盖面:包内互引、包内 tests、根 conftest.py、包外 108 文件(scripts/studies/tests/dashboard/webui 中活着的)。

CLI 变化:子系统统一变为 `python -m davis_analyzer.systems.<name>`,同步修 systemd unit 的 ExecStart(3 个模块 CLI:recap/surge/thermometer)与 cron prompts。**历史 spec/回测记录/实验日志不改**(历史真相);AGENTS.md(两份)与 docs/代码库索引.md 重写(现行真相)。

### 2.3 scripts/ 分组

```
scripts/
├── ops/            ★ 长期运维(timer 引用的全进这里)
│   ├── daily_market_cards.py  daily_bulletin.py  daily_longpic.py
│   ├── chase_shadow_daily.py  distress_signal_export.py  g2_signal_export.py
│   ├── replay_rotation_close.py  pool_digest.py
│   ├── after_hours_inhibit.sh  unlock_calendar.py  wait_and_backfill.sh
│   └── longpic_numbers_check.py  publish_reconcile.py   (散文件归位)
├── card_factory/       (不动 — AGENTS 纪律 + 被 cardgen builder 引用)
├── content_publisher/  (不动 — 同上 + 自有 timer 生态)
├── backfill/  diag/  tools/      (保留原名;research_search.py → tools/)
├── abx/                        (negative_factor_abx.py 并入)
├── research/           ★ 六个研究项目原名平移(实验日志引用原名,不缩写)
│   ├── washout_research/  promotion_research/  tech_research/
│   └── trend_exit_research/  event_research/  intraday_research/
└── archive/  (存量清点,死脚本归此)
```

systemd ExecStart 同步:distress-signal-export / g2-signal-export / chase-shadow / daily-longpic / drift-sentinel(abx 路径不变)等。

### 2.4 docs/ 激进全重分

```
docs/
├── superpowers/          (保留原位 — 工具链默认约定路径,specs/plans/prompts 互引密集)
├── 代码库索引.md  README.md   (根索引,重写)
├── 研报/
│   ├── 个股/             ← 个股研报/
│   ├── 产业链/           ← 产业链研报/
│   └── 方法论/           ← 方法论/
├── 复盘/
│   ├── 盘前/             ← 盘前整理/
│   ├── 盘后/             ← 盘后复盘/ + 盘后总结/ 合并
├── 回测记录/             (原位 — 实验日志约定与 AGENTS 互锚)
├── 发布/
│   ├── 小红书/           ← 小红书卡片/  (未发布/已发布/废稿 三态结构原样)
│   ├── 长图/             ← 长图/       (已核实:8/31 AI漫剧长图 html+png 发布素材)
│   └── 雪球/             ← 雪球发布/
├── 开发/                 ← 开发记录/ + 分析笔记/ + 其他/ + 根散文件
└── (paper_trading_forward_live_requirements.md → 开发/)
```

**代码硬编码同步清单(激进模式的代价,全部要改)**:

| 引用点 | 改法 |
|---|---|
| `davis_analyzer/systems/cardgen/cli.py` 默认 `REPO_ROOT/"docs"/"小红书卡片"` | → `docs/发布/小红书` |
| `scripts/ops/daily_market_cards.py` 同上默认 | 同 |
| `scripts/ops/daily_longpic.py` `CARDS_ROOT` | 同 |
| `scripts/ops/daily_bulletin.py` `--out` 默认 | 同 |
| `scripts/ops/longpic_numbers_check.py` `BASE` | 同 |
| `cardgen/publish_sync.py` `_MARKER="小红书卡片"` 与 source 路径解析 | 改为「发布/小红书」并**兼容历史 publish_queue 行**(旧路径行按旧树解析,幂等不炸) |
| `.agents/skills/*/SKILL.md` 硬编码输出目录(盘前/盘后/研报等) | 逐一同步 |
| AGENTS.md(根 + davis_analyzer)全部路径叙事 | 重写 |

新约定:`docs/发布/小红书` 三态结构(未发布/已发布/废稿)与 cardgen sync/enqueue 纪律**原样平移**,行为不变,只换根路径。

## 三、执行策略

- **窗口**:2026-09-19(周六)白天,当日完成;周日 08:00 thermometer-universe 前必须收尾(否则先 `systemctl --user disable thermometer-universe.timer` 兜底)。
- **前置**:工作区未提交改动先全部归零(当前 git status 有积压,周六开工第一步处理);davis_webui 服务 stop+disable。
- **分 6 个 commit,每步全量 pytest 绿后才进下一步**:
  1. `chore(reorg): 顶层归档+根清理` — archive/ 建立、webui 服务停用、根杂物清理
  2. `refactor(davis): core+factors 分层` — git mv + import 改写 + 测试
  3. `refactor(davis): report/backtest/migrations 分层`
  4. `refactor(davis): systems 子系统归拢` + systemd unit ExecStart 更新 + daemon-reload
  5. `chore(scripts): ops/research 分组` + 相关 unit 路径更新
  6. `chore(docs): 激进重分 + 代码路径同步 + AGENTS/索引重写`
- **门禁**:①全量 pytest(根 tests + davis_analyzer/tests)②各子系统 CLI status 类子命令抽查 ③`systemctl --user daemon-reload && systemctl --user list-timers` 逐条核对路径 ④cardgen 走一遍 init→ingest→validate 干跑验证新路径。
- **回滚**:每 phase 独立 commit,可单独 revert;git mv 保留历史追踪。

## 四、风险与对策

| 风险 | 对策 |
|---|---|
| import 改写漏网(动态 import/字符串引用) | 全局 grep `davis_analyzer\.` 复核 + pytest 全量 + CLI 抽查 |
| publish_queue 历史行旧路径 | publish_sync 兼容双树解析,单测锁定 |
| cron prompts / skills 文档漏改 | 移动前逐目录 grep 审计形成清单,计划阶段逐项打勾 |
| 周日 timer 修复不完整 | 周六收尾 checklist 显式列 daemon-reload + list-timers 核对 |
| 长回测/夜间任务撞车 | 周六执行避开;执行前 `list-timers` 确认无当日残余 |

## 五、明确不做

- 不改 stockhot/ 内部结构(活跃且自有 AGENTS 叙事)。
- 不改 storage/ 数据库与台账(运行时数据)。
- 不重写历史 spec/回测记录/实验日志中的旧路径(历史真相)。
- 不动 card_factory / content_publisher 两个受保护目录的内容,只可能在其外层换父路径(本设计:不动)。
- 不删任何归档内容(archive/ 全保留,git 历史可溯)。
