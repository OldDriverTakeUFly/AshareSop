"""Live monitoring daemon for paper-trading.

Runs an intraday loop during A-share market hours (09:30-11:30, 13:00-15:00
CST), checking positions every *interval* seconds. When sell signals trigger
(hard stop, trailing stop, target reached), it auto-executes virtual sells.
When the market closes, it runs a full daily evaluation (factor scoring +
strategy signals + buy execution + NAV snapshot).

Usage::

    python -m davis_analyzer.paper_trading live --name my_account

The monitor is safe to leave running — it auto-detects trading days,
sleeps outside market hours, and is idempotent (won't double-execute).
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger

from stockhot.data_layer import get_repository
from stockhot.data_layer.market_db import get_connection as get_market_conn

from davis_analyzer.paper_trading.account import PaperAccount
from davis_analyzer.paper_trading.executor import DailyExecutor
from davis_analyzer.paper_trading.strategy import Strategy, create_strategy

_TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")

# A-share market hours
_MORNING_OPEN = (9, 25)   # 09:25 — pre-open, start watching
_MORNING_CLOSE = (11, 30)
_AFTERNOON_OPEN = (13, 0)
_AFTERNOON_CLOSE = (15, 5)  # 15:05 — slightly after close to catch final prices


def is_market_open(now: datetime | None = None) -> bool:
    """Check if the A-share market is currently open (CST)."""
    now = now or datetime.now(_TZ_SHANGHAI)
    # Weekend check
    if now.weekday() >= 5:
        return False
    t = (now.hour, now.minute)
    return _MORNING_OPEN <= t <= _MORNING_CLOSE or _AFTERNOON_OPEN <= t <= _AFTERNOON_CLOSE


def is_trading_day(date_str: str | None = None) -> bool:
    """Check if a date is a trading day using the market_data.db calendar.

    判据(2026-08-23 放宽): 库中最近行情行距今 ≤5 个自然日即视为交易日。
    原版要求「当日有行」, 但 daily_price 的写入方全部盘后运行, 节后首个
    交易日盘中启动时当日无行会被误判为非交易日(此前依赖旁路写入兜底)。
    长假最后 1-2 天误判 True 仅导致空转轮询, 不产生交易。
    """
    target = date_str or datetime.now(_TZ_SHANGHAI).strftime("%Y%m%d")
    with get_market_conn() as conn:
        row = conn.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
    latest = row[0] if row else None
    if not latest:
        return False
    t = datetime.strptime(target, "%Y%m%d")
    l = datetime.strptime(str(latest), "%Y%m%d")
    return 0 <= (t - l).days <= 5


# ── 盘中实时价: AKShare 东财 spot 全市场快照(60s TTL 模块缓存) ──
# 一次快照服务全部持仓查询, 模式与 intraday_manager 的实时价轮询一致。
_SPOT_TTL_SEC = 60.0
_spot_cache: dict[str, float] = {}
_spot_cache_at: float = 0.0
_spot_lock = threading.Lock()


def _spot_suffix(code: str) -> str:
    if code[:2] in ("60", "68"):
        return ".SH"
    if code[:2] in ("00", "30"):
        return ".SZ"
    return ".BJ"


def _refresh_spot_map(force: bool = False) -> dict[str, float]:
    """Return ts_code→最新价 快照; 拉取失败时退回旧缓存(可能为空)."""
    global _spot_cache, _spot_cache_at
    with _spot_lock:
        if not force and _spot_cache and time.time() - _spot_cache_at < _SPOT_TTL_SEC:
            return _spot_cache
    try:
        import akshare as ak

        df = ak.stock_zh_a_spot_em()
        fresh: dict[str, float] = {}
        for code, px in zip(df["代码"].astype(str), df["最新价"]):
            try:
                f = float(px)
            except (TypeError, ValueError):
                continue
            if f > 0:
                fresh[code + _spot_suffix(code)] = f
        if fresh:
            with _spot_lock:
                _spot_cache = fresh
                _spot_cache_at = time.time()
        return fresh
    except Exception as e:
        logger.warning("AKShare spot 快照失败, 沿用旧缓存: {}", e)
        with _spot_lock:
            return _spot_cache


def get_realtime_price(ts_code: str) -> float | None:
    """Get the latest price for a stock.

    盘中(market hours)优先 AKShare 东财 spot 实时价(60s TTL 快照缓存),
    失败或盘后回落到 DAL daily_price 缓存的最近收盘价。(2026-08-23 修复:
    原版盘中也读 DAL close=昨日价, 硬止损/止盈判定失真——已知债
    「live_monitor 读缓存收盘价」, 日内策略上线的前置条件。)
    """
    if is_market_open():
        spot = _refresh_spot_map()
        px = spot.get(ts_code)
        if px is not None:
            return px
        logger.debug("spot miss {}, fallback DAL close", ts_code)
    repo = get_repository()
    today = datetime.now(_TZ_SHANGHAI).strftime("%Y%m%d")
    # Look back a few days in case today's data isn't cached yet
    start = (datetime.now(_TZ_SHANGHAI) - timedelta(days=5)).strftime("%Y%m%d")
    try:
        df = repo.get_daily_prices(ts_code, start, today)
        if df is not None and not df.empty:
            import pandas as pd

            close = pd.to_numeric(df["close"], errors="coerce").dropna()
            if len(close) > 0:
                return float(close.iloc[-1])
    except Exception:
        pass
    return None


class LiveMonitor:
    """Intraday monitoring daemon for a paper-trading account.

    Loop behavior:
    - During market hours: check sell signals every *interval* seconds.
    - At market close (15:00+): run full daily evaluation.
    - Outside market hours / non-trading days: sleep.
    """

    def __init__(
        self,
        account: PaperAccount,
        strategy: Strategy,
        interval_seconds: int = 60,
        commission_bps: float = 2.5,
        stamp_tax_bps: float = 10.0,
    ) -> None:
        self.account = account
        self.strategy = strategy
        self.interval = interval_seconds
        self.commission_bps = commission_bps
        self.stamp_tax_bps = stamp_tax_bps
        self._executor = DailyExecutor(account, strategy)
        self._eod_done_for: str | None = None  # date string of last EOD run

    def run_forever(self) -> None:
        """Main loop. Runs until interrupted (Ctrl+C / kill)."""
        logger.info(
            f"[{self.account.name}] Live monitor started. "
            f"Interval={self.interval}s. Ctrl+C to stop."
        )

        while True:
            try:
                now = datetime.now(_TZ_SHANGHAI)
                today_str = now.strftime("%Y%m%d")

                # Check if today is a trading day
                if not is_trading_day(today_str):
                    logger.debug(f"[{self.account.name}] {today_str} is not a trading day, sleeping...")
                    self._sleep_until_next_check()
                    continue

                if is_market_open(now):
                    # ── Intraday: check sell signals ──
                    self._check_sell_signals(today_str)
                    time.sleep(self.interval)

                elif now.hour >= 15 and self._eod_done_for != today_str:
                    # ── Market closed: run full EOD evaluation ──
                    logger.info(f"[{self.account.name}] Market closed — running EOD evaluation for {today_str}")
                    self._run_eod(today_str)
                    self._eod_done_for = today_str

                    # After EOD, sleep until tomorrow morning
                    logger.info(f"[{self.account.name}] EOD done, sleeping until tomorrow...")
                    self._sleep_until_morning()

                else:
                    # Before market open — wait
                    self._sleep_until_next_check()

            except KeyboardInterrupt:
                logger.info(f"[{self.account.name}] Live monitor stopped by user.")
                break
            except Exception:
                logger.exception(f"[{self.account.name}] Monitor error — continuing")
                time.sleep(self.interval)

    def _check_sell_signals(self, trade_date: str) -> None:
        """Check all positions for sell signals and execute if triggered."""
        positions = self.account.get_positions()
        if not positions:
            return

        for pos in positions:
            price = get_realtime_price(pos.ts_code)
            if price is None or price <= 0:
                continue

            # Simple sell rules (can be extended to use sell_monitor module):
            # 1. Hard stop: price drops below avg_cost × (1 - stop_loss_pct)
            stop_pct = 0.12  # 12% hard stop (sector default)
            if price <= pos.avg_cost * (1 - stop_pct):
                loss_pct = (price / pos.avg_cost - 1) * 100
                logger.warning(
                    f"[{self.account.name}] SELL SIGNAL: {pos.ts_code} {pos.name} "
                    f"hard stop triggered — price {price:.2f} ≤ cost×(1-{stop_pct}) "
                    f"= {pos.avg_cost*(1-stop_pct):.2f} (P&L {loss_pct:.1f}%)"
                )
                self.account.sell_all(
                    ts_code=pos.ts_code,
                    name=pos.name,
                    price=price,
                    trade_date=trade_date,
                    signal_reason=f"硬止损 P&L={loss_pct:.1f}%",
                )

            # 2. Target reached: price rises above avg_cost × (1 + target_pct)
            target_pct = 0.20  # 20% take-profit
            if price >= pos.avg_cost * (1 + target_pct):
                gain_pct = (price / pos.avg_cost - 1) * 100
                logger.info(
                    f"[{self.account.name}] SELL SIGNAL: {pos.ts_code} {pos.name} "
                    f"target reached — price {price:.2f} ≥ cost×(1+{target_pct}) "
                    f"= {pos.avg_cost*(1+target_pct):.2f} (P&L +{gain_pct:.1f}%)"
                )
                self.account.sell_all(
                    ts_code=pos.ts_code,
                    name=pos.name,
                    price=price,
                    trade_date=trade_date,
                    signal_reason=f"止盈 P&L=+{gain_pct:.1f}%",
                )

    def _run_eod(self, trade_date: str) -> None:
        """Run the full daily evaluation at market close."""
        # Skip if already done
        if self.account.has_run_on(trade_date):
            logger.info(f"[{self.account.name}] {trade_date} already executed")
            return

        # The executor handles: prices → strategy → trades → NAV
        result = self._executor.run_day(trade_date)
        logger.info(
            f"[{self.account.name}] EOD {trade_date}: {result['status']} "
            f"NAV={result.get('nav', 0):,.0f}"
        )

    def _sleep_until_next_check(self, seconds: int = 300) -> None:
        """Sleep for a short interval before re-checking market status."""
        time.sleep(min(seconds, self.interval * 5))

    def _sleep_until_morning(self) -> None:
        """Sleep until 09:20 CST tomorrow."""
        now = datetime.now(_TZ_SHANGHAI)
        tomorrow = now + timedelta(days=1)
        morning = tomorrow.replace(hour=9, minute=20, second=0, microsecond=0)
        wait_secs = (morning - now).total_seconds()
        if wait_secs > 0:
            logger.info(f"[{self.account.name}] Sleeping {wait_secs/3600:.1f}h until {morning:%H:%M}")
            time.sleep(wait_secs)
