# Session State（上次 session 结束时的状态快照）

> 使用：每次 session 结束前（或大功能完成后）更新此文件。
> 下次 session 开始时 session-context hook 自动注入，省去重新探索。

## 最近完成的工作

<!-- 记录 3-5 条最近完成的功能/修复，格式：[日期] 描述 + commit hash -->

- [2026-08-18] 双模拟仓（主仓100万+小仓10万）多账户监控 — `68de988`
- [2026-08-18] 命令输出截断规则写入 AGENTS.md + hooks 优化
- [2026-08-17] 双级板块（L1+L2）+ 概念板块接入 — `1bba7cf`

## 进行中 / 未完成

<!-- 有什么做到一半的？下次要接着做什么？ -->

- （无）

## 已知问题 / 待办

<!-- 遗留的 bug、技术债、待验证的事项 -->

- 东财 push2 被 Clash Verge 代理屏蔽 → 已切新浪源降级，东财恢复后自动切回
- sector_volatility 模块计算量大（首次 10+ 分钟），周频 cron 已配
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

关键路径：
  crontab.txt         — cron 定义（改后必须跑 install.sh）
  .zcode/hooks/       — ZCode hooks
  .zcode/config.json  — hooks 配置
```

## Cron 时间表

```
08:00  盘前策略表
08:15  AI 盘前报告（LLM，可能跑到中午）
09:25  盘中监控启动（常驻到15:05，每2分钟）
16:00  更新持仓现价
16:30  四因子选股 top20（~2h，限流可能延迟）
19:00  收盘调仓 + 报告推送
18:00  日常数据采集
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
