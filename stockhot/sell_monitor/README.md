# sell_monitor — 卖出时机监控

## 模块定位

持仓卖出时机监控模块。对单只持仓独立计算 4 个卖出信号，输出结构化
字段。作为 `stockhot` 后端的卖出决策数据层。

## 信号清单

| 函数 | 触发条件 | 输出 signal_type |
|------|----------|------------------|
| `check_hard_stop_loss` | 现价 ≤ 硬止损价 | `hard_stop` |
| `check_trailing_stop` | 现价 ≤ 移动止损线 | `trailing_stop` |
| `check_target_reached` | 现价 ≥ 目标价 | `target_reached` |
| `check_thesis_broken` | 百分位排名下降 > 20 | `thesis_broken` |

每个信号独立触发，无组合、无优先级仲裁。

## 不做的事

- **不做信号组合** — 4 个信号各自独立，不合并为单一决策
- **不做优先级仲裁** — 不在多信号同时触发时排序取舍
- **不做置信度评分** — 信号输出 `triggered: bool`，不附加概率
- **不做自然语言建议** — 不生成"建议卖出"等文字，仅输出结构化字段

## 数据依赖

- `holding` dict 字段来自 `invest_holdings` 表（`stop_loss_hard`、
  `target_price`、`position_pct` 等）
- `check_trailing_stop` 依赖
  `stockhot.technical_analyzer.indicators.ma`（T11 提供 MA20）
- `check_thesis_broken` 需要 `thesis_snapshot_json` 字段（T16
  schema 扩展提供）；字段缺失时优雅降级返回 SKIP

## 开发路线

| 任务 | 内容 | 状态 |
|------|------|------|
| T1 | 脚手架 + testpaths 配置 | 完成 |
| T3 | 信号 API 冻结 (`signals.py`) | 完成 |
| T14 | check_hard_stop_loss + check_target_reached 实现 | 待实现 |
| T15 | check_trailing_stop + check_thesis_broken 实现 | 待实现 |
| T16 | database.py schema 扩展 + 集成 | 待实现 |

## 运行测试

```bash
.venv/bin/python -m pytest stockhot/sell_monitor/tests/ -v
```
