# 双仓拆分实施计划(2026-09-19 当日执行)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。按 Task 顺序执行,每步验证。
> Spec: docs/superpowers/specs/2026-09-19-repo-split-design.md

## Global Constraints

- 一切从 `/home/leo/Projects/CodeAgentDashboard` 执行(改名前的旧仓),Python 用 `.venv/bin/python`。
- **Task 0 必须最先 disable thermometer-universe.timer**,收口(Task 6)后才 enable。
- 新仓冷启动:`git init` 首 commit=快照;旧仓 `git rm` 迁出内容(历史保留)。
- 两仓门禁:各自全量 pytest 0 failed;systemd ExecStart 目标存在性全过。
- gh 未登录:新仓只建本地,推送留待用户。

---

### Task 0: 前置安全

- [ ] `systemctl --user disable thermometer-universe.timer` + 确认 `git status` 干净(基线 2147p/0f 已锚定)
- [ ] 记录 systemd unit 当前快照(`grep -h ExecStart ~/.config/systemd/user/*.service | sort > /tmp/units_before.txt`)

### Task 1: workspace 数据真身 + symlink 切换

- [ ] `mkdir -p ~/Projects/.ashare-data/storage` ;**mv(非 cp)** `storage/database storage/files storage/publish_prep storage/video_render_profile storage/xiaohongshu storage/browser_profile_xhs storage/sector_rv_cache` → `.ashare-data/storage/` 对应名
- [ ] 旧仓建 symlink:`ln -s ~/Projects/.ashare-data/storage/database storage/database`(files/publish_prep/video_render_profile/xiaohongshu/browser_profile_xhs/sector_rv_cache 同法)
- [ ] 验证:`.venv/bin/python -c "from stockhot.core.config import DB_PATH; print(DB_PATH, DB_PATH.exists())"`;cardgen/surge/thermometer 三 status
- [ ] commit(旧仓;symlink 不入库也无妨,`.gitignore` 已忽略 storage 子树则 status 干净)

### Task 2: 新仓 davis-analyzer 建立

- [ ] `mkdir ~/Projects/davis-analyzer && cd ~/Projects/davis-analyzer && git init`
- [ ] 复制内容集(用 `cp -a` 保时间戳):`davis_analyzer/`、`studies/`、`conftest.py`、
      `scripts/{ops,research,abx,backfill,diag,tools,card_factory,content_publisher}` + `scripts/ops` 下的 davis 11 脚本
      (daily_market_cards/daily_bulletin/daily_longpic/longpic_numbers_check/publish_reconcile/pool_digest/chase_shadow_daily/distress_signal_export/g2_signal_export/replay_rotation_close/unlock_calendar.py)
      (旧仓 scripts/ops 只留 after_hours_inhibit.sh、wait_and_backfill.sh)
- [ ] 复制 `docs/{研报,复盘,发布,回测记录,开发,superpowers,README.md,代码库索引.md}`、
      `.agents/skills/{research-report,industry-prosperity,multi-factor-screening,valuation-loss-making-targets,invest-sop-pre-market,local-development-environment}` + `data-source-convention.md`
- [ ] 复制根 `tests/test_price_estimator.py`(若 import davis)、`tests/test_sync_screen_to_watchlist.py`(归属查后放);davis_analyzer/tests 已随包
- [ ] 写新仓 `pyproject.toml`(name=davis-analyzer,py311,deps=[])+ `.gitignore`(从旧仓裁剪:保 Python/DB/logs/storage 条目)+ `.env`(从旧仓拷贝,键不变)
- [ ] `python -m venv .venv && .venv/bin/pip install -e ../AshareSop && .venv/bin/pip install -e . && .venv/bin/pip install pytest`
- [ ] **storage symlink 同 Task 1**(新仓指同一真身)
- [ ] 新仓全量测试:`.venv/bin/python -m pytest -q` → 0 failed(davis 全集基线 2147-stockhot 部分)
- [ ] 首 commit:`feat: davis-analyzer 独立建仓(自 AshareSop monorepo 拆分,快照 2026-09-19)`

### Task 3: 旧仓瘦身 + 反噬修复

- [ ] 反噬修复(先于删除,保持旧仓可测):逐个看 stockhot 内 10 处 davis import
      (advisor/data_sources/fundamental.py、invest_sop/scripts/{intraday_holdings_alert,intraday_rotation,run_daily_scan}.py、advisor/tests×4 等),按原则本地化:复制所需小函数;advisor tests 引 davis 的改为 skip+标记或复制 fixture
- [ ] `git rm -r davis_analyzer studies scripts/{research,abx,backfill,diag,tools,card_factory,content_publisher}` + scripts/ops 的 davis 11 脚本 + `docs/{研报,复盘,发布,回测记录,开发,superpowers}` + `.agents/skills` davis 5 个 + `tests/test_price_estimator.py` 等已迁项
- [ ] `docs/复盘/盘后/` 旧仓重建空目录+.gitkeep;`pyproject.toml` testpaths 改 `["stockhot/tests", "tests", ...]`(去 davis)
- [ ] 旧仓测试:`tests/ + stockhot/` 全量 → 0 failed
- [ ] commit:`refactor!: davis-analyzer 拆出独立仓(内容迁 /home/leo/Projects/davis-analyzer),反噬 import 本地化`

### Task 4: 文件夹改名 + 绝对路径修复

- [ ] `cd ~/Projects && mv CodeAgentDashboard AshareSop`
- [ ] 全两仓 grep `/home/leo/Projects/CodeAgentDashboard` → sed 改 `/home/leo/Projects/AshareSop`(stockhot 侧活代码 invest_sop scripts×2 等);davis 仓内绝对路径同理核对
- [ ] 旧仓 `.venv` 重建:`rm -rf .venv && python3 -m venv .venv && .venv/bin/pip install -e . && pytest 依赖` → 测试复验绿

### Task 5: systemd 全量分家

- [ ] davis 17 路 unit:WorkingDirectory+ExecStart 的仓路径改 `/home/leo/Projects/davis-analyzer`(thermometer-run/thermometer-universe/thermometer-card/surge-run/recap-run/screener-card/rotation-close-replay/radar-feishu/chase-shadow/g2-signal-export/distress-signal-export/daily-longpic/xhs-ai-longpic-push/prep-push/publish-confirm/publish-due/publisher-board/drift-sentinel)
- [ ] stockhot 2 路(after-hours-inhibit/trading-hours-inhibit)路径改 `/home/leo/Projects/AshareSop`
- [ ] `daemon-reload` + ExecStart 目标存在性全查(绝对+相对)+ `list-timers` 快照对比
- [ ] `systemctl --user enable thermometer-universe.timer`(收口恢复)

### Task 6: 文档真相源重写 + 终验

- [ ] 两仓 AGENTS.md 重写(拓扑:双层双仓+数据真身+互链);README×2;代码库索引重写;cron prompts 路径核对
- [ ] 终验:两仓全量测试绿 + CLI 抽查(旧仓:stockhot advisor/due;新仓:thermometer/surge/cardgen/limitup status)+ timer 核对
- [ ] 收尾报告(含:GitHub 推送待用户、.zcode 记忆路径提醒)

---

## 中断与回滚

- Task 1 用 mv+symlink:回滚=删 symlink、mv 回目录。
- Task 2 新仓独立,随时可弃。
- Task 3 单 commit,`git revert` 可整体回退(反噬修复随行)。
- Task 4 改名:回滚=mv 回;Task 5 unit:回滚=改回+reload。
