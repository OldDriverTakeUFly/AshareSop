# 公告日报日卡 cron — 待创建(本文件即完整 prompt,新会话直接用)

> 背景:2026-09-02 用户拍板「公告日报按日期做成小红书卡片」常态化。当日会话因已绑定定时任务
> 无法 CronCreate(会话限制,同 2026-09-01 的 17:00 cron 先例),prompt 存于此,新会话说一声即可建。

- **cron 表达式**: `0 23 * * 1-5` 换成 `40 23 * * 1-5`(每交易日 23:40)
- **标题**: 每交易日23:40 公告日报日卡(入发稿池,不发布)
- **创建方式**: 新会话对助手说「按 docs/小红书卡片/未发布/公告日报/日卡cron_prompt_2340.md 建 cron」即可

---

以下是 CronCreate 的 prompt 原文:

【定时任务·每交易日23:40 公告日报日卡】把当日公告日报做成带日期的小红书卡片工程并入发稿池(用户 2026-09-02 授权常态化;「卡上数字必须 facts 锚定」纪律不变,机器闸+目检兜底)。工作目录 /home/leo/Projects/CodeAgentDashboard,解释器 .venv/bin/python,命令从仓库根目录运行。无人值守:异常记录后继续,事后报告。本任务只产卡入池,绝不发布(发布永远人工 queue.py publish)。

前置检查:
1. 读 docs/小红书卡片/未发布/公告日报/YYYY-MM-DD.md(今日文件,23:00 cron 应已生成并提交;日期取当天)。若文件缺失或没有任何「✅可做卡」条目 → 报「今日无料,不出卡」,任务结束(不出空卡)。

建工程(命名带日期):
2. .venv/bin/python -m davis_analyzer.cardgen init --topic 公告日报YYYYMMDD(如 公告日报20260902;init 自动建在 未发布/ 并登记台账)。
3. 结构模板:找 未发布/ 或 已发布/ 下日期最大的 公告日报*/cards.spec.json 作模板(首期回退用 未发布/今日公告公司行为/);沿用其卡片结构(封面 stats 三格 + 品类内容卡[table 型] + 小结[summary 型])与 theme 命名,只换内容。张数 3-6 张:封面+小结必选,品类卡按当日有料品类出(回购/定增募资/收购重组/业绩,一品类一张,优先放信号最强事件)。

写事实(facts.json,铁律:卡上每个数字都必须有对应 fact):
4. 只允许两类锚点:
   ①管线计数(全市场公告 N 份/回购 N 家/定增 N 家/收购重组 N 家等)——source.ref 写「公告日报管线@YYYY-MM-DD HH:MM(未发布/公告日报/YYYY-MM-DD.md,已提交git)」,quote 摘 md 统计行原文;
   ②公告金额——用 curl 下载对应巨潮 PDF(http://static.cninfo.com.cn/finalpage/...),pdfplumber 抽前4页文本,取「首次/累计/上限」金额、股数、占比等硬数字,fact 的 quote 必须是 PDF 原文句子(如「支付的总金额为318,402,795.70元」)。
   单位规律:value 的单位=文本数字后紧跟的单位字(亿/万/%;复合单位如万米拆到最末单位字「万」),display 可带全称。PDF 拉不到或抽不出金额 → 该条目改定性文案(不含数字),严禁估算造数。
5. 内容红线:增减持/询价转让类绝不入卡(§8.6 敏感品类);tag_top 禁日期;标题/正文避开「增持/减持/卖出」字样;避免「N月N日」写法(数字闸会咬,日期只放 foot 的 ISO 形式);foot 统一「数据来源:巨潮资讯当日公告(YYYY-MM-DD披露,PDF核实) · 仅供研究参考,不构成投资建议」,尾卡 foot 含「市场有风险,投资需谨慎」。

闸与渲染:
6. .venv/bin/python -m davis_analyzer.cardgen validate --topic 公告日报YYYYMMDD —— 不过则按报错修 facts/spec 再验,最多迭代3轮;仍不过 → 保留工程文件不删,报告失败项,不入池,结束。
7. .venv/bin/python -m davis_analyzer.cardgen build --topic 公告日报YYYYMMDD(新工程首版无需 --bump)。
8. 逐张目检 output/*.png:.venv/bin/python scripts/content_publisher/vision.py <png绝对路径> --prompt "这是小红书金融科普卡片,请目检并返回JSON:{\"pass\":bool,\"issues\":[str]}。检查:文字无溢出卡片边界、无互相重叠遮挡;表格/文本块完整未截断(尤其底部);清晰可读;无明显异常留白;整体视觉可接受;有问题给具体位置。" —— 任一张 pass=false → 不入池,报告 issues 与 png 路径,结束(等人工);全 pass 才继续。

入池与留痕:
9. .venv/bin/python -m davis_analyzer.cardgen enqueue --topic 公告日报YYYYMMDD,然后原样执行它打印的「.venv/bin/python scripts/content_publisher/queue.py enqueue …」那一行(这只是入发稿池,不是发布),记下入池编号。
10. git add docs/小红书卡片/未发布/公告日报YYYYMMDD && git commit -m "feat(cardgen): 公告日报日卡YYYYMMDD——<一句话当日主线>"(不 push)。
11. 最终消息报告:入池编号、张数、当日主线一句话、封面三个钩子数字、目检结论;任何降级/跳过(如某 PDF 拉取失败改定性、某品类无料未出卡)如实列出。

纪律:不改 scripts/daily_bulletin.py、davis_analyzer/cardgen、scripts/card_factory、scripts/content_publisher 任何代码;不动 WATCH 池;不发布、不删发稿池条目;不创建/修改/停用任何其他 cron;遇未覆盖情形按项目 AGENTS.md 纪律记录后继续,事后报告。首期参照工程:未发布/今日公告公司行为(2026-09-01 晚间版,入池 #58)。
