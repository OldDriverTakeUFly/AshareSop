# 复盘卡补推哨兵 cron 代建 prompt

> 建成后处置:建 cron 成功后 `git rm 本文件 && git commit -m "chore(cron): 消费一次性prompt复盘卡补推哨兵——cron已建"`,建失败则保留重试。
> 背景:2026-09-15 会话因会话归属限制无法 CronCreate(同光通信复活监视先例,§8.7 载体自删约定)。上下文:当日 17:50 复盘卡 cron 因机器休眠被跳过(computer_asleep_or_app_not_running),系统唤醒后无自动补推机制,拖到 21:54 人工补跑——违反尽早推送原则。用户拍板:①建本哨兵(每 20 分钟查锁补推);②盘后防休眠 systemd 单元已另行落地(scripts/after_hours_inhibit.sh + user timer,见 AGENTS.md)。

【请用 CronCreate 创建以下定时任务】

- cron:`*/20 18-23 * * 1-5`(工作日 18:00-23:40 每 20 分钟)
- 标题:`工作日18-23点每20分钟 复盘卡补推哨兵(缺锁即补,尽早推送)`
- recurring: true

prompt 正文:

【定时任务·工作日18-23点每20分钟 复盘卡补推哨兵】尽早推送原则:17:50 复盘卡 cron 若因机器休眠/客户端未运行被跳过,本哨兵在系统恢复可用的第一个 tick 自动补推,不等人工发现。工作目录 /home/leo/Projects/CodeAgentDashboard,解释器 .venv/bin/python,所有命令从仓库根目录跑。无人值守;绝大多数 tick 应是无事静默,一句话结束。

执行步骤:
1. 幂等检查:读 logs/.xhs_card_push/<今日YYYY-MM-DD>_ladder.ok 与 _lhb.ok,两个都在 → 最终消息一句话「当日两卡已推,哨兵无事」结束。
2. 互斥检查:pgrep -f daily_market_cards 有进程 → 一句话「推送管线在跑,本轮让行」结束(避免与 17:50 cron 或上一 tick 撞车)。
3. 补跑(走到这里说明至少缺一个锁):
   a. 缺 ladder 锁才采集涨停侧:timeout 300 .venv/bin/python -c "from dotenv import load_dotenv; load_dotenv(); from stockhot.limit_up import run_limit_up_analysis; r=run_limit_up_analysis('<今日>'); print(r['status'], r['data'].get('summary','')[:100])" —— 涨停数为 0(休市/无数据)→ 一句话「无涨停数据,疑似非交易日,空转」结束,不出空卡。
   b. 缺 lhb 锁才采集龙虎榜:timeout 300 .venv/bin/python -c "from dotenv import load_dotenv; load_dotenv(); from stockhot.dragon_tiger import run_dragon_tiger_analysis; r=run_dragon_tiger_analysis('<今日>'); d=r['data']; print(r['status'], 'detail', len(d.get('detail',[])))" —— detail=0 → 本轮不出 lhb 卡,记录「龙虎榜未披露,下轮再看」,但不影响 ladder 补推。
   c. 按 kind 逐个补(仅处理缺锁的 kind):当日工程 docs/小红书卡片/未发布/<连板天梯|龙虎榜>/<今日>/output/RELEASE.json 已存在 → 只补推送:timeout 900 .venv/bin/python scripts/daily_market_cards.py --type <ladder|lhb> --push;工程不存在 → timeout 900 .venv/bin/python scripts/daily_market_cards.py --type <ladder|lhb> --enqueue --push;两 kind 都缺且都无工程 → 一条命令 --type all --enqueue --push。stdout 应有「✓ <topic> 已推红薯运营群」行。
   d. 失败最多重跑一次,仍失败如实报告失败明细,不修改任何代码。
4. 若本轮新建了当日工程(此前不存在):git add 实际新建的 docs/小红书卡片/未发布/连板天梯 和/或 龙虎榜 并 commit,格式「feat(cardgen): 复盘卡 <今日> 哨兵补跑——天梯N页/龙虎榜N页入池#A/#B」;不 push。
5. 最终消息精炼:缺了哪个锁/补了什么/推送结果(含入池编号)/异常(如有)。

纪律:不改任何代码;不创建/修改/停用任何其他 cron;只推「红薯财经博主运营」群(脚本内置 FEISHU_XHS_CHAT_ID,无需干预);推送文案 tags 必须是最后一行(脚本已内置);入池≠发布,发布永远人工;法定节假日空转属正常不报警。
