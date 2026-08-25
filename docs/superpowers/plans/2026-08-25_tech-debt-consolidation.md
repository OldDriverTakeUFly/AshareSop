# 技术债三大工程开发规划（2026-08-25）

> 状态：规划稿，待用户批准后按阶段立项实施。
> 前置：2026-08-22~25 已完成清理/修复批次（pickle 湖、scheduler、yoy 对齐、
> live_monitor 实时价、cooldown 持久化、`_pro` 私有调用收敛），本文档只覆盖
> 剩余三大工程。现状证据基于 2026-08-23 三路核实 + 08-25 补充扫描。

## 〇、优先级与依赖关系

| 工程 | 工作量 | 风险 | 建议优先级 | 依赖 |
|---|---|---|---|---|
| 一、Tushare 客户端统一 | 中 | 中（生产采集路径） | **P1 先行** | 无 |
| 二、双库停写 | 大 | 高（盘后总结全链路） | P2 | 工程一 P1 完成后再动（避免采集层并发重构） |
| 三、双 FastAPI | 小 | 低 | 降级：文档化现状，不合并 | 无 |

理由：工程一的 P1（safe_tushare_call 收编）是纯机械替换、独立可交付、
且不完成它就直接做工程二会让采集层同时承受两套重构。工程三功能不重叠、
用户不同（davis_webui 服务估值引擎 8322 / stockhot/api 服务盘面四模块 8321
且当前 docker 未跑），合并收益低于风险，建议只补文档边界说明。

---

## 一、Tushare 客户端统一（三套 → 一套底座）

### 现状

| 客户端 | 传输 | 限流 | 重试 | 缓存 | 使用面 |
|---|---|---|---|---|---|
| `davis_analyzer.tushare_client.TushareClient` | tushare SDK | 400/min 单线程滑窗 | 3 次指数退避 | 结构化 SQLite(market_data.db) | backtest/valuation/quality_factor/davis_webui/20+ studies |
| `stockhot.data_layer.tushare_gateway.TushareGateway` | 直连 HTTP | 400/min 线程安全滑窗 + 5/s | 错误分类(权限错不重试) | 无(委托 repository) | 12 个文件(macro/valuation/eod_review…) |
| `stockhot.core.tushare_client_safe.safe_tushare_call` | 直连 HTTP | 5/s | 2 次线性退避 | 无 | limit_up / dragon_tiger(2处) / fund_flow(2处) / risk_alert / technical_analyzer |

另：`stockhot/tushare_config.py` 的 `_ProApi`（裸 POST、无限流、出错静默返
空 DataFrame）仍被 ~65 个一次性研究脚本使用（研究沙盒设计取舍，本工程不追溯）。

### 目标终态

- **唯一传输底座** = TushareGateway（HTTP + 分页 + 线程安全限流 + 错误分类）；
- davis `TushareClient` 保留，**对外 API 与缓存接口不变**，内部传输从 tushare
  SDK 切换为 gateway 调用（成为「Gateway + 缓存」的包装层）;
- `safe_tushare_call` 与 `_ProApi` 生产路径清零后删除。

### 阶段分解

**P1：收编 safe_tushare_call（1 个采集周，风险最低先行）**
- 任务：6 个调用点机械替换为 `get_gateway().call(...)`（limit_up、
  dragon_tiger 的 top_list/top_inst、fund_flow 的 moneyflow_mkt_dc 等、
  risk_alert、technical_analyzer/data_loader）。
- 适配注意：safe_tushare_call 返回「出错时空 DataFrame」；gateway 出错抛
  异常——调用方都有 try/except 包裹，需逐一确认 except 分支行为等价。
- 验证：① 单测（每模块现有测试全过）；② 盘后采集窗口实跑一轮，
  对比替换前后 `scan_log` 行数与 stockhot 日志 error 数；③ 连续观察 3 个
  交易日无 WARN 升级。
- 回滚：git revert 单 commit。

**P2：消除 stockhot→davis 交叉 import（半天）**
- `overseas_market_data.py:120,160` 两处 `TushareClient` → gateway
  （需给 gateway 补 `hk_daily` 透传，`__getattr__` 代理已天然支持，仅确认
  字段映射）。
- 验证：`rg "from davis_analyzer" stockhot/` 清零（run_daily_scan 已于
  08-25 收敛）。

**P3：TushareClient 内部传输切 Gateway（2-3 天，核心工程）**
- 任务：`TushareClient._call` 的 API 调用从 `self._pro.xxx()` 改为
  `self._gw.call("xxx", ...)`；分页由 gateway 的 paginate 透传；限流统一
  走 gateway（删除 TushareClient 自己的 400/min 滑窗，避免双重限流叠加）。
- 不变式：`_call(endpoint, func, params)` 签名不动 → 20+ 调用方零改动；
  缓存读写逻辑（stock_basic/daily_price/financial 三层 TTL）零改动。
- 风险点：SDK 与 HTTP 返回的 DataFrame 字段/类型差异（重点回归：
  income/cashflow/fina_indicator 的 dtype、日期格式）。
- 验证：① davis 全量 1115 测试；② 用 10 个代表性 endpoint 做 SDK vs
  HTTP 双跑 diff（行数/列集/数值逐项）；③ 一个 davis_nightly 周期实跑
  无 error；④ 确认 tushare SDK 限流计数从共享池摘除（避免 gateway 独占
  400/min 后与 limitup daily_refresh 19:20 撞限流——必要时错峰）。
- 回滚：TushareClient 内部 revert（对外接口无变化，回滚零成本）。

**P4：清理（半天）**
- 删 `stockhot/core/tushare_client_safe.py`、`tushare_config._ProApi`
  的生产引用（研究脚本若仍用可保留该文件并加 deprecation 头注释）；
- 全景报告技术债表 #2 关闭。

---

## 二、双库停写（stockhot.db daily_data JSON blob 退役）

### 现状

- **写端**：`save_daily_data`（stockhot/storage/database.py:369）9 个调用方
  （limit_up/dragon_tiger/risk_alert/fund_flow/volatility/sector_volatility/
  index_technical/run_daily_scan/data_collector），每日双写 JSON blob。
- **读端**：`get_daily_data` 10 个消费方——volatility/analyzer、
  eod_review/push_eod_feishu（盘后总结推送）、api/db + 4 routers（8321，
  docker 未跑）、hotspot_discovery、repository、ai_analyzer。
- market_data.db 对应结构化表已建好且有数据（limit_pool 134,539 行 /
  dragon_tiger / fund_flow_market 等，2021 起完整）。
- **不在范围**：stockhot.db 的 paper_trades(8 万行)/analysis_results/
  advisor_runs/invest_*——独立迁移线，非 daily_data 双写债。

### 阶段分解

**P1：读端切换（2-3 天，最大未知数在读端语义映射）**
- 任务：`get_daily_data(date)` 内部改从 market_data.db 结构化表组装
  （保持返回 dict[data_type] 的 JSON 兼容形态，**10 个消费方零改动**）。
- 关键工作：14 种 data_type ↔ 结构化表的字段映射表（先写映射文档，
  逐 type 对照双库样本验证）；缺 type 的降级策略（返回空，消费方已有
  缺失容忍）。
- 验证：① 映射对照脚本：30 天样本逐 data_type diff 双库数据；② 盘后
  总结（18:30）新旧双跑一份报告 diff（飞书只推旧路径）；③ ZCode 的
  after-hours-review/盘前 SOP 各跑一次 dry-run。
- 回滚：get_daily_data 单点 revert。

**P2：写端观察期（1 周）**
- save_daily_data 继续写（作为影子对照），但读已全部走结构化表；
- 每日 diff 脚本核对双库行数；异常即回滚读端。
- 此阶段零风险：读端已被 P1 验证。

**P3：停写拆除（1 天）**
- 9 个调用方移除 save_daily_data 调用（保留采集与 market_data.db 主写）；
- 删 stockhot.db daily_data 表的增量写入路径。
- 验证：一个完整采集周（daily_scan 18:00 / limitup 19:20 / 影子 19:55 /
  盘后总结 18:30 / 盘前 08:00）全部正常。

**P4：历史归档（半天，可选）**
- daily_data 历史 JSON 导出压缩存档（stockhot/backup/），表清空；
- stockhot.db 体积从 35MB 显著下降；全景报告技术债 #7 关闭。

---

## 三、双 FastAPI（降级：文档化，不合并）

决策记录：davis_webui(8322, systemd 常驻, 估值引擎) 与 stockhot/api
(8321, docker 未启, 盘面四模块+盘前 SOP) 功能零重叠、消费场景不同，
合并的唯一收益是「少一个栈」，代价是路由/config/auth 三套合并回归。
**维持现状**，仅在全景报告补边界说明。若未来 stockhot/api 需要重启，
优先考虑挂载为 davis_webui 的子路由（uvicorn 单服务多 app）再做评估。

---

## 四、里程碑与节奏

| 里程碑 | 内容 | 预计 | 前置条件 |
|---|---|---|---|
| M1 | 工程一 P1+P2（safe 收编 + 交叉 import 清零） | 3-4 个工作日 | 无 |
| M2 | 工程一 P3+P4（TushareClient 换底座 + 清理） | 3-4 个工作日 | M1 稳定一周 |
| M3 | 工程二 P1（读端切换） | 3 个工作日 | M1 完成（采集层稳定） |
| M4 | 工程二 P2→P4（观察/停写/归档） | 2 周（含观察期） | M3 验证 |

总量约 5-6 周日历时间（含两个观察窗口）。每阶段独立 commit、独立可回滚，
不与并行 session 的策略/研究工作抢文件（工程一动 stockhot 采集层与 davis
数据层，工程二动 stockhot/storage——均避开 paper_trading/limitup/tournament）。

## 五、全局风险与原则

1. **限流池合并后余量**：三套限流合一后 400/min 为全局共享——需重新核算
   19:00-21:30 夜间高峰的调用预算（davis_nightly + limitup refresh + 影子
   验证），必要时错峰（工程一 P3 验证④）。
2. **不在盘中/长回测运行期间动采集层**：所有替换部署选在周末或盘后
   观察窗口，遵守长回测硬性规范第 4 条。
3. **每阶段留双跑对照**：涉及生产数据的改动，新旧路径并跑 ≥1 个采集周，
   diff 通过后才拆旧路径（工程一 P3、工程二 P2 均内置）。
4. **回滚单点化**：每阶段改动收敛在尽量少的 commit 里，revert 即回滚。
