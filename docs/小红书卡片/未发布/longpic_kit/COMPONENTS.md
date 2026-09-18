# longpic_kit 组件目录(COMPONENTS.md)

> 每个组件 = HTML 片段 + 适用场景 + 颜色语义注记。新工程组装流程:`<link rel="stylesheet" href="../longpic_kit/kit.css">` → 拷 themes.md 所属系列 `:root` → 按骨架顺序拼组件 → render_longpics.py 渲染 + check_overflow.py 溢出检测必须全过。
>
> **骨架铁律**:tag → h1(钩子,≤3行) → sub → hook(黄金三秒) → 编号小节卡片(一、二、…) → insight → foot(每图自带信源+免责)。
> **颜色语义双轨**:行情涨跌数字列 `td.up`(红)/`td.down`(绿);定性好坏(事实正负)`.pos`(青绿)/`.neg`(粉红)。同一表格两类列并存按列取色,不得混用。

## tag 系列徽章
```html
<span class="tag">板块热点复盘 · 2026-09-18</span>
```
场景:每图第一行,系列名+日期。

## h1 钩子大标题(实心/渐变)
```html
<h1>自高点跌掉两三成的光模块<br>今晚集体回血</h1>          <!-- 日更系:实心 accent1 -->
<h1 class="grad">电解家族点名册:<br>…反而越不赚钱?</h1>    <!-- 研报系:accent1→accent2 渐变 -->
```
场景:≤3 行,最大反差/核心背离/最响数字。日更长图用实心(像素回归基线),研报/板块热点用 .grad。

## sub 副题
```html
<div class="sub">一句话悬念或事件概括,数字留正文。</div>
```

## hook 首屏卡(黄金三秒)
```html
<div class="hook">
  <div class="h">今晚发生了什么(9-18收盘)</div>
  <div class="stats">
    <div class="stat"><div class="v">+2.62%</div><div class="k">通信设备板块涨幅</div></div>
    <div class="stat"><div class="v ice">60.5亿</div><div class="k">主力净流入·两市第二</div></div>
    <div class="stat"><div class="v">涨停</div><div class="k">共进股份 +10.03%</div></div>
  </div>
  <p style="margin-top:14px">一段导语,把读者拉进横截面。</p>
</div>
```
注记:`.v.ice`=次强调色(accent2);日更系 hook 无 `.h` 头与 stats 结构相同。

## card 内容卡 + 小节标题
```html
<div class="card">
  <span class="tag">温度全景</span>          <!-- 卡内 tag 可选 -->
  <h2>一、今晚的点名册</h2>
  <div class="st">10家代表性公司,按今日涨幅降序</div>
  …
</div>
```

## table 表格(点名册/榜单;双轨取色)
```html
<table>
  <tr><th>公司</th><th>今日涨幅</th><th>距近季峰</th></tr>
  <tr><td>共进股份</td><td class="up">+10.03% 涨停</td><td class="down">-4.6%</td></tr>
  <tr><td>源杰科技</td><td class="up">+2.82%</td><td><span class="pos">已收复失地</span></td></tr>
</table>
```
注记:行情列 up/down;定性判断列用 pos/neg span;口径写在随卡 note。

## note 口径注脚
```html
<div class="note">口径:Tushare日线收盘(前复权);红=涨,绿=跌。</div>
```

## insight 左边条金句(每图≥1)
```html
<div class="insight">复活是分层的:…同一晚,有人叫回归,有人只能叫反弹。</div>
```

## rows 列表(催化/要点)
```html
<ul class="rows">
  <li>高盛9月14日报告:把2027/2028年需求预测上修<b>39%/36%</b>…</li>
</ul>
```

## vs 对峙双栏(多空/新旧/两物种)
```html
<div class="vs">
  <div class="side a"><h3>支持延续的证据</h3><p>…</p></div>
  <div class="side b"><h3>值得警惕的信号</h3><p>…</p></div>
</div>
```

## tier 梯队分层(涨停先锋/跟风/中军;宽度即层级)
```html
<div class="tier t1"><b>涨停先锋</b> 共进股份(+10.03% 涨停)</div>
<div class="tier t2"><b>跟风弹性</b> 联特·太辰光·仕佳(+6%档)</div>
<div class="tier t3"><b>中军回暖</b> 旭创·新易盛(+2.5~5%)</div>
```

## pyr 金字塔分层(四层框架等)
```html
<div class="pyr">
  <div class="layer l1"><b>第一层·总量共振</b>(存储/铜)…</div>
  <div class="layer l2"><b>第二层·等级跃迁</b>…</div>
  <div class="layer l3"><b>第三层·单点垄断</b>…</div>
  <div class="layer l4"><b>第四层·地缘管制</b>…</div>
</div>
```

## foot 信源免责(每图末)
```html
<div class="foot">信源:Tushare/stockhot(截至2026-09-18收盘)、自研板块温度计…不构成投资建议,市场有风险,决策需独立。</div>
```

## 注意事项
1. **归档**:工程挪「已发布/」后 `../longpic_kit/` 相对路径失效——PNG 已生成,html 仅为源档;若需归档后重渲,顺手把 link 换成 kit.css 内联。
2. **微调**:个别工程的特殊组件(如温度条 bar-row/dim-row 属 daily_longpic 专属,随脚本注入)在工程 `<style>` 内追加,勿改 kit.css 单系列化。
3. **纪律沿用**:数字锚 facts.json / 敏感词全表含 html / 时效闸 5 天 / tags 末行 / 发布永远人工。
