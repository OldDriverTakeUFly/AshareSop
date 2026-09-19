# davis_analyzer/cardgen/__init__.py
"""cardgen — 小红书金融信息卡片内容生成子系统。

研报/数据 → facts.json(事实清单)→ cards.spec.json($fact 引用)
→ validate(数字/合规/完整性/事实四闸)→ build(渲染+RELEASE.json)→ enqueue。
台账:storage/database/content_cards.db;渲染复用 scripts/card_factory;发布对接 scripts/content_publisher。
设计 spec:docs/superpowers/specs/2026-08-28-cardgen-design.md
"""
