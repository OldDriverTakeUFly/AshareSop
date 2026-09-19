# Paper Trading 前向实盘测试系统 — 需求规格

> **文档目的**：本文档是 paper_trading 前向实盘测试系统的完整需求规格，供独立 task 实施补全。
> 编写日期：2026-07-24。基于对 `davis_analyzer/paper_trading/` 的深度调研。
>
> **目标**：建立独立模拟账户，用 screen_top20 真实选股信号驱动前向交易，验证 davis/screen 探索出的交易因子在实盘中的表现，并提供日内异动监控（盘中 4 次推飞书）。

---

## 一、现状与缺陷（动手前必读）

### 1.1 paper_trading 现有架构（可复用的基础设施）

paper_trading 已是一套**经过回测验证、含 14 个安全阀、cash/整手/成本/NAV 全齐**的完整虚拟交易系统：

| 模块 | 文件 | 职责 |
|------|------|------|
| `account.py` | `davis_analyzer/paper_trading/account.py` | 虚拟账户（cash/持仓/交易成本/NAV 快照），DB 持久化 |
| `executor.py` | `davis_analyzer/paper_trading/executor.py`（1957 行） | 执行器（风控/整手/涨停成交模型/T 交易/影子交易） |
| `strategy.py` | `davis_analyzer/paper_trading/strategy.py`（730 行） | 策略（DavisDoubleStrategy / FactorThresholdStrategy） |
| `cli.py` | `davis_analyzer/paper_trading/cli.py` | CLI（init/run/backfill/live/report/list） |
| `backtest.py` | `davis_analyzer/backtest.py` | 回测引擎（Portfolio.rebalance 两阶段算法、_trade_cost） |

**数据库表**（`stockhot/storage/database.py:238-308`，均在 `stockhot.db`）：
- `paper_accounts`：账户（name UNIQUE / strategy_name / initial_capital / cash / config_json）
- `paper_positions`：持仓（account_id / ts_code / shares / avg_cost / entry_date / signal_reason）
- `paper_trades`：交易记录（account_id / trade_date / ts_code / action BUY|SELL / shares / price / amount / cost）
- `paper_nav_history`：净值快照（account_id / trade_date UNIQUE / cash / positions_value / total_equity / daily_return）

### 1.2 ⚠️ 关键缺陷：`run` 命令（前向模式）跑不出 BUY 信号

这是实施前必须理解的最重要问题：

- `cmd_run`（`cli.py:94-118`）调 `executor.run_day(trade_date)` 时**不传 `factor_scores`**（默认 `None`）。
- `run_day`（`executor.py:1430-1434`）在 `factor_scores is None` 时直接 `factor_scores = {}`，**从不调用** `_get_factor_scores` / `_get_davis_scores`（定义于 executor.py:96-146 但**全仓库无调用方**，`_get_davis_scores` 内部直接 `return {}`）。
- 结果：snapshot 里的 `factor_scores` 和 `davis_scores` 都是空 dict → 策略候选循环无东西可遍历 → **只产生 SELL/HOLD，永不产生 BUY**。
- 唯一能跑出完整买卖的是 `backfill` / `run_backfill_auto`（executor.py:1775-1875），它在循环内主动 `_compute_davis_scores_at` + `_compute_factor_scores_at` 注入 snapshot。

**含义**：要实现"每日收盘后用当日选股信号驱动前向交易"，**现有 `run` 命令无法直接使用**，必须自行把选股信号注入 snapshot。

### 1.3 screen_top20 字段与 paper_trading 策略的字段不兼容

| screen_top20 JSON 输出（`studies/screen_top20.py:258-274`） | DavisDoubleStrategy 期望（`strategy.py:123-184`） |
|---|---|
| `composite / momentum / valuation / prosperity / distress / delta_g / persistence_bonus / target_price / stop_loss_technical / current_price` | `snapshot.davis_scores[ts_code]["final_score"]` + `"name"` + `"rank"` |

两套字段名完全对不上，需要桥接层。

### 1.4 日内分钟级数据未接入

代码库目前只有两条个股行情路径：
- 全市场快照 `stock_zh_a_spot_em`（AKShare，实时，盘中可用）→ 当日累计涨跌幅
- 日线历史 `pro.daily` / `pro_bar`（Tushare，缓存于 daily_price 表）→ 收盘价

**没有分钟线/分时/盘口接口接入**（grep `stock_zh_a_hist_min_em` / `stock_intraday_em` / `stock_bid_ask_em` 全仓 0 命中，`AKSHARE_ENDPOINTS.md` 也未记录）。要做分钟级急跌/放量异动，需先 POC 验证这些 API 在本网络可用。

---

## 二、需求一：日级调仓执行器（独立模拟账户）

### 2.1 账户初始化（一次性）

建立独立前向测试账户，全现金起步，与现有回测账户数据分离：

```bash
python -m davis_analyzer.paper_trading init \
  --name live_factor_test \
  --strategy davis_double \
  --capital 1000000 \
  --top-n 5 --frequency 1 --min-score 60
```

- `davis_double` 策略：轮动型，消费 `final_score`，最简（推荐起步）
- `top_n=5`：小批量起步（后续可扩到 10）
- `frequency=1`：每日评估调仓
- `min_score=60`：低于 60 分不建仓（买入阈值）
- cash 自动 = initial_capital（account.py:92-102）

**安全阀配置**（复用 paper_trading 已验证的，无需新增）：
- max_positions 动态缩减（bear→0 / neutral→半仓 / bull→满仓 ×vol_mult）
- max_single_position_pct=12%（单只封顶）
- cooldown_days=5（卖出后 5 交易日不回买）—— ⚠️ 注意：paper_trading 用内存 dict 存 cooldown，跨进程会丢失；前向每日 cron 重启需持久化（查 paper_trades 最近 sell date）
- 整手对齐 100 股、停牌保护、涨停成交概率模型、现金不足裁剪

### 2.2 选股信号注入器（核心新建）

**新增脚本** `studies/inject_screen_to_paper.py`：

**职责**：读当日 screen_top20 选股结果，桥接字段后注入 executor，驱动前向调仓。绕过 `run` 命令的残缺路径。

**流程**：
1. 读 `studies/output/top20_screen_<date>.json`
2. 字段桥接：`{r["ts_code"]: {"final_score": r["composite"], "name": r["name"], "rank": i}} for i, r in enumerate(top20)`
3. 加载账户：`PaperAccount.load("live_factor_test")`
4. 创建策略：`create_strategy(account.strategy_name, account.config)`
5. 实例化 executor：`DailyExecutor(account, strategy)`
6. 调用：`executor.run_day(trade_date, factor_scores={"_davis_scores": 桥接dict})`
7. 幂等：executor 内部已有 `has_run_on` 检查（executor.py:1417-1419）

**接口契约**（已确认）：
- `executor.run_day(trade_date, factor_scores=...)` 接受 `factor_scores` 参数
- `DavisDoubleStrategy.evaluate`（strategy.py:123-184）消费 `snapshot.davis_scores[ts_code]["final_score"]`
- 注入 `factor_scores={"_davis_scores": {code: {"final_score": ..., "name": ..., "rank": ...}}}` 即可让策略识别候选

**安全保证**：
- executor 全部 14 个安全阀自动生效（不重写金融逻辑）
- 模拟账户与 invest_holdings 完全分离，零污染

**验证**：
- `--dry-run` 模式：打印将注入的信号 + 桥接结果，不实际调仓
- 实跑一次确认账户从全现金建第一批仓

### 2.3 cron 接入

在 `stockhot/invest_sop/crontab.txt` 追加（screen 跑完后、daily_scan 前）：

```
# 17:25 – 注入选股信号到前向测试账户（screen 跑完后）
25 17 * * 1-5 cd /home/leo/Projects/CodeAgentDashboard && PYTHONPATH=/home/leo/Projects/CodeAgentDashboard .venv/bin/python studies/inject_screen_to_paper.py >> stockhot/invest_sop/logs/paper_inject.log 2>&1
```

完整日级时序：
```
16:30  screen_top20         选股（~45min）
17:20  sync_to_watchlist    候选池
17:25  inject_to_paper      调仓执行（本需求）← 新增
17:30  daily_scan           盘后扫描
```

---

## 三、需求二：日内异动监控器（盘中 4 次推飞书）

### 3.1 目标

盘中 4 次（10:30/11:30/13:30/14:30）用 AKShare 实时价检查模拟账户 + 手动持仓的异动，触发推飞书。增加日内灵敏度。

### 3.2 新增脚本

**新增** `stockhot/invest_sop/scripts/intraday_holdings_alert.py`：

**仿 `run_panic_alert.py` 结构**（`stockhot/invest_sop/scripts/run_panic_alert.py` 是现成模板）：

1. **交易日校验**：`is_trading_day(today)`，非交易日 return 0（`stockhot/invest_sop/utils/trading_calendar.py`）
2. **拉实时行情**：`safe_akshare_call(ak.stock_zh_a_spot_em)`（`stockhot/core/rate_limiter.py:98`，限速+重试+代理剥离），返回 DataFrame 含 `代码/名称/最新价/涨跌幅/所属行业`
3. **读持仓**：
   - 模拟账户：`paper_positions WHERE account_id=(live_factor_test)`（含 stop_loss/target 需从 paper_trades 或注入信号推导，paper_positions 无止损字段——见 3.5）
   - 手动持仓：`invest_holdings WHERE status='active'`（含 stop_loss_hard/target_price）
4. **三类信号检查**（每只持仓）：
   - **止损触发**：`check_hard_stop_loss(holding, 实时价)`（`stockhot/sell_monitor/signals.py:21`）→ 实时价 ≤ stop_loss_hard
   - **目标触发**：`check_target_reached(holding, 实时价)`（`signals.py:113`）→ 实时价 ≥ target_price
   - **涨跌幅异动**：当日涨跌幅 ≥ +7% 或 ≤ -7%（接近涨跌停，从 spot_em 的 `涨跌幅` 列取）
5. **去重**（止损/目标当日首次才推）：用模块级 `set` 或查 scan_log
6. **格式化**（示例）：
   ```
   ⚠️ 盘中持仓异动 | 2026-07-24 10:30

   🔴 止损触发：
   • 扬杰科技(300373) 现价 90.2 ≤ 止损 92.51（距止损 -2.5%）

   🎯 目标触发：
   • 利通电子(603629) 现价 360.5 ≥ 目标 354.88（+1.6%）

   📈 涨幅异动：
   • 晶方科技(603005) 当日 +8.2%
   ```
7. **推送**：`get_feishu_notifier().send_text(msg)`（`stockhot/notification/feishu_bot.py:282`，复用现成，asyncio.run 包裹）
8. **无触发不推**（同 panic_alert：无信号 return 0）
9. **日志**：`repo.log_scan(module_name="intraday_holdings_alert", ...)`（`stockhot/data_layer/repository.py:703`）

### 3.3 cron 接入

```
# 盘中持仓异动监控（与 panic_alert 同时间但不同脚本：本脚本看个股持仓）
30 10,11,13,14 * * 1-5 cd /home/leo/Projects/CodeAgentDashboard && PYTHONPATH=/home/leo/Projects/CodeAgentDashboard .venv/bin/python stockhot/invest_sop/scripts/intraday_holdings_alert.py >> stockhot/invest_sop/logs/intraday_holdings.log 2>&1
```

### 3.4 关键复用点（已确认可用）

| 组件 | 位置 | 用途 |
|------|------|------|
| `safe_akshare_call` | `stockhot/core/rate_limiter.py:98` | 限速+重试+代理的 AKShare 调用 |
| `check_hard_stop_loss(holding, price)` | `stockhot/sell_monitor/signals.py:21` | 止损检查（接受实时价参数） |
| `check_target_reached(holding, price)` | `signals.py:113` | 目标检查（接受实时价参数） |
| `get_feishu_notifier()` | `stockhot/notification/feishu_bot.py:282` | 飞书推送工厂（企业自建应用已配 .env） |
| `_push_feishu` 范本 | `run_panic_alert.py:166` | 推送 thin wrapper |
| `is_trading_day` | `trading_calendar.py` | 交易日校验 |
| `repo.log_scan` | `repository.py:703` | scan_log 写入 |

### 3.5 ⚠️ 待解决：模拟账户持仓的止损/目标价来源

`paper_positions` 表**没有 stop_loss / target_price 字段**（只有 ts_code/shares/avg_cost）。而日内监控需要止损/目标价。三个方案：

- **方案 A（推荐）**：从 screen_top20 JSON 反查（候选池 watchlist 已存了 target_entry_high/stop_loss_pct，sync_screen_to_watchlist 已写入）
- **方案 B**：用 avg_cost × 固定百分比（如 -12% 止损 / +25% 目标，仿 invest_sector_rules）
- **方案 C**：给 paper_positions 加 stop_loss/target 列（改 schema，迁移）

建议 A：复用 watchlist 已算好的价位（sync_screen_to_watchlist 已把 screen 的 target_price 写进 watchlist.target_entry_high，stop_loss_technical 算成 pct 写进 stop_loss_pct）。

---

## 四、不做（后续迭代）

1. **分钟级急跌/放量**：需先 POC 验证 `stock_zh_a_hist_min_em`（1/5/15 分钟 K 线）在本网络可用，代码库当前未接入
2. **实盘券商自动下单**：模拟账户足够验证因子表现，实盘下单是另一层级风险
3. **飞书富文本/卡片**：纯文本足够，feishu_bot 目前只支持 send_text
4. **cooldown 持久化重构**：paper_trading 当前用内存 dict 存 cooldown，前向每日 cron 会丢失——短期可接受（每日评估不会立即回买刚卖的），长期需持久化到 DB

---

## 五、验证清单

### 日级执行器
- [ ] `init` 建账户成功，cash=1000000，positions 空
- [ ] inject `--dry-run`：正确读 top20 JSON + 桥接字段 + 打印将注入的信号
- [ ] inject 实跑：账户从全现金建第一批仓（≤5 只），NAV 快照写入 paper_nav_history
- [ ] inject 幂等：同日再跑显示 skipped（has_run_on）
- [ ] cron 条目安装

### 日内监控
- [ ] `--dry-run`：用当前持仓（扬杰科技）+ 实时价，正确检测止损/目标（扬杰止损 92.51，现价若 ≤92.51 触发）
- [ ] 无触发时不推、有触发时格式化正确
- [ ] 去重逻辑（止损/目标当日首次才推）
- [ ] cron 条目安装

---

## 六、相关文件索引（实施时参考）

### paper_trading 基础设施（复用，不改）
- `davis_analyzer/paper_trading/account.py`（账户：create L81/load L112/buy L188/sell L261/market_value L349/record_nav L367/has_run_on L443）
- `davis_analyzer/paper_trading/executor.py`（run_day L1409、_check_risk_signals L1207、_RISK_RULES L1146、整手在 account 层）
- `davis_analyzer/paper_trading/strategy.py`（STRATEGY_REGISTRY L717、create_strategy L723、DavisDoubleStrategy.evaluate L123）
- `davis_analyzer/paper_trading/cli.py`（init L57、STRATEGY_REGISTRY 校验 L63）
- `davis_analyzer/backtest.py`（Portfolio.rebalance L139、_trade_cost L243）

### 选股信号链（输入源）
- `studies/screen_top20.py`（选股，每日 16:30 跑，输出 top20 JSON）
- `studies/output/top20_screen_<date>.json`（选股结果，字段见 1.3）
- `studies/sync_screen_to_watchlist.py`（候选池同步，已把 target_price 写入 watchlist）

### 日内监控复用（不改）
- `stockhot/invest_sop/scripts/run_panic_alert.py`（入口模板）
- `stockhot/sell_monitor/signals.py`（止损/目标检查）
- `stockhot/notification/feishu_bot.py`（飞书推送）
- `stockhot/core/rate_limiter.py`（safe_akshare_call）
- `stockhot/invest_sop/scripts/update_holdings.py`（AKShare 实时价范本 L79-97）

### 配置/调度
- `stockhot/storage/database.py`（paper_* 表 schema L238-308、invest_holdings L151）
- `stockhot/invest_sop/crontab.txt`（cron 配置）
- `stockhot/data_layer/repository.py`（log_scan L703）
- `.env`（飞书企业应用 FEISHU_APP_ID/SECRET/CHAT_ID 已配置）

---

> 本文档基于 2026-07-24 的代码库深度调研编写，所有文件路径/行号/字段名均已核实。实施时如遇与文档不符，以代码实际为准。
