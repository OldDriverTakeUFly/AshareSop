# 仓库物理重组实施计划(2026-09-19 周六执行)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 [spec](../specs/2026-09-18-repo-reorg-design.md) 完成仓库物理重组:顶层归档、davis_analyzer 全包分层、scripts 分组、docs 激进重分,同步修复全部引用。

**Architecture:** 纯位置重组零行为变更。7 个 Task 顺序执行、各自成 commit、每步过 pytest 门禁。git mv 保历史,不留兼容 shim。

**Tech Stack:** git / bash / systemd user units / SQLite(publish_queue 与 content_cards 台账一次性路径 UPDATE)/ pytest

## Global Constraints(每个 Task 隐含遵守)

- 一切命令从 `/home/leo/Projects/CodeAgentDashboard` 执行,Python 一律 `.venv/bin/python`。
- **pytest 门禁**:`.venv/bin/python -m pytest tests/ davis_analyzer/tests -q` 结果不得差于基线 **5 failed / 1527 passed**(5 个失败全部位于 `davis_analyzer/tests/test_publisher_m3.py::TestCli`,系 8/30 小红书判定账号自动化后 publisher CLI 加安全横幅的既有失败,与重组无关)。
- 不改 `scripts/card_factory/`、`scripts/content_publisher/` 内容(只读纪律)。
- 历史 spec/plans/回测记录/实验日志中的旧路径**不改**(历史真相);改的是:代码、systemd unit、cron prompts(`docs/superpowers/prompts/`)、`.agents/skills/*/SKILL.md`、两份 AGENTS.md、`docs/README.md`、`docs/代码库索引.md`。
- `archive/`、`.venv/`、`node_modules/`、`.git/`、`docs/superpowers/`、`docs/回测记录/` 不参与 import 重写。
- **周日 08:00 硬边界**:`thermometer-universe.timer` 是重组后首个触发 timer。Task 4 必须在周六完成;若中断,先 `systemctl --user disable thermometer-universe.timer` 兜底。
- 运行时数据库(`storage/database/*.db`)只做 Task 6 规定的路径 UPDATE,不做其他任何改动。

---

### Task 0: 工作区归零 + 基线锚定

**Files:**
- Modify: `.gitignore`
- Commit 全部积压(61 条:日报类未跟踪报告、`docs_audio/.gitkeep` 删除、`scripts/replay_rotation_close.py` 未跟踪等)

**Interfaces:**
- Produces: 干净工作区 + 基线数字(5 failed / 1527 passed),后续所有 Task 的回退锚点。

- [x] **Step 1: .gitignore 补运行时产物**

在 `.gitignore` 末尾追加(先 `cat .gitignore` 确认不重复):

```
# 运行时诊断产物(2026-09-19 重组清点)
storage/browser_profile_xhs/
storage/diag_*.png
storage/metrics_capture.png
```

- [x] **Step 2: 核对将提交清单无意外项**

Run: `git status --short | grep -v "^??" ; git status --short | grep "^??" | head -30`
Expected: 未跟踪项均为报告/文档/`scripts/diag/profile_backfill_day.py`/`scripts/replay_rotation_close.py` 类;`storage/` 下的 png 与 browser_profile 已被新 ignore 规则吞掉(不再出现在 `??`)。

- [x] **Step 3: 归零提交**

```bash
git add -A
git commit -m "chore: 重组前工作区归零——日报积压入库+storage运行时产物gitignore+replay_rotation_close入库"
git status --short   # 必须为空
```

- [x] **Step 4: 基线锚定**

Run: `.venv/bin/python -m pytest tests/ davis_analyzer/tests -q 2>&1 | tail -2`
Expected: `5 failed, 1527 passed`,失败全在 `test_publisher_m3.py`。

### Task 1: 顶层归档集群 + 根清理

**Files:**
- Create: `archive/README.md`
- Move: `dashboard/ src/ test-results/ public/ davis_webui/` 及看板配套 → `archive/`
- Delete: `~/.config/systemd/user/davis-webui-backend.{service,timer}` 等 webui unit、空壳 `data/`、根 `.coverage`、根 `__pycache__/`

**Interfaces:**
- Produces: 顶层只剩 stockhot / davis_analyzer / scripts / docs / studies / storage / tests / archive / 配置文件;后续 Task 的 grep 验证以 `archive/` 为排除前缀。

- [x] **Step 1: 停用 webui 服务**

```bash
systemctl --user stop davis-webui-backend.service davis-webui-frontend.service
systemctl --user disable davis-webui-backend.service davis-webui-frontend.service
systemctl --user is-active davis-webui-backend.service davis-webui-frontend.service  # 两者均 inactive/failed 即可
```

- [x] **Step 2: 建归档区并移入看板集群**

```bash
mkdir -p archive
git mv dashboard archive/dashboard
git mv src archive/src
git mv test-results archive/test-results
git mv public archive/public
git mv davis_webui archive/davis_webui
git mv docker-compose.davis.yml archive/
git mv .env.davis archive/ 2>/dev/null || mv .env.davis archive/   # 视是否被跟踪
git mv docker-compose.yml archive/
git mv docker archive/docker
git mv package.json archive/
git mv package-lock.json archive/
git mv scripts/start-davis-quick-tunnel.sh scripts/stop-davis-quick-tunnel.sh scripts/verify-davis-tunnel.sh archive/
mv node_modules archive/node_modules   # 未跟踪,纯 mv
git mv SPEC.md archive/ 2>/dev/null || true   # SPEC.md 若属看板叙事则归档;否则跳过(先 head -5 判断)
```

注:根 `package.json`(`main: src/app/main.js`,Express 看板)与 `docker-compose.yml`(backend/frontend/cloudflared)均属看板集群,一并归档;`.github/` 服务 Python 单仓,**保留**。

- [x] **Step 3: 根杂物清理**

```bash
rm -rf data __pycache__ .coverage .pytest_cache .ruff_cache   # data 为空壳;cache 类无提交价值
git status --short | head    # 确认无意外删除(这些应本就未跟踪或已 ignore)
```

若 `git status` 出现 `.coverage` 等的 `D`(说明曾被跟踪):`git rm --cached` 后并入本次 commit。

- [x] **Step 4: 删 webui systemd unit**

```bash
rm ~/.config/systemd/user/davis-webui-backend.service ~/.config/systemd/user/davis-webui-frontend.service
ls ~/.config/systemd/user/ | grep -i davis    # 若还有 davis-webui 相关残留 timer 也一并删
systemctl --user daemon-reload
systemctl --user list-timers | head -25       # 无 davis-webui 行
```

- [x] **Step 5: 写归档说明**

`archive/README.md`:

```markdown
# 归档区

本目录存放已停用项目,git 历史完整,复活方法 = `git mv` 回原位。

| 目录 | 原顶层路径 | 归档日期 | 原因 |
|---|---|---|---|
| dashboard/ | dashboard/ | 2026-09-19 | stockhot 数据看板(Next.js+TS),2026-05 后未动 |
| src/ + test-results/ + package.json + node_modules + docker-compose.yml + docker/ | 同名根路径 | 2026-09-19 | 仓库最初的 CodeAgent 看板(Express+Playwright+vitest),2026-05 后未动 |
| public/ | public/ | 2026-09-19 | 早期静态前端(vanilla JS),2026-05 后未动 |
| davis_webui/ + docker-compose.davis.yml + .env.davis + *-davis-quick-tunnel.sh | 同名根路径 | 2026-09-19 | davis Web 界面(FastAPI+Next.js),2026-07 后未动,systemd 服务已 stop+disable,unit 已删 |
| SPEC.md | SPEC.md | 2026-09-19 | 看板时代规格书(若 Step 2 判断归档) |
```

- [x] **Step 6: 残留引用验证**

Run: `grep -rn -E "davis_webui|dashboard/|quick-tunnel" --include="*.py" --include="*.sh" --include="*.yml" stockhot/ davis_analyzer/ scripts/ tests/ studies/ .github/ 2>/dev/null | grep -v archive | head`
Expected: 空或仅注释性提及(记录下来,若为活代码引用则现场修复后再继续)。

- [x] **Step 7: 门禁 + commit**

```bash
.venv/bin/python -m pytest tests/ davis_analyzer/tests -q 2>&1 | tail -2   # 5 failed / 1527 passed
git add -A && git commit -m "chore(reorg): 顶层归档——看板集群/webui/public入archive,根杂物清理,webui服务停用"
```

### Task 2: davis_analyzer core + factors 分层

**Files:**
- Create: `davis_analyzer/core/`、`davis_analyzer/factors/`(各含 `__init__.py`)
- Move: 9 个 core 模块 + 19 个 factors 模块(见 Step 1 映射)
- Modify: 全库 import(重写脚本见 Step 3)

**Interfaces:**
- Produces: `davis_analyzer.core.{config,constants,types,tushare_client,financial_fetcher,stock_universe,pipeline,scoring,price_estimator}`、`davis_analyzer.factors.{valuation,valuation_forward,prosperity,prosperity_sector,prosperity_inflection,momentum,trend,distress,dividend,forecast,profitability,holder_concentration,quality_factor,sub_industry,international_overlay,market_regime,sector_pipeline,strategy_signal,cyclical}`;Task 3/4 复用 Step 3 的重写脚本。

- [x] **Step 1: git mv 分层**

```bash
cd davis_analyzer
mkdir core factors
touch core/__init__.py factors/__init__.py
git mv config.py constants.py types.py tushare_client.py financial_fetcher.py stock_universe.py pipeline.py scoring.py price_estimator.py core/
git mv valuation.py valuation_forward.py prosperity.py prosperity_sector.py prosperity_inflection.py momentum.py trend.py distress.py dividend.py forecast.py profitability.py holder_concentration.py quality_factor.py sub_industry.py international_overlay.py market_regime.py sector_pipeline.py strategy_signal.py cyclical.py factors/
cd ..
```

注意:`config/`(数据目录,sub_industry_map.json)**原地不动**;`cli.py __main__.py __init__.py` 留根。

- [x] **Step 2: 写 import 重写脚本**

创建 `/tmp/rewrite_imports.py`(Task 2/3/4 共用,只改 MAPPING):

```python
#!/usr/bin/env python3
"""按 MAPPING 重写全库 davis_analyzer.<name> 引用;打印每文件替换计数。"""
import re
import sys
from pathlib import Path

REPO = Path("/home/leo/Projects/CodeAgentDashboard")
EXCLUDE_PREFIXES = ("archive", ".venv", "node_modules", ".git", "docs/superpowers",
                    "docs/回测记录", "storage")

# 按任务填充:name → 新子包路径(不含 davis_analyzer. 前缀)
MAPPING = {
    "config": "core.config", "constants": "core.constants", "types": "core.types",
    "tushare_client": "core.tushare_client", "financial_fetcher": "core.financial_fetcher",
    "stock_universe": "core.stock_universe", "pipeline": "core.pipeline",
    "scoring": "core.scoring", "price_estimator": "core.price_estimator",
    "valuation": "factors.valuation", "valuation_forward": "factors.valuation_forward",
    "prosperity": "factors.prosperity", "prosperity_sector": "factors.prosperity_sector",
    "prosperity_inflection": "factors.prosperity_inflection", "momentum": "factors.momentum",
    "trend": "factors.trend", "distress": "factors.distress", "dividend": "factors.dividend",
    "forecast": "factors.forecast", "profitability": "factors.profitability",
    "holder_concentration": "factors.holder_concentration",
    "quality_factor": "factors.quality_factor", "sub_industry": "factors.sub_industry",
    "international_overlay": "factors.international_overlay",
    "market_regime": "factors.market_regime", "sector_pipeline": "factors.sector_pipeline",
    "strategy_signal": "factors.strategy_signal", "cyclical": "factors.cyclical",
}

def main() -> None:
    # 键按长度降序,防短名先吃到长名前缀(_ 是词字符,\b 本身也安全,双保险)
    pairs = sorted(MAPPING.items(), key=lambda kv: -len(kv[0]))
    total = 0
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if any(rel.startswith(p) for p in EXCLUDE_PREFIXES):
            continue
        text = orig = path.read_text(encoding="utf-8")
        count = 0
        for name, new in pairs:
            text, n = re.subn(rf"davis_analyzer\.{name}\b",
                              f"davis_analyzer.{new}", text)
            count += n
        if count:
            path.write_text(text, encoding="utf-8")
            print(f"{rel}: {count}")
            total += count
    print(f"TOTAL: {total}")

if __name__ == "__main__":
    sys.exit(main())
```

- [x] **Step 3: 执行重写并检查 `from davis_analyzer import X` 裸形式**

```bash
.venv/bin/python /tmp/rewrite_imports.py
grep -rn "from davis_analyzer import" --include="*.py" stockhot/ scripts/ tests/ studies/ davis_analyzer/ | grep -v archive | head
```

Expected: 第二条命令输出为空(若不为空,逐行手工改成 `from davis_analyzer.core import X` 形式)。

- [x] **Step 4: 残留验证**

```bash
grep -rnE "davis_analyzer\.(config|constants|types|tushare_client|financial_fetcher|stock_universe|pipeline|scoring|price_estimator|valuation|valuation_forward|prosperity|prosperity_sector|prosperity_inflection|momentum|trend|distress|dividend|forecast|profitability|holder_concentration|quality_factor|sub_industry|international_overlay|market_regime|sector_pipeline|strategy_signal|cyclical)\b" \
  --include="*.py" --include="*.md" stockhot/ scripts/ tests/ studies/ davis_analyzer/ .agents/ 2>/dev/null | grep -v archive | head
```

Expected: 空(`.md` 命中若属历史文档不动,属 AGENTS/SKILL 记录到 Task 6 清单)。

- [x] **Step 5: 门禁 + commit**

```bash
.venv/bin/python -m pytest tests/ davis_analyzer/tests -q 2>&1 | tail -2   # 5 failed / 1527 passed
.venv/bin/python -c "from davis_analyzer.core import pipeline; from davis_analyzer.factors import valuation; print('import ok')"
git add -A && git commit -m "refactor(davis): core/factors 分层——28模块git mv+全库import重写,不留兼容shim"
```

### Task 3: report + backtest + migrations 分层

**Files:**
- Create: `davis_analyzer/report/`、`davis_analyzer/backtest/`、`davis_analyzer/migrations/`(各含 `__init__.py`)
- Move: 4 + 3 + 2 个模块

- [x] **Step 1: git mv**

```bash
cd davis_analyzer
mkdir report backtest migrations
touch report/__init__.py backtest/__init__.py migrations/__init__.py
git mv templates.py report_generator.py checklist_generator.py rescorer.py report/
git mv backtest.py backtest_factors.py backtest_report.py backtest/
git mv migrate_cache.py migrate_to_market_db.py migrations/
cd ..
```

- [x] **Step 2: 更新重写脚本 MAPPING 并执行**

`/tmp/rewrite_imports.py` 的 MAPPING 替换为:

```python
MAPPING = {
    "templates": "report.templates", "report_generator": "report.report_generator",
    "checklist_generator": "report.checklist_generator", "rescorer": "report.rescorer",
    "backtest": "backtest.backtest", "backtest_factors": "backtest.backtest_factors",
    "backtest_report": "backtest.backtest_report",
    "migrate_cache": "migrations.migrate_cache", "migrate_to_market_db": "migrations.migrate_to_market_db",
}
```

Run: `.venv/bin/python /tmp/rewrite_imports.py`

- [x] **Step 3: 残留验证**

```bash
grep -rnE "davis_analyzer\.(templates|report_generator|checklist_generator|rescorer|backtest|backtest_factors|backtest_report|migrate_cache|migrate_to_market_db)\b" \
  --include="*.py" stockhot/ scripts/ tests/ studies/ davis_analyzer/ .agents/ 2>/dev/null | grep -v archive | head
```

Expected: 活代码为空。

- [x] **Step 4: 回测烟测(模块可导入即过,不真跑回测)**

```bash
.venv/bin/python -c "from davis_analyzer.backtest import backtest; from davis_analyzer.report import templates; from davis_analyzer.migrations import migrate_cache; print('ok')"
```

- [x] **Step 5: 门禁 + commit**

```bash
.venv/bin/python -m pytest tests/ davis_analyzer/tests -q 2>&1 | tail -2
git add -A && git commit -m "refactor(davis): report/backtest/migrations 分层+import重写"
```

### Task 4: systems 归拢 + systemd 修复(周日 08:00 硬边界)

**Files:**
- Create: `davis_analyzer/systems/`(含 `__init__.py`、`README.md`)
- Move: 9 个子系统目录
- Modify: `~/.config/systemd/user/{thermometer-run,thermometer-universe,recap-run,surge-run}.service` 的 ExecStart

**Interfaces:**
- Produces: CLI 形式 `python -m davis_analyzer.systems.<name>`;`davis_analyzer.systems.{limitup,tournament,thermometer,intraday,cardgen,recap,surge,paper_trading,metrics}`。

- [x] **Step 1: git mv 子系统**

```bash
cd davis_analyzer
mkdir systems && touch systems/__init__.py
git mv limitup tournament thermometer intraday cardgen recap surge paper_trading metrics systems/
cd ..
```

- [x] **Step 2: 更新 MAPPING 并执行重写**

```python
MAPPING = {
    "limitup": "systems.limitup", "tournament": "systems.tournament",
    "thermometer": "systems.thermometer", "intraday": "systems.intraday",
    "cardgen": "systems.cardgen", "recap": "systems.recap",
    "surge": "systems.surge", "paper_trading": "systems.paper_trading",
    "metrics": "systems.metrics",
}
```

Run: `.venv/bin/python /tmp/rewrite_imports.py`

- [x] **Step 3: 写 systems/README.md 导航**

`davis_analyzer/systems/README.md`,每子系统四行信息(名字/一句话职责/CLI/关键表),内容取自根 AGENTS.md 对应段落浓缩。格式:

```markdown
# davis_analyzer 子系统导航

| 子系统 | 职责 | CLI | 关键表/产物 |
|---|---|---|---|
| limitup | 涨停研究:回补→事件/形态/情绪→事件研究→事件驱动打板回测 | `python -m davis_analyzer.systems.limitup` | 自管表+reports/candidates_*.md |
| tournament | 策略锦标赛:适配→统一评估→评分权重→回放→CPCV-lite进化→冠军存档 | `python -m davis_analyzer.systems.tournament {run\|replay\|evolve\|champions}` | 共享库 tournament_ledger 表 |
| thermometer | 板块温度计:宇宙→数据回补→成分聚合主力净额→五族因子→截面温度+大盘五维→校准→日报 | `python -m davis_analyzer.systems.thermometer {backfill\|run\|calibrate\|report\|status}` | market_data.db 九张表+thermo卡长图链 |
| intraday | 日内做T研究沙盒(baostock 分钟线):回补→闭环回转引擎→因果特征→策略族→对账→影子验证 | `python -m davis_analyzer.systems.intraday {backfill\|status\|verify\|run\|shadow\|shadow-report\|shadow-enrich}` | 独立库 intraday_research.db |
| cardgen | 金融卡片生成:facts 溯源→物化→四道机器闸→渲染→发布包 | `python -m davis_analyzer.systems.cardgen {init\|ingest\|validate\|build\|status\|enqueue\|sync}` | content_cards.db+docs/发布/小红书 工程树 |
| recap | 复盘视频:NBA解说式热点复盘短视频(选片→剧本→录制单→原料包→五佳球模式成片) | `python -m davis_analyzer.systems.recap {run\|select\|script\|sheet\|audio\|post\|status}` | recap_episodes 表+episodes/{day}/ |
| surge | 涨幅筛选:当日>7%九维分析(筹码/巨潮公告/压力支撑/形态/标签)+双工作长图 | `python -m davis_analyzer.systems.surge {run\|backfill\|status}` | 七张自管表+reports/{YYYYMMDD}/ |
| paper_trading | 模拟交易子系统 | `python -m davis_analyzer.systems.paper_trading {init\|run\|backfill\|report\|list}` | paper_accounts 等台账 |
| metrics | 指标采集与报告 | `python -m davis_analyzer.systems.metrics` | collector/db/report |

> 各子系统详细纪律见仓库根 AGENTS.md 对应章节;本文件只做导航。
```

- [x] **Step 4: 改 4 个 systemd unit + reload**

```bash
sed -i 's/davis_analyzer\.thermometer/davis_analyzer.systems.thermometer/' \
  ~/.config/systemd/user/thermometer-run.service ~/.config/systemd/user/thermometer-universe.service
sed -i 's/davis_analyzer\.recap/davis_analyzer.systems.recap/' ~/.config/systemd/user/recap-run.service
sed -i 's/davis_analyzer\.surge/davis_analyzer.systems.surge/' ~/.config/systemd/user/surge-run.service
systemctl --user daemon-reload
grep -h ExecStart ~/.config/systemd/user/{thermometer-run,thermometer-universe,recap-run,surge-run}.service
```

Expected: 4 行 ExecStart 均含 `davis_analyzer.systems.`。

- [x] **Step 5: CLI 烟测(选不触网/只读子命令)**

```bash
.venv/bin/python -m davis_analyzer.systems.thermometer status 2>&1 | head -5
.venv/bin/python -m davis_analyzer.systems.surge status 2>&1 | head -5
.venv/bin/python -m davis_analyzer.systems.cardgen status 2>&1 | head -5
.venv/bin/python -c "import davis_analyzer.systems.recap, davis_analyzer.systems.limitup, davis_analyzer.systems.tournament, davis_analyzer.systems.intraday, davis_analyzer.systems.paper_trading, davis_analyzer.systems.metrics; print('ok')"
```

Expected: 三条 status 正常输出(读库即返回),import ok。

- [x] **Step 6: 门禁 + commit**

```bash
.venv/bin/python -m pytest tests/ davis_analyzer/tests -q 2>&1 | tail -2
git add -A && git commit -m "refactor(davis): 九子系统归拢systems/+CLI升级systems路径+4个systemd unit同步"
```

**此 Task 完成后,周日 timer 已安全。若后续中断,不影响周日 08:00 thermometer-universe。**

### Task 5: scripts 分组 + systemd 路径修复

**Files:**
- Create: `scripts/ops/`、`scripts/research/`
- Move: 13 个运维脚本 → `ops/`;6 个研究目录 → `research/`;3 个散脚本归位
- Modify: 10 个 systemd unit 的 ExecStart

- [x] **Step 1: git mv 运维脚本**

```bash
cd scripts
mkdir ops research
git mv after_hours_inhibit.sh chase_shadow_daily.py distress_signal_export.py g2_signal_export.py \
       replay_rotation_close.py pool_digest.py unlock_calendar.py wait_and_backfill.sh \
       daily_market_cards.py daily_bulletin.py daily_longpic.py longpic_numbers_check.py \
       publish_reconcile.py ops/
cd ..
```

- [x] **Step 2: git mv 研究目录与散脚本**

```bash
cd scripts
git mv washout_research promotion_research tech_research trend_exit_research event_research intraday_research research/
git mv negative_factor_abx.py abx/
git mv research_search.py tools/
git mv run_5yr_backtest.sh run_all_abx.sh abx/
cd ..
```

- [x] **Step 3: 改 10 个 systemd unit**

对以下 unit 把 `scripts/<name>` 替换为 `scripts/ops/<name>`(逐个 sed 或手工,注意 daily-longpic 与 thermometer-card 是 `bash -c` 双命令都要改):

```bash
for pair in "after-hours-inhibit after_hours_inhibit.sh" \
            "trading-hours-inhibit after_hours_inhibit.sh" \
            "g2-signal-export g2_signal_export.py" \
            "chase-shadow chase_shadow_daily.py" \
            "daily-longpic daily_longpic.py" \
            "screener-card daily_market_cards.py" \
            "rotation-close-replay replay_rotation_close.py" \
            "radar-feishu daily_bulletin.py" \
            "distress-signal-export distress_signal_export.py" \
            "thermometer-card daily_market_cards.py" ; do
  set -- $pair
  sed -i "s|scripts/$2|scripts/ops/$2|g" ~/.config/systemd/user/$1.service
done
sed -i "s|scripts/daily_longpic.py|scripts/ops/daily_longpic.py|g" ~/.config/systemd/user/thermometer-card.service
systemctl --user daemon-reload
grep -h ExecStart ~/.config/systemd/user/*.service | grep -oE "scripts[^ ;]*" | sort -u
```

Expected: 输出仅含 `scripts/ops/...`、`scripts/content_publisher/...`、`scripts/abx/...` 三类。

- [x] **Step 4: 代码内 scripts 路径引用同步**

Run: `grep -rn -E "scripts/(daily_|chase_|g2_|distress_|replay_|pool_|after_hours|unlock_|wait_|longpic_numbers|publish_reconcile)" --include="*.py" --include="*.md" --include="*.sh" stockhot/ davis_analyzer/ scripts/ tests/ studies/ .agents/ 2>/dev/null | grep -v archive | grep -v "scripts/ops/" | head -20`

对每一条**代码**(.py/.sh)引用改为 `scripts/ops/`;`.md` 命中若属 AGENTS/SKILL/cron prompts 记入 Task 6 清单,属历史文档不动。

- [x] **Step 5: 门禁 + commit**

```bash
.venv/bin/python -m pytest tests/ davis_analyzer/tests -q 2>&1 | tail -2
git add -A && git commit -m "chore(scripts): ops/research分组——13运维脚本入ops,6研究项目入research,10个systemd unit同步"
```

### Task 6: docs 激进重分 + 代码路径同步 + 真相源文档重写

**Files:**
- Move: `docs/` 全部内容分类(命令见 Step 1)
- Modify: 6 处代码默认路径、`publish_sync.py` 双标记兼容、两份 AGENTS.md、`docs/README.md`、`docs/代码库索引.md`、6 个 SKILL.md、1 个 cron prompt
- Modify(DB): `storage/database/content_cards.db`、`content_publisher.db`、`content_publisher_archive.db`(若存在)路径前缀 UPDATE

- [x] **Step 1: 目录重分(git mv)**

```bash
cd docs
mkdir -p 研报/个股 研报/产业链 研报/方法论 复盘/盘前 复盘/盘后 发布/长图 发布/雪球 开发
git mv 个股研报/* 研报/个股/ && rmdir 个股研报
git mv 产业链研报/* 研报/产业链/ && rmdir 产业链研报
git mv 方法论/* 研报/方法论/ && rmdir 方法论
git mv 盘前整理/* 复盘/盘前/ && rmdir 盘前整理
git mv 盘后复盘/* 复盘/盘后/
git mv 盘后总结/* 复盘/盘后/ && rmdir 盘后总结        # 已验证零同名冲突
git mv 小红书卡片 发布/小红书
git mv 长图/* 发布/长图/ && rmdir 长图
git mv 雪球发布/* 发布/雪球/ && rmdir 雪球发布
git mv 分析笔记 开发/分析笔记
git mv 开发记录 开发/开发记录
git mv 其他/* 开发/ && rmdir 其他                    # 执行前 ls 其他/ 确认无同名冲突
git mv paper_trading_forward_live_requirements.md 开发/
cd ..
ls docs/    # 期望:superpowers 研报 复盘 回测记录 发布 开发 README.md 代码库索引.md
```

- [x] **Step 2: 代码默认路径同步(6 处)**

```bash
# 1) cardgen 工程根
sed -i 's|"docs", "小红书卡片"|"docs", "发布", "小红书"|' davis_analyzer/systems/cardgen/cli.py
# 2) 日卡生成器同款默认
sed -i 's|"docs", "小红书卡片"|"docs", "发布", "小红书"|' scripts/ops/daily_market_cards.py
# 3) 长图链 CARDS_ROOT
sed -i 's|docs/小红书卡片/未发布|docs/发布/小红书/未发布|' scripts/ops/daily_longpic.py
# 4) 公告日报输出默认
sed -i 's|docs/小红书卡片/未发布/公告日报|docs/发布/小红书/未发布/公告日报|' scripts/ops/daily_bulletin.py
# 5) 长图审计工具 BASE
sed -i 's|docs/小红书卡片/未发布|docs/发布/小红书/未发布|' scripts/ops/longpic_numbers_check.py
grep -rn "小红书卡片" --include="*.py" davis_analyzer/ scripts/ | grep -v archive    # 仅 publish_sync.py 应命中
```

- [x] **Step 3: publish_sync 双标记兼容**

`davis_analyzer/systems/cardgen/publish_sync.py`:

```python
# 旧:
# _MARKER = "小红书卡片"  # publish_queue.source 形如 docs/小红书卡片/<topic>
# 新:
_MARKERS = ("小红书卡片", "小红书")  # 新旧两代根名;历史行=小红书卡片,现行=发布/小红书
```

`_published_topics` 内:

```python
        parts = Path(str(source)).parts
        marker = next((m for m in _MARKERS if m in parts), None)
        if marker is not None:
            tail = parts[parts.index(marker) + 1:]
```

(即把 `if _MARKER in parts:` 块改为上述;其余剥归档层级逻辑不动。)

- [x] **Step 4: 台账 DB 一次性路径 UPDATE(先备份)**

```bash
cp storage/database/content_cards.db /tmp/content_cards.db.bak
cp storage/database/content_publisher.db /tmp/content_publisher.db.bak
[ -f storage/database/content_publisher_archive.db ] && cp storage/database/content_publisher_archive.db /tmp/content_publisher_archive.db.bak
.venv/bin/python - <<'EOF'
import sqlite3
for db, table, col in [
    ("storage/database/content_cards.db", "cards", "spec_path"),
    ("storage/database/content_publisher.db", "publish_queue", "source"),
    ("storage/database/content_publisher_archive.db", "publish_log", "source"),
]:
    try:
        conn = sqlite3.connect(db)
        n = conn.execute(
            f"UPDATE {table} SET {col} = REPLACE({col}, 'docs/小红书卡片', 'docs/发布/小红书') "
            f"WHERE {col} LIKE '%小红书卡片%'").rowcount
        conn.commit(); conn.close()
        print(db, table, n)
    except Exception as e:
        print(db, "SKIP:", e)
EOF
```

(archive 库的 publish_log 列名执行时先 `PRAGMA table_info` 核对,不对则改列名;SKIP 输出可接受——记录即可。)

- [x] **Step 5: skills 与 cron prompt 路径同步**

```bash
sed -i 's|docs/盘后总结|docs/复盘/盘后|g; s|docs/方法论|docs/研报/方法论|g' .agents/skills/after-hours-review/SKILL.md
sed -i 's|docs/方法论|docs/研报/方法论|g' .agents/skills/daily-market-scan/SKILL.md .agents/skills/invest-sop-pre-market/SKILL.md
sed -i 's|docs/个股研报|docs/研报/个股|g; s|docs/方法论|docs/研报/方法论|g' .agents/skills/research-report/SKILL.md
sed -i 's|docs/个股研报|docs/研报/个股|g' .agents/skills/industry-prosperity/SKILL.md
sed -i 's|docs/分析笔记|docs/开发/分析笔记|g; s|docs/小红书卡片|docs/发布/小红书|g' docs/superpowers/prompts/weekly_market_portrait_cron_prompt.md
grep -rn "盘后总结\|小红书卡片\|个股研报\|产业链研报\|盘前整理\|盘后复盘" .agents/skills/ docs/superpowers/prompts/ | grep -v "发布/小红书\|复盘/盘后\|研报/个股" | head
```

Expected: 末条 grep 为空或仅注释性行(逐条处理)。

- [x] **Step 6: AGENTS.md ×2 + README + 索引重写**

逐节更新(不是重写全文,是路径与叙事同步):

1. 根 `AGENTS.md`:模块地图加 core/factors/report/backtest/systems/migrations 分层;子系统 CLI 全部改 `python -m davis_analyzer.systems.*`;`scripts/` 引用改 `scripts/ops/`、`scripts/research/`;docs 路径按新树(`docs/发布/小红书` 三态约定、`docs/复盘/盘后`、`docs/研报/方法论`);删除 davis_webui/docker 看板/quick-tunnel/run_output.log 全部叙事;补一行「归档区 archive/ 说明」。
2. `davis_analyzer/AGENTS.md`:同口径更新模块划分与依赖方向图。
3. `docs/README.md`:docs 新树导航(四大树+superpowers+回测记录)。
4. `docs/代码库索引.md`:顶层目录总览更新(去归档项、加 archive/),工具类/引擎类表格中全部 davis 模块路径、scripts 路径按新结构改;「最近更新」改 2026-09-19。

- [x] **Step 7: cardgen 冒烟 + 门禁 + commit**

```bash
.venv/bin/python -m davis_analyzer.systems.cardgen status 2>&1 | head -5      # 走新路径读台账不炸
.venv/bin/python -m pytest tests/ davis_analyzer/tests -q 2>&1 | tail -2
git add -A && git commit -m "chore(docs): 激进重分四大树+cardgen/长图链路径同步+publish_sync双标记+台账路径UPDATE+AGENTS/索引真相源重写"
```

### Task 7: 终验收口

**Files:** 无新改动,只验证与报告。

- [x] **Step 1: 全量测试(含 stockhot)**

```bash
.venv/bin/python -m pytest -q 2>&1 | tail -2   # 按 pyproject testpaths 跑全集
.venv/bin/python -m pytest tests/ davis_analyzer/tests -q 2>&1 | tail -2
```

Expected: 与基线口径一致(testpaths 全集同样只有那 5 个 publisher 既有失败;若 stockhot 测试另有环境性失败,与重组前对照——可先在 git stash 前提下抽验,执行者记录差异并判断是否重组引入)。

- [x] **Step 2: timer 全量核对**

```bash
systemctl --user daemon-reload
systemctl --user list-timers --all | grep -vE "^$" | head -25
grep -h ExecStart ~/.config/systemd/user/*.service | grep -E "davis_analyzer|scripts" | sort -u
```

Expected: 模块 CLI 均为 `davis_analyzer.systems.*`;scripts 均为 `scripts/ops/|content_publisher/|abx/`;无任何指向不存在文件的行(可再跑 `while read -r u; do systemctl --user cat $u >/dev/null || echo "BAD $u"; done < <(systemctl --user list-units --type=service --no-legend | awk '{print $1}'))`。

- [x] **Step 3: 旧路径残留全库审计**

```bash
grep -rn -E "davis_analyzer\.(thermometer|surge|recap|cardgen|limitup|tournament|intraday|paper_trading|metrics|valuation|pipeline|scoring|config|constants)\b" \
  --include="*.py" --include="*.sh" --include="*.service" --include="*.timer" \
  stockhot/ davis_analyzer/ scripts/ tests/ studies/ ~/.config/systemd/user/ 2>/dev/null | grep -v "systems\." | grep -v archive | head
grep -rn "docs/小红书卡片\|docs/盘后总结\|docs/个股研报\|docs/方法论" \
  --include="*.py" --include="*.md" davis_analyzer/ scripts/ .agents/ docs/superpowers/prompts/ 2>/dev/null | grep -v archive | head
```

Expected: 均为空(命中项逐条归属:历史文档豁免/活代码必须修)。

- [x] **Step 4: CLI 抽查矩阵**

```bash
for m in thermometer surge cardgen limitup; do echo "== $m"; .venv/bin/python -m davis_analyzer.systems.$m status 2>&1 | head -3; done
```

Expected: 全部正常返回(不触网)。

- [x] **Step 5: 收尾报告**

向用户输出:7 个 commit 清单(`git log --oneline -8`)、测试基线对比、timer 核对结论、遗留事项(如 5 个既有 publisher 测试失败与本次无关、cron prompt 若有历史引用豁免明细)。

---

## 中断与回滚

- 每个 Task 独立 commit,`git revert <sha>` 单独回退;systemd unit 改动同步 `daemon-reload` 验证。
- Task 4 完成前若必须中断:立即 `systemctl --user disable thermometer-universe.timer`,周一补。
- 台账 DB 改动有 `/tmp/*.bak` 备份,回滚 = 拷回。
