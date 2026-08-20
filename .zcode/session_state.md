# Session State（上次 session 结束时的状态快照）

> 使用：每次 session 结束前（或大功能完成后）更新此文件。
> 下次 session 开始时 session-context hook 自动注入，省去重新探索。

## 最近完成的工作

<!-- 记录 3-5 条最近完成的功能/修复，格式：[日期] 描述 + commit hash -->
<!-- 详细过程/根因/证据链见 docs/开发记录/ -->

- [2026-08-20] 尾盘 14:40 实时价轮动（intraday_rotation，挂载 intraday_manager，19:00 inject 降级兜底）— `94814a8`
- [2026-08-20] 选股宇宙剔除退市/停牌股（339 只 D 股污染 top20 霸榜 + advisor 变慢主因）— `ae099c7`
- [2026-08-20] 模拟仓科创板 200 股下限 + 小资金可买性递补（account/executor/strategy）— `8788167` + `bf1d975`(搭车)
- [2026-08-20] 盘前报告 advisor cron 08:15→05:00（2.5-2.8h 全量打分赶不上盘前）+ 盘前策略表 latest.json 陈旧引用修复 — `803fadd`
- [2026-08-19] 双模拟仓（主仓100万+小仓10万）多账户监控 — `68de988`
- 📋 全程记录：docs/开发记录/2026-08-19~20_恐慌预警链路与模拟仓体系修复.md

## 进行中 / 未完成

<!-- 有什么做到一半的？下次要接着做什么？ -->

- 观察尾盘轮动首个交易日运行（intraday_manager.log 搜"尾盘轮动"，收盘摘要应报"已完成"）
- root 属主 __pycache__ 目录待用户 sudo chown（仅性能，命令在开发记录§五）

## 已知问题 / 待办

<!-- 遗留的 bug、技术债、待验证的事项 -->

- **ZCode 沙箱禁 setgid → `crontab` 命令在沙箱内永远 Permission denied（spool 没坏，勿再误诊）**；改 cron 让用户终端执行或授权非沙箱
- paper_trades.created_at 是 UTC（=本地-8h），排查日志先换算
- inject 链路对 factor_threshold 账号结构性 0 信号（只传 _davis_scores）；mini_100k 已切 davis_double 规避，正修需 executor"缺什么补什么"
- 东财 push2 被 Clash Verge 代理屏蔽 → 已切新浪源降级，东财恢复后自动切回
- 新浪降级实时源不含北交所 → 尾盘轮动对北交所标的自动顺延/跳过
- screen_top20 受 Tushare 限流可能跑到 18:30+ → paper_inject 已推迟到 19:00

## 系统架构速查

```
数据库：
  market_data.db — 行情/波动率/板块/事件日历
  stockhot.db     — 持仓(paper_positions)/交易(paper_trades)/账户(paper_accounts)

模拟账户：
  live_factor_test (100万) — 主仓
  mini_100k (10万)          — 小仓
  production_forward        — 旧的前向测试仓

核心模块：
  stockhot/alert/panic_detector.py     — 四象限恐慌检测
  stockhot/alert/vol_streak_analyzer.py — 高波持续+衰减分析
  stockhot/invest_sop/event_calendar.py — 事件驱动日历
  stockhot/macro_fitness.py            — 宏观适配度
  stockhot/concept_board.py            — 概念板块
  stockhot/invest_sop/scripts/intraday_manager.py — 盘中仓位管理
  stockhot/invest_sop/scripts/intraday_rotation.py — 尾盘14:40实时价轮动（由上者主循环触发）

关键路径：
  crontab.txt         — cron 定义（改后必须跑 install.sh）
  .zcode/hooks/       — ZCode hooks
  .zcode/config.json  — hooks 配置
  docs/开发记录/      — 跨 session 开发日志（根因/过程/提交索引）
```

## Cron 时间表

```
05:00  AI 盘前报告（原08:15；全量打分2.5-2.8h，~07:50完成）
08:00  盘前策略表
08:30  盘前数据完整性检查
09:25  盘中监控启动（常驻到15:05，每2分钟；14:40触发尾盘轮动）
14:40  尾盘实时价轮动（intraday_manager 内置，主仓+小仓，飞书可跟随）
16:00  更新持仓现价
16:30  四因子选股 top20（~45min，限流可能延迟）
17:20  top20 → watchlist 同步
18:00  日常数据采集
19:00  收盘调仓兜底（14:40 成功则自动跳过）+ git auto push
19:20-19:55  limitup daily/candidates/paper/queue-sim/shadow
20:00  Davis估值筛选
20:00-21:30  海外/国内/供应链/期货数据采集
```

## 数据源状态

| 源 | 状态 | 备注 |
|---|---|---|
| Tushare | ✅ 稳定 | 主要数据源，400/min 限流 |
| 新浪 AKShare | ✅ 稳定 | 实时价降级源 |
| 东财 push2 | ❌ 被屏蔽 | Clash Verge 代理+服务器双封锁 |
| 同花顺 | ⚠️ 慢（7s） | 概念列表可用 |
