# 内容自动发布系统(M1)

> 目标:研报 → 小红书卡片 → 发稿池(人审)→ 发布。当前为 **M1**:卡片工厂 + 发稿池台账 + 敏感词扫描,**发布动作仍人工**(M2 引入 Playwright 发布器)。

## 架构

```
研报/手工 → card_factory(JSON spec → HTML → PNG,带溢出校验)
         → content_publisher/queue.py(发稿池 SQLite 状态机)
              draft → [scan 通过] → review(人工) → schedule → (M1 止;M2 发布器)
```

## 卡片工厂 scripts/card_factory/

- `card.css` — 共享卡片样式(6 主题色,1080×1440,源自 GPU四小龙实战调优版)
- `build_cards.py` — JSON spec → cards.html。支持 6 种卡片类型:
  `cover`(封面大数字)/ `profiles`(档案行卡)/ `table`(对比表)/ `bars`(横条图)/ `timeline`(节点日历)/ `summary`(结论+风险)
- `snap.cjs` — Playwright 截图,**内置溢出校验**(内容超高/超宽即中止,防底部裁切——2026-08-28 GPU卡片踩坑沉淀);emoji 渲染依赖 Noto Color Emoji
- `examples/gpu4.json` — 完整示例(GPU四小龙6张卡的纯数据描述,可作新 spec 模板)
- `sensitive_words.txt` — 金融敏感词表(买卖指向/承诺收益类)

```bash
# 生成并截图
.venv/bin/python scripts/card_factory/build_cards.py scripts/card_factory/examples/gpu4.json --out scripts/card_factory/output/gpu4
node scripts/card_factory/snap.cjs scripts/card_factory/output/gpu4/cards.html --outdir scripts/card_factory/output/gpu4 --prefix GPU四小龙
```

## 发稿池 scripts/content_publisher/queue.py

数据库:`storage/database/content_publisher.db`(gitignore,不入库)。两张表:
`publish_queue`(状态机)/ `publish_log`(全事件审计)。

状态机:`draft → reviewed(人工) → scheduled → published/failed`。
**合规闸门**:enqueue 即扫敏感词(命中词表或缺少"不构成投资建议"话术 → `review` 命令拒绝放行);发布频率节流在 M2 实现。

```bash
PY=.venv/bin/python; Q=scripts/content_publisher/queue.py
$PY $Q init                                   # 首次建库
$PY $Q enqueue --title "..." --body "..." --tags "#xx" --images a.png,b.png --source spec路径
$PY $Q scan                                   # 重扫全部 draft
$PY $Q list [--status draft]
$PY $Q review <id>                            # 人工审核(扫描未通过会被拒)
$PY $Q schedule <id> --at "2026-08-30 20:00"  # 排期(M1 到此为止)
$PY $Q mark <id> published|failed             # 人工发布后回填状态
```

## 设计文档

完整设计(五层架构/M2 Playwright 发布器方案/风险清单)见 2026-08-28 会话讨论;M2 开工前提:确认发布账号(建议小号)、频率、平台范围。

## 已知边界

- 敏感词表为初版,`scan` 命令幂等可重扫;表更新后无需改代码
- 卡片 spec 目前手工编写;后续可加"研报元数据 → spec"的自动抽取器(研报元数据块已结构化,顺势)
- `output/` 目录为生成物,已 gitignore
