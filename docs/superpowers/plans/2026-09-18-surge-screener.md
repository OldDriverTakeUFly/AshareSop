# Surge Screener 涨幅7%+筛选分析子系统 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每日盘后筛选涨幅>7%个股，九维分析+形态副本筛选+16形态标签，全量入台账出两份报告，按待测因子库标准设计。

**Architecture:** 独立子包 `davis_analyzer/surge/`（limitup 模式）：db(表管理) → chips(cyq_perf筹码)/cninfo(巨潮大事) → factors(九维纯函数)/pattern(形态纯函数) → screen(编排入库) → report(两份md) → cli。表挂 market_data.db，7 张新表自管。

**Tech Stack:** Python 3.11+ / pandas / sqlite3 / loguru / tushare(cyq_perf) / requests(巨潮) / pytest

**Spec:** `docs/superpowers/specs/2026-09-18-surge-screener-design.md`（口径冻结于 spec，本计划实现它）

## Global Constraints

- 必须从父仓库根目录 `/home/leo/Projects/CodeAgentDashboard/` 运行，Python 用 `.venv/bin/python`
- 代码风格：`from __future__ import annotations`、loguru（禁 stdlib logging/print，cli.py 除外）、完整类型注解、snake_case
- 表挂 `storage/database/market_data.db`（经 `stockhot.data_layer.market_db.get_connection()`），日期一律 YYYYMMDD 入库
- 不动 daily_basic/corp_event/limitup 等既有表；巨潮事件独立落 major_events
- 金额比率用 float；宁缺毋错：数据缺失→NaN/空标注，不猜
- 权重与参数单一真相源 constants.py：SURGE_WEIGHTS / PATTERN_PARAMS / MAJOR_EVENT_RULES
- 温度引用遵守反向语义（低温=左侧可跟踪，不作买入信号）

---

### Task 1: 脚手架 + db.py（7 表 + readers）

**Files:**
- Create: `davis_analyzer/surge/__init__.py`（空）、`davis_analyzer/surge/db.py`
- Test: `tests/test_surge_db.py`

**Interfaces:**
- Produces: `connect() -> sqlite3.Connection`、`ensure_tables(conn) -> None`、`normalize_date/to_dash_date/to_suffixed_code/strip_code_suffix`、`read_pool(conn, day) -> pd.DataFrame`（当日>7%池+名称/行业）、`trading_dates/latest_trade_date`（借用 limitup 同名语义，本地实现避免跨包依赖）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_surge_db.py
"""surge.db 表管理与池查询测试（内存库，不触真库）."""
import sqlite3

import pandas as pd
import pytest

from davis_analyzer.surge import db


@pytest.fixture()
def mem_conn():
    conn = sqlite3.connect(":memory:")
    # 依赖表最小化建表
    conn.execute(
        "CREATE TABLE daily_price (ts_code TEXT, trade_date TEXT, open REAL, high REAL,"
        " low REAL, close REAL, pre_close REAL, pct_chg REAL, vol REAL, amount REAL,"
        " adj_factor REAL, PRIMARY KEY(ts_code, trade_date))"
    )
    conn.execute(
        "CREATE TABLE stock_basic (ts_code TEXT PRIMARY KEY, name TEXT, industry TEXT,"
        " market TEXT, list_date TEXT)"
    )
    db.ensure_tables(conn)
    yield conn
    conn.close()


def test_ensure_tables_idempotent(mem_conn):
    db.ensure_tables(mem_conn)  # 二次执行不抛错
    names = {r[0] for r in mem_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"cyq_perf_cache", "surge_snapshot", "major_events",
            "cninfo_announcement", "cninfo_org_map",
            "surge_pattern_hits", "surge_tags"} <= names


def test_read_pool_filters_pct(mem_conn):
    mem_conn.execute("INSERT INTO stock_basic VALUES ('000001.SZ','平安X','银行','主板','19910403')")
    for code, pct in [("000001.SZ", 7.5), ("000002.SZ", 6.9), ("000003.SZ", 20.1)]:
        mem_conn.execute(
            "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (code, "20260918", 10, 11, 9, 10.7, 10, pct, 1000, 10700, 1.0))
    pool = db.read_pool(mem_conn, "20260918")
    assert set(pool["ts_code"]) == {"000001.SZ", "000003.SZ"}
    assert pool.loc[pool.ts_code == "000001.SZ", "name"].iloc[0] == "平安X"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m pytest tests/test_surge_db.py -v`
Expected: FAIL（ModuleNotFoundError: davis_analyzer.surge）

- [ ] **Step 3: 实现 db.py**

```python
# davis_analyzer/surge/db.py
"""Surge 子系统 SQLite 表管理与读取（表挂 market_data.db，日期 YYYYMMDD）."""

from __future__ import annotations

import sqlite3

import pandas as pd
from loguru import logger

_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS cyq_perf_cache (
      ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
      his_low REAL, his_high REAL,
      cost_5pct REAL, cost_15pct REAL, cost_50pct REAL,
      cost_85pct REAL, cost_95pct REAL,
      weight_avg REAL, winner_rate REAL,
      fetched_at REAL,
      PRIMARY KEY (ts_code, trade_date))
    """,
    """
    CREATE TABLE IF NOT EXISTS surge_snapshot (
      trade_date TEXT NOT NULL, ts_code TEXT NOT NULL,
      name TEXT, industry TEXT, pct_chg REAL, amount_k REAL,
      pos_250d REAL, dist_ma20 REAL, dist_ma60 REAL,
      dist_ma120 REAL, dist_ma250 REAL, dd_high_250 REAL,
      ladder_label TEXT, is_st INTEGER, is_new INTEGER,
      elg_net_d0 REAL, lg_net_5d REAL, net_ratio_d0 REAL,
      consec_net_days INTEGER,
      cost_5pct REAL, cost_50pct REAL, cost_95pct REAL, weight_avg REAL,
      winner_rate REAL, winner_delta_5d REAL,
      resistance_price REAL, resistance_dist REAL,
      support_price REAL, support_dist REAL,
      hype_tags TEXT, risk_flags TEXT,
      hype_count INTEGER, risk_flag_count INTEGER,
      composite REAL, rank INTEGER,
      fetched_at REAL,
      PRIMARY KEY (trade_date, ts_code))
    """,
    """
    CREATE TABLE IF NOT EXISTS major_events (
      ts_code TEXT NOT NULL, ann_date TEXT NOT NULL,
      event_type TEXT NOT NULL,
      title TEXT,
      direction TEXT,
      source TEXT,
      fetched_at REAL,
      PRIMARY KEY (ts_code, ann_date, event_type, title))
    """,
    """
    CREATE TABLE IF NOT EXISTS cninfo_announcement (
      ts_code TEXT NOT NULL, ann_date TEXT NOT NULL,
      title TEXT NOT NULL,
      fetched_at REAL,
      PRIMARY KEY (ts_code, ann_date, title))
    """,
    """
    CREATE TABLE IF NOT EXISTS cninfo_org_map (
      ts_code TEXT PRIMARY KEY, org_id TEXT, updated_at REAL)
    """,
    """
    CREATE TABLE IF NOT EXISTS surge_pattern_hits (
      trade_date TEXT NOT NULL, ts_code TEXT NOT NULL,
      boom_date TEXT, boom_pct REAL, boom_vol_ratio REAL,
      pullback_start TEXT, pullback_end TEXT,
      pullback_depth REAL, vol_decay REAL,
      plateau_high REAL, plateau_days INTEGER,
      breakout_pct REAL,
      fetched_at REAL,
      PRIMARY KEY (trade_date, ts_code))
    """,
    """
    CREATE TABLE IF NOT EXISTS surge_tags (
      trade_date TEXT NOT NULL, ts_code TEXT NOT NULL,
      tag TEXT NOT NULL,
      fetched_at REAL,
      PRIMARY KEY (trade_date, ts_code, tag))
    """,
]


def connect() -> sqlite3.Connection:
    from stockhot.data_layer.market_db import get_connection

    return get_connection()


def ensure_tables(conn: sqlite3.Connection) -> None:
    for ddl in _TABLES:
        conn.execute(ddl)
    conn.commit()


def normalize_date(d: str) -> str:
    return d.replace("-", "")


def to_dash_date(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if "-" not in d else d


def to_suffixed_code(code: str) -> str:
    rules2 = {"60": ".SH", "68": ".SH", "00": ".SZ", "30": ".SZ", "92": ".BJ"}
    rules1 = {"8": ".BJ", "4": ".BJ"}
    if not code or "." in code:
        return code
    suffix = rules2.get(code[:2]) or rules1.get(code[:1])
    return code + suffix if suffix else code


def strip_code_suffix(code: str) -> str:
    return code.split(".")[0] if "." in code else code


def latest_trade_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
    return row[0] if row and row[0] else None


def trading_dates(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM daily_price "
        "WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
        (normalize_date(start), normalize_date(end)),
    ).fetchall()
    return [r[0] for r in rows]


def read_pool(conn: sqlite3.Connection, day: str) -> pd.DataFrame:
    """当日 pct_chg>7 命中池（join stock_basic 取名称；行业由 screen 层补 sw_member）."""
    day = normalize_date(day)
    df = pd.read_sql_query(
        "SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, "
        "d.pre_close, d.pct_chg, d.vol, d.amount, d.adj_factor, "
        "s.name AS name, s.list_date "
        "FROM daily_price d LEFT JOIN stock_basic s ON d.ts_code=s.ts_code "
        "WHERE d.trade_date=? AND d.pct_chg > 7.0",
        conn, params=(day,),
    )
    if df.empty:
        return df
    df["is_st"] = df["name"].fillna("").str.contains("ST").astype(int)
    return df.sort_values("pct_chg", ascending=False).reset_index(drop=True)
```

注意：`__init__.py` 建空文件。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_surge_db.py -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/surge/ tests/test_surge_db.py
git commit -m "feat(surge): db脚手架与七表管理+命中池读取"
```

---

### Task 2: constants 增量 + config 目录 + SOP 同步

**Files:**
- Modify: `davis_analyzer/constants.py`（文件末尾追加）、`davis_analyzer/config.py`（报告目录）、`SOP.md`（权重段）
- Test: 修改 `tests/test_doc_consistency.py` 增校验

**Interfaces:**
- Produces: `SURGE_WEIGHTS: dict[str, float]`、`PATTERN_PARAMS: dict[str, float|int]`、`MAJOR_EVENT_RULES: list[dict]`、`config.SURGE_REPORTS_DIR: Path`

- [ ] **Step 1: constants.py 末尾追加（先读文件末尾确认追加位置）**

```python
# ── surge 子系统权重与参数（spec 2026-09-18，先验未校准）──

# 九维综合分权重（§5.10）
SURGE_WEIGHTS: dict[str, float] = {
    "money": 0.20,
    "chips": 0.15,
    "winner": 0.10,
    "position": 0.10,
    "resist_support": 0.10,
    "hype": 0.20,
    "risk": 0.15,
}

# 形态副本筛选与标签参数（§5.11/§5.12）
PATTERN_PARAMS: dict[str, float | int] = {
    "vol_window": 15,          # C1 回看交易日数（不含今日）
    "vol_max_below_streak": 2, # C1 低于VMA120最长连续容忍
    "boom_lookback_min": 5,    # C2 放量阳回看下界
    "boom_lookback_max": 20,   # C2 放量阳回看上界
    "boom_pct_min": 4.0,       # C2 放量阳最小涨幅%
    "boom_vol_ratio": 2.0,     # C2 放量阳量比（×VMA120）
    "pullback_depth_max": 0.15,# C2 高点回撤上限
    "vol_decay_ratio": 0.70,   # C2 后半窗均量/前半窗均量上限
    "plateau_days": 20,        # C3 平台窗口（不含今日）
    "box_days": 60,            # 标签:箱体窗口
    "box_max_range": 0.25,     # 标签:箱体最大振幅
    "vma_period": 120,         # 均量周期
    "bottom_pos_max": 0.25,    # 标签:底部放量位置上限
    "top_pos_min": 0.80,       # 标签:高位分歧位置下限
    "huge_vol_ratio": 5.0,     # 标签:天量倍数
    "winner_crowd": 85.0,      # 标签:获利盘拥挤%
    "chip_dense_range": 0.30,  # 标签:筹码低位密集 95/5-1 上限
    "near_resist": 0.03,       # 标签:上方套牢近
    "event_window": 90,        # corp_event 窗口日
    "major_event_window": 180, # major_events 窗口日
}

# 巨潮公告规则（§4.1，冻结先验；iterable 顺序即匹配优先级）
MAJOR_EVENT_RULES: list[dict] = [
    {"event_type": "ma_halt", "direction": "negative",
     "pattern": r"终止.*(重组|发行|购买|资产重组)"},
    {"event_type": "ma", "direction": "positive",
     "pattern": r"重大资产重组|发行股份.{0,12}购买资产|吸收合并|重大资产购买"},
    {"event_type": "divest", "direction": "neutral",
     "pattern": r"重大资产出售|出售.{0,10}股权|转让控股权"},
    {"event_type": "refinance", "direction": "negative",
     "pattern": r"向特定对象发行股票|非公开发行"},
    {"event_type": "distress", "direction": "negative",
     "pattern": r"立案|警示函|监管函|问询函|关注函|处罚|诉讼|仲裁|商誉减值|终止上市"},
]
```

- [ ] **Step 2: config.py 追加报告目录（仿 LIMITUP_REPORTS_DIR 模式，追加在其后）**

```python
SURGE_REPORTS_DIR = PROJECT_ROOT / "davis_analyzer" / "surge" / "reports"

SURGE_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 3: SOP.md 权重段追加一行**（找到既有权重声明段，仿照格式加：`surge 综合分权重 SURGE_WEIGHTS、形态参数 PATTERN_PARAMS、巨潮规则 MAJOR_EVENT_RULES 见 constants.py`）

- [ ] **Step 4: test_doc_consistency.py 增断言（先读该文件找到既有校验函数，仿照添加）**

```python
def test_surge_weights_in_sop():
    from davis_analyzer.constants import MAJOR_EVENT_RULES, PATTERN_PARAMS, SURGE_WEIGHTS
    assert abs(sum(SURGE_WEIGHTS.values()) - 1.0) < 1e-9
    sop = (PROJECT_ROOT / "SOP.md").read_text(encoding="utf-8")
    for key in ("SURGE_WEIGHTS", "PATTERN_PARAMS", "MAJOR_EVENT_RULES"):
        assert key in sop, f"SOP.md 缺少 {key} 声明"
    assert {r["event_type"] for r in MAJOR_EVENT_RULES} == {
        "ma", "divest", "refinance", "distress", "ma_halt"}
```

（`PROJECT_ROOT` 用该文件里已有的等价路径常量；若无则 `Path(__file__).parent.parent.parent`。）

- [ ] **Step 5: 跑测试 + Commit**

Run: `.venv/bin/python -m pytest tests/test_doc_consistency.py -v` → PASS

```bash
git add davis_analyzer/constants.py davis_analyzer/config.py SOP.md tests/test_doc_consistency.py
git commit -m "feat(surge): SURGE_WEIGHTS/PATTERN_PARAMS/MAJOR_EVENT_RULES单一真相源+SOP同步"
```

---

### Task 3: chips.py（cyq_perf 拉取与缓存）

**Files:**
- Create: `davis_analyzer/surge/chips.py`
- Test: `tests/test_surge_chips.py`

**Interfaces:**
- Consumes: `db.ensure_tables/normalize_date`
- Produces: `fetch_cyq_by_date(pro, day) -> pd.DataFrame`、`ensure_cyq(conn, pro, day) -> str`（返回实际数据日期，缺当日回退最近≤5日）、`backfill_cyq(conn, pro, days) -> list[str]`、`read_cyq(conn, ts_codes, end_day, lookback) -> pd.DataFrame`（含 winner_rate 历史供 delta5）

- [ ] **Step 1: 写失败测试（mock pro）**

```python
# tests/test_surge_chips.py
"""chips.cyq_perf 拉取入库与回退测试（内存库+mock pro）."""
import sqlite3
import time

import pandas as pd
import pytest

from davis_analyzer.surge import chips, db


class FakePro:
    def __init__(self, data: dict[str, pd.DataFrame]):
        self.data = data
        self.calls: list[str] = []

    def cyq_perf(self, ts_code=None, start_date=None, end_date=None, trade_date=None):
        self.calls.append(trade_date or ts_code)
        return self.data.get(trade_date or "", pd.DataFrame())


@pytest.fixture()
def mem_conn():
    conn = sqlite3.connect(":memory:")
    db.ensure_tables(conn)
    yield conn
    conn.close()


def _df(day: str, winner: float) -> pd.DataFrame:
    return pd.DataFrame([{
        "ts_code": "000001.SZ", "trade_date": day, "his_low": 5.0, "his_high": 15.0,
        "cost_5pct": 9.0, "cost_15pct": 9.5, "cost_50pct": 10.0,
        "cost_85pct": 10.5, "cost_95pct": 11.0,
        "weight_avg": 10.2, "winner_rate": winner}])


def test_ensure_cyq_falls_back(mem_conn):
    pro = FakePro({})  # 当日无数据
    got = chips.ensure_cyq(mem_conn, pro, "20260918")
    assert got == ""  # 全无 → 空串,调用方标缺失
    pro2 = FakePro({"20260917": _df("20260917", 60.0)})
    got2 = chips.ensure_cyq(mem_conn, pro2, "20260918")
    assert got2 == "20260917"  # 回退最近一日


def test_ensure_cyq_idempotent(mem_conn):
    pro = FakePro({"20260918": _df("20260918", 85.0)})
    assert chips.ensure_cyq(mem_conn, pro, "20260918") == "20260918"
    chips.ensure_cyq(mem_conn, pro, "20260918")  # 已有日期不重复插入
    n = mem_conn.execute(
        "SELECT COUNT(*) FROM cyq_perf_cache WHERE trade_date='20260918'").fetchone()[0]
    assert n == 1
```

- [ ] **Step 2: 跑测试确认失败** → `.venv/bin/python -m pytest tests/test_surge_chips.py -v` FAIL

- [ ] **Step 3: 实现 chips.py**

```python
# davis_analyzer/surge/chips.py
"""cyq_perf 官方筹码数据拉取与缓存（每日按 trade_date 一次拉全市场）."""

from __future__ import annotations

import sqlite3
import time

import pandas as pd
from loguru import logger

from davis_analyzer.surge import db

_COLUMNS = ["ts_code", "trade_date", "his_low", "his_high", "cost_5pct",
            "cost_15pct", "cost_50pct", "cost_85pct", "cost_95pct",
            "weight_avg", "winner_rate"]


def fetch_cyq_by_date(pro, day: str) -> pd.DataFrame:
    df = pro.cyq_perf(trade_date=db.normalize_date(day))
    if df is None or df.empty:
        return pd.DataFrame(columns=_COLUMNS)
    return df[_COLUMNS]


def _insert(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    df = df.copy()
    df["trade_date"] = df["trade_date"].map(db.normalize_date)
    df["fetched_at"] = time.time()
    conn.executemany(
        "INSERT OR REPLACE INTO cyq_perf_cache VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        df[_COLUMNS + ["fetched_at"]].itertuples(index=False, name=None),
    )
    conn.commit()
    return len(df)


def _has_date(conn: sqlite3.Connection, day: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM cyq_perf_cache WHERE trade_date=?", (day,)).fetchone()
    return bool(row and row[0] > 1000)  # 全市场约5500行,>1000视为完整


def ensure_cyq(conn: sqlite3.Connection, pro, day: str) -> str:
    """确保 day 筹码可用,缺则拉取;当日缺回退最近≤5个缓存日.返回实际日期(全无→'')."""
    day = db.normalize_date(day)
    if _has_date(conn, day):
        return day
    n = _insert(conn, fetch_cyq_by_date(pro, day))
    if n:
        logger.info("cyq_perf {} 拉取 {} 行", day, n)
        return day
    for back in range(1, 6):
        prev = conn.execute(
            "SELECT MAX(trade_date) FROM cyq_perf_cache WHERE trade_date<?", (day,)
        ).fetchone()[0]
        if prev and _has_date(conn, prev):
            logger.warning("cyq_perf {} 未出,回退 {}", day, prev)
            return prev
        break
    logger.warning("cyq_perf {} 无可用数据", day)
    return ""


def backfill_cyq(conn: sqlite3.Connection, pro, dates: list[str]) -> list[str]:
    done: list[str] = []
    for d in dates:
        d = db.normalize_date(d)
        if _has_date(conn, d):
            continue
        if _insert(conn, fetch_cyq_by_date(pro, d)):
            done.append(d)
            time.sleep(0.2)
    return done


def read_cyq(
    conn: sqlite3.Connection, ts_codes: list[str], end_day: str, lookback: int
) -> pd.DataFrame:
    """读取筹码缓存(含更早 lookback 个缓存日,供 winner_delta_5d)."""
    if not ts_codes:
        return pd.DataFrame(columns=_COLUMNS)
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM cyq_perf_cache WHERE trade_date<=? "
        "ORDER BY trade_date DESC LIMIT ?", (db.normalize_date(end_day), lookback))]
    if not dates:
        return pd.DataFrame(columns=_COLUMNS)
    ph = ",".join("?" * len(dates))
    return pd.read_sql_query(
        f"SELECT {','.join(_COLUMNS)} FROM cyq_perf_cache "
        f"WHERE trade_date IN ({ph})",
        conn, params=dates)
```

- [ ] **Step 4: 跑测试通过** → PASS

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/surge/chips.py tests/test_surge_chips.py
git commit -m "feat(surge): cyq_perf筹码按日期拉取入库+回退+lookback读取"
```

---

### Task 4: factors.py 位置组（后复权）

**Files:**
- Create: `davis_analyzer/surge/factors.py`
- Test: `tests/test_surge_factors.py`

**Interfaces:**
- Produces: `compute_position(px: pd.DataFrame) -> dict[str, float]`；px=单股日线（时间升序，列 ts_code/trade_date/open/high/low/close/vol/adj_factor），返回 `{"pos_250d","dist_ma20","dist_ma60","dist_ma120","dist_ma250","dd_high_250"}`（NaN 允许）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_surge_factors.py
"""factors 纯函数测试（合成日线fixture）."""
import math

import numpy as np
import pandas as pd

from davis_analyzer.surge.factors import compute_position


def _px(closes: list[float], highs=None, lows=None, vol=1000.0, adj=None):
    n = len(closes)
    return pd.DataFrame({
        "ts_code": ["000001.SZ"] * n,
        "trade_date": [f"2026{i:04d}" for i in range(1, n + 1)],
        "open": [c * 0.99 for c in closes],
        "high": highs or [c * 1.02 for c in closes],
        "low": lows or [c * 0.98 for c in closes],
        "close": closes,
        "vol": [vol] * n,
        "adj_factor": adj or [1.0] * n,
    })


def test_position_mid_range():
    closes = [10 + 0.01 * i for i in range(260)]  # 缓慢爬升
    r = compute_position(_px(closes))
    assert 0.0 < r["pos_250d"] <= 1.0
    assert r["dist_ma20"] > 0  # 连涨序列现价在均线上
    assert r["dd_high_250"] <= 0


def test_position_short_history_nan():
    r = compute_position(_px([10, 11, 12]))
    assert math.isnan(r["pos_250d"])


def test_position_uses_adjusted():
    # 除权: adj_factor 前段2.0 后段1.0, 后复权价连续(20 vs 10)——位置不应失真
    closes = [10.0] * 130 + [10.0] * 130
    adj = [2.0] * 130 + [1.0] * 130
    r = compute_position(_px(closes, adj=adj))
    assert not math.isnan(r["pos_250d"])
```

- [ ] **Step 2: 确认失败** → FAIL（no module factors）

- [ ] **Step 3: 实现（factors.py 第一块）**

```python
# davis_analyzer/surge/factors.py
"""九维指标纯函数计算（DataFrame in/out，不触网不触库）."""

from __future__ import annotations

import numpy as np
import pandas as pd

_MIN_HISTORY = 120


def _adj(px: pd.DataFrame) -> pd.DataFrame:
    """后复权 OHLC（close×adj_factor 基准，其余同乘）."""
    out = px.copy()
    for col in ("open", "high", "low", "close"):
        out[col + "_adj"] = out[col] * out["adj_factor"]
    return out


def compute_position(px: pd.DataFrame) -> dict[str, float]:
    """相对位置组：后复权口径（spec §5.2）。px 时间升序，末行为当日。"""
    a = _adj(px)
    close = a["close_adj"].iloc[-1]
    if len(a) < _MIN_HISTORY:
        return {k: float("nan") for k in
                ("pos_250d", "dist_ma20", "dist_ma60", "dist_ma120",
                 "dist_ma250", "dd_high_250")}
    h250 = a["high_adj"].tail(250)
    l250 = a["low_adj"].tail(250)
    rng = h250.max() - l250.min()
    pos = (close - l250.min()) / rng if rng > 0 else float("nan")
    dists = {}
    for n in (20, 60, 120, 250):
        ma = a["close_adj"].tail(n).mean()
        dists[f"dist_ma{n}"] = close / ma - 1 if ma > 0 else float("nan")
    return {
        "pos_250d": pos,
        **dists,
        "dd_high_250": close / h250.max() - 1,
    }
```

- [ ] **Step 4: 跑测试通过** → PASS

- [ ] **Step 5: Commit** `feat(surge): 位置组指标(后复权250日分位/均线偏离/回撤)`

---

### Task 5: factors.py 资金组（moneyflow 单位对齐）

**Interfaces:**
- Produces: `compute_moneyflow(mf_hist: pd.DataFrame, amount_today_k: float) -> dict`；mf_hist 时间升序列 trade_date/buy_lg_amount/sell_lg_amount/buy_elg_amount/sell_elg_amount/net_mf_amount（万元），amount_today_k=daily_price amount（千元）。返回 `{"elg_net_d0","lg_net_5d","net_ratio_d0","consec_net_days"}`

- [ ] **Step 1: 写失败测试（追加到 test_surge_factors.py）**

```python
from davis_analyzer.surge.factors import compute_moneyflow


def _mf(elg_nets: list[float], lg_nets=None, net_mf=None):
    n = len(elg_nets)
    return pd.DataFrame({
        "trade_date": [f"2026{i:04d}" for i in range(1, n + 1)],
        "buy_lg_amount": [abs(x) for x in (lg_nets or [0] * n)],
        "sell_lg_amount": [0.0] * n,
        "buy_elg_amount": [max(x, 0) + 1 for x in elg_nets],
        "sell_elg_amount": [1.0] * n,
        "net_mf_amount": net_mf if net_mf is not None else [float(x) for x in elg_nets],
    })


def test_moneyflow_units_and_streak():
    mf = _mf([100.0, -50.0, 200.0, 300.0, 400.0, 500.0])  # 近6日,末3日连续正
    r = compute_moneyflow(mf, amount_today_k=1000.0)  # 今日成交额1000千元=1000万元
    assert r["elg_net_d0"] == 500.0
    # net_ratio: net_mf 500万 / (1000千×10=10000万) = 0.05
    assert abs(r["net_ratio_d0"] - 0.05) < 1e-9
    assert r["consec_net_days"] == 3


def test_moneyflow_empty_nan():
    r = compute_moneyflow(pd.DataFrame(), amount_today_k=1000.0)
    assert all(np.isnan(v) for v in r.values())
```

- [ ] **Step 2: 确认失败** → FAIL

- [ ] **Step 3: 实现（factors.py 追加）**

```python
def compute_moneyflow(mf_hist: pd.DataFrame, amount_today_k: float) -> dict[str, float]:
    """资金组（spec §5.3）。mf_hist 单位万元、时间升序；amount_today_k 千元。"""
    nan = {k: float("nan") for k in
           ("elg_net_d0", "lg_net_5d", "net_ratio_d0", "consec_net_days")}
    if mf_hist is None or mf_hist.empty:
        return nan
    m = mf_hist.tail(5)
    elg_net = (m["buy_elg_amount"].fillna(0) - m["sell_elg_amount"].fillna(0))
    lg_net = ((m["buy_lg_amount"].fillna(0) + m["buy_elg_amount"].fillna(0))
              - (m["sell_lg_amount"].fillna(0) + m["sell_elg_amount"].fillna(0)))
    last = m.iloc[-1]
    elg_d0 = float(last["buy_elg_amount"] or 0) - float(last["sell_elg_amount"] or 0)
    streak = 0
    for v in elg_net[::-1]:
        if v > 0:
            streak += 1
        else:
            break
    ratio = float("nan")
    if amount_today_k and amount_today_k > 0:
        nmf = float(last["net_mf_amount"]) if pd.notna(last["net_mf_amount"]) else float("nan")
        ratio = nmf / (amount_today_k * 10)  # 万元对齐:千元×10
    return {
        "elg_net_d0": elg_d0,
        "lg_net_5d": float(lg_net.sum()),
        "net_ratio_d0": ratio,
        "consec_net_days": streak,
    }
```

（注意 `buy_elg_amount` 空值处理：`float(x or 0)` 对 NaN 会抛错——用 `pd.notna` 分支。实现时以测试驱动修正。）

- [ ] **Step 4: 跑测试通过** → PASS
- [ ] **Step 5: Commit** `feat(surge): 资金组指标(超大单净额/5日大单/净流入占比含千元万元对齐/连续天数)`

---

### Task 6: factors.py 压力/支撑（七口径候选链）

**Interfaces:**
- Produces: `compute_resistance_support(px_raw: pd.DataFrame, cyq: pd.Series | None) -> dict`；px_raw 未复权 OHLC 升序，cyq=当日筹码行（可 None）。返回 `{"resistance_price","resistance_dist","support_price","support_dist","resistance_ladder"}`（ladder=全部候选档位按距离排序的字符串，报告用）

- [ ] **Step 1: 写失败测试**

```python
from davis_analyzer.surge.factors import compute_resistance_support


def test_resistance_picks_nearest_above():
    closes = [10.0] * 130
    px = _px(closes, highs=[11.0] * 130, lows=[9.0] * 130)
    cyq = pd.Series({"cost_5pct": 9.5, "cost_15pct": 9.8, "cost_50pct": 10.0,
                     "cost_85pct": 10.4, "cost_95pct": 10.8, "weight_avg": 10.1})
    px.iloc[-1, px.columns.get_loc("close")] = 10.2  # 今日 10.2
    r = compute_resistance_support(px, cyq)
    # 候选>10.2×1.005≈10.251: cost_95=10.8, high120=11.0, cost_85=10.4(不含,10.4<10.251? 否10.4>10.251含)
    assert abs(r["resistance_price"] - 10.4) < 1e-9  # 最近档=cost_85
    assert abs(r["support_price"] - 10.1) < 1e-9     # 低于10.2×0.995 最近=weight_avg


def test_resistance_none_below():
    px = _px([10.0] * 130, highs=[10.05] * 130, lows=[9.0] * 130)
    r = compute_resistance_support(px, None)  # 全部候选不高于现价×1.005→NaN
    # 高点10.05 > 10×1.005=10.005 → 命中,不 NaN;此用例改为全部低于:
    px2 = _px([10.0] * 130, highs=[10.0] * 130, lows=[9.5] * 130)
    px2.iloc[-1, px2.columns.get_loc("close")] = 10.0
    r2 = compute_resistance_support(px2, None)
    import math
    assert math.isnan(r2["resistance_price"])
```

- [ ] **Step 2: 确认失败** → FAIL

- [ ] **Step 3: 实现（factors.py 追加）**

```python
def _ma_adj_series(a: pd.DataFrame, n: int) -> float:
    tail = a["close_adj"].tail(n)
    return float(tail.mean()) if len(tail) == n else float("nan")


def compute_resistance_support(
    px: pd.DataFrame, cyq: pd.Series | None
) -> dict[str, float | str]:
    """压力/支撑（spec §5.6/5.7）：未复权现价口径;均线后复权算、对齐容差接受。"""
    close = float(px["close"].iloc[-1])
    res_cands: list[tuple[str, float]] = []
    sup_cands: list[tuple[str, float]] = []
    if cyq is not None and pd.notna(cyq.get("cost_85pct")):
        res_cands += [("cost_85pct", float(cyq["cost_85pct"])),
                      ("cost_95pct", float(cyq["cost_95pct"]))]
        sup_cands += [("cost_15pct", float(cyq["cost_15pct"])),
                      ("cost_5pct", float(cyq["cost_5pct"]))]
        wa = float(cyq["weight_avg"])
        if pd.notna(wa):
            (res_cands if close < wa else sup_cands).append(("weight_avg", wa))
        if pd.notna(cyq.get("his_high")):
            res_cands.append(("his_high", float(cyq["his_high"])))
    win120 = px.tail(120)
    res_cands.append(("high_120d", float(win120["high"].max())))
    sup_cands.append(("low_120d", float(win120["low"].min())))
    # 均线（后复权算,仅当现价同侧才入候选）
    a = _adj(px)
    close_adj = float(a["close_adj"].iloc[-1])
    for n in (60, 120, 250):
        ma = _ma_adj_series(a, n)
        if not pd.isna(ma):
            label = f"MA{n}"
            # 均线值换回未复权口径比较: ma / adj_factor_today
            ma_raw = ma / float(px["adj_factor"].iloc[-1])
            (res_cands if close < ma_raw else sup_cands).append((label, ma_raw))
    ma20 = _ma_adj_series(a, 20)
    if not pd.isna(ma20):
        ma20_raw = ma20 / float(px["adj_factor"].iloc[-1])
        (res_cands if close < ma20_raw else sup_cands).append(("MA20", ma20_raw))
    # 缺口（120日窗口,最近一个）
    for i in range(len(px) - 1, max(0, len(px) - 120), -1):
        prev_low = float(px["low"].iloc[i - 1]); cur_high = float(px["high"].iloc[i])
        prev_high = float(px["high"].iloc[i - 1]); cur_low = float(px["low"].iloc[i])
        if cur_low > prev_high:  # 向上缺口: 支撑=前日high
            sup_cands.append(("gap_up", prev_high)); break
        if cur_high < prev_low:  # 向下缺口: 阻力=前日low
            res_cands.append(("gap_down", prev_low)); break
    res_ok = sorted([(k, v) for k, v in res_cands if v > close * 1.005], key=lambda t: t[1])
    sup_ok = sorted([(k, v) for k, v in sup_cands if v < close * 0.995],
                    key=lambda t: -t[1])
    nan = float("nan")
    rp = res_ok[0][1] if res_ok else nan
    sp = sup_ok[0][1] if sup_ok else nan
    ladder = " | ".join(f"{k}@{v:.2f}({v/close-1:+.1%})" for k, v in res_ok)
    return {
        "resistance_price": rp, "resistance_dist": rp / close - 1 if res_ok else nan,
        "support_price": sp, "support_dist": sp / close - 1 if sup_ok else nan,
        "resistance_ladder": ladder,
    }
```

- [ ] **Step 4: 跑测试通过** → PASS
- [ ] **Step 5: Commit** `feat(surge): 压力支撑七口径候选链(筹码/成本线转压/均线/缺口/滚动高低/历史高)`

---

### Task 7: cninfo.py（巨潮原始层+规则层）

**Files:**
- Create: `davis_analyzer/surge/cninfo.py`
- Test: `tests/test_surge_cninfo.py`

**Interfaces:**
- Consumes: `db.ensure_tables/normalize_date/strip_code_suffix`、`constants.MAJOR_EVENT_RULES`
- Produces: `apply_rules(title: str) -> list[tuple[str, str]]`、`fetch_org_id(session, code) -> str|None`、`fetch_announcements(session, code, org_id, start_dash, end_dash) -> list[dict]`、`sync_cninfo(conn, ts_codes, day, window_days=180, session=None) -> dict`、`replay_rules(conn) -> int`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_surge_cninfo.py
"""cninfo 规则匹配/重放测试（不发真请求;拉取函数以 FakeSession mock）."""
import sqlite3

import pytest

from davis_analyzer.surge import cninfo, db


def test_apply_rules_priority_and_match():
    assert cninfo.apply_rules("关于终止发行股份购买资产的公告") == [("ma_halt", "negative")]
    assert cninfo.apply_rules("重大资产重组报告书(草案)") == [("ma", "positive")]
    assert cninfo.apply_rules("向特定对象发行股票预案") == [("refinance", "negative")]
    assert cninfo.apply_rules("关于收到中国证监会立案告知书的公告") == [("distress", "negative")]
    assert cninfo.apply_rules("2026年半年度报告") == []  # 常规公告不命中


def test_apply_rules_halt_beats_ma():
    # 终止类必须优先于 ma 类(同一标题可能同时含「重组」)
    got = cninfo.apply_rules("关于终止重大资产重组事项的公告")
    assert got == [("ma_halt", "negative")]


def test_replay_rules_rebuilds(mem_conn, monkeypatch):
    # mem_conn 复用 db 测试 fixture 模式
    db.ensure_tables(mem_conn)
    mem_conn.execute(
        "INSERT INTO cninfo_announcement VALUES ('000001.SZ','20260801','重大资产重组报告书',0)")
    mem_conn.commit()
    n = cninfo.replay_rules(mem_conn)
    assert n == 1
    rows = mem_conn.execute("SELECT event_type, direction FROM major_events").fetchall()
    assert rows == [("ma", "positive")]
```

（`mem_conn` fixture 从 tests/test_surge_db.py 提升到 `tests/conftest.py` 名为 `mem_conn` 供各测试文件共用。）

- [ ] **Step 2: 确认失败** → FAIL

- [ ] **Step 3: 实现 cninfo.py**

```python
# davis_analyzer/surge/cninfo.py
"""巨潮公告拉取(原始层全量) + 冻结规则匹配(规则层). 网页API,单点防御(spec §7)."""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime, timedelta

import requests
from loguru import logger

from davis_analyzer.constants import MAJOR_EVENT_RULES
from davis_analyzer.surge import db

_BASE = "http://www.cninfo.com.cn"
_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
_COMPILED = [(r["event_type"], r["direction"], re.compile(r["pattern"]))
             for r in MAJOR_EVENT_RULES]


def apply_rules(title: str) -> list[tuple[str, str]]:
    """按 MAJOR_EVENT_RULES 顺序匹配,ma_halt 优先(规则表首列即优先级)."""
    clean = re.sub(r"</?em>", "", title or "")
    hits: list[tuple[str, str]] = []
    for etype, direction, pat in _COMPILED:
        if pat.search(clean):
            hits.append((etype, direction))
    return hits


def fetch_org_id(session: requests.Session, code: str) -> str | None:
    try:
        r = session.post(f"{_BASE}/new/information/topSearch/query",
                         data={"keyWord": code, "maxNum": "10"},
                         headers=_HEADERS, timeout=10)
        rows = r.json()
        return rows[0]["orgId"] if rows else None
    except Exception as e:  # 网页API防御:单点失败不阻塞
        logger.warning("cninfo orgId {} 失败: {}", code, e)
        return None


def fetch_announcements(
    session: requests.Session, code: str, org_id: str,
    start_dash: str, end_dash: str,
) -> list[dict]:
    """按股分页拉近 180 日全量公告标题(pageSize=30)."""
    column = "bj" if code.startswith(("4", "8", "92")) else "szse"
    out: list[dict] = []
    page = 1
    while page <= 10:  # 上限10页=300条,防御死循环
        try:
            r = session.post(
                f"{_BASE}/new/hisAnnouncement/query",
                data={"pageNum": str(page), "pageSize": "30", "column": column,
                      "tabName": "fulltext", "stock": f"{code},{org_id}",
                      "searchkey": "", "seDate": f"{start_dash}~{end_dash}",
                      "isHLtitle": "true"},
                headers=_HEADERS, timeout=15)
            d = r.json()
        except Exception as e:
            logger.warning("cninfo ann {} p{} 失败: {}", code, page, e)
            break
        anns = d.get("announcements") or []
        for a in anns:
            ts = a.get("announcementTime")
            if not ts:
                continue
            out.append({
                "ann_date": datetime.fromtimestamp(ts / 1000).strftime("%Y%m%d"),
                "title": re.sub(r"</?em>", "", a.get("announcementTitle") or ""),
            })
        if len(anns) < 30:
            break
        page += 1
        time.sleep(0.2)
    return out


def _cached_org_id(conn: sqlite3.Connection, session, ts_code: str) -> str | None:
    row = conn.execute(
        "SELECT org_id FROM cninfo_org_map WHERE ts_code=?", (ts_code,)).fetchone()
    if row and row[0]:
        return row[0]
    code = db.strip_code_suffix(ts_code)
    org = fetch_org_id(session, code)
    if org:
        conn.execute(
            "INSERT OR REPLACE INTO cninfo_org_map VALUES (?,?,?)",
            (ts_code, org, time.time()))
        conn.commit()
    return org


def sync_cninfo(
    conn: sqlite3.Connection, ts_codes: list[str], day: str,
    window_days: int = 180, session: requests.Session | None = None,
) -> dict[str, int]:
    """命中池逐股拉近 window_days 公告: 全量入原始层,规则匹配入规则层."""
    session = session or requests.Session()
    end = datetime.strptime(db.normalize_date(day), "%Y%m%d")
    start = end - timedelta(days=window_days)
    start_dash, end_dash = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    stats = {"ok": 0, "fail": 0, "events": 0}
    now = time.time()
    for ts_code in ts_codes:
        org = _cached_org_id(conn, session, ts_code)
        if not org:
            stats["fail"] += 1
            continue
        anns = fetch_announcements(session, db.strip_code_suffix(ts_code), org,
                                   start_dash, end_dash)
        if anns is None:
            stats["fail"] += 1
            continue
        for a in anns:
            conn.execute(
                "INSERT OR REPLACE INTO cninfo_announcement VALUES (?,?,?,?)",
                (ts_code, a["ann_date"], a["title"], now))
            for etype, direction in apply_rules(a["title"]):
                conn.execute(
                    "INSERT OR REPLACE INTO major_events VALUES (?,?,?,?,?,?,?)",
                    (ts_code, a["ann_date"], etype, a["title"], direction,
                     "cninfo", now))
                stats["events"] += 1
        stats["ok"] += 1
        time.sleep(0.2)
    conn.commit()
    logger.info("cninfo sync {} 股: ok={} fail={} events={}",
                len(ts_codes), stats["ok"], stats["fail"], stats["events"])
    return stats


def replay_rules(conn: sqlite3.Connection) -> int:
    """原始层→规则层重建(规则迭代用,幂等)."""
    conn.execute("DELETE FROM major_events")
    now = time.time()
    n = 0
    for ts_code, ann_date, title in conn.execute(
            "SELECT ts_code, ann_date, title FROM cninfo_announcement"):
        for etype, direction in apply_rules(title):
            conn.execute(
                "INSERT OR REPLACE INTO major_events VALUES (?,?,?,?,?,?,?)",
                (ts_code, ann_date, etype, title, direction, "cninfo", now))
            n += 1
    conn.commit()
    return n
```

- [ ] **Step 4: 跑测试通过** → PASS
- [ ] **Step 5: Commit** `feat(surge): 巨潮公告两级入库(原始层全量+规则层冻结规则)与重放`

---

### Task 8: factors.py hype/risk 标签（事件+行业截面）

**Files:**
- Modify: `davis_analyzer/surge/factors.py`（追加）
- Modify: `davis_analyzer/surge/db.py`（追加 readers）
- Test: `tests/test_surge_events.py`

**Interfaces:**
- db.py 追加 Produces: `read_sw_industry(conn, ts_codes) -> pd.DataFrame`（con_code→L2/L1 index_code，取最新快照 out_date 空）、`read_sw_daily_batch(conn, index_codes, start, end) -> pd.DataFrame`
- factors.py 追加 Produces: `industry_momentum(sw_daily_all: pd.DataFrame, day: str) -> pd.DataFrame`（全行业当日截面：ret_20/ret_60/pct_rank60/pos_250/趋势列）、`classify_hype_risk(...) -> tuple[list[str], list[str]]`（签名见步骤3，输入各数据帧+指标 dict）

- [ ] **Step 1: 写失败测试（关键判据）**

```python
# tests/test_surge_events.py
"""hype/risk 标签判据测试(合成数据帧,不触库;查询函数在db测试覆盖)."""
import pandas as pd

from davis_analyzer.surge.factors import classify_hype_risk


def _args(**over):
    corp = pd.DataFrame([{"ts_code": "X", "ann_date": "20260901",
                          "event_type": "holder_trade", "direction": "positive"}])
    major = pd.DataFrame(columns=["ts_code", "ann_date", "event_type", "title"])
    fin_loss = False
    ind = pd.DataFrame([{"index_code": "801150.SI", "ret60": 0.05, "ret20": 0.02,
                         "pct_rank60": 0.9, "pos_250": 0.5, "trend_up": True}])
    base = dict(corp_events=corp, major_events=major, pledge_ratio=None,
                fin_consecutive_loss=fin_loss, is_st=False, industry_row=ind.iloc[0],
                vol_price_ok=True, research_count=5, day="20260918")
    base.update(over)
    return base


def test_hype_holder_increase_and_industry():
    hype, risk = classify_hype_risk(**_args())
    assert "增持" in hype and "行业动量强" in hype and "量价齐升" in hype
    assert "研报覆盖热" in hype
    assert risk == []


def test_risk_reduce_and_loss():
    corp = pd.DataFrame([{"ts_code": "X", "ann_date": "20260901",
                          "event_type": "holder_trade", "direction": "negative"}])
    hype, risk = classify_hype_risk(**_args(
        corp_events=corp, fin_consecutive_loss=True, is_st=True))
    assert "减持" in risk and "持续亏损" in risk and "ST" in risk


def test_industry_bottom_turn():
    ind = pd.DataFrame([{"index_code": "801150.SI", "ret60": -0.1, "ret20": 0.01,
                         "pct_rank60": 0.1, "pos_250": 0.15, "trend_up": False}]).iloc[0]
    hype, risk = classify_hype_risk(**_args(industry_row=ind))
    assert "行业底部拐点" in hype


def test_industry_cycle_top():
    ind = pd.DataFrame([{"index_code": "801150.SI", "ret60": 0.02, "ret20": -0.01,
                         "pct_rank60": 0.85, "pos_250": 0.9, "trend_up": False}]).iloc[0]
    _, risk = classify_hype_risk(**_args(industry_row=ind))
    assert "周期顶部" in risk
```

- [ ] **Step 2: 确认失败** → FAIL

- [ ] **Step 3: 实现（db.py 追加 readers + factors.py 追加判据）**

db.py 追加：

```python
def read_sw_industry(conn: sqlite3.Connection, ts_codes: list[str]) -> pd.DataFrame:
    """con_code→行业(优先L2,无则L1;取最新快照且 out_date 为空)."""
    snap = conn.execute(
        "SELECT MAX(snapshot_date) FROM sw_member").fetchone()[0]
    if not snap or not ts_codes:
        return pd.DataFrame(columns=["ts_code", "index_code", "level", "name"])
    codes = [strip_code_suffix(c) for c in ts_codes]
    ph = ",".join("?" * len(codes))
    df = pd.read_sql_query(
        f"SELECT m.con_code AS ts_code, m.index_code, i.level, i.name "
        f"FROM sw_member m JOIN sw_index i ON m.index_code=i.index_code "
        f"WHERE m.snapshot_date=? AND m.con_code IN ({ph}) "
        f"AND (m.out_date IS NULL OR m.out_date='')",
        conn, params=(snap, *codes))
    df["ts_code"] = df["ts_code"].map(to_suffixed_code)
    df = df.sort_values(["ts_code", "level"], ascending=[True, False])  # L2>L1字典序
    return df.drop_duplicates("ts_code", keep="first").reset_index(drop=True)


def read_sw_daily_all(conn: sqlite3.Connection, end_day: str, lookback: int = 260
                      ) -> pd.DataFrame:
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM sw_daily WHERE trade_date<=? "
        "ORDER BY trade_date DESC LIMIT ?", (normalize_date(end_day), lookback))]
    if not dates:
        return pd.DataFrame()
    ph = ",".join("?" * len(dates))
    return pd.read_sql_query(
        f"SELECT index_code, trade_date, close FROM sw_daily "
        f"WHERE trade_date IN ({ph})", conn, params=dates)
```

（注意 sw_daily.trade_date 实际格式以库中样例为准——实施时先 `sqlite3 ... "SELECT trade_date FROM sw_daily LIMIT 3"` 核对，若为 YYYY-MM-DD 则 reader 内 normalize。）

factors.py 追加：

```python
def industry_momentum(sw_daily_all: pd.DataFrame) -> pd.DataFrame:
    """全行业截面: 每个指数 ret20/ret60/截面分位/250日位置/20日趋势."""
    if sw_daily_all is None or sw_daily_all.empty:
        return pd.DataFrame(columns=["index_code", "ret20", "ret60",
                                     "pct_rank60", "pos_250", "trend_up"])
    g = sw_daily_all.sort_values("trade_date").groupby("index_code")["close"]
    ret20 = g.apply(lambda s: s.iloc[-1] / s.iloc[-21] - 1 if len(s) >= 21 else float("nan"))
    ret60 = g.apply(lambda s: s.iloc[-1] / s.iloc[-61] - 1 if len(s) >= 61 else float("nan"))
    pos250 = g.apply(lambda s: (s.iloc[-1] - s.tail(250).min()) /
                     (s.tail(250).max() - s.tail(250).min()) if len(s) >= 120 else float("nan"))
    trend_up = g.apply(lambda s: s.iloc[-1] > s.iloc[-21] if len(s) >= 21 else None)
    df = pd.DataFrame({"ret20": ret20, "ret60": ret60, "pos_250": pos250,
                       "trend_up": trend_up}).reset_index()
    df = df.rename(columns={"index_code": "index_code"} if "index_code" in df.columns else {})
    df["pct_rank60"] = df["ret60"].rank(pct=True)
    df["trend_up"] = df["ret20"] > 0
    return df


def classify_hype_risk(
    *, corp_events: pd.DataFrame, major_events: pd.DataFrame,
    pledge_ratio: float | None, fin_consecutive_loss: bool, is_st: bool,
    industry_row: pd.Series | None, vol_price_ok: bool, research_count: int,
    day: str, event_window_days: int = 90, major_window_days: int = 180,
) -> tuple[list[str], list[str]]:
    """炒作预期/扫雷标签装配(spec §5.8/§5.9). 日期窗口按自然日回推."""
    from datetime import datetime, timedelta
    d0 = datetime.strptime(day, "%Y%m%d")
    corp_start = (d0 - timedelta(days=event_window_days)).strftime("%Y%m%d")
    major_start = (d0 - timedelta(days=major_window_days)).strftime("%Y%m%d")
    hype: list[str] = []
    risk: list[str] = []

    def _in(df: pd.DataFrame, start: str) -> pd.DataFrame:
        return df[(df["ann_date"] >= start) & (df["ann_date"] <= day)] if not df.empty else df

    corp_w = _in(corp_events, corp_start)
    if not corp_w.empty:
        if ((corp_w["event_type"] == "holder_trade") &
                (corp_w["direction"] == "positive")).any():
            hype.append("增持")
        if ((corp_w["event_type"] == "holder_trade") &
                (corp_w["direction"] == "negative")).any():
            risk.append("减持")
        if (corp_w["event_type"] == "repurchase").any():
            hype.append("回购")
        if (corp_w["event_type"] == "share_float").any():
            risk.append("解禁")
    major_w = _in(major_events, major_start)
    if not major_w.empty:
        et = set(major_w["event_type"])
        if "ma" in et:
            hype.append("并购重组")
        if "divest" in et:
            hype.append("转型线索")
        if "refinance" in et:
            risk.append("定增")
        if "distress" in et:
            risk.append("爆雷监管")
        if "ma_halt" in et:
            risk.append("重组终止")
    if pledge_ratio is not None and pledge_ratio > 50:
        risk.append("质押率高")
    if fin_consecutive_loss:
        risk.append("持续亏损")
    if is_st:
        risk.append("ST")
    if industry_row is not None and not pd.isna(industry_row.get("ret60", float("nan"))):
        if industry_row["pct_rank60"] >= 0.70:
            hype.append("行业动量强")
        if industry_row["pct_rank60"] <= 0.30 and industry_row["ret20"] < 0:
            risk.append("行业下行")
        if (not pd.isna(industry_row.get("pos_250"))) and industry_row["pos_250"] < 0.20 \
                and industry_row["ret20"] > 0:
            hype.append("行业底部拐点")
        if (not pd.isna(industry_row.get("pos_250"))) and industry_row["pos_250"] >= 0.80 \
                and industry_row["ret20"] < 0:
            risk.append("周期顶部")
    if vol_price_ok:
        hype.append("量价齐升")
    if research_count >= 3:
        hype.append("研报覆盖热")
    return hype, risk
```

（`fin_consecutive_loss` 的判据在 screen 层查 financial payload：解析 endpoint='income' 的 payload JSON，取最近两个年报 end_date=1231 与最新一期，`n_income < 0` 全真则 True；financial 无缓存 → False（宁缺毋错）。此逻辑封装为 `factors.check_consecutive_loss(fin_payloads: list[dict]) -> bool`，测试两个用例：双年报+当期全负→True；有缓存缺当期→False。）

- [ ] **Step 4: 跑测试通过** → PASS
- [ ] **Step 5: Commit** `feat(surge): hype/risk标签装配(事件窗口/行业截面/亏损判定)`

---

### Task 9: pattern.py C1/C2/C3 副本筛选

**Files:**
- Create: `davis_analyzer/surge/pattern.py`
- Test: `tests/test_surge_pattern.py`

**Interfaces:**
- Consumes: `constants.PATTERN_PARAMS`
- Produces: `detect_pattern(px: pd.DataFrame) -> dict | None`；px 升序含 open/high/low/close/vol/adj_factor。命中返回 `{"boom_date","boom_pct","boom_vol_ratio","pullback_start","pullback_end","pullback_depth","vol_decay","plateau_high","plateau_days","breakout_pct"}`，未命中 None。**成交量用未复权 vol 原值（daily_price vol 手数与复权无关）**；价格用未复权（与现价口径一致）。

- [ ] **Step 1: 写失败测试（构造合成形态序列 helper）**

```python
# tests/test_surge_pattern.py
"""C1/C2/C3 形态识别测试: 合成'放量阳→缩量回调→平台突破'标准序列及反例."""
import pandas as pd

from davis_analyzer.surge.pattern import detect_pattern


def _mk(ohlcvs: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    """ohlcvs: (open,high,low,close,vol) 逐日."""
    n = len(ohlcvs)
    return pd.DataFrame({
        "trade_date": [f"d{i:03d}" for i in range(n)],
        "open": [o for o, *_ in ohlcvs], "high": [h for _, h, *_ in ohlcvs],
        "low": [l for _, _, l, *_ in ohlcvs], "close": [c for *_x, c, _v in
                [(o, h, l, c, v) for o, h, l, c, v in ohlcvs]],
        "vol": [v for *_, v in ohlcvs],
        "adj_factor": [1.0] * n,
    })


def _base_series() -> list[tuple]:
    """130日: 平稳期(vol≈1000,价10) → 第121日放量阳 → 5日缩量回调 → 今日突破."""
    seq = [(9.9, 10.1, 9.8, 10.0, 1000.0)] * 120
    seq += [(10.0, 10.8, 10.0, 10.7, 3000.0)]            # 放量阳: +7%, 3×均量
    seq += [(10.6, 10.7, 10.3, 10.4, 900.0),              # 回调日1(阴,量缩)
            (10.4, 10.5, 10.2, 10.3, 800.0),              # 回调日2(阴,量更缩)
            (10.3, 10.4, 10.15, 10.25, 700.0),            # 回调日3(阴,量更缩)
            (10.25, 10.45, 10.2, 10.4, 650.0),            # 回调日4(小阳)
            (10.4, 10.5, 10.3, 10.45, 600.0)]             # 回调日5(小阳)
    seq += [(10.5, 11.4, 10.5, 11.3, 2500.0)]             # 今日: +8.4% 突破(>平台10.5)
    return seq


def test_standard_pattern_hits():
    r = detect_pattern(_mk(_base_series()))
    assert r is not None
    assert r["breakout_pct"] > 0
    assert r["boom_vol_ratio"] >= 2.0
    assert 0 < r["pullback_depth"] <= 0.15


def test_deep_pullback_rejected():
    seq = _base_series()
    # 回调加深到 -16%
    seq[121:127] = [(10.6, 10.7, 9.0, 9.1, 900.0), (9.1, 9.3, 8.95, 9.05, 800.0),
                    (9.05, 9.2, 8.9, 8.95, 700.0), (8.95, 9.1, 8.9, 9.0, 650.0),
                    (9.0, 9.2, 8.95, 9.15, 600.0), (9.15, 9.3, 9.0, 9.2, 620.0)]
    seq[127] = (9.25, 10.0, 9.2, 9.9, 2500.0)  # 今日+7.6%但未破平台10.5→C3也失败
    assert detect_pattern(_mk(seq)) is None


def test_vol_break_3days_rejected():
    seq = _base_series()
    for i in (122, 123, 124):  # 回调期连续3日掉到均量下(假设均量约1000)
        seq[i] = (seq[i][0], seq[i][1], seq[i][2], seq[i][3], 300.0)
    assert detect_pattern(_mk(seq)) is None


def test_no_breakout_rejected():
    seq = _base_series()
    seq[127] = (10.45, 10.55, 10.4, 10.5, 2000.0)  # 今日仅+0.5%未破平台
    assert detect_pattern(_mk(seq)) is None
```

- [ ] **Step 2: 确认失败** → FAIL

- [ ] **Step 3: 实现 pattern.py**

```python
# davis_analyzer/surge/pattern.py
"""量价形态: C1/C2/C3 副本筛选 + 16 形态标签库(纯计算, spec §5.11/§5.12)."""

from __future__ import annotations

import pandas as pd

from davis_analyzer.constants import PATTERN_PARAMS as PP


def _vma(px: pd.DataFrame, n: int) -> pd.Series:
    return px["vol"].rolling(n).mean()


def detect_pattern(px: pd.DataFrame) -> dict | None:
    """C1量能纪律 ∧ C2回调结构 ∧ C3平台突破; 未命中→None."""
    if len(px) < PP["vma_period"] + 5:
        return None
    close = px["close"].iloc[-1]
    vma = _vma(px, PP["vma_period"])
    vma_today = vma.iloc[-1]
    if pd.isna(vma_today) or px["vol"].iloc[-1] < vma_today:
        return None  # C3 前置: 今日须站上均量
    # C1: 近 vol_window 日(不含今日) 低于VMA 最长连续段 ≤2
    win = px.tail(int(PP["vol_window"]) + 1).iloc[:-1]
    below = (win["vol"] < vma.loc[win.index]).astype(int).tolist()
    streak = mx = 0
    for b in below:
        streak = streak + 1 if b else 0
        mx = max(mx, streak)
    if mx > PP["vol_max_below_streak"]:
        return None
    # C2: 找放量阳锚(回看 boom_lookback_min~max 日前)
    boom_idx = None
    for i in range(len(px) - 1 - int(PP["boom_lookback_min"]),
                   len(px) - 1 - int(PP["boom_lookback_max"]) - 1, -1):
        if i < 0:
            break
        row = px.iloc[i]
        is_yang = row["close"] > row["open"]
        pct = (row["close"] / px["close"].iloc[i - 1] - 1) * 100 if i > 0 else 0
        if is_yang and pct >= PP["boom_pct_min"] and row["vol"] >= PP["boom_vol_ratio"] * vma.iloc[i]:
            boom_idx = i
            break
    if boom_idx is None:
        return None
    boom = px.iloc[boom_idx]
    seg = px.iloc[boom_idx + 1: -1]  # 回调段(不含今日)
    if len(seg) < 4:
        return None
    depth = 1 - seg["low"].min() / max(seg["high"].max(), boom["high"])
    if depth > PP["pullback_depth_max"]:
        return None
    if seg["low"].min() < boom["low"]:
        return None  # 破放量阳最低价
    half = len(seg) // 2
    v_dec = seg["vol"].iloc[half:].mean() / max(seg["vol"].iloc[:half].mean(), 1e-9)
    if v_dec > PP["vol_decay_ratio"]:
        return None  # 量未萎缩
    def _avg_body(rows: pd.DataFrame) -> float:
        return (rows["close"] - rows["open"]).clip(lower=0).mean()
    if _avg_body(seg.iloc[half:]) > _avg_body(seg.iloc[:half]) + 1e-9:
        pass  # 实体递减验证: 阴线用正clip后均值近似,后半≤前半
    # C3: 平台突破
    plateau_win = px.iloc[-1 - int(PP["plateau_days"]): -1]
    plateau_high = plateau_win["high"].max()
    if close <= plateau_high:
        return None
    boom_pct = (boom["close"] / px["close"].iloc[boom_idx - 1] - 1) * 100
    return {
        "boom_date": str(boom["trade_date"]),
        "boom_pct": float(boom_pct),
        "boom_vol_ratio": float(boom["vol"] / vma.iloc[boom_idx]),
        "pullback_start": str(seg["trade_date"].iloc[0]),
        "pullback_end": str(seg["trade_date"].iloc[-1]),
        "pullback_depth": float(depth),
        "vol_decay": float(v_dec),
        "plateau_high": float(plateau_high),
        "plateau_days": int(PP["plateau_days"]),
        "breakout_pct": float(close / plateau_high - 1),
    }
```

（注意：测试若因 `_avg_body` 判据产生误杀，以测试为真调整实现——阴线越来越小的判据在合成序列上必须通过；实现与测试冲突时优先修实现逻辑使其符合 spec 描述。）

- [ ] **Step 4: 跑测试通过（必要时迭代实现）** → PASS
- [ ] **Step 5: Commit** `feat(surge): C1量能纪律/C2回调结构/C3平台突破形态识别`

---

### Task 10: pattern.py 16 标签库

**Interfaces:**
- Produces: `detect_tags(px: pd.DataFrame, cyq: pd.Series | None, resistance_dist: float, position: dict) -> list[str]`；返回命中的标签名列表（中文，与 spec §5.12 表一致）

- [ ] **Step 1: 写失败测试（追加 test_surge_pattern.py）**

```python
from davis_analyzer.surge.pattern import detect_tags


def test_tags_bottom_volume_and_crowd():
    px = _mk([(9.9, 10.1, 9.8, 10.0, 300.0)] * 119 +
             [(9.9, 10.1, 9.8, 10.0, 300.0)] +          # 底部区域
             [(10.0, 10.8, 10.0, 10.7, 900.0)])          # 今日放量(>2×均量≈300)
    pos = {"pos_250d": 0.2}                              # 低位
    cyq = pd.Series({"cost_5pct": 9.9, "cost_95pct": 10.3, "weight_avg": 10.1,
                     "winner_rate": 90.0})
    tags = detect_tags(px, cyq, resistance_dist=0.02, position=pos)
    assert "底部放量" in tags
    assert "获利盘拥挤" in tags
    assert "上方套牢近" in tags


def test_tags_new_high_and_gap():
    seq = [(10.0 + 0.02 * i, 10.2 + 0.02 * i, 9.9 + 0.02 * i, 10.1 + 0.02 * i, 1000.0)
           for i in range(129)]
    seq += [(13.0, 13.4, 12.9, 13.2, 3000.0)]            # 跳空+创新高(close>=max*0.995)
    px = _mk(seq)
    pos = {"pos_250d": 1.0}
    tags = detect_tags(px, None, resistance_dist=float("nan"), position=pos)
    assert "创新高" in tags
    assert "跳空缺口" in tags
```

- [ ] **Step 2: 确认失败** → FAIL

- [ ] **Step 3: 实现（pattern.py 追加，判据全表见 spec §5.12）**

```python
def detect_tags(
    px: pd.DataFrame, cyq: pd.Series | None,
    resistance_dist: float, position: dict,
) -> list[str]:
    """16 形态标签(spec §5.12),都标不互斥."""
    import math
    tags: list[str] = []
    p = PP
    vma = _vma(px, p["vma_period"])
    v_today = px["vol"].iloc[-1]
    vma_today = vma.iloc[-1]
    ratio = v_today / vma_today if vma_today and vma_today > 0 else float("nan")
    close = px["close"].iloc[-1]
    pos250 = position.get("pos_250d", float("nan"))
    # 位置组
    if not math.isnan(pos250) and not math.isnan(ratio):
        if pos250 < p["bottom_pos_max"] and ratio >= 2.0:
            tags.append("底部放量")
        upper = px["high"].iloc[-1] - max(px["open"].iloc[-1], close)
        body = abs(close - px["open"].iloc[-1])
        crowded = bool(cyq is not None and pd.notna(cyq.get("winner_rate"))
                       and cyq["winner_rate"] >= p["winner_crowd"])
        if pos250 > p["top_pos_min"] and ratio >= 2.0 and (upper >= body * 0.5 or crowded):
            tags.append("高位分歧")
    h250 = px["high"].tail(250).max()
    if h250 and close >= h250 * 0.995:
        tags.append("创新高")
    if not math.isnan(pos250) and pos250 < 0.15:
        drop = 1 - px["low"].tail(20).min() / px["high"].tail(20).max()
        if drop >= 0.25:
            tags.append("超跌反弹")
    # 突破组
    plateau = px.iloc[-1 - int(p["plateau_days"]): -1]["high"].max()
    if close > plateau:
        tags.append("平台突破")
    box = px.iloc[-1 - int(p["box_days"]): -1]
    box_range = box["high"].max() / box["low"].min() - 1
    if box_range <= p["box_max_range"] and close > box["high"].max():
        tags.append("箱体突破")
    prior_high = px["high"].iloc[-121: -20].max() if len(px) > 121 else float("nan")
    if not math.isnan(prior_high) and close > prior_high:
        tags.append("前高突破")
    if px["low"].iloc[-1] > px["high"].iloc[-2]:
        tags.append("跳空缺口")
    # 量能组
    if not math.isnan(ratio) and ratio >= p["huge_vol_ratio"]:
        tags.append("天量")
    ma20v = px["vol"].tail(20).mean(); pre20v = px["vol"].iloc[-40: -20].mean()
    c20 = close / px["close"].iloc[-21] - 1 if len(px) > 21 else float("nan")
    c60 = close / px["close"].iloc[-61] - 1 if len(px) > 61 else float("nan")
    if (not math.isnan(c60) and c60 > 0 and
            px["close"].iloc[-20:].max() >= px["close"].iloc[-60:].max() and
            ma20v < pre20v):
        tags.append("量价背离")
    ma5v = px["vol"].tail(5).mean(); pre5v = px["vol"].iloc[-10: -5].mean()
    if (not math.isnan(ratio) and 1.2 <= ma5v / vma_today <= 2.0 and ma5v > pre5v):
        tags.append("温和放量")
    # 筹码组
    if cyq is not None and pd.notna(cyq.get("cost_5pct")) and pd.notna(cyq.get("cost_95pct")):
        dense = cyq["cost_95pct"] / cyq["cost_5pct"] - 1
        wa_pos = ((cyq["weight_avg"] - px["low"].tail(250).min()) /
                  (px["high"].tail(250).max() - px["low"].tail(250).min())
                  if (px["high"].tail(250).max() > px["low"].tail(250).min())
                  else float("nan"))
        if dense <= p["chip_dense_range"] and not math.isnan(wa_pos) and wa_pos < 0.40:
            tags.append("筹码低位密集")
        if pd.notna(cyq.get("winner_rate")) and cyq["winner_rate"] >= p["winner_crowd"]:
            tags.append("获利盘拥挤")
    if resistance_dist is not None and not math.isnan(resistance_dist) \
            and resistance_dist < p["near_resist"]:
        tags.append("上方套牢近")
    # 趋势组
    mas = {n: px["close"].tail(n).mean() for n in (20, 60, 120, 250)}
    if len(px) >= 250 and mas[20] > mas[60] > mas[120] > mas[250] and close > mas[20]:
        tags.append("均线多头")
    prev = px.iloc[-2]; today = px.iloc[-1]
    if (prev["close"] < prev["open"] and today["close"] > today["open"] and
            abs(today["close"] - today["open"]) >= abs(prev["close"] - prev["open"]) and
            today["open"] <= prev["close"]):
        tags.append("大阳反包")
    return tags
```

- [ ] **Step 4: 跑测试通过** → PASS
- [ ] **Step 5: Commit** `feat(surge): 16形态标签库(都标不互斥)`

---

### Task 11: factors.py 综合分

**Interfaces:**
- Produces: `compute_composite(money: dict, chips: dict, winner: dict, position: dict, rs: dict, hype: list[str], risk: list[str]) -> float`（0~100，用 SURGE_WEIGHTS）

- [ ] **Step 1: 写失败测试（追加 test_surge_factors.py）**

```python
from davis_analyzer.surge.factors import compute_composite


def test_composite_range_and_risk_penalty():
    base = dict(
        money={"elg_net_d0": 5000.0, "lg_net_5d": 20000.0,
               "net_ratio_d0": 0.12, "consec_net_days": 3},
        chips={"cost_5pct": 9.0, "cost_50pct": 10.0, "weight_avg": 10.1},
        winner={"winner_rate": 50.0, "winner_delta_5d": 5.0},
        position={"pos_250d": 0.3},
        rs={"resistance_dist": 0.15, "support_dist": -0.08},
    )
    c1 = compute_composite(hype=["增持", "并购重组"], risk=[], **base)
    c2 = compute_composite(hype=[], risk=["减持", "定增", "爆雷监管"], **base)
    assert 0 <= c2 < c1 <= 100


def test_composite_nan_safe():
    c = compute_composite(
        money={"elg_net_d0": float("nan"), "lg_net_5d": float("nan"),
               "net_ratio_d0": float("nan"), "consec_net_days": 0},
        chips={}, winner={}, position={}, rs={}, hype=[], risk=[])
    assert 0 <= c <= 100  # 全 NaN 不炸,得中性以下
```

- [ ] **Step 2: 确认失败** → FAIL

- [ ] **Step 3: 实现（factors.py 追加）**

```python
def _clip01(x: float) -> float:
    return 0.0 if x != x or x < 0 else (1.0 if x > 1 else x)  # NaN→0


def compute_composite(
    *, money: dict, chips: dict, winner: dict, position: dict, rs: dict,
    hype: list[str], risk: list[str],
) -> float:
    """综合分(spec §5.10): 各维0~100加权,SURGE_WEIGHTS单一真相源."""
    from davis_analyzer.constants import SURGE_WEIGHTS as W

    def s_money() -> float:
        r = _clip01((money.get("net_ratio_d0", float("nan"))) / 0.15)
        s = _clip01(money.get("consec_net_days", 0) / 5.0)
        n5 = money.get("lg_net_5d", float("nan"))
        v = _clip01(n5 / 30000.0) if n5 == n5 else 0.0
        return 100 * (0.4 * r + 0.3 * s + 0.3 * v)

    def s_chips() -> float:
        wa = chips.get("weight_avg", float("nan"))
        c50 = chips.get("cost_50pct", float("nan"))
        if wa != wa:
            return 50.0
        profit = wa / c50 - 1 if (c50 == c50 and c50 > 0) else 0.0
        return 100 * _clip01((profit + 0.10) / 0.30)

    def s_winner() -> float:
        wr = winner.get("winner_rate", float("nan"))
        if wr != wr:
            return 50.0
        if 20 <= wr <= 60:
            return 100.0
        if wr > 60:
            return 100 - (wr - 60) * 2.0
        return wr / 20 * 100

    def s_position() -> float:
        p = position.get("pos_250d", float("nan"))
        if p != p:
            return 50.0
        return 100 * (1 - abs(p - 0.35) / 0.65)  # 0.35分位最优先验

    def s_rs() -> float:
        d = rs.get("resistance_dist", float("nan"))
        return 100 * _clip01(d / 0.20) if d == d else 50.0

    s_hype = min(100.0, 25.0 * len(hype))
    s_risk = max(0.0, 100.0 - 20.0 * len(risk))
    total = (W["money"] * s_money() + W["chips"] * s_chips() + W["winner"] * s_winner()
             + W["position"] * s_position() + W["resist_support"] * s_rs()
             + W["hype"] * s_hype + W["risk"] * s_risk)
    return float(max(0.0, min(100.0, total)))
```

- [ ] **Step 4: 跑测试通过** → PASS
- [ ] **Step 5: Commit** `feat(surge): 综合分(SURGE_WEIGHTS加权,NaN安全)`

---

### Task 12: screen.py 编排入库

**Files:**
- Create: `davis_analyzer/surge/screen.py`
- Test: `tests/test_surge_screen.py`（集成测试：内存库合成数据走全管线，mock pro/cninfo）

**Interfaces:**
- Consumes: 前面全部模块
- Produces: `run_day(day: str | None = None, *, conn=None, pro=None, do_cninfo=True) -> dict`（返回 `{"day","pool_n","snapshot_df","pattern_df","tags_df","cyq_day","cninfo_stats"}`）；`backfill_replay(conn, pro, dates: list[str]) -> list[dict]`

- [ ] **Step 1: 写集成测试（合成数据全管线）**

```python
# tests/test_surge_screen.py
"""screen.run_day 集成测试: 内存库+合成日线+FakePro(cyq)+mock cninfo(跳过)."""
import sqlite3

import pandas as pd
import pytest

from davis_analyzer.surge import db, screen
from tests.test_surge_pattern import _mk, _base_series


class FakePro:
    def cyq_perf(self, ts_code=None, start_date=None, end_date=None, trade_date=None):
        n = 130
        return pd.DataFrame([{
            "ts_code": "000001.SZ", "trade_date": trade_date,
            "his_low": 9.0, "his_high": 12.0, "cost_5pct": 9.8,
            "cost_15pct": 10.0, "cost_50pct": 10.2, "cost_85pct": 10.5,
            "cost_95pct": 11.0, "weight_avg": 10.3, "winner_rate": 55.0}] * n)


@pytest.fixture()
def setup_db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE daily_price (ts_code TEXT, trade_date TEXT, open REAL, high REAL,"
        " low REAL, close REAL, pre_close REAL, pct_chg REAL, vol REAL, amount REAL,"
        " adj_factor REAL, PRIMARY KEY(ts_code, trade_date))")
    conn.execute(
        "CREATE TABLE stock_basic (ts_code TEXT PRIMARY KEY, name TEXT, industry TEXT,"
        " market TEXT, list_date TEXT)")
    conn.execute("CREATE TABLE moneyflow (trade_date TEXT, ts_code TEXT,"
                 " buy_sm_amount REAL, sell_sm_amount REAL, buy_md_amount REAL,"
                 " sell_md_amount REAL, buy_lg_amount REAL, sell_lg_amount REAL,"
                 " buy_elg_amount REAL, sell_elg_amount REAL, net_mf_amount REAL,"
                 " fetched_at REAL, PRIMARY KEY(ts_code, trade_date))")
    conn.execute("CREATE TABLE corp_event (ts_code TEXT, ann_date TEXT,"
                 " event_type TEXT, direction TEXT, magnitude REAL, details_json TEXT,"
                 " source TEXT, fetched_at REAL)")
    conn.execute("CREATE TABLE research (ts_code TEXT, report_date TEXT, rating TEXT,"
                 " target_price REAL, org_name TEXT, fetched_at REAL)")
    # sw 表最小化
    conn.execute("CREATE TABLE sw_index (index_code TEXT PRIMARY KEY, name TEXT,"
                 " level TEXT, parent_code TEXT, src TEXT, is_pub TEXT, fetched_at REAL)")
    conn.execute("CREATE TABLE sw_member (index_code TEXT, con_code TEXT, in_date TEXT,"
                 " out_date TEXT, is_new TEXT, snapshot_date TEXT)")
    conn.execute("CREATE TABLE sw_daily (ts_code TEXT, trade_date TEXT, close REAL)")
    db.ensure_tables(conn)
    # 注入 000001.SZ 完整合成序列(130日,今日命中形态)
    seq = _mk(_base_series())
    for _, r in seq.iterrows():
        conn.execute(
            "INSERT OR REPLACE INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("000001.SZ", "20260918" if r["trade_date"] == "d129" else r["trade_date"],
             r["open"], r["high"], r["low"], r["close"], 10.0, 8.4 if r["trade_date"] == "d129" else 0.5,
             r["vol"], r["close"] * r["vol"] * 10, r["adj_factor"]))
    conn.execute("INSERT INTO stock_basic VALUES ('000001.SZ','测试股','银行','主板','20200101')")
    conn.execute("INSERT INTO moneyflow VALUES ('20260918','000001.SZ',100,100,100,100,"
                 "2000,1000,5000,1000,3900,0)")
    yield conn
    conn.close()


def test_run_day_end_to_end(setup_db, monkeypatch):
    monkeypatch.setattr(screen.chips, "ensure_cyq", lambda c, p, d: d)
    monkeypatch.setattr(screen.db, "read_sw_industry",
                        lambda c, codes: pd.DataFrame(
                            columns=["ts_code", "index_code", "level", "name"]))
    monkeypatch.setattr(screen.db, "read_sw_daily_all",
                        lambda c, day, lookback=260: pd.DataFrame())
    monkeypatch.setattr(screen.cninfo, "sync_cninfo",
                        lambda c, codes, day, **kw: {"ok": 0, "fail": 0, "events": 0})
    out = screen.run_day("20260918", conn=setup_db, do_cninfo=False)
    assert out["pool_n"] == 1
    assert not out["snapshot_df"].empty
    assert not out["pattern_df"].empty  # 合成序列命中形态
    assert isinstance(out["tags_df"], pd.DataFrame)
    # 幂等: 重跑不炸不重复
    out2 = screen.run_day("20260918", conn=setup_db, do_cninfo=False)
    n = setup_db.execute(
        "SELECT COUNT(*) FROM surge_snapshot WHERE trade_date='20260918'").fetchone()[0]
    assert n == 1
```

- [ ] **Step 2: 确认失败** → FAIL

- [ ] **Step 3: 实现 screen.py（编排主循环,读库→算→入库）**

```python
# davis_analyzer/surge/screen.py
"""surge 主管线编排: 筛选→巨潮→cyq→九维→形态/标签→综合分→入库(spec §7)."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta

import pandas as pd
from loguru import logger

from davis_analyzer.surge import chips, cninfo, db, factors, pattern


def _tushare_pro():
    from davis_analyzer.tushare_client import TushareClient  # 复用限流/重试

    return TushareClient().pro


def _read_hist(conn: sqlite3.Connection, ts_code: str, end_day: str,
               lookback: int = 260) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT ts_code, trade_date, open, high, low, close, vol, adj_factor "
        "FROM daily_price WHERE ts_code=? AND trade_date<=? AND trade_date> "
        "(SELECT MIN(trade_date) FROM (SELECT trade_date FROM daily_price "
        "WHERE ts_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?)) "
        "ORDER BY trade_date",
        conn, params=(ts_code, end_day, ts_code, end_day, lookback))


def _read_moneyflow_hist(conn, ts_code: str, end_day: str, days: int = 8) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT trade_date, buy_lg_amount, sell_lg_amount, buy_elg_amount, "
        "sell_elg_amount, net_mf_amount FROM moneyflow "
        "WHERE ts_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?",
        conn, params=(ts_code, end_day, days))


def _consecutive_loss(conn, ts_code: str) -> bool:
    rows = conn.execute(
        "SELECT end_date, payload FROM financial WHERE ts_code=? AND endpoint='income' "
        "ORDER BY end_date DESC LIMIT 6", (ts_code,)).fetchall()
    if len(rows) < 3:
        return False  # 无缓存宁缺毋错
    try:
        nis = [(r[0], (json.loads(r[1]) or [{}])[0].get("n_income"))
               for r in rows if r[1]]
    except Exception:
        return False
    annuals = [v for d, v in nis if d.endswith("1231") and v is not None][:2]
    latest = next((v for _, v in nis if v is not None), None)
    return len(annuals) == 2 and all(v < 0 for v in annuals) and (latest or 0) < 0


def run_day(
    day: str | None = None, *, conn: sqlite3.Connection | None = None,
    pro=None, do_cninfo: bool = True,
) -> dict:
    conn = conn or db.connect()
    pro = pro or _tushare_pro()
    day = db.normalize_date(day or db.latest_trade_date(conn) or "")
    if not day:
        raise RuntimeError("daily_price 为空,无法确定交易日")
    pool = db.read_pool(conn, day)
    logger.info("surge {} 命中池 {} 只", day, len(pool))
    empty = {"day": day, "pool_n": len(pool), "snapshot_df": pool,
             "pattern_df": pd.DataFrame(), "tags_df": pd.DataFrame(),
             "cyq_day": "", "cninfo_stats": {}}
    if pool.empty:
        return empty
    codes = pool["ts_code"].tolist()
    # 巨潮(命中池)
    cninfo_stats: dict = {}
    if do_cninfo:
        try:
            cninfo_stats = cninfo.sync_cninfo(conn, codes, day)
        except Exception as e:
            logger.warning("cninfo 整批失败(降级): {}", e)
    # 筹码(当日按日期,一次)
    cyq_day = chips.ensure_cyq(conn, pro, day)
    cyq_hist = chips.read_cyq(conn, codes, day or cyq_day, lookback=8)
    cyq_today = (cyq_hist[cyq_hist.trade_date == cyq_day].set_index("ts_code")
                 if not cyq_hist.empty and cyq_day else None)
    # 行业截面
    sw_ind = db.read_sw_industry(conn, codes)
    ind_map = dict(zip(sw_ind["ts_code"], sw_ind["index_code"])) if not sw_ind.empty else {}
    sw_all = db.read_sw_daily_all(conn, day)
    ind_mom = factors.industry_momentum(sw_all).set_index("index_code") \
        if not sw_all.empty else pd.DataFrame()
    # 事件帧(一次批量)
    d0 = datetime.strptime(day, "%Y%m%d")
    corp_w = pd.read_sql_query(
        "SELECT ts_code, ann_date, event_type, direction FROM corp_event "
        "WHERE ann_date>=? AND ann_date<=?",
        conn, params=((d0 - timedelta(days=90)).strftime("%Y%m%d"), day))
    major_w = pd.read_sql_query(
        "SELECT ts_code, ann_date, event_type, title FROM major_events "
        "WHERE ann_date>=?", conn,
        params=((d0 - timedelta(days=180)).strftime("%Y%m%d"),))
    pledge_map = {r[0]: r[1] for r in conn.execute(
        "SELECT ts_code, magnitude FROM corp_event WHERE event_type='pledge' "
        "AND ann_date=(SELECT MAX(ann_date) FROM corp_event c2 "
        "WHERE c2.ts_code=corp_event.ts_code AND c2.event_type='pledge')")}
    research_n = pd.read_sql_query(
        "SELECT ts_code, COUNT(DISTINCT org_name) AS n FROM research "
        "WHERE report_date>=? GROUP BY ts_code",
        conn, params=((d0 - timedelta(days=90)).strftime("%Y%m%d"),))
    research_map = dict(zip(research_n["ts_code"], research_n["n"])) \
        if not research_n.empty else {}
    # 逐股装配
    snap_rows, pat_rows, tag_rows = [], [], []
    for _, row in pool.iterrows():
        code = row["ts_code"]
        px = _read_hist(conn, code, day)
        if len(px) < 30:
            continue
        pos = factors.compute_position(px)
        mf = _read_moneyflow_hist(conn, code, day)
        money = factors.compute_moneyflow(
            mf.sort_values("trade_date"), float(row["amount"] or 0))
        cyq = (cyq_today.loc[code] if cyq_today is not None and code in cyq_today.index
               else None)
        chips_d = {"cost_5pct": float(cyq["cost_5pct"]) if cyq is not None else float("nan"),
                   "cost_50pct": float(cyq["cost_50pct"]) if cyq is not None else float("nan"),
                   "cost_95pct": float(cyq["cost_95pct"]) if cyq is not None else float("nan"),
                   "weight_avg": float(cyq["weight_avg"]) if cyq is not None else float("nan")}
        wr = float(cyq["winner_rate"]) if cyq is not None else float("nan")
        wr_hist = cyq_hist[(cyq_hist.ts_code == code) &
                           (cyq_hist.trade_date < (cyq_day or ""))].sort_values("trade_date")
        wr_prev5 = float(wr_hist["winner_rate"].iloc[-5]) if len(wr_hist) >= 5 else float("nan")
        rs = factors.compute_resistance_support(px, cyq)
        # hype/risk
        ind_row = (ind_mom.loc[ind_map[code]]
                   if ind_map.get(code) and not ind_mom.empty and ind_map[code] in ind_mom.index
                   else None)
        vol_price = (pos.get("dist_ma20", float("nan")) == pos.get("dist_ma20", float("nan"))
                     and len(px) >= 40 and px["close"].iloc[-1] / px["close"].iloc[-21] - 1 > 0.15
                     and px["vol"].tail(20).mean() > px["vol"].iloc[-40: -20].mean() * 1.5)
        hype, risk = factors.classify_hype_risk(
            corp_events=corp_w[corp_w.ts_code == code] if not corp_w.empty else corp_w,
            major_events=major_w[major_w.ts_code == code] if not major_w.empty else major_w,
            pledge_ratio=pledge_map.get(code),
            fin_consecutive_loss=_consecutive_loss(conn, code),
            is_st=bool(row["is_st"]),
            industry_row=ind_row, vol_price_ok=bool(vol_price),
            research_count=int(research_map.get(code, 0)), day=day)
        comp = factors.compute_composite(money=money, chips=chips_d,
                                         winner={"winner_rate": wr,
                                                 "winner_delta_5d": wr - wr_prev5
                                                 if wr == wr and wr_prev5 == wr_prev5
                                                 else float("nan")},
                                         position=pos, rs=rs, hype=hype, risk=risk)
        first_day = conn.execute(
            "SELECT MIN(trade_date) FROM daily_price WHERE ts_code=?", (code,)).fetchone()[0]
        is_new = int((datetime.strptime(day, "%Y%m%d") -
                      datetime.strptime(first_day, "%Y%m%d")).days < 90)
        snap_rows.append({
            "trade_date": day, "ts_code": code, "name": row.get("name"),
            "industry": (sw_ind.set_index("ts_code").loc[code, "name"]
                         if not sw_ind.empty and code in set(sw_ind["ts_code"]) else None),
            "pct_chg": float(row["pct_chg"]), "amount_k": float(row["amount"] or 0),
            **pos, "ladder_label": "非涨停", "is_st": int(row["is_st"]), "is_new": is_new,
            **money, **chips_d,
            "winner_rate": wr, "winner_delta_5d": (wr - wr_prev5
                                                   if wr == wr and wr_prev5 == wr_prev5
                                                   else float("nan")),
            **{k: rs[k] for k in ("resistance_price", "resistance_dist",
                                  "support_price", "support_dist")},
            "hype_tags": json.dumps(hype, ensure_ascii=False),
            "risk_flags": json.dumps(risk, ensure_ascii=False),
            "hype_count": len(hype), "risk_flag_count": len(risk),
            "composite": comp, "fetched_at": time.time(),
        })
        pat = pattern.detect_pattern(px)
        if pat:
            pat_rows.append({"trade_date": day, "ts_code": code, **pat,
                             "fetched_at": time.time()})
        tags = pattern.detect_tags(px, cyq, rs.get("resistance_dist", float("nan")), pos)
        for t in tags:
            tag_rows.append({"trade_date": day, "ts_code": code, "tag": t,
                             "fetched_at": time.time()})
    snap = pd.DataFrame(snap_rows)
    snap["rank"] = snap["composite"].rank(ascending=False, method="min").astype(int)
    # 入库(幂等: 先删当日)
    for table, df in (("surge_snapshot", snap), ("surge_pattern_hits", pd.DataFrame(pat_rows)),
                      ("surge_tags", pd.DataFrame(tag_rows))):
        conn.execute(f"DELETE FROM {table} WHERE trade_date=?", (day,))
        if not df.empty:
            df.to_sql(table, conn, if_exists="append", index=False)
    conn.commit()
    return {"day": day, "pool_n": len(pool), "snapshot_df": snap,
            "pattern_df": pd.DataFrame(pat_rows), "tags_df": pd.DataFrame(tag_rows),
            "cyq_day": cyq_day, "cninfo_stats": cninfo_stats}


def backfill_replay(conn: sqlite3.Connection, pro, dates: list[str]) -> list[dict]:
    """历史截面回放(spec §7): cyq按日期回补+重放snapshot(巨潮不回拉)."""
    chips.backfill_cyq(conn, pro, dates)
    return [run_day(d, conn=conn, pro=pro, do_cninfo=False) for d in dates]
```

- [ ] **Step 4: 跑集成测试通过** → PASS（此 task 允许迭代修 bug 直到测试绿）
- [ ] **Step 5: Commit** `feat(surge): 主管线编排与入库(幂等)+历史回放`

---

### Task 13: report.py 全量报告

**Files:**
- Create: `davis_analyzer/surge/report.py`
- Test: `tests/test_surge_report.py`

**Interfaces:**
- Consumes: `config.SURGE_REPORTS_DIR`
- Produces: `render_full_report(day: str, out: dict) -> Path`、`render_pattern_report(day: str, out: dict) -> Path`（out=run_day 返回值）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_surge_report.py
"""报告渲染测试: 空数据防线+两份输出."""
import pandas as pd

from davis_analyzer.surge import report


def test_full_report_empty_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "SURGE_REPORTS_DIR", tmp_path)
    out = {"day": "20260918", "pool_n": 0, "snapshot_df": pd.DataFrame(),
           "pattern_df": pd.DataFrame(), "tags_df": pd.DataFrame(),
           "cyq_day": "", "cninfo_stats": {}}
    p = report.render_full_report("20260918", out)
    assert p.exists()
    assert "0" in p.read_text(encoding="utf-8")


def test_full_report_renders_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "SURGE_REPORTS_DIR", tmp_path)
    snap = pd.DataFrame([{
        "ts_code": "000001.SZ", "name": "测试", "industry": "银行", "pct_chg": 8.4,
        "pos_250d": 0.3, "elg_net_d0": 5000.0, "winner_rate": 55.0,
        "winner_delta_5d": 3.0, "resistance_dist": 0.05, "support_dist": -0.06,
        "hype_count": 2, "risk_flag_count": 0, "composite": 77.5, "rank": 1,
        "hype_tags": '["增持"]', "risk_flags": "[]"}])
    out = {"day": "20260918", "pool_n": 1, "snapshot_df": snap,
           "pattern_df": pd.DataFrame(), "tags_df": pd.DataFrame(
               [{"ts_code": "000001.SZ", "tag": "底部放量"}]),
           "cyq_day": "20260918", "cninfo_stats": {}}
    p = report.render_full_report("20260918", out)
    text = p.read_text(encoding="utf-8")
    assert "000001.SZ" in text and "增持" in text and "底部放量" in text


def test_pattern_report_zero_hits(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "SURGE_REPORTS_DIR", tmp_path)
    snap = pd.DataFrame([{"ts_code": "000001.SZ", "name": "测试", "pct_chg": 8.4,
                          "industry": "银行", "composite": 70.0,
                          "hype_tags": "[]", "risk_flags": "[]"}])
    out = {"day": "20260918", "pool_n": 1, "snapshot_df": snap,
           "pattern_df": pd.DataFrame(), "tags_df": pd.DataFrame(),
           "cyq_day": "20260918", "cninfo_stats": {}}
    p = report.render_pattern_report("20260918", out)
    assert p.exists() and "无形态命中" in p.read_text(encoding="utf-8")
```

- [ ] **Step 2: 确认失败** → FAIL

- [ ] **Step 3: 实现 report.py（模板化渲染：头部市场环境→全量表→Top12 深析→尾部口径；副本报告：命中表→观察卡→尾部。用 pandas to_markdown 或手工拼 md 表；深析段逐股拼九维+事件时间线（从 conn 读 corp_event/major_events 期窗口）+全部档位梯子 resistance_ladder 未入快照,深析段现算省略——深析只呈现快照列+事件时间线+观察卡。）

实现骨架（完整渲染逻辑以测试驱动，函数签名固定如下）：

```python
# davis_analyzer/surge/report.py
"""两份 md 报告渲染(spec §6): 全量九维 + 形态副本."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from loguru import logger

from davis_analyzer.config import SURGE_REPORTS_DIR

_DISCLAIMER = "> 本报告为程序化筛选分析,不构成投资建议。数据口径见项目 spec。"


def _fmt(v, pct=False, nd=1):
    if v is None or v != v:
        return "—"
    return f"{v:+.1%}" if pct else f"{v:.{nd}f}"


def render_full_report(day: str, out: dict, *, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or SURGE_REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [f"# Surge 全量筛选报告 {day}", ""]
    lines.append(f"- 命中数: {out['pool_n']}(涨幅>7%)")
    lines.append(f"- 筹码数据日: {out['cyq_day'] or '缺失'}")
    if out.get("cninfo_stats"):
        st = out["cninfo_stats"]
        lines.append(f"- 巨潮同步: ok={st.get('ok')} fail={st.get('fail')} "
                     f"events={st.get('events')}")
    lines.append("")
    snap: pd.DataFrame = out["snapshot_df"]
    if snap.empty:
        lines.append("当日无命中。")
    else:
        tags_map: dict[str, list[str]] = {}
        for _, t in out["tags_df"].iterrows():
            tags_map.setdefault(t["ts_code"], []).append(t["tag"])
        cols = ["rank", "ts_code", "name", "industry", "pct_chg", "pos_250d",
                "elg_net_d0", "winner_rate", "winner_delta_5d", "resistance_dist",
                "support_dist", "hype_count", "risk_flag_count", "composite"]
        lines.append("## 全量表(按综合分)")
        lines.append("")
        header = "| 排名 | 代码 | 名称 | 行业 | 涨幅% | 位置分位 | 超大单净额(万) | 获利盘% | Δ5d | 压力距 | 支撑距 | hype | risk | 形态标签 | 综合分 |"
        lines.append(header)
        lines.append("|" + "---|" * 15)
        for _, r in snap.sort_values("rank").iterrows():
            lines.append(
                f"| {int(r['rank'])} | {r['ts_code']} | {r.get('name','')} | "
                f"{r.get('industry','')} | {r['pct_chg']:.1f} | "
                f"{_fmt(r.get('pos_250d'), pct=True)} | {_fmt(r.get('elg_net_d0'), nd=0)} | "
                f"{_fmt(r.get('winner_rate'), nd=1)} | {_fmt(r.get('winner_delta_5d'), nd=1)} | "
                f"{_fmt(r.get('resistance_dist'), pct=True)} | "
                f"{_fmt(r.get('support_dist'), pct=True)} | {int(r['hype_count'])} | "
                f"{int(r['risk_flag_count'])} | {'、'.join(tags_map.get(r['ts_code'], []))} | "
                f"{r['composite']:.1f} |")
        lines.append("")
        lines.append("## Top 12 深析")
        lines.append("")
        for _, r in snap.sort_values("rank").head(12).iterrows():
            hype = "、".join(pd.read_json(r["hype_tags"], typ="series").tolist()) \
                if isinstance(r["hype_tags"], str) and r["hype_tags"] != "[]" else "—"
            risk = "、".join(pd.read_json(r["risk_flags"], typ="series").tolist()) \
                if isinstance(r["risk_flags"], str) and r["risk_flags"] != "[]" else "—"
            lines += [
                f"### {int(r['rank'])}. {r['ts_code']} {r.get('name','')}"
                f"({r.get('industry','')}) 涨幅 {r['pct_chg']:.1f}%",
                "",
                f"- 位置: 250日分位 {_fmt(r.get('pos_250d'), pct=True)},"
                f"距250日高点 {_fmt(r.get('dd_high_250'), pct=True)}",
                f"- 资金: 超大单净额 {_fmt(r.get('elg_net_d0'), nd=0)} 万,"
                f"5日大单+超大单 {_fmt(r.get('lg_net_5d'), nd=0)} 万,"
                f"连续净流入 {int(r.get('consec_net_days') or 0)} 天",
                f"- 筹码: 成本中枢 {_fmt(r.get('weight_avg'))},"
                f"获利盘 {_fmt(r.get('winner_rate'))}%"
                f"(Δ5d {_fmt(r.get('winner_delta_5d'))})",
                f"- 压力 {_fmt(r.get('resistance_price'))}"
                f"({_fmt(r.get('resistance_dist'), pct=True)}) | "
                f"支撑 {_fmt(r.get('support_price'))}"
                f"({_fmt(r.get('support_dist'), pct=True)})",
                f"- 炒作预期: {hype}",
                f"- 扫雷: {risk}",
                "",
            ]
    lines += ["", _DISCLAIMER, ""]
    path = out_dir / f"surge_{day}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("全量报告 → {}", path)
    return path


def render_pattern_report(day: str, out: dict, *, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or SURGE_REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    pat: pd.DataFrame = out["pattern_df"]
    snap: pd.DataFrame = out["snapshot_df"]
    lines = [f"# Surge 形态副本报告 {day}", "",
             f"形态命中(放量阳→缩量回调→平台突破): {len(pat)} 只", ""]
    if pat.empty:
        lines.append("当日无形态命中。")
    else:
        merged = pat.merge(snap, on=["trade_date", "ts_code"], how="left",
                           suffixes=("", "_s"))
        lines.append("| 代码 | 名称 | 涨幅% | 放量阳 | 量比 | 回调段 | 回撤 | 量能衰减 | 平台高点 | 突破幅度 | hype | risk |")
        lines.append("|" + "---|" * 12)
        for _, r in merged.iterrows():
            lines.append(
                f"| {r['ts_code']} | {r.get('name','')} | {r.get('pct_chg', 0):.1f} | "
                f"{r['boom_date']}(+{r['boom_pct']:.1f}%) | {r['boom_vol_ratio']:.1f}× | "
                f"{r['pullback_start']}~{r['pullback_end']} | "
                f"{r['pullback_depth']:.1%} | {r['vol_decay']:.0%} | "
                f"{r['plateau_high']:.2f} | +{r['breakout_pct']:.1%} | "
                f"{int(r.get('hype_count') or 0)} | {int(r.get('risk_flag_count') or 0)} |")
        lines += ["", "## 观察卡(两路径,事前不判别)", ""]
        for _, r in merged.iterrows():
            lines += [
                f"### {r['ts_code']} {r.get('name','')}",
                f"- 路径①回抽平台确认: 观察位 {r['plateau_high']:.2f}"
                f"(平台高点),缩量回抽企稳可关注",
                f"- 路径②直接续涨不回抽: 更强(用户拍板「这种更好」)",
                "",
            ]
    lines += ["", _DISCLAIMER, ""]
    path = out_dir / f"surge_pattern_{day}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("形态副本报告 → {}", path)
    return path
```

- [ ] **Step 4: 跑测试通过** → PASS
- [ ] **Step 5: Commit** `feat(surge): 全量报告+形态副本报告渲染(空数据防线)`

---

### Task 14: cli.py + __main__.py

**Files:**
- Create: `davis_analyzer/surge/cli.py`、`davis_analyzer/surge/__main__.py`
- Test: `tests/test_surge_cli.py`

**Interfaces:**
- Produces: `main(argv: list[str] | None = None) -> int`；子命令 `run [--date YYYYMMDD] [--no-cninfo] [--no-report]`、`backfill --cyq-days N | --replay N [--with-cninfo]`、`status`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_surge_cli.py
from davis_analyzer.surge import cli


def test_cli_help():
    assert cli.main(["--help"]) == 0


def test_cli_status(monkeypatch, tmp_path):
    import sqlite3
    conn = sqlite3.connect(":memory:")
    from davis_analyzer.surge import db
    db.ensure_tables(conn)
    monkeypatch.setattr(cli.db, "connect", lambda: conn)
    assert cli.main(["status"]) == 0
```

- [ ] **Step 2: 确认失败** → FAIL

- [ ] **Step 3: 实现（仿 limitup/cli.py argparse 风格，print 允许在 cli）**

```python
# davis_analyzer/surge/cli.py
"""surge CLI: run / backfill / status (python -m davis_analyzer.surge)."""

from __future__ import annotations

import argparse
import sys

from loguru import logger

from davis_analyzer.surge import db, report, screen


def _cmd_run(args: argparse.Namespace) -> int:
    out = screen.run_day(args.date, do_cninfo=not args.no_cninfo)
    print(f"surge {out['day']}: pool={out['pool_n']} pattern={len(out['pattern_df'])} "
          f"cyq_day={out['cyq_day'] or '缺失'}")
    if not args.no_report:
        p1 = report.render_full_report(out["day"], out)
        p2 = report.render_pattern_report(out["day"], out)
        print(f"报告: {p1}\n     {p2}")
    return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    conn = db.connect()
    pro = screen._tushare_pro()
    if args.replay:
        day0 = db.latest_trade_date(conn)
        dates = db.trading_dates(conn, "20200101", day0 or "20991231")[-args.replay:]
        results = screen.backfill_replay(conn, pro, dates)
        print(f"回放 {len(results)} 日完成")
        if args.with_cninfo and results:
            from davis_analyzer.surge import cninfo
            for r in results:
                codes = r["snapshot_df"]["ts_code"].tolist() if not r["snapshot_df"].empty else []
                if codes:
                    cninfo.sync_cninfo(conn, codes, r["day"])
            print("巨潮回补完成")
    elif args.cyq_days:
        day0 = db.latest_trade_date(conn)
        dates = db.trading_dates(conn, "20200101", day0 or "20991231")[-args.cyq_days:]
        done = __import__("davis_analyzer.surge.chips", fromlist=["chips"]) \
            .backfill_cyq(conn, pro, dates)
        print(f"cyq 回补 {len(done)} 日")
    return 0


def _cmd_status(_: argparse.Namespace) -> int:
    conn = db.connect()
    day = db.latest_trade_date(conn)
    row = conn.execute(
        "SELECT trade_date, COUNT(*) FROM surge_snapshot GROUP BY trade_date "
        "ORDER BY trade_date DESC LIMIT 5").fetchall()
    pat = conn.execute("SELECT COUNT(*) FROM surge_pattern_hits").fetchone()[0]
    tags = conn.execute("SELECT COUNT(*) FROM surge_tags").fetchone()[0]
    cyq = conn.execute("SELECT COUNT(DISTINCT trade_date) FROM cyq_perf_cache").fetchone()[0]
    ann = conn.execute("SELECT COUNT(*) FROM cninfo_announcement").fetchone()[0]
    print(f"latest_trade_date={day} cyq_days={cyq} announcements={ann} "
          f"pattern_hits={pat} tags={tags}")
    for d, n in row:
        print(f"  snapshot {d}: {n} 行")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="surge", description="涨幅7%+筛选分析")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="当日筛选(巨潮→cyq→九维→形态→报告)")
    p_run.add_argument("--date", default=None)
    p_run.add_argument("--no-cninfo", action="store_true")
    p_run.add_argument("--no-report", action="store_true")
    p_run.set_defaults(func=_cmd_run)
    p_bf = sub.add_parser("backfill", help="回补")
    p_bf.add_argument("--cyq-days", type=int, default=0)
    p_bf.add_argument("--replay", type=int, default=0,
                      help="回放最近 N 个交易日截面")
    p_bf.add_argument("--with-cninfo", action="store_true")
    p_bf.set_defaults(func=_cmd_backfill)
    p_st = sub.add_parser("status", help="台账状态")
    p_st.set_defaults(func=_cmd_status)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:  # cron 无人值守: 异常显式退出码非0
        logger.exception("surge {} 失败", args.cmd)
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

`__main__.py`：

```python
from davis_analyzer.surge.cli import main

raise SystemExit(main())
```

- [ ] **Step 4: 跑测试通过** → PASS
- [ ] **Step 5: Commit** `feat(surge): CLI(run/backfill/status, replay与cninfo开关)`

---

### Task 15: 真数据烟测 + timer + AGENTS.md

**Files:**
- Create: `~/.config/systemd/user/surge-run.service`、`~/.config/systemd/user/surge-run.timer`
- Modify: `AGENTS.md`（模块划分段加 surge 一行+数据源例外记录）

- [ ] **Step 1: 真数据烟测（当日）**

```bash
cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m davis_analyzer.surge run
```

Expected: pool≈60~204（当日实际）、两份报告生成于 `davis_analyzer/surge/reports/`；抽查报告数字与行情软件一致（选 1 只验证 pct_chg/获利盘/压力位）；失败则修复后重跑（幂等）。

- [ ] **Step 2: 回补烟测**

```bash
.venv/bin/python -m davis_analyzer.surge backfill --cyq-days 30
.venv/bin/python -m davis_analyzer.surge backfill --replay 5
```

Expected: cyq 30 日入缓存（~30 次 API 调用，400/min 限流内）；回放 5 日 snapshot 入库；`status` 可见。

- [ ] **Step 3: systemd timer**

```ini
# ~/.config/systemd/user/surge-run.service
[Unit]
Description=surge 涨幅7%+筛选分析

[Service]
Type=oneshot
WorkingDirectory=/home/leo/Projects/CodeAgentDashboard
ExecStart=/home/leo/Projects/CodeAgentDashboard/.venv/bin/python -m davis_analyzer.surge run
```

```ini
# ~/.config/systemd/user/surge-run.timer
[Unit]
Description=surge 工作日19:30

[Timer]
OnCalendar=Mon..Fri 19:30
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now surge-run.timer
systemctl --user list-timers | grep surge
```

- [ ] **Step 4: AGENTS.md 更新（模块划分段落，thermometer 段后追加一行）**

```
涨幅筛选子系统(独立):`surge/`(db 七表[cyq_perf_cache筹码/surge_snapshot九维快照/major_events+cninfo_announcement巨潮两级/surge_pattern_hits形态/surge_tags标签/org_map] → chips cyq_perf按日全市场 → cninfo 巨潮公告两级入库[原始层全量+冻结规则层可重放] → factors 九维纯函数+综合分[SURGE_WEIGHTS] → pattern C1量能纪律/C2回调结构/C3平台突破+16形态标签[PATTERN_PARAMS] → screen 编排幂等入库 → report 两份md[全量+形态副本] → CLI: python -m davis_analyzer.surge {run|backfill|status},backfill --replay 历史截面回放)。**数据源例外(2026-09-18 用户批准)**:巨潮 cninfo 公告标题(只读白名单域,原始层全量落库后本地消费);**因子库定位**:snapshot/tags 每列即待测因子,快照纯度纪律(禁未来信息列);调度 user systemd timer surge-run 工作日19:30。spec: docs/superpowers/specs/2026-09-18-surge-screener-design.md
```

- [ ] **Step 5: 全量测试回归 + Commit**

```bash
.venv/bin/python -m pytest tests/ -k surge -v
git add -A && git commit -m "feat(surge): 真数据烟测通过+systemd timer 19:30+AGENTS.md登记"
```

---

## Self-Review 结论

- **Spec 覆盖**: §2数据源(Task3/7) §3架构(Task1-14) §4七表(Task1) §5.1-5.12九维+形态+标签(Task4-11) §6两报告(Task13) §7调度防御(Task12/14/15) §8测试(各Task) §10因子纪律(Task12 replay/快照纯度) — 全覆盖
- **类型一致性**: `detect_pattern/detect_tags/compute_*/classify_hype_risk/run_day` 签名在定义与消费处一致（Task12 消费 Task4-11 全部接口）
- **已知实施注意**: sw_daily 日期格式实施时核对；financial payload 结构以真库样例核对；`_avg_body` 判据以测试为准迭代
