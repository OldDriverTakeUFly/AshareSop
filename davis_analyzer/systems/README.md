# davis_analyzer 子系统导航

| 子系统 | 职责 | CLI | 关键表/产物 |
|---|---|---|---|
| limitup | 涨停研究:回补→事件/形态/情绪→事件研究→事件驱动打板回测 | `python -m davis_analyzer.systems.limitup` | 自管表+reports/candidates_*.md |
| tournament | 策略锦标赛:适配→统一评估→评分权重→回放→CPCV-lite进化→冠军存档 | `python -m davis_analyzer.systems.tournament {run\|replay\|evolve\|champions}` | 共享库 tournament_ledger 表 |
| thermometer | 板块温度计:宇宙→数据回补→成分聚合主力净额→五族因子→截面温度+大盘五维→校准→日报 | `python -m davis_analyzer.systems.thermometer {backfill\|run\|calibrate\|report\|status}` | market_data.db 九张表+thermo卡长图链 |
| intraday | 日内做T研究沙盒(baostock 分钟线):回补→闭环回转引擎→因果特征→策略族→对账→影子验证 | `python -m davis_analyzer.systems.intraday {backfill\|status\|verify\|run\|shadow\|shadow-report\|shadow-enrich}` | 独立库 intraday_research.db |
| cardgen | 金融卡片生成:facts 溯源→物化→四道机器闸→渲染→发布包 | `python -m davis_analyzer.systems.cardgen {init\|ingest\|validate\|build\|status\|enqueue\|sync}` | content_cards.db+docs/发布/小红书 工程树 |
| recap | 复盘视频:NBA解说式热点复盘短视频(选片→剧本→录制单→原料包→五佳球模式成片) | `python -m davis_analyzer.systems.recap {run\|select\|script\|sheet\|audio\|post\|status}` | recap_episodes 表+episodes/{day}/ |
| surge | 涨幅筛选:当日>7%九维分析(筹码/巨潮公告/压力支撑/形态/标签)+双工作长图 | `python -m davis_analyzer.systems.surge {run\|backfill\|status}` | 七张自管表+reports/{YYYYMMDD}/ |
| paper_trading | 模拟交易子系统 | `python -m davis_analyzer.systems.paper_trading {init\|run\|backfill\|report\|list}` | paper_accounts 等台账 |
| metrics | 指标采集与报告 | `python -m davis_analyzer.systems.metrics` | collector/db/report |

> 各子系统详细纪律见仓库根 AGENTS.md 对应章节;本文件只做导航。
