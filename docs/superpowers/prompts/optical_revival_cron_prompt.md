# 光通信卡复活监视 cron 代建 prompt

> 建成后处置:建 cron 成功后 `git rm 本文件 && git commit -m "chore(cron): 消费一次性prompt光通信复活监视——cron已建"`,建失败则保留重试。
> 背景:2026-09-14 会话因会话归属限制无法 CronCreate,按 §8.7 载体自删约定落盘本文件。上下文:光通信板块 20 日普跌 9-14%,池内 #23-30 光通信卡已过期归档,约定机械复活条件,触发后推博主群。

【请用 CronCreate 创建以下定时任务】

- cron:`10 19 * * 1-5`(每交易日 19:10)
- 标题:`每交易日19:10 光通信卡复活监视(触发式,推博主群)`
- recurring: true

prompt 正文:

【定时任务·每交易日19:10 光通信卡复活监视】机械判定光通信板块是否满足"卡组复排"条件;未触发则一句话静默报告,触发则推博主运营群提醒一次(幂等锁防重复推)。工作目录 /home/leo/Projects/CodeAgentDashboard,解释器 .venv/bin/python,命令从仓库根目录跑。无人值守:异常记录后继续。

执行步骤:
1. 幂等检查:若 logs/.optical_revival.done 存在 → 报「已触发过(锁存在),本任务完成使命」结束。
2. 判定脚本(.venv/bin/python,heredoc):
   - dotenv load_dotenv 显式路径 /home/leo/Projects/CodeAgentDashboard/.env(find_dotenv 在 stdin 会炸,禁用);tushare pro_api 取 TUSHARE_TOKEN。
   - 篮子四股:300502.SZ 新易盛 / 300308.SZ 中际旭创 / 300394.SZ 天孚通信 / 002281.SZ 光迅科技。
   - 每股 pro.daily(start_date=今天-140自然日, end_date=今天, fields=trade_date,close) 升序。
   - 对最近 3 个交易日 t 逐日判定单股条件:close[t] > MA20(close[t-19..t]) 且 (close[t]/close[t-20]-1) > 0。
   - 触发条件:连续 3 个交易日,每日满足单股条件的家数 ≥3(共4股)。数据不足60根K线视为不满足。
3. 未触发 → 最终消息一句话:各股现价/20日收益/距MA20 一览表 + 「未触发,继续静默监视」。
4. 触发 → ①写锁文件 logs/.optical_revival.done(内容=触发日期);②推「红薯财经博主运营」群:sys.path.insert repo 根,stockhot.notification.feishu_bot.EnterpriseFeishuNotifier(FEISHU_APP_ID/FEISHU_APP_SECRET, FEISHU_XHS_CHAT_ID),asyncio send_text:
   「【光通信卡复活条件触发】四股篮子(新易盛/中际旭创/天孚/光迅)连续3日≥3家满足20日收益转正+收复MA20。光通信系列卡(#23-30原池号,工程在 docs/小红书卡片/未发布/ 的 中际旭创/仕佳光子 等目录)可考虑复排。复活流程纪律:原卡facts已过期(08末口径),必须刷新数据(估值分位/价格重跑)→ --bump 重建 → 重新入池,不得原样重发。」+ 当日四股读数。
   ③最终消息报告触发明细与推送结果。
5. API 失败:最多重试一次,仍失败如实报告当日未判定,不影响次日。

纪律:不改任何代码;不创建/修改/停用其他cron;只推博主群不推盯盘群;除锁文件外不写任何状态。
