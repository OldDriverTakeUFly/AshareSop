# 金融信息卡片生成系统(cardgen)设计

日期:2026-08-28
状态:待用户审阅
关联:scripts/card_factory(JSON spec→HTML→PNG 渲染器)、scripts/content_publisher(发稿池 M1)、davis_analyzer(金融数据与因子层)

## 一、背景与定位

小红书金融卡片链路已建成后半段:card_factory 负责**渲染**(spec JSON → HTML → PNG,无 LLM),content_publisher 负责**发布管理**(发稿池台账 + 敏感词扫描 + 状态机 + 人工审核)。缺失的是前半段——**内容生产与质量**:

- 选题从哪来、素材如何沉淀;
- 卡片 spec 里的数字目前由 agent 会话手写,无机器可验证的溯源链路(金融内容最大风险点);
- 合规扫描只覆盖发稿阶段的 title/body/tags,卡片图片内文字(即 spec 正文)不在扫描范围;
- 无时效管控(估值/行情数字过期的卡片发出去是事故)、无修订链。

cardgen 补这段:**研报/数据 → 事实清单 → 卡片 spec → 质量闸门 → 发布包**,并与下游两个系统定义机器契约。

**M1 不做**:自动选题、OCR、自动发布、效果回流分析(点赞/收藏→选题看板)、全局事实库(见决策记录 #3)。

## 二、方案对比

| 方案 | 形态 | 取舍 |
|---|---|---|
| **A(采用)** | davis_analyzer 独立子系统 `davis_analyzer/cardgen/`,工程目录在 `docs/小红书卡片/<topic>/`,台账独立库,经 RELEASE.json 契约对接 publisher | 数字事实采集天然依赖 davis 数据层;与 limitup/intraday 同构;三个系统松耦合,各 session 边界清晰 |
| B | 生成+校验+台账全部并入 scripts/card_factory | 工具链一体,但 card_factory 膨胀成 monolith,且从 scripts 侧反向依赖 davis 数据层,职责混乱 |
| C | 生成逻辑作为 content_publisher 的上游模块,台账并入 content_publisher.db | 链路一体化,但发布系统 session 的库被上游写入,两会话边界耦合 |

## 三、双层内容模型(已确认)

- **硬内容层(确定性)**:数字/表格/条形图/估值条目从 davis_analyzer 数据层(Tushare 缓存/因子引擎)或研报文本确定性提取,逐条登记事实清单,带 as_of 与来源指纹。
- **叙事层(agent 蒸馏)**:观点、框架解读、金句由 agent 从深度研报蒸馏;研报是叙事的唯一真相源,观点须能指回研报章节。
- 两路汇入同一质量闸门。

## 四、工程目录与文件契约

```
docs/小红书卡片/<topic>/
  facts.json         事实清单(真相层,进 git)
  cards.spec.json    卡片 spec(card_factory 格式 + $fact 扩展,进 git)
  compliance_waivers.json   敏感词豁免登记(可选,进 git)
  output/            渲染产物(HTML/PNG/manifest,card_factory 输出,进 git)
  RELEASE.json       发布包(对接 content_publisher,进 git)
```

### 4.1 facts.json

```json
{
  "facts": [
    {
      "id": "muxi_ps_ttm",
      "value": 143,
      "unit": "x",
      "display": "143x",
      "as_of": "2026-08-28",
      "source": {"kind": "report", "ref": "docs/个股研报/.../沐曦深度研报.md#估值", "quote": "PS(TTM) 143x"},
      "expires": "2026-09-04"
    }
  ]
}
```

- `source.kind ∈ {report, tushare, manual}`:
  - `report`:研报路径 + 章节锚点 + 原文引句;
  - `tushare`:查询指纹(如 `daily_basic:688802@2026-08-27:ps_ttm`),可回放验证;
  - `manual`:人工录入,必须写 ref 备注(审核时重点看)。
- `expires` 可选,缺省 = `as_of + 7 天`;行情/估值类事实应配短 expires,行业叙事类可配长。

### 4.2 spec 的 $fact 扩展(不改 card_factory)

`cards.spec.json` 保持与 card_factory 兼容的格式,数字字段两态:

- **结构化字段**(cover.stats 的 v、table cells、bars.value 等):必须写 `{"$fact": "muxi_ps_ttm"}`,build 前由 cardgen **物化**为 display 文本,产出 `output/spec.materialized.json` 喂给 card_factory。强校验,杜绝转写错位。
- **叙事字段**(quote/desc/foot 等 HTML 内):允许裸写数字,validate 时提取比对(见 5.1)。

### 4.3 RELEASE.json(发布包契约)

```json
{
  "topic": "GPU四小龙",
  "version": 1,
  "as_of": "2026-08-28",
  "expires_at": "2026-09-04",
  "images": ["docs/小红书卡片/GPU四小龙/output/xxx_01_封面.png", "..."],
  "facts_digest": "sha256:...",
  "validate": {"passed": true, "ts": "2026-08-28T21:00:00"},
  "cardgen_version": "0.1"
}
```

- `expires_at` = 所引用事实 `expires` 的**最小值**(保守取最早过期)。
- 接口约定:M1 由 cardgen 在 build/enqueue 时检查过期;content_publisher 侧 schedule 硬闸(读 RELEASE 拒绝过期排期)留给发布系统 M2,本期只交付契约。

## 五、质量闸门

### 5.1 机器闸(cardgen validate,不过不准 build)

1. **数字全量核对(双层)**:
   - 结构化字段:`$fact` 引用必须命中 facts.json,id 重复/缺失即失败;
   - 叙事字段:正则提取全部数值 token,**每个必须命中事实清单**(display 文本精确匹配优先;次选值+单位归一化等价判定:亿/万换算,数值精确相等、不设容差),未命中 → 失败并列出「未溯源数字 + 所在卡片」;
   - 豁免规则(代码内置,spec 作者不可关闭):纯日期(mm/dd、yyyy、季度词、"9-10月"类区间)、中文数字(四小龙/九…)、标识符内数字(股票代码 688801、产品型号 C600/BR166/B30A)

2. **合规扫描**:敏感词(复用 `card_factory/sensitive_words.txt`)+ 必备免责话术(「不构成投资建议」必须在尾卡 foot)+ 每卡 foot 必含数据来源标注。**扫描对象是 spec 全文本**——spec 即图片上全部文字,等价于扫图,修复 publisher M1 只扫 title/body/tags 的缺口。
   - 误伤豁免:命中可经 `compliance_waivers.json` 登记 `{word, card, reason}` 人工放行(如「非目标价」否定语境),豁免记录随工程进 git 可审计。

3. **完整性**:封面卡与尾卡存在;RELEASE 必填字段齐全。(渲染产物张数与 manifest 一致为 build 阶段的内置检查,不属于 validate。)

4. **事实清单自检**:每条 fact 必须有 source;`expires >= as_of`;id 唯一;`display` 与 `value+unit` 自洽。

### 5.2 agent 纪律(写入 AGENTS/SOP)

- 叙事观点须能指回研报章节,禁止自造观点;
- 数字只能来自事实清单,禁止在叙事里引入未登记数字;
- 敏感词表更新必须显式提交,不得运行时改动。

### 5.3 人工闸(沿用 content_publisher)

review 看渲染图,schedule 排期,发布留人工。

**责任分层**:机器管「数字对不对、词合不合规、要素全不全」,agent 管「观点有没有出处」,人管「值不值得发、图好不好看」。

## 六、台账与生命周期

库:`storage/database/content_cards.db`(独立,不写 content_publisher.db)

- `cards(topic PK, spec_path, current_version, status, as_of, created_at, updated_at)`
- `revisions(topic, version, parent_version, reason, facts_digest, ts)`
- `validate_log(ts, topic, version, passed, failures_json)`

状态机:`drafting → validated → rendered → queued`(published 在 publisher 侧,不重复记)。

- **修订链**:spec/facts 任何变更 → version+1 → 重新全量 validate;已 queued 的版本变更必须填 reason。PNG 不做多版本目录(git 留痕),指纹(digest)进 revisions 表。
- **时效管控**:validate 计算 `expires_at = min(facts.expires)`;build 时已过期 → 拒绝;enqueue 命令执行前再查一次。

## 七、端到端数据流

```
docs 深度研报 ──(agent 提取,带章节锚点)──→ facts.json ←──(ingest 脚本,带查询指纹)── davis 数据层
                                                  ↓
                                          cards.spec.json($fact 引用 + 叙事)
                                                  ↓
                                     cardgen validate(机器闸 ×4)
                                        ↓ pass                ↓ fail
                              cardgen build          报告:未溯源数字/敏感词/缺要素
                        (物化 → card_factory → PNG)
                                        ↓
                              RELEASE.json + enqueue 命令
                                        ↓
                        content_publisher(人工 review → schedule → 发布)
```

## 八、CLI

```
python -m davis_analyzer.cardgen
  init --topic GPU四小龙 --source <研报路径...>   # 建工程目录 + 台账登记
  ingest --topic X --fact ps_ttm:688802           # 从 DB 拉数值事实追加 facts.json(按需逐条)
  validate --topic X                              # 全量闸门,输出失败明细
  build --topic X [--bump --reason "..."]         # 物化 + 渲染 + RELEASE(变更时 version+1)
  status [--topic X]                              # 台账/时效一览
  enqueue --topic X                               # 过期检查后打印 publisher enqueue 完整命令
```

主要用户是 agent(交互式会话):init → 填 facts+spec → validate 循环修正 → build → enqueue。定时任务不适用本系统(创作型流程)。

## 九、测试与验收

pytest(`davis_analyzer/tests/test_cardgen_*.py`,fixture 惯例对齐 conftest):

- 数字提取器:`17.36亿` / `143x` / `+1998%` / `+326%~455%` 区间 / `3.5倍` / `75%` / `≈16x` / `-43%` / `400-600亿` 区间 / 日期、中文数字、标识符豁免(688801、C600);
- `$fact` 物化产物与 card_factory 兼容(以 examples/gpu4.json 改造为 fixture);
- 叙事裸数字命中/未命中事实清单;
- 合规扫描覆盖 spec 全文本 + waivers 豁免生效;
- expires 最小值聚合;
- validate 集成:每类失败模式各有坏 fixture 用例。

**验收标准**:GPU四小龙卡片用 cardgen 流程完整重建一遍,validate 全过,产出与现有成品一致(或差异可解释并记录)。

## 十、M1 / M2 切分

- **M1**:上述全部。
- **M2 候选**(按需再立项):publisher 侧时效硬闸(queue.py 改造,消费 RELEASE)、全局事实库(跨卡片复用同一数字)、选题看板(选题→素材→草稿→成卡,防重复选题)、效果回流分析、自动选题建议。

## 十一、关键设计决策记录

1. **双层混合**(用户确认):数字确定性生成 + 叙事研报蒸馏。
2. **全量数字核对**(用户确认):结构化 `$fact` 强引用 + 叙事提取比对,豁免规则内置。
3. **事实清单随工程,不建全局事实库**:第一张卡先跑通,YAGNI;source 规范保证日后可合并成全局库(用户多选未逐项确认,此为待确认假设)。
4. **不改 card_factory 渲染器**:$fact 物化在喂渲染器之前完成,渲染器保持纯函数。
5. **扫 spec 即扫图片文字**:不做 OCR,spec 是图片文字的唯一真相源。
6. **台账独立库**:两 session 系统经 RELEASE.json 契约松耦合,互不写对方的库。
7. **时效默认 7 天、按事实覆写**:估值/行情短、叙事长;expires_at 取最小值保守策略。

## 十二、待用户确认的假设

- 内容管理范围多选未收到回答,按推荐纳入:时效管控、修订链版本化;「选题看板」与「全局事实库复用」推迟到 M2(假设三、七与此相关);
- 时效默认 7 天阈值是否合适;
- waivers 误伤豁免机制是否接受(替代方案:改写文案绕词,无豁免登记)。
