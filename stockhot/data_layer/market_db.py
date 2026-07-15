"""统一市场数据库 — market_data.db 连接管理 + schema 初始化.

本模块定义**唯一的市场行情数据源** ``market_data.db``，作为 stockhot（盘面采集）
与 davis_analyzer（量化基本面）共享的存储层，消除跨库隔离导致的重复取数。

设计原则（继承 davis_analyzer/tushare_client.py 的成熟实践）：
- **结构化复合主键**（ts_code+trade_date），支持范围查询和增量拉取
- **幂等 schema 迁移**（CREATE TABLE IF NOT EXISTS + ALTER TABLE ADD COLUMN）
- **WAL 模式**：读多写少场景下并发性能更优
- **显式 Asia/Shanghai 时区**：A 股按北京时间收盘

表结构分三类：
1. 基础行情表（从 davis tushare_cache.db 继承）：stock_basic / daily_price / daily_basic /
   financial / hk_hold / index_daily
2. 盘面采集表（从 stockhot.db daily_data JSON blob 结构化）：limit_pool / dragon_tiger /
   fund_flow_sector / fund_flow_market / index_technical
3. 元数据表：macro_indicator（宏观缓存）/ scan_log（采集日志）

详见 ``docs/方法论/统一市场数据架构.md``。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from stockhot.core.config import STORAGE_DIR

# ── 路径 ──────────────────────────────────────────────────────────────

MARKET_DB_PATH: Path = STORAGE_DIR / "database" / "market_data.db"

# 连接锁。SQLite 的 connection 对象默认禁止跨线程复用，这里每次新建连接，
# 但用一个全局锁串行化 schema 初始化等写操作，避免并发 DDL 冲突。
_init_lock = threading.Lock()
_initialized = False


# ── Schema 定义 ──────────────────────────────────────────────────────

_SCHEMA_STATEMENTS: list[str] = [
    # ═══ 基础行情表（继承 davis tushare_cache.db 结构）═════════════════
    # 股票列表（~5500 只 A 股），7 天 TTL 全量刷新
    """CREATE TABLE IF NOT EXISTS stock_basic (
        ts_code    TEXT PRIMARY KEY,
        name       TEXT,
        industry   TEXT,
        list_status TEXT,
        fetched_at REAL
    )""",
    # 个股日线行情 — 扩展列兼容两套取数（stockhot pro_bar + davis daily+adj_factor）
    # open/high/low/close/pre_close/pct_chg/vol/amount 来自 Tushare daily
    # adj_factor 来自 Tushare adj_factor
    """CREATE TABLE IF NOT EXISTS daily_price (
        ts_code    TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        open       REAL,
        high       REAL,
        low        REAL,
        close      REAL NOT NULL,
        pre_close  REAL,
        pct_chg    REAL,
        vol        REAL,
        amount     REAL,
        adj_factor REAL,
        fetched_at REAL,
        PRIMARY KEY (ts_code, trade_date)
    )""",
    # 个股每日估值指标（PE/PB/PS/总市值），24h TTL 增量
    """CREATE TABLE IF NOT EXISTS daily_basic (
        ts_code    TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        pe_ttm     REAL,
        pb         REAL,
        ps         REAL,
        total_mv   REAL,
        fetched_at REAL,
        PRIMARY KEY (ts_code, trade_date)
    )""",
    # 财务三表 + 指标（永久缓存，按 end_date 季度键）
    """CREATE TABLE IF NOT EXISTS financial (
        ts_code    TEXT NOT NULL,
        end_date   TEXT NOT NULL,
        endpoint   TEXT NOT NULL,
        payload    TEXT,
        fetched_at REAL,
        PRIMARY KEY (ts_code, end_date, endpoint)
    )""",
    # 北向资金个股持股（增量）
    """CREATE TABLE IF NOT EXISTS hk_hold (
        ts_code    TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        vol        REAL,
        ratio      REAL,
        fetched_at REAL,
        PRIMARY KEY (ts_code, trade_date)
    )""",
    # 指数日线 — 新增统一表（index_technical 和 volatility 共享，消除重复拉取）
    """CREATE TABLE IF NOT EXISTS index_daily (
        ts_code    TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        open       REAL,
        high       REAL,
        low        REAL,
        close      REAL NOT NULL,
        vol        REAL,
        amount     REAL,
        pct_chg    REAL,
        fetched_at REAL,
        PRIMARY KEY (ts_code, trade_date)
    )""",

    # ═══ 盘面采集表（从 stockhot.db daily_data JSON blob 结构化）═══════
    # 统一涨停/炸板/跌停池（pool_kind 鉴别列）
    """CREATE TABLE IF NOT EXISTS limit_pool (
        trade_date        TEXT NOT NULL,
        ts_code           TEXT NOT NULL,
        pool_kind         TEXT NOT NULL,  -- 'limit_up' / 'broken' / 'limit_down'
        name              TEXT,
        sector            TEXT,
        change_pct        REAL,
        seal_amount       REAL,
        consecutive_boards INTEGER,
        broken_count      INTEGER,
        first_seal_time   TEXT,
        last_seal_time    TEXT,
        turnover_rate     REAL,
        fetched_at        REAL,
        PRIMARY KEY (trade_date, ts_code, pool_kind)
    )""",
    # 龙虎榜明细
    """CREATE TABLE IF NOT EXISTS dragon_tiger (
        trade_date   TEXT NOT NULL,
        ts_code      TEXT NOT NULL,
        name         TEXT,
        reason       TEXT,
        close        REAL,
        change_pct   REAL,
        net_buy      REAL,
        buy_amount   REAL,
        sell_amount  REAL,
        list_date    TEXT,
        fetched_at   REAL,
        PRIMARY KEY (trade_date, ts_code)
    )""",
    # 板块资金流（110 个申万/东财板块）
    """CREATE TABLE IF NOT EXISTS fund_flow_sector (
        trade_date   TEXT NOT NULL,
        sector_name  TEXT NOT NULL,
        change_pct   REAL,
        main_net     REAL,
        main_pct     REAL,
        huge_net     REAL,
        large_net    REAL,
        medium_net   REAL,
        small_net    REAL,
        fetched_at   REAL,
        PRIMARY KEY (trade_date, sector_name)
    )""",
    # 大盘资金流时间序列
    """CREATE TABLE IF NOT EXISTS fund_flow_market (
        trade_date   TEXT NOT NULL,
        seq          INTEGER NOT NULL,
        main_net     REAL,
        main_pct     REAL,
        huge_net     REAL,
        large_net    REAL,
        medium_net   REAL,
        small_net    REAL,
        fetched_at   REAL,
        PRIMARY KEY (trade_date, seq)
    )""",
    # 指数技术面（reasons/signals 保留 JSON，因结构嵌套不规则）
    """CREATE TABLE IF NOT EXISTS index_technical (
        trade_date         TEXT NOT NULL,
        ts_code            TEXT NOT NULL,
        close              REAL,
        pct_chg            REAL,
        technical_score    REAL,
        technical_state    TEXT,
        stage              TEXT,
        stage_confidence   INTEGER,
        expected_action    TEXT,
        reasons_json       TEXT,   -- list[str]，保留 JSON
        signals_json       TEXT,   -- dict，保留 JSON
        fetched_at         REAL,
        PRIMARY KEY (trade_date, ts_code)
    )""",

    # ═══ 元数据表 ════════════════════════════════════════════════════
    # 宏观指标缓存（PMI/CPI/PPI/M2/Shibor/LPR），给 macro 模块加缓存
    """CREATE TABLE IF NOT EXISTS macro_indicator (
        indicator_name TEXT NOT NULL,
        report_date    TEXT NOT NULL,
        value          REAL,
        unit           TEXT,
        fetched_at     REAL,
        PRIMARY KEY (indicator_name, report_date)
    )""",
    # 分析师研报缓存（等价 davis research_cache），按 ts_code + report_date 查询
    """CREATE TABLE IF NOT EXISTS research (
        ts_code      TEXT NOT NULL,
        report_date  TEXT NOT NULL,
        rating       TEXT,
        target_price REAL,
        org_name     TEXT,
        fetched_at   REAL,
        PRIMARY KEY (ts_code, report_date, org_name)
    )""",
    # iVIX/QVIX 历史（中国 50ETF 波动率指数），AKShare index_option_50etf_qvix 唯一源，
    # volatility 和 invest_sop/overseas 共享缓存避免重复拉取
    """CREATE TABLE IF NOT EXISTS ivix_history (
        trade_date TEXT PRIMARY KEY,
        close      REAL,
        fetched_at REAL
    )""",
    # 采集日志 — 解决"不知道哪个模块跑没跑"的问题
    """CREATE TABLE IF NOT EXISTS scan_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date    TEXT NOT NULL,
        module_name   TEXT NOT NULL,
        status        TEXT NOT NULL,  -- 'success' / 'failed' / 'no_data'
        error_msg     TEXT,
        started_at    REAL,
        finished_at   REAL,
        duration_sec  REAL,
        rows_affected INTEGER,
        created_at    REAL
    )""",
]


def init_db(db_path: Path | None = None) -> None:
    """初始化 market_data.db 的全部表（幂等，线程安全）.

    多次调用安全：CREATE TABLE IF NOT EXISTS 保证已存在的表不受影响。
    WAL 模式在首次创建连接时设置。
    """
    global _initialized
    if _initialized and db_path is None:
        return  # 已初始化过默认库

    path = db_path or MARKET_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    with _init_lock:
        with sqlite3.connect(str(path)) as conn:
            # WAL 模式：读多写少场景下并发性能更优
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")  # WAL 下 NORMAL 足够安全
            for stmt in _SCHEMA_STATEMENTS:
                conn.execute(stmt)
            conn.commit()

    if db_path is None:
        _initialized = True


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """获取一个 market_data.db 的连接（每次新建，WAL 模式）.

    调用方负责在 finally 中 close()。推荐用法::

        with closing(get_connection()) as conn:
            conn.execute("SELECT ...")
    """
    path = db_path or MARKET_DB_PATH
    if not _initialized and db_path is None:
        init_db()
    elif db_path is not None and not path.exists():
        init_db(path)

    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def table_info(table_name: str, db_path: Path | None = None) -> list[str]:
    """返回指定表的列名列表（调试/迁移用）."""
    with get_connection(db_path) as conn:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [r[1] for r in rows]
