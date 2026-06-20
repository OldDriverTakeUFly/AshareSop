# Scan Completeness Checklist

当执行 daily-market-scan 盘面扫描时，在四个模块全部运行完毕后、向用户汇报结果前使用此清单。逐项确认每个模块已执行、失败已隔离、结果已持久化。

## 模块执行确认

- [ ] **limit_up 已执行？** — `results["limit_up"]` 存在，`status` 为 `success` 或 `no_data`（非交易日）或 `数据不可用`（失败）
- [ ] **dragon_tiger 已执行（依赖 limit_up）？** — `results["dragon_tiger"]` 存在。注意：dragon_tiger 是 limit_up 的逻辑下游，但代码层面不直接调用 limit_up，两者共享同一批热点股票的不同视角
- [ ] **fund_flow 已执行？** — `results["fund_flow"]` 存在。fund_flow 与 limit_up / dragon_tiger 完全独立
- [ ] **risk_alert 已执行（读取上游）？** — `results["risk_alert"]` 存在。risk_alert 必须最后运行，因为它通过 `get_daily_data(date)` 从数据库读取 `limit_up_pool`、`dragon_tiger_detail`、`fund_flow_sector`

## 失败隔离验证

- [ ] **每个模块都有独立的 try/except？** — 确认没有任何模块的 `run_*_analysis(date)` 调用未被 try/except 包裹
- [ ] **失败模块标记为"数据不可用"？** — 失败模块的 results 条目为 `{"date": date, "status": "数据不可用", "error": str(e)}}`
- [ ] **一个模块失败没有阻止其他模块执行？** — 逐个检查 results 字典，确认失败模块的条目存在但其他三个模块仍然执行
- [ ] **risk_alert 优雅降级？** — 如果上游模块失败，risk_alert 对应检测项返回空列表（得益于 `or []` 回退），不会崩溃

## 数据持久化确认

- [ ] **成功模块的数据已写入数据库？** — 每个成功模块通过 `save_daily_data` 和 `save_analysis_result` 持久化
- [ ] **数据库可被下游 skill 读取？** — `invest-sop-pre-market` 可通过 `get_daily_data(date)` 读取本 skill 写入的数据

## 结果汇总

- [ ] **已向用户报告每个模块的状态？** — 区分 `success`（成功）、`no_data`（非交易日）、`数据不可用`（错误）
- [ ] **失败模块已告知用户原因？** — 包含 error 字段的简要描述
- [ ] **未生成任何报告？** — 本 skill 是数据采集层，不产出 markdown 报告（如需报告，转交 `invest-sop-pre-market`）

## 判定

- [ ] 所有项目通过 → 扫描完成，数据可用于下游
- [ ] 任一模块 `数据不可用` → 告知用户哪个模块失败，建议稍后重试，其他模块数据仍可用于下游
