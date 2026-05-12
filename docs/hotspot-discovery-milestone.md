# 热点发现模块里程碑

## 目标

打造一个可独立演进的 **Hotspot Discovery Layer**，让系统逐步从“市场样本归纳热点”演进到“结合外部证据发现热点”的能力。

## V1（已实现）

- 基于当前 `sectors / fund_flows / gainers` 做热点候选归纳
- 输出结构化 `hotspot_discovery`
- 给出：
  - `themes`
  - `confidence`
  - `matched_sectors`
  - `matched_fund_flows`
  - `matched_stocks`
  - `summary`
- 明确标注当前限制：这是 **sample-driven**，不是新闻/事件驱动的主题发现

## 最终目标

## V2（已完成）

- 在 sample-driven 基础上叠加少量 **curated public evidence**
- 输出：
  - `news_signals`
  - `evidence_sources`
  - `source_mode`
- 当前已输出：
  - `news_events`
  - `event_theme_candidates`
  - `event_backed_themes`
- 仍然不是完整新闻流采集，而是“样本 + curated event/evidence”混合发现
- 当前允许出现三种模式：
  - `sample-only`
  - `sample+evidence`
  - `evidence-only`

## V2.5（当前方向）

- 在 V2 基础上增加第一波**公开页面新闻输入层**
- 当前已接入：
  - 工信部 RSS 订阅页（公开 HTML 列表页）
  - 中国政府网最新政策 JSON
  - 证券时报快讯页（公开 HTML 列表页）
  - 国家发改委通知页（公开 HTML 列表页）
- 输出新增：
  - `raw_news_events`
- 当前方法标识：
  - `sample+public-news-v2.5`
- `raw_news_events` 当前表示：**所有标准化事件输入**（包括 curated evidence 与第一波公开新闻/政策输入）
- 仍然不是完整实时快讯/公告流采集，而是“样本 + curated evidence + first-wave public news/policy inputs”混合发现

### 阶段 2：新闻/事件驱动

- 接入主流财经快讯/新闻源
- 自动抽取近期催化事件
- 输出“事件 → 主题候选”映射

### 阶段 3：证据分级

- 按证据等级组织主题：
  - 一级证据：政策/公告/监管原文
  - 二级证据：行业进展/技术验证
  - 辅助证据：主流财经媒体
  - 信号源：市场样本反馈

### 阶段 4：主题归一与演化

- 做 alias 归一
- 识别“主线/支线/衍生概念”
- 跟踪主题连续性与切换

### 阶段 5：下游联动

- 日报自动引用 `hotspot_discovery`
- 研报可自动建议主题
- 图卡可新增“今日热点榜 / 热点演化卡”

## 当前边界

- V1 只能发现 **市场样本热点**
- V2 只能发现 **市场样本热点**、**市场样本 + 少量人工整理证据包的热点**，以及少量 **evidence-only** 候选
- V2.5 还能纳入少量来自公开页面的新闻事件输入，但仍不是完整实时新闻流
- 当前版本仍不能替代实时新闻流、政策流或公告流驱动的事件级主题发现
- 不能单独证明“事件催化已成立”
- 不能替代新闻/政策/公告验证
