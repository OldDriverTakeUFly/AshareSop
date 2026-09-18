# longpic_kit(方案B)+ daily_longpic Phase 2 换骨 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 longpic_kit 共享骨架组件库(kit.css/themes.md/COMPONENTS.md,组件全收),并把 daily_longpic.py 的 CSS 模板换骨为 kit 注入(天梯/龙虎榜像素级无回归;thermo 构建器机制迁移、format bug 消除、不接线)。

**Architecture:** kit.css 全变量化(色彩角色+组件角色/尺寸),系列主题=themes.md 三套 :root;daily_longpic 运行时读 kit.css+由 THEMES 生成 :root 覆盖块;回归闸=body 标记逐字节相等 + PNG 像素差≤0.1%。

**Tech Stack:** 纯 CSS 变量、playwright(现有)、PIL(像素对比)。

## Global Constraints

- spec:docs/superpowers/specs/2026-09-18-longpic-kit-design.md(2026-09-18 拍板:组件全收/Phase 2 本轮做)
- 不改:cardgen/card_factory/content_publisher/daily_market_cards/历史工程(含光通信首作)
- 工作树注记:scripts/daily_longpic.py 存在**他人未提交的 thermo 长图半成品**(未接线,CSS.format 有 KeyError bug);Phase 2 与其共存:机制统一到 kit(顺带修 bug),**不接线 thermo**,提交时说明
- 日更回归判据:改造前后 天梯/龙虎榜 2026-09-18 工程——①`<style>` 剔除后 html 逐字节相等;②PNG 像素差>0 的像素占比≤0.1%
- 明日周六无日更 cron;17:50 无人值守链路下次运行 9-21(周一)——改造窗口安全

---

### Task 1: longpic_kit 三件套

**Files:**
- Create: `docs/小红书卡片/未发布/longpic_kit/kit.css`
- Create: `docs/小红书卡片/未发布/longpic_kit/themes.md`
- Create: `docs/小红书卡片/未发布/longpic_kit/COMPONENTS.md`

**Interfaces:**
- Produces: kit.css(CSS 变量清单=`--bg --card --border --text --dim --accent1 --accent2 --tagbg --tagfg --up --down --pos --neg --th` + 角色/尺寸 `--h1-size --h1-lh --h1-weight --sub-size --sub-mb --hook-bg --stats-mt --stat-bg --stat-border --stat-pad --v-size --v-color --h2-size --h2-color --td-border --td-color --td-lh --td-wb --note-mt --note-lh --insight-size --insight-color --insight-bar --insight-bg --rows-mb --rows-color --b-em --foot-bg --foot-color --border-soft`);Task 3 的 `_root_vars()` 消费同名变量

- [ ] **Step 1: kit.css**(默认值=板块热点复盘新肤;h1 默认 solid accent1,`h1.grad` 渐变;`td.up/td.down` 行情红涨绿跌,`.pos/.neg` 定性;tier/pyr/vs 研报系字面量)——完整内容见执行时写入(骨架同光通信长图.html 的 style,全部色值/关键尺寸换 var,另含 `.hook .h`、`.stat .v.ice`、`.vs`、`.tier .t1/.t2/.t3`、`.pyr .layer .l1-.l4`)
- [ ] **Step 2: themes.md** 三套可拷 :root:产业链(AI蓝系:bg#0b1026/card#141b40/border#2b3775/accent1#ffb347/h2#ffd166/h1.grad 90deg(#ffb347,#ff6b9d))、日更A风格(角色映射与 Task 3 `_root_vars` 输出一致,文档化)、板块热点(kit 默认值的显式化)
- [ ] **Step 3: COMPONENTS.md** 组件目录(片段+场景+颜色语义注记):tag/h1(.grad)/sub/hook(.h+stats)/stat(.ice)/card(h2+.st)/table(双轨取色)/note/insight/rows/vs/tier/pyr/foot;注意事项:新工程 `<link rel="stylesheet" href="../longpic_kit/kit.css">`+工程内只放 :root 与微调;工程挪「已发布/」后 link 相对路径失效→归档时顺手内联(PNG 已生成,仅源档)
- [ ] **Step 4: Commit** `feat(card): longpic_kit共享骨架组件库落地(方案B,组件全收)`

### Task 2: 样张工程(验收#1)

**Files:**
- Create: `docs/小红书卡片/未发布/longpic_kit/样张.html`(link kit.css,:root 由 sed/三份临时变体切换)+ 三张 PNG

- [ ] **Step 1: 写样张**(覆盖全部组件:tag/h1.grad/sub/hook+stats/stat.ice/card+st/表格双轨列/note/insight/rows/vs/tier/pyr/foot)
- [ ] **Step 2: 三主题各渲一张**(`:root` 分别换 themes.md 三套;playwright 750px 2x;输出 样张_产业链.png/样张_日更.png/样张_板块热点.png)
- [ ] **Step 3: 溢出检测**(check_overflow 的 JS 片段对样张跑一遍,零溢出)+ vision.py 目检一张(排版完整)
- [ ] **Step 4: Commit**

### Task 3: daily_longpic Phase 2 换骨(验收#2)

**Files:**
- Modify: `scripts/daily_longpic.py`(CSS 常量→kit 读取+`_root_vars()`;build_html/build_thermo_html 的 css 组装;thermo `_THERMO_CSS_EXTRA` 的 `{border}55`→`var(--border-soft)` 等;**THEMES/页面构建/CLI 不动语义**)

- [ ] **Step 1: 基线采集**(改前):`--kind ladder --day 2026-09-18 --out /tmp/kit_base_ladder.png` + `--kind lhb ...`;工程 长图.html 与 PNG 拷 /tmp/kit_base_*
- [ ] **Step 2: 换骨编辑**:新增 `KIT_CSS=(CARDS_ROOT/'longpic_kit'/'kit.css').read_text()`(启动时读,缺失即 SystemExit 带明确报错);`_root_vars(t)` 按 Task 1 变量清单由 THEMES 生成日更 :root(逐值对应旧模板:h1 42/1.38、sub 21/mb22、stats-mt14、stat-bg=border、stat-border none、stat-pad 14px 8px、v 32/accent2、h2 25/accent2、td-border=border+66、td-color=text、td-lh1.55、td-wb break-all、note-mt8/lh1.6、insight 19/text/accent2 条/border+55 底、rows-mb6/text/accent2、foot-bg=card/dim、border-soft=border+55、hook-bg=card);`build_html` 的 css=`KIT_CSS+_root_vars(theme)`;删除旧 `CSS.replace` 组装;`build_thermo_html` 同机制(`_THERMO_CSS_EXTRA` 改 var 引用,**不接线不动 CLI thermo 流程**)
- [ ] **Step 3: 回归闸**:`--kind ladder/lhb --day 2026-09-18 --out /tmp/kit_post_*.png`;脚本断言:①前后 html 剔除 `<style>...</style>` 后逐字节相等;②PIL ImageChops.difference 逐像素,差异像素占比≤0.1% 且报告最大通道差
- [ ] **Step 4: thermo 冒烟**(证明 format bug 消除):直接调 `build_thermo_html(板块温度/2026-09-18, THEMES['thermo'])` 渲 /tmp png + `numbers_gate` 零未锚定;**不写入工程目录**
- [ ] **Step 5: Commit**(提交信息注明:包含工作树内 thermo 长图构建器半成品(机制已迁 kit、format bug 消除、未接线——接线与启用属 thermo 迁移任务本身))

### Task 4: 收尾

- [ ] **Step 1: spec 状态行更新(已实施)+ Commit**
- [ ] **Step 2: 汇报**:kit 结构、样张、回归数字(body 相等/像素差)、thermo 半成品发现与处置、AGENTS/方法论是否需要一句 kit 指引(加一句:新长图工程用 kit,见 COMPONENTS.md)
