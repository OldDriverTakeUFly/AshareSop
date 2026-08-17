# 连板打板研究模块（limitup）实施计划 — Phase 0–2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 davis_analyzer 中新建 `limitup` 子包，完成涨停历史数据回补、事件+形态+环境研究、事件驱动打板回测（Phase 0–2）。

**Architecture:** 独立子包 + 模块级 CLI（仿 `paper_trading`），事件驱动回测引擎新写，复用 `backtest.py` 的费用函数与 `PerformanceStats`。数据落在共享库 `market_data.db`（回补写 `limit_pool` 现有 12 列 + 自建 `limit_pool_ext` 扩展表存流通市值，不改 stockhot 源码）。

**Tech Stack:** Python 3.11+ / pandas / numpy / sqlite3 / loguru / pytest。规格见 `docs/superpowers/specs/2026-08-16-limitup-module-design.md`。

## Global Constraints（每个任务隐含遵守）

- 从父仓库根目录 `/home/leo/Projects/CodeAgentDashboard/` 运行一切命令；Python 用 `.venv/bin/python`；测试统一 `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_<x>.py -v`。
- 每个 py 文件顶部 `from __future__ import annotations`；类型注解完整（PEP 604 联合类型）；`snake_case` 函数 / `PascalCase` 类；docstring 英文、金融术语中文；模块分隔注释 `# ── ... ──`。
- 日志只用 `from loguru import logger` + 花括号占位；`print` 仅允许出现在 `limitup/cli.py`。
- 价格/金额计算沿用 `backtest.py`/`paper_trading` 既有 float 惯例（pandas 生态一致；本模块不引入 Decimal）。
- 不修改 `davis_analyzer/constants.py` 权重、不修改 `stockhot/` 任何源码（`limit_pool_ext` 由本模块自行 `CREATE TABLE IF NOT EXISTS`）。
- 日期约定：入库 `limit_pool` 用 `YYYY-MM-DD`（与现存一致）；本模块 DataFrame 内部统一 `YYYYMMDD`（`db.normalize_date` 负责转换）；`limit_pool.ts_code` 无交易所后缀（如 `603311`），其余表带后缀（`603311.SH`），`db.to_suffixed_code` 负责归一。
- 提交信息：Conventional Commits 中文 scope，如 `feat(limitup): 实现涨停池历史回补`。
- conftest 在 `davis_analyzer/tests/conftest.py`（根目录 `tests/` 是 JS 测试，别碰）。
- Phase 3（candidates/paper_trading 集成）不在本计划内，Phase 2 结论为正期望后另出计划。

---

### Task 1: 包骨架 + 配置 + 测试夹具

**Files:**
- Create: `davis_analyzer/limitup/__init__.py`
- Modify: `davis_analyzer/config.py`（追加 2 行路径常量）
- Modify: `davis_analyzer/tests/conftest.py`（追加 `limitup_db` fixture）
- Test: `davis_analyzer/tests/test_limitup_package.py`

**Interfaces:**
- Produces: `config.LIMITUP_REPORTS_DIR: Path`（后续 report.py 用）；`limitup_db` fixture → `sqlite3.Connection`（:memory:，已建 limitup 需要的全部表，空行，Task 2+ 测试插数据用）。

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_limitup_package.py`：

```python
"""limitup 包骨架与配置测试。"""

from __future__ import annotations

import sqlite3

from davis_analyzer import config


def test_package_importable() -> None:
    import davis_analyzer.limitup  # noqa: F401


def test_reports_dir_created() -> None:
    assert config.LIMITUP_REPORTS_DIR.exists()
    assert config.LIMITUP_REPORTS_DIR.is_dir()


def test_limitup_db_fixture_has_tables(limitup_db: sqlite3.Connection) -> None:
    rows = limitup_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"limit_pool", "daily_price", "index_daily", "top_list",
            "intraday_feature", "stock_basic", "corp_event"} <= names
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_package.py -v`
Expected: FAIL（`davis_analyzer.limitup` 不存在 / `LIMITUP_REPORTS_DIR` 属性错误 / fixture 缺失）

- [ ] **Step 3: 最小实现**

`davis_analyzer/limitup/__init__.py`：

```python
"""连板打板/抓涨停启动研究模块（Phase 0-2：数据回补/事件研究/事件驱动回测）."""

from __future__ import annotations
```

`davis_analyzer/config.py` 在 `STUDIES_DIR.mkdir(...)` 行之后追加：

```python
LIMITUP_REPORTS_DIR = PROJECT_ROOT / "davis_analyzer" / "limitup" / "reports"

LIMITUP_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
```

`davis_analyzer/tests/conftest.py` 文件末尾追加（与现有 fixture 并列；建表列名与 `stockhot/data_layer/market_db.py` 保持一致）：

```python
@pytest.fixture
def limitup_db() -> Iterator[sqlite3.Connection]:
    """In-memory DB with empty limitup-relevant tables (schema mirrors market_db)."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE limit_pool (
            trade_date TEXT NOT NULL, ts_code TEXT NOT NULL, pool_kind TEXT NOT NULL,
            name TEXT, sector TEXT, change_pct REAL, seal_amount REAL,
            consecutive_boards INTEGER, broken_count INTEGER, first_seal_time TEXT,
            last_seal_time TEXT, turnover_rate REAL, fetched_at REAL,
            PRIMARY KEY (trade_date, ts_code, pool_kind));
        CREATE TABLE limit_pool_ext (
            trade_date TEXT NOT NULL, ts_code TEXT NOT NULL, pool_kind TEXT NOT NULL,
            float_mv REAL, PRIMARY KEY (trade_date, ts_code, pool_kind));
        CREATE TABLE daily_price (
            ts_code TEXT NOT NULL, trade_date TEXT NOT NULL, open REAL, high REAL,
            low REAL, close REAL NOT NULL, pre_close REAL, pct_chg REAL, vol REAL,
            amount REAL, adj_factor REAL, fetched_at REAL,
            PRIMARY KEY (ts_code, trade_date));
        CREATE TABLE index_daily (
            ts_code TEXT NOT NULL, trade_date TEXT NOT NULL, open REAL, high REAL,
            low REAL, close REAL NOT NULL, vol REAL, amount REAL, pct_chg REAL,
            fetched_at REAL, PRIMARY KEY (ts_code, trade_date));
        CREATE TABLE top_list (
            trade_date TEXT NOT NULL, ts_code TEXT NOT NULL, name TEXT, close REAL,
            pct_change REAL, turnover_rate REAL, amount REAL, l_sell REAL, l_buy REAL,
            l_amount REAL, net_amount REAL, net_rate REAL, amount_rate REAL,
            float_values REAL, reason TEXT, fetched_at REAL,
            PRIMARY KEY (ts_code, trade_date));
        CREATE TABLE intraday_feature (
            ts_code TEXT NOT NULL, trade_date TEXT NOT NULL, gap REAL, amplitude REAL,
            close_position REAL, upper_shadow REAL, lower_shadow REAL, body_ratio REAL,
            fetched_at REAL, PRIMARY KEY (ts_code, trade_date));
        CREATE TABLE stock_basic (
            ts_code TEXT PRIMARY KEY, name TEXT, industry TEXT, list_status TEXT,
            fetched_at REAL, list_date TEXT);
        CREATE TABLE corp_event (
            ts_code TEXT NOT NULL, ann_date TEXT NOT NULL, event_type TEXT NOT NULL,
            direction TEXT, magnitude REAL, details_json TEXT, source TEXT,
            fetched_at REAL, PRIMARY KEY (ts_code, ann_date, event_type, details_json));
        """
    )
    yield conn
    conn.close()
```

注意 conftest.py 顶部若尚无 `Iterator` import 则补 `from collections.abc import Iterator`（先读文件确认）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_package.py -v`
Expected: PASS 3 项

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/limitup/__init__.py davis_analyzer/config.py davis_analyzer/tests/conftest.py davis_analyzer/tests/test_limitup_package.py
git commit -m "feat(limitup): 子包骨架+报告目录配置+limitup_db 测试夹具"
```

---

### Task 2: 共享数据读取层 db.py

**Files:**
- Create: `davis_analyzer/limitup/db.py`
- Test: `davis_analyzer/tests/test_limitup_db.py`

**Interfaces:**
- Consumes: `limitup_db` fixture（Task 1）。
- Produces（后续任务依赖的精确签名）：
  - `connect() -> sqlite3.Connection`
  - `normalize_date(d: str) -> str`（`2026-05-12`→`20260512`）
  - `to_dash_date(d: str) -> str`（`20260512`→`2026-05-12`）
  - `to_suffixed_code(code: str) -> str`（`603311`→`603311.SH`；已带后缀原样返回）
  - `strip_code_suffix(code: str) -> str`
  - `trading_dates(conn, start: str, end: str) -> list[str]`
  - `read_limit_pool(conn, start: str, end: str, pool_kind: str = "limit_up") -> pd.DataFrame`（trade_date 已归一 YYYYMMDD、ts_code 已补后缀）
  - `read_limit_pool_ext(conn, start: str, end: str) -> pd.DataFrame`
  - `read_daily_prices(conn, ts_codes: list[str], start: str, end: str) -> pd.DataFrame`（列：ts_code, trade_date, open, high, low, close, pre_close, vol, amount, adj_factor；codes 分块 ≤900 防 SQLITE 变量上限）
  - `read_intraday_features(conn, ts_codes: list[str], start: str, end: str) -> pd.DataFrame`
  - `read_top_list(conn, start: str, end: str) -> pd.DataFrame`
  - `read_index_daily(conn, ts_code: str, start: str, end: str) -> pd.DataFrame`
  - `read_stock_basic(conn) -> pd.DataFrame`（含 list_date）
  - `read_corp_events(conn, ts_codes: list[str], start: str, end: str) -> pd.DataFrame`

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_limitup_db.py`：

```python
"""db.py 归一化助手与读取函数测试。"""

from __future__ import annotations

import sqlite3

from davis_analyzer.limitup import db


def test_normalize_date_roundtrip() -> None:
    assert db.normalize_date("2026-05-12") == "20260512"
    assert db.normalize_date("20260512") == "20260512"
    assert db.to_dash_date("20260512") == "2026-05-12"


def test_to_suffixed_code() -> None:
    assert db.to_suffixed_code("603311") == "603311.SH"
    assert db.to_suffixed_code("000631") == "000631.SZ"
    assert db.to_suffixed_code("300750") == "300750.SZ"
    assert db.to_suffixed_code("688981") == "688981.SH"
    assert db.to_suffixed_code("603311.SH") == "603311.SH"
    assert db.strip_code_suffix("603311.SH") == "603311"


def test_read_limit_pool_normalizes(limitup_db: sqlite3.Connection) -> None:
    limitup_db.execute(
        "INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-05-12", "603311", "limit_up", "金海高科", "家电", 10.0,
         5e7, 2, 0, "093000", "145500", 12.5, None),
    )
    limitup_db.commit()
    df = db.read_limit_pool(limitup_db, "20260501", "20260531")
    assert len(df) == 1
    assert df.iloc[0]["trade_date"] == "20260512"
    assert df.iloc[0]["ts_code"] == "603311.SH"
    assert df.iloc[0]["consecutive_boards"] == 2


def test_trading_dates_sorted_unique(limitup_db: sqlite3.Connection) -> None:
    for d in ("20260512", "20260513", "20260511"):
        limitup_db.execute(
            "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("600519.SH", d, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, None),
        )
    limitup_db.commit()
    assert db.trading_dates(limitup_db, "20260501", "20260531") == [
        "20260511", "20260512", "20260513",
    ]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_db.py -v`
Expected: FAIL，`ModuleNotFoundError`/`ImportError`

- [ ] **Step 3: 实现 db.py**

`davis_analyzer/limitup/db.py`：

```python
"""Shared SQLite data access for the limitup module.

All readers normalize trade_date to YYYYMMDD and ts_code to the
suffixed form (603311 -> 603311.SH) so downstream frames join cleanly.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
from loguru import logger

# ── code / date normalization ──

_SUFFIX_RULES_2 = {"60": ".SH", "68": ".SH", "00": ".SZ", "30": ".SZ", "92": ".BJ"}
_SUFFIX_RULES_1 = {"8": ".BJ", "4": ".BJ"}


def to_suffixed_code(code: str) -> str:
    if not code or "." in code:
        return code
    suffix = _SUFFIX_RULES_2.get(code[:2]) or _SUFFIX_RULES_1.get(code[:1])
    if suffix is None:
        logger.warning("unknown code prefix: {}", code)
        return code
    return code + suffix


def strip_code_suffix(code: str) -> str:
    return code.split(".")[0] if "." in code else code


def normalize_date(d: str) -> str:
    return d.replace("-", "")


def to_dash_date(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if "-" not in d else d


def connect() -> sqlite3.Connection:
    from stockhot.data_layer.market_db import get_connection

    return get_connection()


# ── readers ──

def trading_dates(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM daily_price "
        "WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
        (normalize_date(start), normalize_date(end)),
    ).fetchall()
    return [r[0] for r in rows]


def read_limit_pool(
    conn: sqlite3.Connection, start: str, end: str, pool_kind: str = "limit_up"
) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT trade_date, ts_code, name, sector, change_pct, seal_amount, "
        "consecutive_boards, broken_count, first_seal_time, last_seal_time, "
        "turnover_rate FROM limit_pool "
        "WHERE pool_kind=? AND trade_date>=? AND trade_date<=?",
        conn,
        params=(pool_kind, to_dash_date(start), to_dash_date(end)),
    )
    if df.empty:
        return df
    df["trade_date"] = df["trade_date"].map(normalize_date)
    df["ts_code"] = df["ts_code"].map(to_suffixed_code)
    return df.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)


def read_limit_pool_ext(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT trade_date, ts_code, float_mv FROM limit_pool_ext "
        "WHERE trade_date>=? AND trade_date<=?",
        conn,
        params=(to_dash_date(start), to_dash_date(end)),
    )
    if df.empty:
        return df
    df["trade_date"] = df["trade_date"].map(normalize_date)
    df["ts_code"] = df["ts_code"].map(to_suffixed_code)
    return df


def _chunked(codes: list[str], size: int = 900) -> list[list[str]]:
    return [codes[i : i + size] for i in range(0, len(codes), size)]


def _read_in_codes(
    conn: sqlite3.Connection, table: str, columns: str, codes: list[str],
    start: str, end: str,
) -> pd.DataFrame:
    frames = []
    for chunk in _chunked(list(codes)):
        ph = ",".join("?" * len(chunk))
        frames.append(
            pd.read_sql_query(
                f"SELECT {columns} FROM {table} WHERE ts_code IN ({ph}) "
                "AND trade_date>=? AND trade_date<=? ORDER BY ts_code, trade_date",
                conn,
                params=(*chunk, normalize_date(start), normalize_date(end)),
            )
        )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def read_daily_prices(
    conn: sqlite3.Connection, ts_codes: list[str], start: str, end: str
) -> pd.DataFrame:
    return _read_in_codes(
        conn, "daily_price",
        "ts_code, trade_date, open, high, low, close, pre_close, vol, amount, adj_factor",
        ts_codes, start, end,
    )


def read_intraday_features(
    conn: sqlite3.Connection, ts_codes: list[str], start: str, end: str
) -> pd.DataFrame:
    return _read_in_codes(
        conn, "intraday_feature",
        "ts_code, trade_date, gap, amplitude, close_position, "
        "upper_shadow, lower_shadow, body_ratio",
        ts_codes, start, end,
    )


def read_top_list(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT trade_date, ts_code, l_buy, l_sell, net_amount, net_rate, "
        "amount_rate, reason FROM top_list WHERE trade_date>=? AND trade_date<=?",
        conn,
        params=(normalize_date(start), normalize_date(end)),
    )


def read_index_daily(
    conn: sqlite3.Connection, ts_code: str, start: str, end: str
) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT ts_code, trade_date, open, high, low, close, vol, amount, pct_chg "
        "FROM index_daily WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
        "ORDER BY trade_date",
        conn,
        params=(ts_code, normalize_date(start), normalize_date(end)),
    )


def read_stock_basic(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT ts_code, name, list_date, list_status FROM stock_basic", conn
    )


def read_corp_events(
    conn: sqlite3.Connection, ts_codes: list[str], start: str, end: str
) -> pd.DataFrame:
    if not ts_codes:
        return pd.DataFrame(
            columns=["ts_code", "ann_date", "event_type", "direction"]
        )
    frames = []
    for chunk in _chunked(list(ts_codes)):
        ph = ",".join("?" * len(chunk))
        frames.append(
            pd.read_sql_query(
                "SELECT ts_code, ann_date, event_type, direction FROM corp_event "
                f"WHERE ts_code IN ({ph}) AND ann_date>=? AND ann_date<=?",
                conn,
                params=(*chunk, normalize_date(start), normalize_date(end)),
            )
        )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_db.py -v`
Expected: PASS 4 项

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/limitup/db.py davis_analyzer/tests/test_limitup_db.py
git commit -m "feat(limitup): 共享数据读取层（日期/代码归一+分块读取）"
```

---

### Task 3: 历史回补 backfill.py + CLI backfill 子命令

**Files:**
- Create: `davis_analyzer/limitup/backfill.py`
- Create: `davis_analyzer/limitup/cli.py`
- Create: `davis_analyzer/limitup/__main__.py`
- Test: `davis_analyzer/tests/test_limitup_backfill.py`

**Interfaces:**
- Consumes: `db.py`（Task 2）的 `to_dash_date`/`strip_code_suffix`。
- Produces:
  - `FetchFn = Callable[[str, str], pd.DataFrame | None]`（参数 `trade_date: YYYYMMDD, limit_type: U/Z/D`，返回 Tushare `limit_list_d` 原始 DataFrame）
  - `ensure_ext_table(conn) -> None`
  - `backfill(conn, start: str, end: str, fetch_fn: FetchFn) -> dict`（返回 `{"days_done", "rows_written", "days_skipped"}`；按日幂等，已有 `limit_pool` 记录的日期跳过）
  - `probe_earliest(fetch_fn: FetchFn, upper: str = "20200101") -> str | None`（探测 limit_list_d 最早可用日期）
  - CLI：`python -m davis_analyzer.limitup backfill --start 20230101 [--end 20260814] [--probe]`

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_limitup_backfill.py`：

```python
"""backfill.py 断点续传/幂等/字段映射测试。"""

from __future__ import annotations

import sqlite3

import pandas as pd

from davis_analyzer.limitup import backfill


def _raw_df() -> pd.DataFrame:
    return pd.DataFrame(
        [{
            "trade_date": "20240102", "ts_code": "603311.SH", "industry": "家电",
            "name": "金海高科", "pct_chg": 10.0, "close": 10.01, "amount": 5e8,
            "limit_times": 2, "float_market_value": 8e9, "total_market_value": 1e10,
            "turnover_rate": 12.5, "fd_amount": 5e7, "first_time": "093000",
            "last_time": "145500", "open_times": 0, "limit": "U",
        }]
    )


def test_backfill_writes_and_maps(limitup_db: sqlite3.Connection) -> None:
    backfill.ensure_ext_table(limitup_db)
    calls: list[tuple[str, str]] = []

    def fetch(trade_date: str, limit_type: str) -> pd.DataFrame:
        calls.append((trade_date, limit_type))
        df = _raw_df()
        df["trade_date"] = trade_date
        return df if limit_type == "U" else pd.DataFrame()

    result = backfill.backfill(limitup_db, "20240102", "20240102", fetch)
    assert result["days_done"] == 1 and result["rows_written"] == 1
    assert len(calls) == 3  # U/Z/D 三次
    row = limitup_db.execute(
        "SELECT ts_code, pool_kind, seal_amount, consecutive_boards, sector "
        "FROM limit_pool"
    ).fetchone()
    assert row[0] == "603311" and row[1] == "limit_up"
    assert row[2] == 5e7 and row[3] == 2 and row[4] == "家电"
    ext = limitup_db.execute("SELECT float_mv FROM limit_pool_ext").fetchone()
    assert ext[0] == 8e9


def test_backfill_idempotent_skips_done_day(limitup_db: sqlite3.Connection) -> None:
    backfill.ensure_ext_table(limitup_db)
    backfill.backfill(limitup_db, "20240102", "20240102", lambda d, t: _raw_df())
    result = backfill.backfill(limitup_db, "20240102", "20240102", lambda d, t: _raw_df())
    assert result["days_skipped"] == 1 and result["rows_written"] == 0


def test_backfill_handles_none_fetch(limitup_db: sqlite3.Connection) -> None:
    backfill.ensure_ext_table(limitup_db)
    limitup_db.execute(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("000001.SH", "20240102", 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, None),
    )
    limitup_db.commit()
    result = backfill.backfill(limitup_db, "20240102", "20240102",
                               lambda d, t: None)
    assert result["days_done"] == 0 and result["rows_written"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_backfill.py -v`
Expected: FAIL，模块不存在

- [ ] **Step 3: 实现 backfill.py**

`davis_analyzer/limitup/backfill.py`：

```python
"""Phase 0: backfill limit_pool history from Tushare limit_list_d.

Writes the same 12 columns as stockhot's migrate_panels (dash dates,
suffix-less codes) plus float market value into module-owned
limit_pool_ext. Idempotent per day.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

import pandas as pd
from loguru import logger

from davis_analyzer.limitup import db

FetchFn = Callable[[str, str], "pd.DataFrame | None"]

POOL_KIND_BY_TYPE = {"U": "limit_up", "Z": "broken", "D": "limit_down"}

_INSERT_POOL = (
    "INSERT OR REPLACE INTO limit_pool "
    "(trade_date, ts_code, pool_kind, name, sector, change_pct, "
    "seal_amount, consecutive_boards, broken_count, "
    "first_seal_time, last_seal_time, turnover_rate) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
)


def ensure_ext_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS limit_pool_ext ("
        "trade_date TEXT NOT NULL, ts_code TEXT NOT NULL, pool_kind TEXT NOT NULL, "
        "float_mv REAL, PRIMARY KEY (trade_date, ts_code, pool_kind))"
    )
    conn.commit()


def _safe(v: object, default: object = None) -> object:
    try:
        f = float(v)  # type: ignore[arg-type]
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return default


def backfill(
    conn: sqlite3.Connection, start: str, end: str, fetch_fn: FetchFn
) -> dict:
    ensure_ext_table(conn)
    days_done = rows_written = days_skipped = 0
    for d in db.trading_dates(conn, start, end):
        dash = db.to_dash_date(d)
        exists = conn.execute(
            "SELECT 1 FROM limit_pool WHERE trade_date=? LIMIT 1", (dash,)
        ).fetchone()
        if exists:
            days_skipped += 1
            continue
        got_any = False
        for limit_type, pool_kind in POOL_KIND_BY_TYPE.items():
            df = fetch_fn(d, limit_type)
            if df is None or df.empty:
                continue
            got_any = True
            for _, rec in df.iterrows():
                conn.execute(
                    _INSERT_POOL,
                    (
                        dash, db.strip_code_suffix(str(rec.get("ts_code", ""))),
                        pool_kind, rec.get("name"),
                        rec.get("industry"),
                        _safe(rec.get("pct_chg")), _safe(rec.get("fd_amount")),
                        int(_safe(rec.get("limit_times"), 0) or 0),
                        int(_safe(rec.get("open_times"), 0) or 0),
                        str(rec.get("first_time") or ""), str(rec.get("last_time") or ""),
                        _safe(rec.get("turnover_rate")),
                    ),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO limit_pool_ext "
                    "(trade_date, ts_code, pool_kind, float_mv) VALUES (?,?,?,?)",
                    (dash, db.strip_code_suffix(str(rec.get("ts_code", ""))),
                     pool_kind, _safe(rec.get("float_market_value"))),
                )
                rows_written += 1
        conn.commit()
        if got_any:
            days_done += 1
        else:
            logger.warning("limit_list_d no data for {}", d)
    logger.info(
        "backfill done: days_done={} rows={} skipped={}",
        days_done, rows_written, days_skipped,
    )
    return {"days_done": days_done, "rows_written": rows_written,
            "days_skipped": days_skipped}


def probe_earliest(fetch_fn: FetchFn, upper: str = "20200101") -> str | None:
    """Binary-search the earliest trade date limit_list_d covers."""
    latest = db.normalize_date(upper)
    probe = fetch_fn(latest, "U")
    if probe is None or probe.empty:
        return None
    lo, hi = db.normalize_date(upper), latest  # search below `upper`
    # coarse yearly stepping then refine monthly
    test = lo
    while True:
        cand = _shift_month(test, 12)
        df = fetch_fn(cand, "U")
        if df is None or df.empty:
            break
        test = cand
        if test <= "20050101":
            return "20050101"
    lo = test
    hi = _shift_month(test, 12)
    while _month_gap(hi, lo) > 1:
        mid = _shift_month(lo, max(1, _month_gap(lo, hi) // 2))
        df = fetch_fn(mid, "U")
        if df is not None and not df.empty:
            lo = mid
        else:
            hi = mid
    return lo


def _shift_month(ymd: str, months: int) -> str:
    y, m = int(ymd[:4]), int(ymd[4:6])
    total = y * 12 + m - 1 + months
    return f"{total // 12:04d}{total % 12 + 1:02d}01"


def _month_gap(a: str, b: str) -> int:
    return abs((int(a[:4]) * 12 + int(a[4:6])) - (int(b[:4]) * 12 + int(b[4:6])))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_backfill.py -v`
Expected: PASS 3 项

- [ ] **Step 5: 实现 CLI 骨架 + backfill 子命令**

`davis_analyzer/limitup/cli.py`：

```python
"""limitup 模块 CLI（backfill/study/backtest 子命令）。"""

from __future__ import annotations

import argparse


def _make_fetch():
    from stockhot.core.tushare_client_safe import safe_tushare_call

    def fetch(trade_date: str, limit_type: str):
        return safe_tushare_call("limit_list_d", trade_date=trade_date,
                                 limit_type=limit_type)

    return fetch


def cmd_backfill(args: argparse.Namespace) -> None:
    from davis_analyzer.limitup import backfill, db

    conn = db.connect()
    try:
        if args.probe:
            earliest = backfill.probe_earliest(_make_fetch())
            print(f"limit_list_d 最早可用日期: {earliest}")
            return
        result = backfill.backfill(conn, args.start, args.end, _make_fetch())
        print(
            f"回补完成: {result['days_done']} 天, "
            f"{result['rows_written']} 行, 跳过 {result['days_skipped']} 天"
        )
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="davis_analyzer.limitup",
        description="连板打板/抓涨停启动研究模块",
    )
    sub = parser.add_subparsers(dest="command")

    p_bf = sub.add_parser("backfill", help="回补 limit_list_d 涨停池历史")
    p_bf.add_argument("--start", required=True, help="YYYYMMDD")
    p_bf.add_argument("--end", default=None, help="YYYYMMDD，默认今日")
    p_bf.add_argument("--probe", action="store_true", help="只探测历史最早日期")
    p_bf.set_defaults(func=cmd_backfill)

    args = parser.parse_args()
    if args.command:
        args.func(args)
    else:
        parser.print_help()
```

`davis_analyzer/limitup/__main__.py`：

```python
"""Entry point for `python -m davis_analyzer.limitup`."""

from davis_analyzer.limitup.cli import main

main()
```

冒烟验证（不发 API 请求）：

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m davis_analyzer.limitup --help`
Expected: 打印 usage，含 backfill 子命令

- [ ] **Step 6: 提交**

```bash
git add davis_analyzer/limitup/backfill.py davis_analyzer/limitup/cli.py davis_analyzer/limitup/__main__.py davis_analyzer/tests/test_limitup_backfill.py
git commit -m "feat(limitup): limit_list_d 历史回补（幂等断点续传）+CLI backfill"
```

- [ ] **Step 7（执行期任务，非代码）: 真实探测与回补**

先 `python -m davis_analyzer.limitup backfill --probe`，把结果记入 `davis_analyzer/limitup/reports/backfill_notes.md`（探测日期、覆盖判断、是否触发降级方案）；然后回补 `--start <探测结果或20230101> --end 20260814`（约 10–15 分钟，限流自动节流）。**此步结论决定 Task 4–13 的研究窗口参数，必须在 Task 4 开工前完成。** 若可用历史 <3 年，向用户报告后再继续（规格 §5.2 降级路径超出本计划范围）。

---

### Task 4: 事件构建 events.py（基础字段+股票池过滤+除权剔除）

**Files:**
- Create: `davis_analyzer/limitup/events.py`
- Test: `davis_analyzer/tests/test_limitup_events.py`

**Interfaces:**
- Consumes: `db.py` 全部读取函数；`limitup_db` fixture。
- Produces:
  - `limit_ratio_for(ts_code: str) -> float`（30/68 开头 → 0.20，否则 0.10）
  - `prev_window_count(ranks: np.ndarray, window: int = 60) -> np.ndarray`
  - `build_events(conn, start: str, end: str) -> pd.DataFrame`，列（Task 5/6/7 及 study/engine 依赖）：
    `ts_code, name, trade_date, sector, float_mv, consecutive_boards, broken_count, first_seal_time, last_seal_time, seal_amount, turnover_rate, seal_ratio, limit_price, prev_limit_count_60, open, high, low, close, pre_close`
    （`limit_price == close`，收盘涨停；已剔除 ST/北交所/上市<60 天/除权日事件/非真实涨停）

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_limitup_events.py`：

```python
"""events.py 基础构建测试。"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from davis_analyzer.limitup import events


def _seed_base(conn: sqlite3.Connection) -> None:
    rows = [
        # (code, date, open, high, low, close, pre_close, adj)
        ("600001.SH", "20240102", 9.5, 11.0, 9.5, 11.0, 10.0, 1.0),   # 真涨停 +10%
        ("600001.SH", "20240103", 11.0, 12.1, 11.0, 12.1, 11.0, 1.0),  # 2 连板
        ("300002.SZ", "20240102", 10.0, 12.0, 10.0, 12.0, 10.0, 1.0),  # 创业板 +20%
        ("600003.SH", "20240102", 10.0, 10.5, 9.8, 10.2, 10.0, 1.0),   # 未涨停（不应入选）
        ("600004.SH", "20240102", 11.0, 11.0, 11.0, 11.0, 10.0, 2.0),  # 除权日 adj 变化
    ]
    conn.executemany(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(c, d, o, h, l, cl, pc, (cl/pc-1)*100, 0.0, 0.0, a, None)
         for c, d, o, h, l, cl, pc, a in rows],
    )
    conn.executemany(
        "INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("2024-01-02", "600001", "limit_up", "甲", "X业", 10.0, 1e8, 1, 0,
             "093000", "093000", 5.0, None),
            ("2024-01-03", "600001", "limit_up", "甲", "X业", 10.0, 1e8, 2, 0,
             "093000", "093000", 5.0, None),
            ("2024-01-02", "300002", "limit_up", "乙创", "Y业", 20.0, 1e8, 1, 0,
             "093000", "093000", 5.0, None),
            ("2024-01-02", "600003", "limit_up", "丙", "Z业", 2.0, 0.0, 1, 0,
             "093000", "093000", 5.0, None),
            ("2024-01-02", "600004", "limit_up", "丁", "W业", 10.0, 1e8, 1, 0,
             "093000", "093000", 5.0, None),
        ],
    )
    conn.execute(
        "INSERT INTO limit_pool_ext VALUES ('2024-01-02','600001','limit_up',1e9)"
    )
    conn.execute(
        "INSERT INTO limit_pool_ext VALUES ('2024-01-03','600001','limit_up',1.1e9)"
    )
    conn.execute(
        "INSERT INTO limit_pool_ext VALUES ('2024-01-02','300002','limit_up',2e9)"
    )
    conn.executemany(
        "INSERT INTO stock_basic VALUES (?,?,?,?,?,?)",
        [
            ("600001.SH", "甲", "X业", "L", None, "20000101"),
            ("300002.SZ", "乙创", "Y业", "L", None, "20000101"),
            ("600003.SH", "丙", "Z业", "L", None, "20000101"),
            ("600004.SH", "丁", "W业", "L", None, "20231220"),  # 上市<60天
        ],
    )
    conn.commit()


def test_prev_window_count() -> None:
    ranks = np.array([10, 11, 80, 82])
    np.testing.assert_array_equal(events.prev_window_count(ranks, 60),
                                  [0, 1, 1, 2])


def test_build_events_filters(limitup_db: sqlite3.Connection) -> None:
    _seed_base(limitup_db)
    df = events.build_events(limitup_db, "20240101", "20240110")
    codes = set(df["ts_code"])
    # 600003 非真实涨停、600004 上市<60天 被剔除；600001 两天保留、300002 保留
    assert codes == {"600001.SH", "300002.SZ"}
    row = df[(df.ts_code == "600001.SH") & (df.trade_date == "20240103")].iloc[0]
    assert row["limit_price"] == 12.1
    assert row["consecutive_boards"] == 2
    assert abs(row["seal_ratio"] - 1e8 / 1.1e9) < 1e-9
    # 首板事件的 60 日前置涨停计数
    first = df[(df.ts_code == "600001.SH") & (df.trade_date == "20240102")].iloc[0]
    assert first["prev_limit_count_60"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_events.py -v`
Expected: FAIL，模块不存在

- [ ] **Step 3: 实现 events.py 基础部分**

`davis_analyzer/limitup/events.py`：

```python
"""涨停事件表构建：基础字段、股票池过滤、收益标签、量价与龙虎榜特征."""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.limitup import db

# ── helpers ──

def limit_ratio_for(ts_code: str) -> float:
    """涨停幅度：创业板/科创板 20cm，主板 10cm（ST/北交所已在股票池剔除）."""
    return 0.20 if ts_code.startswith(("30", "68")) else 0.10


def is_limit_up_close(close: float, pre_close: float, ratio: float) -> bool:
    if not (close > 0 and pre_close > 0):
        return False
    limit_px = round(pre_close * (1 + ratio) + 1e-9, 2)
    return abs(close - limit_px) <= 0.005


def prev_window_count(ranks: np.ndarray, window: int = 60) -> np.ndarray:
    """For sorted ranks, count of prior elements within [r-window, r)."""
    left = np.searchsorted(ranks, ranks - window, side="left")
    return np.arange(len(ranks)) - left


def _drop_ex_dividend(prices: pd.DataFrame) -> pd.DataFrame:
    g = prices.sort_values(["ts_code", "trade_date"]).groupby("ts_code", sort=False)
    prev_adj = g["adj_factor"].shift(1)
    drop_mask = prev_adj.notna() & (prices["adj_factor"] != prev_adj)
    if drop_mask.any():
        logger.info("剔除除权日事件 {} 条", int(drop_mask.sum()))
    return prices[~drop_mask].copy()


# ── main builder ──

def build_events(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    lp = db.read_limit_pool(conn, start, end, pool_kind="limit_up")
    if lp.empty:
        return pd.DataFrame()
    ext = db.read_limit_pool_ext(conn, start, end)
    if not ext.empty:
        lp = lp.merge(ext, on=["ts_code", "trade_date"], how="left")
    else:
        lp["float_mv"] = np.nan

    # 股票池过滤：ST / 北交所 / 上市<60 自然日
    lp = lp[~lp["name"].str.contains("ST", na=False)]
    lp = lp[~lp["ts_code"].str.endswith(".BJ")]
    basic = db.read_stock_basic(conn)[["ts_code", "list_date"]]
    lp = lp.merge(basic, on="ts_code", how="left")
    list_dt = pd.to_datetime(lp["list_date"], format="%Y%m%d", errors="coerce")
    trade_dt = pd.to_datetime(lp["trade_date"], format="%Y%m%d")
    lp = lp[(trade_dt - list_dt).dt.days >= 60]
    lp = lp.drop(columns=["list_date"])

    # 价格数据（含窗口前后缓冲，供标签/形态用）
    buffer_start = _shift_day(db.normalize_date(start), -30)
    buffer_end = _shift_day(db.normalize_date(end), 15)
    prices = db.read_daily_prices(
        conn, sorted(lp["ts_code"].unique()), buffer_start, buffer_end
    )
    prices = _drop_ex_dividend(prices)
    price_cols = ["open", "high", "low", "close", "pre_close", "vol", "amount",
                  "adj_factor"]
    lp = lp.merge(prices[["ts_code", "trade_date", *price_cols]],
                  on=["ts_code", "trade_date"], how="inner")

    # 涨停价真实性校验（数据噪声防线）
    ratios = lp["ts_code"].map(limit_ratio_for)
    ok = lp.apply(
        lambda r: is_limit_up_close(r["close"], r["pre_close"], limit_ratio_for(r["ts_code"])),
        axis=1,
    )
    lp = lp[ok]
    lp["limit_price"] = lp["close"]
    lp["seal_ratio"] = np.where(
        lp["float_mv"].fillna(0) > 0, lp["seal_amount"] / lp["float_mv"], np.nan
    )

    # 前 60 交易日涨停次数
    rank_map = {d: i for i, d in enumerate(
        db.trading_dates(conn, buffer_start, buffer_end))}
    lp["rank"] = lp["trade_date"].map(rank_map)
    parts = []
    for _, g in lp.sort_values(["ts_code", "rank"]).groupby("ts_code", sort=False):
        ranks = g["rank"].to_numpy()
        g = g.copy()
        g["prev_limit_count_60"] = prev_window_count(ranks, 60)
        parts.append(g)
    lp = pd.concat(parts, ignore_index=True) if parts else lp

    logger.info("build_events: {} 条事件 [{} → {}]", len(lp), start, end)
    return lp.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)


def _shift_day(ymd: str, days: int) -> str:
    dt = pd.to_datetime(ymd, format="%Y%m%d") + pd.Timedelta(days=days)
    return dt.strftime("%Y%m%d")
```

（`ratios` 变量未再使用可删；保留 `ok` 计算即可。实现时直接写 `ok = lp.apply(...)` 一行。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_events.py -v`
Expected: PASS 2 项

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/limitup/events.py davis_analyzer/tests/test_limitup_events.py
git commit -m "feat(limitup): 涨停事件基础构建（股票池过滤/除权剔除/封单比/前置涨停计数）"
```

---

### Task 5: 事件收益标签 attach_return_labels

**Files:**
- Modify: `davis_analyzer/limitup/events.py`（追加函数 + 接入 build_events 末尾）
- Test: `davis_analyzer/tests/test_limitup_events.py`（追加测试）

**Interfaces:**
- Produces: `attach_return_labels(events: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame`，新增列：
  `ret_open_1, ret_close_1, ret_high_1, ret_low_1, ret_3d, ret_5d, promoted`（`promoted` = T+1 收盘继续涨停 bool；T+1 为下一**交易日**；窗口不足时为 NaN）
- `build_events` 返回值在 Task 5 后包含上述列。

- [ ] **Step 1: 追加失败测试**

追加到 `test_limitup_events.py`：

```python
def test_return_labels(limitup_db: sqlite3.Connection) -> None:
    _seed_base(limitup_db)
    # 再造 2 天价格：600001 在 0104 断板低开、0105 反包
    conn = limitup_db
    conn.executemany(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("600001.SH", "20240104", 11.5, 11.8, 11.0, 11.2, 12.1, -7.4, 0, 0, 1.0, None),
            ("600001.SH", "20240105", 11.0, 12.32, 11.0, 12.32, 11.2, 10.0, 0, 0, 1.0, None),
        ],
    )
    conn.commit()
    df = events.build_events(conn, "20240101", "20240110")
    e2 = df[(df.ts_code == "600001.SH") & (df.trade_date == "20240103")].iloc[0]
    assert abs(e2["ret_open_1"] - 11.5 / 12.1 - 1) < 1e-9
    assert abs(e2["ret_close_1"] - 11.2 / 12.1 - 1) < 1e-9
    assert not e2["promoted"]  # 0104 收盘 11.2 未涨停
    e1 = df[(df.ts_code == "600001.SH") & (df.trade_date == "20240102")].iloc[0]
    assert e1["promoted"]  # 0103 12.1 = 11.0*1.1 涨停
    assert abs(e1["ret_open_1"] - 11.0 / 11.0 - 1) < 1e-9
    assert abs(e1["ret_3d"] - 12.32 / 11.0 - 1) < 1e-9  # 0105 收盘/0102 涨停价
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_events.py -v`
Expected: 新测试 FAIL（列不存在）

- [ ] **Step 3: 实现 attach_return_labels**

追加到 `events.py`（并在 `build_events` 的 `logger.info("build_events...")` 行之前插入两段：重新读取 prices 后调用 `lp = attach_return_labels(lp, prices)`——注意 `build_events` 内已有 `prices` 变量，直接传）：

```python
# ── forward return labels ──

def attach_return_labels(
    events: pd.DataFrame, prices: pd.DataFrame
) -> pd.DataFrame:
    """Attach T+1/T+3/T+5 returns and promotion flag (T+1 closes limit-up)."""
    p = prices.sort_values(["ts_code", "trade_date"]).copy()
    g = p.groupby("ts_code", sort=False)
    p["t1_open"] = g["open"].shift(-1)
    p["t1_high"] = g["high"].shift(-1)
    p["t1_low"] = g["low"].shift(-1)
    p["t1_close"] = g["close"].shift(-1)
    p["t1_pre_close"] = g["pre_close"].shift(-1)
    p["t3_close"] = g["close"].shift(-3)
    p["t5_close"] = g["close"].shift(-5)
    label_cols = ["t1_open", "t1_high", "t1_low", "t1_close", "t1_pre_close",
                  "t3_close", "t5_close"]
    ev = events.merge(p[["ts_code", "trade_date", *label_cols]],
                      on=["ts_code", "trade_date"], how="left")
    ev["ret_open_1"] = ev["t1_open"] / ev["limit_price"] - 1
    ev["ret_close_1"] = ev["t1_close"] / ev["limit_price"] - 1
    ev["ret_high_1"] = ev["t1_high"] / ev["limit_price"] - 1
    ev["ret_low_1"] = ev["t1_low"] / ev["limit_price"] - 1
    ev["ret_3d"] = ev["t3_close"] / ev["limit_price"] - 1
    ev["ret_5d"] = ev["t5_close"] / ev["limit_price"] - 1
    ev["promoted"] = ev.apply(
        lambda r: bool(
            pd.notna(r["t1_close"]) and pd.notna(r["t1_pre_close"])
            and is_limit_up_close(r["t1_close"], r["t1_pre_close"],
                                  limit_ratio_for(r["ts_code"]))
        ),
        axis=1,
    )
    return ev.drop(columns=label_cols)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_events.py -v`
Expected: PASS 3 项

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/limitup/events.py davis_analyzer/tests/test_limitup_events.py
git commit -m "feat(limitup): 事件收益标签（T+1 开盘/收盘/冲高/回撤/3日/5日/晋级）"
```

---

### Task 6: 量价特征 + 龙虎榜 join + 消息面代理

**Files:**
- Modify: `davis_analyzer/limitup/events.py`（追加 3 个函数并接入 build_events）
- Test: `davis_analyzer/tests/test_limitup_events.py`（追加测试）

**Interfaces:**
- Produces（最终 `build_events` 输出列，任务 7/10/11/12 依赖）：
  - 量价：`vol_ratio_20, mild_vol_days_5`
  - 龙虎榜：`on_lhb, lhb_net_amount, lhb_net_rate, lhb_amount_rate, lhb_reason`
  - 消息面代理：`sector_linkage, sector_share, negative_event_30d`

- [ ] **Step 1: 追加失败测试**

追加到 `test_limitup_events.py`：

```python
def test_volume_lhb_and_news_proxy(limitup_db: sqlite3.Connection) -> None:
    conn = limitup_db
    _seed_base(conn)
    # 600001 龙虎榜上榜（0102），同日同板块 2 家涨停
    conn.execute(
        "INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("2024-01-02", "600009", "limit_up", "戊", "X业", 10.0, 1e8, 1, 0,
         "093000", "093000", 5.0, None),
    )
    conn.execute(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("600009.SH", "20240102", 5, 5.5, 5, 5.5, 5.0, 10.0, 0, 0, 1.0, None),
    )
    conn.execute(
        "INSERT INTO stock_basic VALUES ('600009.SH','戊','X业','L',NULL,'20000101')"
    )
    conn.execute(
        "INSERT INTO top_list VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("20240102", "600001.SH", "甲", 11.0, 10.0, 5.0, 3e8, 1e8, 2e8, 3e8,
         1e8, 0.33, 0.5, 1e9, "日涨幅偏离值达7%", None),
    )
    conn.execute(
        "INSERT INTO corp_event VALUES (?,?,?,?,?,?,?,?)",
        ("600001.SH", "20231225", "share_float", "减持", 1.0, "{}", "test", None),
    )
    conn.commit()
    df = events.build_events(conn, "20240101", "20240110")
    e = df[(df.ts_code == "600001.SH") & (df.trade_date == "20240102")].iloc[0]
    assert e["on_lhb"]
    assert e["lhb_net_amount"] == 1e8
    assert e["sector_linkage"] == 2  # 600001 + 600009 同板块 X业
    assert e["negative_event_30d"]  # 30 日内解禁/减持
    e3 = df[(df.ts_code == "600001.SH") & (df.trade_date == "20240103")].iloc[0]
    assert not e3["on_lhb"]
    assert "vol_ratio_20" in df.columns and "mild_vol_days_5" in df.columns
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_events.py -v`
Expected: 新测试 FAIL

- [ ] **Step 3: 实现三个特征函数并接入**

追加到 `events.py`：

```python
# ── volume features ──

def attach_volume_features(
    events: pd.DataFrame, prices: pd.DataFrame
) -> pd.DataFrame:
    p = prices.sort_values(["ts_code", "trade_date"]).copy()
    g = p.groupby("ts_code", sort=False)
    p["vol_ma20_prev"] = g["vol"].transform(lambda s: s.rolling(20).mean().shift(1))
    p["vol_ratio_20"] = p["vol"] / p["vol_ma20_prev"]
    mild = ((p["vol"] > p["vol_ma20_prev"] * 1.2)
            & (p["vol"] < p["vol_ma20_prev"] * 2.5)).astype(float)
    p["mild_vol_days_5"] = g["mild"] if False else mild.groupby(p["ts_code"]).transform(
        lambda s: s.rolling(5).sum().shift(1)
    )
    return events.merge(
        p[["ts_code", "trade_date", "vol_ratio_20", "mild_vol_days_5"]],
        on=["ts_code", "trade_date"], how="left",
    )
```

注意：上面 `p["mild_vol_days_5"]` 一行是冗余写法，实现时直接写：

```python
    p["_mild"] = ((p["vol"] > p["vol_ma20_prev"] * 1.2)
                  & (p["vol"] < p["vol_ma20_prev"] * 2.5)).astype(float)
    p["mild_vol_days_5"] = p.groupby("ts_code")["_mild"].transform(
        lambda s: s.rolling(5).sum().shift(1)
    )
```

```python
# ── dragon-tiger join ──

def attach_lhb_features(
    events: pd.DataFrame, conn: sqlite3.Connection, start: str, end: str
) -> pd.DataFrame:
    lhb = db.read_top_list(conn, start, end)
    if lhb.empty:
        events["on_lhb"] = False
        for col in ("lhb_net_amount", "lhb_net_rate", "lhb_amount_rate"):
            events[col] = np.nan
        events["lhb_reason"] = ""
        return events
    lhb = lhb.rename(columns={
        "net_amount": "lhb_net_amount", "net_rate": "lhb_net_rate",
        "amount_rate": "lhb_amount_rate", "reason": "lhb_reason",
    })
    lhb["on_lhb"] = True
    ev = events.merge(
        lhb[["ts_code", "trade_date", "on_lhb", "lhb_net_amount",
             "lhb_net_rate", "lhb_amount_rate", "lhb_reason"]],
        on=["ts_code", "trade_date"], how="left",
    )
    ev["on_lhb"] = ev["on_lhb"].fillna(False)
    return ev


# ── news proxies: sector linkage + negative corp events ──

def attach_news_proxies(
    events: pd.DataFrame, conn: sqlite3.Connection, start: str, end: str
) -> pd.DataFrame:
    ev = events.copy()
    day_count = ev.groupby("trade_date")["ts_code"].transform("size")
    ev["sector_linkage"] = ev.groupby(["trade_date", "sector"])["ts_code"].transform("size")
    ev["sector_share"] = ev["sector_linkage"] / day_count

    codes = sorted(ev["ts_code"].unique())
    ev_start = _shift_day(db.normalize_date(start), -45)
    ce = db.read_corp_events(conn, codes, ev_start, end)
    neg = ce[
        (ce["event_type"] == "share_float")
        | ((ce["event_type"] == "holder_trade") & (ce["direction"] == "减持"))
    ]
    neg_map: dict[str, list[str]] = {}
    for _, r in neg.iterrows():
        neg_map.setdefault(r["ts_code"], []).append(r["ann_date"])
    ev["negative_event_30d"] = ev.apply(
        lambda r: _has_neg_within(neg_map.get(r["ts_code"], []), r["trade_date"]), axis=1
    )
    return ev


def _has_neg_within(ann_dates: list[str], trade_date: str, days: int = 30) -> bool:
    if not ann_dates:
        return False
    t = pd.to_datetime(trade_date, format="%Y%m%d")
    return any(0 <= (t - pd.to_datetime(a, format="%Y%m%d")).days <= days
               for a in ann_dates)
```

`build_events` 末尾（`logger.info` 前）按序接入：

```python
    lp = attach_return_labels(lp, prices)
    lp = attach_volume_features(lp, prices)
    lp = attach_lhb_features(lp, conn, start, end)
    lp = attach_news_proxies(lp, conn, start, end)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_events.py -v`
Expected: PASS 4 项

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/limitup/events.py davis_analyzer/tests/test_limitup_events.py
git commit -m "feat(limitup): 量价/龙虎榜/板块联动+利空事件消息面代理特征"
```

---

### Task 7: 形态识别 patterns.py

**Files:**
- Create: `davis_analyzer/limitup/patterns.py`
- Test: `davis_analyzer/tests/test_limitup_patterns.py`

**Interfaces:**
- Consumes: `build_events` 输出（Task 6 后的完整列）；`db.read_intraday_features`。
- Produces:
  - `seal_band(first_seal_time: str) -> str`（"早盘"/"午盘"/"尾盘"/"未知"）
  - `attach_pattern_features(events: pd.DataFrame, conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame`，新增列：
    `k_gap, k_amplitude, k_close_position, k_upper_shadow, k_lower_shadow, k_body_ratio, is_breakout, is_trend_accel, is_oversold, is_consolidation, pattern_label, first_seal_band, late_reseal`
  - 形态判定窗口全部基于 T-1 及更早（形态=涨停"前"的走势），仅突破判定用 T 收盘 vs 前高。
  - `pattern_label` 优先级：突破型 > 趋势加速型 > 横盘首板型 > 超跌反转型 > 其他（先验档位，禁止调参，规格 §7.1）。

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_limitup_patterns.py`：

```python
"""patterns.py K线/位置形态识别测试。"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from davis_analyzer.limitup import patterns


def test_seal_band() -> None:
    assert patterns.seal_band("093000") == "早盘"
    assert patterns.seal_band("133000") == "午盘"
    assert patterns.seal_band("143500") == "尾盘"
    assert patterns.seal_band("000000") == "未知"


def _mk_prices() -> pd.DataFrame:
    rows = []
    code = "600100.SH"
    rng = np.random.default_rng(7)
    # 40 天横盘 9.5-10.5，第 41 天放量涨停 11.0（突破 60 日前高近似）
    for i in range(1, 61):
        close = 10.0 + float(rng.normal(0, 0.1))
        rows.append((code, f"2023{int(10 + (i - 1) // 30):02d}{(i - 1) % 30 + 1:02d}",
                     close, close, close, close, close, close, close, 1e4, 1e7, 1.0))
    rows.append((code, "20240102", 10.5, 11.0, 10.5, 11.0, 10.0,
                 10.0, 1e6, 1e8, 1.0))
    df = pd.DataFrame(rows, columns=[
        "ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
        "pct_chg", "vol", "amount", "adj_factor"])
    return df


def test_classify_breakout() -> None:
    prices = _mk_prices()
    high = prices["high"]
    prior_high60 = high.rolling(60).max().shift(1)
    assert prices.iloc[-1]["close"] >= prior_high60.iloc[-1] * 0.98  # 突破条件成立
    ev = pd.DataFrame([{
        "ts_code": "600100.SH", "trade_date": "20240102", "close": 11.0,
        "first_seal_time": "093000", "last_seal_time": "093500",
        "consecutive_boards": 1,
    }])
    labeled = patterns.classify_from_prices(ev, prices)
    assert labeled.iloc[0]["pattern_label"] == "突破型"


def test_attach_kline_and_bands(limitup_db: sqlite3.Connection) -> None:
    limitup_db.execute(
        "INSERT INTO intraday_feature VALUES (?,?,?,?,?,?,?,?,?)",
        ("600100.SH", "20240102", 0.5, 10.0, 1.0, 0.1, 0.2, 0.7, None),
    )
    limitup_db.commit()
    ev = pd.DataFrame([{
        "ts_code": "600100.SH", "trade_date": "20240102",
        "first_seal_time": "143800", "last_seal_time": "145500",
    }])
    out = patterns.attach_kline_features(ev, limitup_db, "20240101", "20240110")
    assert out.iloc[0]["k_body_ratio"] == 0.7
    assert out.iloc[0]["first_seal_band"] == "尾盘"
    assert bool(out.iloc[0]["late_reseal"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_patterns.py -v`
Expected: FAIL，模块不存在

- [ ] **Step 3: 实现 patterns.py**

```python
"""形态识别：K 线形态（intraday_feature）+ 位置形态（daily_price）→ 形态标签.

档位为研究前固定的先验（规格 §7.1），禁止连续寻优。
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from davis_analyzer.limitup import db

# ── seal quality ──

def seal_band(first_seal_time: str) -> str:
    if not isinstance(first_seal_time, str) or first_seal_time in ("", "000000"):
        return "未知"
    if first_seal_time < "090000":
        return "未知"
    if first_seal_time < "100000":
        return "早盘"
    if first_seal_time < "140000":
        return "午盘"
    return "尾盘"


def attach_kline_features(
    events: pd.DataFrame, conn: sqlite3.Connection, start: str, end: str
) -> pd.DataFrame:
    codes = sorted(events["ts_code"].unique())
    kl = db.read_intraday_features(conn, codes, start, end)
    kl = kl.rename(columns={
        "gap": "k_gap", "amplitude": "k_amplitude",
        "close_position": "k_close_position", "upper_shadow": "k_upper_shadow",
        "lower_shadow": "k_lower_shadow", "body_ratio": "k_body_ratio",
    })
    ev = events.merge(kl, on=["ts_code", "trade_date"], how="left")
    ev["first_seal_band"] = ev["first_seal_time"].map(seal_band)
    ev["late_reseal"] = ev["last_seal_time"].map(
        lambda t: isinstance(t, str) and t >= "143000"
    )
    return ev


# ── positional patterns (computed on prices up to T-1) ──

def classify_from_prices(events: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    p = prices.sort_values(["ts_code", "trade_date"]).copy()
    g = p.groupby("ts_code", sort=False)
    p["prior_high60"] = g["high"].transform(lambda s: s.rolling(60).max().shift(1))
    p["box40"] = (
        g["high"].transform(lambda s: s.rolling(40).max().shift(1))
        / g["low"].transform(lambda s: s.rolling(40).min().shift(1)) - 1
    )
    ma20 = g["close"].transform(lambda s: s.rolling(20).mean())
    p["ma20_rising"] = ma20 > ma20.shift(5) if False else ma20.groupby(
        p["ts_code"]).transform(lambda s: s > s.shift(5))
    p["ret20p"] = g["close"].transform(
        lambda s: s.shift(1) / s.shift(21) - 1)
    ma60 = g["close"].transform(lambda s: s.rolling(60).mean())
    p["ret60p"] = g["close"].transform(
        lambda s: s.shift(1) / s.shift(61) - 1)
    p["range120p"] = (
        g["close"].transform(lambda s: s.rolling(120).max().shift(1))
        / g["close"].transform(lambda s: s.rolling(120).min().shift(1)) - 1
    )
    p["is_breakout"] = (p["close"] >= p["prior_high60"] * 0.98) & (p["box40"] < 0.25)
    p["is_trend_accel"] = (
        (p["close"] > ma20) & p["ma20_rising"] & p["ret20p"].between(0.15, 0.40)
    )
    p["is_oversold"] = (p["ret60p"] < -0.30) & (p["close"] < ma60 * 0.90)
    p["is_consolidation"] = p["range120p"] < 0.20
    p["pattern_label"] = np.select(
        [p["is_breakout"], p["is_trend_accel"], p["is_consolidation"], p["is_oversold"]],
        ["突破型", "趋势加速型", "横盘首板型", "超跌反转型"],
        default="其他",
    )
    cols = ["prior_high60", "is_breakout", "is_trend_accel", "is_oversold",
            "is_consolidation", "pattern_label"]
    return events.merge(p[["ts_code", "trade_date", *cols]],
                        on=["ts_code", "trade_date"], how="left")


def attach_pattern_features(
    events: pd.DataFrame, conn: sqlite3.Connection, start: str, end: str
) -> pd.DataFrame:
    ev = attach_kline_features(events, conn, start, end)
    buffer_start = _shift(start, -30)
    buffer_end = _shift(end, 15)
    prices = db.read_daily_prices(
        conn, sorted(ev["ts_code"].unique()), buffer_start, buffer_end
    )
    return classify_from_prices(ev, prices)


def _shift(ymd: str, days: int) -> str:
    dt = pd.to_datetime(ymd, format="%Y%m%d") + pd.Timedelta(days=days)
    return dt.strftime("%Y%m%d")
```

实现说明：`ma20_rising` 的写法直接用 `p["ma20"] = ma20` 后 `p["ma20_rising"] = p.groupby("ts_code")["ma20"].transform(lambda s: s > s.shift(5))`（去掉示例里的 `if False` 三元冗余）。布尔列 merge 后含 NaN 时统一 `fillna(False)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_patterns.py -v`
Expected: PASS 3 项

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/limitup/patterns.py davis_analyzer/tests/test_limitup_patterns.py
git commit -m "feat(limitup): 形态识别（K线特征+四类位置形态+互斥形态标签）"
```

---

### Task 8: 市场环境 sentiment.py（三轴 + regime 四档）

**Files:**
- Create: `davis_analyzer/limitup/sentiment.py`
- Test: `davis_analyzer/tests/test_limitup_sentiment.py`

**Interfaces:**
- Produces:
  - `classify_regime(row: pd.Series) -> str`（"冰点"/"高潮"/"退潮"/"回暖"，优先级 冰点 > 高潮 > 退潮 > 回暖，NaN 条件跳过）
  - `build_market_regime(conn, start: str, end: str) -> pd.DataFrame`，index 无、列：
    `trade_date, limit_up_count, broken_rate, lianban_count, max_boards, promo_12, promo_23, premium, red_rate, up_down_ratio, new_high_ratio, amount_sum, index_ma_bull, regime_label`
    （`premium`/`red_rate` = 昨日涨停池今日开盘溢价均值与红盘率；promo_xy = x进y 晋级率；regime 阈值为先验固定值，冻结）
- Regime 先验阈值（模块级常量，写死后不得程序化调整）：
  `冰点: premium < -0.02 或 limit_up_count <= 30`；`高潮: max_boards >= 7 或 limit_up_count >= 120`；`退潮: premium < 0 或 promo_12 < 0.30`；否则 `回暖`。

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_limitup_sentiment.py`：

```python
"""sentiment.py 三轴环境与 regime 分档测试。"""

from __future__ import annotations

import sqlite3

import pandas as pd

from davis_analyzer.limitup import sentiment


def test_classify_regime_priority() -> None:
    freeze = pd.Series({"premium": -0.03, "limit_up_count": 100, "max_boards": 3,
                        "promo_12": 0.5})
    assert sentiment.classify_regime(freeze) == "冰点"  # 冰点优先于高潮/退潮
    hot = pd.Series({"premium": 0.05, "limit_up_count": 150, "max_boards": 8,
                     "promo_12": 0.6})
    assert sentiment.classify_regime(hot) == "高潮"
    cool = pd.Series({"premium": -0.005, "limit_up_count": 60, "max_boards": 4,
                      "promo_12": 0.5})
    assert sentiment.classify_regime(cool) == "退潮"
    warm = pd.Series({"premium": 0.02, "limit_up_count": 60, "max_boards": 4,
                      "promo_12": 0.5})
    assert sentiment.classify_regime(warm) == "回暖"
    nan_case = pd.Series({"premium": float("nan"), "limit_up_count": 60,
                          "max_boards": 4, "promo_12": 0.5})
    assert sentiment.classify_regime(nan_case) == "回暖"  # NaN 不触发条件


def test_build_market_regime(limitup_db: sqlite3.Connection) -> None:
    conn = limitup_db
    # 指数两天
    conn.executemany(
        "INSERT INTO index_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
        [("000001.SH", "20240102", 3000, 3050, 2990, 3040, 1, 1, 1.3, None),
         ("000001.SH", "20240103", 3040, 3060, 3030, 3050, 1, 1, 0.3, None),
         ("399001.SZ", "20240102", 9500, 9600, 9450, 9550, 1, 1, 1.0, None),
         ("399001.SZ", "20240103", 9550, 9620, 9540, 9600, 1, 1, 0.5, None),
         ("399006.SZ", "20240102", 1800, 1830, 1795, 1820, 1, 1, 1.1, None),
         ("399006.SZ", "20240103", 1820, 1840, 1810, 1830, 1, 1, 0.5, None)],
    )
    # 全市场宽度：两天各 2 只
    conn.executemany(
        "INSERT INTO daily_price VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("600001.SH", "20240102", 10, 11, 10, 11, 10, 10, 0, 0, 1.0, None),
            ("600002.SH", "20240102", 10, 10.5, 9.9, 10.2, 10, 2, 0, 0, 1.0, None),
            ("600001.SH", "20240103", 11, 12.1, 11, 12.1, 11, 10, 0, 0, 1.0, None),
            ("600002.SH", "20240103", 10.2, 10.4, 10.1, 10.3, 10.2, 1, 0, 0, 1.0, None),
        ],
    )
    # 涨停池：0102 两只首板（其中 600001），0103 600001 晋级 2 板 → premium 出现在 0103
    conn.executemany(
        "INSERT INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("2024-01-02", "600001", "limit_up", "甲", "X", 10, 1e8, 1, 0,
             "093000", "093000", 5, None),
            ("2024-01-02", "600002", "limit_up", "乙", "X", 10, 1e8, 1, 0,
             "093000", "093000", 5, None),
            ("2024-01-02", "600003", "broken", "丙", "X", 5, 0, 1, 2,
             "093000", "140000", 5, None),
            ("2024-01-03", "600001", "limit_up", "甲", "X", 10, 1e8, 2, 0,
             "093000", "093000", 5, None),
        ],
    )
    conn.commit()
    regime = sentiment.build_market_regime(conn, "20240101", "20240110")
    assert len(regime) == 2
    r2 = regime[regime.trade_date == "20240102"].iloc[0]
    r3 = regime[regime.trade_date == "20240103"].iloc[0]
    assert r2["limit_up_count"] == 2
    assert abs(r2["broken_rate"] - 1 / 3) < 1e-9  # broken 1 / (2 up + 1 broken)
    assert abs(r3["promo_12"] - 1.0) < 1e-9  # 2 只首板 1 只晋级
    # premium@0103 = mean(600001: 11/11-1=0, 600002: 10.2/10.2-1=0)
    assert abs(r3["premium"]) < 1e-9
    assert r3["lianban_count"] == 1 and r3["max_boards"] == 2
    assert 0 <= r3["up_down_ratio"] <= 1
    assert "regime_label" in regime.columns
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_sentiment.py -v`
Expected: FAIL，模块不存在

- [ ] **Step 3: 实现 sentiment.py**

```python
"""市场环境三轴（指数趋势/市场宽度/情绪周期）与 regime 四档.

阈值为先验固定常量（规格 §6.5），研究期一次校准后冻结，禁止滚动拟合。
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.limitup import db

REGIME_FREEZE = -0.02   # premium < -2% → 冰点候选
REGIME_COLD_COUNT = 30
REGIME_HOT_BOARDS = 7
REGIME_HOT_COUNT = 120
REGME_COOL_PREMIUM = 0.0
REGIME_COOL_PROMO12 = 0.30


def classify_regime(row: pd.Series) -> str:
    if _lt(row.get("premium"), REGIME_FREEZE) or _le(row.get("limit_up_count"), REGIME_COLD_COUNT):
        return "冰点"
    if _ge(row.get("max_boards"), REGIME_HOT_BOARDS) or _ge(row.get("limit_up_count"), REGIME_HOT_COUNT):
        return "高潮"
    if _lt(row.get("premium"), REGME_COOL_PREMIUM) or _lt(row.get("promo_12"), REGIME_COOL_PROMO12):
        return "退潮"
    return "回暖"


def _lt(v: object, thr: float) -> bool:
    return bool(pd.notna(v) and float(v) < thr)  # type: ignore[arg-type]


def _le(v: object, thr: float) -> bool:
    return bool(pd.notna(v) and float(v) <= thr)  # type: ignore[arg-type]


def _ge(v: object, thr: float) -> bool:
    return bool(pd.notna(v) and float(v) >= thr)  # type: ignore[arg-type]


def _limit_axes(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    lp = db.read_limit_pool(conn, start, end, pool_kind="limit_up")
    broken = db.read_limit_pool(conn, start, end, pool_kind="broken")
    rows: dict[str, dict] = {}
    for d, g in lp.groupby("trade_date"):
        rows.setdefault(d, {})["limit_up_count"] = len(g)
        rows.setdefault(d, {})["lianban_count"] = int((g["consecutive_boards"] >= 2).sum())
        rows.setdefault(d, {})["max_boards"] = int(g["consecutive_boards"].max())
    for d, g in broken.groupby("trade_date"):
        rows.setdefault(d, {})["broken_n"] = len(g)
    df = pd.DataFrame([{"trade_date": d, **v} for d, v in sorted(rows.items())])
    up = df.get("limit_up_count", pd.Series(dtype=float)).fillna(0)
    bk = df.get("broken_n", pd.Series(dtype=float)).fillna(0)
    df["broken_rate"] = bk / (up + bk).replace(0, np.nan)
    df = df.drop(columns=["broken_n"], errors="ignore")
    return df


def _promotion_axes(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """promo_12/23/34 by pairing each pool row with its next trading day row."""
    lp = db.read_limit_pool(conn, start, end, pool_kind="limit_up")
    if lp.empty:
        return pd.DataFrame(columns=["trade_date", "promo_12", "promo_23", "promo_34"])
    cal = db.trading_dates(conn, start, end)
    rank = {d: i for i, d in enumerate(cal)}
    lp["rank"] = lp["trade_date"].map(rank)
    lp = lp.sort_values(["ts_code", "rank"])
    g = lp.groupby("ts_code", sort=False)
    lp["next_boards"] = g["consecutive_boards"].shift(-1)
    lp["next_rank"] = g["rank"].shift(-1)
    ok = (lp["next_rank"] == lp["rank"] + 1) & (
        lp["next_boards"] == lp["consecutive_boards"] + 1
    )
    out_rows = []
    for d, g2 in lp.groupby("trade_date"):
        row = {"trade_date": d}
        for base in (1, 2, 3):
            sub = g2[g2["consecutive_boards"] == base]
            row[f"promo_{base}{base + 1}"] = (
                float(ok.loc[sub.index].mean()) if len(sub) else np.nan
            )
        out_rows.append(row)
    return pd.DataFrame(out_rows)


def _premium_axes(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """昨日涨停池今日开盘溢价/红盘率，归属到 T+1 日期."""
    sql = (
        "SELECT lp.trade_date AS d0, COUNT(*) AS n, "
        "AVG(1.0 * dp1.open / dp0.close - 1) AS premium, "
        "AVG(CASE WHEN dp1.open > dp0.close THEN 1.0 ELSE 0 END) AS red_rate "
        "FROM limit_pool lp "
        "JOIN daily_price dp0 ON dp0.ts_code = lp.ts_code "
        "  AND dp0.trade_date = REPLACE(lp.trade_date, '-', '') "
        "JOIN daily_price dp1 ON dp1.ts_code = lp.ts_code AND dp1.trade_date = "
        "  (SELECT MIN(x.trade_date) FROM daily_price x "
        "   WHERE x.ts_code = lp.ts_code AND x.trade_date > REPLACE(lp.trade_date,'-','')) "
        "WHERE lp.pool_kind = 'limit_up' AND lp.trade_date >= ? AND lp.trade_date <= ? "
        "GROUP BY lp.trade_date"
    )
    raw = pd.read_sql_query(sql, conn, params=(db.to_dash_date(start), db.to_dash_date(end)))
    cal = db.trading_dates(conn, start, end)
    nxt = {}
    for i, d in enumerate(cal[:-1]):
        nxt[db.to_dash_date(d)] = cal[i + 1]
    rows = []
    for _, r in raw.iterrows():
        target = nxt.get(r["d0"])
        if target:
            rows.append({"trade_date": target, "premium": r["premium"],
                         "red_rate": r["red_rate"]})
    return pd.DataFrame(rows)


def _breadth_axes(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    breadth = pd.read_sql_query(
        "SELECT trade_date, "
        "SUM(CASE WHEN close > pre_close THEN 1 ELSE 0 END) AS up_cnt, "
        "COUNT(*) AS total, SUM(amount) AS amount_sum "
        "FROM daily_price WHERE trade_date >= ? AND trade_date <= ? "
        "GROUP BY trade_date",
        conn, params=(db.normalize_date(start), db.normalize_date(end)),
    )
    breadth["up_down_ratio"] = breadth["up_cnt"] / breadth["total"]
    nh = pd.read_sql_query(
        "SELECT trade_date, AVG(CASE WHEN close >= hh20 THEN 1.0 ELSE 0 END) "
        "AS new_high_ratio FROM ("
        "  SELECT trade_date, close, MAX(close) OVER "
        "  (PARTITION BY ts_code ORDER BY trade_date ROWS 19 PRECEDING) AS hh20 "
        "  FROM daily_price WHERE trade_date >= ? AND trade_date <= ?) "
        "GROUP BY trade_date",
        conn, params=(db.normalize_date(start), db.normalize_date(end)),
    )
    df = breadth.merge(nh, on="trade_date", how="left").drop(columns=["up_cnt", "total"])
    return df


def _index_axes(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    frames = []
    for code in ("000001.SH", "399001.SZ", "399006.SZ"):
        idx = db.read_index_daily(conn, code, start, end)
        if idx.empty:
            continue
        ma20 = idx["close"].rolling(20).mean()
        ma60 = idx["close"].rolling(60).mean()
        idx["bull"] = (idx["close"] > ma20) & (ma20 > ma60)
        frames.append(idx[["trade_date", "bull"]])
    if not frames:
        return pd.DataFrame(columns=["trade_date", "index_ma_bull"])
    merged = frames[0]
    for f in frames[1:]:
        merged = merged.merge(f, on="trade_date", how="outer", suffixes=("", "_y"))
    bull_cols = [c for c in merged.columns if c.startswith("bull")]
    merged["index_ma_bull"] = merged[bull_cols].sum(axis=1) >= (len(bull_cols) / 2)
    return merged[["trade_date", "index_ma_bull"]]


def build_market_regime(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    cal = db.trading_dates(conn, start, end)
    base = pd.DataFrame({"trade_date": cal})
    parts = [
        _limit_axes(conn, start, end),
        _promotion_axes(conn, start, end),
        _premium_axes(conn, start, end),
        _breadth_axes(conn, start, end),
        _index_axes(conn, start, end),
    ]
    df = base
    for p in parts:
        df = df.merge(p, on="trade_date", how="left")
    df["regime_label"] = df.apply(classify_regime, axis=1)
    logger.info("market regime: {} 天 [{} → {}]", len(df), start, end)
    return df
```

性能注记：`_breadth_axes` 的窗口函数子查询对 670 万行 daily_price 单次扫描，实测若 >60s 则在执行期为 new_high_ratio 增加日期下限裁剪，语义不变。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_sentiment.py -v`
Expected: PASS 2 项

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/limitup/sentiment.py davis_analyzer/tests/test_limitup_sentiment.py
git commit -m "feat(limitup): 市场环境三轴（指数/宽度/情绪）+regime 四档冻结阈值"
```

---

### Task 9: 统计稳健性 robustness.py

**Files:**
- Create: `davis_analyzer/limitup/robustness.py`
- Test: `davis_analyzer/tests/test_limitup_robustness.py`

**Interfaces:**
- Produces:
  - 常量 `MIN_SAMPLE_RETURN = 30`，`MIN_SAMPLE_PROMOTION = 50`
  - `split_is_oos(events: pd.DataFrame, oos_start: str) -> tuple[pd.DataFrame, pd.DataFrame]`（按 trade_date YYYYMMDD 字典序切）
  - `perturb_factors() -> tuple[float, float]` → `(0.8, 1.2)`
  - `sufficient(counts: pd.Series, kind: str) -> pd.Series`（kind ∈ `"return"|"promotion"`，bool）
  - `direction_stable(baseline: float, *perturbed: float) -> bool`（符号全部一致）

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_limitup_robustness.py`：

```python
"""robustness.py 样本门槛/IS-OOS/扰动测试。"""

from __future__ import annotations

import pandas as pd

from davis_analyzer.limitup import robustness


def test_split_is_oos_no_overlap_ordered() -> None:
    ev = pd.DataFrame({"trade_date": ["20230101", "20250630", "20250701", "20260801"]})
    is_ev, oos_ev = robustness.split_is_oos(ev, "20250701")
    assert list(is_ev["trade_date"]) == ["20230101", "20250630"]
    assert list(oos_ev["trade_date"]) == ["20250701", "20260801"]


def test_sufficient_thresholds() -> None:
    counts = pd.Series([29, 30, 49, 50])
    assert list(robustness.sufficient(counts, "return")) == [False, True, True, True]
    assert list(robustness.sufficient(counts, "promotion")) == [False, False, False, True]


def test_direction_stable() -> None:
    assert robustness.direction_stable(0.02, 0.015, 0.03)
    assert robustness.direction_stable(-0.01, -0.02, -0.005)
    assert not robustness.direction_stable(0.02, -0.01, 0.03)
    assert robustness.perturb_factors() == (0.8, 1.2)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_robustness.py -v`
Expected: FAIL，模块不存在

- [ ] **Step 3: 实现 robustness.py**

```python
"""统计稳健性规范（规格 §7）：样本门槛、IS/OOS 切分、参数扰动、方向稳定性."""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_SAMPLE_RETURN = 30
MIN_SAMPLE_PROMOTION = 50


def split_is_oos(
    events: pd.DataFrame, oos_start: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    is_ev = events[events["trade_date"] < oos_start]
    oos_ev = events[events["trade_date"] >= oos_start]
    return is_ev.copy(), oos_ev.copy()


def perturb_factors() -> tuple[float, float]:
    return (0.8, 1.2)


def sufficient(counts: pd.Series, kind: str) -> pd.Series:
    floor = MIN_SAMPLE_RETURN if kind == "return" else MIN_SAMPLE_PROMOTION
    return counts.fillna(0) >= floor


def direction_stable(baseline: float, *perturbed: float) -> bool:
    signs = {np.sign(v) for v in (baseline, *perturbed)}
    return len(signs) == 1 and 0 not in signs
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_robustness.py -v`
Expected: PASS 3 项

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/limitup/robustness.py davis_analyzer/tests/test_limitup_robustness.py
git commit -m "feat(limitup): 统计稳健性工具（样本门槛/IS-OOS/±20%扰动/方向稳定）"
```

---

### Task 10: 事件研究 study.py + 报告 report.py + CLI study

**Files:**
- Create: `davis_analyzer/limitup/study.py`
- Create: `davis_analyzer/limitup/report.py`
- Modify: `davis_analyzer/limitup/cli.py`（追加 study 子命令）
- Test: `davis_analyzer/tests/test_limitup_study.py`

**Interfaces:**
- Consumes: `build_events`（Task 6 输出）、`attach_pattern_features`（Task 7）、`build_market_regime`（Task 8）、`robustness`（Task 9）。
- Produces:
  - `promotion_matrix(events: pd.DataFrame, by: list[str] | None = None) -> pd.DataFrame`（index: consecutive_boards[+by]；列：promo_rate, n）
  - `return_distribution(events: pd.DataFrame, by: list[str] | None = None) -> pd.DataFrame`（列：mean, median, win_rate, payoff, n；基于 ret_open_1）
  - `feature_effectiveness(events: pd.DataFrame, feature: str) -> pd.DataFrame`（分桶分布 + `sufficient` 标记列 `enough_sample`）
  - `regime_slices(events: pd.DataFrame, regime: pd.DataFrame) -> pd.DataFrame`（merge regime_label 后按档位分布 + 样本量）
  - report：`df_to_md_table(df: pd.DataFrame) -> str`、`write_report(path, title: str, sections: list[tuple[str, str]]) -> Path`
  - CLI：`python -m davis_analyzer.limitup study --start 20230101 --end 20260814 --oos-start 20250701`

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_limitup_study.py`：

```python
"""study.py 晋级率矩阵/收益分布/分桶有效性测试。"""

from __future__ import annotations

import pandas as pd

from davis_analyzer.limitup import report, study


def _ev(n: int = 40) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": [f"6000{i:02d}.SH" for i in range(n)],
        "trade_date": ["20240102"] * n,
        "consecutive_boards": [1] * 20 + [2] * 20,
        "ret_open_1": [0.03] * 10 + [-0.02] * 10 + [0.05] * 15 + [-0.06] * 5,
        "promoted": [True] * 12 + [False] * 8 + [True] * 10 + [False] * 10,
        "pattern_label": ["突破型"] * 20 + ["其他"] * 20,
    })


def test_promotion_matrix() -> None:
    m = study.promotion_matrix(_ev())
    assert list(m.index) == [1, 2]
    assert abs(m.loc[1, "promo_rate"] - 0.6) < 1e-9
    assert m.loc[1, "n"] == 20


def test_return_distribution_stats() -> None:
    d = study.return_distribution(_ev(), by=["consecutive_boards"])
    row1 = d[d["consecutive_boards"] == 1].iloc[0]
    assert abs(row1["mean"] - 0.005) < 1e-9  # (10*0.03 + 10*-0.02)/20
    assert row1["win_rate"] == 0.5
    assert row1["n"] == 20


def test_feature_effectiveness_flags_small_sample() -> None:
    df = study.feature_effectiveness(_ev(10), "pattern_label")
    assert set(df["pattern_label"]) == {"突破型"}
    assert not bool(df.iloc[0]["enough_sample"])  # 10 < 30


def test_report_writes_markdown(tmp_path) -> None:
    tbl = pd.DataFrame({"a": [1, 2], "b": [0.5, -0.25]})
    md = report.df_to_md_table(tbl)
    assert "| a | b |" in md and "|---|---|" in md and "| 1 | 0.5 |" in md
    out = report.write_report(tmp_path / "r.md", "测试", [("小节", md)])
    assert out.exists() and "测试" in out.read_text(encoding="utf-8")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_study.py -v`
Expected: FAIL，模块不存在

- [ ] **Step 3: 实现 study.py**

```python
"""事件研究：晋级率矩阵、打板收益分布、特征有效性、环境切片（规格 §8）."""

from __future__ import annotations

import pandas as pd
from loguru import logger

from davis_analyzer.limitup import robustness


def _dist(g: pd.DataFrame) -> pd.Series:
    r = g["ret_open_1"].dropna()
    pos, neg = r[r > 0], r[r <= 0]
    return pd.Series({
        "mean": r.mean(),
        "median": r.median(),
        "win_rate": (r > 0).mean() if len(r) else float("nan"),
        "payoff": pos.mean() / abs(neg.mean()) if len(pos) and len(neg) else float("nan"),
        "n": len(r),
    })


def return_distribution(
    events: pd.DataFrame, by: list[str] | None = None
) -> pd.DataFrame:
    keys = by or []
    if keys:
        out = events.groupby(keys, dropna=False).apply(
            lambda g: _dist(g), include_groups=False
        ).reset_index()
    else:
        out = _dist(events).to_frame().T
    out["enough_sample"] = robustness.sufficient(out["n"], "return")
    return out


def promotion_matrix(
    events: pd.DataFrame, by: list[str] | None = None
) -> pd.DataFrame:
    keys = ["consecutive_boards", *(by or [])]
    out = (
        events.groupby(keys, dropna=False)
        .agg(promo_rate=("promoted", "mean"), n=("promoted", "size"))
        .reset_index()
        .set_index("consecutive_boards" if not by else keys)
    )
    out["enough_sample"] = robustness.sufficient(out["n"], "promotion")
    return out


def feature_effectiveness(events: pd.DataFrame, feature: str) -> pd.DataFrame:
    return return_distribution(events, by=[feature])


def regime_slices(events: pd.DataFrame, regime: pd.DataFrame) -> pd.DataFrame:
    merged = events.merge(regime[["trade_date", "regime_label"]],
                          on="trade_date", how="inner")
    if merged.empty:
        logger.warning("regime_slices: 无可合并事件")
        return pd.DataFrame()
    out = return_distribution(merged, by=["regime_label"])
    order = [x for x in ("冰点", "回暖", "高潮", "退潮") if x in set(out["regime_label"])]
    return out.set_index("regime_label").reindex(order).reset_index()
```

`report.py`：

```python
"""Markdown 报告输出（study/backtest 共用）."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def df_to_md_table(df: pd.DataFrame) -> str:
    def _fmt(v: object) -> str:
        if isinstance(v, float):
            return f"{v:.4f}"
        return str(v)

    lines = ["| " + " | ".join(str(c) for c in df.columns) + " |",
             "|" + "|".join(["---"] * len(df.columns)) + "|"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(_fmt(v) for v in row) + " |")
    return "\n".join(lines)


def write_report(
    path: Path, title: str, sections: list[tuple[str, str]]
) -> Path:
    parts = [f"# {title}", ""]
    for heading, body in sections:
        parts += [f"## {heading}", "", body, ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    return path
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_study.py -v`
Expected: PASS 4 项

- [ ] **Step 5: 接入 CLI study 子命令**

`cli.py` 追加：

```python
def cmd_study(args: argparse.Namespace) -> None:
    from davis_analyzer import config
    from davis_analyzer.limitup import db, patterns, report, study
    from davis_analyzer.limitup.events import build_events
    from davis_analyzer.limitup.robustness import split_is_oos
    from davis_analyzer.limitup.sentiment import build_market_regime

    conn = db.connect()
    try:
        events = build_events(conn, args.start, args.end)
        events = patterns.attach_pattern_features(events, conn, args.start, args.end)
        regime = build_market_regime(conn, args.start, args.end)
        is_ev, oos_ev = split_is_oos(events, args.oos_start)
        sections = [
            ("数据概览", f"事件数 IS={len(is_ev)} / OOS={len(oos_ev)}；"
                        f"样本门槛：收益类≥30、晋级率类≥50（不足标记样本不足）"),
            ("晋级率矩阵（全样本）",
             report.df_to_md_table(study.promotion_matrix(events).reset_index())),
            ("晋级率矩阵 × 形态标签",
             report.df_to_md_table(
                 study.promotion_matrix(events, by=["pattern_label"]).reset_index())),
            ("打板次日开盘收益分布（全样本）",
             report.df_to_md_table(study.return_distribution(events))),
            ("形态标签收益分布",
             report.df_to_md_table(
                 study.feature_effectiveness(events, "pattern_label"))),
            ("龙虎榜有效性",
             report.df_to_md_table(study.feature_effectiveness(
                 events.assign(上榜=lambda d: d["on_lhb"].map({True: "上榜", False: "未榜"})),
                 "上榜"))),
            ("封单强度分档有效性",
             report.df_to_md_table(study.feature_effectiveness(
                 events.assign(封档=lambda d: pd.cut(
                     d["seal_ratio"], [-1, 0.02, 0.05, 100],
                     labels=["弱", "中", "强"])), "封档"))),
            ("情绪 regime 切片",
             report.df_to_md_table(study.regime_slices(events, regime))),
        ]
        out = report.write_report(
            config.LIMITUP_REPORTS_DIR / f"{args.start}-{args.end}_limitup_study.md",
            f"连板打板事件研究 [{args.start} → {args.end}]",
            sections,
        )
        print(f"研究报告已生成: {out}")
    finally:
        conn.close()
```

并在 `main()` 的 `sub` 注册区追加：

```python
    p_st = sub.add_parser("study", help="涨停事件研究（Phase 1）")
    p_st.add_argument("--start", required=True, help="YYYYMMDD")
    p_st.add_argument("--end", required=True, help="YYYYMMDD")
    p_st.add_argument("--oos-start", default="20250701",
                      help="IS/OOS 切分日（默认 20250701）")
    p_st.set_defaults(func=cmd_study)
```

冒烟：`cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m davis_analyzer.limitup study --help`（不跑真实数据，真实数据依赖 Task 3 Step 7 回补结果）。

- [ ] **Step 6: 提交**

```bash
git add davis_analyzer/limitup/study.py davis_analyzer/limitup/report.py davis_analyzer/limitup/cli.py davis_analyzer/tests/test_limitup_study.py
git commit -m "feat(limitup): 事件研究引擎+markdown 报告+CLI study"
```

---

### Task 11: 策略预设 strategies.py

**Files:**
- Create: `davis_analyzer/limitup/strategies.py`
- Test: `davis_analyzer/tests/test_limitup_strategies.py`

**Interfaces:**
- Consumes: `build_events`+`attach_pattern_features` 输出列、`build_market_regime` 的 `regime_label`。
- Produces:
  - `class ExitRule(str, Enum)`：`OPEN_NEXT = "open_next"` / `RIDE_BOARD = "ride_board"` / `CLOSE_NEXT = "close_next"`
  - `@dataclass(frozen=True) class StrategyPreset`：字段 `name: str, board_range: tuple[int, int], pattern_labels: tuple[str, ...] | None, exit_rule: ExitRule, rank_key: str = "seal_ratio", regime_allow: tuple[str, ...] | None = ("回暖", "高潮"), min_seal_ratio: float | None = None, min_sector_linkage: int | None = None, exclude_negative_event: bool = True`
  - `PRESETS: dict[str, StrategyPreset]`：`first_board`（板数 1，形态 突破型/横盘首板型，OPEN_NEXT）、`relay_2`（板数 2–2，形态 None，RIDE_BOARD）、`relay_3`（板数 3–3，形态 None，RIDE_BOARD）
  - `apply_preset(events, preset, regime=None, *, seal_ratio_median: float | None = None) -> pd.DataFrame`（过滤后候选；激活过滤条件 >4 时 `raise ValueError("过滤条件超过预算(4)")`）

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_limitup_strategies.py`：

```python
"""strategies.py 预设过滤与预算约束测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from davis_analyzer.limitup.strategies import PRESETS, ExitRule, apply_preset


def _ev() -> pd.DataFrame:
    return pd.DataFrame([
        {"ts_code": "600001.SH", "trade_date": "20240102", "consecutive_boards": 1,
         "pattern_label": "突破型", "seal_ratio": 0.03, "sector_linkage": 3,
         "negative_event_30d": False, "ret_open_1": 0.01, "promoted": True},
        {"ts_code": "600002.SH", "trade_date": "20240102", "consecutive_boards": 1,
         "pattern_label": "其他", "seal_ratio": 0.01, "sector_linkage": 1,
         "negative_event_30d": False, "ret_open_1": -0.01, "promoted": False},
        {"ts_code": "600003.SH", "trade_date": "20240102", "consecutive_boards": 2,
         "pattern_label": "其他", "seal_ratio": 0.06, "sector_linkage": 2,
         "negative_event_30d": True, "ret_open_1": 0.02, "promoted": True},
    ])


def test_first_board_filters() -> None:
    out = apply_preset(_ev(), PRESETS["first_board"])
    assert list(out["ts_code"]) == ["600001.SH"]


def test_relay_2_median_filter() -> None:
    out = apply_preset(_ev(), PRESETS["relay_2"], seal_ratio_median=0.05)
    assert list(out["ts_code"]) == ["600003.SH"]


def test_regime_filter() -> None:
    regime = pd.DataFrame([{"trade_date": "20240102", "regime_label": "冰点"}])
    out = apply_preset(_ev(), PRESETS["first_board"], regime=regime)
    assert out.empty


def test_filter_budget_raises() -> None:
    preset = PRESETS["relay_2"]
    with pytest.raises(ValueError, match="过滤条件超过预算"):
        apply_preset(_ev(), preset, min_sector_linkage=2,
                     seal_ratio_median=0.01)  # 板数+seal+联动 = 3 → ok 不触发
    # 构造 5 条件触发：直接换 preset 字段
    from dataclasses import replace
    fat = replace(preset, pattern_labels=("突破型",), regime_allow=("回暖",),
                  exclude_negative_event=True)
    with pytest.raises(ValueError, match="过滤条件超过预算"):
        apply_preset(_ev(), fat, min_sector_linkage=2, seal_ratio_median=0.01)


def test_exit_rules_defined() -> None:
    assert PRESETS["first_board"].exit_rule is ExitRule.OPEN_NEXT
    assert PRESETS["relay_2"].exit_rule is ExitRule.RIDE_BOARD
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_strategies.py -v`
Expected: FAIL，模块不存在

- [ ] **Step 3: 实现 strategies.py**

```python
"""策略预设（首板/接力）与过滤预算（规格 §9.5/§7.6）."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

import pandas as pd
from loguru import logger

FILTER_BUDGET = 4


class ExitRule(str, Enum):
    OPEN_NEXT = "open_next"
    RIDE_BOARD = "ride_board"
    CLOSE_NEXT = "close_next"


@dataclass(frozen=True)
class StrategyPreset:
    name: str
    board_range: tuple[int, int]
    pattern_labels: tuple[str, ...] | None
    exit_rule: ExitRule
    rank_key: str = "seal_ratio"
    regime_allow: tuple[str, ...] | None = ("回暖", "高潮")
    min_seal_ratio: float | None = None
    min_sector_linkage: int | None = None
    exclude_negative_event: bool = True


PRESETS: dict[str, StrategyPreset] = {
    "first_board": StrategyPreset(
        name="首板启动", board_range=(1, 1),
        pattern_labels=("突破型", "横盘首板型"), exit_rule=ExitRule.OPEN_NEXT,
    ),
    "relay_2": StrategyPreset(
        name="二板接力", board_range=(2, 2), pattern_labels=None,
        exit_rule=ExitRule.RIDE_BOARD,
    ),
    "relay_3": StrategyPreset(
        name="三板接力", board_range=(3, 3), pattern_labels=None,
        exit_rule=ExitRule.RIDE_BOARD,
    ),
}


def apply_preset(
    events: pd.DataFrame,
    preset: StrategyPreset,
    regime: pd.DataFrame | None = None,
    *,
    seal_ratio_median: float | None = None,
    min_sector_linkage: int | None = None,
) -> pd.DataFrame:
    eff = replace(preset)
    if min_sector_linkage is not None:
        eff = replace(eff, min_sector_linkage=min_sector_linkage)
    n_filters = sum([
        True,  # board_range 恒为第 1 个过滤
        eff.pattern_labels is not None,
        eff.regime_allow is not None,
        seal_ratio_median is not None or eff.min_seal_ratio is not None,
        eff.min_sector_linkage is not None,
        eff.exclude_negative_event,
    ]) - 1
    if n_filters > FILTER_BUDGET:
        raise ValueError(f"过滤条件超过预算({FILTER_BUDGET})：当前 {n_filters} 条")

    ev = events.copy()
    lo, hi = eff.board_range
    mask = ev["consecutive_boards"].between(lo, hi)
    if eff.pattern_labels is not None:
        mask &= ev["pattern_label"].isin(eff.pattern_labels)
    if eff.regime_allow is not None and regime is not None:
        allowed = regime[regime["regime_label"].isin(eff.regime_allow)]["trade_date"]
        mask &= ev["trade_date"].isin(set(allowed))
    thr = eff.min_seal_ratio if eff.min_seal_ratio is not None else seal_ratio_median
    if thr is not None:
        mask &= ev["seal_ratio"].fillna(0) >= thr
    if eff.min_sector_linkage is not None:
        mask &= ev["sector_linkage"].fillna(0) >= eff.min_sector_linkage
    if eff.exclude_negative_event:
        mask &= ~ev["negative_event_30d"].fillna(False)
    out = ev[mask].sort_values(
        ["trade_date", eff.rank_key], ascending=[True, False]
    )
    logger.info("preset[{}]: {} → {} 候选", eff.name, len(ev), len(out))
    return out.reset_index(drop=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_strategies.py -v`
Expected: PASS 5 项

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/limitup/strategies.py davis_analyzer/tests/test_limitup_strategies.py
git commit -m "feat(limitup): 策略预设（首板/二板/三板）+过滤条件预算约束"
```

---

### Task 12: 事件驱动回测引擎 engine.py（核心循环）

**Files:**
- Create: `davis_analyzer/limitup/engine.py`
- Test: `davis_analyzer/tests/test_limitup_engine.py`

**Interfaces:**
- Consumes: `strategies.apply_preset` 输出（候选，含 `ts_code/trade_date/limit_price/first_seal_time/broken_count/open/low/close/pre_close/rank_key 列`）；`backtest._trade_cost`（`_trade_cost(gross: float, commission_bps: float, stamp_tax_bps: float, is_sell: bool) -> float`）；`db.trading_dates`。
- Produces:
  - `SCENARIOS: dict[str, float]` = `{"base": 1.0, "optimistic": 1.5, "pessimistic": 0.5, "always": 1.0}`
  - `@dataclass(frozen=True) class LimitupBacktestConfig`：`initial_capital: float = 1_000_000.0, max_positions: int = 3, commission_bps: float = 2.5, stamp_tax_bps: float = 10.0, slippage_bps: float = 10.0`
  - `@dataclass class TradeRecord`：`ts_code, entry_date, entry_price, shares, exit_date, exit_price, exit_reason, fill_scenario, gross_pnl, fees, ret_pct`
  - `fill_probability(row: pd.Series, scenario: str = "base") -> float`（一字板 0.05 / 早盘硬板 0.20 / 普通未炸 0.35 / 炸板回封 0.70，×场景系数，clip [0.05, 0.95]，`always` 恒 1.0）
  - `run_backtest(candidates, prices, preset, config, scenario="base", seed=42) -> tuple[list[TradeRecord], pd.DataFrame]`（返回闭合交易列表与逐日净值 DataFrame[date, cash, equity]）
  - 卖出语义：`open_next` T+1 开盘卖；`ride_board` 持有日收盘继续涨停则顺延、断板次日开盘卖；`close_next` T+1 收盘卖；**一字跌停（open==low 且 open≈跌停价）无法卖出顺延下一交易日**；期末强制平仓（`exit_reason="期末"`，按末日收盘）。

- [ ] **Step 1: 写失败测试**

`davis_analyzer/tests/test_limitup_engine.py`：

```python
"""engine.py 成交概率/主循环/跌停顺延测试。"""

from __future__ import annotations

import pandas as pd

from davis_analyzer.limitup.engine import (
    LimitupBacktestConfig, TradeRecord, fill_probability, run_backtest,
)
from davis_analyzer.limitup.strategies import PRESETS


def _cand(**kw) -> pd.DataFrame:
    base = {
        "ts_code": "600001.SH", "trade_date": "20240102", "limit_price": 11.0,
        "first_seal_time": "093000", "broken_count": 0, "open": 10.2, "low": 10.0,
        "close": 11.0, "pre_close": 10.0, "seal_ratio": 0.05,
    }
    base.update(kw)
    return pd.DataFrame([base])


def _prices() -> pd.DataFrame:
    return pd.DataFrame([
        # 0102 打板日：open 10.2 low 10.0 close 11.0（涨停）
        ("600001.SH", "20240102", 10.2, 11.0, 10.0, 11.0, 10.0),
        # 0103 正常回落：open 10.8 → open_next 以 10.8 卖
        ("600001.SH", "20240103", 10.8, 11.2, 10.5, 10.9, 11.0),
        ("600001.SH", "20240104", 10.9, 11.0, 10.6, 10.7, 10.9),
    ], columns=["ts_code", "trade_date", "open", "high", "low", "close", "pre_close"])


def test_fill_probability_bands() -> None:
    hard = _cand().iloc[0]              # 早盘未炸板
    yizi = _cand(open=11.0, low=11.0).iloc[0]   # 一字板
    broken = _cand(broken_count=2, first_seal_time="140000").iloc[0]
    assert fill_probability(hard) == 0.35
    assert fill_probability(yizi) == 0.05
    assert fill_probability(broken) == 0.70
    assert fill_probability(hard, "optimistic") == 0.525
    assert fill_probability(hard, "always") == 1.0


def test_open_next_roundtrip_with_fees() -> None:
    cfg = LimitupBacktestConfig()
    trades, nav = run_backtest(
        _cand(), _prices(), PRESETS["first_board"], cfg, scenario="always", seed=1
    )
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_date == "20240102" and t.exit_date == "20240103"
    assert t.shares % 100 == 0 and t.shares > 0
    # 卖出价含滑点 10bps：10.8 * (1 - 1e-3)
    assert abs(t.exit_price - 10.8 * (1 - 10 / 1e4)) < 1e-9
    assert t.ret_pct < (10.8 / 11.0 - 1)  # 费用+滑点拖累
    assert list(nav.columns) == ["date", "cash", "equity"]
    assert nav["equity"].iloc[-1] > 0


def test_limit_down_postpones_sell() -> None:
    prices = pd.DataFrame([
        ("600001.SH", "20240102", 10.2, 11.0, 10.0, 11.0, 10.0),
        # 0103 一字跌停：open=low=9.9 = round(11.0*0.9,2)
        ("600001.SH", "20240103", 9.9, 9.9, 9.9, 9.9, 11.0),
        ("600001.SH", "20240104", 9.5, 9.8, 9.4, 9.7, 9.9),
    ], columns=["ts_code", "trade_date", "open", "high", "low", "close", "pre_close"])
    trades, _ = run_backtest(
        _cand(), prices, PRESETS["first_board"], LimitupBacktestConfig(),
        scenario="always", seed=1,
    )
    assert trades[0].exit_date == "20240104"  # 0103 无法卖 → 顺延
    assert abs(trades[0].exit_price - 9.5 * (1 - 10 / 1e4)) < 1e-9


def test_ride_board_holds_through_boards() -> None:
    cand = _cand(consecutive_boards=2)
    prices = pd.DataFrame([
        ("600001.SH", "20240102", 10.2, 11.0, 10.0, 11.0, 10.0),
        # 0103 继续涨停（close = 12.1 = 11.0*1.1）
        ("600001.SH", "20240103", 11.5, 12.1, 11.4, 12.1, 11.0),
        # 0104 断板 → 0105 开盘卖
        ("600001.SH", "20240104", 12.5, 13.0, 12.0, 12.4, 12.1),
        ("600001.SH", "20240105", 12.2, 12.6, 12.0, 12.3, 12.4),
    ], columns=["ts_code", "trade_date", "open", "high", "low", "close", "pre_close"])
    trades, _ = run_backtest(
        cand, prices, PRESETS["relay_2"], LimitupBacktestConfig(),
        scenario="always", seed=1,
    )
    assert trades[0].exit_date == "20240105"
    assert abs(trades[0].exit_price - 12.2 * (1 - 10 / 1e4)) < 1e-9


def test_pessimistic_scenario_reduces_fills() -> None:
    # 弱成交场景下大概率买不进：多 seed 统计成交次数不高于乐观场景
    cfg = LimitupBacktestConfig()
    n_pess = n_opt = 0
    for seed in range(20):
        tp, _ = run_backtest(_cand(), _prices(), PRESETS["first_board"], cfg,
                             scenario="pessimistic", seed=seed)
        to, _ = run_backtest(_cand(), _prices(), PRESETS["first_board"], cfg,
                             scenario="optimistic", seed=seed)
        n_pess += len(tp)
        n_opt += len(to)
    assert n_pess <= n_opt
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_engine.py -v`
Expected: FAIL，模块不存在

- [ ] **Step 3: 实现 engine.py**

```python
"""事件驱动打板回测引擎：T 日涨停价打板（概率成交）→ T+1 起按规则离场（规格 §9）."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.backtest import _trade_cost
from davis_analyzer.limitup.events import limit_ratio_for
from davis_analyzer.limitup.strategies import ExitRule, StrategyPreset

SCENARIOS: dict[str, float] = {
    "base": 1.0, "optimistic": 1.5, "pessimistic": 0.5, "always": 1.0,
}


@dataclass(frozen=True)
class LimitupBacktestConfig:
    initial_capital: float = 1_000_000.0
    max_positions: int = 3
    commission_bps: float = 2.5
    stamp_tax_bps: float = 10.0
    slippage_bps: float = 10.0


@dataclass
class TradeRecord:
    ts_code: str
    entry_date: str
    entry_price: float
    shares: int
    exit_date: str
    exit_price: float
    exit_reason: str
    fill_scenario: str
    gross_pnl: float
    fees: float
    ret_pct: float


# ── fill model ──

def fill_probability(row: pd.Series, scenario: str = "base") -> float:
    if scenario == "always":
        return 1.0
    ratio = limit_ratio_for(row["ts_code"])
    limit_up = round(float(row["pre_close"]) * (1 + ratio) + 1e-9, 2)
    yizi = (abs(float(row["open"]) - limit_up) <= 0.005) and (
        abs(float(row["low"]) - limit_up) <= 0.005
    )
    ft = str(row.get("first_seal_time", "") or "")
    if yizi:
        p = 0.05
    elif int(row.get("broken_count", 0) or 0) > 0:
        p = 0.70
    elif "090000" < ft < "100000":
        p = 0.20
    else:
        p = 0.35
    return float(min(0.95, max(0.05, p * SCENARIOS[scenario])))


# ── main loop ──

def run_backtest(
    candidates: pd.DataFrame,
    prices: pd.DataFrame,
    preset: StrategyPreset,
    config: LimitupBacktestConfig,
    scenario: str = "base",
    seed: int = 42,
) -> tuple[list[TradeRecord], pd.DataFrame]:
    rng = np.random.default_rng(seed)
    px = {
        code: g.set_index("trade_date").sort_index()
        for code, g in prices.groupby("ts_code")
    }
    cand_by_date: dict[str, pd.DataFrame] = {
        d: g for d, g in candidates.groupby("trade_date")
    }
    all_dates = sorted(set(prices["trade_date"]) | set(candidates["trade_date"]))
    if not all_dates:
        return [], pd.DataFrame(columns=["date", "cash", "equity"])

    cash = config.initial_capital
    positions: dict[str, dict] = {}   # code -> 持仓与卖出计划
    trades: list[TradeRecord] = []
    nav_rows: list[dict] = []

    def _next_day(d: str, dates: list[str]) -> str | None:
        i = dates.index(d)
        return dates[i + 1] if i + 1 < len(dates) else None

    def _limit_down_locked(code: str, d: str) -> bool:
        row = px[code].loc[d]
        ratio = limit_ratio_for(code)
        limit_down = round(float(row["pre_close"]) * (1 - ratio) + 1e-9, 2)
        return abs(float(row["open"]) - limit_down) <= 0.005 and abs(
            float(row["low"]) - limit_down
        ) <= 0.005

    for d in all_dates:
        # 1) 执行卖出计划（open 类）
        for code in list(positions):
            pos = positions[code]
            if pos.get("sell_on") != d or pos.get("exec") != "open":
                continue
            if code not in px or d not in px[code].index:
                continue  # 停牌：顺延
            if _limit_down_locked(code, d):
                nxt = _next_day(d, all_dates)
                if nxt:
                    pos["sell_on"] = nxt
                    logger.info("{} {} 一字跌停无法卖出，顺延", code, d)
                continue
            exec_px = float(px[code].loc[d]["open"]) * (1 - config.slippage_bps / 1e4)
            _close_position(code, pos, d, exec_px, "规则卖出", scenario, config,
                            positions, trades)
            cash += pos["_cash_credit"]  # 由 _close_position 记录
        # 2) ride_board 收盘评估
        for code, pos in positions.items():
            if pos["exit_rule"] != ExitRule.RIDE_BOARD or pos.get("sell_on"):
                continue
            if code in px and d in px[code].index and d > pos["entry_date"]:
                row = px[code].loc[d]
                if not _closed_limit_up(code, row):
                    nxt = _next_day(d, all_dates)
                    if nxt:
                        pos["sell_on"] = nxt
                        pos["exec"] = "open"
        # 3) close_next 收盘卖出
        for code in list(positions):
            pos = positions[code]
            if pos.get("sell_on") == d and pos.get("exec") == "close":
                if code in px and d in px[code].index:
                    exec_px = float(px[code].loc[d]["close"])
                    _close_position(code, pos, d, exec_px, "规则卖出", scenario,
                                    config, positions, trades)
                    cash += pos["_cash_credit"]
        # 4) 打板买入（先卖后买，空出的 slot 当日可用）
        slots = config.max_positions - len(positions)
        if slots > 0 and d in cand_by_date:
            ranked = cand_by_date[d].sort_values(
                preset.rank_key if preset.rank_key in cand_by_date[d].columns
                else "seal_ratio", ascending=False
            )
            equity_now = cash + _positions_market_value(positions, px, d)
            per_slot = equity_now / config.max_positions
            taken = 0
            for _, row in ranked.iterrows():
                if taken >= slots:
                    break
                code = row["ts_code"]
                if code in positions:
                    continue
                if fill_probability(row, scenario) < rng.random():
                    continue  # 排队未成交
                price = float(row["limit_price"])
                shares = int(per_slot / price // 100) * 100
                if shares < 100 or shares * price > cash:
                    continue
                gross = shares * price
                fee = _trade_cost(gross, config.commission_bps, config.stamp_tax_bps, False)
                cash -= gross + fee
                nxt = _next_day(d, all_dates)
                pos = {
                    "shares": shares, "entry_date": d, "entry_price": price,
                    "entry_fee": fee, "exit_rule": preset.exit_rule,
                    "sell_on": None, "exec": None, "_cash_credit": 0.0,
                    "last_close": price,
                }
                if preset.exit_rule is ExitRule.OPEN_NEXT:
                    pos["sell_on"], pos["exec"] = nxt, "open"
                elif preset.exit_rule is ExitRule.CLOSE_NEXT:
                    pos["sell_on"], pos["exec"] = nxt, "close"
                positions[code] = pos
                taken += 1
        # 5) 收盘 MTM
        for code, pos in positions.items():
            if code in px and d in px[code].index:
                pos["last_close"] = float(px[code].loc[d]["close"])
        equity = cash + _positions_market_value(positions, px, d)
        nav_rows.append({"date": d, "cash": cash, "equity": equity})

    # 期末强平
    last = all_dates[-1]
    for code in list(positions):
        pos = positions[code]
        exec_px = pos["last_close"]
        _close_position(code, pos, last, exec_px, "期末", scenario, config,
                        positions, trades)
    logger.info("backtest[{}] {} 笔交易", scenario, len(trades))
    return trades, pd.DataFrame(nav_rows)


def _closed_limit_up(code: str, row: pd.Series) -> bool:
    ratio = limit_ratio_for(code)
    limit_up = round(float(row["pre_close"]) * (1 + ratio) + 1e-9, 2)
    return abs(float(row["close"]) - limit_up) <= 0x1.3670a3p-10  # ≈0.005 容差


def _positions_market_value(
    positions: dict[str, dict], px: dict[str, pd.DataFrame], d: str
) -> float:
    total = 0.0
    for code, pos in positions.items():
        total += pos["shares"] * pos["last_close"]
    return total


def _close_position(
    code: str, pos: dict, d: str, exec_px: float, reason: str,
    scenario: str, config: LimitupBacktestConfig,
    positions: dict[str, dict], trades: list[TradeRecord],
) -> None:
    gross = pos["shares"] * exec_px
    fee = _trade_cost(gross, config.commission_bps, config.stamp_tax_bps, True)
    net = gross - fee
    buy_gross = pos["shares"] * pos["entry_price"]
    fees = fee + pos["entry_fee"]
    trades.append(
        TradeRecord(
            ts_code=code, entry_date=pos["entry_date"], entry_price=pos["entry_price"],
            shares=pos["shares"], exit_date=d, exit_price=exec_px,
            exit_reason=reason, fill_scenario=scenario,
            gross_pnl=net - buy_gross - pos["entry_fee"], fees=fees,
            ret_pct=(net - buy_gross - pos["entry_fee"])
            / (buy_gross + pos["entry_fee"]),
        )
    )
    pos["_cash_credit"] = net
    del positions[code]
```

实现说明两点（照做，不要"优化"）：
1. `_closed_limit_up` 中 `0x1.3670a3p-10` 是 0.005 的浮点误差安全写法，直接写 `<= 0.0051` 也行——统一改成 `<= 0.0051`，与 `fill_probability` 保持一致。
2. 卖出现金流通过 `pos["_cash_credit"]` 回传并在主循环 `cash +=` —— 保持 `_close_position` 无返回值的签名不变，期末强平后不 `cash +=`（净值已按 last_close MTM，不影响 stats 计算的交易记录正确性）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_engine.py -v`
Expected: PASS 5 项

- [ ] **Step 5: 提交**

```bash
git add davis_analyzer/limitup/engine.py davis_analyzer/tests/test_limitup_engine.py
git commit -m "feat(limitup): 事件驱动回测引擎（概率成交/T+1规则卖出/一字跌停顺延）"
```

---

### Task 13: 绩效统计 + 三档敏感性 + CLI backtest + 收尾

**Files:**
- Modify: `davis_analyzer/limitup/engine.py`（追加绩效与敏感性函数）
- Modify: `davis_analyzer/limitup/cli.py`（追加 backtest 子命令）
- Modify: `AGENTS.md`（模块划分图追加 limitup 一行）
- Test: `davis_analyzer/tests/test_limitup_engine.py`（追加测试）

**Interfaces:**
- Consumes: `run_backtest`/`TradeRecord`（Task 12）、`backtest_report.PerformanceStats`（字段：total_return_pct, annualized_return_pct, sharpe_ratio, max_drawdown_pct, win_rate_pct, turnover_per_rebalance, num_trades, num_rebalances, avg_holding_count, total_cost）、Task 10/11 全链路。
- Produces:
  - `compute_limitup_performance(nav: pd.DataFrame, trades: list[TradeRecord], n_signal_days: int) -> PerformanceStats`（`turnover_per_rebalance=0.0`、`num_rebalances=n_signal_days`）
  - `run_sensitivity(candidates, prices, preset, config, seed=42) -> dict[str, PerformanceStats]`（键：pessimistic/base/optimistic/always）
  - CLI：`python -m davis_analyzer.limitup backtest --preset first_board --start 20230101 --end 20260814 [--fill-scenario base] [--seed 42]`，输出：三档敏感性报告 md + 交易明细 CSV（均写入 `LIMITUP_REPORTS_DIR`）。

- [ ] **Step 1: 追加失败测试**

追加到 `test_limitup_engine.py`：

```python
def test_compute_limitup_performance() -> None:
    from davis_analyzer.backtest_report import PerformanceStats
    from davis_analyzer.limitup.engine import compute_limitup_performance

    nav = pd.DataFrame({
        "date": ["20240102", "20240103", "20240104"],
        "cash": [1e6, 1e6, 1e6], "equity": [1e6, 1.01e6, 1.02e6],
    })
    trades = [
        TradeRecord("A", "20240102", 10.0, 100, "20240103", 10.5, "规则卖出",
                    "base", 40.0, 6.0, 0.04),
        TradeRecord("B", "20240102", 10.0, 100, "20240103", 9.5, "规则卖出",
                    "base", -60.0, 6.0, -0.06),
    ]
    stats = compute_limitup_performance(nav, trades, n_signal_days=2)
    assert isinstance(stats, PerformanceStats)
    assert stats.num_trades == 2
    assert stats.win_rate_pct == 50.0
    assert stats.total_return_pct == 2.0
    assert stats.num_rebalances == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_engine.py -v`
Expected: 新测试 FAIL

- [ ] **Step 3: 实现绩效与敏感性**

追加到 `engine.py`：

```python
# ── performance & sensitivity ──

def compute_limitup_performance(
    nav: pd.DataFrame, trades: list[TradeRecord], n_signal_days: int
) -> "PerformanceStats":
    from davis_analyzer.backtest_report import PerformanceStats

    eq = nav["equity"].astype(float)
    total_ret = eq.iloc[-1] / eq.iloc[0] - 1
    n_days = max(len(eq) - 1, 1)
    ann = (1 + total_ret) ** (252 / n_days) - 1 if total_ret > -1 else -1.0
    daily = eq.pct_change().dropna()
    sharpe = (
        float(daily.mean() / daily.std() * np.sqrt(252))
        if len(daily) > 1 and daily.std() > 0 else 0.0
    )
    drawdown = eq / eq.cummax() - 1
    wins = [t for t in trades if t.ret_pct > 0]
    total_cost = sum(t.fees for t in trades)
    return PerformanceStats(
        total_return_pct=total_ret * 100,
        annualized_return_pct=ann * 100,
        sharpe_ratio=sharpe,
        max_drawdown_pct=float(drawdown.min()) * 100,
        win_rate_pct=len(wins) / len(trades) * 100 if trades else 0.0,
        turnover_per_rebalance=0.0,
        num_trades=len(trades),
        num_rebalances=n_signal_days,
        avg_holding_count=0.0,
        total_cost=total_cost,
    )


def run_sensitivity(
    candidates: pd.DataFrame, prices: pd.DataFrame, preset: StrategyPreset,
    config: LimitupBacktestConfig, seed: int = 42,
) -> dict[str, "PerformanceStats"]:
    out: dict[str, PerformanceStats] = {}
    n_signal_days = candidates["trade_date"].nunique() if len(candidates) else 0
    for scenario in ("pessimistic", "base", "optimistic", "always"):
        trades, nav = run_backtest(
            candidates, prices, preset, config, scenario=scenario, seed=seed
        )
        out[scenario] = compute_limitup_performance(nav, trades, n_signal_days)
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/test_limitup_engine.py -v`
Expected: PASS 6 项

- [ ] **Step 5: CLI backtest 子命令 + AGENTS.md 更新**

`cli.py` 追加：

```python
def cmd_backtest(args: argparse.Namespace) -> None:
    import pandas as pd

    from davis_analyzer import config
    from davis_analyzer.limitup import db, engine, patterns, report
    from davis_analyzer.limitup.engine import LimitupBacktestConfig, run_sensitivity
    from davis_analyzer.limitup.events import build_events
    from davis_analyzer.limitup.robustness import split_is_oos
    from davis_analyzer.limitup.sentiment import build_market_regime
    from davis_analyzer.limitup.strategies import PRESETS, apply_preset

    conn = db.connect()
    try:
        events = build_events(conn, args.start, args.end)
        events = patterns.attach_pattern_features(events, conn, args.start, args.end)
        regime = build_market_regime(conn, args.start, args.end)
        is_ev, _ = split_is_oos(events, args.oos_start)
        preset = PRESETS[args.preset]
        seal_med = float(is_ev["seal_ratio"].median()) if len(is_ev) else None
        candidates = apply_preset(
            events, preset, regime=regime, seal_ratio_median=seal_med
        )
        prices = db.read_daily_prices(
            conn, sorted(candidates["ts_code"].unique()),
            args.start, args.end,
        )
        cfg = LimitupBacktestConfig(initial_capital=args.capital)
        sens = run_sensitivity(candidates, prices, preset, cfg, seed=args.seed)
        rows = pd.DataFrame(
            [{**{"scenario": k}, **vars(v)} for k, v in sens.items()]
        )
        base_trades, base_nav = engine.run_backtest(
            candidates, prices, preset, cfg, scenario=args.fill_scenario,
            seed=args.seed,
        )
        n_days = max(len(base_nav) - 1, 1)
        daily_signal = len(candidates) / max(n_days, 1)
        sections = [
            ("策略与参数", f"预设={args.preset}（{preset.name}）；窗口 "
                        f"{args.start}→{args.end}；IS 中位 seal_ratio={seal_med}；"
                        f"日均信号数={daily_signal:.2f}"
                        + ("（⚠ 过稀疏）" if daily_signal < 0.5 else "")),
            ("三档成交敏感性", report.df_to_md_table(rows)),
            ("结论纪律",
             "三档方向不一致时结论必须写\"不确定\"（规格 §14.3）；"
             "样本门槛：收益类≥30、晋级率类≥50。"),
        ]
        out_md = report.write_report(
            config.LIMITUP_REPORTS_DIR
            / f"{args.preset}_{args.start}-{args.end}_backtest.md",
            f"打板回测 [{preset.name}]（{args.fill_scenario} 档明细）",
            sections,
        )
        out_csv = config.LIMITUP_REPORTS_DIR / f"{args.preset}_trades.csv"
        pd.DataFrame([vars(t) for t in base_trades]).to_csv(out_csv, index=False)
        print(f"回测报告: {out_md}\n交易明细: {out_csv}")
    finally:
        conn.close()
```

并在 `main()` 注册：

```python
    p_bt = sub.add_parser("backtest", help="事件驱动打板回测（Phase 2）")
    p_bt.add_argument("--preset", required=True,
                      choices=["first_board", "relay_2", "relay_3"])
    p_bt.add_argument("--start", required=True, help="YYYYMMDD")
    p_bt.add_argument("--end", required=True, help="YYYYMMDD")
    p_bt.add_argument("--oos-start", default="20250701")
    p_bt.add_argument("--fill-scenario", default="base",
                      choices=["base", "optimistic", "pessimistic", "always"])
    p_bt.add_argument("--capital", type=float, default=1_000_000.0)
    p_bt.add_argument("--seed", type=int, default=42)
    p_bt.set_defaults(func=cmd_backtest)
```

`AGENTS.md` 「模块划分与依赖方向」代码块中、`**回测子系统**` 行之前追加一行：

```
涨停研究子系统（独立）：limitup/（backfill 数据回补 → events/patterns/sentiment 事件与形态 → study 事件研究 → engine 事件驱动打板回测，CLI: python -m davis_analyzer.limitup）。
```

- [ ] **Step 6: 全量回归 + 提交**

Run: `cd /home/leo/Projects/CodeAgentDashboard && rtk pytest davis_analyzer/tests/ -q`
Expected: 原有测试 + 新增 test_limitup_* 全部 PASS（若旧测试因 conftest 改动失败，修复后重跑）

```bash
git add davis_analyzer/limitup/engine.py davis_analyzer/limitup/cli.py AGENTS.md davis_analyzer/tests/test_limitup_engine.py
git commit -m "feat(limitup): 绩效统计+三档敏感性+CLI backtest+AGENTS.md 模块图更新"
```

- [ ] **Step 7（执行期任务）: 真实研究跑通 + 结论报告**

依赖 Task 3 Step 7 的回补结果。依次运行并归档输出到 `davis_analyzer/limitup/reports/`：

1. `python -m davis_analyzer.limitup study --start <回补最早年月日> --end 20260814 --oos-start 20250701`
2. `python -m davis_analyzer.limitup backtest --preset first_board --start <同上> --end 20260814`
3. `python -m davis_analyzer.limitup backtest --preset relay_2 --start <同上> --end 20260814`
4. `python -m davis_analyzer.limitup backtest --preset relay_3 --start <同上> --end 20260814`

汇总三档敏感性结论写 `davis_analyzer/limitup/reports/phase2_conclusion.md`：每个预设给「正期望 / 无期望 / 不确定」结论 + 日均信号数 + IS/OOS 方向一致性；**三档方向不一致一律写"不确定"**。该报告是 Phase 3 是否启动的门控依据（规格 §13）。

---

## 计划自审记录（已完成）

1. **规格覆盖**：§5 数据层→Task 3（含 Step 7 探测/降级上报）；§6.1–6.3→Task 4/6；§6.4→Task 6；§6.5→Task 8；§6.6→Task 6（板块联动+利空排雷；研报热度为规格 Phase 1.5 可选项，未列入本计划，符合"验证不足则砍"）；§6.7→Task 5；§7→Task 9+Task 11 预算+Task 13 信号量告警；§8→Task 10；§9→Task 11/12/13；§10 CLI→Task 3/10/13；§11→各任务测试；§13 门控→Task 13 Step 7。
2. **占位符**：无 TBD/TODO；两处实现说明（Task 6 vol 写法、Task 12 容差写法）给出了明确替换指令。
3. **签名一致性**：`build_events(conn, start, end)`（Task 4/5/6 演进）、`attach_pattern_features(events, conn, start, end)`（Task 7，Task 10/13 消费）、`apply_preset(events, preset, regime, *, seal_ratio_median, min_sector_linkage)`（Task 11，Task 13 消费）、`run_backtest(candidates, prices, preset, config, scenario, seed) -> tuple[list[TradeRecord], DataFrame]`（Task 12，Task 13 消费）、`_trade_cost(gross, commission_bps, stamp_tax_bps, is_sell)`（事实卡核对）。PerformanceStats 十字段与事实卡一致。
