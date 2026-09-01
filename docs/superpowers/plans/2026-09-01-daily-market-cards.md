# 每日盘面复盘卡片(连板天梯 + 龙虎榜)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 脚本全自动生成每日《连板天梯》与《龙虎榜》两张数据复盘卡(读 stockhot.db → facts+spec → cardgen 四道闸 → 渲染),挂工作日 20:30 cron,发布留人工。

**Architecture:** 生成逻辑放 `davis_analyzer/cardgen/daily.py`(可测试的包内模块),薄 CLI 入口 `scripts/daily_market_cards.py`(参照 `scripts/daily_bulletin.py` 先例)。复用 cardgen 现有 validate/render/ledger,publish_sync 与 builder 做嵌套 topic 适配。前置修复 `stockhot/dragon_tiger` 机构席位字段缺失。

**Tech Stack:** Python 3.11+(`.venv/bin/python`,从仓库根目录运行)、sqlite3 只读查询、pytest、既有 card_factory 渲染链(禁改)。

**Spec:** `docs/superpowers/specs/2026-09-01-daily-market-cards-design.md`(已批准)

## Global Constraints

- 一切命令从仓库根目录 `/home/leo/Projects/CodeAgentDashboard` 运行,解释器 `.venv/bin/python`。
- **禁改** `scripts/card_factory/*` 与 `scripts/content_publisher/*`(对接只读)。
- 卡片叙事**零观点、纯数据描述句**;所有数字走 `$fact` 结构化引用或精确匹配 facts 值+单位。
- 金额措辞只用「净买额/净卖额」,禁用「买入额/卖出额」(敏感词表含 `买入`/`卖出`)。
- 敏感词禁用:追高/上车/抄作业/标的/庄家/主力/拉盘/内幕/赌/仓位/梭哈 等(全表见 `scripts/card_factory/sensitive_words.txt`);规避诱导句式(`买的是什么`/`你应该`/`下一个动作`)。
- `tag_top` 禁止含日期(validator 闸4c);日期在正文用 ISO `2026-09-01` 或 `09-01` 形态(数字闸豁免),**禁用「9月1日」形态**(`1日` 会产生 token `(1,"")`)。
- 卡内文本禁阿拉伯数字裸奔:板块名含数字须映射(`3D打印→三维打印`)或剔除;上榜原因须映射为无数字短标签。
- topic 命名:`连板天梯/<YYYY-MM-DD>`、`龙虎榜/<YYYY-MM-DD>`;工程落 `docs/小红书卡片/未发布/<topic>/`。
- facts `expires = as_of`(当日有效;次日 build/enqueue 均被拒,复盘卡过时不发)。
- 金额显示口径:元 → 亿,保留 2 位小数,净额带符号(`-1.37亿`);fact value 取绝对值、unit `亿`。
- loguru 打日志;`print()` 只允许出现在 `scripts/daily_market_cards.py`(CLI 用户输出)。
- 每卡 foot:`数据来源:沪深交易所/东方财富(经 stockhot 采集) · 仅供研究参考,不构成投资建议`;尾卡额外带「市场有风险,投资需谨慎」。
- 测试用 `CARDGEN_PROJECT_ROOT`/`CARDGEN_LEDGER_DB`/`CARDGEN_STOCKHOT_DB`(本计划新增)env 重定向,不碰真实库和 docs/。
- Conventional Commits 中文 scope,如 `feat(cardgen): ...`。

---

### Task 1: facts.py 放行 source.kind="stockhot"

**Files:**
- Modify: `davis_analyzer/cardgen/facts.py:15` 与 `check_facts` 错误文案(`davis_analyzer/cardgen/facts.py:54`)
- Test: `davis_analyzer/tests/test_cardgen_facts.py`(追加用例)

**Interfaces:**
- Produces: `SOURCE_KINDS = ("report", "tushare", "manual", "stockhot")`;后续 Task 5/6 构造 `Fact(source_kind="stockhot", source_ref="stockhot.db:<表>:<analysis_type>@<日期>:<json键>")`。

- [ ] **Step 1: 写失败测试**(追加到 `davis_analyzer/tests/test_cardgen_facts.py`,沿用该文件现有 Fact 构造惯例)

```python
def test_source_kind_stockhot_accepted():
    from decimal import Decimal
    from davis_analyzer.cardgen.facts import check_facts
    from davis_analyzer.cardgen.types import Fact
    f = Fact(id="zt_count", value=Decimal("83"), unit="只", display="83只",
             as_of="2026-09-01", source_kind="stockhot",
             source_ref="stockhot.db:daily_data:limit_up_pool@2026-09-01:len")
    assert check_facts([f]) == []

def test_source_kind_unknown_still_rejected():
    from decimal import Decimal
    from davis_analyzer.cardgen.facts import check_facts
    from davis_analyzer.cardgen.types import Fact
    f = Fact(id="x", value=Decimal("1"), unit="", display="1",
             as_of="2026-09-01", source_kind="eastmoney", source_ref="r")
    errs = check_facts([f])
    assert any("source_kind 非法" in e for e in errs)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest davis_analyzer/tests/test_cardgen_facts.py -k stockhot -v`
Expected: FAIL(`source_kind 非法 stockhot`)

- [ ] **Step 3: 最小实现**

```python
SOURCE_KINDS = ("report", "tushare", "manual", "stockhot")
```
`check_facts` 中错误文案改为:`f"fact {f.id}: source_kind 非法 {f.source_kind}(须 report/tushare/manual/stockhot)"`

- [ ] **Step 4: 跑测试确认通过**(整个文件,防回归)

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_cardgen_facts.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/cardgen/facts.py davis_analyzer/tests/test_cardgen_facts.py
git commit -m "feat(cardgen): 事实溯源 source.kind 新增 stockhot——每日复盘卡消费 stockhot.db 数据的指纹通道"
```

---

### Task 2: 嵌套 topic 基础设施(publish_sync 两级发现 + builder PNG 前缀)

背景:topic 将是 `连板天梯/2026-09-01` 形态。两处现状不兼容:
①`publish_sync._published_topics` 只取 source 路径第一段(`tail[0]`),嵌套 topic 会被折叠成 `连板天梯`,任一天发布会误判整个品类已发布;`sync()` 只扫一层目录。
②`builder.render` 用 topic 直接做 PNG 文件名前缀(`snap.cjs --prefix topic` 与 `glob(f"{topic}_*.png")`),含 `/` 会破坏文件名与 glob。

**Files:**
- Modify: `davis_analyzer/cardgen/publish_sync.py`(`_published_topics`/`sync`/`_move`/`demote_to_pending`)
- Modify: `davis_analyzer/cardgen/builder.py:172,182`(PNG 前缀)
- Test: `davis_analyzer/tests/test_cardgen_publish_sync.py`(新建)

**Interfaces:**
- Produces:
  - `publish_sync.sync(projects_root, db, dry_run)` 语义扩展:两级发现——目录含 `cards.spec.json` 视为平铺工程(topic=目录名),否则视为品类容器,其下含 `cards.spec.json` 的子目录为工程(topic=`品类/日期`)。
  - `publish_sync.demote_to_pending(projects_root, proj)` 支持嵌套(按 `proj.relative_to(projects_root/已发布)` 推回)。
  - `builder._png_prefix(topic: str) -> str`:返回 `topic.replace("/", "_")`;`render` 内两处使用。

- [ ] **Step 1: 写失败测试**(新建 `davis_analyzer/tests/test_cardgen_publish_sync.py`)

```python
"""嵌套日期 topic(连板天梯/2026-09-01)的 sync/归档/降级与 builder PNG 前缀。"""
import sqlite3

from davis_analyzer.cardgen import publish_sync
from davis_analyzer.cardgen.builder import _png_prefix


def _mk_proj(base, topic: str) -> None:
    d = base / topic
    d.mkdir(parents=True)
    (d / "cards.spec.json").write_text("{}", encoding="utf-8")


def _mk_pub_db(tmp_path, sources: list[str]):
    db = tmp_path / "pub.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE publish_queue(id INTEGER PRIMARY KEY, source TEXT, status TEXT)")
    for s in sources:
        con.execute("INSERT INTO publish_queue(source, status) VALUES(?, 'published')", (s,))
    con.commit()
    con.close()
    return db


def test_nested_topic_moves_only_that_date(tmp_path):
    root = tmp_path / "卡片"
    _mk_proj(root / "未发布", "连板天梯/2026-09-01")
    _mk_proj(root / "未发布", "连板天梯/2026-09-02")
    db = _mk_pub_db(tmp_path, ["docs/小红书卡片/未发布/连板天梯/2026-09-01"])
    actions = publish_sync.sync(root, db=db)
    assert (root / "已发布" / "连板天梯" / "2026-09-01").is_dir()
    assert (root / "未发布" / "连板天梯" / "2026-09-02").is_dir()
    assert len(actions) == 1


def test_flat_topic_backward_compat(tmp_path):
    root = tmp_path / "卡片"
    _mk_proj(root, "GPU四小龙")  # 存量根目录平铺工程
    db = _mk_pub_db(tmp_path, ["docs/小红书卡片/GPU四小龙"])
    publish_sync.sync(root, db=db)
    assert (root / "已发布" / "GPU四小龙").is_dir()


def test_category_dir_without_spec_not_moved_as_project(tmp_path):
    root = tmp_path / "卡片"
    _mk_proj(root / "未发布", "龙虎榜/2026-09-01")
    db = _mk_pub_db(tmp_path, [])  # 无已发布记录
    actions = publish_sync.sync(root, db=db)
    assert actions == []
    assert (root / "未发布" / "龙虎榜" / "2026-09-01").is_dir()


def test_demote_nested_to_pending(tmp_path):
    root = tmp_path / "卡片"
    _mk_proj(root / "已发布", "连板天梯/2026-09-01")
    new = publish_sync.demote_to_pending(root, root / "已发布" / "连板天梯" / "2026-09-01")
    assert new == root / "未发布" / "连板天梯" / "2026-09-01"
    assert new.is_dir()


def test_png_prefix_replaces_slash():
    assert _png_prefix("连板天梯/2026-09-01") == "连板天梯_2026-09-01"
    assert _png_prefix("GPU四小龙") == "GPU四小龙"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_cardgen_publish_sync.py -v`
Expected: FAIL(`_png_prefix` 不存在;nested 用例整目录被挪/不挪)

- [ ] **Step 3: 实现 publish_sync 两级发现**

`_published_topics` 尾部改(剥离归档层级后取**全路径**):

```python
    topics = set()
    for (source,) in rows:
        parts = Path(str(source)).parts
        if _MARKER in parts:
            tail = parts[parts.index(_MARKER) + 1:]
            # 剥掉归档层级(兼容 source 记录了 已发布/<topic> 的情况)
            tail = tuple(p for p in tail if p not in (PENDING_DIR, PUBLISHED_DIR))
            if tail:
                # 2026-09-01 嵌套 topic:品类/日期 取全路径,防整品类误判已发布
                topics.add("/".join(tail))
    return topics
```

`sync()` 的遍历段整体替换为:

```python
    def _iter_projects(base: Path):
        """两级发现:含 cards.spec.json 的目录=工程;否则=品类容器,其下含 spec 的子目录=日期工程。"""
        for p in sorted(x for x in base.iterdir() if x.is_dir()):
            if p.name in (PENDING_DIR, PUBLISHED_DIR):
                continue
            if (p / "cards.spec.json").exists():
                yield p.name, p
                continue
            for sub in sorted(x for x in p.iterdir() if x.is_dir()):
                if (sub / "cards.spec.json").exists():
                    yield f"{p.name}/{sub.name}", sub

    def _move(topic: str, proj: Path, dest_dir: str, label: str) -> None:
        dst = projects_root / dest_dir / topic
        entry = (f"{label}→{dest_dir}", str(dst))
        actions.append(entry)
        if not dry_run:
            if _safe_move(proj, dst):
                _update_ledger_spec_path(projects_root, topic, dst / "cards.spec.json")

    # 根目录与 未发布/ 下的工程:已发布→已发布/,其余(仅根目录存量)→未发布/
    for base, strict in ((projects_root, False), (projects_root / PENDING_DIR, True)):
        if not base.exists():
            continue
        for topic, proj in _iter_projects(base):
            if topic in published:
                _move(topic, proj, PUBLISHED_DIR, "已发布")
            elif not strict:
                _move(topic, proj, PENDING_DIR, "归位")
    return actions
```

`demote_to_pending` 改为按相对路径推回:```python
def demote_to_pending(projects_root: Path, proj: Path) -> Path | None:
    """build --bump 产生新版本时,把已发布工程挪回未发布;返回新路径(无需挪则 None)。"""
    if proj.parent.name != PUBLISHED_DIR:
        return None
    topic = str(proj.relative_to(projects_root / PUBLISHED_DIR))
    dst = projects_root / PENDING_DIR / topic
    if _safe_move(proj, dst):
        _update_ledger_spec_path(projects_root, topic, dst / "cards.spec.json")
        return dst
    return proj if proj.exists() else None
```

注意:品类容器目录(无 `cards.spec.json`的一层目录)不会被当作工程归位——它只参与发现;"归位仅根目录存量"的判断保留在外层循环 `strict` 上(与原逻辑一致)。

- [ ] **Step 4: 实现 builder PNG 前缀**

`davis_analyzer/cardgen/builder.py` 新增模块级函数并替换两处使用:

```python
def _png_prefix(topic: str) -> str:
    """嵌套 topic(连板天梯/2026-09-01)作文件名前缀时压平斜杠。"""
    return topic.replace("/", "_")
```

`render()` 内:
```python
    prefix = _png_prefix(topic)
    _run(["node", str(REPO_ROOT / "scripts" / "card_factory" / "snap.cjs"),
          str(out / "cards.html"), "--outdir", str(out), "--prefix", prefix])
```
以及 RELEASE images 行:
```python
               "images": [str(p.relative_to(project_dir)) for p in sorted(out.glob(f"{prefix}_*.png"))],
```

- [ ] **Step 5: 跑测试确认通过 + 回归 cardgen 全套**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_cardgen_publish_sync.py davis_analyzer/tests/test_cardgen_cli.py davis_analyzer/tests/test_cardgen_builder.py -v`
Expected: 全 PASS(现有 sync/cli 用例不回归)

- [ ] **Step 6: Commit**

```bash
git add davis_analyzer/cardgen/publish_sync.py davis_analyzer/cardgen/builder.py davis_analyzer/tests/test_cardgen_publish_sync.py
git commit -m "feat(cardgen): 嵌套日期topic支持——sync两级发现+已发布全路径判定+PNG前缀压平斜杠"
```

---

### Task 3: 修复 stockhot/dragon_tiger 机构席位字段缺失

背景:2026-09-01 `analysis_results.dragon_tiger.institutional` 270 行只有 `inst_code/buy_amount/sell_amount`,缺 `inst_name`(映射声明 `exile→inst_name` 未命中实际返回列)与 `net_amount`(导致排序失效、summary 机构净额恒 0)。

**Files:**
- Modify: `stockhot/dragon_tiger/__init__.py`(`fetch_institutional_trading` Tushare 路径)
- Test: `stockhot/tests/unit/test_dragon_tiger.py`(追加)

**Interfaces:**
- Produces: `fetch_institutional_trading` 返回行保证含 `inst_name`(缺源列时 `"机构专用"`)与 `net_amount`(缺源列时 `buy_amount - sell_amount`)。Task 5 的 lhb 生成器依赖此不变量。

- [ ] **Step 1: 探测真实列名**(决定映射怎么改;token 在根目录 .env)

```bash
cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -c "
from dotenv import load_dotenv; load_dotenv()
from stockhot.data_layer import get_gateway
df = get_gateway().call('top_inst', trade_date='20260901')
print(df.columns.tolist()); print(df.head(3).to_string())
"
```

观察 `columns`:记录机构名称实际列名(可能就叫 `exile` 但当日为空,或列名不同如 `name`)与净额列(可能无 `net`)。**若探测与假设不符,以实测为准调整 Step 3 映射**;若 `exile` 列存在且有值,则缺陷只在 `net`,修复退化为净额兜底。

- [ ] **Step 2: 写失败测试**(追加到 `stockhot/tests/unit/test_dragon_tiger.py`,沿用其 DataFrame 惯例)

```python
# Tushare top_inst 缺 exile/net 列的现实形态(2026-09-01 实测缺失)
_INST_DF_TS_SPARSE = pd.DataFrame(
    {
        "ts_code": ["000560.SZ", "002084.SZ"],
        "buy": [30000000.0, 12000000.0],
        "sell": [40000000.0, 5000000.0],
    }
)


def test_fetch_institutional_fills_missing_name_and_net(monkeypatch):
    """缺 exile/net 列时:inst_name 兜底『机构专用』,net=buy-sell。"""
    seen = {}

    class FakeGateway:
        def call(self, api, **kw):
            seen["api"] = api
            return _INST_DF_TS_SPARSE.copy()

    # fetch_institutional_trading 内部是 `from stockhot.data_layer import get_gateway`
    # 的函数级局部导入——patch 模块属性即可在调用时生效
    import stockhot.data_layer
    monkeypatch.setattr(stockhot.data_layer, "get_gateway", lambda: FakeGateway())

    rows = dt.fetch_institutional_trading("2026-09-01", "2026-09-01")
    assert seen["api"] == "top_inst"
    assert rows and rows[0]["inst_name"] == "机构专用"
    assert rows[0]["net_amount"] == -10000000.0
    assert rows[1]["net_amount"] == 7000000.0
```

注意:该用例会真实触发 AKShare 兜底吗?不会——Tushare 路径返回非空即直接 return,不会走到兜底分支;且 `import akshare` 在模块顶,若环境无该依赖导致收集期 import 失败,给用例加 `pytest.importorskip("akshare")`。

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv/bin/python -m pytest stockhot/tests/unit/test_dragon_tiger.py -k institutional_fills -v`
Expected: FAIL(`rows[0]['inst_name']` KeyError 或 net 缺失断言失败)

- [ ] **Step 4: 实现**(Tushare 循环内,`all_rows.extend(...)` 之后统一兜底)

```python
    while cur <= end_dt:
        d = cur.strftime("%Y%m%d")
        df_ts = get_gateway().call("top_inst", trade_date=d)
        if df_ts is not None and not df_ts.empty:
            all_rows.extend(_extract_rows(df_ts, _INST_FIELDS_TUSHARE))
        cur += timedelta(days=1)
    # 现实兜底(2026-09-01 实测):top_inst 部分数据档位缺 exile/net 列——
    # 机构名称一律『机构专用』(该接口本就是机构席位聚合口径),净额=买卖差
    for row in all_rows:
        if not row.get("inst_name"):
            row["inst_name"] = "机构专用"
        if row.get("net_amount") is None:
            row["net_amount"] = (row.get("buy_amount") or 0.0) - (row.get("sell_amount") or 0.0)
```

若 Step 1 探测出机构名称真实列名(非 `exile`),同步把 `_INST_FIELDS_TUSHARE` 该键改为实测列名,`inst_name` 兜底保留。

- [ ] **Step 5: 跑测试确认通过 + 全文件回归**

Run: `.venv/bin/python -m pytest stockhot/tests/unit/test_dragon_tiger.py -v`
Expected: 全 PASS

- [ ] **Step 6: 用当日真实数据端到端验证并回写 DB**

```bash
cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -c "
from dotenv import load_dotenv; load_dotenv()
from stockhot.dragon_tiger import run_dragon_tiger_analysis
r = run_dragon_tiger_analysis('2026-09-01')
inst = r['data']['institutional']
print('rows:', len(inst)); print(inst[:2])
"
```
Expected: 行含 `inst_name='机构专用'` 与非零 `net_amount`;随后确认落库:

```bash
.venv/bin/python -c "
import sqlite3, json
con = sqlite3.connect('storage/database/stockhot.db')
row = con.execute(\"SELECT result_json FROM analysis_results WHERE trade_date='2026-09-01' AND analysis_type='dragon_tiger'\").fetchone()
inst = json.loads(row[0])['institutional']
print(len(inst), inst[0])
"
```
Expected: DB 内 institutional 行已带 `inst_name`/`net_amount`。

- [ ] **Step 7: Commit**

```bash
git add stockhot/dragon_tiger/__init__.py stockhot/tests/unit/test_dragon_tiger.py
git commit -m "fix(dragon_tiger): 机构席位字段兜底——inst_name 缺源列时『机构专用』、net=买卖差,修复排序与净额统计恒零"
```

---

### Task 4: cardgen/daily.py 数据读取层 + 连板天梯生成器

**Files:**
- Create: `davis_analyzer/cardgen/daily.py`
- Test: `davis_analyzer/tests/test_cardgen_daily.py`(新建)

**Interfaces:**
- Consumes: `Fact`/`ValidateReport`(types)、`save_facts`/`check_facts`(facts)、`run_validation`(validator)、`ledger.connect/register_card/log_validate/set_status`、Task 1 的 `source_kind="stockhot"`。
- Produces(Task 6 与测试依赖,签名精确):

```python
DEFAULT_STOCKHOT_DB = REPO_ROOT / "storage" / "database" / "stockhot.db"

class DailyDataMissing(RuntimeError): ...

def fetch_day_bundle(db_path: Path, day: str) -> dict:
    """day 为 'YYYY-MM-DD'。返回 keys: pool(list) / broken(list) / down(list) /
    boards(list[{board_count:int, stocks:list[{code,name}]}]) / prev_boards_max(int|None) /
    lhb_detail(list) / brokers(list) / institutional(list)。只读连接(mode=ro)。"""

def build_ladder(day: str, bundle: dict) -> tuple[list[Fact], dict]:
    """连板天梯 5 页卡的 (facts, spec)。"""

def write_project(projects_root: Path, topic: str, facts: list[Fact], spec: dict) -> Path:
    """建 未发布/<topic>/ 工程目录,写 facts.json + cards.spec.json,建 output/。"""

def generate(kind: str, day: str, projects_root: Path, ledger_db: Path | None,
             stockhot_db: Path | None = None) -> tuple[Path, str, ValidateReport]:
    """kind ∈ {'ladder','lhb'};完整性自检→build→write→register→validate→log+set_status。
    返回 (project_dir, topic, report)。缺数据 raise DailyDataMissing。
    stockhot_db 缺省取 env CARDGEN_STOCKHOT_DB 或 DEFAULT_STOCKHOT_DB。"""
```

`daily.py` 顶部:

```python
# davis_analyzer/cardgen/daily.py
"""每日盘面复盘卡生成器(2026-09-01):stockhot.db 当日数据 → facts+spec → validate。

叙事纪律(spec §4.3):零观点、纯数据描述句;所有数字 $fact 引用或与 facts 值+单位严格一致。
措辞红线:只用事实词汇(N连板/净买额/换手/封单);禁 追高/上车/抄作业/标的/庄家/主力/拉盘/内幕/赌;
金额口径只用「净买额/净卖额」(敏感词表含二字词 买入/卖出);正文日期用 ISO 或 09-01 形态,禁「9月1日」。"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from decimal import Decimal
from pathlib import Path

from loguru import logger

from davis_analyzer.cardgen import ledger
from davis_analyzer.cardgen.facts import save_facts
from davis_analyzer.cardgen.types import Fact, ValidateReport
from davis_analyzer.cardgen.validator import run_validation

REPO_ROOT = Path(__file__).resolve().parents[2]
PENDING_DIR = "未发布"
FOOT = "数据来源:沪深交易所/东方财富(经 stockhot 采集) · 仅供研究参考,不构成投资建议"
FOOT_LAST = FOOT + "。市场有风险,投资需谨慎。"


class DailyDataMissing(RuntimeError):
    """当日采集数据不完整,拒绝生成。"""
```

- [ ] **Step 1: 写失败测试**(新建 `davis_analyzer/tests/test_cardgen_daily.py`;fixture 迷你库用 2026-09-01 真实形态)

```python
"""每日复盘卡生成器:迷你 stockhot.db fixture → build → run_validation 四道闸全过。"""
import json
import sqlite3
from pathlib import Path

import pytest

from davis_analyzer.cardgen import daily

DAY = "2026-09-01"
PREV = "2026-08-31"


def _mk_db(tmp_path: Path, *, with_lhb: bool = True, with_prev: bool = True,
           with_inst: bool = True) -> Path:
    db = tmp_path / "stockhot.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE daily_data(
        id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT NOT NULL,
        data_type TEXT NOT NULL, data_json TEXT NOT NULL,
        UNIQUE(trade_date, data_type))""")
    con.execute("""CREATE TABLE analysis_results(
        id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT NOT NULL,
        analysis_type TEXT NOT NULL, result_json TEXT NOT NULL,
        UNIQUE(trade_date, analysis_type))""")
    pool = [
        {"code": "002084.SZ", "name": "海鸥住工", "change_pct": 10.03,
         "seal_amount": 171719954.0, "max_board": 7.0, "consecutive_boards": 7.0,
         "sector": "家居用品", "broken_count": 0.0, "first_seal_time": "92500",
         "last_seal_time": "92500", "turnover_rate": 13.81},
        {"code": "002855.SZ", "name": "捷荣技术", "change_pct": 10.0,
         "seal_amount": 80000000.0, "max_board": 6.0, "consecutive_boards": 6.0,
         "sector": "消费电子", "broken_count": 1.0, "first_seal_time": "100001",
         "last_seal_time": "100001", "turnover_rate": 8.5},
        {"code": "600371.SH", "name": "万向德农", "change_pct": 10.0,
         "seal_amount": 60000000.0, "max_board": 6.0, "consecutive_boards": 6.0,
         "sector": "种植业", "broken_count": 0.0, "first_seal_time": "093000",
         "last_seal_time": "093000", "turnover_rate": 5.2},
        {"code": "000560.SZ", "name": "我爱我家", "change_pct": 10.0,
         "seal_amount": 40000000.0, "max_board": 3.0, "consecutive_boards": 3.0,
         "sector": "房地产", "broken_count": 2.0, "first_seal_time": "133003",
         "last_seal_time": "140500", "turnover_rate": 21.7},
    ]
    con.execute("INSERT INTO daily_data(trade_date, data_type, data_json) VALUES(?,?,?)",
                (DAY, "limit_up_pool", json.dumps(pool, ensure_ascii=False)))
    con.execute("INSERT INTO daily_data(trade_date, data_type, data_json) VALUES(?,?,?)",
                (DAY, "broken_pool", json.dumps([{"code": "300999.SZ", "name": "X股"}])))
    analysis = {"consecutive_boards": [
        {"board_count": 7, "stocks": [{"code": "002084.SZ", "name": "海鸥住工"}]},
        {"board_count": 6, "stocks": [{"code": "002855.SZ", "name": "捷荣技术"},
                                      {"code": "600371.SH", "name": "万向德农"}]},
        {"board_count": 3, "stocks": [{"code": "000560.SZ", "name": "我爱我家"}]}]}
    con.execute("INSERT INTO analysis_results(trade_date, analysis_type, result_json) VALUES(?,?,?)",
                (DAY, "limit_up_analysis", json.dumps(analysis, ensure_ascii=False)))
    if with_prev:
        prev = {"consecutive_boards": [
            {"board_count": 6, "stocks": [{"code": "002084.SZ", "name": "海鸥住工"}]}]}
        con.execute("INSERT INTO analysis_results(trade_date, analysis_type, result_json) VALUES(?,?,?)",
                    (PREV, "limit_up_analysis", json.dumps(prev, ensure_ascii=False)))
    if with_lhb:
        detail = [
            {"code": "000892.SZ", "name": "欢瑞世纪", "reason": "连续三个交易日内,涨幅偏离值累计达到20%的证券",
             "close_price": 4.73, "change_pct": 10.0, "net_buy_amount": 37288799.9,
             "buy_amount": 68847694.0, "sell_amount": 31558894.1, "list_date": "20260901"},
            {"code": "000560.SZ", "name": "我爱我家", "reason": "日换手率达到20%的前5只证券",
             "close_price": 3.19, "change_pct": 10.0, "net_buy_amount": -137245894.65,
             "buy_amount": 300034118.38, "sell_amount": 437280013.03, "list_date": "20260901"},
            {"code": "000011.SZ", "name": "深物业A", "reason": "日跌幅偏离值达到7%的前5只证券",
             "close_price": 9.16, "change_pct": -9.0367, "net_buy_amount": -46085510.86,
             "buy_amount": 53578781.34, "sell_amount": 99664292.2, "list_date": "20260901"},
        ]
        con.execute("INSERT INTO daily_data(trade_date, data_type, data_json) VALUES(?,?,?)",
                    (DAY, "dragon_tiger_detail", json.dumps(detail, ensure_ascii=False)))
        dt_analysis = {
            "brokers": [
                {"broker_name": "国泰海通证券股份有限公司上海自贸试验区第二分公司",
                 "buy_amount": 299679625.98, "sell_amount": 0.0, "net_amount": 299679625.98},
                {"broker_name": "开源证券股份有限公司西安西大街证券营业部",
                 "buy_amount": 491999114.42, "sell_amount": 226286311.73, "net_amount": 265712802.69},
            ],
            "institutional": ([{"inst_code": "000892.SZ", "inst_name": "机构专用",
                                "buy_amount": 50000000.0, "sell_amount": 20000000.0,
                                "net_amount": 30000000.0}] if with_inst else []),
        }
        con.execute("INSERT INTO analysis_results(trade_date, analysis_type, result_json) VALUES(?,?,?)",
                    (DAY, "dragon_tiger", json.dumps(dt_analysis, ensure_ascii=False)))
    con.commit()
    con.close()
    return db


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "卡片"
    root.mkdir()
    monkeypatch.setenv("CARDGEN_PROJECT_ROOT", str(root))
    monkeypatch.setenv("CARDGEN_LEDGER_DB", str(tmp_path / "cards.db"))
    db = _mk_db(tmp_path)
    return root, db


class TestLadder:
    def test_generated_project_passes_all_gates(self, env):
        root, db = env
        proj, topic, report = daily.generate("ladder", DAY, root, None, db)
        assert topic == f"连板天梯/{DAY}"
        assert (proj / "facts.json").exists() and (proj / "cards.spec.json").exists()
        assert report.passed, [f"{f.gate}:{f.detail}" for f in report.failures]

    def test_promotion_wording_when_higher(self, env):
        _, db = env
        facts, spec = daily.build_ladder(DAY, daily.fetch_day_bundle(db, DAY))
        texts = json.dumps(spec, ensure_ascii=False)
        assert "晋级" in texts  # 今日7板 vs 昨日6板

    def test_flat_wording_when_equal(self, tmp_path):
        db = _mk_db(tmp_path)  # 默认昨日6板,先构造持平:把 prev 改成 7
        con = sqlite3.connect(db)
        prev = {"consecutive_boards": [
            {"board_count": 7, "stocks": [{"code": "002084.SZ", "name": "海鸥住工"}]}]}
        con.execute("UPDATE analysis_results SET result_json=? WHERE trade_date=?",
                    (json.dumps(prev, ensure_ascii=False), PREV))
        con.commit(); con.close()
        _, spec = daily.build_ladder(DAY, daily.fetch_day_bundle(db, DAY))
        assert "持平" in json.dumps(spec, ensure_ascii=False)

    def test_pullback_wording_when_lower(self, tmp_path):
        db = _mk_db(tmp_path)  # 构造回落:昨日8板
        con = sqlite3.connect(db)
        prev = {"consecutive_boards": [
            {"board_count": 8, "stocks": [{"code": "002084.SZ", "name": "海鸥住工"}]}]}
        con.execute("UPDATE analysis_results SET result_json=? WHERE trade_date=?",
                    (json.dumps(prev, ensure_ascii=False), PREV))
        con.commit(); con.close()
        _, spec = daily.build_ladder(DAY, daily.fetch_day_bundle(db, DAY))
        assert "回落" in json.dumps(spec, ensure_ascii=False)

    def test_no_prev_day_wording(self, tmp_path):
        db = _mk_db(tmp_path, with_prev=False)
        _, spec = daily.build_ladder(DAY, daily.fetch_day_bundle(db, DAY))
        assert "昨日无梯队数据" in json.dumps(spec, ensure_ascii=False)

    def test_missing_analysis_raises(self, tmp_path):
        db = _mk_db(tmp_path)
        con = sqlite3.connect(db)
        con.execute("DELETE FROM analysis_results WHERE analysis_type='limit_up_analysis'")
        con.commit(); con.close()
        with pytest.raises(daily.DailyDataMissing):
            daily.fetch_day_bundle(db, DAY)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_cardgen_daily.py -v`
Expected: FAIL(`ModuleNotFoundError: davis_analyzer.cardgen.daily`)

- [ ] **Step 3: 实现 daily.py 数据层 + build_ladder**

数据层(`fetch_day_bundle` 读法,只读):

```python
def _ro_conn(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _daily_json(con: sqlite3.Connection, day: str, data_type: str) -> list[dict]:
    row = con.execute("SELECT data_json FROM daily_data WHERE trade_date=? AND data_type=?",
                      (day, data_type)).fetchone()
    return json.loads(row[0]) if row else []


def _analysis_json(con: sqlite3.Connection, day: str, analysis_type: str) -> dict | None:
    row = con.execute("SELECT result_json FROM analysis_results WHERE trade_date=? AND analysis_type=?",
                      (day, analysis_type)).fetchone()
    return json.loads(row[0]) if row else None


def fetch_day_bundle(db_path: Path, day: str) -> dict:
    con = _ro_conn(db_path)
    try:
        lu = _analysis_json(con, day, "limit_up_analysis")
        if not lu or not lu.get("consecutive_boards"):
            raise DailyDataMissing(f"{day} 缺 limit_up_analysis.consecutive_boards(盘面扫描未完成?)")
        pool = _daily_json(con, day, "limit_up_pool")
        if not pool:
            raise DailyDataMissing(f"{day} 缺 limit_up_pool")
        prev_row = con.execute(
            "SELECT result_json FROM analysis_results WHERE analysis_type='limit_up_analysis' "
            "AND trade_date<? ORDER BY trade_date DESC LIMIT 1", (day,)).fetchone()
        prev_max = None
        if prev_row:
            prev_boards = json.loads(prev_row[0]).get("consecutive_boards") or []
            prev_max = max((int(t["board_count"]) for t in prev_boards), default=None)
        dt = _analysis_json(con, day, "dragon_tiger") or {}
        return {
            "pool": pool,
            "broken": _daily_json(con, day, "broken_pool"),
            "down": _daily_json(con, day, "limit_down_pool"),
            "boards": lu["consecutive_boards"],
            "prev_boards_max": prev_max,
            "lhb_detail": _daily_json(con, day, "dragon_tiger_detail"),
            "brokers": dt.get("brokers") or [],
            "institutional": dt.get("institutional") or [],
        }
    finally:
        con.close()
```

工具函数(格式化 + 无数字化):

```python
def _fact(fid: str, value, unit: str, display: str, day: str, ref: str) -> Fact:
    return Fact(id=fid, value=Decimal(str(value)), unit=unit, display=display,
                as_of=day, source_kind="stockhot", source_ref=ref, expires=day)


def _yi_signed(amount: float) -> tuple[str, str]:
    """元→亿,带符号 display + 无符号数值字符串。0 亦带 +。"""
    v = round(abs(amount) / 1e8, 2)
    sign = "-" if amount < 0 else "+"
    return f"{v:.2f}", f"{sign}{v:.2f}亿"


def _pct_signed(pct: float) -> tuple[str, str]:
    v = round(abs(pct), 2)
    return f"{v:.2f}", f"{'-' if pct < 0 else '+'}{v:.2f}%"


def _hhmm(hhmmss: str) -> str:
    s = str(hhmmss).zfill(6)
    return f"{s[:2]}:{s[2:4]}"


def _digit_safe(name: str) -> str | None:
    """板块名去阿拉伯数字:3D打印→三维打印;仍含数字则弃用(None)。"""
    out = name.replace("3D", "三维").replace("4D", "四维")
    return None if re.search(r"\d", out) else out
```

`build_ladder` 完整实现(5 页;数字全部 `$fact`,正文无裸数字,措辞分支晋级/持平/回落):

```python
def build_ladder(day: str, bundle: dict) -> tuple[list[Fact], dict]:
    ref_pool = f"stockhot.db:daily_data:limit_up_pool@{day}"
    ref_ana = f"stockhot.db:analysis_results:limit_up_analysis@{day}:consecutive_boards"
    pool, boards = bundle["pool"], bundle["boards"]
    facts: list[Fact] = []
    top_tier = boards[0]
    board_max = int(top_tier["board_count"])
    top_stock = max(pool, key=lambda r: (r.get("consecutive_boards") or 0,
                                         -(r.get("broken_count") or 0)))
    facts += [
        _fact("zt_count", len(pool), "只", f"{len(pool)}只", day, ref_pool + ":len"),
        _fact("broken_count", len(bundle["broken"]), "只",
              f"{len(bundle['broken'])}只", day, f"stockhot.db:daily_data:broken_pool@{day}:len"),
        _fact("down_count", len(bundle["down"]), "只",
              f"{len(bundle['down'])}只", day, f"stockhot.db:daily_data:limit_down_pool@{day}:len"),
        _fact("board_max", board_max, "", f"{board_max}板", day, f"{ref_ana}[0].board_count"),
    ]
    # 梯队表行:板数/家数/个股
    tier_rows = []
    for t in boards:
        n, stocks = int(t["board_count"]), t["stocks"]
        fid, cid = f"tier_{n}", f"tier_{n}_count"
        facts.append(_fact(fid, n, "", f"{n}板", day, f"{ref_ana}:board_count={n}"))
        facts.append(_fact(cid, len(stocks), "只", f"{len(stocks)}只", day,
                           f"{ref_ana}:board_count={n}:len"))
        tier_rows.append({"cells": [{"$fact": fid}, {"$fact": cid}, "、".join(s["name"] for s in stocks)],
                          "cls": ["up" if n == board_max else "", "", ""]})
    # 最高板个股明细
    seal_v, seal_d = _yi_signed(float(top_stock.get("seal_amount") or 0))
    turn_v, turn_d = _pct_signed(float(top_stock.get("turnover_rate") or 0))
    chg_v, chg_d = _pct_signed(float(top_stock.get("change_pct") or 0))
    facts += [
        _fact("top_seal_yi", seal_v, "亿", seal_d, day, f"{ref_pool}:{top_stock['code']}.seal_amount"),
        _fact("top_turnover_pct", turn_v, "%", turn_d, day, f"{ref_pool}:{top_stock['code']}.turnover_rate"),
        _fact("top_change_pct", chg_v, "%", chg_d, day, f"{ref_pool}:{top_stock['code']}.change_pct"),
    ]
    prev_max = bundle.get("prev_boards_max")
    if prev_max is None:
        compare_rows = [{"cells": ["昨日高度", "昨日无梯队数据"], "cls": ["", ""]}]
    else:
        delta = board_max - prev_max
        if delta != 0:
            facts.append(_fact("board_delta", abs(delta), "", f"{delta:+d}", day,
                               f"stockhot.db:analysis_results:limit_up_analysis@{day}:vs_prev"))
            word = "晋级" if delta > 0 else "回落"
            compare_rows = [{"cells": ["较昨日高度", {"$fact": "board_delta"}, word],
                             "cls": ["", "up" if delta > 0 else "", ""]}]
        else:
            compare_rows = [{"cells": ["较昨日高度", "持平"], "cls": ["", ""]}]
    sub_word = ("较昨日晋级" if (prev_max or 0) < board_max
                else "较昨日回落" if (prev_max or 0) > board_max else "梯队高度观察")
```

(`f"{delta:+d}"` 产 `+1`/`-1`;display 自检:value `1`,display `+1` 含 `1` ✅。subtitle 只用无数字文案,高度对比数字走 table cell 的 `$fact`——严禁把 `$fact` 或裸数字写进字符串。)

板块联动(聚合 pool 的 sector,代表股取该板块最早 first_seal_time):

```python
    sector_map: dict[str, list[dict]] = {}
    for r in pool:
        sec = _digit_safe(str(r.get("sector") or ""))
        if sec:
            sector_map.setdefault(sec, []).append(r)
    top_sectors = sorted(sector_map.items(), key=lambda kv: -len(kv[1]))[:4]
    sector_rows = []
    for i, (sec, rows) in enumerate(top_sectors, 1):
        rep = min(rows, key=lambda r: str(r.get("first_seal_time") or "999999"))
        cid = f"sector_{i}_count"
        facts.append(_fact(cid, len(rows), "只", f"{len(rows)}只", day,
                           f"{ref_pool}:sector={sec}:len"))
        sector_rows.append({"cells": [sec, {"$fact": cid}, rep["name"]], "cls": ["", "", ""]})
```

spec 组装(注意:首卡 `type: "cover"`、尾卡 `type: "summary"`;tag_top 无日期;每卡 foot):

```python
    spec = {
        "group": "每日复盘",
        "cards": [
            {"type": "cover", "theme": "red", "name": "01_封面",
             "tag_top": "连板天梯 · 每日数据复盘",
             "title": "今天的连板天梯<br>梯队与高度一览",
             "sub": f"封板结构 · {sub_word} · 板块联动<br>{day} 交易数据整理",
             "stats": [
                 {"v": {"$fact": "board_max"}, "k": "最高连板(板)"},
                 {"v": {"$fact": "zt_count"}, "k": "涨停(家)"},
                 {"v": {"$fact": "broken_count"}, "k": "炸板(家)"}],
             "tags": "#连板天梯 #每日复盘 #涨停数据 #市场结构",
             "foot": FOOT},
            {"type": "table", "theme": "orange", "name": "02_梯队", "first_left": True,
             "tag_top": "连板梯队", "tag_color": "#ea580c",
             "title": f"最高 {board_max} 连板 · 梯队全景",
             "subtitle": "按连板高度分层,个股按梯队归属",
             "table": {"headers": ["板数", "家数", "个股"], "rows": tier_rows},
             "foot": FOOT},
            {"type": "table", "theme": "blue", "name": "03_高度明细", "first_left": True,
             "tag_top": "空间高度", "tag_color": "#2563eb",
             "title": f"{top_stock['name']} · 今日最高板",
             "subtitle": sub_word,
             "table": {"headers": ["要点", "读数"], "rows": [
                 {"cells": ["今日连板高度", {"$fact": "board_max"}], "cls": ["", "up"]},
                 {"cells": ["当日涨跌幅", {"$fact": "top_change_pct"}], "cls": ["", ""]},
                 {"cells": ["封单金额", {"$fact": "top_seal_yi"}], "cls": ["", ""]},
                 {"cells": ["换手率", {"$fact": "top_turnover_pct"}], "cls": ["", ""]},
                 {"cells": ["首次封板时间", _hhmm(top_stock.get("first_seal_time", ""))], "cls": ["", ""]},
                 {"cells": ["所属板块", str(top_stock.get("sector") or "-")], "cls": ["", ""]},
                 compare_rows[0],
             ]},
             "foot": FOOT},
            {"type": "table", "theme": "green", "name": "04_板块联动", "first_left": True,
             "tag_top": "板块联动", "tag_color": "#16a34a",
             "title": "涨停家数居前板块",
             "subtitle": "代表股取该板块最早封板个股",
             "table": {"headers": ["板块", "涨停家数", "代表股"], "rows": sector_rows},
             "foot": FOOT},
            {"type": "summary", "theme": "lavender", "name": "05_收束",
             "tag_top": "数据说明", "tag_color": "#0f172a",
             "title": "天梯是结构数据",
             "subtitle": "不是操作清单",
             "rows": [
                 {"desc": "<b>今日高度</b> → 见封面与高度明细页"},
                 {"desc": "<b>梯队结构</b> → 高度分层与家数分布,反映当日封板结构"},
                 {"desc": "<b>联动主线</b> → 涨停家数居前板块,反映题材聚集度"}],
             "kbox": {"date": "栏目定位", "color": "blue",
                      "html": "本栏目每日盘后从交易所公开数据整理连板梯队——搬运数据,不输出操作指令"},
             "tags": "#连板天梯 #每日复盘 #涨停数据 #市场结构",
             "foot": FOOT_LAST},
        ],
    }
    return facts, spec
```

(物化只处理值恰为 `{"$fact": id}` 的节点——字符串里的 `$fact` 字样不会被替换,所以叙事 desc 一律无数字表述,严禁在字符串里写 `$fact` 或裸数字。)

`write_project` 与 `generate`:

```python
def write_project(projects_root: Path, topic: str, facts: list[Fact], spec: dict) -> Path:
    proj = projects_root / PENDING_DIR / topic
    (proj / "output").mkdir(parents=True, exist_ok=True)
    save_facts(proj / "facts.json", facts)
    (proj / "cards.spec.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return proj


def generate(kind: str, day: str, projects_root: Path, ledger_db: Path | None,
             stockhot_db: Path | None = None) -> tuple[Path, str, ValidateReport]:
    db = stockhot_db or Path(os.environ.get("CARDGEN_STOCKHOT_DB", DEFAULT_STOCKHOT_DB))
    bundle = fetch_day_bundle(db, day)
    if kind == "ladder":
        facts, spec = build_ladder(day, bundle)
        topic = f"连板天梯/{day}"
    elif kind == "lhb":
        facts, spec = build_lhb(day, bundle)   # Task 5 实现;先放占位 raise
        topic = f"龙虎榜/{day}"
    else:
        raise ValueError(f"kind 须 ladder/lhb: {kind}")
    proj = write_project(projects_root, topic, facts, spec)
    conn = ledger.connect(ledger_db)
    try:
        ledger.register_card(conn, topic, str(proj / "cards.spec.json"))
        report = run_validation(proj, topic=topic)
        row = ledger.get_card(conn, topic)
        ledger.log_validate(conn, topic, int(row["current_version"]), report.passed, report.failures)
        if report.passed:
            ledger.set_status(conn, topic, "validated")
    finally:
        conn.close()
    for f in report.failures:
        logger.warning(f"[daily] validate 未过 [{f.gate}] {f.card} {f.field}: {f.detail}")
    return proj, topic, report
```

Task 4 期间 `build_lhb` 以 `raise NotImplementedError` 占位(Task 5 替换),`ladder` 分支与测试先行。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_cardgen_daily.py -v`
Expected: 全 PASS(四道闸全过是核心断言——它同时校验数字溯源/合规/完整性/物化)

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/cardgen/daily.py davis_analyzer/tests/test_cardgen_daily.py
git commit -m "feat(cardgen): 每日连板天梯卡生成器——stockhot.db→facts+spec 五页卡,四道闸全过"
```

---

### Task 5: cardgen/daily.py 龙虎榜生成器(三维度)

**Files:**
- Modify: `davis_analyzer/cardgen/daily.py`(实现 `build_lhb`,替换 NotImplementedError)
- Test: `davis_analyzer/tests/test_cardgen_daily.py`(追加 TestLhb 类)

**Interfaces:**
- Consumes: Task 4 的 `_fact/_yi_signed/_pct_signed/fetch_day_bundle`、Task 3 的机构行不变量。
- Produces: `build_lhb(day: str, bundle: dict) -> tuple[list[Fact], dict]`(6 页卡)。

- [ ] **Step 1: 写失败测试**(追加;沿用 Task 4 fixture)

```python
class TestLhb:
    def test_generated_project_passes_all_gates(self, env):
        root, db = env
        proj, topic, report = daily.generate("lhb", DAY, root, None, db)
        assert topic == f"龙虎榜/{DAY}"
        assert report.passed, [f"{f.gate}:{f.detail}" for f in report.failures]
        texts = json.dumps(json.loads((proj / "cards.spec.json").read_text(encoding="utf-8")),
                           ensure_ascii=False)
        assert "买入额" not in texts and "卖出额" not in texts   # 金额口径红线

    def test_missing_dragon_tiger_raises(self, tmp_path):
        db = _mk_db(tmp_path, with_lhb=False)
        root = tmp_path / "卡片"; root.mkdir()
        bundle = daily.fetch_day_bundle(db, DAY)  # fetch 不拦 lhb 维度(天梯数据齐即可)
        assert bundle["lhb_detail"] == []
        with pytest.raises(daily.DailyDataMissing):
            daily.generate("lhb", DAY, root, None, db)  # generate('lhb') 在 detail 为空时拒绝

    def test_empty_institutional_degrades(self, tmp_path, monkeypatch):
        root = tmp_path / "卡片"; root.mkdir()
        monkeypatch.setenv("CARDGEN_PROJECT_ROOT", str(root))
        monkeypatch.setenv("CARDGEN_LEDGER_DB", str(tmp_path / "cards.db"))
        db = _mk_db(tmp_path, with_inst=False)
        _, _, report = daily.generate("lhb", DAY, root, None, db)
        assert report.passed

    def test_reason_labelled_digit_free(self, env):
        _, db = env
        facts, spec = daily.build_lhb(DAY, daily.fetch_day_bundle(db, DAY))
        texts = json.dumps(spec, ensure_ascii=False)
        assert "换手达标" in texts and "三日涨幅偏离" in texts
        assert "20%" not in texts  # 原因文本里的数字必须被映射掉
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_cardgen_daily.py -k Lhb -v`
Expected: FAIL(NotImplementedError / 断言缺失)

- [ ] **Step 3: 实现 build_lhb**

```python
def _reason_label(reason: str) -> str:
    """交易所上榜原因 → 无数字短标签(原文含 20%/前5 只等数字,直接进卡会撞数字闸)。"""
    if "连续三个交易日" in reason or "三日" in reason:
        return "三日涨幅偏离" if "涨幅" in reason else "三日跌幅偏离"
    if "换手率" in reason:
        return "换手达标"
    if "振幅" in reason:
        return "振幅达标"
    if "跌幅" in reason:
        return "日内跌幅偏离"
    if "涨幅" in reason:
        return "日内涨幅偏离"
    return "异动"


def _truncate(name: str, n: int = 18) -> str:
    return name if len(name) <= n else name[: n - 1] + "…"


def build_lhb(day: str, bundle: dict) -> tuple[list[Fact], dict]:
    detail = bundle["lhb_detail"]
    if not detail:
        raise DailyDataMissing(f"{day} 缺 dragon_tiger_detail(龙虎榜数据未落库?)")
    brokers, inst = bundle["brokers"], bundle["institutional"]
    ref_detail = f"stockhot.db:daily_data:dragon_tiger_detail@{day}"
    ref_ana = f"stockhot.db:analysis_results:dragon_tiger@{day}"
    facts: list[Fact] = []

    net_total = sum(float(r.get("net_buy_amount") or 0) for r in detail)
    nt_v, nt_d = _yi_signed(net_total)
    facts.append(_fact("lhb_count", len(detail), "家", f"{len(detail)}家", day, ref_detail + ":len"))
    facts.append(_fact("lhb_net_total_yi", nt_v, "亿", nt_d, day, ref_detail + ":sum(net_buy_amount)"))

    ladder_codes = {s["code"] for t in bundle["boards"] for s in t["stocks"]}
    cross = [r for r in detail if r.get("code") in ladder_codes]
    facts.append(_fact("cross_count", len(cross), "只", f"{len(cross)}只", day,
                       ref_detail + ":∩limit_up_analysis.consecutive_boards"))

    # 个股净买 Top10 / 净卖 Top5
    ranked = sorted(detail, key=lambda r: -(float(r.get("net_buy_amount") or 0)))
    buy_rows, sell_rows = [], []
    for i, r in enumerate(ranked[:10], 1):
        v, d = _yi_signed(float(r.get("net_buy_amount") or 0))
        p_v, p_d = _pct_signed(float(r.get("change_pct") or 0))
        facts += [_fact(f"nb{i}_yi", v, "亿", d, day, f"{ref_detail}:{r['code']}.net_buy_amount"),
                  _fact(f"nb{i}_pct", p_v, "%", p_d, day, f"{ref_detail}:{r['code']}.change_pct")]
        buy_rows.append({"cells": [r["name"], {"$fact": f"nb{i}_pct"}, {"$fact": f"nb{i}_yi"},
                                   _reason_label(str(r.get("reason") or ""))],
                         "cls": ["", "up" if float(r.get("change_pct") or 0) > 0 else "", "up", ""]})
    for i, r in enumerate(sorted(detail, key=lambda r: float(r.get("net_buy_amount") or 0))[:5], 1):
        v, d = _yi_signed(float(r.get("net_buy_amount") or 0))
        p_v, p_d = _pct_signed(float(r.get("change_pct") or 0))
        facts += [_fact(f"ns{i}_yi", v, "亿", d, day, f"{ref_detail}:{r['code']}.net_buy_amount"),
                  _fact(f"ns{i}_pct", p_v, "%", p_d, day, f"{ref_detail}:{r['code']}.change_pct")]
        sell_rows.append({"cells": [r["name"], {"$fact": f"ns{i}_pct"}, {"$fact": f"ns{i}_yi"},
                                    _reason_label(str(r.get("reason") or ""))],
                          "cls": ["", "", "", ""]})

    # 营业部 Top5(只呈现净额,禁 买入额/卖出额 措辞)
    broker_rows = []
    for i, b in enumerate(sorted(brokers, key=lambda x: -float(x.get("net_amount") or 0))[:5], 1):
        v, d = _yi_signed(float(b.get("net_amount") or 0))
        facts.append(_fact(f"bk{i}_yi", v, "亿", d, day, f"{ref_ana}:brokers.net_amount"))
        broker_rows.append({"cells": [_truncate(str(b.get("broker_name") or "")), {"$fact": f"bk{i}_yi"}],
                            "cls": ["", "up" if float(b.get("net_amount") or 0) > 0 else ""]})

    # 机构席位:按个股聚合净额 Top5(行=个股口径,机构专用);空则降级占位
    inst_agg: dict[str, float] = {}
    for r in inst:
        inst_agg[str(r.get("inst_code") or "")] = inst_agg.get(str(r.get("inst_code") or ""), 0.0) \
            + float(r.get("net_amount") or 0)
    name_by_code = {r.get("code"): r.get("name") for r in detail}
    inst_rows = []
    top_inst = sorted(inst_agg.items(), key=lambda kv: -kv[1])[:5]
    for i, (code, amt) in enumerate(top_inst, 1):
        v, d = _yi_signed(amt)
        facts.append(_fact(f"ist{i}_yi", v, "亿", d, day, f"{ref_ana}:institutional.sum(net_amount)"))
        inst_rows.append({"cells": [name_by_code.get(code, code), {"$fact": f"ist{i}_yi"}],
                          "cls": ["", "up" if amt > 0 else ""]})

    # 交叉视角
    cross_rows = []
    for i, r in enumerate(cross[:5], 1):
        board = next((int(t["board_count"]) for t in bundle["boards"]
                      if any(s["code"] == r["code"] for s in t["stocks"])), 0)
        v, d = _yi_signed(float(r.get("net_buy_amount") or 0))
        facts += [_fact(f"cr{i}_board", board, "", f"{board}板", day,
                        f"stockhot.db:analysis_results:limit_up_analysis@{day}:consecutive_boards"),
                  _fact(f"cr{i}_yi", v, "亿", d, day, f"{ref_detail}:{r['code']}.net_buy_amount")]
        cross_rows.append({"cells": [r["name"], {"$fact": f"cr{i}_board"}, {"$fact": f"cr{i}_yi"}],
                           "cls": ["", "up", ""]})

    inst_page = (
        {"type": "table", "theme": "purple", "name": "05_机构席位", "first_left": True,
         "tag_top": "机构席位", "tag_color": "#7c3aed",
         "title": "机构席位净额居前个股",
         "subtitle": "机构专用席位合并口径",
         "table": {"headers": ["个股", "机构净额"], "rows": inst_rows},
         "foot": FOOT}
        if inst_rows else
        {"type": "table", "theme": "purple", "name": "05_机构席位", "first_left": True,
         "tag_top": "机构席位", "tag_color": "#7c3aed",
         "title": "机构席位动向",
         "subtitle": "数据以交易所披露为准",
         "table": {"headers": ["说明"], "rows": [{"cells": ["今日无机构席位数据"], "cls": [""]}]},
         "foot": FOOT})

    spec = {
        "group": "每日复盘",
        "cards": [
            {"type": "cover", "theme": "purple", "name": "01_封面",
             "tag_top": "龙虎榜 · 每日数据复盘",
             "title": "今天的龙虎榜<br>资金动向一览",
             "sub": f"个股净额 · 营业部 · 机构席位<br>{day} 交易数据整理",
             "stats": [
                 {"v": {"$fact": "lhb_count"}, "k": "上榜(家)"},
                 {"v": {"$fact": "lhb_net_total_yi"}, "k": "整体净买额(亿)"},
                 {"v": {"$fact": "cross_count"}, "k": "上榜且连板(只)"}],
             "tags": "#龙虎榜 #每日复盘 #资金数据 #市场结构",
             "foot": FOOT},
            {"type": "table", "theme": "blue", "name": "02_净买额居前", "first_left": True,
             "tag_top": "个股净买额", "tag_color": "#2563eb",
             "title": "净买额居前个股",
             "subtitle": "口径:龙虎榜净买额(买额-卖额)",
             "table": {"headers": ["个股", "涨跌幅", "净买额", "上榜标签"], "rows": buy_rows},
             "foot": FOOT},
            {"type": "table", "theme": "green", "name": "03_净卖额居前", "first_left": True,
             "tag_top": "个股净卖额", "tag_color": "#16a34a",
             "title": "净卖额居前个股",
             "subtitle": "资金流出侧观察",
             "table": {"headers": ["个股", "涨跌幅", "净卖额", "上榜标签"], "rows": sell_rows},
             "foot": FOOT},
            {"type": "table", "theme": "orange", "name": "04_活跃营业部", "first_left": True,
             "tag_top": "活跃营业部", "tag_color": "#ea580c",
             "title": "净额居前营业部",
             "subtitle": "沪深交易所披露口径,全名截断显示",
             "table": {"headers": ["营业部", "净额"], "rows": broker_rows},
             "foot": FOOT},
            inst_page,
            {"type": "summary", "theme": "lavender", "name": "06_收束",
             "tag_top": "交叉视角", "tag_color": "#0f172a",
             "title": "龙虎榜 × 连板梯队",
             "subtitle": "两份公开数据的交集",
             "rows": (cross_rows and [
                 {"desc": "<b>上榜连板股</b> → 见下表(连板高度 × 龙虎榜净额)"}]
                 or [{"desc": "<b>今日交集为空</b> → 龙虎榜与连板梯队无重叠个股"}]),
             "kbox": {"date": "栏目定位", "color": "blue",
                      "html": "本栏目每日盘后整理交易所龙虎榜披露——搬运数据,不输出操作指令"},
             "tags": "#龙虎榜 #每日复盘 #资金数据 #市场结构",
             "foot": FOOT_LAST},
        ],
    }
    if cross_rows:
        # summary 卡不排表格——交叉明细作为第 6 页内 table 与 rows 共存(spec 支持,参照公告日报 rows+kbox)
        spec["cards"][-1]["table"] = {"headers": ["个股", "连板", "净额"], "rows": cross_rows}
    return facts, spec
```

**执行注意**:①`net_buy_amount` 为负的「净卖额」display 也是带符号亿值(如 `-1.37亿`),列名叫净卖额但值仍用原净额符号——保持与事实一致,负号即流出;②若 `card_factory` 的 table 不支持 summary 卡内 `table` 字段(渲染器行为未验证),把交叉表放到第 5 页机构页之后新增一张 `type: "table"` 卡(总页数 7),以渲染实测为准;③`theme: "purple"` 若渲染器不支持,换 `"blue"`。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_cardgen_daily.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/cardgen/daily.py davis_analyzer/tests/test_cardgen_daily.py
git commit -m "feat(cardgen): 每日龙虎榜卡生成器——个股/营业部/机构三维度+连板交叉,原因无数字化标签"
```

---

### Task 6: CLI 入口 scripts/daily_market_cards.py(生成+渲染一体)

**Files:**
- Create: `scripts/daily_market_cards.py`

**Interfaces:**
- Consumes: `davis_analyzer.cardgen.daily.generate/fetch_day_bundle/DailyDataMissing`、`davis_analyzer.cardgen.builder.render`、`davis_analyzer.cardgen.ledger.connect`、`davis_analyzer.cardgen.cli._projects_root`(或等价 env 逻辑,见下)。
- Produces: CLI `--type {ladder,lhb,all} [--date YYYY-MM-DD] [--no-render]`;成功打印每卡 `渲染完成 v1: N 张 PNG`,失败 exit 1。

- [ ] **Step 1: 实现**(单文件,print 允许;工程根与台账 env 逻辑照抄 cli.py)

```python
# scripts/daily_market_cards.py
"""每日盘面复盘卡管线(2026-09-01):stockhot.db → 连板天梯/龙虎榜 两卡 → validate → render。

用法: .venv/bin/python scripts/daily_market_cards.py --type all [--date 2026-09-01] [--no-render]
纪律:生成不发布——enqueue/发布留人工(责任分层:人管值不值得发)。
缺数据(节假日/扫描未跑)非零退出并说明,不硬造。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from davis_analyzer.cardgen import daily, ledger            # noqa: E402
from davis_analyzer.cardgen.builder import render           # noqa: E402


def _projects_root() -> Path:
    return Path(os.environ.get("CARDGEN_PROJECT_ROOT", REPO_ROOT / "docs" / "小红书卡片"))


def _ledger_db() -> Path | None:
    env = os.environ.get("CARDGEN_LEDGER_DB")
    return Path(env) if env else None


def run_one(kind: str, day: str, do_render: bool) -> bool:
    try:
        proj, topic, report = daily.generate(kind, day, _projects_root(), _ledger_db())
    except daily.DailyDataMissing as e:
        print(f"✗ {kind} {day}: 数据不完整,拒绝生成——{e}")
        return False
    if not report.passed:
        print(f"✗ {kind} {day}: validate 未过({len(report.failures)} 项),未渲染")
        return False
    print(f"✓ {topic} validate 通过 | as_of={report.as_of} expires={report.expires_at}")
    if not do_render:
        return True
    conn = ledger.connect(_ledger_db())
    try:
        release = render(proj, topic, conn)
        print(f"✓ {topic} 渲染完成 v{release['version']}: {len(release['images'])} 张 PNG | "
              f"过期日 {release['expires_at']}")
    except (SystemExit, RuntimeError) as e:
        print(f"✗ {topic} 渲染失败: {e}")
        return False
    finally:
        conn.close()
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="每日盘面复盘卡(连板天梯+龙虎榜)")
    ap.add_argument("--type", choices=["ladder", "lhb", "all"], default="all")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--no-render", action="store_true", help="只生成+validate,不渲染(调试用)")
    args = ap.parse_args()
    kinds = ["ladder", "lhb"] if args.type == "all" else [args.type]
    ok = all(run_one(k, args.date, not args.no_render) for k in kinds)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 用真实数据跑 ladder(先单品类)**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python scripts/daily_market_cards.py --type ladder --date 2026-09-01`
Expected: `✓ 连板天梯/2026-09-01 渲染完成 v1: 5 张 PNG | 过期日 2026-09-01`
若 validate 失败:按失败明细修 daily.py 模板(常见:未溯源数字/敏感词),修完重跑(工程重建幂等——同日重跑覆盖 facts/spec,render 需 `--bump`?**不需要**:当日重跑属首次渲染前修正,revisions 尚无记录;若已 rendered 过再改,走 `python -m davis_analyzer.cardgen build --topic 连板天梯/2026-09-01 --bump --reason "当日修正"`。)

- [ ] **Step 3: 跑 lhb 与 all**

Run: `.venv/bin/python scripts/daily_market_cards.py --type all --date 2026-09-01`
Expected: 两卡各 `渲染完成`,合计 5+6 张 PNG(若 Task 5 执行注意③生效则 5+7)。

- [ ] **Step 4: Commit**(含当日两个工程目录)

```bash
git add scripts/daily_market_cards.py "docs/小红书卡片/未发布/连板天梯" "docs/小红书卡片/未发布/龙虎榜"
git commit -m "feat(cardgen): 每日复盘卡CLI——ladder/lhb/all 一键生成渲染,2026-09-01 首卡出图"
```

---

### Task 7: 视觉验收(vision.py 目检 5+6 页)

**Files:**
- 无代码改动(问题修复回 Task 4/5 的模板)

**Interfaces:**
- Consumes: `scripts/content_publisher/vision.py`(glm 视觉模型,AGENTS.md 视觉任务规范)。

- [ ] **Step 1: 逐张目检**

```bash
cd /home/leo/Projects/CodeAgentDashboard && for f in "docs/小红书卡片/未发布/连板天梯/2026-09-01/output/"连板天梯_2026-09-01_*.png; do
  .venv/bin/python scripts/content_publisher/vision.py "$f" "检查这张金融信息卡片:1)文字是否有溢出/截断/重叠 2)表格是否越界 3)排版是否明显失衡 4)有无乱码。返回 JSON:{\"ok\": bool, \"issues\": [str]}";
done
```
对 龙虎榜 6 张重复同命令。Expected: 每张 `"ok": true`。
已预案的 contingencies:净买 Top10 表若溢出 → 拆两页各 5 行;summary 内 table 若渲染器不支持 → 交叉表独立成卡;`theme: "purple"` 不识别 → 换 `blue`。

- [ ] **Step 2: 目检不过的修复循环**

修 `daily.py` 模板 → 删当日工程目录重跑 `--type all --date 2026-09-01`(当日重生成幂等)→ 重新目检,直到 11 张全过。

- [ ] **Step 3: Commit(如有修复)**

```bash
git add davis_analyzer/cardgen/daily.py "docs/小红书卡片/未发布/连板天梯" "docs/小红书卡片/未发布/龙虎榜"
git commit -m "fix(cardgen): 复盘卡首目检修正——<具体问题一句话>"
```

---

### Task 8: cron 挂载 + 方法论/AGENTS 沉淀

**Files:**
- Modify: `docs/方法论/小红书金融卡片生产方法论_2026-08-29.md`(追加 §8.8)
- Modify: `/home/leo/Projects/CodeAgentDashboard/davis_analyzer/AGENTS.md`(cardgen 段落补一句)

**Interfaces:**
- Consumes: CronCreate(ZCode 自动化)。

- [ ] **Step 1: 方法论追加 §8.8**(文件末尾 §8 节内追加;口吻对齐既有小节)

内容要点(执行时落成正文):每日复盘卡品类(连板天梯/龙虎榜)落地记录——数据源 stockhot.db(daily-market-scan 采集);生产管线 `scripts/daily_market_cards.py`(生成不发布,enqueue/发布留人工);叙事纪律=零观点纯数据句,数字全 `$fact`,expires=当日;措辞红线(净买额/净卖额口径,禁 买入额/卖出额——敏感词表含二字词;上榜原因映射无数字标签;板块名 3D→三维;正文日期禁「9月1日」形态);嵌套 topic 工程 `连板天梯/<日期>` 与 sync 两级归档。

- [ ] **Step 2: AGENTS.md cardgen 段落补一句**(在 cardgen 子系统描述的 CLI 行后)

```
每日复盘卡(2026-09-01):连板天梯/龙虎榜——`scripts/daily_market_cards.py --type {ladder,lhb,all}` 生成+渲染(逻辑在 davis_analyzer/cardgen/daily.py),工作日 20:30 cron 无人值守,生成不发布;零观点纯数据叙事,facts source.kind=stockhot 指纹溯源,expires=当日(过时不发)。
```

- [ ] **Step 3: 建 cron(20:30 工作日)**

CronCreate 参数:
- title: `每交易日20:30 连板天梯/龙虎榜复盘卡生成`
- cron: `30 20 * * 1-5`
- recurring: true
- prompt(自包含,无人值守纪律对齐公告日报 cron):

```
【定时任务·每交易日20:30 复盘卡生成】生成《连板天梯》《龙虎榜》两张每日数据复盘卡并渲染(生成不发布)。工作目录 /home/leo/Projects/CodeAgentDashboard,解释器 .venv/bin/python。无人值守例行:按步骤执行,异常记录后继续,事后报告,不等待人工。

执行步骤:
1. 运行:timeout 600 .venv/bin/python scripts/daily_market_cards.py --type all
   (默认当日;正常 stdout 每卡一行「✓ <topic> 渲染完成 vN: N 张 PNG」)
2. 数据不完整(节假日/盘面扫描未跑)导致非零退出且信息为「拒绝生成」:一句话确认当日空转属正常,不报警不重试,任务结束。
3. validate 失败或渲染报错:报告失败明细(stdout 全文)+当日工程路径,最多重跑一次,仍失败如实报告,不修改任何代码。
4. 成功:git add docs/小红书卡片/未发布/连板天梯 docs/小红书卡片/未发布/龙虎榜 并 commit,格式「feat(cardgen): 复盘卡 YYYY-MM-DD——天梯N页/龙虎榜N页」;不 push。
5. 最终消息精炼:两卡 PNG 张数、最高连板与净买额合计两个核心数字(从 facts.json 读,不手抄)、当日异常(如有)。

纪律:不 enqueue 不发布(发布需人工走 queue 流程);不改 daily_market_cards.py/cardgen/stockhot 代码;不创建/修改/停用任何其他 cron;数据陈旧(当日无数据但非节假日)只报告不重试。
```

- [ ] **Step 4: 汇报验收状态 + Commit 文档**

```bash
git add docs/方法论/小红书金融卡片生产方法论_2026-08-29.md davis_analyzer/AGENTS.md
git commit -m "docs(cardgen): 每日复盘卡方法论§8.8沉淀+AGENTS接入说明——措辞红线与嵌套topic约定"
```

---

## Self-Review 记录

- **Spec 覆盖**:§三工程组织→Task 2/4;§4.1/4.2 卡结构→Task 4/5;§4.3 零观点→模板实现;§五 source.kind/expires/foot/机构修复→Task 1/3/4;§六 脚本+cron+生成不发布→Task 6/8;§七 合规红线→模板+测试断言;§八 测试验收→各任务测试+Task 7 目检。无缺口。
- **已知执行期不确定点**(均有预案,非占位):Task 3 机构列名以实测为准;Task 5 summary 内 table 渲染器支持度(备选独立成卡);Top10 表高度(备选拆页);theme "purple" 支持(备选 blue)。
- **类型一致性**:`generate(kind, day, projects_root, ledger_db, stockhot_db=None)`、`build_ladder/build_lhb(day, bundle)`、`fetch_day_bundle(db_path, day)`、`_png_prefix(topic)`、`DailyDataMissing` 在各任务间引用一致。
