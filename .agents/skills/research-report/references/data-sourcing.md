# 数据采集与来源分级

本文件补充 SKILL.md Phase 2（数据采集）。研报的数据来源分两大类：**结构化数据**（引擎取数）和**非结构化数据**（手动研究）。两类都必须标注来源，但采集方法不同。

## 1. 结构化数据——调引擎取数

财务、估值、景气度、困境反转这些**可计算的数据**，一律调 `davis_analyzer` 引擎，不手算。引擎清单：

| 数据类型 | 引擎函数 | 文件 | 输出 |
|----------|----------|------|------|
| 财务四表 | `fetch_financial_data(code)` | `davis_analyzer/financial_fetcher.py` | income/balancesheet/cashflow/fina_indicator 合并 + YoY |
| 估值分位 | `calculate_valuation_score(code)` | `davis_analyzer/valuation.py` | PE/PB 3 年百分位（周期股用 PB） |
| 景气度 | `calculate_prosperity_score(financials)` | `davis_analyzer/prosperity.py` | 4 维加权（营收0.3+利润0.3+斜率0.25+持续0.15）+ ΔG + DuPont |
| 困境反转 | `calculate_distress_score(financials)` | `davis_analyzer/distress.py` | 3 层 9 信号（困境0.3+反转可能0.3+激活0.4） |
| 综合评分 | `calculate_davis_double_score(...)` | `davis_analyzer/scoring.py` | 估值0.3+趋势0.15+景气0.3+困境0.25 |
| 趋势 | `calculate_trend_score(code)` | `davis_analyzer/trend.py` | PE/PB 月度斜率/加速度 |

**调用方式**：
- 单股查询：`python -m davis_analyzer.cli run --top 30` 跑全市场筛选后查结果
- 研究脚本：参考 `davis_analyzer/studies/tianyue_*.py` 的写法，直接 import 引擎函数
- 批量：`sector_pipeline.run_prosperity_sector_pipeline()` 跑行业级景气度

**数据源**：Tushare Pro API（`stock_basic` / `daily_basic` / `income` / `balancesheet` / `cashflow` / `fina_indicator`）。需 `TUSHARE_TOKEN` 环境变量。结果缓存在 SQLite（`davis_analyzer/cache/tushare_cache.db`），财务数据永久缓存。

**引用格式**：`(12.30亿, tushare income 20251231)` 或 `(PE 25.3, tushare daily_basic 2026-06-23)`

## 2. 非结构化数据——手动研究

产业链结构、产能、价格、交期、订单、供需平衡、国产化率、国际同业财报——这些 Tushare 覆盖不了，必须 web 搜索 + 行业数据库 + 券商研报。

### 2.1 来源分级体系

这套分级体系来自 `stockhot/research_report/evidence.py`，是研报引用的核心规范。**每个催化剂/数据点都应标注来源级别**。

| 级别 | 定义 | 可靠度 | 示例 |
|------|------|--------|------|
| **一级证据** | 政策原文、公司公告、监管文件、交易所披露 | 高 | 工信部政策文件、公司年报、SEC 10-K |
| **二级证据** | 行业进展、技术验证、行业协会数据 | 中高 | TrendForce 报告、TTI MarketEye、USGS 矿产年鉴 |
| **辅助证据** | 主流财经媒体、券商研报 | 中 | 证券时报、第一财经、华泰/中信/开源研报 |
| **信号源** | 市场样本反馈（盘面数据） | 参考 | 涨停池、龙虎榜、资金流（daily-market-scan 采集） |

### 2.2 各类数据的典型来源

| 数据类型 | 推荐来源 | 级别 |
|----------|----------|------|
| 产业链结构 | 行业协会、券商产业链研报 | 二级 |
| 产能/稼动率 | 卓创资讯、百川盈孚、行业协会 | 二级 |
| 价格行情 | TTI MarketEye（电子）、USGS（矿产）、生意社 | 二级 |
| 交期/库存 | 分销商报告、厂商法说会 | 二级 |
| 供需平衡 | 券商深度研报、行业数据库 | 二级/辅助 |
| 国产化率 | 海关数据、行业协会、券商测算 | 二级/辅助 |
| 国际同业财报 | SEC EDGAR（10-K/10-Q）、港交所披露易 | 一级 |
| 政策催化 | 政府官网原文（工信部/发改委/证监会） | 一级 |
| 公司战略 | 年报"管理层讨论"、业绩说明会纪要 | 一级 |
| 技术路线 | 学术论文、专利数据库、技术白皮书 | 二级 |

### 2.3 引用格式

- **正文 inline 标签**：`($260/lb, TTI MarketEye 2026Q1)` 或 `(+37%, 东方钽业2025年报)`
- **数据来源索引表**（附录）：`| D1 | TrendForce Q1'26 钽电容报告 | 2026-03 | 中高 |` 
- **催化剂标签**：`[一级证据] 工信部 2026-05 发布《新材料产业发展指南》`

## 3. curated evidence packs（可选）

若研报主题命中以下 6 个预设主题之一，用 `stockhot/research_report/evidence.py` 的 `get_curated_theme_evidence(theme)` 获取已整理的分级证据包：

- 商业航天
- 卫星互联网
- AI 芯片
- 新能源
- 低空经济
- 消费电子

每个包含：`aliases`（主题别名匹配）、`headline`、`source_tiers`（分级来源）、`catalysts`（带日期+级别+来源的催化事件）、`industry_context`、`segments`（细分方向）、`milestones`、`targets`（具名 A 股标的 + 理由 + 来源级别）。

**使用原则**：证据包是起点，不是终点。第一版研报仅引用当前可核验样本，不扩展到未验证的细分产业链公司。

## 4. 数据采集纪律

- **每个数字标注来源 + 日期**——无来源的数字不可接受
- **交叉验证**——关键结论用 ≥2 个独立来源佐证
- **区分实算与假设**——引擎算出的标注"引擎计算"，手动估算的标注"估算"，查不到的标注"不可用"
- **>25% 数据缺失时停止**——报告数据缺口，不粉饰为完整
- **不复制旧报告表格**——重新取数，数据时效性是研报的生命线
- **优先一级证据**——政策原文 > 行业协会 > 媒体报道 > 市场信号
