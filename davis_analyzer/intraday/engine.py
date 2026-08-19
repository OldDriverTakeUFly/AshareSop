"""日内做T回测引擎（闭环回转 + T+1 卖出池约束）.

防假阳性语义（每条都有单测覆盖）：
- 信号在 bar i 收盘后生成、bar i+1 开盘成交——杜绝 bar 内前视；末日 bar 信号
  直接进收盘竞价（日线 close，可执行的竞价价）
- T+1：当日买入进冻结仓；卖出只消耗"底仓卖出池"（base_shares − sold_today），
  当日买入的股数永远不可当日卖出
- 涨停价不追买、跌停价不追卖（拒单丢弃）；收盘竞价的强制轧平不受涨跌停
  限制（底仓必须恢复，计入 locked_eod_fill 观测）
- 闭环：EOD 轧平当日买卖差额，未配对残余按日线 close 强制成交
- 成本：佣金 2.5bps 双边 + 印花税 10bps（仅卖）+ 滑点 10bps 双边 = 往返 45bps
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from loguru import logger

from davis_analyzer.limitup.events import limit_ratio_for


@dataclass
class IntradayConfig:
    per_stock_notional: float = 200_000.0  # 模拟底仓市值（每股独立）
    trade_fraction: float = 0.30           # 每次动用底仓比例
    commission_bps: float = 2.5
    stamp_tax_bps: float = 10.0
    slippage_bps: float = 10.0


@dataclass
class Bar:
    time: str
    open: float
    high: float
    low: float
    close: float


@dataclass
class DayCtx:
    ts_code: str
    trade_date: str
    pre_close: float
    daily_close: float
    limit_up: float
    limit_down: float
    base_shares: int
    trade_shares: int
    prev_amplitude: float  # 前一交易日振幅（(high-low)/pre_close），首日为 -1
    features: dict = field(default_factory=dict)  # 入场时点前可得的因果特征


@dataclass
class Order:
    side: str  # 'buy' | 'sell'
    shares: int


@dataclass
class DayResult:
    ts_code: str
    trade_date: str
    strategy: str
    shares_bought: int
    shares_sold: int
    avg_buy: float
    avg_sell: float
    pnl: float
    net_bps: float        # pnl / 成交名义本金
    locked_eod_fill: int  # 收盘竞价中越过涨跌停的强制成交股数（观测项）
    n_rejected: int       # 盘中涨跌停拒单次数
    entry_time: str = ""  # 首笔买入成交时间（含滑点口径的审计轨迹）
    exit_time: str = ""   # 末笔卖出成交时间


# ── 单策略单股票日的撮合账本 ──

class DayRunner:
    """Per-strategy per-stock-day ledger：T+1 卖出池 + 成本 + 闭环轧平."""

    def __init__(self, ctx: DayCtx, config: IntradayConfig):
        self.ctx = ctx
        self.cfg = config
        self.bought_today = 0
        self.sold_today = 0
        self.buy_notional = 0.0
        self.sell_notional = 0.0
        self.buy_gross = 0.0   # 不含成本的成交额（用于 avg 与收益率分母）
        self.sell_gross = 0.0
        self.pnl = 0.0
        self.locked_eod_fill = 0
        self.n_rejected = 0
        self.fill_times: dict[str, str] = {}  # side -> 首买/末卖时间

    @property
    def sellable(self) -> int:
        """底仓卖出池：T+1 语义下当日买入不进入可卖池."""
        return self.ctx.base_shares - self.sold_today

    def _buy(self, shares: int, px: float, eod: bool) -> float | None:
        if not eod and px >= self.ctx.limit_up - 1e-9:
            self.n_rejected += 1
            return None
        fill_px = px * (1 + self.cfg.slippage_bps / 1e4)  # 不取整：跟随 limitup 引擎先例
        gross = shares * fill_px
        fee = gross * self.cfg.commission_bps / 1e4
        self.buy_notional += gross + fee
        self.buy_gross += gross  # 成交口径（含滑点）
        self.pnl -= gross + fee
        self.bought_today += shares
        if eod and px >= self.ctx.limit_up - 1e-9:
            self.locked_eod_fill += shares
        return fill_px

    def _sell(self, shares: int, px: float, eod: bool) -> float | None:
        shares = min(shares, self.sellable)
        if shares <= 0:
            return None
        if not eod and px <= self.ctx.limit_down + 1e-9:
            self.n_rejected += 1
            return None
        fill_px = px * (1 - self.cfg.slippage_bps / 1e4)
        gross = shares * fill_px
        fee = gross * (self.cfg.commission_bps + self.cfg.stamp_tax_bps) / 1e4
        self.sell_notional += gross - fee
        self.sell_gross += gross  # 成交口径（含滑点）
        self.pnl += gross - fee
        self.sold_today += shares
        if eod and px <= self.ctx.limit_down + 1e-9:
            self.locked_eod_fill += shares
        return fill_px

    def execute(self, order: Order, px: float, eod: bool = False,
                fill_time: str = "") -> float | None:
        """成交返回成交价（含滑点），拒单/无量返回 None."""
        if order.side == "buy":
            fill_px = self._buy(order.shares, px, eod)
            if fill_px is not None:
                self.fill_times.setdefault("buy", fill_time or "09:35")
            return fill_px
        fill_px = self._sell(order.shares, px, eod)
        if fill_px is not None:
            self.fill_times["sell"] = fill_time or "15:00"
        return fill_px

    def flatten_eod(self, px: float) -> None:
        """收盘竞价轧平当日买卖差额（做T闭环；越过涨跌停也强制恢复底仓）."""
        diff = self.bought_today - self.sold_today
        if diff > 0:
            self._sell(diff, px, eod=True)
        elif diff < 0:
            self._buy(-diff, px, eod=True)

    def result(self, strategy: str) -> DayResult:
        shares = max(self.bought_today, self.sold_today)
        avg_buy = self.buy_gross / self.bought_today if self.bought_today else 0.0
        avg_sell = self.sell_gross / self.sold_today if self.sold_today else 0.0
        # 单边名义本金（与完美上限口径一致：收益/单边敞口）
        notional = (self.buy_gross + self.sell_gross) / 2
        net_bps = self.pnl / notional * 1e4 if notional > 0 else 0.0
        return DayResult(
            self.ctx.ts_code, self.ctx.trade_date, strategy,
            self.bought_today, self.sold_today, round(avg_buy, 4),
            round(avg_sell, 4), round(self.pnl, 2), round(net_bps, 2),
            self.locked_eod_fill, self.n_rejected,
            self.fill_times.get("buy", ""), self.fill_times.get("sell", ""),
        )


# ── 日级模拟（单策略） ──

def simulate_day(strategy, ctx: DayCtx, bars: list[Bar],
                 config: IntradayConfig) -> DayResult | None:
    """跑一个策略 × 一个股票日：次bar开盘成交 + 收盘竞价闭环."""
    if not bars or ctx.trade_shares < 100:
        return None
    runner = DayRunner(ctx, config)
    pending: list[Order] = []
    n = len(bars)
    for i, bar in enumerate(bars):
        # 1) 上一根 bar 生成的订单在本 bar 开盘成交（回报成交价供策略跟踪）
        for order in pending:
            fill_px = runner.execute(order, bar.open, eod=False, fill_time=bar.time)
            if fill_px is not None and hasattr(strategy, "on_fill"):
                strategy.on_fill(order.side, order.shares, fill_px)
        pending = []
        # 2) 收盘后收集新信号（下一根 bar 开盘成交）
        if i < n - 1:
            pending = strategy.on_bar(i, bar, ctx) or []
        else:
            # 末日 bar：信号直接进收盘竞价
            for order in strategy.on_bar(i, bar, ctx) or []:
                runner.execute(order, ctx.daily_close, eod=True)
    # 3) 策略的 EOD 意图 + 引擎强制轧平
    for order in strategy.on_eod(runner, ctx) or []:
        runner.execute(order, ctx.daily_close, eod=True)
    runner.flatten_eod(ctx.daily_close)
    if runner.bought_today == 0 and runner.sold_today == 0:
        return None
    return runner.result(strategy.name)


# ── 主入口 ──

def build_day_ctx(code: str, day: str, drow, base_notional: float,
                  trade_fraction: float, prev_amplitude: float) -> DayCtx:
    pre_close = float(drow.pre_close)
    ratio = limit_ratio_for(code)
    limit_up = round(pre_close * (1 + ratio) + 1e-9, 2)
    limit_down = round(pre_close * (1 - ratio) + 1e-9, 2)
    base = int(base_notional / pre_close / 100) * 100
    trade_shares = int(base * trade_fraction / 100) * 100
    return DayCtx(code, day, pre_close, float(drow.close), limit_up,
                  limit_down, base, trade_shares, prev_amplitude)


def run_backtest(
    minute_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    strategies: list,
    config: IntradayConfig | None = None,
    features_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """跑全部股票日 × 全部策略。返回 DayResult DataFrame.

    minute_df:  [ts_code, trade_date, trade_time, open, high, low, close]（freq 过滤后）
    daily_df:   [ts_code, trade_date, pre_close, close, high, low]
    features_df: 可选，索引键 (ts_code, trade_date) 的入场前因果特征，
                经 DayCtx.features 注入策略（如趋势/大盘/量比过滤）.
    """
    config = config or IntradayConfig()
    daily_df = daily_df.sort_values(["ts_code", "trade_date"]).copy()
    daily_df["prev_amplitude"] = (
        (daily_df["high"] - daily_df["low"]) / daily_df["pre_close"]
    ).groupby(daily_df["ts_code"]).shift(1).fillna(-1.0)
    daily_map = {
        (r.ts_code, r.trade_date): r for r in daily_df.itertuples(index=False)
    }
    features_map: dict[tuple[str, str], dict] = {}
    if features_df is not None:
        for r in features_df.reset_index().itertuples(index=False):
            features_map[(r.ts_code, r.trade_date)] = r._asdict()

    results: list[DayResult] = []
    skipped_days = 0
    for (code, day), gdf in minute_df.groupby(["ts_code", "trade_date"], sort=True):
        drow = daily_map.get((code, day))
        if drow is None or not (drow.pre_close and drow.pre_close > 0):
            skipped_days += 1
            continue
        bars = [
            Bar(t.trade_time, t.open, t.high, t.low, t.close)
            for t in gdf.sort_values("trade_time").itertuples(index=False)
        ]
        ctx = build_day_ctx(
            code, day, drow, config.per_stock_notional, config.trade_fraction,
            float(drow.prev_amplitude),
        )
        ctx.features = features_map.get((code, day), {})
        for strategy in strategies:
            strategy.reset()
            res = simulate_day(strategy, ctx, bars, config)
            if res is not None:
                results.append(res)

    if skipped_days:
        logger.warning("跳过 {} 个无日线锚的股票日", skipped_days)
    return pd.DataFrame([r.__dict__ for r in results])
