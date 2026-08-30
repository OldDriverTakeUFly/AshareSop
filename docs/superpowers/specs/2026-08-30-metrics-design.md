# 小红书数据回流台账(xhs_metrics)设计

**日期**:2026-08-30 | **状态**:已批准(用户 8/29 定方向:半自动 vision 采集,多账号架构)

## 目标

把发布后的运营数据(阅读/赞/藏/评/转/粉丝数)回流进 SQLite,按内容分组(group)聚合,回答「哪条内容线有流量」。多账号从第一天进表结构。

## 架构

- 独立模块 `davis_analyzer/metrics/`,CLI:`python -m davis_analyzer.metrics {collect|record|report}`
- 独立库 `storage/database/xhs_metrics.db`,不动现有库
- 采集:复用 publisher 浏览器 profile(只读)→ 打开创作者平台笔记管理页 → vision.py(glm-5.3-flash)读结构化 JSON → 落库 `source='vision'`
- 人工兜底:`record` 子命令录入/覆盖,`source='manual'`(校准 vision 读数或补漏)
- 节奏:每晚 21:00 cron 一采(采集近 7 天发布笔记 + 账号快照),7 天窗口自然覆盖 24h/72h 观测点

## 表结构

```sql
accounts(account_id TEXT PRIMARY KEY, platform TEXT, name TEXT, created_at TEXT)
notes(note_id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT, topic TEXT,
      grp TEXT, published_at TEXT, title TEXT, url TEXT, UNIQUE(account_id, title))
note_metrics(note_id INTEGER, captured_at TEXT, views INT, likes INT, collects INT,
             comments INT, shares INT, source TEXT, PRIMARY KEY(note_id, captured_at))
account_metrics(account_id TEXT, captured_at TEXT, followers INT, following INT,
                total_likes INT, source TEXT, PRIMARY KEY(account_id, captured_at))
```

- notes.grp 复用 cardgen spec 的 group 字段(产业链调研/工具方法/个股研报·XX),按 title 前缀与 cardgen RELEASE 对齐;
- 快照式多行,最新行即当前值,增速可由相邻快照差分。

## 质量纪律

- vision 读数非整数/缺失 → 该行跳过并 log,不写脏数据;
- manual 永远覆盖同 captured_at 的 vision 行(人工为真);
- report 按 grp 聚合:阅读中位数/收藏率/赞藏比,标注样本数与首末快照时间。

## 边界(不做)

- 不做自动评论、不碰发布动作(publisher 职责);
- 不逆向任何私有 API,只读创作者平台自己账号的可见页面。
