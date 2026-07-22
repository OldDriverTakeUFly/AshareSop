# AGENTS.md — davis_analyzer 项目 Agent 协作规范

> 本文件由 ZCode(及兼容 agent)自动加载,定义本项目的工作约定。所有 agent 在本项目内执行任务时遵循以下规范。

## 项目概述

**davis_analyzer** —— 基于「戴维斯双击」估值理论的 A 股选股分析器。通过 Tushare Pro 拉取行情/财报数据,计算 3 年历史估值分位(PE/PB)、综合景气度评分、三层困境反转信号,对低估值候选股排名并生成模板化深度研报。同时包含周期再平衡回测引擎和模拟交易子系统。

- **核心语言**:Python 3.11+(代码使用 `from __future__ import annotations` + PEP 604 联合类型)
- **回测/数据**:自研(基于 pandas/numpy),不依赖 Zipline/Backtrader
- **数据源**:Tushare Pro(唯一外部数据源)

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

**回测子系统**(相对独立):`backtest.py`(周期再平衡主循环) → `backtest_factors.py`(横截面因子评分) → `backtest_report.py`(收益/夏普/回撤 + CSV 导出)。

**输出层**:`report_generator.py` + `templates.py`(模板化研报,无 LLM);`checklist_generator.py` + `rescorer.py`(深度调研清单循环,人工定性调整)。

**配置与类型**:`config.py`(路径/token)、`constants.py`(评分权重与阈值,单一真相源)、`types.py`(7 个纯数据 dataclass)。

## rtk 使用规范(节省 token)

本项目已本地部署 **rtk**(CLI 代理,压缩命令输出)。执行输出冗长的命令时**必须优先用 rtk 包装**。

| 原命令 | 用法 | 场景 |
|--------|------|------|
| `pytest` | `rtk pytest` | 跑测试(本项目 21 个测试文件,输出长) |
| `python -m davis_analyzer run` | 视输出长度决定 | 跑完整 pipeline 输出极长,**强烈建议 rtk** |
| `pip install -e .` | `rtk pip install -e .` | 从父项目根目录装依赖 |
| `ls` / `find` | `rtk ls` / `rtk find ...` | 列目录/查找 |
| `grep` / `rg` | `rtk grep ...` / `rtk rg ...` | 搜索代码 |
| `git status/log/diff` | `rtk git status` 等 | git 操作 |
| `cat 大文件` | `rtk read <file>` | 读大文件 |

**无需 rtk**:短命令(`mkdir`/`mv`/`echo`)、已知输出极短的命令、修改系统状态的命令(`rm`/`git commit`)。

**原则**:不确定输出多长时,默认用 rtk。

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

## 配置与运行

- **Token**:`TUSHARE_TOKEN` 环境变量(从父仓库根目录 `.env` 读)。
- **输出位置**:研报写入 `STUDIES_DIR`(`davis_analyzer/studies/`),文件名 `{rank}_{ts_code}_{name}_深度研报.md`。回测结果导出为 CSV(交易明细 + 权益曲线)。
- **入口**:
  - 主程序:`python -m davis_analyzer {run|deep-research|rescore}`
  - 模拟交易:`python -m davis_analyzer.paper_trading {init|run|backfill|report|list}`

## 协作流程

- **不擅自扩大范围**:严格按现有 pipeline 步骤实施,新增因子先讨论再落地。
- **动权重前先读 SOP**:`SOP.md` + `constants.py` 必须同步。
- **提交规范**(如启用 git):Conventional Commits 中文 scope,如 `feat(backtest): 实现周频再平衡主循环`。

## 已知技术债(可清理但别复现)

- `run_output.log` 是 4.9MB 的提交进 git 的日志,应加 `.gitignore`。
- `cli.py` 的 `_DEFAULT_CHECKLIST_DIR` 是相对路径,依赖调用方工作目录——改它要小心。
- README 声称"Python 3.12+",但 `pyproject.toml` 目标是 `py311` / `requires-python >= 3.11`。以 pyproject 为准。
