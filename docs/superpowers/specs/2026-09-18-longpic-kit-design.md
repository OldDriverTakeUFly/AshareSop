# longpic_kit 共享骨架与组件库(方案B) — 设计 Spec

- 日期:2026-09-18(方案B 于板块热点复盘首作光通信复活过观感验证后启动,见 2026-09-18-sector-hot-recap-longpic-design.md §八)
- 状态:**已实施**(2026-09-18 当晚,commit 24f7cda/60931f1/196a18d/9c187a5)
- 目标:把三系列长图(产业链研报长图卡/日更长图/板块热点复盘)已事实上同源的骨架与组件,从「各工程拷贝 CSS」固化为「一套 kit,新工程从 kit 起步」——一致性有机制保障,同时保留手工叙事自由度。

## 一、动机与边界

现状:三系列骨架一致(tag→h1→sub→hook→编号小节→insight→foot)但每份 html 内嵌自己的 CSS 拷贝,组件样式靠拷贝漂移;daily_longpic.py 另有一份带 `{bg}` 占位符的 CSS 模板。已出品 9 个长图工程(6 产业链+2 日更+1 板块热点复盘)。

**不做**:自动化内容生成(方案C 已否决);回改历史工程(自包含存档,不追新);改 cardgen/card_factory/content_publisher。

## 二、kit 结构

位置:`docs/小红书卡片/未发布/longpic_kit/`(与 render_longpics.py/check_overflow.py 同层)

```
longpic_kit/
  kit.css          # 骨架+全部组件样式,主题色全部走 CSS 变量(:root 覆盖点)
  COMPONENTS.md    # 组件目录:每个组件一段「HTML 片段+适用场景+颜色语义注记」
  themes.md        # 系列主题表:变量组直接可拷贝的三套 :root 块
```

### kit.css 设计

- 全部颜色走 CSS 变量:`--bg --card --border --text --dim --accent1 --accent2 --tagbg --tagfg --up --down --pos --neg --th`
- 骨架与组件(自三系列归纳,组件清单=spec 2026-09-18-sector-hot-recap §2.2 v1):
  `.tag h1(.gradient) .sub .hook(.h/.stats) .stat(.v/.k) .card(h2/.st) table(th/td.up/td.down) .insight .rows .note .vs(.side.a/.side.b) .tier(.t1/.t2/.t3) .pyr(.layer) .foot`
- 颜色语义双轨注释写在文件头:行情涨跌 `--up=红/--down=绿`(列用途);定性好坏 `--pos=青绿/--neg=粉红`;同一表格两类列并存按列取色。

### themes.md 三套主题(变量组)

| 系列 | bg | card | accent1 | accent2 | tagfg |
|---|---|---|---|---|---|
| 产业链研报长图(AI蓝系) | #0b1026 | #141b40 | #ffb347 | #6db9ff | #9db4ff |
| 日更数据卡(A 风格,炭黑红金) | #0f1014 | #191b22 | #ff4d4f | #ffd166 | #ff9c9c |
| 板块热点复盘(深蓝黑琥珀) | #0a0f1e | #131a30 | #f5b942 | #6db9ff | #f5c96a |

新系列新增主题 = themes.md 加一组 :root 块(用户拍板配色后才入表)。

## 三、接入方式(增量,不动存量)

1. **新工程(产业链长图卡/板块热点复盘)**:手写 html 时 `<link rel="stylesheet" href="../longpic_kit/kit.css">`+工程内 `<style>` 只放 `:root` 主题变量与个别微调;渲染工具按 file:// 加载,相对路径可达。
2. **旧工程**:不动(自包含存档)。
3. **daily_longpic.py 换骨(Phase 2,单独提交)**:其 CSS 模板改为注入 kit.css 文本+THEMES 变量映射(保持 {bg} 占位机制或改为变量拼接,以最小 diff 为准);改动后必须:①天梯/龙虎榜当日工程重渲 PNG 与改造前像素对比无回归;②跑一次 `--type all` 全流程烟测。**若当日 17:50 cron 临近,先做①②再合并**。
4. **check_overflow/render_longpics**:不变(仍按工程目录寻址 长图.html)。

## 四、验收

1. 用 kit 重产一个「最小样张工程」(三主题各渲一张组件全样张),check_overflow 全 PASS、目检无回归;——**已过**(样张_板块热点/产业链/日更 三张全 PASS + vision 目检通过,build_samples.py 固化)
2. daily_longpic Phase 2 后天梯/龙虎榜渲染输出与改造前像素对比一致(容差≤0.1%);——**实施注记:偏差=修复非回归**。body 逐字节相等;唯一布局差异 = 旧模板 `.stat {…}}` 行尾双花括号(str.format 转义残留)被 Chromium 错误恢复吞掉下一条规则——`.stat .v{32px金}` 在生产一直是死的(实测 16px 白),`td.down{绿}` 同款失效。换骨后两处恢复设计意图(当日两卡 +22px 即 stat 数值变高所致,已重渲工程 PNG)。当晚 17:50 已入池/飞书推送的天梯/龙虎榜为旧渲染,池内同步修复版需重新入池,留人工决定。
3. COMPONENTS.md 每个组件都有片段+场景+颜色语义注记,新 agent 零上下文可组装。——**已过**

## 五、风险

- `<link>` 相对路径在工程被移动(如 sync 挪「已发布/」)后失效——发布归档场景 PNG 已生成,html 仅为源档;若需保渲染可复跑,归档时可顺手把 link 内联(记录在 COMPONENTS.md 注意事项)。
- daily_longpic 是无人值守链路一环,Phase 2 必须独立提交+烟测+回滚预案(git revert 即可)。

## 六、开放问题(2026-09-18 用户已拍板)

1. ~~Phase 2 时机~~ → **本轮直接一起做**(9-18 晚实施;次日为周六无日更 cron,17:50 无人值守链路下次运行在下周一晚,改造窗口安全;验收闸=当日天梯/龙虎榜重渲像素对比无回归);
2. ~~低频组件收录~~ → **全收**(金字塔/梯队等一并入 kit,片段即文档)。
