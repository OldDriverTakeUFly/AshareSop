# 双仓拆分设计(2026-09-19)

> 用户拍板:真拆双仓、新仓冷启动(旧仓保完整历史)、共享 SQLite 提 workspace 级、docs/skills/scripts 按生产者分家。当日执行。

## 一、目标布局

```
/home/leo/Projects/
├── AshareSop/               ← 仓1(现 CodeAgentDashboard 文件夹改名,git 历史不变,remote 不变)
│   ├── stockhot/               基础包(core/data_layer/valuation/eod_review/advisor/invest_sop/...)
│   ├── scripts/                after_hours_inhibit.sh、wait_and_backfill.sh(+ops 空壳)
│   ├── docs/                   复盘/盘后(eod_review/after-hours-review 未来产物落这里)+ README
│   ├── .agents/skills/         daily-market-scan、after-hours-review、data-source-convention.md、
│   │                           local-development-environment(通用,两仓同持)
│   ├── tests/                  test_macro_trends 等.stockhot 根测试 + integration JS 产物入 archive
│   └── pyproject.toml          name=stockhot(不变),testpaths 收窄
├── davis-analyzer/          ← 仓2(新建,git init 冷启动,首 commit=快照)
│   ├── davis_analyzer/         整包平移(core/factors/report/backtest/systems/migrations)
│   ├── scripts/                ops(davis 链 11 个)+ research/ + abx/ + backfill/ + diag/
│   │                           + tools/ + card_factory/ + content_publisher/
│   ├── docs/                   研报/复盘(全部现存)/发布/回测记录/开发/superpowers/README/代码库索引
│   ├── .agents/skills/         research-report、industry-prosperity、multi-factor-screening、
│   │                           valuation-loss-making-targets、invest-sop-pre-market(+通用两份)
│   ├── tests/                  davis_analyzer/tests 全量 + 根 tests 中 davis 部分 + conftest.py
│   ├── studies/
│   └── pyproject.toml          name=davis-analyzer;开发装法=pip install -e ../AshareSop 再 -e .
└── .ashare-data/            ← workspace 级运行时数据真身(不进任何 git)
    └── storage/database/*.db + files/ + publish_prep/ + video_render_profile/ + xiaohongshu/
        + browser_profile_xhs/ + sector_rv_cache/
```

## 二、关键机制

1. **import 零改动**:davis→stockhot 141 文件依赖靠「stockhot 以 editable 装进 davis venv」解决,`from stockhot.xxx` 照常;新仓 pyproject `dependencies=[]`,安装顺序文档化。
2. **DB 共享用 symlink,不改一行业务代码**:真身在 `~/Projects/.ashare-data/storage/`,两仓 `storage/database|files|publish_prep/...` 为指向真身的 symlink。stockhot `core/config.py` 的 PROJECT_ROOT 推算与 davis cardgen 的 REPO_ROOT 推算全部保持原逻辑,经 symlink 落到同一份 DB。
3. **反噬修复原则**:stockhot→davis 的 10 处功能性 import(advisor 引 paper_trading.account×4 / core.types×3 / strategy×1;invest_sop 脚本引 strategy_signal/tushare_client/pipeline)一律改为本地复制小函数或经 DB 中转,**旧仓禁止 import davis**(无循环依赖)。docstring/注释性提及可留。
4. **systemd 分家**:davis 侧 unit(thermometer×3/surge/recap/screener-card/rotation-close-replay/radar-feishu/chase-shadow/g2/distress/daily-longpic/thermometer-card/xhs-ai-longpic-push/prep-push/publish-confirm/publish-due/publisher-board/drift-sentinel)WorkingDirectory+ExecStart 改 davis-analyzer 新路径;stockhot 侧(after-hours-inhibit/trading-hours-inhibit)改 AshareSop 新路径。全部 daemon-reload + 目标存在性核查。
5. **窗口纪律**:开工即 `systemctl --user disable thermometer-universe.timer`(周日 08:00 硬边界),收口验证后 enable。
6. **GitHub**:旧仓 remote 不变;新仓今日本地建仓(gh 未登录),推送待用户 `gh auth login` 后执行。

## 三、门禁

- 两仓各自全量 pytest 0 failed(基线:合并仓 2147 passed / 37 xfailed)。
- CLI 抽查:两仓各抽 4 个子系统/模块 status。
- systemd:全量 ExecStart 目标存在性 + list-timers 无 MISSING。
- DB:迁移后 cardgen status / surge status / thermometer status 三读验证。

## 四、明确不做

- 不迁 git 历史(filter-repo);不删旧仓任何历史。
- 不改 davis→stockhot 的 141 处 import。
- 不在今日配置 GitHub CI(两仓 workflow 留待后续)。
- 旧仓 .zcode 会话记忆绑定路径变化:提醒用户重启会话后定位新目录。
