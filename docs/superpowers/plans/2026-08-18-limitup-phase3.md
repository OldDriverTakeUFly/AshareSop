# limitup Phase 3 实施计划（first_board 实践腿）

> **For agentic workers:** 按任务顺序执行，每任务独立 TDD+提交。规格：`docs/superpowers/specs/2026-08-17-limitup-phase3-design.md`（含卖出侧规则矩阵 §3.2.1/§3.2.2）。

**Goal:** first_board 盘后候选清单 + paper_trading 双臂接入 + 两项前置研究（阈值扰动补课、open_hold_locked 可观测变体）。

**Global Constraints:** 同 Phase 0-2（父仓库根目录运行、`.venv/bin/python -m pytest`、loguru 无 print（仅 cli.py）、完整类型注解、不碰 stockhot 源码与 constants.py、Conventional Commits 中文 scope、测试只用 :memory: fixture 不连真实库）。

---

### Task 1: 阈值扰动检验（形态+regime ±20%）

**Files:** Modify `limitup/patterns.py`（classify_from_prices 阈值参数化，默认值=现先验常量，行为不变）、`limitup/study.py`（新增 `threshold_perturbation`）、`limitup/cli.py`（cmd_study 新小节）、Test `tests/test_limitup_study.py` 追加。

**Interfaces:**
- `classify_from_prices(events, prices, *, thresholds: dict | None = None) -> pd.DataFrame`：thresholds 键 `breakout_close(0.98)/breakout_box(0.25)/accel_lo(0.15)/accel_hi(0.40)/oversold(-0.30)/consolidation(0.20)`，None 用默认（冻结先验，参数化仅为扰动检验）。
- `study.threshold_perturbation(events, prices, regime) -> pd.DataFrame`：两个核心结论 × 阈值组 ±20%（robustness.perturb_factors）：
  - 结论1「突破型晋级率 > 其他」：扰动 {breakout_close, breakout_box} 后重分类 pattern_label → 晋级率差
  - 结论2「高潮档 ret_open_1 均值 > 其他档」：扰动 {premium(-0.02), hot_count(120), hot_boards(7)} 后重算 regime_label → 收益差
  - 输出列：结论, 基准差, 扰动0.8x差, 扰动1.2x差, dir_stable（robustness.direction_stable）
- cmd_study 新小节「形态与 regime 阈值扰动稳定性（±20%）」（study 里已有 seal 扰动节之后），需要 events+prices+regime 传递（cmd_study 现流程已有 events/regime，prices 需保留传递）。

**Acceptance:** 合成数据测试两组结论的 dir_stable 断言；全量 pytest 绿；真实跑 cmd_study 输出新节。

### Task 2: open_hold_locked 可观测卖出变体 + 三档研究结论

**Files:** Modify `limitup/engine.py`（新 ExitRule + 主循环分支）、`limitup/strategies.py`（无需新预设——研究脚本用 replace 注入）、Test `tests/test_limitup_engine.py` 追加。产出 `reports/open_hold_locked_study.md`。

**Interfaces:**
- `ExitRule.OPEN_HOLD_LOCKED = "open_hold_locked"`：T+1 开盘执行卖出时，若当日 open≈当日涨停价（容差 0.005，仅用开盘价，无前视）→ 取消本次卖出、转入 ride 循环（sell_on=None，其后每日收盘涨停持有/断板次日开盘卖）；否则正常 T+1 开盘卖。
- 测试：①T+1 开盘=涨停价→持有，断板次日开盘卖；②T+1 开盘低于涨停价→T+1 开盘卖；③一字跌停顺延不受影响。
- 研究（控制器执行）：first_board × 该变体全窗口+OOS 三档对照 → `open_hold_locked_study.md`，按规格 §3.2.1 判定（通过且 IS/OOS 一致才考虑替代基准，否则记录丢弃）。

### Task 3: candidates.py 核心

**Files:** Create `limitup/candidates.py`、Test `tests/test_limitup_candidates.py`。

**Interfaces:**
- `build_candidates(conn, date: str, *, enhanced_filter: bool = False, lookback_days: int = 60) -> pd.DataFrame`
  - events：`build_events(conn, <date-lookback_days 自然日>, date)` 后筛 `trade_date == date`（保证 prev_limit_count_60/形态窗口正确）
  - regime：`build_market_regime(conn, <同窗口>, date)` 取当日行
  - 过滤 = first_board 预设口径（`apply_preset(events, PRESETS["first_board"], regime=当日单行)`）
  - 增强：moneyflow join 当日 → `lg_sell_share`、`enhanced = (lg_sell_share>=0.50) & (seal_ratio>=0.05)`（标注列，不参与过滤；enhanced_filter=True 时仅返回 enhanced 子集）
  - 风险列：`fill_prob`（engine.fill_probability base 档）、`封档`（seal_ratio 弱/中/强）、`炸板次数`
  - 数据新鲜度：当日 limit_pool 无行 → 返回空帧 + logger.warning（CLI 层据此告警退出）
- 测试：合成单日夹具 → 过滤/排序(seal_ratio 降序)/enhanced 标注/enhanced_filter 子集/空数据告警。

### Task 4: candidates CLI + 报告 + cron

**Files:** Modify `limitup/cli.py`、Test 追加（parser/渲染断言）。cron 由控制器挂。

**Interfaces:**
- `python -m davis_analyzer.limitup candidates [--date YYYYMMDD] [--top 10]`（--date 默认= daily_price 最新交易日）
- 输出 `reports/candidates_{date}.md`：①当日 regime 三轴摘要行 ②候选表（代码/名称/板块/形态/封单比/封档/首封档/炸板/卖出结构/enhanced）③增强标注节 ④每候选「次日执行提示」（§3.2.2 定制：一字/上板持有+炸板即卖；低开走弱开盘卖）⑤免责声明
- 空候选：输出「当日无候选（regime=X）」正常退出；数据缺失：告警 + 退出码 1

### Task 5: executor sell_at_open 扩展

**Files:** Modify `paper_trading/strategy.py`（Signal 加 `sell_at_open: bool = False`）、`paper_trading/executor.py`（卖出执行分支）、Test `tests/test_paper_board_chasing.py`（或就近新建）。

**Interfaces:**
- Signal 新字段默认 False（既有策略零影响）
- executor 卖出执行：signal.sell_at_open=True → 成交价=当日 open×(1−10bps)（daily_price 当日 open；读价走 executor 现有 `_get_close_prices` 同源路径扩展 `_get_open_prices`）；open 缺失或当日一字跌停（open=low=跌停价）→ 顺延（不卖，保留持仓，日志）
- 测试：开盘价成交+滑点；缺 open 顺延；sell_at_open=False 走原收盘路径（回归）。

### Task 6: BoardChasingStrategy 双名注册

**Files:** Modify `paper_trading/strategy.py` + `executor.py`（create_strategy 注册，若工厂在 executor）、Test 追加。

**Interfaces:**
- `class BoardChasingStrategy`：`__init__(self, enhanced_filter: bool = False)`；`evaluate(positions, snapshot, total_equity) -> list[Signal]`
  - 持仓 → SELL（sell_at_open=True，signal_reason="T+1开盘卖"）
  - 未持仓候选 → BUY（target_weight=1/max_positions(=3)，action 沿用现有 Signal 语义；reason 含形态/封档/enhanced 状态）
  - 候选来源：`limitup.candidates.build_candidates(conn, snapshot 当日, enhanced_filter=self._enhanced)`；conn 由策略内 `db.connect()` 短生命周期管理
  - 候选空/数据缺失 → 无信号（日志）
- 注册：`board_chasing`（False）/ `board_chasing_enhanced`（True）
- 测试：mock build_candidates（monkeypatch）→ 两名策略的信号差异断言；空候选无信号。

### Task 7: 部署与端到端验证（控制器执行）

1. 真实跑 `candidates --date <最新>` 验证报告；真实跑当日 `open_hold_locked` 三档研究（Task 2 产出）。
2. `paper_trading init` 两账户 fb_base/fb_enhanced（capital 1,000,000）。
3. cron 两行：`30 19 * * 1-5` candidates、`40 19 * * 1-5` paper run（两账户，命令对齐 paper_trading CLI 现有用法）。
4. 真实跑一次两账户 run_day 验证全链路；全量 pytest；提交。
