# Decision Matrix Reference

This document supplements `SKILL.md` §4 (Workflow A, steps 4–5). It is the analytical core that `generate_premarket_report.py` §3 alludes to but does not compute — the four-dimension evaluation and the two-matrix decision logic are applied by the agent/human when filling the holding-decision table.

Source of truth: `.sisyphus/drafts/a-share-pre-market-sop.md` §5 (持仓管理决策矩阵). Quoted verbatim where tables are normative.

## 1. Four-Dimension Evaluation

For each active holding, assess four dimensions before consulting the matrices.

### 维度一：投资逻辑状态

| 状态 | 定义 | 判断方法 |
|------|------|----------|
| ✅ 逻辑完好 | 买入理由仍然成立 | 行业周期位置未变、基本面趋势延续 |
| ⚠️ 逻辑动摇 | 买入理由出现不确定性 | 出现矛盾信号，但尚不足以否定 |
| ❌ 逻辑破坏 | 买入理由已被证伪 | 核心假设被推翻、行业逻辑发生根本改变 |

**Data cue:** check `invest_supply_chain` for the holding's sector and `invest_domestic_events` for thesis-relevant events. Use `invest_holdings.thesis_snapshot_json` (if populated) as the original thesis baseline.

### 维度二：事件冲击评估

| 级别 | 触发条件 | 典型案例 |
|------|----------|----------|
| 🟢 无影响 | 无相关事件或事件影响中性 | — |
| 🟡 轻微负面 | 短期情绪影响，不改变基本面 | 外围市场跟跌、板块轮动 |
| 🟠 中度负面 | 可能影响短期走势，需观察 | 行业政策微调、季度业绩不及预期 |
| 🔴 严重负面 | 可能改变基本面逻辑 | 行业政策重大转向、核心逻辑被证伪 |

**Data cue:** filter `invest_domestic_events` by `affected_sector` matching the holding's sector and by `severity`.

### 维度三：技术状态

| 状态 | 定义 | 关键信号 |
|------|------|----------|
| 强势 | 上升趋势完好 | 站稳20日均线以上，成交量配合 |
| 震荡 | 无明确方向 | 均线缠绕，缩量横盘 |
| 弱势 | 下降趋势 | 跌破20日均线，放量下跌 |
| 关键位 | 接近重要支撑/压力 | 距离关键支撑/压力位<3% |

**Data cue:** compare `invest_holdings.current_price` against `entry_price`, `stop_loss_hard`, `stop_loss_technical`, `target_price`. The four stop/target columns give the key levels; "关键位" triggers when current price is within 3% of any of them. Intraday MA/RSI/MACD are not in the DB — flag for manual check.

### 维度四：行业周期位置

| 位置 | 对应操作倾向 |
|------|------------|
| 复苏期 | 积极，可加仓 |
| 繁荣期 | 持有，逐步止盈 |
| 衰退早期 | 减仓，设紧止损 |
| 衰退晚期 | 观望或轻仓试探 |

**Data cue:** look up the holding's sector in `invest_cycle_assessments.cycle_position`. Values are `复苏` / `繁荣` / `衰退早期` / `衰退晚期`. Combine with `crowding_score` (0–12; ≥7 means crowded, reduces aggressiveness).

## 2. Matrix A — Logic × Event → Base Operation

Apply first. Yields the base operation before technical/cycle adjustment.

| | 事件🟢无 | 事件🟡轻微 | 事件🟠中度 | 事件🔴严重 |
|------|----------|----------|----------|----------|
| **逻辑✅完好** | 持有/加仓 | 持有观察 | 持有+收紧止损 | 减仓30% |
| **逻辑⚠️动摇** | 持有观察 | 持有+收紧止损 | 减仓30-50% | 减仓50-70% |
| **逻辑❌破坏** | 减仓50% | 减仓70% | **清仓** | **清仓** |

## 3. Matrix B — Technical × Cycle → Adjustment

Apply second, on top of Matrix A's base operation.

| 技术状态 | 周期位置 | 操作调整 |
|----------|----------|----------|
| 强势 + 复苏期 | → | 矩阵A结果可上调一级（持有→加仓） |
| 强势 + 繁荣期 | → | 维持矩阵A结果，但设定止盈位 |
| 震荡 + 任何 | → | 维持矩阵A结果 |
| 弱势 + 衰退期 | → | 矩阵A结果下调一级（持有→减仓） |
| 跌破关键支撑 + 任何 | → | 立即减仓至少30% |

## 4. Reduction / Clearance Execution Standard

From Matrix A's operation, the execution urgency is defined by SOP §5.3.

### 减仓执行

| 减仓幅度 | 触发条件 | 执行方式 |
|----------|----------|----------|
| 减仓30% | 逻辑动摇+事件中度负面 / 技术破位 | 开盘后30分钟内执行 |
| 减仓50% | 逻辑动摇+事件严重 / 逻辑破坏+事件轻微 | 开盘后15分钟内执行 |
| 减仓70% | 逻辑破坏+事件中度 | 竞价阶段即开始减仓 |
| 清仓 | 逻辑破坏+事件严重 | 不计成本，竞价阶段清仓 |

### 止盈执行

| 止盈类型 | 触发条件 | 执行方式 |
|----------|----------|----------|
| 目标止盈 | 达到预设目标价（或风险收益比达标） | 分批止盈：先卖出1/2，剩余设跟踪止盈 |
| 周期止盈 | 行业进入繁荣后期（拥挤度>7分） | 逐步减仓，每周至少减20% |
| 逻辑止盈 | 买入逻辑已充分兑现（如预期的事件已发生） | 全部止盈或留底仓 |
| 技术止盈 | 出现顶部信号（放量滞涨、高位十字星、MACD顶背离） | 先减50%，设跟踪止盈 |

## 5. How This Maps to the Report

`generate_premarket_report.py`'s §3 emits a per-holding table with rows for 买入逻辑 / 逻辑状态 / 事件影响 / 技术状态 / 周期位置 / **操作决策** / 止损价 / 目标价 / 执行方式. The generator fills only the price-related rows (止损价, 目标价 from `invest_holdings` columns). The other rows — including the four-dimension assessments and the Matrix A/B result that goes into **操作决策** — are **placeholder text** (`✅完好 / ⚠️动摇 / ❌破坏`, `强势/震荡/弱势`, `持有/减仓/加仓/清仓`).

Filling those rows is the agent's job when executing this skill:

1. Read the four dimensions' data cues above (§1) for the holding.
2. Classify each dimension.
3. Apply Matrix A → base operation.
4. Apply Matrix B → adjustment.
5. Write the final operation into the **操作决策** row, phrased per SKILL.md §5 as "矩阵结果：..." (e.g. "决策矩阵结果：减仓30%"), never as a direct buy/sell command.

## 6. Position Limits (apply when filling §6 风控检查)

SOP §7.1 hard limits, used to validate the report's §6 risk-control section:

| Limit | Value |
|-------|-------|
| 单票仓位 | ≤ 25% |
| 单板块仓位 | ≤ 40% |
| 持仓数量 | ≤ 8 只 |
| 最小止损距离 | ≥ -12%（hard stop） |
| **市场波动率状态** | **≥3 指数 RV20 P90+ → 全局降仓一档（系统性恐慌）** |

Sector-specific stop-loss/target overrides come from `invest_sector_rules` via `config.get_sector_rule(sector)` (default: stop_loss=-12%, target=+20%).

**市场波动率状态行**（2026-07-06 新增）由 `generate_premarket_report.py` 的 `build_section_6` 自动从 `get_daily_data(date)['volatility']` 填充，非人工填写。判定规则（方法论研报 §8.2 四档行动框架）：

| 信号 | 触发条件 | 行动 |
|------|---------|------|
| 绿色（平静） | RV < P50，V/R < 1.1 | 正常持仓 |
| 黄色（警惕） | RV P50-P75，或风格分化 > 20P | 减仓高波成长 |
| 橙色（关注） | RV P75-P90，V/R 1.1-1.3 | 启动左侧关注 |
| 红色（恐慌极值） | **≥3 指数 RV≥P90 + V/R>1.3** | **全局降仓一档**，分批建仓需等政策底 |

结构性恐慌（仅创业板/科创 P90+，蓝筹正常）不触发全局降仓，标注"关注风格切换"。

## 7. Scope Note

This reference documents the **analytical methodology** the SOP prescribes. The §3 holding four-dimension + Matrix A/B evaluation remains a manual/agent step. The §6 风控检查表的市场波动率状态行**已自动填充**（`build_section_6` 从 `volatility` DB key 读取），其余行仍为占位。
