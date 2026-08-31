# 实验 0010 收尾 + G2 信号化后续步骤 — 定时任务 prompt(2026-08-31 18:30)

> **用途**:本会话由 cron 派生,无法再嵌套创建定时任务(harness 硬限制)。
> 两种用法任选:
> ①开一个**新会话**,粘贴一句:「读取 /home/leo/Projects/CodeAgentDashboard/docs/superpowers/prompts/0010_cron_prompt.md,按其内容创建今天 18:30 的一次性 cron(标题:实验0010收尾+G2信号化后续步骤)」;
> ②或 18:30 后在本会话说「按 0010_cron_prompt.md 开始执行」,当场跑同样的决策路径。
> 以下为 cron prompt 原文(两条路径通用)。

---

实验0010(滚动宇宙G2回测)收尾+后续步骤执行(2026-08-31 18:30,用户授权跑"剩下的部分")。工作目录 /home/leo/Projects/CodeAgentDashboard,解释器 .venv/bin/python。背景:滚动宇宙三变体(R_q200主/R_m200频率敏感/R_q300池宽敏感)08:51 起脱离会话运行(日志 logs/abx/rolling_universe_run.log,结果 logs/abx/rolling_universe_g2_abx.json 逐变体落盘);基线=冻结宇宙G2(+126.40%/MDD15.6%/Sharpe1.081,年度 2021:+32.9/2022:+19.3/2023:+16.7/2024:-1.2/2025:+18.6/2026:+4.4)。烟测已验证口径(2021Q1 滚动-3.63% vs 冻结同期-3.47%)。总目标:验证滚动宇宙可用后,把盘中轮动的选股基准从 top20(动量阶段产物)切到 G2(用户已拍板"直接用G2筛选买入池")。

【第一步:状态判定】pgrep -f rolling_universe_g2_abx + 读 json。注意 18:30 时可能 R_q200 已完成而 R_m200/R_q300 仍在跑——D1 判定只依赖 R_q200,敏感性未完标记"数字后补"即可。若 R_q200 也未完成:只报进度(tail -5 日志+估算剩余),任务结束。若进程死且 json 缺 R_q200:报日志尾30行,不重跑,任务结束。

【第二步:D1 判定(预注册判定线,机械执行)】对 R_q200:
- 采纳:Sharpe>1.0 且 收益>=100% 且 MDD<=17% 且 无一年倒退>5pp(vs 冻结年度)
- 否决:Sharpe<0.9 或 MDD>18% 或 任一年倒退>8pp
- 中间地带:其余情形=条件采纳(实盘可用+宇宙质量监控)
- 稳健性哨兵:若 R_q200 Sharpe>1.4(异常好)→ 先做前视审计再出结论(检查 rolling_universe 的 ref 日与段起点关系、持仓∪宇宙合并是否引入幸存者),审计无问题才按上述线判定;若 R_m200/R_q300 已完成且方向与 R_q200 相反(如月度崩盘)→ 判定降一档(采纳→中间)并在日志记录。
写实验日志 docs/回测记录/实验日志/0010_2026-08-31_滚动宇宙G2口径验证.md(六节,含宇宙换血统计)+ README 索引行 + git commit(Conventional Commits 中文 scope)。

【第三步:按 D1 分支】
- D1=否决 → 停:不动轮动,遗留"实盘宇宙方案重设计"(候选:冻结宇宙+年度再平衡);向用户报告,任务结束。
- D1=采纳或中间 → 执行后续步骤(中间地带时步骤②切真账户需用户确认,只做①③):
  ①【G2信号导出】写 G2 信号导出脚本:取最近已完整收盘日 T-1(日线 max trade_date<今日),宇宙=当日成交额 top200,跑 G2 十道闸(FactorThresholdStrategy 的买入过滤,复用 executor 的 _compute_davis_scores_at/_compute_factor_scores_at 等函数,T-1 因果),产出放行名单(含综合分排名)落盘 logs/g2_signals/g2_list_<T-1>.json。立即跑一次并抽样核验 3 只放行股确实过闸(动量>70或牛市60/次维度/短线动量/振幅)。空名单=正常(防守策略特性),不算错误。
  ②【调度挂载】查 CronList 是否存在每日 19:00 inject 或类似盘后投递 cron:有→用 CronUpdate 把"先跑 g2_signal_export.py"并入其 prompt(保守措辞,失败不阻塞原任务);无→不强求,在报告中给用户一句手动挂载指令。注意:cron 派生会话大概率不能再 CronCreate,预设此兜底。
  ③【shadow 账户】创建 g2_shadow 账户(100万虚拟,strategy_name=davis_double 兼容轮动框架),把 intraday_rotation 消费名单的逻辑做最小改动:优先读 logs/g2_signals/ 最新 g2_list_*.json(存在且数据日≤T-1 才用),否则回退 top20 json——改动加开关参数默认 False,shadow 账户走 G2 名单,live_factor_test/mini_100k 行为零变化。用 dry_run 烟测一次确认名单被正确消费。
  ④报告:D1 结论+0010 数字表+导出名单(放行 N 只/前三名)+shadow 就绪状态+待用户确认事项(中间地带时的切真账户;无 19:00 cron 时的手动挂载)。

【预注册的后续决策路径(向用户报告时引用)】
- D3 切换判据:shadow≥10 交易日后对比 live_factor_test(0003 分状态配对口径):G2-shadow 超额>0 且买入样本100%过闸→切换 live_factor_test 基准,mini_100k 同步;窗口内上证单日|涨跌|>3% 天数>2→延长 shadow 一周(极端 regime 顺延,不因窗口特殊性下结论)。
- D5 卖出侧:名单制(跌出放行名单即卖)+保留10%盘中硬止损兜底;不做 0009 式盘中结构豁免(已证伪)。
- D2 数据时机:导出基准日=最近已完整收盘交易日;空名单日=轮动只卖不买(特性非 bug)。

纪律:负结果照写;改动轮动代码必须默认关闭(live 零行为变化);不 push;遇到本 prompt 未覆盖的情形按项目 AGENTS.md 纪律记录后继续,事后报告。
