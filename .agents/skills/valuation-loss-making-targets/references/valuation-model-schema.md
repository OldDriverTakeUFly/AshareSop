# 估值模型 JSON 结构规范

> 本文档描述估值模型 JSON 的抽象结构规范（字段名、类型、嵌套、可选性），不含任何具体数值或案例数据。所有数值以 `<number>`、`<string>`、`<ISO-timestamp>` 等占位符表示。

---

## 1. 顶层结构

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `model_version` | string | 是 | 模型版本号，如 `<major>.<minor>` |
| `modeled_at` | string (ISO 8601) | 是 | 模型生成时间戳，含时区偏移 |
| `target` | string | 是 | 标的证券代码，如 `<ticker>.<exchange>` |
| `target_name` | string | 是 | 标的公司全称 |
| `current_market_data` | object | 是 | 当前市场数据快照，见 §1.1 |
| `ps_valuation` | object | 是 | PS 估值模块，见 §2 |
| `dcf` | object | 是 | DCF 估值模块，见 §3 |
| `distress_adjustment` | object | 是 | 困境调整模块，见 §5 |
| `ev_capacity` | object | 否 | EV/产能估值（可选，重资产型标的适用） |
| `valuation_summary` | object | 是 | 情景汇总与概率加权目标价，见 §6 |

### 1.1 current_market_data

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `market_cap` | number | 是 | 当前总市值（亿元） |
| `ps_ttm` | number | 是 | TTM 市销率倍数 |
| `pb` | number | 是 | 市净率倍数 |
| `price_approx` | number | 是 | 近似现价（元） |
| `shares_total_m` | number | 是 | 总股本（百万股） |
| `source` | string | 是 | 数据来源说明 |
| `note` | string | 否 | 补充注释 |

---

## 2. PS 估值 (`ps_valuation`)

### 2.1 revenue_forecast（营收预测）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `revenue_forecast` | object | 是 | 以年份字符串为键的映射，见下方 |

每年份（`<YYYY>` / `<YYYY>E`）的值结构：

| 字段 | 类型 | 必填 | 适用年份 | 说明 |
|------|------|------|----------|------|
| `actual` | number | 是 | 历史年 | 实际营收（亿元） |
| `source` | string | 是 | 历史年 | 数据来源 |
| `pessimistic` | number | 是 | 预测年 | 悲观情景营收 |
| `neutral` | number | 是 | 预测年 | 中性情景营收 |
| `optimistic` | number | 是 | 预测年 | 乐观情景营收 |
| `growth_assumption` | object | 否 | 预测年 | 各情景增长率假设，键为 `pessimistic`/`neutral`/`optimistic`，值为描述性字符串 |
| `drivers` | string[] | 否 | 预测年 | 营收驱动因素列表 |

### 2.2 ps_multiples（PS 倍数区间）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `ps_multiples` | object | 是 | 各情景 PS 区间，见下方 |

每个情景（`pessimistic`/`neutral`/`optimistic`）的值结构：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `low` | number | 是 | PS 下限倍数 |
| `high` | number | 是 | PS 上限倍数 |
| `rationale` | string | 是 | 倍数设定依据 |

`ps_multiples.source`（顶层字段）：string，必填，倍数来源说明。

### 2.3 market_cap_matrix（市值矩阵）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `market_cap_matrix` | object | 是 | 以年份字符串为键，每年下含三情景 |

每年份下每个情景（`pessimistic`/`neutral`/`optimistic`）的值结构：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `revenue` | number | 是 | 该情景营收 |
| `ps_low` | number | 是 | PS 下限 |
| `ps_high` | number | 是 | PS 上限 |
| `market_cap_low` | number | 是 | 市值下限（亿元） |
| `market_cap_high` | number | 是 | 市值上限（亿元） |
| `market_cap_midpoint` | number | 是 | 市值中值（亿元） |

### 2.4 conclusion & vs_existing_report

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `conclusion.valuation_year` | string | 是 | 选定估值年份 |
| `conclusion.pessimistic_range` | string | 是 | 悲观情景市值区间描述 |
| `conclusion.neutral_range` | string | 是 | 中性情景市值区间描述 |
| `conclusion.optimistic_range` | string | 是 | 乐观情景市值区间描述 |
| `conclusion.current_premium_vs_neutral_mid` | number | 是 | 当前市值相对中性中值的溢价倍数 |
| `conclusion.note` | string | 否 | 补充说明 |
| `vs_existing_report.existing_ps` | string | 否 | 现有报告 PS 区间 |
| `vs_existing_report.new_ps` | string | 否 | 新模型 PS 区间 |
| `vs_existing_report.delta` | string | 否 | 差异说明 |

---

## 3. DCF 估值 (`dcf`)

### 3.1 assumptions（假设输入）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `gross_margin_trajectory` | object | 是 | 毛利率轨迹，以年份为键，值为百分比数值；含可选 `source` |
| `expense_ratio_trajectory` | object | 是 | 费用率轨迹，同上结构 |
| `tax_rate` | number | 是 | 所得税率（百分比） |
| `capex_trajectory` | object | 是 | 资本开支轨迹，同上结构 |
| `depreciation_as_pct_rev` | object | 是 | 折旧占营收比轨迹 |
| `wc_change_as_pct_rev` | number | 是 | 营运资金变动占营收比（百分比） |
| `revenue_neutral` | object | 是 | 中性情景营收轨迹 |

### 3.2 cashflow_forecast（自由现金流预测）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `cashflow_forecast` | object[] | 是 | 每年现金流对象数组 |

单年对象结构：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `year` | string | 是 | 年份标签 |
| `revenue` | number | 是 | 营收 |
| `gross_margin_pct` | number | 是 | 毛利率（百分比） |
| `expense_ratio_pct` | number | 是 | 费用率（百分比） |
| `ebit_margin_pct` | number | 是 | EBIT 利润率（百分比） |
| `ebit` | number | 是 | EBIT |
| `nopat` | number | 是 | 税后净营业利润 |
| `depreciation` | number | 是 | 折旧 |
| `capex` | number | 是 | 资本开支 |
| `wc_change` | number | 是 | 营运资金变动 |
| `fcf` | number | 是 | 自由现金流 |

### 3.3 wacc_decomposition（WACC 分解）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `risk_free_rate` | number | 是 | 无风险利率（小数） |
| `equity_risk_premium` | number | 是 | 股权风险溢价（小数） |
| `sic_industry_beta` | number | 是 | 行业 Beta |
| `raw_cost_of_equity` | number | 是 | 原始股权成本（小数） |
| `size_premium` | number | 是 | 规模溢价（小数） |
| `adjusted_cost_of_equity` | number | 是 | 调整后股权成本（小数） |
| `after_tax_cost_of_debt` | number | 是 | 税后债务成本（小数） |
| `debt_weight` | number | 是 | 债务权重（小数） |
| `equity_weight` | number | 是 | 股权权重（小数） |
| `implied_wacc` | number | 是 | 隐含 WACC（小数） |
| `existing_report_wacc` | number | 否 | 现有报告使用的 WACC |
| `note` | string | 否 | 说明 |
| `sources` | object | 否 | 各参数来源说明映射 |

---

## 4. 5x5 敏感性矩阵 (`dcf.sensitivity_matrix`)

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `dimensions.rows` | string | 是 | 行维度：WACC 5 个值（递增） |
| `dimensions.cols` | string | 是 | 列维度：永续增长率 5 个值（递增） |
| `matrix` | object[] | 是 | 5 行数组，每行代表一个 WACC 值 |
| `cell_count` | number | 是 | 单元格总数（通常 25） |
| `fcf_input` | number[] | 是 | 输入的自由现金流序列（与 cashflow_forecast 对应） |

`matrix` 单行结构：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `wacc_pct` | number | 是 | 该行对应的 WACC（百分比） |
| `cells` | object[] | 是 | 5 个单元格，每个对应一个永续增长率 |

`cells` 单元格结构：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `perpetual_growth_pct` | number | 是 | 永续增长率（百分比） |
| `market_cap` | number | 是 | 对应市值（亿元） |
| `per_share_value` | number | 是 | 每股价值（元） |
| `is_existing_report_point` | boolean | 否 | 标记是否为现有报告基准点 |
| `existing_report_dcf_result` | string | 否 | 现有报告 DCF 结果区间 |

### 4.1 existing_report_point（现有报告基准点）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `wacc` | number | 是 | 现有报告 WACC（小数） |
| `perpetual` | number | 是 | 现有报告永续增长率（小数） |
| `position` | string | 是 | 矩阵坐标描述 |
| `market_cap_at_point` | number | 是 | 该点市值 |
| `existing_report_range` | string | 是 | 现有报告区间描述 |

---

## 5. 困境调整 (`distress_adjustment`)

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `distress_score` | number | 是 | 综合困境得分（0-100） |
| `layer_scores` | object | 是 | 三层得分：`L1`（困境确认）、`L2`（反转基础）、`L3`（拐点信号），均为 number |
| `delta_g` | number | 是 | 增速变化值（百分点） |
| `base_probabilities` | object | 是 | 基准概率，见 §5.1 |
| `adjustments` | object[] | 是 | 概率调整项数组，见 §5.2 |
| `adjusted_probabilities` | object | 是 | 调整后概率，结构同 base_probabilities |
| `sum_check` | number | 是 | 概率和校验（应为 1.0） |
| `reasoning` | string | 是 | 调整逻辑文字说明 |
| `distress_score_usage_count` | number | 是 | 困境得分被引用次数 |

### 5.1 base_probabilities / adjusted_probabilities

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `pessimistic` | number | 是 | 悲观情景概率（小数，0-1） |
| `neutral` | number | 是 | 中性情景概率（小数，0-1） |
| `optimistic` | number | 是 | 乐观情景概率（小数，0-1） |

### 5.2 adjustments（调整项数组元素）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `factor` | string | 是 | 触发因子描述（含得分与阈值判断） |
| `effect` | string | 是 | 调整方向与幅度描述 |
| `evidence` | string | 是 | 证据来源 |

---

## 6. 情景汇总与概率加权目标价 (`valuation_summary`)

### 6.1 区间汇总

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `ps_range_2027E` | object | 是 | PS 估值区间（三情景），值为描述性字符串 |
| `dcf_range` | object | 是 | DCF 估值区间（三情景），值为描述性字符串 |
| `combined_per_scenario` | object | 是 | 各情景综合市值（数值，亿元），键为 `pessimistic`/`neutral`/`optimistic` |
| `scenario_probabilities` | object | 是 | 情景概率（来自 distress_adjustment），结构同 §5.1 |

### 6.2 probability_weighted_target（概率加权目标价）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `market_cap` | number | 是 | 概率加权目标市值（亿元） |
| `per_share` | number | 是 | 概率加权每股目标价（元） |
| `vs_current` | number | 是 | 相对现价的变化比例（负数表示折价，小数） |
| `interpretation` | string | 是 | 结果解读文字 |

### 6.3 风险与催化剂

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `risk_factors` | string[] | 是 | 风险因素列表 |
| `catalysts` | string[] | 是 | 催化剂列表 |
| `bottom_line` | string | 是 | 核心结论文字 |

---

## 7. EV/产能估值（可选，`ev_capacity`）

> 仅适用于重资产型标的（如半导体衬底、制造业等）。轻资产或服务型标的可省略此模块。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `current_capacity_wafers` | number | 是 | 当前产能（万片/吨等实物量） |
| `enterprise_value` | number | 是 | 企业价值（亿元） |
| `ev_per_wafer_10k` | number | 是 | EV/万片产能倍数 |
| `ev_per_wafer_unit` | string | 是 | 单位标注 |
| `note` | string | 否 | 计算说明 |
| `capacity_forecast` | object | 是 | 产能预测，以年份为键 |
| `peer_comparison_note` | string | 否 | 同业对比说明 |
| `rationale` | string | 否 | 方法适用性说明 |

`capacity_forecast` 单年结构：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `capacity_design` | number | 是 | 设计产能 |
| `actual_output` / `estimated_output` | number | 是 | 实际/预估产量 |
| `sales` | number | 否 | 销量（历史年适用） |
| `source` / `rationale` | string | 否 | 数据来源或预测依据 |

---

## 字段命名约定

- 年份键：历史年用 `<YYYY>` 或 `<YYYY>A`，预测年用 `<YYYY>E`
- 概率值：统一用小数（0-1），百分比类字段以 `_pct` 后缀或 `pct` 标注
- 金额单位：市值/营收/EBIT 等默认"亿元"，每股价值为"元"
- 三情景命名固定为 `pessimistic` / `neutral` / `optimistic`
