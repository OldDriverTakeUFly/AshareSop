# 板块温度计(thermometer)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 spec `docs/superpowers/specs/2026-09-13-thermometer-design.md` 建成 thermometer 子系统:申万 L1+L2 五族因子温度 + 大盘五维温度,回补 2022 起历史,IC 校准硬验收,盘后日报 + 小红书卡片。

**Architecture:** 新子包 `davis_analyzer/thermometer/`(data → factors → scoring → report 管线),9 张新表挂共享库 `storage/database/market_data.db`(schema 追加进 `stockhot/data_layer/market_db.py::_SCHEMA_STATEMENTS`),纯 Tushare 数据源(经 `stockhot.data_layer.tushare_gateway`),卡片走 cardgen 现有通道扩展 `kind='thermo'`。

**Tech Stack:** Python 3.11+(`/home/leo/Projects/CodeAgentDashboard/.venv/bin/python`)、pandas/numpy/scipy、sqlite3、loguru、pytest。

## Global Constraints(每个任务隐含遵守)

- 一切命令从仓库根目录 `/home/leo/Projects/CodeAgentDashboard/` 运行,解释器用 `.venv/bin/python`(系统 python 缺依赖)。
- 日志用 loguru;`print()` 只允许出现在 `cli.py`。
- 金额聚合用 `decimal.Decimal` 铁律(moneyflow 聚合、大盘资金维)。
- Tushare 调用只经 `gw.call(api_name, **params)`(失败返回空 DataFrame,永不抛异常),网关自带 400/min 限频;禁止直连 tushare SDK。
- thermometer 自有表日期统一 compact `YYYYMMDD`;join `limit_pool`(dash 日期、无后缀代码)时用 `davis_analyzer.limitup.db.to_dash_date/strip_code_suffix` 转换。
- 代码风格:`from __future__ import annotations`、完整类型注解、docstring 英文+金融术语中文、`# ── ... ──` 模块分隔注释。
- 提交:Conventional Commits 中文 scope,如 `feat(thermometer): ...`。
- 测试命令:`.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_*.py -v`(或单测函数)。
- 权重/阈值常量只放 `davis_analyzer/constants.py`(模块级 dict,运行时禁止修改)。
- 单次预计超 30 分钟的批量任务必须 `setsid nohup` 脱离会话启动(见 AGENTS.md 长回测规范);本计划回补总量 <10 分钟,可直接前台跑,但执行中先看进度日志确认无卡死。

## Global Interfaces(跨任务契约,后续任务按此调用)

```python
# universe.py
def refresh_sw_index(gw) -> int                          # index_classify L1+L2 → sw_index
def refresh_sw_member(gw, snapshot_date: str) -> int     # 165 指数成分 → sw_member
def refresh_ths_index(gw) -> int                         # ths_index 概念列表
def refresh_ths_member(gw, snapshot_date: str) -> int
def load_universe(conn, level: str) -> pd.DataFrame      # [index_code, name, level]
def load_members(conn, levels=("L1","L2")) -> pd.DataFrame  # [level,index_code,con_code(带后缀),member_count?] 最新快照

# data.py
def backfill_sw_daily(conn, gw, start: str, end: str) -> dict   # {"days_done","rows_written","days_skipped"}
def backfill_ths_daily(conn, gw) -> dict
def update_sw_daily_incremental(conn, gw) -> dict               # 补最新交易日

# moneyflow_agg.py
def aggregate_sector_moneyflow(conn, start: str, end: str) -> dict  # 写 sector_moneyflow_daily
def market_flow_series(conn, start: str, end: str) -> pd.DataFrame # [trade_date,main_net_sum,circ_mv_sum]

# factors.py  (纯函数;panel 列契约见下)
def add_factor_columns(panel: pd.DataFrame) -> pd.DataFrame
def family_scores(panel: pd.DataFrame) -> pd.DataFrame

# scoring.py
def build_panel(conn, level: str, start: str, end: str) -> pd.DataFrame
def limit_density_daily(conn, start: str, end: str) -> pd.DataFrame
def score_history(conn, start: str, end: str) -> dict           # 全历史评分+落库
def latest_snapshot(conn, day: str) -> pd.DataFrame

# market_temp.py
def compute_market_history(conn, start: str, end: str) -> pd.DataFrame  # 落库 thermometer_market

# report.py
def write_daily_report(conn, day: str) -> Path

# calibrate.py
def build_eval_panel(conn, start: str, end: str) -> pd.DataFrame
def daily_rank_ic(panel: pd.DataFrame, horizon: int) -> pd.Series
def quintile_report(panel: pd.DataFrame, horizon: int) -> pd.DataFrame
def walk_forward(panel: pd.DataFrame) -> dict
def run_calibration(conn, start: str, end: str) -> Path
```

**panel 列契约**(build_panel 产出、factors 消费):长表,按 `(index_code, trade_date)` 排序,必备列 `index_code, trade_date, close, amount, vol, pct_change, main_net_pct, limit_ratio`;add_factor_columns 追加 `mom_level, mom_slope, flow_level, flow_slope, vol_level, vol_slope, trend_level, trend_slope, limit_level, limit_slope, pv_decay`;family_scores 追加 `mom_score, flow_score, vol_score, trend_score, limit_score`;scoring 再追加 `composite_z, temperature`。

---

### Task 1: 模块骨架 + 9 张表 + config/constants

**Files:**
- Modify: `stockhot/data_layer/market_db.py`(`_SCHEMA_STATEMENTS` 列表末尾追加)
- Create: `davis_analyzer/thermometer/__init__.py`、`__main__.py`、`cli.py`(空子命令骨架)
- Modify: `davis_analyzer/config.py`(加 THERMOMETER_REPORTS_DIR)
- Modify: `davis_analyzer/constants.py`(加四个常量 dict)
- Test: `davis_analyzer/tests/test_thermometer_schema.py`

**Interfaces:**
- Produces: 全部新表(后续任务写入);`config.THERMOMETER_REPORTS_DIR`;`constants.THERMOMETER_WEIGHTS / THERMOMETER_FAMILY_INNER_WEIGHTS / THERMOMETER_PRICE_VOLUME_DECAY / THERMOMETER_MARKET_DIM_WEIGHTS / THERMOMETER_CALIBRATION_TARGETS`;CLI 入口 `python -m davis_analyzer.thermometer {backfill|run|calibrate|report|status}`(本任务仅 argparse 骨架,func 占位报"未实现")。

- [ ] **Step 1: 写失败测试**

```python
# davis_analyzer/tests/test_thermometer_schema.py
"""thermometer 新表 schema 与常量存在性。"""
from __future__ import annotations

THERMO_TABLES = ["sw_index", "sw_daily", "sw_member", "sector_moneyflow_daily",
                 "ths_index", "ths_daily", "ths_member",
                 "thermometer_sector", "thermometer_market"]


def test_thermo_tables_created(tmp_path):
    from stockhot.data_layer import market_db
    db = tmp_path / "t.db"
    market_db.init_db(db)
    conn = market_db.get_connection(db)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in THERMO_TABLES:
            assert t in names, f"missing table {t}"
        # 二次 init 幂等
        market_db.init_db(db)
    finally:
        conn.close()


def test_constants_and_config():
    from davis_analyzer import config, constants
    assert abs(sum(constants.THERMOMETER_WEIGHTS.values()) - 1.0) < 1e-9
    assert abs(sum(constants.THERMOMETER_MARKET_DIM_WEIGHTS.values()) - 1.0) < 1e-9
    assert set(constants.THERMOMETER_WEIGHTS) == {"momentum", "flow", "volume", "trend", "limit"}
    assert config.THERMOMETER_REPORTS_DIR.exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_schema.py -v`
Expected: FAIL(missing table sw_index / AttributeError THERMOMETER_REPORTS_DIR)

- [ ] **Step 3: 实现**

`stockhot/data_layer/market_db.py` 的 `_SCHEMA_STATEMENTS` 列表末尾(`scan_log` 之后)追加 9 个语句(注意表注释风格与邻表一致):

```python
    # ═══ 板块温度计子系统(davis_analyzer/thermometer)════════════════════
    # 申万行业分类缓存(L1 31 + L2 134,SW2021 口径,每周刷新)
    """CREATE TABLE IF NOT EXISTS sw_index (
        index_code  TEXT PRIMARY KEY,
        name        TEXT,
        level       TEXT,             -- 'L1' / 'L2'
        parent_code TEXT,
        src         TEXT,
        is_pub      TEXT,
        fetched_at  REAL
    )""",
    # 申万指数日线(2022-01-04 起 SW2021 稳定口径;按 trade_date 全量 439 指数回补)
    """CREATE TABLE IF NOT EXISTS sw_daily (
        ts_code    TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        open  REAL, high REAL, low REAL, close REAL NOT NULL,
        vol   REAL, amount REAL, pct_change REAL,
        fetched_at REAL,
        PRIMARY KEY (ts_code, trade_date)
    )""",
    # 申万成分(含 in/out 日期;snapshot_date 周级快照,温度用最新快照)
    """CREATE TABLE IF NOT EXISTS sw_member (
        index_code    TEXT NOT NULL,
        con_code      TEXT NOT NULL,
        in_date       TEXT,
        out_date      TEXT,
        is_new        TEXT,
        snapshot_date TEXT NOT NULL,
        PRIMARY KEY (index_code, con_code, snapshot_date)
    )""",
    # 板块主力资金流日频(moneyflow 按 sw_member 成分自聚合;金额单位万元,Decimal 求和)
    """CREATE TABLE IF NOT EXISTS sector_moneyflow_daily (
        level        TEXT NOT NULL,
        index_code   TEXT NOT NULL,
        trade_date   TEXT NOT NULL,
        main_net     REAL,
        huge_net     REAL,
        big_net      REAL,
        mkt_cap      REAL,
        main_net_pct REAL,
        fetched_at   REAL,
        PRIMARY KEY (level, index_code, trade_date)
    )""",
    # 同花顺概念指数列表/日线/成分(概念层只建数据,不参与 v1 温度评分)
    """CREATE TABLE IF NOT EXISTS ths_index (
        ts_code   TEXT PRIMARY KEY,
        name      TEXT,
        count     INTEGER,
        exchange  TEXT,
        list_date TEXT,
        type      TEXT,
        fetched_at REAL
    )""",
    """CREATE TABLE IF NOT EXISTS ths_daily (
        ts_code    TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL NOT NULL,
        pre_close REAL, pct_change REAL, vol REAL, turnover_rate REAL,
        fetched_at REAL,
        PRIMARY KEY (ts_code, trade_date)
    )""",
    """CREATE TABLE IF NOT EXISTS ths_member (
        ts_code       TEXT NOT NULL,
        con_code      TEXT NOT NULL,
        con_name      TEXT,
        snapshot_date TEXT NOT NULL,
        PRIMARY KEY (ts_code, con_code, snapshot_date)
    )""",
    # 板块温度(五族分+复合z+温度;L1/L2 各自截面铺满 0-100)
    """CREATE TABLE IF NOT EXISTS thermometer_sector (
        trade_date TEXT NOT NULL,
        level      TEXT NOT NULL,
        index_code TEXT NOT NULL,
        name       TEXT,
        mom_score REAL, flow_score REAL, vol_score REAL,
        trend_score REAL, limit_score REAL,
        composite_z REAL,
        temperature REAL,
        delta_temp5 REAL,
        hot_streak INTEGER,
        fetched_at REAL,
        PRIMARY KEY (trade_date, level, index_code)
    )""",
    # 大盘温度(五维扩展窗口分位 → 0-100 + 五档标签)
    """CREATE TABLE IF NOT EXISTS thermometer_market (
        trade_date TEXT PRIMARY KEY,
        trend_dim REAL, width_dim REAL, volume_dim REAL,
        flow_dim REAL, sentiment_dim REAL,
        temperature REAL,
        regime_label TEXT,
        detail TEXT,
        fetched_at REAL
    )""",
```

`davis_analyzer/thermometer/__init__.py`:

```python
"""板块温度计子系统:申万 L1/L2 五族因子温度 + 大盘五维温度。

设计 spec: docs/superpowers/specs/2026-09-13-thermometer-design.md
"""
```

`davis_analyzer/thermometer/__main__.py`:

```python
"""Entry point for `python -m davis_analyzer.thermometer`."""

from __future__ import annotations

from davis_analyzer.thermometer.cli import main

main()
```

`davis_analyzer/thermometer/cli.py`(骨架,后续任务填 cmd_*):

```python
"""thermometer 模块 CLI(backfill/run/calibrate/report/status 子命令)。"""

from __future__ import annotations

import argparse


def _not_implemented(_: argparse.Namespace) -> None:
    raise SystemExit("该子命令尚未实现(按实施计划逐步落地)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="thermometer", description="板块温度计")
    sub = p.add_subparsers(dest="cmd", required=True)
    bf = sub.add_parser("backfill", help="全量回补:universe+sw_daily+ths+资金流聚合")
    bf.add_argument("--universe-only", action="store_true", help="仅刷新板块池/成分")
    bf.add_argument("--start", default="20220104")
    bf.add_argument("--end", default=None)
    bf.set_defaults(func=_not_implemented)
    run = sub.add_parser("run", help="盘后增量:当日行情+评分+日报+卡片")
    run.add_argument("--no-card", action="store_true")
    run.set_defaults(func=_not_implemented)
    cal = sub.add_parser("calibrate", help="IC/分组/walk-forward 校准")
    cal.add_argument("--start", default="20220104")
    cal.add_argument("--end", default=None)
    cal.set_defaults(func=_not_implemented)
    rep = sub.add_parser("report", help="生成盘后日报")
    rep.add_argument("--date", default=None)
    rep.set_defaults(func=_not_implemented)
    st = sub.add_parser("status", help="数据覆盖与温度最新日期")
    st.set_defaults(func=_not_implemented)
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)
```

`davis_analyzer/config.py` 在 `TOURNAMENT_REPORTS_DIR` 块后仿照添加:

```python
THERMOMETER_REPORTS_DIR = PROJECT_ROOT / "davis_analyzer" / "thermometer" / "reports"
THERMOMETER_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
```

`davis_analyzer/constants.py` 末尾追加(与 TOURNAMENT_COMPOSITE_WEIGHTS 同风格):

```python
# ── 板块温度计权重(spec 2026-09-13;校准后按实验记录更新,勿运行时修改) ──
THERMOMETER_WEIGHTS: dict[str, float] = {
    "momentum": 0.25, "flow": 0.30, "volume": 0.20, "trend": 0.15, "limit": 0.10,
}
# 每族内 (水平, 斜率) 权重
THERMOMETER_FAMILY_INNER_WEIGHTS: dict[str, tuple[float, float]] = {
    "momentum": (0.6, 0.4), "flow": (0.6, 0.4), "volume": (0.5, 0.5),
    "trend": (0.6, 0.4), "limit": (0.6, 0.4),
}
# 量价交互衰减系数:放量同向 1.0 / 缩量同向 0.7 / 价格反向 0.3
THERMOMETER_PRICE_VOLUME_DECAY: dict[str, float] = {
    "amplified_same": 1.0, "shrinking_same": 0.7, "opposite": 0.3,
}
THERMOMETER_MARKET_DIM_WEIGHTS: dict[str, float] = {
    "trend": 0.2, "width": 0.2, "volume": 0.2, "flow": 0.2, "sentiment": 0.20,
}
# 预测力验收线(spec §8.2,样本外)
THERMOMETER_CALIBRATION_TARGETS: dict[str, float] = {
    "min_ic": 0.03, "min_icir": 0.25, "spread_pvalue": 0.05,
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_schema.py -v`
Expected: 2 PASS。另外 `.venv/bin/python -m davis_analyzer.thermometer status` 应打印"尚未实现"退出。

- [ ] **Step 5: Commit**

```bash
git add stockhot/data_layer/market_db.py davis_analyzer/thermometer/ davis_analyzer/config.py davis_analyzer/constants.py davis_analyzer/tests/test_thermometer_schema.py
git commit -m "feat(thermometer): 模块骨架+九张表schema+权重常量与报告目录"
```

---

### Task 2: universe.py — 板块池与成分管理

**Files:**
- Create: `davis_analyzer/thermometer/universe.py`
- Test: `davis_analyzer/tests/test_thermometer_universe.py`

**Interfaces:**
- Consumes: `gw.call("index_classify", level=..., src="SW2021")` → df[index_code, industry_name, level, parent_code, is_pub];`gw.call("index_member", index_code=...)` → df[index_code, con_code, in_date, out_date, is_new];`gw.call("ths_index", exchange="A", type="N")`;`gw.call("ths_member", ts_code=...)`。
- Produces: Global Interfaces 中 universe.py 的 6 个函数;表 sw_index/sw_member/ths_index/ths_member 的写入。

- [ ] **Step 1: 写失败测试**(gw 用 MagicMock 返回 fixture DataFrame)

```python
# davis_analyzer/tests/test_thermometer_universe.py
"""universe 刷新与读取(网关 mock)。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd


def _gw_index_classify(level: str, src: str = "SW2021") -> pd.DataFrame:
    return pd.DataFrame({
        "index_code": ["801010.SI", "801011.SI"] if level == "L1" else ["801011.SI"],
        "industry_name": ["农林牧渔", "种植业"] if level == "L1" else ["种植业"],
        "level": [level] * (2 if level == "L1" else 1),
        "parent_code": [None, "801010.SI"] if level == "L1" else ["801010.SI"],
        "is_pub": ["1"] * (2 if level == "L1" else 1),
    })


def _make_gw() -> MagicMock:
    gw = MagicMock()
    gw.call.side_effect = lambda api, **kw: {
        "index_classify": lambda: _gw_index_classify(kw["level"]),
        "index_member": lambda: pd.DataFrame({
            "index_code": [kw["index_code"]] * 2,
            "con_code": ["000001.SZ", "600000.SH"],
            "in_date": ["20220104", "20220104"], "out_date": [None, None],
            "is_new": ["1", "1"]}),
    }[api]()
    return gw


def test_refresh_and_load(tmp_path):
    from stockhot.data_layer import market_db
    from davis_analyzer.thermometer import universe

    db = tmp_path / "t.db"
    market_db.init_db(db)
    conn = market_db.get_connection(db)
    try:
        n = universe.refresh_sw_index(_make_gw())
        assert n == 3  # L1 两条 + L2 一条
        m = universe.refresh_sw_member(_make_gw(), "20260913")
        assert m == 6  # 3 个指数 × 2 成分
        uni = universe.load_universe(conn, "L1")
        assert list(uni["name"]) == ["农林牧渔", "种植业"]
        mem = universe.load_members(conn)
        assert set(mem["con_code"]) == {"000001.SZ", "600000.SH"}
        assert len(mem) == 6  # L1(2 指数)+L2(1 指数)各带成分
    finally:
        conn.close()


def test_load_members_latest_snapshot(tmp_path):
    from stockhot.data_layer import market_db
    from davis_analyzer.thermometer import universe

    db = tmp_path / "t.db"
    market_db.init_db(db)
    conn = market_db.get_connection(db)
    try:
        for snap in ("20260901", "20260913"):
            universe.refresh_sw_member(_make_gw(), snap)
        mem = universe.load_members(conn)
        # 只取每 (level,index_code) 最新快照,旧快照不混入
        assert (mem["snapshot_date"] == "20260913").all()
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_universe.py -v`
Expected: FAIL — `No module named 'davis_analyzer.thermometer.universe'`

- [ ] **Step 3: 实现 universe.py**

```python
"""Universe management: 申万 L1/L2 池与成分、同花顺概念层(仅数据)。"""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

if TYPE_CHECKING:
    from stockhot.data_layer.tushare_gateway import TushareGateway


def refresh_sw_index(gw: TushareGateway) -> int:
    """index_classify L1+L2(SW2021)全量覆写 sw_index,返回行数."""
    frames = [gw.call("index_classify", level=lv, src="SW2021") for lv in ("L1", "L2")]
    df = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    if df.empty:
        logger.warning("index_classify L1+L2 均为空,sw_index 未更新")
        return 0
    from stockhot.data_layer.market_db import get_connection

    conn = get_connection()
    try:
        conn.execute("DELETE FROM sw_index")
        for _, r in df.iterrows():
            conn.execute(
                "INSERT INTO sw_index (index_code,name,level,parent_code,src,is_pub,fetched_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (r["index_code"], r["industry_name"], r["level"],
                 r.get("parent_code"), "SW2021", r.get("is_pub"), time.time()),
            )
        conn.commit()
        logger.info("sw_index 刷新: {} 行", len(df))
        return len(df)
    finally:
        conn.close()


def refresh_sw_member(gw: TushareGateway, snapshot_date: str) -> int:
    """逐指数拉 index_member 写 sw_member 快照;同快照日重跑先清后写(幂等)."""
    from stockhot.data_layer.market_db import get_connection

    conn = get_connection()
    try:
        codes = [r[0] for r in conn.execute("SELECT index_code FROM sw_index").fetchall()]
        conn.execute("DELETE FROM sw_member WHERE snapshot_date=?", (snapshot_date,))
        total = 0
        for code in codes:
            df = gw.call("index_member", index_code=code)
            for _, r in df.iterrows():
                conn.execute(
                    "INSERT OR REPLACE INTO sw_member "
                    "(index_code,con_code,in_date,out_date,is_new,snapshot_date) "
                    "VALUES (?,?,?,?,?,?)",
                    (code, r["con_code"], r.get("in_date"), r.get("out_date"),
                     r.get("is_new"), snapshot_date),
                )
                total += 1
        conn.commit()
        logger.info("sw_member 快照 {}: {} 指数 {} 行", snapshot_date, len(codes), total)
        return total
    finally:
        conn.close()


def refresh_ths_index(gw: TushareGateway) -> int:
    """ths_index 概念列表全量覆写(概念层只建数据)."""
    df = gw.call("ths_index", exchange="A", type="N")
    from stockhot.data_layer.market_db import get_connection

    conn = get_connection()
    try:
        conn.execute("DELETE FROM ths_index")
        for _, r in df.iterrows():
            conn.execute(
                "INSERT INTO ths_index (ts_code,name,count,exchange,list_date,type,fetched_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (r["ts_code"], r["name"], r.get("count"), r.get("exchange"),
                 r.get("list_date"), r.get("type"), time.time()),
            )
        conn.commit()
        logger.info("ths_index 刷新: {} 行", len(df))
        return len(df)
    finally:
        conn.close()


def refresh_ths_member(gw: TushareGateway, snapshot_date: str) -> int:
    """逐概念拉 ths_member 写快照(约 395 次调用,周频足够)."""
    from stockhot.data_layer.market_db import get_connection

    conn = get_connection()
    try:
        codes = [r[0] for r in conn.execute("SELECT ts_code FROM ths_index").fetchall()]
        conn.execute("DELETE FROM ths_member WHERE snapshot_date=?", (snapshot_date,))
        total = 0
        for code in codes:
            df = gw.call("ths_member", ts_code=code)
            for _, r in df.iterrows():
                conn.execute(
                    "INSERT OR REPLACE INTO ths_member "
                    "(ts_code,con_code,con_name,snapshot_date) VALUES (?,?,?,?)",
                    (code, r["con_code"], r.get("con_name"), snapshot_date),
                )
                total += 1
        conn.commit()
        return total
    finally:
        conn.close()


def load_universe(conn: sqlite3.Connection, level: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT index_code, name, level FROM sw_index WHERE level=?", conn, params=(level,))


def load_members(conn: sqlite3.Connection, levels: tuple[str, ...] = ("L1", "L2")) -> pd.DataFrame:
    """最新快照的 (level, index_code, con_code, snapshot_date) 长表.

    最新快照 = 每个 index_code 的 MAX(snapshot_date);L1 与 L2 成分都有
    (同一 con_code 会出现在 L1 与其所属 L2 两行,聚合时天然双层级各自成立)。
    """
    ph = ",".join("?" * len(levels))
    return pd.read_sql_query(
        "SELECT i.level, m.index_code, m.con_code, m.snapshot_date "
        "FROM sw_member m JOIN sw_index i ON i.index_code = m.index_code "
        "JOIN (SELECT index_code, MAX(snapshot_date) AS ms FROM sw_member "
        "      GROUP BY index_code) t "
        "  ON t.index_code = m.index_code AND t.ms = m.snapshot_date "
        f"WHERE i.level IN ({ph})",
        conn, params=levels,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_universe.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/thermometer/universe.py davis_analyzer/tests/test_thermometer_universe.py
git commit -m "feat(thermometer): 板块池管理——申万L1/L2指数与成分快照+概念层刷新读取"
```

---

### Task 3: data.py — sw_daily / ths_daily 回补与增量

**Files:**
- Create: `davis_analyzer/thermometer/data.py`
- Test: `davis_analyzer/tests/test_thermometer_data.py`

**Interfaces:**
- Consumes: `gw.call("sw_daily", trade_date=d, fields="ts_code,trade_date,open,high,low,close,vol,amount,pct_change")`;`gw.call("ths_daily", ts_code=code, start_date=..., end_date=...)`;交易日历 `davis_analyzer.limitup.db.trading_dates(conn, start, end)`(daily_price 推导,compact 日期)。
- Produces: `backfill_sw_daily / backfill_ths_daily / update_sw_daily_incremental`(签名见 Global Interfaces)。断点续跑:已完成交易日(行数 ≥31)跳过;每 60 交易日 commit 一次。

- [ ] **Step 1: 写失败测试**

```python
# davis_analyzer/tests/test_thermometer_data.py
"""sw_daily/ths_daily 回补(网关 mock + 内存库)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd


def _sw_df(d: str) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["801010.SI", "801011.SI"], "trade_date": [d, d],
        "open": [1.0, 2.0], "high": [1.5, 2.5], "low": [0.9, 1.9],
        "close": [1.2, 2.2], "vol": [100.0, 200.0], "amount": [1e6, 2e6],
        "pct_change": [1.0, -1.0],
    })


def _seed_calendar(conn) -> None:
    # daily_price 提供交易日历:20220104/20220105 两天
    for d in ("20220104", "20220105"):
        conn.execute("INSERT INTO daily_price (ts_code,trade_date,close) VALUES ('000001.SZ',?,100)",
                     (d,))
    conn.commit()


def test_backfill_sw_daily_idempotent(tmp_path):
    from stockhot.data_layer import market_db
    from davis_analyzer.thermometer import data

    db = tmp_path / "t.db"
    market_db.init_db(db)
    conn = market_db.get_connection(db)
    try:
        _seed_calendar(conn)
        gw = MagicMock()
        gw.call.side_effect = lambda api, **kw: _sw_df(kw["trade_date"])
        r1 = data.backfill_sw_daily(conn, gw, "20220104", "20220105")
        assert r1["days_done"] == 2 and r1["rows_written"] == 4
        r2 = data.backfill_sw_daily(conn, gw, "20220104", "20220105")
        assert r2["days_skipped"] == 2 and r2["rows_written"] == 0  # 断点续跑
        n = conn.execute("SELECT COUNT(*) FROM sw_daily").fetchone()[0]
        assert n == 4
    finally:
        conn.close()


def test_backfill_ths_daily_by_code(tmp_path):
    from stockhot.data_layer import market_db
    from davis_analyzer.thermometer import data

    db = tmp_path / "t.db"
    market_db.init_db(db)
    conn = market_db.get_connection(db)
    try:
        conn.execute(
            "INSERT INTO ths_index (ts_code,name,fetched_at) VALUES ('885900.TI','AI','0')")
        conn.commit()
        gw = MagicMock()
        gw.call.side_effect = lambda api, **kw: pd.DataFrame({
            "ts_code": ["885900.TI", "885900.TI"],
            "trade_date": ["20220104", "20220105"],
            "close": [100.0, 101.0], "pct_change": [1.0, 1.0],
        })
        r = data.backfill_ths_daily(conn, gw)
        assert r["codes_done"] == 1
        assert conn.execute("SELECT COUNT(*) FROM ths_daily").fetchone()[0] == 2
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_data.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: 实现 data.py**

```python
"""sw_daily(按 trade_date)与 ths_daily(按 ts_code)的回补与增量,断点续跑."""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

from davis_analyzer.limitup import db as limitup_db

if TYPE_CHECKING:
    from stockhot.data_layer.tushare_gateway import TushareGateway

_SW_FIELDS = "ts_code,trade_date,open,high,low,close,vol,amount,pct_change"
_COMMIT_EVERY = 60  # 交易日


def _write_sw_day(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    n = 0
    for _, r in df.iterrows():
        conn.execute(
            "INSERT OR REPLACE INTO sw_daily "
            "(ts_code,trade_date,open,high,low,close,vol,amount,pct_change,fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["ts_code"], str(r["trade_date"]), r.get("open"), r.get("high"),
             r.get("low"), r["close"], r.get("vol"), r.get("amount"),
             r.get("pct_change"), time.time()),
        )
        n += 1
    return n


def _day_covered(conn: sqlite3.Connection, d: str) -> bool:
    return conn.execute(
        "SELECT COUNT(*) FROM sw_daily WHERE trade_date=?", (d,)).fetchone()[0] >= 31


def backfill_sw_daily(
    conn: sqlite3.Connection, gw: TushareGateway, start: str, end: str,
) -> dict:
    """按交易日历逐日拉 sw_daily 全量(一日 439 指数),幂等跳过已覆盖日."""
    days_done = rows = skipped = 0
    pending = 0
    for d in limitup_db.trading_dates(conn, start, end):
        if _day_covered(conn, d):
            skipped += 1
            continue
        df = gw.call("sw_daily", trade_date=d, fields=_SW_FIELDS)
        if df is None or df.empty:
            logger.warning("sw_daily 无数据 {}", d)
            continue
        df["trade_date"] = df["trade_date"].astype(str)
        rows += _write_sw_day(conn, df)
        days_done += 1
        pending += 1
        if pending % _COMMIT_EVERY == 0:
            conn.commit()
            logger.info("sw_daily 进度: {} 天完成", days_done)
    conn.commit()
    return {"days_done": days_done, "rows_written": rows, "days_skipped": skipped}


def update_sw_daily_incremental(conn: sqlite3.Connection, gw: TushareGateway) -> dict:
    """补最新交易日(daily_price 日历的 MAX),已有则跳过."""
    latest = limitup_db.latest_trade_date(conn)
    if latest is None or _day_covered(conn, latest):
        return {"days_done": 0, "rows_written": 0, "days_skipped": 1}
    return backfill_sw_daily(conn, gw, latest, latest)


def backfill_ths_daily(conn: sqlite3.Connection, gw: TushareGateway) -> dict:
    """按概念 ts_code 全历史回补 ths_daily(重跑只补缺口日期)."""
    codes = [r[0] for r in conn.execute("SELECT ts_code FROM ths_index").fetchall()]
    done = rows = 0
    for i, code in enumerate(codes, 1):
        have = conn.execute(
            "SELECT COUNT(*) FROM ths_daily WHERE ts_code=?", (code,)).fetchone()[0]
        if have > 0:  # 已回补过;增量日由 run 流程单点补,此处跳过
            continue
        df = gw.call("ths_daily", ts_code=code, start_date="20150101",
                     end_date=limitup_db.latest_trade_date(conn) or "20991231")
        for _, r in (df if df is not None else pd.DataFrame()).iterrows():
            conn.execute(
                "INSERT OR REPLACE INTO ths_daily "
                "(ts_code,trade_date,close,pct_change,vol,turnover_rate,fetched_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (code, str(r["trade_date"]), r["close"], r.get("pct_change"),
                 r.get("vol"), r.get("turnover_rate"), time.time()),
            )
            rows += 1
        done += 1
        if i % 50 == 0:
            conn.commit()
            logger.info("ths_daily 进度: {}/{} 概念", i, len(codes))
    conn.commit()
    return {"codes_done": done, "rows_written": rows}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_data.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/thermometer/data.py davis_analyzer/tests/test_thermometer_data.py
git commit -m "feat(thermometer): sw_daily按日/ths_daily按码回补,断点续跑幂等"
```

---

### Task 4: moneyflow_agg.py — 板块资金流自聚合(Decimal)

**Files:**
- Create: `davis_analyzer/thermometer/moneyflow_agg.py`
- Test: `davis_analyzer/tests/test_thermometer_moneyflow.py`

**Interfaces:**
- Consumes: 表 `moneyflow`(compact 日期,金额单位万元)、`daily_basic.circ_mv`(万元)、`universe.load_members`。
- Produces: `aggregate_sector_moneyflow(conn, start, end) -> dict{"rows","days"}`(写 sector_moneyflow_daily,已有行 INSERT OR REPLACE 幂等);`market_flow_series(conn, start, end) -> pd.DataFrame[trade_date, main_net_sum, circ_mv_sum]`(大盘维度用,同样 Decimal 求和,不落表)。
- 金额口径:main_net = 超大单净额 + 大单净额 = `(buy_elg−sell_elg) + (buy_lg−sell_lg)`;main_net_pct = main_net / mkt_cap,mkt_cap = 成分股当日 daily_basic.circ_mv 之和(缺失当日容忍:该股不计入分母分子)。

- [ ] **Step 1: 写失败测试**(手算期望值)

```python
# davis_analyzer/tests/test_thermometer_moneyflow.py
"""资金流自聚合:手造 2 板块×2 日,Decimal 求和,断言到分毫."""
from __future__ import annotations


def _seed(conn) -> None:
    # 成分:801010.SI(L1) ← 000001.SZ;801011.SI(L1, 且 L2) ← 600000.SH
    conn.executemany(
        "INSERT INTO sw_index (index_code,name,level,fetched_at) VALUES (?,?,?,0)",
        [("801010.SI", "农林牧渔", "L1"), ("801011.SI", "种植业", "L1"),
         ("801011.SI", "种植业", "L2")])
    conn.executemany(
        "INSERT INTO sw_member (index_code,con_code,snapshot_date) VALUES (?,?, '20260913')",
        [("801010.SI", "000001.SZ"), ("801011.SI", "600000.SH")])
    # moneyflow:两日;金额单位万元
    conn.executemany(
        "INSERT INTO moneyflow (trade_date,ts_code,buy_elg_amount,sell_elg_amount,"
        "buy_lg_amount,sell_lg_amount,fetched_at) VALUES (?,?,?,?,?,?,0)",
        [("20220104", "000001.SZ", 100.0, 60.0, 30.0, 20.0),   # 主力净 +50
         ("20220104", "600000.SH", 10.0, 90.0, 5.0, 5.0),      # 主力净 -80
         ("20220105", "000001.SZ", 1.0, 2.0, 1.0, 2.0),        # 主力净 -2
         ("20220105", "600000.SH", 7.0, 3.0, 2.0, 1.0)])       # 主力净 +5
    # daily_basic 流通市值(万元):恒定 1000
    conn.executemany(
        "INSERT INTO daily_basic (ts_code,trade_date,circ_mv,fetched_at) VALUES (?,?,?,0)",
        [("000001.SZ", "20220104", 1000.0), ("000001.SZ", "20220105", 1000.0),
         ("600000.SH", "20220104", 1000.0), ("600000.SH", "20220105", 1000.0)])
    conn.commit()


def test_aggregate_sector_moneyflow(tmp_path):
    from stockhot.data_layer import market_db
    from davis_analyzer.thermometer import moneyflow_agg

    db = tmp_path / "t.db"
    market_db.init_db(db)
    conn = market_db.get_connection(db)
    try:
        _seed(conn)
        r = moneyflow_agg.aggregate_sector_moneyflow(conn, "20220104", "20220105")
        assert r["rows"] >= 6  # 3 (level,index_code) × 2 日
        row = conn.execute(
            "SELECT main_net, main_net_pct FROM sector_moneyflow_daily "
            "WHERE level='L1' AND index_code='801010.SI' AND trade_date='20220104'").fetchone()
        assert row[0] == 50.0            # 100-60+30-20
        assert abs(row[1] - 0.05) < 1e-9  # 50/1000
        # 幂等
        r2 = moneyflow_agg.aggregate_sector_moneyflow(conn, "20220104", "20220105")
        assert conn.execute("SELECT COUNT(*) FROM sector_moneyflow_daily").fetchone()[0] == r["rows"]
    finally:
        conn.close()


def test_market_flow_series(tmp_path):
    from stockhot.data_layer import market_db
    from davis_analyzer.thermometer import moneyflow_agg

    db = tmp_path / "t.db"
    market_db.init_db(db)
    conn = market_db.get_connection(db)
    try:
        _seed(conn)
        df = moneyflow_agg.market_flow_series(conn, "20220104", "20220105")
        d1 = df[df["trade_date"] == "20220104"].iloc[0]
        assert d1["main_net_sum"] == -30.0   # 50 + (-80)
        assert d1["circ_mv_sum"] == 2000.0
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_moneyflow.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: 实现 moneyflow_agg.py**

```python
"""moneyflow 个股明细 → 板块主力资金流日频(Decimal 求和).

主力口径 = 超大单净额 + 大单净额(buy_elg−sell_elg + buy_lg−sell_lg),单位万元。
分母 = 成分股当日 circ_mv 之和(daily_basic,万元);分子分母同源同时点。
"""

from __future__ import annotations

import sqlite3
import time
from decimal import Decimal

import pandas as pd
from loguru import logger

from davis_analyzer.thermometer.universe import load_members


def _dec(v: object) -> Decimal:
    """None/NaN 安全转 Decimal(float 直转有二进制尾差,str 中转精确)."""
    if v is None:
        return Decimal(0)
    try:
        if pd.isna(v):
            return Decimal(0)
    except TypeError:
        pass
    return Decimal(str(v))


def aggregate_sector_moneyflow(conn: sqlite3.Connection, start: str, end: str) -> dict:
    """聚合 [start,end] 全部交易日;已有行覆写,幂等.

    一次拉取窗口内全部 moneyflow/daily_basic 明细,Python 侧 Decimal 累加
    (铁律:金额不用 float 累加)。窗口全量约 540 万行,回补一次约 1-2 分钟。
    """
    members = load_members(conn)
    if members.empty:
        raise RuntimeError("sw_member 为空,先 refresh universe")
    code_to_idx: dict[str, list[tuple[str, str]]] = {}
    for _, m in members.iterrows():
        code_to_idx.setdefault(m["con_code"], []).append((m["level"], m["index_code"]))

    mf = pd.read_sql_query(
        "SELECT trade_date, ts_code, buy_elg_amount, sell_elg_amount, "
        "buy_lg_amount, sell_lg_amount FROM moneyflow "
        "WHERE trade_date>=? AND trade_date<=?",
        conn, params=(start, end),
    )
    cap = pd.read_sql_query(
        "SELECT trade_date, ts_code, circ_mv FROM daily_basic "
        "WHERE trade_date>=? AND trade_date<=? AND circ_mv IS NOT NULL",
        conn, params=(start, end),
    )
    cap_map = {(r.ts_code, r.trade_date): _dec(r.circ_mv) for r in cap.itertuples()}

    # (level,index_code,trade_date) → Decimal 累加器
    main_acc: dict[tuple[str, str, str], Decimal] = {}
    huge_acc: dict[tuple[str, str, str], Decimal] = {}
    big_acc: dict[tuple[str, str, str], Decimal] = {}
    cap_acc: dict[tuple[str, str, str], Decimal] = {}

    for r in mf.itertuples():
        targets = code_to_idx.get(r.ts_code)
        if not targets:
            continue
        huge = _dec(r.buy_elg_amount) - _dec(r.sell_elg_amount)
        big = _dec(r.buy_lg_amount) - _dec(r.sell_lg_amount)
        net = huge + big
        cv = cap_map.get((r.ts_code, r.trade_date))
        for lv, idx in targets:
            key = (lv, idx, r.trade_date)
            main_acc[key] = main_acc.get(key, Decimal(0)) + net
            huge_acc[key] = huge_acc.get(key, Decimal(0)) + huge
            big_acc[key] = big_acc.get(key, Decimal(0)) + big
            if cv is not None:
                cap_acc[key] = cap_acc.get(key, Decimal(0)) + cv

    now = time.time()
    for key, net in main_acc.items():
        lv, idx, d = key
        mkt = cap_acc.get(key, Decimal(0))
        pct = float(net / mkt) if mkt else None
        conn.execute(
            "INSERT OR REPLACE INTO sector_moneyflow_daily "
            "(level,index_code,trade_date,main_net,huge_net,big_net,mkt_cap,main_net_pct,fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (lv, idx, d, float(net), float(huge_acc[key]), float(big_acc[key]),
             float(mkt) if mkt else None, pct, now),
        )
    conn.commit()
    days = len({k[2] for k in main_acc})
    logger.info("sector_moneyflow 聚合: {} 行 / {} 日 [{},{}]", len(main_acc), days, start, end)
    return {"rows": len(main_acc), "days": days}


def market_flow_series(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """全市场(不分板块)主力净额与流通市值日和,大盘资金维用."""
    mf = pd.read_sql_query(
        "SELECT trade_date, ts_code, buy_elg_amount, sell_elg_amount, "
        "buy_lg_amount, sell_lg_amount FROM moneyflow "
        "WHERE trade_date>=? AND trade_date<=?",
        conn, params=(start, end),
    )
    cap = pd.read_sql_query(
        "SELECT trade_date, SUM(circ_mv) AS cap_sum FROM daily_basic "
        "WHERE trade_date>=? AND trade_date<=? AND circ_mv IS NOT NULL GROUP BY trade_date",
        conn, params=(start, end),
    )
    cap_map = dict(zip(cap["trade_date"], cap["cap_sum"]))
    acc: dict[str, Decimal] = {}
    for r in mf.itertuples():
        net = (_dec(r.buy_elg_amount) - _dec(r.sell_elg_amount)
               + _dec(r.buy_lg_amount) - _dec(r.sell_lg_amount))
        acc[r.trade_date] = acc.get(r.trade_date, Decimal(0)) + net
    return pd.DataFrame({
        "trade_date": sorted(acc),
        "main_net_sum": [float(acc[d]) for d in sorted(acc)],
        "circ_mv_sum": [float(cap_map.get(d) or 0.0) for d in sorted(acc)],
    })
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_moneyflow.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/thermometer/moneyflow_agg.py davis_analyzer/tests/test_thermometer_moneyflow.py
git commit -m "feat(thermometer): moneyflow按成分自聚合板块主力净额(Decimal)+大盘资金序列"
```

---

### Task 5: factors.py — 五族因子(纯函数)

**Files:**
- Create: `davis_analyzer/thermometer/factors.py`
- Test: `davis_analyzer/tests/test_thermometer_factors.py`

**Interfaces:**
- Consumes: panel 列契约(Global Interfaces);`constants.THERMOMETER_FAMILY_INNER_WEIGHTS / THERMOMETER_PRICE_VOLUME_DECAY`。
- Produces: `add_factor_columns(panel)` 追加 10 个因子列 + pv_decay;`family_scores(panel)` 追加 5 个族分列(当日截面 z,clip ±3)。
- 公式(spec §5,水平/斜率):mom_level=20日累计收益;mom_slope=3日收益−20日收益/20;flow_level=main_net_pct 20日和;flow_slope=3日均−20日均;vol_level=amount/20日均额−1;vol_slope=5日均额/20日均额−1;trend_level=0.5×MA排列( (c>MA20)+(c>MA60)+(MA20>MA60) )/3 + 0.5×close/60日滚动最高;trend_slope=20日上行天数占比;limit_level=limit_ratio 20日均;limit_slope=5日均−20日均;pv_decay=放量同向1.0/缩量同向0.7/价格反向0.3(以 1 日收益方向为价格方向、vol_level>0 为放量)。

- [ ] **Step 1: 写失败测试**(合成 panel 手算)

```python
# davis_analyzer/tests/test_thermometer_factors.py
"""五族因子纯函数:构造 2 指数×22 日 panel,断言关键窗口值与 z 性质."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _panel() -> pd.DataFrame:
    dates = [f"2022010{d:02d}" for d in range(4, 10)] + \
            [f"202201{d:02d}" for d in range(10, 26)]  # 22 日
    rows = []
    for i, code in enumerate(("801010.SI", "801011.SI")):
        base = 100.0 + i * 10
        for j, d in enumerate(dates):
            close = base * (1.01 ** j)  # 每日 +1%
            rows.append({
                "index_code": code, "trade_date": d, "close": close,
                "amount": 1e6 * (1 + 0.1 * j), "vol": 1e4, "pct_change": 1.0,
                "main_net_pct": 0.001 * (1 if i == 0 else -1),
                "limit_ratio": 0.02 if i == 0 else 0.0,
            })
    return pd.DataFrame(rows).sort_values(["index_code", "trade_date"]).reset_index(drop=True)


def test_add_factor_columns_windows():
    from davis_analyzer.thermometer import factors

    p = factors.add_factor_columns(_panel())
    a = p[p["index_code"] == "801010.SI"].reset_index(drop=True)
    # mom_level 第21行(idx=20,恰好20日窗口)= 1.01**20−1
    assert abs(a.loc[21, "mom_level"] - (1.01 ** 21 - 1)) < 1e-9
    # flow_level 20日和: 0.001×20 = 0.02
    assert abs(a.loc[21, "flow_level"] - 0.02) < 1e-9
    # trend_slope 恒 +1(每日上涨)
    assert a.loc[21, "trend_slope"] == 1.0
    # pv_decay: 放量(amount 递增)且上涨 → 1.0
    assert a.loc[21, "pv_decay"] == 1.0
    # 前 19 行窗口不足 → NaN
    assert np.isnan(a.loc[5, "mom_level"])


def test_family_scores_z_and_decay():
    from davis_analyzer.thermometer import factors

    p = factors.family_scores(factors.add_factor_columns(_panel()))
    last_day = p["trade_date"].max()
    day = p[p["trade_date"] == last_day]
    # 两成员截面 z 互为相反数
    assert abs(day["mom_score"].sum()) < 1e-9
    # 801010(涨+放量+资金流入) 各族分应高于 801011
    hi = day[day["index_code"] == "801010.SI"].iloc[0]
    lo = day[day["index_code"] == "801011.SI"].iloc[0]
    for col in ("mom_score", "flow_score", "vol_score", "trend_score", "limit_score"):
        assert hi[col] > lo[col], col
    # clip 生效:构造极端截面不越界
    assert day["mom_score"].abs().max() <= 3.0 + 1e-9
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_factors.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: 实现 factors.py**

```python
"""五族因子(纯函数):水平+斜率正交化,截面 z 合成族分.

panel 契约: 长表按 (index_code, trade_date) 排序,列
index_code/trade_date/close/amount/vol/pct_change/main_net_pct/limit_ratio。
"""

from __future__ import annotations

import pandas as pd

from davis_analyzer.constants import (
    THERMOMETER_FAMILY_INNER_WEIGHTS,
    THERMOMETER_PRICE_VOLUME_DECAY,
)

_Z_CLIP = 3.0


def add_factor_columns(panel: pd.DataFrame) -> pd.DataFrame:
    """按 index_code 分组滚动计算 10 个子指标 + pv_decay(不改入参)."""
    df = panel.copy()
    g = df.groupby("index_code", sort=False)

    ret1 = g["close"].transform(lambda s: s / s.shift(1) - 1)
    ret3 = g["close"].transform(lambda s: s / s.shift(3) - 1)
    ret20 = g["close"].transform(lambda s: s / s.shift(20) - 1)
    df["mom_level"] = ret20
    df["mom_slope"] = ret3 - ret20 / 20

    df["flow_level"] = g["main_net_pct"].transform(lambda s: s.rolling(20).sum())
    df["flow_slope"] = (
        g["main_net_pct"].transform(lambda s: s.rolling(3).mean())
        - g["main_net_pct"].transform(lambda s: s.rolling(20).mean())
    )

    ma20_amt = g["amount"].transform(lambda s: s.rolling(20).mean())
    ma5_amt = g["amount"].transform(lambda s: s.rolling(5).mean())
    df["vol_level"] = df["amount"] / ma20_amt - 1
    df["vol_slope"] = ma5_amt / ma20_amt - 1

    ma20 = g["close"].transform(lambda s: s.rolling(20).mean())
    ma60 = g["close"].transform(lambda s: s.rolling(60).mean())
    hh60 = g["close"].transform(lambda s: s.rolling(60).max())
    align3 = ((df["close"] > ma20).astype(float) + (df["close"] > ma60).astype(float)
              + (ma20 > ma60).astype(float)) / 3.0
    df["trend_level"] = 0.5 * align3 + 0.5 * (df["close"] / hh60)
    df["trend_slope"] = (ret1 > 0).astype(float).groupby(df["index_code"]).transform(
        lambda s: s.rolling(20).mean())

    df["limit_level"] = g["limit_ratio"].transform(lambda s: s.rolling(20).mean())
    df["limit_slope"] = (
        g["limit_ratio"].transform(lambda s: s.rolling(5).mean())
        - g["limit_ratio"].transform(lambda s: s.rolling(20).mean())
    )

    decay = THERMOMETER_PRICE_VOLUME_DECAY
    same_dir = ret1 > 0
    df["pv_decay"] = pd.Series(
        map(
            lambda up, amp: (decay["opposite"] if not up
                             else decay["amplified_same"] if amp
                             else decay["shrinking_same"]),
            same_dir, df["vol_level"] > 0,
        ),
        index=df.index, dtype=float,
    )
    return df


def cross_section_z(panel: pd.DataFrame, col: str) -> pd.Series:
    """当日截面 z-score(winsorize clip ±3),index 与 panel 对齐."""
    z = panel.groupby("trade_date")[col].transform(
        lambda s: (s - s.mean()) / s.std(ddof=0) if s.std(ddof=0) > 0 else s * 0.0)
    return z.clip(-_Z_CLIP, _Z_CLIP)


def family_scores(panel: pd.DataFrame) -> pd.DataFrame:
    """各族 水平z×w_level + 斜率z×w_slope → 族分;量能族乘 pv_decay."""
    df = panel.copy()
    for fam, cols in {
        "momentum": ("mom_level", "mom_slope"),
        "flow": ("flow_level", "flow_slope"),
        "volume": ("vol_level", "vol_slope"),
        "trend": ("trend_level", "trend_slope"),
        "limit": ("limit_level", "limit_slope"),
    }.items():
        wl, ws = THERMOMETER_FAMILY_INNER_WEIGHTS[fam]
        lvl, slo = cols
        df[f"{fam[:4]}_score" if fam != "momentum" else "mom_score"] = (
            wl * cross_section_z(df, lvl) + ws * cross_section_z(df, slo))
    # 量价交互:量能族分数 × 价格方向衰减(spec §5 规则2)
    df["vol_score"] = df["vol_score"] * df["pv_decay"]
    return df
```

注意:`family_scores` 里族分列名映射必须是固定五个:`mom_score / flow_score / volume 族列名是 vol_score / trend_score / limit_score`。为避免上面 dict 推导的列名技巧引入错误,实现时直接写五行显式赋值(`df["mom_score"] = ...` 等),公式同上。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_factors.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/thermometer/factors.py davis_analyzer/tests/test_thermometer_factors.py
git commit -m "feat(thermometer): 五族因子纯函数——水平/斜率正交化+截面z+量价交互衰减"
```

---

### Task 6: scoring.py — 温度合成、涨停密度、落库

**Files:**
- Create: `davis_analyzer/thermometer/scoring.py`
- Test: `davis_analyzer/tests/test_thermometer_scoring.py`

**Interfaces:**
- Consumes: `universe.load_universe/load_members`、表 sw_daily/sector_moneyflow_daily/limit_pool/thermometer_sector、`factors.add_factor_columns/family_scores`、`constants.THERMOMETER_WEIGHTS`。
- Produces: Global Interfaces 的 4 个函数。temperature = 当日当日层级截面 `rank(pct=True,method='average')×100`;delta_temp5 = temperature − 5 日前 temperature(同 index_code);hot_streak = temperature>80 的连续交易日数(布尔 cummax-reset 法)。

- [ ] **Step 1: 写失败测试**

```python
# davis_analyzer/tests/test_thermometer_scoring.py
"""温度合成:边界/单调/L1L2隔离/涨停密度join/衍生读数."""
from __future__ import annotations


def _seed(conn) -> None:
    conn.executemany(
        "INSERT INTO sw_index (index_code,name,level,fetched_at) VALUES (?,?,?,0)",
        [("801010.SI", "农林牧渔", "L1"), ("801011.SI", "种植业", "L1")])
    conn.executemany(
        "INSERT INTO sw_member (index_code,con_code,snapshot_date) VALUES (?,?,'20260913')",
        [("801010.SI", "000001.SZ"), ("801010.SI", "600000.SH"),
         ("801011.SI", "000001.SZ")])
    # sw_daily: 801010 稳涨放量,801011 稳跌;26 日(≥20 窗口)
    rows = []
    for j in range(26):
        d = f"2022{j // 21 + 1:02d}{j % 21 + 1:02d}"  # 简化日期,唯一递增即可
        rows.append(("801010.SI", d, 100 * 1.01 ** j, 1e6 * (1 + 0.1 * j)))
        rows.append(("801011.SI", d, 100 * 0.99 ** j, 1e6))
    conn.executemany(
        "INSERT INTO sw_daily (ts_code,trade_date,close,amount,fetched_at) VALUES (?,?,?,?,0)",
        rows)
    # 资金流:801010 每日 +0.5%,801011 −0.5%
    conn.executemany(
        "INSERT INTO sector_moneyflow_daily (level,index_code,trade_date,main_net_pct,fetched_at) "
        "VALUES ('L1',?,?,?,0)",
        [(r[0], r[1], 0.005 if r[0] == "801010.SI" else -0.005) for r in rows])
    # 涨停:801010 的 000001.SZ 每日涨停(dash 日期,无后缀代码)
    conn.executemany(
        "INSERT INTO limit_pool (trade_date,ts_code,pool_kind,fetched_at) VALUES (?,?,'limit_up',0)",
        [(f"{r[1][:4]}-{r[1][4:6]}-{r[1][6:]}", "000001") for r in rows if r[0] == "801010.SI"])
    conn.commit()


def test_temperature_bounded_and_monotonic(tmp_path):
    from stockhot.data_layer import market_db
    from davis_analyzer.thermometer import scoring

    db = tmp_path / "t.db"
    market_db.init_db(db)
    conn = market_db.get_connection(db)
    try:
        _seed(conn)
        r = scoring.score_history(conn, "20220101", "20991231")
        assert r["rows"] > 0
        df = pd_read_thermo(conn)
        assert df["temperature"].between(0, 100).all()
        last = df[df["trade_date"] == df["trade_date"].max()]
        hot = last[last["index_code"] == "801010.SI"]["temperature"].iloc[0]
        cold = last[last["index_code"] == "801011.SI"]["temperature"].iloc[0]
        assert hot > cold
        # 涨停密度已入 panel:801010 的 limit_ratio ≈ 1/2 成分
        panel = scoring.build_panel(conn, "L1", df["trade_date"].max(), df["trade_date"].max())
        assert panel["limit_ratio"].iloc[0] > 0.4
    finally:
        conn.close()


def pd_read_thermo(conn):
    import pandas as pd
    return pd.read_sql_query(
        "SELECT * FROM thermometer_sector ORDER BY trade_date, index_code", conn)


def test_hot_streak_and_delta(tmp_path):
    from stockhot.data_layer import market_db
    from davis_analyzer.thermometer import scoring

    import pandas as pd
    db = tmp_path / "t.db"
    market_db.init_db(db)
    conn = market_db.get_connection(db)
    try:
        _seed(conn)
        scoring.score_history(conn, "20220101", "20991231")
        df = pd_read_thermo(conn)
        # delta_temp5 存在且后 5 日行非空;hot_streak 非负
        assert df["hot_streak"].fillna(0).ge(0).all()
        assert df["delta_temp5"].notna().sum() > 0
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_scoring.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: 实现 scoring.py**

```python
"""温度合成:panel 组装(行情+资金+涨停密度)→ 五族 → composite_z → 温度落库."""

from __future__ import annotations

import sqlite3
import time

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.constants import THERMOMETER_WEIGHTS
from davis_analyzer.limitup import db as limitup_db
from davis_analyzer.thermometer import factors
from davis_analyzer.thermometer.universe import load_universe


def limit_density_daily(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """limit_pool(dash 日期/无后缀) × sw_member → (level,index_code,trade_date,limit_ratio).

    涨停归属按股票代码 join 成分(不用板块名匹配);比率 = 涨停家数 / 成分数。
    """
    members = pd.read_sql_query(
        "SELECT i.level, m.index_code, m.con_code FROM sw_member m "
        "JOIN sw_index i ON i.index_code=m.index_code "
        "JOIN (SELECT index_code, MAX(snapshot_date) ms FROM sw_member GROUP BY index_code) t "
        "ON t.index_code=m.index_code AND t.ms=m.snapshot_date", conn)
    members["bare"] = members["con_code"].str.split(".").str[0]
    counts = members["index_code"].value_counts()
    member_n = members[["level", "index_code"]].drop_duplicates().assign(
        member_count=lambda d: d["index_code"].map(counts))
    zt = pd.read_sql_query(
        "SELECT trade_date, ts_code FROM limit_pool WHERE pool_kind='limit_up' "
        "AND trade_date>=? AND trade_date<=?",
        conn, params=(limitup_db.to_dash_date(start), limitup_db.to_dash_date(end)))
    zt["bare"] = zt["ts_code"].str.split(".").str[0]
    zt["trade_date"] = zt["trade_date"].str.replace("-", "", regex=False)
    zt = zt.drop_duplicates(["trade_date", "bare"])
    merged = zt.merge(members, on="bare", how="inner")
    zt_n = merged.groupby(["level", "index_code", "trade_date"]).size().rename(
        "limit_count").reset_index()
    out = member_n.merge(zt_n, on=["level", "index_code"], how="left")
    out["trade_date"] = out["trade_date"].fillna(
        zt["trade_date"].max() if not zt.empty else None)
    out = out.dropna(subset=["trade_date"])
    out["limit_ratio"] = out["limit_count"].fillna(0) / out["member_count"]
    return out[["level", "index_code", "trade_date", "limit_ratio"]]


def build_panel(conn: sqlite3.Connection, level: str, start: str, end: str) -> pd.DataFrame:
    """拼装某层级 panel:sw_daily + main_net_pct + limit_ratio(列契约见 factors)."""
    uni = load_universe(conn, level)
    codes = uni["index_code"].tolist()
    if not codes:
        return pd.DataFrame()
    ph = ",".join("?" * len(codes))
    px = pd.read_sql_query(
        f"SELECT ts_code AS index_code, trade_date, close, amount, vol, pct_change "
        f"FROM sw_daily WHERE ts_code IN ({ph}) AND trade_date>=? AND trade_date<=? "
        f"ORDER BY ts_code, trade_date", conn, params=(*codes, start, end))
    flow = pd.read_sql_query(
        f"SELECT index_code, trade_date, main_net_pct FROM sector_moneyflow_daily "
        f"WHERE level=? AND index_code IN ({ph}) AND trade_date>=? AND trade_date<=?",
        conn, params=(level, *codes, start, end))
    dens = limit_density_daily(conn, start, end)
    dens = dens[dens["level"] == level]
    panel = px.merge(flow, on=["index_code", "trade_date"], how="left")
    panel = panel.merge(dens[["index_code", "trade_date", "limit_ratio"]],
                        on=["index_code", "trade_date"], how="left")
    panel["main_net_pct"] = panel["main_net_pct"].fillna(0.0)
    panel["limit_ratio"] = panel["limit_ratio"].fillna(0.0)
    return panel.sort_values(["index_code", "trade_date"]).reset_index(drop=True)


def _score_level(panel: pd.DataFrame) -> pd.DataFrame:
    """单层级全历史:factors → composite_z → temperature(每日截面 rank pct)."""
    df = factors.family_scores(factors.add_factor_columns(panel))
    z = sum(THERMOMETER_WEIGHTS[fam] * df[f"{col}"]
            for fam, col in {"momentum": "mom_score", "flow": "flow_score",
                             "volume": "vol_score", "trend": "trend_score",
                             "limit": "limit_score"}.items())
    df["composite_z"] = z
    df["temperature"] = df.groupby("trade_date")["composite_z"].rank(
        pct=True, method="average") * 100
    return df


def score_history(conn: sqlite3.Connection, start: str, end: str) -> dict:
    """L1+L2 全历史评分并落库(INSERT OR REPLACE 全量覆写,幂等);衍生读数后算."""
    name_by_code: dict[str, str] = {}
    rows_total = 0
    now = time.time()
    for level in ("L1", "L2"):
        panel = build_panel(conn, level, start, end)
        if panel.empty:
            logger.warning("build_panel {} 为空", level)
            continue
        uni = load_universe(conn, level)
        name_by_code.update(dict(zip(uni["index_code"], uni["name"])))
        df = _score_level(panel)
        # 窗口不足的日子(因子 NaN)没有温度意义:五族全 NaN 的行丢弃
        df = df.dropna(subset=["mom_level", "flow_level", "vol_level",
                               "trend_level", "limit_level"], how="any")
        # 衍生读数:按 (level,index_code) 时序
        df = df.sort_values(["index_code", "trade_date"])
        df["delta_temp5"] = df.groupby("index_code")["temperature"].transform(
            lambda s: s - s.shift(5))
        hot = (df["temperature"] > 80).astype(int)
        grp_reset = (hot != hot.groupby(df["index_code"]).shift(1)).cumsum()
        df["hot_streak"] = hot.groupby(grp_reset).cumsum() * hot
        for r in df.itertuples():
            conn.execute(
                "INSERT OR REPLACE INTO thermometer_sector "
                "(trade_date,level,index_code,name,mom_score,flow_score,vol_score,"
                "trend_score,limit_score,composite_z,temperature,delta_temp5,hot_streak,fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.trade_date, level, r.index_code, name_by_code.get(r.index_code),
                 r.mom_score, r.flow_score, r.vol_score, r.trend_score, r.limit_score,
                 r.composite_z, r.temperature, r.delta_temp5,
                 int(r.hot_streak) if pd.notna(r.hot_streak) else 0, now))
        rows_total += len(df)
        logger.info("thermometer {}: {} 行落库", level, len(df))
    conn.commit()
    return {"rows": rows_total}


def latest_snapshot(conn: sqlite3.Connection, day: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM thermometer_sector WHERE trade_date=? ORDER BY temperature DESC",
        conn, params=(day,))
```

实现注意:①`limit_density_daily` 中 `member_n` 与涨停计数 join 后,无涨停日也要有 limit_ratio=0 行——用「涨停日历笛卡尔」展开(取 daily_price 日历与 index 集合的叉乘)以保证 20 日滚动窗口连续;测试里 seed 每日都有涨停,真实库中需按日历 forward fill。具体做法:`out` 先构造 level×index_code×全交易日 的完全框架(`pd.MultiIndex.from_product`),再 left join 涨停计数 fillna(0)。②`_score_level` 的 dropna 保留至少有完整 20 日窗口的截面日;hot_streak 的 groupby-reset 写法如上(hot 为 0 的行 streak 归 0)。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_scoring.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/thermometer/scoring.py davis_analyzer/tests/test_thermometer_scoring.py
git commit -m "feat(thermometer): 温度合成——panel组装/涨停密度代码join/截面分位温度/升温与连热衍生读数"
```

---

### Task 7: market_temp.py — 大盘五维温度

**Files:**
- Create: `davis_analyzer/thermometer/market_temp.py`
- Test: `davis_analyzer/tests/test_thermometer_market.py`

**Interfaces:**
- Consumes: `index_daily`(000300.SH/399006.SZ)、`daily_price`(宽度/量能 SQL,仿 `limitup/sentiment._breadth_axes`)、`moneyflow_agg.market_flow_series`、`limit_pool`(涨停数/最高连板/炸板率)、`constants.THERMOMETER_MARKET_DIM_WEIGHTS`。
- Produces: `compute_market_history(conn, start, end) -> pd.DataFrame`(落库 thermometer_market 并返回)。五维各自 → 扩展窗口分位(`expanding(min_periods=250).apply(percentileofscore 式)`,即当日值在截至当日历史中的分位)→ 加权 ×100 → regime_label 五档(冰点<15/低温15-35/温和35-65/偏热65-85/过热≥85);detail JSON 记录维度背离(最大−最小分位 >0.5 时列出热/冷维)。

- [ ] **Step 1: 写失败测试**

```python
# davis_analyzer/tests/test_thermometer_market.py
"""大盘温度:合成序列的档位边界/最少历史闸/落库."""
from __future__ import annotations

import pandas as pd


def _seed(conn, days: int = 300) -> None:
    dates = [f"2022{(i // 28) + 1:02d}{(i % 28) + 1:02d}" for i in range(days)]
    for code in ("000300.SH", "399006.SZ"):
        for i, d in enumerate(dates):
            close = 4000 + i * 2 if code == "000300.SH" else 2000 + i
            conn.execute(
                "INSERT INTO index_daily (ts_code,trade_date,close,amount,fetched_at) "
                "VALUES (?,?,?,?,0)", (code, d, close, 1e9))
    for i, d in enumerate(dates):
        conn.execute(
            "INSERT INTO daily_price (ts_code,trade_date,close,pre_close,amount,fetched_at) "
            "VALUES ('000001.SZ',?,?,?,?,0)",
            (d, 10.0 if i % 2 == 0 else 9.0, 10.0, 1e8))
        dash = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        conn.execute(
            "INSERT INTO limit_pool (trade_date,ts_code,pool_kind,consecutive_boards,fetched_at) "
            "VALUES (?, '000001', 'limit_up', 2, 0)", (dash,))
    conn.execute(
        "INSERT INTO moneyflow (trade_date,ts_code,buy_elg_amount,sell_elg_amount,"
        "buy_lg_amount,sell_lg_amount,fetched_at) VALUES (?,?,?,?,?,?,0)",
        (dates[0], "000001.SZ", 10.0, 5.0, 1.0, 1.0))
    conn.execute(
        "INSERT INTO daily_basic (ts_code,trade_date,circ_mv,fetched_at) VALUES (?,?,?,0)",
        (dates[0], "000001.SZ", 1000.0))
    conn.commit()


def test_market_temperature_series(tmp_path):
    from stockhot.data_layer import market_db
    from davis_analyzer.thermometer import market_temp

    db = tmp_path / "t.db"
    market_db.init_db(db)
    conn = market_db.get_connection(db)
    try:
        _seed(conn)
        df = market_temp.compute_market_history(conn, "20220101", "20991231")
        assert len(df) > 0
        # 最少 250 日历史闸:第一天应有 NaN 维度,输出温度的行 ≥ 250 日之后
        assert df["temperature"].notna().sum() >= 1
        valid = df.dropna(subset=["temperature"])
        assert valid["temperature"].between(0, 100).all()
        assert set(valid["regime_label"]) <= {"冰点", "低温", "温和", "偏热", "过热"}
        # 落库行数 = 输出行数
        n_db = conn.execute("SELECT COUNT(*) FROM thermometer_market").fetchone()[0]
        assert n_db == len(df)
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_market.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: 实现 market_temp.py**

```python
"""大盘五维温度:指数趋势/宽度/量能/资金/涨停情绪,扩展窗口分位锚定."""

from __future__ import annotations

import json
import sqlite3
import time

import numpy as np
import pandas as pd
from loguru import logger

from davis_analyzer.constants import THERMOMETER_MARKET_DIM_WEIGHTS
from davis_analyzer.limitup import db as limitup_db
from davis_analyzer.thermometer.moneyflow_agg import market_flow_series

_MIN_HISTORY = 250  # 约一年交易日,不足不分位
_LABELS = [(85.0, "过热"), (65.0, "偏热"), (35.0, "温和"), (15.0, "低温"), (-1.0, "冰点")]


def _expanding_pct(s: pd.Series) -> pd.Series:
    """当日值在截至当日全部历史中的分位(0-1)."""
    vals = s.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    for i in range(_MIN_HISTORY - 1, len(vals)):
        if np.isnan(vals[i]):
            continue
        hist = vals[: i + 1]
        hist = hist[~np.isnan(hist)]
        out[i] = float((hist <= vals[i]).sum() / len(hist))
    return pd.Series(out, index=s.index)


def _trend_axis(conn: sqlite3.Connection, start: str, end: str) -> pd.Series:
    """沪深300+创业板:MA 排列与距 250 日高点,两指数均值 → 日序列."""
    frames = []
    for code in ("000300.SH", "399006.SZ"):
        df = pd.read_sql_query(
            "SELECT trade_date, close FROM index_daily WHERE ts_code=? "
            "AND trade_date>=? AND trade_date<=? ORDER BY trade_date",
            conn, params=(code, start, end))
        if df.empty:
            continue
        ma20 = df["close"].rolling(20).mean()
        ma60 = df["close"].rolling(60).mean()
        ma250 = df["close"].rolling(250).mean()
        hh250 = df["close"].rolling(250).max()
        align = ((df["close"] > ma20).astype(float) + (df["close"] > ma60).astype(float)
                 + (ma20 > ma250).astype(float)) / 3.0
        frames.append((0.5 * align + 0.5 * df["close"] / hh250).rename(code))
    if not frames:
        return pd.Series(dtype=float)
    merged = pd.concat(frames, axis=1).mean(axis=1)
    merged.index = pd.read_sql_query(
        "SELECT DISTINCT trade_date FROM index_daily WHERE ts_code='000300.SH' "
        "AND trade_date>=? AND trade_date<=? ORDER BY trade_date",
        conn, params=(start, end))["trade_date"]
    return merged


def _width_volume_axis(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """宽度(涨跌家数比+20日新高占比)与量能(成交额20日均)——SQL 仿 sentiment._breadth_axes."""
    return pd.read_sql_query(
        "SELECT trade_date, "
        "0.5 * (SUM(CASE WHEN close > pre_close THEN 1.0 ELSE 0 END) / COUNT(*)) "
        "  + 0.5 * AVG(CASE WHEN close >= hh20 THEN 1.0 ELSE 0 END) AS width_raw, "
        "AVG(amount_20) AS volume_raw FROM ("
        "  SELECT trade_date, close, pre_close, amount, "
        "         AVG(amount) OVER (PARTITION BY trade_date) AS amount_20, "
        "         MAX(close) OVER (PARTITION BY ts_code ORDER BY trade_date "
        "                          ROWS 19 PRECEDING) AS hh20 "
        "  FROM daily_price WHERE trade_date>=? AND trade_date<=?) "
        "GROUP BY trade_date ORDER BY trade_date",
        conn, params=(start, end))


def _sentiment_axis(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """涨停数/最高连板/炸板率合成(炸板率反向)."""
    zt = pd.read_sql_query(
        "SELECT trade_date, COUNT(*) AS zt_n FROM limit_pool WHERE pool_kind='limit_up' "
        "AND trade_date>=? AND trade_date<=? GROUP BY trade_date",
        conn, params=(limitup_db.to_dash_date(start), limitup_db.to_dash_date(end)))
    brk = pd.read_sql_query(
        "SELECT trade_date, COUNT(*) AS brk_n FROM limit_pool WHERE pool_kind='broken' "
        "AND trade_date>=? AND trade_date<=? GROUP BY trade_date",
        conn, params=(limitup_db.to_dash_date(start), limitup_db.to_dash_date(end)))
    hi = pd.read_sql_query(
        "SELECT trade_date, MAX(consecutive_boards) AS hi_board FROM limit_pool "
        "WHERE pool_kind='limit_up' AND trade_date>=? AND trade_date<=? GROUP BY trade_date",
        conn, params=(limitup_db.to_dash_date(start), limitup_db.to_dash_date(end)))
    df = zt.merge(hi, on="trade_date", how="outer").merge(brk, on="trade_date", how="outer")
    df["brk_n"] = df["brk_n"].fillna(0)
    df["broken_rate"] = df["brk_n"] / (df["zt_n"] + df["brk_n"])
    df["sentiment_raw"] = (0.4 * df["zt_n"].clip(upper=150) / 150
                           + 0.3 * df["hi_board"].clip(upper=12) / 12
                           + 0.3 * (1 - df["broken_rate"].fillna(0.5)))
    return df


def compute_market_history(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    trend = _trend_axis(conn, start, end).rename("trend")
    wv = _width_volume_axis(conn, start, end)
    wv["trade_date"] = wv["trade_date"].str.replace("-", "", regex=False)
    sent = _sentiment_axis(conn, start, end)
    sent["trade_date"] = sent["trade_date"].str.replace("-", "", regex=False)
    flow = market_flow_series(conn, start, end)
    flow["flow_raw"] = flow["main_net_sum"] / flow["circ_mv_sum"].replace(0, np.nan)
    flow["flow_raw"] = flow["flow_raw"].rolling(5).mean()

    cal = limitup_db.trading_dates(conn, start, end)
    base = pd.DataFrame({"trade_date": pd.Series(cal, dtype="object")})
    df = (base.merge(trend.rename("trend_raw").reset_index().rename(
              columns={"index": "trade_date"}), on="trade_date", how="left")
          .merge(wv[["trade_date", "width_raw", "volume_raw"]], on="trade_date", how="left")
          .merge(flow[["trade_date", "flow_raw"]], on="trade_date", how="left")
          .merge(sent[["trade_date", "sentiment_raw"]], on="trade_date", how="left"))

    for raw, dim in (("trend_raw", "trend_dim"), ("width_raw", "width_dim"),
                     ("volume_raw", "volume_dim"), ("flow_raw", "flow_dim"),
                     ("sentiment_raw", "sentiment_dim")):
        df[dim] = _expanding_pct(df[raw])
    w = THERMOMETER_MARKET_DIM_WEIGHTS
    df["temperature"] = (w["trend"] * df["trend_dim"] + w["width"] * df["width_dim"]
                         + w["volume"] * df["volume_dim"] + w["flow"] * df["flow_dim"]
                         + w["sentiment"] * df["sentiment_dim"]) * 100

    def _label(t: float) -> str:
        for th, name in _LABELS:
            if t >= th:
                return name
        return _LABELS[-1][1]

    dims = ["trend_dim", "width_dim", "volume_dim", "flow_dim", "sentiment_dim"]
    now = time.time()
    for r in df.itertuples():
        temp = r.temperature
        label = _label(temp) if pd.notna(temp) else None
        detail = None
        if pd.notna(temp):
            vals = {d: getattr(r, d) for d in dims}
            if pd.notna(list(vals.values())).all() and max(vals.values()) - min(vals.values()) > 0.5:
                hot = [d for d, v in vals.items() if v >= 0.7]
                cold = [d for d, v in vals.items() if v <= 0.3]
                detail = json.dumps({"divergence": {"hot": hot, "cold": cold}},
                                    ensure_ascii=False)
        conn.execute(
            "INSERT OR REPLACE INTO thermometer_market "
            "(trade_date,trend_dim,width_dim,volume_dim,flow_dim,sentiment_dim,"
            "temperature,regime_label,detail,fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r.trade_date, r.trend_dim, r.width_dim, r.volume_dim, r.flow_dim,
             r.sentiment_dim, temp, label, detail, now))
    conn.commit()
    logger.info("thermometer_market: {} 日 [{},{}] 有温度 {} 日",
                len(df), start, end, int(df["temperature"].notna().sum()))
    return df
```

实现注意:`_width_volume_axis` 的 SQL 里量能取的是「当日全市场成交额」(窗口列 amount_20 命名有误导,直接用 SUM(amount) GROUP BY trade_date 更简单,实施时简化为 `SUM(amount) AS volume_raw` 并删除窗口列,以最简 SQL 为准);`_trend_axis` 里 index 对齐写法如有别扭,可用 df 自带 trade_date 列 merge 而非 reset_index。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_market.py -v`
Expected: 1 PASS(seed 只塞了 1 日 moneyflow,flow_raw 多为 NaN 分位,温度列可能全 NaN——若因此 FAIL,把 seed 扩为每日都有 moneyflow/daily_basic 行再断言)

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/thermometer/market_temp.py davis_analyzer/tests/test_thermometer_market.py
git commit -m "feat(thermometer): 大盘五维温度——扩展窗口分位锚定+五档标签+背离检测"
```

---

### Task 8: report.py — 盘后日报

**Files:**
- Create: `davis_analyzer/thermometer/report.py`
- Test: `davis_analyzer/tests/test_thermometer_report.py`

**Interfaces:**
- Consumes: thermometer_sector/thermometer_market 表、`config.THERMOMETER_REPORTS_DIR`。
- Produces: `write_daily_report(conn, day) -> Path`,文件名 `{day-dash}_板块温度计.md`;day=None 时取 thermometer_sector 最新日期(cli 层处理默认值)。章节:L1/L2 温度榜 top10+bottom5、升温/降温榜(Δtemp5 top5/bottom5)、高温预警(hot_streak≥3)、大盘温度五维+背离、数据完整性备注(当日有温度的指数数 vs universe 数)。

- [ ] **Step 1: 写失败测试**

```python
# davis_analyzer/tests/test_thermometer_report.py
"""日报渲染:章节齐全、表格式 markdown 可读."""
from __future__ import annotations


def _seed(conn) -> str:
    day = "20260911"
    conn.execute(
        "INSERT INTO thermometer_market (trade_date,temperature,regime_label,fetched_at) "
        "VALUES (?,42.0,'温和',0)", (day,))
    for i in range(12):
        for level in ("L1", "L2"):
            conn.execute(
                "INSERT INTO thermometer_sector (trade_date,level,index_code,name,"
                "temperature,delta_temp5,hot_streak,mom_score,flow_score,vol_score,"
                "trend_score,limit_score,composite_z,fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                (day, level, f"8010{i:02d}.SI", f"行业{i}", 90 - i * 5, 3.0 - i,
                 5 if i == 0 else 0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0))
    conn.commit()
    return day


def test_write_daily_report(tmp_path, monkeypatch):
    from stockhot.data_layer import market_db
    from davis_analyzer.thermometer import report

    db = tmp_path / "t.db"
    market_db.init_db(db)
    conn = market_db.get_connection(db)
    try:
        day = _seed(conn)
        monkeypatch.setattr(report, "REPORTS_DIR", tmp_path)
        path = report.write_daily_report(conn, day)
        text = path.read_text(encoding="utf-8")
        assert path.name == f"{day}_板块温度计.md" or "板块温度计" in path.name
        for sec in ("L1 温度榜", "L2 温度榜", "升温榜", "降温榜", "高温预警", "大盘温度"):
            assert sec in text, sec
        assert "行业0" in text and "42.0" in text
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_report.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: 实现 report.py**

```python
"""盘后 markdown 日报:温度排行/升降温和/高温预警/大盘温度/完整性备注."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from davis_analyzer.config import THERMOMETER_REPORTS_DIR
from davis_analyzer.limitup import db as limitup_db

REPORTS_DIR = THERMOMETER_REPORTS_DIR  # 测试可 monkeypatch


def _md_table(df: pd.DataFrame, floatfmt: str = "{:.1f}") -> str:
    if df.empty:
        return "(无数据)"
    fmt = df.copy()
    for c in fmt.select_dtypes("number").columns:
        fmt[c] = fmt[c].map(lambda v: floatfmt.format(v) if pd.notna(v) else "-")
    head = "| " + " | ".join(fmt.columns) + " |"
    sep = "|" + "|".join("---" for _ in fmt.columns) + "|"
    rows = ["| " + " | ".join(str(v) for v in r) + " |" for r in fmt.itertuples(index=False)]
    return "\n".join([head, sep, *rows])


def write_daily_report(conn: sqlite3.Connection, day: str) -> Path:
    dash = limitup_db.to_dash_date(day)
    sec = pd.read_sql_query(
        "SELECT * FROM thermometer_sector WHERE trade_date=?", conn, params=(day,))
    mkt = pd.read_sql_query(
        "SELECT * FROM thermometer_market WHERE trade_date=?", conn, params=(day,)).fetchone() \
        if False else pd.read_sql_query(
            "SELECT * FROM thermometer_market WHERE trade_date=?", conn, params=(day,))
    lines = [f"# 板块温度计 · {dash}", ""]

    for level, label in (("L1", "一级行业"), ("L2", "二级行业")):
        sub = sec[sec["level"] == level].sort_values("temperature", ascending=False)
        top = sub.head(10)[["name", "temperature", "delta_temp5", "hot_streak"]]
        top.columns = ["板块", "温度", "5日升温", "连热天数"]
        bottom = sub.tail(5)[["name", "temperature", "delta_temp5"]]
        bottom.columns = ["板块", "温度", "5日升温"]
        lines += [f"## {level} 温度榜 · {label}", "",
                  "### 最热 top10", "", _md_table(top.reset_index(drop=True)), "",
                  "### 最冷 bottom5", "", _md_table(bottom.reset_index(drop=True)), ""]

    hottest = sec.sort_values("delta_temp5", ascending=False).head(5)
    coldest = sec.sort_values("delta_temp5").head(5)
    for title, df in (("升温榜", hottest), ("降温榜", coldest)):
        t = df[["level", "name", "temperature", "delta_temp5"]]
        t.columns = ["层级", "板块", "温度", "5日升温"]
        lines += [f"## {title}", "", _md_table(t.reset_index(drop=True)), ""]

    warn = sec[sec["hot_streak"] >= 3].sort_values("hot_streak", ascending=False)
    w = warn[["level", "name", "temperature", "hot_streak"]]
    w.columns = ["层级", "板块", "温度", "连热天数"]
    lines += ["## 高温预警(温度>80 连续≥3日)", "", _md_table(w.reset_index(drop=True)), ""]

    if not mkt.empty:
        row = mkt.iloc[0]
        lines += ["## 大盘温度", "",
                  f"- 温度 **{row['temperature']:.1f}** · 档位 **{row['regime_label']}**",
                  f"- 五维分位: 趋势 {row['trend_dim']:.2f} / 宽度 {row['width_dim']:.2f} "
                  f"/ 量能 {row['volume_dim']:.2f} / 资金 {row['flow_dim']:.2f} "
                  f"/ 情绪 {row['sentiment_dim']:.2f}"]
        if row["detail"]:
            lines += [f"- 背离提示: {row['detail']}"]
        lines.append("")
    n_all = pd.read_sql_query(
        "SELECT level, COUNT(*) n FROM sw_index GROUP BY level", conn)
    have = sec.groupby("level")["index_code"].nunique().to_dict()
    notes = [f"{r.level}: 覆盖 {have.get(r.level, 0)}/{r.n}"
             for r in n_all.itertuples()] or ["sw_index 为空"]
    lines += ["## 数据完整性", "", " · ".join(notes), ""]

    out = REPORTS_DIR / f"{dash}_板块温度计.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
```

(实现时删掉 `if False else` 的残留写法,直接一条 `pd.read_sql_query`。)

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_report.py -v`
Expected: 1 PASS

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/thermometer/report.py davis_analyzer/tests/test_thermometer_report.py
git commit -m "feat(thermometer): 盘后日报——两级温度榜/升降温和/高温预警/大盘温度/完整性备注"
```

---

### Task 9: cli.py 串联 backfill / run / status / report

**Files:**
- Modify: `davis_analyzer/thermometer/cli.py`(替换 `_not_implemented` 占位)
- Test: `davis_analyzer/tests/test_thermometer_cli.py`

**Interfaces:**
- Consumes: Task 2-8 全部模块;`stockhot.data_layer.tushare_gateway.get_gateway()`。
- Produces: 可用的五个子命令。`backfill` 流程 = universe 刷新(含 ths)→ sw_daily 回补 → ths_daily 回补 → moneyflow 聚合 → score_history → market_temp(一次到位);`run` = 增量:universe 不动 → update_sw_daily_incremental → moneyflow 聚合(最近 30 日,防补数据)→ score_history(近 2 年,覆写幂等)→ market_temp(近 3 年)→ write_daily_report → 卡片(Task 13 接入,本任务留 `--no-card` 直通);`status` = 各表行数+最新日期。

- [ ] **Step 1: 写失败测试**(mock 网关与各模块函数,验证调用编排顺序)

```python
# davis_analyzer/tests/test_thermometer_cli.py
"""CLI 编排:mock 下游模块,断言 run/backfill 调用链与参数."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_run_orchestration(tmp_path, monkeypatch):
    from davis_analyzer.thermometer import cli

    fake_conn = MagicMock()
    called = []

    def _rec(name):
        def f(*a, **kw):
            called.append(name)
            return {"rows": 0}
        return f

    monkeypatch.setattr(cli, "_conn", lambda: fake_conn)
    monkeypatch.setattr(cli, "_gw", lambda: MagicMock())
    with patch("davis_analyzer.thermometer.cli.data") as m_data, \
         patch("davis_analyzer.thermometer.cli.moneyflow_agg") as m_mf, \
         patch("davis_analyzer.thermometer.cli.scoring") as m_sc, \
         patch("davis_analyzer.thermometer.cli.market_temp") as m_mt, \
         patch("davis_analyzer.thermometer.cli.report") as m_rp:
        m_data.update_sw_daily_incremental.side_effect = _rec("sw_incr")
        m_mf.aggregate_sector_moneyflow.side_effect = _rec("mf_agg")
        m_sc.score_history.side_effect = _rec("score")
        m_mt.compute_market_history.side_effect = _rec("market")
        m_rp.write_daily_report.return_value = tmp_path / "r.md"
        cli.main_shim(["run", "--no-card"]) if hasattr(cli, "main_shim") else None
        # 直接调 cmd_run
    from davis_analyzer.thermometer import cli as c2
    with patch.object(c2, "_conn", return_value=fake_conn), \
         patch.object(c2, "_gw", return_value=MagicMock()), \
         patch.object(c2.data, "update_sw_daily_incremental", side_effect=_rec("sw_incr")), \
         patch.object(c2.moneyflow_agg, "aggregate_sector_moneyflow", side_effect=_rec("mf_agg")), \
         patch.object(c2.scoring, "score_history", side_effect=_rec("score")), \
         patch.object(c2.market_temp, "compute_market_history", side_effect=_rec("market")), \
         patch.object(c2.report, "write_daily_report", return_value=tmp_path / "r.md"):
        c2.main()  # argv 由 monkeypatch 注入
    assert called[-5:] == ["sw_incr", "mf_agg", "score", "market", "report"]
```

(测试写法允许在实现时简化:核心断言 = run 命令按序触发 增量行情→资金聚合→评分→大盘→日报 五步;可用 `capsys`/直接调 `cli.cmd_run` 的方式重写,保持断言不变。)

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_cli.py -v`
Expected: FAIL — cmd_run 不存在

- [ ] **Step 3: 实现**(cli.py 补齐;关键骨架)

```python
import sys
from datetime import datetime

from loguru import logger


def _conn():
    from stockhot.data_layer.market_db import get_connection
    return get_connection()


def _gw():
    from stockhot.data_layer.tushare_gateway import get_gateway
    return get_gateway()


def cmd_backfill(args):
    from davis_analyzer.thermometer import data, moneyflow_agg, market_temp, scoring, universe
    today = datetime.now().strftime("%Y%m%d")
    if args.universe_only:
        universe.refresh_sw_index(_gw())
        universe.refresh_sw_member(_gw(), today)
        universe.refresh_ths_index(_gw())
        universe.refresh_ths_member(_gw(), today)
        print("universe 刷新完成")
        return
    gw = _gw()
    conn = _conn()
    try:
        universe.refresh_sw_index(gw)
        universe.refresh_sw_member(gw, today)
        universe.refresh_ths_index(gw)
        end = args.end or datetime.now().strftime("%Y%m%d")
        r1 = data.backfill_sw_daily(conn, gw, args.start, end)
        r2 = data.backfill_ths_daily(conn, gw)
        r3 = moneyflow_agg.aggregate_sector_moneyflow(conn, args.start, end)
        r4 = scoring.score_history(conn, args.start, end)
        r5 = market_temp.compute_market_history(conn, args.start, end)
        print(f"backfill 完成: sw_daily={r1} ths_daily={r2} 资金={r3} 温度={r4} 大盘={r5['日'] if isinstance(r5, dict) else len(r5)}")
    finally:
        conn.close()


def cmd_run(args):
    from davis_analyzer.limitup import db as limitup_db
    from davis_analyzer.thermometer import data, market_temp, moneyflow_agg, report, scoring
    conn = _conn()
    try:
        gw = _gw()
        data.update_sw_daily_incremental(conn, gw)
        latest = limitup_db.latest_trade_date(conn)
        start30 = (pd_shift_month(latest, -1) if False else _minus_days(latest, 40))
        moneyflow_agg.aggregate_sector_moneyflow(conn, start30, latest)
        scoring.score_history(conn, _minus_days(latest, 730), latest)
        market_temp.compute_market_history(conn, _minus_days(latest, 1100), latest)
        path = report.write_daily_report(conn, latest)
        print(f"run 完成: 日报 {path}")
        if not args.no_card:
            _build_card(latest)  # Task 13 实现;此前为 no-op log
    finally:
        conn.close()
```

`_minus_days(d, n)`:基于 daily_price 日历往前取 n 个自然日前(实现:`limitup_db.trading_dates(conn, "20000101", d)` 取尾部再按日期减,或直接 `datetime` 减法返回 compact 字符串即可——聚合/评分窗口宽一点无妨,选后者,最简)。`_build_card` 在 Task 13 前是 `logger.info("卡片通道未接入")` 占位函数。`cmd_status` 打印 9 张表 COUNT(*) 与 MAX(trade_date)。`cmd_report` 默认取 thermometer_sector MAX(trade_date)。`cmd_calibrate` 委托 Task 11。`build_parser` 中各 `set_defaults(func=cmd_*)` 替换占位。

- [ ] **Step 4: 跑测试确认通过 + 手动烟测**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_cli.py davis_analyzer/tests/test_thermometer_schema.py -v`
Expected: PASS。`cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m davis_analyzer.thermometer status` 打印各表计数(全 0)。

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/thermometer/cli.py davis_analyzer/tests/test_thermometer_cli.py
git commit -m "feat(thermometer): CLI编排——backfill全量链/run增量链/status覆盖一览"
```

---

### Task 10: 全量回补执行(P0 数据落地)

**Files:** 无新代码(执行 + 修 bug;如遇 bug 修复对应模块并补测试)

- [ ] **Step 1: 先刷 universe(真实网关)**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m davis_analyzer.thermometer backfill --universe-only`
Expected: 输出"universe 刷新完成";约 3 + 165 + 1 + 395 次调用 ≈ 2 分钟(限频 400/min)。验证:sqlite 查 sw_index=165 行(L1 31+L2 134)、sw_member>0、ths_index≈395。

- [ ] **Step 2: 全量回补(前台,预计 <10 分钟;若 >5 分钟无输出,改 setsid 后台跑)**

Run: `.venv/bin/python -m davis_analyzer.thermometer backfill 2>&1 | tee logs/thermo_backfill.log`
Expected: sw_daily ≈ 1100+ 交易日 × 439 行(≈50 万行);ths_daily 395 码全历史;资金聚合 ≈ 165×1100 行;温度落库(2022-02 起,因 20/60 日窗口预热);大盘温度(2023 年起出分位,250 日闸)。

- [ ] **Step 3: 数据验证(必须逐项过)**

```bash
.venv/bin/python - <<'EOF'
import sqlite3
conn = sqlite3.connect("storage/database/market_data.db")
for t in ["sw_index","sw_daily","sw_member","ths_index","ths_daily","ths_member",
          "sector_moneyflow_daily","thermometer_sector","thermometer_market"]:
    n, lo, hi = conn.execute(
        f"SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM {t}").fetchone()
    print(f"{t:26s} {n:>9,}  {lo} → {hi}")
# 抽查:801010.SI 最近收盘非空且 pct_change 有值
print(conn.execute("SELECT trade_date, close, pct_change FROM sw_daily "
                   "WHERE ts_code='801010.SI' ORDER BY trade_date DESC LIMIT 1").fetchone())
# 温度分布:末日 L1 截面应铺满 0-100
print(conn.execute("SELECT MIN(temperature), MAX(temperature), COUNT(*) "
                   "FROM thermometer_sector WHERE level='L1' "
                   "AND trade_date=(SELECT MAX(trade_date) FROM thermometer_sector)").fetchone())
EOF
```

Expected: sw_daily hi=最近交易日;L1 温度 min≈0-10、max≈90-100、count=31;thermometer_market 有 regime_label 非空行。任一不符 → 回到对应 Task 修 bug,不带病推进。

- [ ] **Step 4: Commit(数据不进 git,只提交执行中产生的修复)**

```bash
git add -A davis_analyzer/thermometer davis_analyzer/tests
git commit -m "fix(thermometer): 全量回补执行中修复(如有)——P0数据落地2022起"
```

---

### Task 11: calibrate.py — IC / 分组 / walk-forward 校准工具

**Files:**
- Create: `davis_analyzer/thermometer/calibrate.py`
- Test: `davis_analyzer/tests/test_thermometer_calibrate.py`

**Interfaces:**
- Consumes: thermometer_sector(温度)、sw_daily(前向收益)、`constants.THERMOMETER_CALIBRATION_TARGETS`。
- Produces: Global Interfaces 的 5 个函数 + `run_calibration`。`build_eval_panel` 输出列 `[trade_date, level, index_code, temperature, composite_z, mom_score…limit_score, fwd5, fwd10, fwd20]`(fwd = close.shift(-h)/close−1,per index)。`daily_rank_ic(panel, horizon)` = 逐日 scipy.stats.spearmanr(temperature, fwd)。`quintile_report` = 逐日温度五分位组的 fwd 均值 + top-bottom 价差 t 检验(scipy.stats.ttest_ind, equal_var=False, 单边)。`walk_forward`:2 年训练/6 月验证/6 月步进;三档方法 spec §8.3——①先验权重(即现温度)直接 OOS IC;②IC 加权族权重(train 段各族分 spearman IC,∝max(IC,0) 归一,重合成 composite_z);③横截面 ridge(train 段去均值族分 → np.linalg.lstsq 拟合 fwd,去均值);输出各档 OOS IC 均值/ICIR。

- [ ] **Step 1: 写失败测试**(合成面板:一族分完全决定前向收益 → IC=1;打乱 → IC≈0)

```python
# davis_analyzer/tests/test_thermometer_calibrate.py
"""校准工具:构造温度完全预测/完全不预测两种面板,断言 IC 与分组单调."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _panel(predictive: bool, n_days: int = 60, n_sec: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    for i in range(n_days):
        d = f"2023{i + 1:04d}"
        temp = rng.uniform(0, 100, n_sec)
        fwd = (temp / 100 if predictive else rng.uniform(0, 100, n_sec)) * 0.1
        for j in range(n_sec):
            rows.append({"trade_date": d, "level": "L1",
                         "index_code": f"S{j:03d}", "temperature": temp[j],
                         "composite_z": temp[j], "fwd10": fwd[j]})
    return pd.DataFrame(rows)


def test_rank_ic_extremes():
    from davis_analyzer.thermometer import calibrate

    assert calibrate.daily_rank_ic(_panel(True), 10).mean() > 0.95
    assert abs(calibrate.daily_rank_ic(_panel(False), 10).mean()) < 0.15


def test_quintile_monotonic_and_spread():
    from davis_analyzer.thermometer import calibrate

    rep = calibrate.quintile_report(_panel(True), 10)
    means = [rep[f"q{i}"] for i in range(1, 6)]
    assert all(a < b for a, b in zip(means, means[1:]))  # 单调
    assert rep["spread_p"] < 0.05                          # top-bottom 显著


def test_walk_forward_shape():
    from davis_analyzer.thermometer import calibrate

    panel = _panel(True, n_days=200)
    out = calibrate.walk_forward(panel)
    assert set(out) >= {"prior", "ic_weighted", "ridge"}
    assert out["prior"]["oos_ic_mean"] > 0.9  # 完美预测下先验也达标
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_calibrate.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: 实现 calibrate.py**

```python
"""温度预测力校准:rank IC / 五分组价差 / walk-forward 三档权重(spec §8)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from scipy import stats

from davis_analyzer.config import THERMOMETER_REPORTS_DIR
from davis_analyzer.constants import THERMOMETER_CALIBRATION_TARGETS

_FAMILY_COLS = ["mom_score", "flow_score", "vol_score", "trend_score", "limit_score"]


def build_eval_panel(conn: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    th = pd.read_sql_query(
        "SELECT trade_date, level, index_code, temperature, composite_z, "
        "mom_score, flow_score, vol_score, trend_score, limit_score "
        "FROM thermometer_sector WHERE trade_date>=? AND trade_date<=?",
        conn, params=(start, end))
    px = pd.read_sql_query(
        "SELECT ts_code, trade_date, close FROM sw_daily WHERE trade_date>=? AND trade_date<=? "
        "ORDER BY ts_code, trade_date", conn, params=(start, end))
    px = px.rename(columns={"ts_code": "index_code"})
    for h in (5, 10, 20):
        px[f"fwd{h}"] = px.groupby("index_code")["close"].transform(
            lambda s: s.shift(-h) / s - 1)
    return th.merge(px[["index_code", "trade_date", "fwd5", "fwd10", "fwd20"]],
                    on=["index_code", "trade_date"], how="inner")


def daily_rank_ic(panel: pd.DataFrame, horizon: int) -> pd.Series:
    col = f"fwd{horizon}"
    def _ic(g: pd.DataFrame) -> float:
        if len(g) < 5 or g[col].nunique() < 2:
            return np.nan
        return float(stats.spearmanr(g["temperature"], g[col]).statistic)
    ic = panel.groupby("trade_date").apply(_ic, include_groups=False)
    return ic.dropna()


def quintile_report(panel: pd.DataFrame, horizon: int) -> dict:
    col = f"fwd{horizon}"
    def _q(g: pd.DataFrame) -> pd.Series:
        try:
            q = pd.qcut(g["temperature"], 5, labels=False, duplicates="drop")
        except ValueError:
            return pd.Series(dtype=float)
        return g.groupby(q)[col].mean()
    qs = panel.groupby("trade_date").apply(_q, include_groups=False)
    qs = qs.reset_index()
    out = {f"q{i + 1}": float(qs[i].mean()) for i in range(5) if i in qs.columns}
    top = panel.assign(q=panel.groupby("trade_date")["temperature"].transform(
        lambda s: pd.qcut(s, 5, labels=False, duplicates="drop")))
    t = top[top["q"] == 4][col].dropna()
    b = top[top["q"] == 0][col].dropna()
    if len(t) > 30 and len(b) > 30:
        res = stats.ttest_ind(t, b, equal_var=False)
        # 单边:t>0 且 p/2 < alpha 视为显著
        out["spread"] = float(t.mean() - b.mean())
        out["spread_p"] = float(res.pvalue / 2 if res.statistic > 0 else 1 - res.pvalue / 2)
    return out


def _composite(panel: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    return sum(panel[c] * w for c, w in weights.items())


def walk_forward(panel: pd.DataFrame,
                 train_days: int = 486, valid_days: int = 126) -> dict:
    """滚动 2年训练/6月验证;返回三档方法的 OOS IC 汇总(spec §8.3)."""
    from davis_analyzer.constants import THERMOMETER_WEIGHTS

    dates = sorted(panel["trade_date"].unique())
    oof: dict[str, list[float]] = {"prior": [], "ic_weighted": [], "ridge": []}
    i = train_days
    while i + valid_days <= len(dates):
        tr = panel[panel["trade_date"].isin(dates[i - train_days:i])]
        va = panel[panel["trade_date"].isin(dates[i:i + valid_days])]
        # ① 先验
        oof["prior"] += _eval(va, va["temperature"])
        # ② IC 加权族权重(train)
        ic_w = {}
        for c in _FAMILY_COLS:
            r = tr.groupby("trade_date").apply(
                lambda g, c=c: (stats.spearmanr(g[c], g["fwd10"]).statistic
                                if len(g) >= 5 else np.nan), include_groups=False)
            ic_w[c] = max(float(r.mean()), 0.0)
        s = sum(ic_w.values())
        if s > 0:
            comp = _composite(va, {c: v / s for c, v in ic_w.items()})
            oof["ic_weighted"] += _eval(va, comp)
        # ③ 去均值截面 ridge(train 拟合,OOS 应用)
        Xtr = tr[_FAMILY_COLS].to_numpy()
        ytr = tr["fwd10"].to_numpy()
        mu_x, mu_y = Xtr.mean(0), ytr.mean()
        beta, *_ = np.linalg.lstsq(Xtr - mu_x, ytr - mu_y, rcond=None)
        Xva = va[_FAMILY_COLS].to_numpy()
        oof["ridge"] += _eval(va, pd.Series((Xva - mu_x) @ beta, index=va.index))
        i += valid_days
    def _sum(vals: list[float]) -> dict:
        s = pd.Series(vals, dtype=float)
        return {"oos_ic_mean": float(s.mean()), "oos_icir": float(s.mean() / s.std() if s.std() > 0 else 0.0),
                "n_days": int(len(s))}
    return {k: _sum(v) for k, v in oof.items() if v}


def _eval(va: pd.DataFrame, score: pd.Series) -> list[float]:
    tmp = va.assign(_s=score)
    def _ic(g):
        if len(g) < 5 or g["_s"].nunique() < 2:
            return np.nan
        return float(stats.spearmanr(g["_s"], g["fwd10"]).statistic)
    return [v for v in tmp.groupby("trade_date").apply(_ic, include_groups=False)
            if v == v]


def run_calibration(conn: sqlite3.Connection, start: str, end: str) -> Path:
    panel = build_eval_panel(conn, start, end)
    ic5, ic10, ic20 = (daily_rank_ic(panel, h) for h in (5, 10, 20))
    quint = quintile_report(panel, 10)
    wf = walk_forward(panel)
    tgt = THERMOMETER_CALIBRATION_TARGETS
    verdict = {}
    for name, r in wf.items():
        verdict[name] = (r["oos_ic_mean"] >= tgt["min_ic"]
                         and r["oos_icir"] >= tgt["min_icir"]
                         and quint.get("spread_p", 1.0) < tgt["spread_pvalue"])
    lines = [
        f"# 温度计校准报告 [{start} → {end}]",
        f"- 样本: {len(panel):,} 行 / {panel['trade_date'].nunique()} 日",
        f"- 全样本 rank IC: 5日 {ic5.mean():.4f} / 10日 {ic10.mean():.4f} / 20日 {ic20.mean():.4f}",
        f"- 五分组 10日均值: { {k: round(v, 5) for k, v in quint.items() if k.startswith('q')} }",
        f"- top-bottom 价差 {quint.get('spread', float('nan')):.5f}, 单边 p={quint.get('spread_p', float('nan')):.4f}",
        "", "## walk-forward 样本外(2年训练/6月验证滚动)",
    ]
    for name, r in wf.items():
        lines.append(f"- {name}: IC {r['oos_ic_mean']:.4f} / ICIR {r['oos_icir']:.2f} / {r['n_days']} 验证日 → {'达标' if verdict[name] else '未达标'}")
    lines += ["", f"验收线: IC≥{tgt['min_ic']}, ICIR≥{tgt['min_icir']}, 价差 p<{tgt['spread_pvalue']}",
              f"结论: {'通过' if any(verdict.values()) else '未通过——按 spec §8.4 停止并回到用户决策'}"]
    out = THERMOMETER_REPORTS_DIR / f"{start}-{end}_校准报告.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    logger.info("校准报告: {}", out)
    return out
```

(实现注:`groupby().apply` 的 include_groups 参数在 pandas≥2.2 可用,项目 pandas 版本满足;`daily_rank_ic` 返回逐日 IC Series。)

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_thermometer_calibrate.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/thermometer/calibrate.py davis_analyzer/tests/test_thermometer_calibrate.py davis_analyzer/thermometer/cli.py
git commit -m "feat(thermometer): 校准工具——rankIC/五分组价差/walk-forward三档权重与验收判定"
```

---

### Task 12: 校准执行与验收门(P2 执行,回到用户)

**Files:** 无新代码(执行 + 报告;通过则可能更新 constants 权重)

- [ ] **Step 1: 跑全窗口校准**

Run: `cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m davis_analyzer.thermometer calibrate 2>&1 | tee logs/thermo_calibrate.log`
Expected: 产出 `davis_analyzer/thermometer/reports/20220104-<最新>_校准报告.md`;面板约 16 万行,walk-forward 数秒级。

- [ ] **Step 2: 判定与分支(硬性验收,spec §8.4)**

- **通过**(任一档 OOS 达标):若通过档 = 先验权重 → 不改代码;若 = IC 加权或 ridge → 把该档权重写回 `constants.py`(族权重归一后)并在 commit message 注明实验依据,重跑 `score_history` + 日报,提交。
- **未通过**:输出诊断(各族 IC 时序图数据、失效模式)追加到校准报告,**停止,向用户汇报三个档的完整数字与诊断,等待决策**——不允许静默降级部署。

- [ ] **Step 3: Commit**

```bash
git add davis_analyzer/thermometer davis_analyzer/constants.py
git commit -m "feat(thermometer): P2校准执行——<通过:权重/结论摘要 或 未通过:诊断报告待用户决策>"
```

---

### Task 13: cardgen thermo 卡片(P3)

**Files:**
- Modify: `davis_analyzer/cardgen/daily.py`(加 `fetch_thermo_bundle / build_thermo / thermo_insights`,扩 `publish_copy` 与 `generate` 的 kind 分支)
- Modify: `scripts/daily_market_cards.py`(`--type` 增加 `thermo`,kind 元组扩展)
- Test: `davis_analyzer/tests/test_cardgen_daily.py`(追加 thermo 用例)

**Interfaces:**
- Consumes: market_data.db 的 thermometer_sector/thermometer_market/sw_index(只读);`cardgen.daily` 现有 `Fact/_fact/write_project/FOOT/generate` 基建;`scripts/content_publisher/queue.py enqueue`(由 daily_market_cards.enqueue_one 复用,不改动)。
- Produces: `kind='thermo'` 全链路;topic `板块温度/{day}`;5 页卡(封面/L1热榜/L2热榜/大盘五维/收束+洞察)。facts source_ref 形如 `market_data.db:thermometer_sector@{day}:L1:top1.temperature`;`source_kind` 沿用 "stockhot"(指纹语义一致,不新增 kind)。

- [ ] **Step 1: 写失败测试**(放 test_cardgen_daily.py,仿 ladder 用例)

```python
# davis_analyzer/tests/test_cardgen_daily.py 追加
def test_build_thermo_card(tmp_path, monkeypatch):
    """thermo 卡:facts 登记齐/五页结构/洞察零数字."""
    from davis_analyzer.cardgen import daily

    bundle = {
        "day": "2026-09-11",
        "l1": [{"name": "半导体", "temperature": 95.0, "delta_temp5": 8.0},
               {"name": "农林牧渔", "temperature": 5.0, "delta_temp5": -6.0}],
        "l2": [{"name": "光伏设备", "temperature": 92.0, "delta_temp5": 7.0}],
        "market": {"temperature": 42.0, "regime_label": "温和",
                   "dims": {"趋势": 0.45, "宽度": 0.5, "量能": 0.4,
                            "资金": 0.38, "情绪": 0.6}},
    }
    facts, spec = daily.build_thermo("2026-09-11", bundle)
    assert len(spec["cards"]) == 5
    assert any(f.id == "l1_top1_temp" for f in facts)
    # 洞察库零数字
    import re
    for s in daily.thermo_insights(bundle):
        assert not re.search(r"\d", s), s
    # 发稿文案存在且零数字
    copy = daily.publish_copy("thermo", "2026-09-11")
    assert not re.search(r"[0-9０-９]", copy["body"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_cardgen_daily.py::test_build_thermo_card -v`
Expected: FAIL — build_thermo 不存在

- [ ] **Step 3: 实现**(daily.py 追加;spec 结构仿 build_ladder 五页)

```python
# ── 板块温度卡(P3;数据源 market_data.db thermometer 表) ────────────────

_THERMO_COPY = {"title": "板块温度计 · 每日市场热度",
                "body": "今天哪些行业在发烧,哪些在退烧。温度由量能、主力资金、动量、"
                        "趋势与涨停密度合成,仅供学习参考,不构成投资建议。",
                "tags": "#板块温度计 #市场结构 #每日复盘 #资金流"}


def _market_db() -> Path:
    return REPO_ROOT / "storage" / "database" / "market_data.db"


def fetch_thermo_bundle(day: str) -> dict:
    """day 为 dash 日期;读 thermometer_sector/market 最新快照(day 空取最新)."""
    con = _ro_conn(_market_db())
    try:
        d = day.replace("-", "") or con.execute(
            "SELECT MAX(trade_date) FROM thermometer_sector").fetchone()[0]
        sec = pd.read_sql_query(
            "SELECT level, name, temperature, delta_temp5, hot_streak "
            "FROM thermometer_sector WHERE trade_date=?", con, params=(d,))
        mkt = pd.read_sql_query(
            "SELECT * FROM thermometer_market WHERE trade_date=?", con, params=(d,))
        if sec.empty or mkt.empty:
            raise DailyDataMissing(f"{d} 温度数据缺失(thermometer run 未完成?)")
        row = mkt.iloc[0]
        return {
            "day": f"{d[:4]}-{d[4:6]}-{d[6:]}",
            "l1": sec[sec["level"] == "L1"].nlargest(5, "temperature").to_dict("records"),
            "l2": sec[sec["level"] == "L2"].nlargest(5, "temperature").to_dict("records"),
            "cold": sec[sec["level"] == "L1"].nsmallest(3, "temperature").to_dict("records"),
            "market": {"temperature": float(row["temperature"]),
                       "regime_label": row["regime_label"],
                       "dims": {"趋势": row["trend_dim"], "宽度": row["width_dim"],
                                "量能": row["volume_dim"], "资金": row["flow_dim"],
                                "情绪": row["sentiment_dim"]}},
        }
    finally:
        con.close()
```

`thermo_insights(bundle)`:预审洞察库(全部零数字,锚 spec 方法论),按形态机械选用,例如:温度分层结构(最高温−最低温>70 → "冷热分化极端的日子,主线集中度比指数涨跌更能定义这个市场");连热 streak≥3 的板块存在 → "连续高温板块是资金合力的痕迹,但高温本身不等于可以追,温度计只测温不决策";大盘温度与板块最高温背离(市场<35 且 top>85)→ "大盘温吞而局部沸腾,是典型结构市——这种行情里板块温度表的参考价值高于指数"。兜底一条:"温度是合成的结构数据:量能给燃料,资金给方向,动量与趋势给惯性,涨停密度给赚钱效应——合起来读才完整"。

`build_thermo(day, bundle)`:facts 至少含 `l1_top1_temp / l1_top1_name(数字化 f_fact)/ l2_top1_temp / mkt_temp / mkt_label`;L1/L2 榜表格 rows 用 `$fact` 引用;五页 = cover(theme red,stats: mkt_temp+档位标签文字)/ table L1(theme orange)/ table L2(theme blue)/ table 大盘五维(theme green,rows 维度名+分位值,注意分位值也是数字→每个都过 facts)/ summary(theme lavender,kbox = thermo_insights)。所有数字 display 走 `_fact` 登记指纹;板块名走 `_digit_safe`。

`generate()` 的 kind 分支加:

```python
    elif kind == "thermo":
        facts, spec = build_thermo(day, fetch_thermo_bundle(day))
        topic = f"板块温度/{day}"
```

`_PUBLISH_COPY["thermo"] = _THERMO_COPY`。`scripts/daily_market_cards.py`:`--type` choices 加 `"thermo"`,`all` 不含 thermo(时序不同,17:50 无数据),单跑 `--type thermo --enqueue` 由 19:45 cron 触发(见 Task 14)。

- [ ] **Step 4: 跑测试确认通过 + 真数据烟测**

Run: `.venv/bin/python -m pytest davis_analyzer/tests/test_cardgen_daily.py -v && .venv/bin/python scripts/daily_market_cards.py --type thermo --no-render`
Expected: 测试全过(含既有 ladder/lhb 用例);烟测产出工程目录(不渲染),validator 四闸过。

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/cardgen/daily.py scripts/daily_market_cards.py davis_analyzer/tests/test_cardgen_daily.py
git commit -m "feat(cardgen): 板块温度卡——五页数据叙事卡入池,facts指纹锚thermometer表,发稿文案零数字锁定"
```

---

### Task 14: cron 挂载 + SOP/AGENTS 同步 + 收尾(P4)

**Files:**
- Modify: `davis_analyzer/SOP.md`(温度计权重段)、`AGENTS.md`(模块划分清单加一行 thermometer)、`docs/代码库索引.md`(如有模块索引则加行)
- crontab:3 条新条目

- [ ] **Step 1: SOP.md 加温度计权重段**(与 constants 一致,格式仿 PROSPERITY_WEIGHTS 段;写明"校准依据见 thermometer/reports 校准报告")

- [ ] **Step 2: AGENTS.md 模块划分段追加**(仿 limitup 行,一句话+CLI)

```
**板块温度计子系统**(独立):`thermometer/`(universe 申万L1/L2池与成分 → data sw_daily/ths_daily回补 → moneyflow_agg 成分自聚合主力净额 → factors 五族因子(水平/斜率) → scoring 截面分位温度+大盘五维 market_temp → calibrate IC/walk-forward 硬验收 → report 日报 + cardgen thermo 卡;表挂 market_data.db 九张,CLI: python -m davis_analyzer.thermometer {backfill|run|calibrate|report|status})。注意:温度只测温不决策,权重单一真相源在 constants.py THERMOMETER_WEIGHTS,校准未达标禁止部署评分入口(spec §8.4)。
```

- [ ] **Step 3: crontab 挂载**(先 `crontab -l` 备份确认无冲突再追加)

```bash
crontab -l > logs/crontab_backup_$(date +%Y%m%d).txt
( crontab -l; echo "# thermometer 板块温度计(2026-09-13)"; \
  echo "35 19 * * 1-5 cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m davis_analyzer.thermometer run >> logs/thermo_run.log 2>&1"; \
  echo "45 19 * * 1-5 cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python scripts/daily_market_cards.py --type thermo --enqueue >> logs/thermo_card.log 2>&1"; \
  echo "0 8 * * 0 cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python -m davis_analyzer.thermometer backfill --universe-only >> logs/thermo_universe.log 2>&1" ) | crontab -
crontab -l | tail -4
```

- [ ] **Step 4: 全量回归 + 手动 run 烟测**

Run: `.venv/bin/python -m pytest davis_analyzer/tests -x -q 2>&1 | tail -5 && .venv/bin/python -m davis_analyzer.thermometer run --no-card && .venv/bin/python -m davis_analyzer.thermometer status`
Expected: 全测试绿;run 增量幂等(重复跑无重复行);status 九表行数正常。

- [ ] **Step 5: Commit**

```bash
git add davis_analyzer/SOP.md AGENTS.md docs/代码库索引.md
git commit -m "docs(thermometer): SOP权重段+AGENTS模块行+索引;crontab三挂(19:35 run/19:45卡/周日universe)"
```

---

## 任务依赖图

```
T1 骨架/表 ─→ T2 universe ─→ T3 data ─┐
   └────────→ T4 moneyflow_agg ────────┼→ T6 scoring ─→ T8 report ─→ T9 cli ─→ T10 回补执行 ─→ T12 校准执行
                                          T5 factors ──↗        T7 market_temp ↗      T11 calibrate ↗
T13 卡片(依赖 T9/T10)─→ T14 运维收尾(依赖全部)
```

## Self-Review 记录(已检查)

1. **Spec 覆盖**:§3 口径→T2/T3;§4 表/回补→T1/T3/T4/T10;§5 因子→T5/T6;§6 合成→T6;§7 大盘→T7;§8 校准→T11/T12;§9.1 日报→T8/T9;§9.2 卡片→T13;§10 工程/CLI→T1/T9;§11 测试→各任务 TDD;§12 局限→已在 spec,T10 验证覆盖抽查;§13 非目标未越界(概念层只建数据=T2/T3);§14 阶段→任务序。覆盖完整。
2. **占位符扫描**:Task 9 `_build_card` 在 T13 前为显式 no-op log(有意的接线点,非 TBD);Task 7/8 内嵌的"实现注意"是精确指令非占位。无 TODO/TBD。
3. **类型一致性**:panel 列契约、函数签名与 Global Interfaces 一致;`family_scores` 五列名已锁定;`sw_daily` 与 `sector_moneyflow_daily` 的 (level,index_code) 键在 T4/T6 对齐;limit_pool dash 日期/裸代码转换在 T6 显式处理。
