# limitup Phase 3 设计规格 —— first_board 实践腿（盘后候选清单 + paper_trading 接入）

> 状态：v1.0，待用户审阅
> 门控依据：`davis_analyzer/limitup/reports/phase2_conclusion.md`——first_board 三档成交敏感性全正（最悲观档 5.5 年 +5,993%/年化 114%）+ OOS 同向，是唯一过门控的战法；relay_2 不确定、relay_3 证伪，均不入本阶段。
> 前置数据：`limitup daily` 每日刷新已挂 cron 19:20（moneyflow/top_list/intraday/corp_event 全部日更），本阶段所需特征当日可得。

## 1. 目标与非目标

**目标**：
1. **盘后候选清单**（candidates CLI）：每个交易日 19:30 输出次日的 first_board 打板候选（含风险提示与大单×强封标注）。
2. **paper_trading 接入**：BoardChasingStrategy 进入模拟盘每日运行，**双臂对照**验证「大单主导 × 强封单」增强过滤（不写死进策略）。
3. **校准**：量化回测模型与模拟现实的偏差（卖出滑点、成交口径、期望衰减幅度）。
4. **补课**：形态与 regime 阈值的 ±20% 扰动检验（终审延后项，Phase 3 前置任务）。

**非目标**：实盘下单、盘中实时信号、relay_2/relay_3 重启、成交概率模型的逆向选择校准（需真实挂单数据，本阶段只能标注边界）、策略参数寻优。

## 2. 交付物 A：candidates CLI（盘后候选清单）

### 2.1 命令与调度

```
python -m davis_analyzer.limitup candidates --date YYYYMMDD [--top 10]
```

cron：`30 19 * * 1-5`（在 daily 刷新 19:20 之后），日志 `davis_analyzer/limitup/logs/candidates.log`。支持 `--date` 回补任意历史日。

### 2.2 计算口径（与回测严格同源，保证校准有效性）

- 复用 `build_events`（单日窗口）+ `attach_pattern_features` + `build_market_regime`（近 60 日窗口，当日行）。
- 候选过滤 = first_board 预设原样：板数==1 + 形态(突破型/横盘首板型) + regime(回暖/高潮) + 利空排雷。**不加封单中位过滤**（C2 修复后的正确口径）。
- 卖出结构标签：join 当日 moneyflow，lg_sell_share = (大单+特大单卖出额)/总卖出额（阈值同调研：≥0.50 大单主导）。**标注用，不过滤**。
- 增强标注：`enhanced = (卖出结构==大单主导) and (seal_ratio>=0.05)`（调研的强封单档）。

### 2.3 输出（`reports/candidates_{date}.md`）

1. 当日情绪档位与三轴摘要（涨停家数/晋级率/溢价/指数多空）。
2. 候选表：代码/名称/板块/形态标签/封单比/首封时间档/炸板次数/卖出结构/enhanced 标记，按 seal_ratio 降序，默认前 10。
3. 每条候选的**风险提示**：一字板概率档（引擎 fill_probability 的档位值）、封单比档（弱/中/强）、若当日曾炸板标注回封次数。
4. 「增强标注」独立小节：命中大单×强封的候选单独列出（Phase 1 调研该组合晋级率 46.3%、样本 160）。
5. 免责口径声明：基于日线近似（EOD 特征做当日决策），与回测同口径。

## 3. 交付物 B：paper_trading 接入

### 3.1 BoardChasingStrategy（paper_trading/strategy.py 新增类）

- 实现 `Strategy` Protocol：`evaluate(positions, snapshot, total_equity) -> list[Signal]`；注册进 `create_strategy` 工厂。
- **双名注册避免参数机制改造**：工厂注册 `board_chasing`（enhanced_filter=False）与 `board_chasing_enhanced`（True）两个策略名，同一实现类实例化两次——不动 paper_trading 现有的策略名传参链路。
- 信号生成：当日 snapshot 中命中 first_board 口径的标的 → BUY（打板）；持仓标的次日 → SELL。特征计算 import limitup 模块（单日 events 复用），不复刻逻辑。
- **参数化增强臂**：`enhanced_filter: bool = False`。True 时在 first_board 口径上叠加 `enhanced`（大单主导×强封）过滤——两臂用同一份代码，只有这个开关不同。

### 3.2 executor 口径对齐（小扩展，规格内明确）

回测语义是「T 日涨停价买入 → T+1 开盘卖出」，而 `DailyExecutor` 现状是当日收盘价双边成交。扩展两点（向后兼容，不影响既有策略）：
1. **打板买入**：收盘涨停时收盘价==涨停价，executor 现有收盘成交 + `_limit_up_fill_probability` 概率折减已天然近似，买入侧不改。
2. **次日开盘卖出**：`Signal` 增加可选属性 `sell_at_open: bool = False`；`DailyExecutor.run_day` 在处理卖出时，若持仓带该标记，以**当日开盘价**（daily_price 当日 open）成交并加滑点 10bps，替代收盘价。开盘价缺失（一字跌停/停牌）顺延，语义与回测引擎一致。

### 3.3 双臂对照运行（增强过滤的验证协议）

| 账户 | 策略名 | enhanced_filter |
|---|---|---|
| `fb_base` | board_chasing | False |
| `fb_enhanced` | board_chasing_enhanced | True |

- 初始化：`python -m davis_analyzer.paper_trading init --name fb_base --strategy board_chasing --capital 1000000`（enhanced 账户同理）。
- 每日运行挂 cron `40 19 * * 1-5`（candidates 之后，同一数据底座）。
- **转正/否决标准**（粗粒度先验，禁统计检验过度设计）：增强臂积累 **≥50 笔成交或 ≥250 个交易日**（以先到者为准）后评审——样本 <30 笔时一律维持基准不结论；≥30 笔且增强臂单笔均值优于基准臂方向一致 → 允许把增强过滤合入预设；否则维持基准。期间只观察不调参。
- 对照报告：`python -m davis_analyzer.paper_trading report` 既有输出 + 每周人工比对一次两臂（均值/胜率/回撤/成交率）。

### 3.4 校准目标与诚实边界

模拟盘每笔交易记录后，与「同日回测引擎的理论值」逐笔对照，量化三项：
1. **卖出滑点**：实际成交 vs 当日开盘价的偏离（回测假设 10bps）。
2. **期望衰减**：模拟盘滚动 30 笔均值 vs 回测 base 档同口径均值（phase2 结论：真实预期应锚定悲观档，验证衰减系数）。
3. **成交率**：模拟盘的概率折减成交次数 vs 候选总数（对照引擎档位 5%/20%/35%/70%）。
**边界声明**：模拟盘的成交仍是概率模型，**逆向选择**（真实挂单成交往往因为板在松动）无法在模拟盘校准——该项升级为实盘前的最后未知数，Phase 3 报告必须持续携带此声明。

## 4. 补课任务：形态与 regime 阈值 ±20% 扰动

终审延后项。扩展 `study.py`：对形态四档阈值（0.98/0.25/0.15-0.40/-0.30/0.20）与 regime 阈值（-0.02/30/7/120/0/0.30）各做 ±20% 扰动重跑晋级率与收益分布，输出方向稳定性标记；接入 `cmd_study` 新小节。任一核心结论在扰动下翻负 → 下调 phase2 结论置信度并在模拟盘报告注明。

## 5. 模块结构

```
davis_analyzer/limitup/candidates.py     # 交付物 A 核心（复用 events/patterns/sentiment/strategies）
davis_analyzer/limitup/cli.py            # +candidates 子命令
davis_analyzer/paper_trading/strategy.py # +BoardChasingStrategy（注册工厂）
davis_analyzer/paper_trading/executor.py # +sell_at_open 开盘卖出扩展
davis_analyzer/limitup/study.py          # +阈值扰动检验（补课）
tests/test_limitup_candidates.py
tests/test_paper_board_chasing.py
cron: 30 19 candidates / 40 19 paper run（fb_base + fb_enhanced）
```

## 6. 测试策略

- candidates：单日合成夹具 → 候选过滤/排序/enhanced 标注/风险提示字段逐项断言；空候选日（冰点档）输出说明而非报错。
- BoardChasingStrategy：mock snapshot → 信号 action/权重/理由断言；enhanced 开关两臂输出差异断言。
- executor sell_at_open：次日开盘价成交+滑点；一字跌停顺延；缺开盘价顺延。
- 扰动检验：合成事件 × 已知阈值 → dir_stable 标记断言（复用 robustness.direction_stable）。

## 7. 风险与开放问题

1. **模拟盘成交仍是模型**：能校准价格路径与滑点，不能校准逆向选择（§3.4 边界）。
2. **enhanced 臂样本积累速度**：调研中大单×强封首板 5.5 年仅 160 例（约 0.12 笔/日），叠加成交概率后模拟盘预计每月 1-3 笔——50 笔需一年以上，故判定门槛设为「≥50 笔或 ≥250 交易日，先到为准；<30 笔不结论」（§3.3）。若积累过慢，可把对照观察降级为月度快照报告，不影响基准臂运行。
3. paper_trading 账户存储在 stockhot.db（paper_* 表），与 market_data.db 分离——策略读特征连 market_data.db，双连接生命周期在 executor 内管理。
4. candidates 与 paper run 依赖 19:20 daily 刷新成功；刷新失败日（API 故障）两任务应检测数据日期并跳过当日（输出告警），不使用陈旧数据生成信号。
