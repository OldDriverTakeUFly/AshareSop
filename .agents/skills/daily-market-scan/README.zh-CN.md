# 日常盘面扫描 Skill

本 skill 是四个 `stockhot` 模块的编排包装层，用于每日 A 股盘面数据采集。

## 定位

**数据采集层。** 只负责按正确顺序调用四个模块、隔离失败、把结果写入数据库。不生成报告，不做决策分析。

下游的 `invest-sop-pre-market` skill 读取本 skill 采集的数据，生成盘前报告。

## 四大模块

| 模块 | 入口函数 | 功能 |
|------|----------|------|
| 涨停分析 | `stockhot.limit_up.run_limit_up_analysis(date)` | 涨停池、炸板池、跌停池、连板梯队、板块联动、封单强度 |
| 龙虎榜 | `stockhot.dragon_tiger.run_dragon_tiger_analysis(date)` | 龙虎榜明细、机构席位、营业部、游资追踪 |
| 资金流向 | `stockhot.fund_flow.run_fund_flow_analysis(date)` | 大盘资金流、板块资金流、趋势判断（方向/动能/大小单背离） |
| 风险提示 | `stockhot.risk_alert.run_risk_alert_analysis(date)` | ST 股票、停复牌、异常波动、资金出逃、高位连板风险 |

## 执行顺序

```
Wave 1: 涨停分析（基础上游，写入 limit_up_pool）
    ↓
Wave 2: 龙虎榜 + 资金流（并行，各自写入数据库）
    ↓
Wave 3: 风险提示（读取前面三个模块的数据库输出）
```

风险提示模块通过 `get_daily_data(date)` 从数据库读取上游数据，所以必须最后执行。

## 快速开始

```python
from stockhot.limit_up import run_limit_up_analysis
from stockhot.dragon_tiger import run_dragon_tiger_analysis
from stockhot.fund_flow import run_fund_flow_analysis
from stockhot.risk_alert import run_risk_alert_analysis

date = "2025-06-20"
results = {}

# 每个模块独立 try/except，一个失败不影响其他
try:
    results["limit_up"] = run_limit_up_analysis(date)
except Exception as e:
    results["limit_up"] = {"date": date, "status": "数据不可用", "error": str(e)}

# ...其余模块同理
```

完整编排代码见 `SKILL.md` 第 4 节。

## 失败隔离

每个模块用独立的 `try/except` 包裹。任何一个模块失败时：

1. 标记为 `数据不可用`
2. 继续执行其他模块
3. 最终汇总时报告哪些成功、哪些失败

风险提示模块内置 `or []` 回退，上游模块失败时对应检测项返回空结果，不会崩溃。

## 局限性

- 所有阈值固定在模块源码中，不对外暴露配置（高位连板阈值 = 3，趋势回看天数 = 5）
- 炸板池/跌停池接口仅支持最近 30 天
- 涨停池接口排除 ST 和科创板（688xxx）
- 非交易日所有模块返回 `no_data`
- 无重试机制，接口临时不可用时标记 `数据不可用` 并提示用户稍后重试

## 与其他 skill 的关系

| Skill | 关系 |
|-------|------|
| `invest-sop-pre-market` | 下游消费者，读取本 skill 采集的数据生成盘前报告 |
| `valuation-loss-making-targets` | 无关，用于个股估值分析 |
| `local-development-environment` | 环境搭建，AKShare 导入失败或数据库缺失时先跑这个 |
