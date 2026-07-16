"""eod_review 编排引擎 — 串联数据层/分析层/落库/报告，产出 EODReviewResult.

编排流程（5 层，每层独立错误隔离）::

    1. get_market_snapshot(date)     → 拉全维度数据
    2. aggregate_sector_performance  → 板块聚合
    3. attribute_limit_up            → 涨停归因
    4. compute_sentiment_thermometer → 情绪温度计
    5. compute_n_day_trend           → N 日趋势
    + persist_to_db                  → 落库 eod_review + eod_sentiment

单维度失败不阻断整体——失败的维度记入 result.errors，报告中标注"数据不可用"。
"""

from __future__ import annotations

import json
import time
from contextlib import closing
from dataclasses import dataclass, field

from stockhot.core.logging import logger
from stockhot.data_layer.market_db import get_connection
from stockhot.eod_review.analyzers import (
    LimitUpAttribution,
    SectorPerf,
    SentimentReading,
    TrendComparison,
    aggregate_sector_performance,
    attribute_limit_up,
    compute_n_day_trend,
    compute_sentiment_thermometer,
)
from stockhot.eod_review.data_layer import MarketSnapshot, clear_snapshot_cache, get_market_snapshot


@dataclass
class EODReviewResult:
    """盘后复盘引擎完整结果."""

    trade_date: str
    snapshot: MarketSnapshot
    sector_performance: list[SectorPerf] = field(default_factory=list)
    limit_up_attributions: list[LimitUpAttribution] = field(default_factory=list)
    sentiment: SentimentReading | None = None
    trend: TrendComparison | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """核心维度是否成功（至少 daily + 涨跌停可用）."""
        return self.snapshot.has_daily and self.snapshot.has_limit_data


class EODReviewEngine:
    """盘后复盘引擎编排器."""

    def __init__(self, *, max_history_fetch: int = 72) -> None:
        """
        Parameters
        ----------
        max_history_fetch : int
            涨停归因时最多拉取多少只股票的历史日线（控耗时，默认全量涨停数）。
        """
        self.max_history_fetch = max_history_fetch

    def run_review(
        self,
        date: str,
        *,
        use_cache: bool = True,
        persist: bool = True,
    ) -> EODReviewResult:
        """执行完整盘后复盘.

        Parameters
        ----------
        date : str
            交易日期 YYYYMMDD。
        use_cache : bool
            是否复用进程内快照缓存。
        persist : bool
            是否将结果落库到 eod_review / eod_sentiment 表。
        """
        logger.info(f"[EOD] ===== 盘后复盘开始: {date} =====")

        # ── 层 1：数据快照 ──
        if not use_cache:
            clear_snapshot_cache()
        snapshot = get_market_snapshot(date)
        result = EODReviewResult(trade_date=date, snapshot=snapshot, errors=list(snapshot.errors))

        if not snapshot.has_daily:
            logger.warning(f"[EOD] {date} 无日线数据，可能非交易日，停止分析")
            return result

        # ── 层 2：板块聚合 ──
        try:
            result.sector_performance = aggregate_sector_performance(snapshot)
            logger.info(f"[EOD] 板块聚合: {len(result.sector_performance)} 板块")
        except Exception as e:
            logger.exception(f"[EOD] 板块聚合失败: {e}")
            result.errors.append("sector_performance")

        # ── 层 3：涨停归因 ──
        try:
            result.limit_up_attributions = attribute_limit_up(
                snapshot, max_history_fetch=self.max_history_fetch
            )
            logger.info(f"[EOD] 涨停归因: {len(result.limit_up_attributions)} 只")
        except Exception as e:
            logger.exception(f"[EOD] 涨停归因失败: {e}")
            result.errors.append("limit_up_attribution")

        # ── 层 4：情绪温度计 ──
        try:
            result.sentiment = compute_sentiment_thermometer(snapshot)
            if result.sentiment:
                logger.info(
                    f"[EOD] 情绪温度计: {result.sentiment.score}/100 ({result.sentiment.label})"
                )
        except Exception as e:
            logger.exception(f"[EOD] 情绪温度计失败: {e}")
            result.errors.append("sentiment")

        # ── 层 5：N 日趋势 ──
        try:
            result.trend = compute_n_day_trend(date, snapshot)
        except Exception as e:
            logger.exception(f"[EOD] N日趋势失败: {e}")
            result.errors.append("trend")

        # ── 落库 ──
        if persist:
            try:
                self._persist(result)
                logger.info(f"[EOD] 落库完成")
            except Exception as e:
                logger.exception(f"[EOD] 落库失败: {e}")
                result.errors.append("persist")

        logger.info(f"[EOD] ===== 复盘完成: {date} (errors={result.errors}) =====")
        return result

    # ── 落库 ──────────────────────────────────────────────────────────

    def _persist(self, result: EODReviewResult) -> None:
        """将归因信号 + 情绪温度计写入 market_data.db."""
        date = result.trade_date
        now = time.time()

        with closing(get_connection()) as conn:
            # ── eod_review：涨停归因信号 ──
            for attr in result.limit_up_attributions:
                conn.execute(
                    "INSERT OR REPLACE INTO eod_review "
                    "(trade_date, ts_code, name, signal_type, sector, price, pct_chg, detail, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                    (
                        date,
                        attr.ts_code,
                        attr.name,
                        f"limit_up_{_attr_type_key(attr.attribution_type)}",
                        attr.sector,
                        attr.detail.get("consecutive_boards"),
                        json.dumps(attr.detail, ensure_ascii=False),
                        now,
                    ),
                )

            # 跌停池也落库（signal_type=limit_down）
            for stock in result.snapshot.limit_down:
                code = stock.get("code", "")
                conn.execute(
                    "INSERT OR REPLACE INTO eod_review "
                    "(trade_date, ts_code, name, signal_type, sector, price, pct_chg, detail, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                    (
                        date,
                        code,
                        stock.get("name", ""),
                        "limit_down",
                        stock.get("sector", ""),
                        stock.get("change_pct"),
                        json.dumps({"sector": stock.get("sector", "")}, ensure_ascii=False),
                        now,
                    ),
                )

            # ── eod_sentiment：情绪温度计 ──
            if result.sentiment:
                detail = result.sentiment.detail
                conn.execute(
                    "INSERT OR REPLACE INTO eod_sentiment "
                    "(trade_date, margin_balance, margin_chg, north_net, north_5d_avg, "
                    "block_trade_count, block_discount_median, sentiment_score, sentiment_label, detail, fetched_at) "
                    "VALUES (?, ?, NULL, ?, NULL, ?, ?, ?, ?, ?, ?)",
                    (
                        date,
                        detail.get("margin", {}).get("total_balance_yi"),
                        detail.get("north", {}).get("net_yi"),
                        detail.get("block", {}).get("count"),
                        detail.get("block", {}).get("median_discount_pct"),
                        result.sentiment.score,
                        result.sentiment.label,
                        json.dumps(result.sentiment.detail, ensure_ascii=False),
                        now,
                    ),
                )

            conn.commit()


# ── 归因类型中文 → 英文 key（用于 signal_type 存储和 LIKE 查询）──────────
_ATTR_TYPE_MAP = {
    "箱体突破": "breakout",
    "放量资金推动": "volume_fund",
    "连板接力": "relay",
    "低估值修复": "value_repair",
    "事件驱动": "event",
}


def _attr_type_key(chinese: str) -> str:
    return _ATTR_TYPE_MAP.get(chinese, "event")
