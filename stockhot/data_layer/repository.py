"""数据仓库 — market_data.db 的统一读写 API.

本模块是 stockhot 与 davis_analyzer 共享的**唯一数据读写入口**。
所有 market_data.db 的读写都经过这里，确保：
- 写入时自动加 fetched_at 时间戳
- 读取时自动应用缓存策略（三段式增量）
- 表名/列名集中管理，避免各模块拼写不一致

核心 API：
- ``get_stock_list()`` — A 股列表（7 天 TTL）
- ``get_daily_prices(ts_code, start, end)`` — 个股日线（增量缓存）
- ``get_daily_by_date(trade_date)`` — 全市场某日行情（分页）
- ``save_daily_prices(df)`` — 批量写日线
- ``save_scan_result(module, trade_date, data)`` — 采集结果写入
- ``get_scan_result(module, trade_date)`` — 读取采集结果
- ``log_scan(trade_date, module, status, ...)`` — 写采集日志

第一阶段聚焦个股日线去重（get_daily_prices / save_daily_prices），
盘面采集表（limit_pool/dragon_tiger/...）的读写 API 先提供骨架，
供后续阶段从 stockhot.db daily_data JSON 迁移过来。
"""

from __future__ import annotations

import json
import time
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from stockhot.core.logging import logger
from stockhot.data_layer.cache import (
    TTL_DAILY,
    TTL_STOCK_BASIC,
    CacheDecision,
    decide_by_date_range,
    is_expired,
    now_ts,
)
from stockhot.data_layer.market_db import get_connection, init_db
from stockhot.data_layer.tushare_gateway import TushareGateway, get_gateway

_TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")


class MarketDataRepository:
    """market_data.db 的统一读写仓库."""

    def __init__(self, gateway: TushareGateway | None = None) -> None:
        self._gw = gateway or get_gateway()
        init_db()

    # ═══════════════════════════════════════════════════════════════════
    # 基础行情：股票列表
    # ═══════════════════════════════════════════════════════════════════

    def get_stock_list(self, force_refresh: bool = False) -> pd.DataFrame:
        """获取 A 股全量股票列表（7 天 TTL，过期全量刷新）.

        替代 davis TushareClient.get_stock_list + stockhot 各处裸调 stock_basic。
        """
        with closing(get_connection()) as conn:
            if not force_refresh:
                row = conn.execute(
                    "SELECT MAX(fetched_at) FROM stock_basic"
                ).fetchone()
                latest = row[0] if row else None
                if latest is not None and not is_expired(latest, TTL_STOCK_BASIC):
                    rows = conn.execute(
                        "SELECT ts_code, name, industry, list_status FROM stock_basic"
                    ).fetchall()
                    return pd.DataFrame(rows, columns=["ts_code", "name", "industry", "list_status"])

        # 刷新：全量拉取后替换
        logger.info("Refreshing stock_basic (7d TTL expired or forced)")
        df = self._gw.get_stock_list()
        if df.empty:
            return df
        ts = now_ts()
        with closing(get_connection()) as conn:
            conn.execute("DELETE FROM stock_basic")
            conn.executemany(
                "INSERT OR REPLACE INTO stock_basic (ts_code, name, industry, list_status, fetched_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [(r.ts_code, r.name, r.industry, r.list_status, ts) for r in df.itertuples()],
            )
            conn.commit()
        return df

    # ═══════════════════════════════════════════════════════════════════
    # 基础行情：个股日线（核心去重点）
    # ═══════════════════════════════════════════════════════════════════

    def get_daily_prices(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """获取个股日线行情（增量缓存，三段式判定）.

        替代 davis TushareClient.get_daily_prices + stockhot technical_analyzer
        的无缓存 pro_bar 调用。两者现在共享同一份缓存。

        参数：
            ts_code: 股票代码，如 "000001.SZ"
            start_date/end_date: YYYYMMDD 格式

        返回：
            DataFrame，列含 ts_code/trade_date/open/high/low/close/pct_chg/vol/amount/adj_factor
        """
        # 检查缓存
        with closing(get_connection()) as conn:
            row = conn.execute(
                "SELECT MAX(trade_date), MAX(fetched_at) FROM daily_price WHERE ts_code=?",
                (ts_code,),
            ).fetchone()
            max_date = row[0] if row else None
            latest_fetched = row[1] if row else None

        decision = decide_by_date_range(max_date, latest_fetched, start_date, end_date)

        if not decision.use_cache and decision.fetch_start <= end_date:
            # 增量拉取 daily + adj_factor
            logger.info(
                f"daily_price incremental fetch: {ts_code} {decision.fetch_start}→{end_date}"
            )
            daily_df = self._gw.call(
                "daily", ts_code=ts_code,
                start_date=decision.fetch_start, end_date=end_date,
                fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
            )
            adj_df = self._gw.call(
                "adj_factor", ts_code=ts_code,
                start_date=decision.fetch_start, end_date=end_date,
                fields="ts_code,trade_date,adj_factor",
            )
            if not daily_df.empty:
                self._save_daily_prices(daily_df, adj_df)

        # 从缓存读取请求范围
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT ts_code, trade_date, open, high, low, close, pre_close, "
                "pct_chg, vol, amount, adj_factor "
                "FROM daily_price WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
                "ORDER BY trade_date",
                (ts_code, start_date, end_date),
            ).fetchall()
        return pd.DataFrame(
            rows,
            columns=["ts_code", "trade_date", "open", "high", "low", "close",
                      "pre_close", "pct_chg", "vol", "amount", "adj_factor"],
        )

    def get_daily_by_date(self, trade_date: str, use_cache: bool = True) -> pd.DataFrame:
        """获取全市场某日个股行情（分页拉取，用于盘后重建）.

        替代 daily-market-scan 各模块独立拉全市场行情。
        """
        # 检查缓存
        if use_cache:
            with closing(get_connection()) as conn:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM daily_price WHERE trade_date=?", (trade_date,)
                ).fetchone()[0]
                if cnt > 1000:  # 已有当日全市场数据
                    rows = conn.execute(
                        "SELECT ts_code, trade_date, open, high, low, close, pre_close, "
                        "pct_chg, vol, amount, adj_factor "
                        "FROM daily_price WHERE trade_date=? ORDER BY ts_code",
                        (trade_date,),
                    ).fetchall()
                    return pd.DataFrame(
                        rows,
                        columns=["ts_code", "trade_date", "open", "high", "low", "close",
                                  "pre_close", "pct_chg", "vol", "amount", "adj_factor"],
                    )

        # 全市场分页拉取
        df = self._gw.get_daily_by_date(trade_date)
        adj_df = self._gw.get_adj_factor(trade_date)
        if not df.empty:
            self._save_daily_prices(df, adj_df)
        return df

    def _save_daily_prices(self, daily_df: pd.DataFrame, adj_df: pd.DataFrame) -> int:
        """批量写日线（内部，合并 daily + adj_factor）."""
        ts = now_ts()
        # 合并复权因子
        if not adj_df.empty:
            adj_map = dict(zip(adj_df["trade_date"], adj_df["adj_factor"]))
            daily_df = daily_df.copy()
            daily_df["adj_factor"] = daily_df["trade_date"].map(adj_map)
        else:
            daily_df["adj_factor"] = None

        records = []
        for r in daily_df.itertuples():
            records.append((
                r.ts_code, r.trade_date,
                getattr(r, "open", None), getattr(r, "high", None),
                getattr(r, "low", None), r.close,
                getattr(r, "pre_close", None), getattr(r, "pct_chg", None),
                getattr(r, "vol", None), getattr(r, "amount", None),
                getattr(r, "adj_factor", None),
                ts,
            ))

        with closing(get_connection()) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO daily_price "
                "(ts_code, trade_date, open, high, low, close, pre_close, "
                "pct_chg, vol, amount, adj_factor, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                records,
            )
            conn.commit()
        logger.info(f"Saved {len(records)} daily_price rows")
        return len(records)

    # ═══════════════════════════════════════════════════════════════════
    # 基础行情：指数日线（统一 index_technical + volatility 的重复拉取）
    # ═══════════════════════════════════════════════════════════════════

    def get_index_daily(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取指数日线（增量缓存）.

        替代 index_technical 和 volatility 两个模块各自独立拉 index_daily。
        """
        with closing(get_connection()) as conn:
            row = conn.execute(
                "SELECT MAX(trade_date), MAX(fetched_at) FROM index_daily WHERE ts_code=?",
                (ts_code,),
            ).fetchone()
            max_date = row[0] if row else None
            latest_fetched = row[1] if row else None

        decision = decide_by_date_range(max_date, latest_fetched, start_date, end_date)

        if not decision.use_cache and decision.fetch_start <= end_date:
            df = self._gw.call(
                "index_daily", ts_code=ts_code,
                start_date=decision.fetch_start, end_date=end_date,
                fields="ts_code,trade_date,open,high,low,close,vol,amount,pct_chg",
            )
            if not df.empty:
                ts = now_ts()
                records = [
                    (r.ts_code, r.trade_date, getattr(r, "open", None),
                     getattr(r, "high", None), getattr(r, "low", None), r.close,
                     getattr(r, "vol", None), getattr(r, "amount", None),
                     getattr(r, "pct_chg", None), ts)
                    for r in df.itertuples()
                ]
                with closing(get_connection()) as conn:
                    conn.executemany(
                        "INSERT OR REPLACE INTO index_daily "
                        "(ts_code, trade_date, open, high, low, close, vol, amount, pct_chg, fetched_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        records,
                    )
                    conn.commit()

        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT ts_code, trade_date, open, high, low, close, vol, amount, pct_chg "
                "FROM index_daily WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
                "ORDER BY trade_date",
                (ts_code, start_date, end_date),
            ).fetchall()
        return pd.DataFrame(
            rows,
            columns=["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount", "pct_chg"],
        )

    # ═══════════════════════════════════════════════════════════════════
    # 基础行情：估值 / 财务 / 北向 / 研报（davis 等价读取，供 stockhot 侧复用）
    # ═══════════════════════════════════════════════════════════════════

    def get_daily_basic(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取个股每日估值（PE/PB/PS/总市值）.

        替代 davis TushareClient.get_daily_basic + stockhot advisor 的无缓存 daily_basic。
        缓存由 davis 侧的 TushareClient 填充（迁移后写入同一张 daily_basic 表）。
        """
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT ts_code, trade_date, pe_ttm, pb, ps, total_mv "
                "FROM daily_basic WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
                "ORDER BY trade_date DESC",
                (ts_code, start_date, end_date),
            ).fetchall()
        return pd.DataFrame(
            rows, columns=["ts_code", "trade_date", "pe_ttm", "pb", "ps", "total_mv"]
        )

    def get_financial(
        self, ts_code: str, endpoint: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取财务数据（income/balancesheet/cashflow/fina_indicator/forecast/dividend）.

        payload 是每行一个 JSON（与 davis financial_cache 格式一致），解包为 DataFrame。
        """
        import json as _json
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT payload FROM financial "
                "WHERE ts_code=? AND endpoint=? AND end_date>=? AND end_date<=? "
                "ORDER BY end_date DESC",
                (ts_code, endpoint, start_date, end_date),
            ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([_json.loads(r[0]) for r in rows])

    def get_hk_hold(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取北向资金个股持股."""
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT ts_code, trade_date, vol, ratio FROM hk_hold "
                "WHERE ts_code=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date",
                (ts_code, start_date, end_date),
            ).fetchall()
        return pd.DataFrame(rows, columns=["ts_code", "trade_date", "vol", "ratio"])

    def get_research_reports(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取分析师研报."""
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT ts_code, report_date, rating, target_price, org_name FROM research "
                "WHERE ts_code=? AND report_date>=? AND report_date<=? ORDER BY report_date",
                (ts_code, start_date, end_date),
            ).fetchall()
        return pd.DataFrame(
            rows, columns=["ts_code", "report_date", "rating", "target_price", "org_name"]
        )

    # ═══════════════════════════════════════════════════════════════════
    # 盘面采集读取（结构化表，供 after-hours-review 等消费）
    # ═══════════════════════════════════════════════════════════════════

    def get_limit_pool(self, trade_date: str, pool_kind: str | None = None) -> list[dict]:
        """读取涨停/炸板/跌停池（结构化）.

        pool_kind: 'limit_up' / 'broken' / 'limit_down' / None（全部）
        """
        with closing(get_connection()) as conn:
            if pool_kind:
                rows = conn.execute(
                    "SELECT ts_code, pool_kind, name, sector, change_pct, seal_amount, "
                    "consecutive_boards, broken_count, first_seal_time, last_seal_time, turnover_rate "
                    "FROM limit_pool WHERE trade_date=? AND pool_kind=?",
                    (trade_date, pool_kind),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT ts_code, pool_kind, name, sector, change_pct, seal_amount, "
                    "consecutive_boards, broken_count, first_seal_time, last_seal_time, turnover_rate "
                    "FROM limit_pool WHERE trade_date=?",
                    (trade_date,),
                ).fetchall()
        cols = ["ts_code", "pool_kind", "name", "sector", "change_pct", "seal_amount",
                "consecutive_boards", "broken_count", "first_seal_time", "last_seal_time", "turnover_rate"]
        return [dict(zip(cols, r)) for r in rows]

    def get_dragon_tiger(self, trade_date: str) -> list[dict]:
        """读取龙虎榜明细（结构化）."""
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT ts_code, name, reason, close, change_pct, net_buy, "
                "buy_amount, sell_amount, list_date "
                "FROM dragon_tiger WHERE trade_date=?",
                (trade_date,),
            ).fetchall()
        cols = ["ts_code", "name", "reason", "close", "change_pct", "net_buy",
                "buy_amount", "sell_amount", "list_date"]
        return [dict(zip(cols, r)) for r in rows]

    def get_fund_flow_sector(self, trade_date: str) -> list[dict]:
        """读取板块资金流（结构化）."""
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT sector_name, change_pct, main_net, main_pct, huge_net, "
                "large_net, medium_net, small_net "
                "FROM fund_flow_sector WHERE trade_date=? ORDER BY change_pct DESC",
                (trade_date,),
            ).fetchall()
        cols = ["sector_name", "change_pct", "main_net", "main_pct", "huge_net",
                "large_net", "medium_net", "small_net"]
        return [dict(zip(cols, r)) for r in rows]

    # ═══════════════════════════════════════════════════════════════════
    # iVIX/QVIX 历史（中国 50ETF 波动率指数，AKShare 唯一源，跨模块共享缓存）
    # ═══════════════════════════════════════════════════════════════════

    def get_ivix_history(self, days: int = 1300) -> pd.DataFrame:
        """获取 iVIX/QVIX 历史收盘价序列（跨模块共享缓存）.

        替代 volatility 和 invest_sop/overseas 各自独立调 AKShare
        ``index_option_50etf_qvix``。缓存命中时不调 API。

        返回 DataFrame[index=date, columns=[close]]，升序。
        """
        from datetime import datetime, timedelta

        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=int(days * 1.6))).strftime("%Y%m%d")

        # 检查缓存
        with closing(get_connection()) as conn:
            row = conn.execute(
                "SELECT COUNT(*), MAX(trade_date) FROM ivix_history "
                "WHERE trade_date >= ?", (start_date,)
            ).fetchone()
            cached_count = row[0] if row else 0

        # 缓存不足或过期（非今日拉取）则刷新
        from stockhot.data_layer.cache import is_fetched_today, now_ts
        with closing(get_connection()) as conn:
            latest = conn.execute(
                "SELECT fetched_at FROM ivix_history ORDER BY fetched_at DESC LIMIT 1"
            ).fetchone()
        latest_ts = latest[0] if latest else None

        if cached_count < 100 or not is_fetched_today(latest_ts):
            # 从 AKShare 拉取（唯一源）
            try:
                import akshare as ak
                from stockhot.core.rate_limiter import safe_akshare_call

                raw = safe_akshare_call(ak.index_option_50etf_qvix)
                if raw is not None and not raw.empty:
                    ts = now_ts()
                    records = []
                    for _, r in raw.iterrows():
                        td = str(r.get("date", "")).replace("-", "")
                        if td:
                            records.append((td, float(r.get("close", 0)), ts))
                    with closing(get_connection()) as conn:
                        conn.executemany(
                            "INSERT OR REPLACE INTO ivix_history (trade_date, close, fetched_at) "
                            "VALUES (?,?,?)", records
                        )
                        conn.commit()
            except Exception as e:
                logger.warning(f"iVIX refresh failed: {e}")

        # 从缓存读取
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT trade_date, close FROM ivix_history "
                "WHERE trade_date >= ? ORDER BY trade_date", (start_date,)
            ).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["trade_date", "close"])
        df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
        return df.set_index("date")[["close"]].astype(float).tail(days)

    # ═══════════════════════════════════════════════════════════════════
    # 缓存维护：清理过期行（解决 davis/stockhot 缓存只增不减的膨胀问题）
    # ═══════════════════════════════════════════════════════════════════

    def cleanup_expired_cache(self, max_age_days: int = 90) -> dict[str, int]:
        """清理过期缓存行，返回各表删除行数.

        清理策略（与数据生命周期对齐）：
        - daily_basic: 删除 30 天前的行（24h TTL，但保留近期供查询）
        - scan_log: 删除 max_age_days 天前（默认 90 天）的日志
        - daily_price / financial / hk_hold: **不删**（历史数据不可变，永久保留）
        - ivix_history: 删除 5 年前的行（够算长周期分位）

        由 run_daily_scan Wave 4 每天调用一次（盘后低频，不影响采集）。
        """
        from datetime import datetime, timedelta

        cutoff_30d = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        cutoff_5y = (datetime.now() - timedelta(days=365 * 5)).strftime("%Y%m%d")
        cutoff_log = now_ts() - max_age_days * 86400

        deleted: dict[str, int] = {}
        cleanup_sql = [
            ("daily_basic", f"DELETE FROM daily_basic WHERE trade_date < '{cutoff_30d}'"),
            ("scan_log", f"DELETE FROM scan_log WHERE created_at < {cutoff_log}"),
            ("ivix_history", f"DELETE FROM ivix_history WHERE trade_date < '{cutoff_5y}'"),
        ]
        with closing(get_connection()) as conn:
            for table, sql in cleanup_sql:
                cur = conn.execute(sql)
                deleted[table] = cur.rowcount
            conn.commit()
            # VACUUM 压缩碎片（仅在删除量较大时）
            total_del = sum(deleted.values())
            if total_del > 1000:
                conn.execute("VACUUM")
        logger.info(f"cleanup_expired_cache: deleted {deleted}")
        return deleted

    # ═══════════════════════════════════════════════════════════════════
    # 盘面采集：scan_log（采集日志，解决"不知道哪个模块跑没跑"）
    # ═══════════════════════════════════════════════════════════════════

    def log_scan(
        self,
        trade_date: str,
        module_name: str,
        status: str,
        error_msg: str | None = None,
        started_at: float | None = None,
        finished_at: float | None = None,
        rows_affected: int | None = None,
    ) -> None:
        """记录一次采集模块的执行结果."""
        duration = None
        if started_at and finished_at:
            duration = finished_at - started_at
        with closing(get_connection()) as conn:
            conn.execute(
                "INSERT INTO scan_log "
                "(trade_date, module_name, status, error_msg, started_at, finished_at, "
                "duration_sec, rows_affected, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trade_date, module_name, status, error_msg, started_at, finished_at,
                 duration, rows_affected, now_ts()),
            )
            conn.commit()

    def get_scan_log(self, trade_date: str) -> list[dict]:
        """读取某日所有模块的采集日志."""
        with closing(get_connection()) as conn:
            rows = conn.execute(
                "SELECT module_name, status, error_msg, duration_sec, rows_affected "
                "FROM scan_log WHERE trade_date=? ORDER BY id",
                (trade_date,),
            ).fetchall()
        return [
            {"module": r[0], "status": r[1], "error": r[2],
             "duration": r[3], "rows": r[4]}
            for r in rows
        ]


# ── 模块级单例 ───────────────────────────────────────────────────────

_repo_instance: MarketDataRepository | None = None


def get_repository() -> MarketDataRepository:
    """获取 MarketDataRepository 单例."""
    global _repo_instance
    if _repo_instance is None:
        _repo_instance = MarketDataRepository()
    return _repo_instance
