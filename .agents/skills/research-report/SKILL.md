---
name: research-report
description: A 股研报写作流程 skill。当用户要求写研报、深度报告、产业链分析、个股分析、方法论报告、估值报告、或任何 docs/ 下的长篇中文研究文档时使用。涵盖从选题、数据采集、分析、写作、验证到提交推送的完整生命周期。Use whenever the user mentions 研报, 深度分析, 产业链, 个股分析, 估值报告, 方法论, or wants to produce a long-form Chinese research document — even if they don't explicitly say "研报".
---

# A 股研报写作流程

本 skill 是**写作流程编排器**，不重复估值/景气度的计算方法论（那些由专门的 skill 负责）。它管的是：选题 → 数据 → 分析 → 写作 → 验证 → 提交推送这条主线，确保每篇研报结构一致、数据可追溯、质量达标。

## 1. 核心原则（先读）

- **复用而非重写**：估值计算调 `davis_analyzer` 引擎；景气度方法论遵循 `industry-prosperity` skill；亏损标的估值遵循 `valuation-loss-making-targets` skill 的三角框架。本 skill 只负责编排和写作。
- **每个数字可追溯**：≥80% 的数字必须带具名来源标签，如 `(12.30, tushare income 20251231)` 或 `($260/lb, TTI MarketEye 2026Q1)`。无来源的数字一律视为不可信。
- **三情景概率加权，禁止单一目标价**：估值输出必须是悲观/中性/乐观三情景的概率加权区间，权重由客观信号调整。
- **诚实标注局限**：信息缺乏处明确标注"估算"或"不可用"，**未做任何编造**。>25% 数据缺失时停止，报告缺口而非粉饰。
- **没有提交就不算完成**：研报验证通过后必须 commit + push 到 `origin/master`。AGENTS.md 的 Post-Report Push Rule 是强制的。

## 2. 报告类型路由

确定报告类型是第一步，不同类型用不同模板。**读 `references/report-templates.md` 获取完整的章节结构**再动笔。

| 类型 | 触发场景 | 模板结构 | 长度 | 黄金标准 |
|------|----------|----------|------|----------|
| **个股深度研报** | "分析 XX 公司""XX 股票深度报告" | 景气度 11 章结构 | 12K-35K 字 | 阳光电源 v1.2 |
| **产业链研报** | "XX 产业链分析""XX 行业全景" | 景气度 6 节结构 或 传统 8 章 | 18K-47K 字 | 钽电容产业链 |
| **方法论研报** | "XX 方法论""XX 投资框架" | 固定 8 章结构 | 35K-50K 字 | A股景气度投资方法论 |
| **分析笔记/短篇** | "快速看看 XX""XX 涨停分析" | 轻量 5 节结构 | 5K-10K 字 | 易德龙分析 |

判断方法：用户提到一只具体股票 → 个股研报；提到一个行业/材料/技术 → 产业链；提到一种投资方法/框架 → 方法论；只要快速结论 → 分析笔记。不确定时问用户。

## 3. 执行流程（六阶段）

### Phase 1：选题与差异化

1. **确定报告类型**（见上表路由），读对应模板。
2. **差异化检查**（强制）：搜索 `docs/` 是否已有该标的/主题的报告。若有，明确声明本报告**新增了什么**——钽电容报告的"与既有报告的边界声明"是范例。同标的多份报告优先整合为主报告 + 附录存档。
3. **既有研报刷新检查**（强制）：若已有同标的深度研报，检查其最后版本日期 + 是否含"补充因子分析"章节。若距最后更新 >2 周 或缺因子章节，应作为**版本更新**（而非新建）：重跑 davis_analyzer 引擎刷新估值（PE/PB 分位用当日 daily_basic）+ 景气度（最新财务 prosperity）+ 补充因子分析（5 引擎），在版本历史表新增一行（如 v1.2 → v2.0）。**禁止对已有完整研报的标的另起独立因子分析文件**——必须更新主研报。
4. **写核心论点**（一段话）：在采集数据前就写下你的投资论点。论点驱动数据收集，不是反过来。

### Phase 2：数据采集

4. **结构化数据**（调引擎，不手算）：
   - **单股评分**：复制 `davis_analyzer/studies/tianyue_scoring.py` 改 `TS_CODE`，它展示完整的四维评分调用链（不要从零拼底层函数调用，签名复杂）
   - **全市场筛选**：`python -m davis_analyzer.cli run --top 30`
   - 引擎模块：`valuation`（PE/PB 分位）、`prosperity`（G+ΔG + DuPont）、`distress`（3 层 9 信号）、`scoring`（Davis 加权分）、`trend`（趋势）
   - **调用引擎时务必参照 `references/engine-usage.md`**——底层函数签名复杂、返回类型多样（list 而非 DataFrame、dataclass 而非 dict、字段名易记错如 report_period 而非 end_date），该指南含完整可运行模板和常见错误速查表，照着写不会踩坑
   - **数据来源分级体系见 `references/data-sourcing.md`**
   - **Tushare 底层接口可用性/调用模式见 `references/tushare-api-reference.md`**——列出了本项目实测验证可用的 A 股/财务/资金流/宏观/港股接口、新端点（api.tushare.pro/dataapi）调用方式、分批拉取策略、MCP fallback、频率限制与权限边界。直接调底层接口取特殊字段时必读

4.5. **数据时效性校验（强制，写报告前必做）**：取数前先确认数据新鲜度，避免「报告日 6/27 但财务数据是 Q1」却不标注的陷阱。对每个标的查三项：
   - **`daily_basic` 最新交易日** vs 报告日（应 ≤1 个交易日，估值数据要新）
   - **`income` 最新报告期 `end_date` + 披露日 `ann_date`**（确认是最新已披露季报——半年报 8 月底、年报 4 月底才披露，中途财务快照只能到上一季报）
   - **`forecast` 业绩预告**（H1/全年预告常先于正式财报披露，是更早的领先信号，必须纳入分析）
   - **时效边界必须写进报告**：财务快照标注「截至 YYYYQN（YYYYMMDD 披露），下季报 X 月披露」；若数据非报告日，先取最新再写。可复用代码模板见 `references/engine-usage.md` §10，实例见 `davis_analyzer/studies/wf6_freshness_check.py`。
4.6. **股东户数趋势分析（强制，每篇个股研报必备）**：股东户数（筹码集中度）是领先信号——户数减少=筹码集中=主力收集=上涨动能增强；户数增加=筹码分散=动能减弱。对每个标的调用 `pro.stk_holdernumber(ts_code, fields="ts_code,ann_date,end_date,holder_num")` 取近 4–8 期，**算环比变化并判断趋势**：
   - 户数逐期下降 → 筹码集中，动能增强 ✓（看多信号）
   - 户数逐期上升 → 筹码分散，动能减弱 ⚠（警示信号）
   - 写进报告：财务分析章节或独立「股东户数与筹码集中度」小节，附户数变化表 + 趋势判断。可补充 `pro.top10_floatholders` 的十大流通股东持股比例合计变化作交叉验证。可复用代码模板见 `references/engine-usage.md` §11。
4.7. **补充因子引擎产出（强制，每篇个股研报必备）**：davis_analyzer 集成了 5 个补充因子引擎，**每篇个股研报必须跑这 5 个引擎并把产出作为研报的一个专门章节（"补充因子分析"）嵌入**，不得作为独立文件产出。这取代了旧的"因子分析报告"独立文件做法。5 个引擎（调用模板见 `references/engine-usage.md` §9 PipelineResult 补充因子字段 + 单股按需调用）：
   - **`analyze_momentum(client, ts_code)`** → MomentumSignal：多窗口（60/120/250d）原始复权收益 + 行业 RS，区分"估值趋势"（trend.py）与"价格动量"。60d 涨幅饱和点 30%、120d 50%、250d 100%。
   - **`analyze_dividend(client, ts_code)`** → DividendSignal：连续派息年数（实施口径）+ 年化股息率。非派息股地板分 10。
   - **`analyze_forecast(client, ts_code, prosperity_score)`** → ForecastSignal：业绩预告前瞻景气分（0-100），含 ΔG 加速叠加。
   - **`analyze_holder_concentration(client, ts_code)`** → HolderConcentration：筹码集中度评分（0-100），趋势"集中/分散/数据不足"。
   - **`analyze_profitability_quality(fin_list)`** → ProfitabilityQuality：毛利率趋势 + 研发强度，区分"高质量增长"与"低质量增长"。纯计算无需 client。
   - 写进报告时每个因子给一个小节（表格+核心洞察），最后用"多因子信号方向汇总"表收束（得分/方向/解读）。**预告百分比若为亏损基数效应（如扭亏 +500%），必须标注"基数效应"而非真实增长。**
   - **文件位置强制**：因子分析作为研报章节嵌入，**禁止产出独立的"XX因子分析报告.md"文件**。若该标的无既有深度研报，必须先补一篇完整深度研报（含因子分析章节），不得只产出因子报告。
5. **行业/产业链数据**（手动研究）：产能、价格、交期、订单、供需平衡表、国产化率、国际同业财报。这些 Tushare 覆盖不了，必须 web 搜索 + 行业数据库。每个数字标注来源 + 日期。
6. **curated evidence packs**（可选）：若主题命中 `stockhot/research_report/evidence.py` 的 6 个主题之一（商业航天/卫星互联网/AI芯片/新能源/低空经济/消费电子），用 `get_curated_theme_evidence(theme)` 取分级证据包。

**详细的来源分级体系见 `references/data-sourcing.md`。**

### Phase 3：分析

7. **景气度定位**：遵循 `industry-prosperity` skill——G+ΔG 框架、山峰理论（Δg 符号定左/右山坡）、成长股投资时钟（4 象限）、六维指标、30% 阈值、二次点火筛选。G 和 ΔG 必须同时给出。
8. **估值分析**：
   - 盈利标的：PE+PB+PS 三角验证，历史分位表 + 三模型情景区间 + 概率加权目标价
   - 亏损标的：遵循 `valuation-loss-making-targets` skill 的三角框架（困境评分 + PS/DCF + 全球同业锚定），**禁止 PE/PEG 主估值**
   - **永远输出概率加权区间，禁止单一目标价**
   - **相对市场估值锚定（强制）**：每篇个股研报必须调用 `stockhot.valuation.analyze_relative_valuation()` 计算相对 PE 溢价率、ERP、PE-Band 象限，并插入"横向市场估值锚定"section。方法论详见 `references/relative-valuation.md`。这一步消除"个股 PE 中位但市场高位"的估值幻觉。
9. **横向对比**：至少 3 家可比（国内上市 + 国际上市 + 未上市/IPO 的），建对比表而非纯叙述。
10. **交叉验证**：关键结论用 ≥2 个独立来源佐证。

### Phase 4：写作

11. **开头元数据块**（所有类型必备）：
    ```markdown
    # {标题}：{洞察性副标题}

    > **研究日期**：YYYY-MM-DD | **股价/数据快照**：{关键数字}
    > **核心逻辑**：{3-5 句论点，含关键财务 + 拐点判断}
    > **数据来源**：{穷举具名来源列表}
    ```
12. **按类型模板写正文**（读 `references/report-templates.md`）。表格化呈现（每小节至少一个数据表）。核心洞察用 `> **核心洞察**：...` 引用块。

    **个股研报的三个"灵魂章节"必须详尽（参考德福科技黄金标准 `docs/个股研报/半导体电子/德福科技深度研报.md`）：**
    - **第一章 公司概览**：必须包含 **创始人/管理层背景**（姓名、学历、创业路径，web 搜索）+ **完整发展历程**（年份×里程碑表格，阶段划分）+ **业务布局**（基地/研发/客户/股权）。严禁只放基本信息表。
    - **第二章 业务与产品矩阵**：必须包含 **技术代际演进与技术发展方向**（当前代际位置 + 演进路径 + 未来技术布局）。
    - **第九章 横向对比**：必须做 **深度竞争者分析**（≥2 家逐一对比：核心差异+各自优劣势+财务对比，非简单堆表），最后汇总本标的优势与劣势排名。
    - 这三章的深度决定了研报能否回答"这是一家什么样的公司、它强在哪弱在哪"。
13. **结尾固定结构**：风险提示（≥4 条具体风险）→ 免责声明（AI 辅助 + 不构成投资建议 + 市场有风险投资需谨慎）→ 报告定位与下游引用 → 版本历史表。

### Phase 5：验证

14. **章节完整性**：必须章节齐全，无 `PART`/`待续`/`...未完` 标记。
15. **来源可追溯性**：统计 inline 来源标签密度，必须 ≥80%。
16. **计算复核**：引擎算出的分数与报告一致。
17. **差异化复核**：确认与既有同标的报告有结构差异。
18. **合规复核**：无买卖建议（表述为"矩阵结果"），无编造数据。

**用 `checklists/report-quality.md` 逐项检查后再提交。**

### Phase 6：提交推送（强制）

19. **更新 `docs/README.md` 索引**——新报告必须登记到对应分类表。
20. **中文文件名**——禁止英文文件名（如 `docs/ai算力全景研报.md`，不是 `docs/ai_power_report.md`）。
21. **commit 格式**：`feat(docs): add {short-description}`
22. **commit footer**（强制）：
    ```
    Ultraworked with [Sisyphus](https://github.com/code-yeongyu/oh-my-openagent)
    Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
    ```
23. **push 到 `origin/master`**——确认 push 成功后才算完成。"A report is not done until it is on GitHub."
24. **语音化**（可选但推荐）——md 报告是给「看」设计的，表格/ASCII 框图/百分位数字直接朗读无法听懂。语音化分两步，**先转口语化文本，再（可选）转音频**：

    **步骤 A（推荐）：转口语化文本**——把表格转成"净利润走过一个完整周期，从 0.2 亿反弹至 1.95 亿又跌回 0.6 亿"这样的叙述，而非逐行流水账：
    ```bash
    .venv/bin/python scripts/report_to_speech_text.py "docs/个股研报/.../某某深度研报.md"
    ```
    默认用 GLM（glm-4-flash）叙述化表格，LLM 不可用时自动降级规则版。输出到 `docs_speech/` 镜像目录（.txt，已 gitignore）。**完整转换规则、表格策略、防幻觉机制、踩坑速查见 `references/speech-conversion.md`。** `--no-llm` 可走纯规则降级（离线/省成本）。

    **步骤 B（可选）：转 MP3 音频**——把文本转语音：
    ```bash
    .venv/bin/python scripts/report_to_audio.py "docs/个股研报/.../某某深度研报.md"
    ```
    默认女声晓晓（zh-CN-XiaoxiaoNeural），语速 +10%，输出到 `docs_audio/` 镜像目录（MP3 不提交 git）。

    ⚠️ **注意**：`report_to_audio.py` 内置的 `clean_markdown()` 对表格只做逐行流水账处理（听感差）。要听口语化版本，应先用 `report_to_speech_text.py` 生成 .txt，再让 `report_to_audio.py` 处理该 .txt。

### 踩坑反馈（持续改进本 skill）

写研报过程中如果遇到引擎调用报错、模板不适配、数据源缺失、checklist 项模糊等**可复现的坑**，不要只绕过——把它沉淀回 skill，让下一个 agent 不重蹈覆辙：

- **引擎调用坑** → 在 `references/engine-usage.md` §8 常见错误速查表加一行（报错信息 → 原因 → 解法）
- **模板/章节坑** → 在 `references/report-templates.md` 对应模板处加约束说明
- **数据源坑** → 在 `references/data-sourcing.md` 补充来源限制或 fallback 方法
- **语音化坑** → 在 `references/speech-conversion.md` §7 踩坑速查表加一行（现象 → 原因 → 解法）
- **checklist 坑** → 在 `checklists/report-quality.md` 调整措辞或分类型

报告提交时，在 commit message 里注明是否更新了 skill 文件（如"附带更新 engine-usage.md 速查表"）。**绕过坑而不记录，等于让每个 agent 重新踩一遍。**

## 4. 与其他 skill 的关系

| Skill | 本 skill 何时调用它 |
|-------|---------------------|
| `industry-prosperity` | Phase 3 景气度定位——G+ΔG、山峰理论、六维指标 |
| `valuation-loss-making-targets` | Phase 3 亏损标的估值——三角框架、概率加权区间 |
| `daily-market-scan` | 不直接调用，但其采集的 `invest_*` 数据可供产业链报告参考 |
| `local-development-environment` | Tushare/AKShare 导入失败或数据库问题时 |

## 5. 绝对禁止

- **给单一目标价**——必须概率加权区间
- **编造数据**——无来源的数字不可接受
- **写买卖建议**——合规风险，表述为"矩阵结果"
- **复制旧报告表格而不重新取数**
- **用 PE/PEG 对亏损标的做主估值**
- **跳过验证直接提交**
- **提交后不 push**
- **产出独立的"因子分析报告"文件**——5 因子引擎产出必须作为研报章节嵌入（见 Phase 2 步骤 4.7）。若标的无既有深度研报，必须先补完整深度研报（含因子章节），不得只产出独立因子报告。同标的的因子数据与基本面分析必须在同一文件内，便于阅读。

## 6. 文件位置约定

- 研报产出目录：`docs/`，按类型分文件夹（`个股研报/{行业}/`、`产业链研报/`、`方法论/`、`分析笔记/`）
- 索引文件：`docs/README.md`（新增报告必须更新）
- 方法论参考：`docs/方法论/`（9 篇方法论文档，写作时引用）

## 7. 配套文件

- `references/engine-usage.md`——**davis_analyzer 引擎调用指南**（调引擎取数时必读，含完整模板 + 常见错误速查；踩坑后在此更新速查表）
- `references/tushare-api-reference.md`——**Tushare 底层接口参考**（直接调 `pro.<api>()` 取特殊字段时必读，含已验证接口清单、新端点调用模式、分批拉取、MCP fallback、频率/权限边界）
- `references/report-templates.md`——4 种报告类型的完整章节结构（写作前必读）
- `references/data-sourcing.md`——数据来源分级体系 + 采集方法 + 引用格式
- `references/relative-valuation.md`——**横向相对估值方法论**（每篇个股研报必须包含相对市场基准的估值锚定 section，用 `stockhot/valuation/` 模块自动计算相对PE溢价率/ERP/PE-Band象限）
- `checklists/report-quality.md`——提交前的质量检查清单（18 项）

## Source of Truth

如果本 skill 与实际报告产物（`docs/` 里的黄金标准）冲突，以实际产物为准。方法论细节以 `docs/方法论/` 为准；估值/景气度计算以 `davis_analyzer` 引擎为准。
