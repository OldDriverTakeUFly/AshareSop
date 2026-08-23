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
    # 高潮增强仓位：dict[trade_date → max_positions]，覆盖全局默认
    # （仅当日期在此 dict 中时使用该值，否则用 max_positions）
    dynamic_slots: dict[str, int] | None = None

    def effective_max_positions(self, trade_date: str) -> int:
        if self.dynamic_slots and trade_date in self.dynamic_slots:
            return self.dynamic_slots[trade_date]
        return self.max_positions


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
    ft = str(row.get("first_seal_time", "") or "").strip()
    if ft.isdigit():
        ft = ft.zfill(6)  # Tushare 无前导零格式（'95321'）归一为 HHMMSS
    if yizi:
        p = 0.05
    elif int(row.get("broken_count", 0) or 0) > 0:
        p = 0.70
    elif "090000" < ft < "100000":
        p = 0.20
    else:
        p = 0.35
    # round(…, 10)：消除 0.2*1.5=0.30000000000000004 之类的浮点表示误差
    return float(min(0.95, max(0.05, round(p * SCENARIOS[scenario], 10))))


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
                nxt = _next_day(d, all_dates)
                if nxt:
                    pos["sell_on"] = nxt  # 停牌无价：顺延卖出
                continue
            if _limit_down_locked(code, d):
                nxt = _next_day(d, all_dates)
                if nxt:
                    pos["sell_on"] = nxt
                    logger.info("{} {} 一字跌停无法卖出，顺延", code, d)
                continue
            if (pos["exit_rule"] is ExitRule.OPEN_HOLD_LOCKED
                    and _open_at_limit_up(code, px[code].loc[d])):
                # 可观测持有变体（规格 §3.2.1 第 4 条）：9:25 开盘即涨停 →
                # 取消本次卖出，转入 ride 循环（sell_on=None 由第 2 步接管）
                pos["sell_on"], pos["exec"] = None, None
                logger.info("{} {} 开盘=涨停价，open_hold_locked 取消卖出转 ride", code, d)
                continue
            exec_px = float(px[code].loc[d]["open"]) * (1 - config.slippage_bps / 1e4)
            _close_position(code, pos, d, exec_px, "规则卖出", scenario, config,
                            positions, trades)
            cash += pos["_cash_credit"]  # 由 _close_position 记录
        # 2) ride_board 收盘评估（open_hold_locked 变体取消卖出后由同一循环接管：
        #    其 sell_on 在触发开盘卖前恒非 None，故未取消的变体持仓不会进入此分支）
        for code, pos in positions.items():
            if (
                pos["exit_rule"] not in (ExitRule.RIDE_BOARD, ExitRule.OPEN_HOLD_LOCKED)
                or pos.get("sell_on")
            ):
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
        day_max_pos = config.effective_max_positions(d)
        slots = day_max_pos - len(positions)
        if slots > 0 and d in cand_by_date:
            ranked = cand_by_date[d].sort_values(
                preset.rank_key if preset.rank_key in cand_by_date[d].columns
                else "seal_ratio", ascending=False
            )
            equity_now = cash + _positions_market_value(positions, px, d)
            per_slot = equity_now / day_max_pos
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
                if preset.exit_rule in (ExitRule.OPEN_NEXT, ExitRule.OPEN_HOLD_LOCKED):
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
    return abs(float(row["close"]) - limit_up) <= 0.0051  # ≈0.005 容差，与 fill_probability 一致


def _open_at_limit_up(code: str, row: pd.Series) -> bool:
    """9:25 可观测判定：开盘价≈当日涨停价（只用 open，不看 low——无前视）."""
    ratio = limit_ratio_for(code)
    limit_up = round(float(row["pre_close"]) * (1 + ratio) + 1e-9, 2)
    return abs(float(row["open"]) - limit_up) <= 0.005


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
