# 戴维斯双击估值分析器 (davis_analyzer)

## 第一章：项目简介

基于戴维斯双击理论的A股估值分析器。通过 Tushare Pro API 获取全市场行情与财务数据，计算3年历史估值分位、景气度复合评分和三层困境信号，综合筛选出具备戴维斯双击潜力的 Top 30 低估值标的，并生成深度研报。

核心能力：

- 3年历史估值分位（PE/PB百分位，周期股PB替代PE）
- 景气度复合评分（营收+盈利+趋势斜率+持续时间，四维加权）
- 三层困境信号（困境确认 + 反转可能 + 反转激活）
- Top N 标的排序输出 + 逐只深度研报生成（纯模板插值，不依赖 LLM）

数据源：仅使用 Tushare Pro API。

---

## 第二章：快速开始

### 环境要求

- Python 3.12+
- Tushare Pro 账号（[注册地址](https://tushare.pro)）

### 安装

在项目根目录执行：

```bash
pip install -e .
```

### 配置

在项目根目录创建 `.env` 文件，写入 Tushare API Token：

```
TUSHARE_TOKEN=your_token_here
```

### 使用方式

```bash
# 查看帮助
python -m davis_analyzer.cli --help

# 运行完整分析（Top 30标的）
python -m davis_analyzer.cli run --top 30

# 使用缓存数据（dry-run模式）
python -m davis_analyzer.cli run --dry-run --top 5

# 指定输出目录
python -m davis_analyzer.cli run --top 30 --output /path/to/output
```

### 参数说明

| 参数 | 说明 |
|------|------|
| `--dry-run` | 使用缓存数据，不调用 API |
| `--top N` | 输出前 N 个标的（默认 30） |
| `--output DIR` | 报告输出目录 |

### 输出位置

研报文件默认写入 `davis_analyzer/studies/` 目录。

---

## 第三章：整体架构

### 模块依赖关系

```
cli.py → pipeline.py → [stock_universe.py, tushare_client.py]
                            → [valuation.py, prosperity.py, distress.py]
                            → scoring.py → report_generator.py → templates.py
```

### 模块职责

| 模块 | 职责 |
|------|------|
| `tushare_client.py` | Tushare Pro API 客户端，内置频率限制（400次/分钟）和 SQLite 缓存 |
| `stock_universe.py` | A股股票宇宙构建，排除 ST/停牌/退市股 |
| `financial_fetcher.py` | 财务数据获取，合并利润表/资产负债表/现金流量表/财务指标 |
| `valuation.py` | 估值引擎，PE/PB 3年历史百分位，周期股 PB 替代 PE |
| `prosperity.py` | 景气度引擎，复合评分 + delta_G 边际变化 + 杜邦分解 |
| `distress.py` | 三层困境信号系统 |
| `scoring.py` | 戴维斯双击综合评分 |
| `report_generator.py` | 模板化研报生成器 |
| `templates.py` | 研报模板定义 |
| `pipeline.py` | 8步筛选管线编排 |
| `cli.py` | CLI 入口（argparse） |
| `config.py` | 配置加载（.env token、路径） |
| `constants.py` | 常量定义（权重、阈值） |
| `types.py` | 数据类型定义（7个 dataclass） |

### 评分模型

景气度权重：

```
revenue: 0.30  +  profit: 0.30  +  slope: 0.25  +  duration: 0.15
```

戴维斯双击权重：

```
valuation: 0.30  +  trend: 0.15  +  prosperity: 0.30  +  distress: 0.25
```

### 周期性行业

以下行业判定为周期股，估值引擎自动切换为 PB 替代 PE：

```
钢铁, 有色金属, 煤炭, 石油石化, 化工, 建材, 造纸
```

---

## 第四章：三层困境信号系统

困境评分由三层信号构成，每个信号产生 0.0-1.0 的连续得分，而非简单的二元（命中/未命中）。每层得分 = Σ(信号得分) / 信号数 × 100。最终困境得分 = 第一层 × 0.3 + 第二层 × 0.3 + 第三层 × 0.4。

### 第一层：困境确认

判断股票是否处于真正的困境状态。

| 信号 | 判断条件 |
|------|----------|
| `eps_decline` | 最新 EPS 同比下滑 > 30% |
| `pe_pb_percentile` | PE 或 PB 百分位 < 10%（深度低估） |
| `financial_health` | 资产负债率 < 50% 且经营性现金流 > 0 |

### 第二层：反转可能

判断资产负债表是否支撑困境反转。

| 信号 | 判断条件 |
|------|----------|
| `balance_sheet` | 总负债 / 总资产 < 50% |
| `operating_cf` | 经营性现金流 > 0 |
| `roe_trend` | ROE 企稳或改善（最新一期 >= 上一期） |

### 第三层：反转激活

判断基本面是否出现拐点信号。

| 信号 | 判断条件 |
|------|----------|
| `revenue_inflection` | 营收增速由负转正 |
| `profit_inflection` | 利润增速由负转正 |
| `delta_g_positive` | delta_G > 0（增速加速） |

---

## 第五章：筛选流程

管线编排 8 个步骤，从全 A 股逐步收敛至 Top N 标的：

1. **构建股票宇宙**（预筛 ST/停牌） → 约 4500 只
2. **获取全市场估值数据**（3年日线行情，`PERCENTILE_DAYS = 1095`）
3. **粗筛**：估值分数 > 50 → 约 500-800 只
4. **获取粗筛股财务数据**（4张报表合并：利润表、资产负债表、现金流量表、财务指标）
5. **计算景气度评分**（四维复合：营收增长、盈利增长、趋势斜率、持续时间）
6. **计算困境信号评分**（三层：困境确认、反转可能、反转激活）
7. **计算戴维斯双击综合评分**（估值 0.30 + 趋势 0.15 + 景气度 0.30 + 困境 0.25）
8. **排序输出 Top N**（默认 30 只）

---

## 第六章：研报输出

### 研报结构

每只标的的深度研报包含 7 个章节：

1. 公司概况
2. 估值分析
3. 景气度分析
4. 困境反转信号
5. 戴维斯双击评分
6. 风险因素
7. 投资结论

### 文件命名规则

- 个股研报：`{排名}_{股票代码}_{股票名称}_深度研报.md`
- 汇总索引：`戴维斯双击估值筛选汇总_YYYYMMDD.md`

### 技术说明

- 每篇研报上限约 1500 词
- 纯模板 + 数据插值，不依赖 LLM 或任何 AI 生成
- 投资结论根据综合评分自动分级：
  - 80分以上：强烈推荐关注
  - 65分以上：推荐关注
  - 50分以上：可关注
  - 50分以下：谨慎观察

---

## 第七章：开发与测试

### 运行测试

```bash
python -m pytest davis_analyzer/tests/ -v
```

### 代码检查

```bash
ruff check davis_analyzer/
```

### 测试文件

测试位于 `davis_analyzer/tests/` 目录。

---

## 第八章：方法论参考

- [戴维斯双击与困境反转方法论深度研报](../docs/戴维斯双击与困境反转方法论深度研报.md)
- [A股景气度投资方法论深度研报](../docs/A股景气度投资方法论深度研报.md)
- [A股多因子量化选股方法论深度研报](../docs/A股多因子量化选股方法论深度研报.md)

---

## 第九章：Milestone 及实现情况

| Wave | Task | 描述 | 状态 |
|------|------|------|------|
| Wave 1 | T1 | 项目脚手架 + 配置 + 依赖 | 完成 |
| Wave 1 | T2 | 类型定义 + 常量 + 评分权重 | 完成 |
| Wave 1 | T3 | 研报模板 + 汇总索引模板 | 完成 |
| Wave 2 | T4 | TushareClient + 股票宇宙 + 预筛 | 完成 |
| Wave 3 | T5 | 财务数据获取模块 | 完成 |
| Wave 3 | T6 | 估值引擎（PE/PB百分位 + 周期股处理） | 完成 |
| Wave 3 | T7 | 景气度引擎（复合评分 + delta_G + 杜邦分解） | 完成 |
| Wave 4 | T8 | 困境信号系统 + 戴维斯双击评分模型 | 完成 |
| Wave 5 | T9 | 筛选管线（8步编排） | 完成 |
| Wave 5 | T10 | 研报生成器 | 完成 |
| Wave 6 | T11 | CLI 入口 + 全量测试套件 | 完成 |
| Final | F1 | 计划合规审计 | APPROVE |
| Final | F2 | 代码质量审查 | APPROVE |
| Final | F3 | 手动 QA 验证 | APPROVE |
| Final | F4 | 范围忠实度检查 | APPROVE |

关键数据：25 个文件，3123 行代码，全量测试通过，commit `78a4bf3`。
