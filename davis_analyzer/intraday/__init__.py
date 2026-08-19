"""日内做T研究子系统（Phase 1：分钟数据研究沙盒）.

数据源为 baostock（AGENTS.md"Tushare 唯一外部数据源"的唯一例外——经用户批准，
仅限本沙盒）：5 分钟线落独立库 storage/database/intraday_research.db，表内标注
source，生产 pipeline 与 market_data.db 缓存不读取本库。Tushare 的 stk_mins
在当前积分档限频 1 次/小时、2 次/天，无法承担回补（2026-08-19 实测）。

数据质量结论（2026-08-19 全量对账，60 只×13 个月）：分钟 bar 的 open/close
与日线严格一致、bar 内 VWAP 自洽；但 high/low 在约 30% 股票日低于日线极值
（快照采样漏瞬时尖刺，幅度多 <1.5%）——做T回测以分钟线为准（瞬时极值不可
执行），任何策略假设不得把日线 high/low 当作可成交价。
"""
