# longpic_kit 系列主题表(themes.md)

> 用法:新工程 `<link rel="stylesheet" href="../longpic_kit/kit.css">` 后,把所属系列的 `:root` 块拷进工程 `<style>`(可再微调)。**新增系列须用户拍板配色后才入本表。**变量清单见 kit.css 文件头注释。

## 1. 板块热点复盘(2026-09-18 拍板,深蓝黑+琥珀金+冰蓝)

= kit.css 默认值,显式化如下:

```css
:root {
  --bg:#0a0f1e; --card:#131a30; --border:#26335c; --text:#eef2ff; --dim:#8a96c4;
  --accent1:#f5b942; --accent2:#6db9ff; --tagbg:#3a2e12; --tagfg:#f5c96a;
  --up:#ff6b6b; --down:#4ade80; --pos:#6ee7b7; --neg:#fda4af; --th:#6db9ff;
  --h1-size:44px; --h1-lh:1.28; --h1-weight:800;
  --sub-size:22px; --sub-mb:24px;
  --hook-bg:linear-gradient(135deg,#1a1830,#101626);
  --stats-mt:16px; --stat-bg:#131a30; --stat-border:1px solid #26335c; --stat-pad:14px;
  --v-size:31px; --v-color:var(--accent1);
  --h2-size:26px; --h2-color:var(--accent1);
  --td-border:#1c2748; --td-color:#dbe4ff; --td-lh:1.5; --td-wb:normal;
  --note-mt:10px; --note-lh:1.65; --note-color:#7a86b5;
  --insight-size:20px; --insight-color:#e6ebff; --insight-bar:var(--accent1); --insight-bg:#1a1f3d;
  --rows-mb:8px; --rows-color:#dbe4ff; --b-em:var(--accent1);
  --foot-bg:#0d1428; --foot-color:#6b78a5;
  --vs-bg:#101626; --border-soft:#1c2748aa;
}
```

## 2. 产业链研报长图(AI 蓝系,新工程自此系起)

```css
:root {
  --bg:#0b1026; --card:#141b40; --border:#2b3775; --text:#eaf0ff; --dim:#8d97c9;
  --accent1:#ffb347; --accent2:#ff6b9d; --tagbg:#283566; --tagfg:#9db4ff;
  --up:#ff6b6b; --down:#4ade80; --pos:#6ee7b7; --neg:#fda4af; --th:#9db4ff;
  --h1-size:44px; --h1-lh:1.28; --h1-weight:800;
  --sub-size:22px; --sub-mb:24px;
  --hook-bg:linear-gradient(135deg,#2a1f3d,#1a1533);
  --stats-mt:16px; --stat-bg:#1b2452; --stat-border:none; --stat-pad:14px;
  --v-size:31px; --v-color:#6ee7b7;
  --h2-size:26px; --h2-color:#ffd166;
  --td-border:#222c5c; --td-color:#dbe2ff; --td-lh:1.5; --td-wb:normal;
  --note-mt:10px; --note-lh:1.6; --note-color:#7b85b5;
  --insight-size:20px; --insight-color:#e6ebff; --insight-bar:#ff8fab; --insight-bg:#241a3f;
  --rows-mb:8px; --rows-color:#dbe2ff; --b-em:#ffd166;
  --foot-bg:#10163a; --foot-color:#6b75a5;
  --vs-bg:#101626; --border-soft:#222c5caa;
}
```

## 3. 日更数据卡(A 风格炭黑+正红+金,2026-09-18 拍板;daily_longpic.py `_root_vars()` 自动生成,此处为文档化)

```css
:root {
  --bg:#0f1014; --card:#191b22; --border:#343846; --text:#f2f3f7; --dim:#9aa0b5;
  --accent1:#ff4d4f; --accent2:#ffd166; --tagbg:#3a1d1f(天梯)/#1d2a4a(龙虎榜); --tagfg:#ff9c9c/#8fb5ff;
  --up:#ff6b6b; --down:#4ade80; --pos:#ff6b6b; --neg:#4ade80; --th:#ff9c9c/#8fb5ff;
  --h1-size:42px; --h1-lh:1.38; --h1-weight:800;
  --sub-size:21px; --sub-mb:22px;
  --hook-bg:#191b22(=card);
  --stats-mt:14px; --stat-bg:#343846(=border); --stat-border:none; --stat-pad:14px 8px;
  --v-size:32px; --v-color:var(--accent2);
  --h2-size:25px; --h2-color:var(--accent2);
  --td-border:#34384666(=border+66); --td-color:var(--text); --td-lh:1.55; --td-wb:break-all;
  --note-mt:8px; --note-lh:1.6; --note-color:var(--dim);
  --insight-size:19px; --insight-color:var(--text); --insight-bar:var(--accent2); --insight-bg:#34384655(=border+55);
  --rows-mb:6px; --rows-color:var(--text); --b-em:var(--accent2);
  --foot-bg:#191b22(=card); --foot-color:var(--dim);
  --vs-bg:#191b22; --border-soft:#34384655(=border+55);
}
```

> 注:日更系 `--pos/--neg` 与 `--up/--down` 同值(红涨绿跌贯穿到底),系历史口径;研报系双轨分离。
