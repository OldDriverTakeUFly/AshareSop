"""第一批做T策略（朴素基线，验证引擎与捕获率量级）.

三个策略都是"底仓闭环"：正T=低吸后收盘卖出底仓等量股数；反T=冲高先卖
底仓、收盘买回；网格=围绕昨收挂双向档位、收盘轧平。全部不做隔夜方向暴露。
"""

from __future__ import annotations

from dataclasses import dataclass

from davis_analyzer.intraday.engine import Bar, DayCtx, Order


class _Strategy:
    name: str = "base"

    def reset(self) -> None:
        raise NotImplementedError

    def on_bar(self, i: int, bar: Bar, ctx: DayCtx) -> list[Order] | None:
        raise NotImplementedError

    def on_eod(self, runner, ctx: DayCtx) -> list[Order] | None:
        return None


# ── 正T：大幅低开 → 早盘承接，收盘竞价卖出底仓 ──

class GapDownLongT(_Strategy):
    """开盘较昨收低开 >= gap_pct 时，次bar开盘买入，收盘竞价卖出等量底仓."""

    def __init__(self, gap_pct: float = 0.02):
        self.gap_pct = gap_pct
        self.name = f"gap_down_long@{gap_pct:.0%}"
        self._entered = False

    def reset(self) -> None:
        self._entered = False

    def on_bar(self, i: int, bar: Bar, ctx: DayCtx) -> list[Order] | None:
        if i == 0 and not self._entered:
            if bar.open <= ctx.pre_close * (1 - self.gap_pct):
                self._entered = True
                return [Order("buy", ctx.trade_shares)]
        return None

    def on_eod(self, runner, ctx: DayCtx) -> list[Order] | None:
        if self._entered and runner.bought_today > 0:
            return [Order("sell", runner.bought_today)]
        return None


# ── 增强版：低开正T + 因果过滤 + 止损/回补止盈/时间退出 ──

class GapDownSmart(_Strategy):
    """GapDownLongT 的增强版.

    require 过滤器（ctx.features，全部入场前因果可得）：
      - 'trend_up'/'trend_down'：bool 等值匹配
      - 'xxx_min'/'xxx_max'：数值下/上限（如 idx_ret0940_min=-0.01）
    退出优先级：止损 > 回补止盈（收回昨收）> 时间退出 > 收盘竞价兜底。
    特征缺失/NaN 一律不入场（保守）。
    """

    def __init__(self, gap_pct: float = 0.03, stop_pct: float | None = None,
                 tp_fill: bool = False, exit_time: str | None = None,
                 require: dict | None = None):
        self.gap_pct = gap_pct
        self.stop_pct = stop_pct
        self.tp_fill = tp_fill
        self.exit_time = exit_time
        self.require = require or {}
        self.name = (
            f"gap_smart@{gap_pct:.0%}"
            + (f"/sl{stop_pct:.1%}" if stop_pct else "")
            + ("/tp" if tp_fill else "")
            + (f"/x{exit_time}" if exit_time else "")
            + (f"|{','.join(f'{k}={v}' for k, v in self.require.items())}"
               if self.require else "")
        )
        self._reset_state()

    def _reset_state(self) -> None:
        self._entered = False
        self._exited = False
        self._entry_px: float | None = None
        self._shares = 0

    def reset(self) -> None:
        self._reset_state()

    def _passes(self, features: dict) -> bool:
        if not self.require:
            return True  # 未启用过滤：不要求特征
        if not features:
            return False  # 启用过滤但特征缺失 → 保守不入场
        for key, want in self.require.items():
            # '_min'/'_max' 是约束方向限定词，特征名去掉后缀
            feat_key = key[:-4] if key.endswith(("_min", "_max")) else key
            v = features.get(feat_key)
            if v is None or v != v:  # None 或 NaN
                return False
            if isinstance(want, bool):
                if bool(v) != want:
                    return False
            elif key.endswith("_min") and v < want:
                return False
            elif key.endswith("_max") and v > want:
                return False
        return True

    def on_bar(self, i: int, bar: Bar, ctx: DayCtx) -> list[Order] | None:
        if i == 0 and not self._entered:
            if (bar.open <= ctx.pre_close * (1 - self.gap_pct)
                    and self._passes(ctx.features)):
                self._entered = True
                return [Order("buy", ctx.trade_shares)]
            return None
        if 0 < i and self._entered and not self._exited and self._entry_px:
            if self.stop_pct and bar.close <= self._entry_px * (1 - self.stop_pct):
                self._exited = True
                return [Order("sell", self._shares)]
            if self.tp_fill and bar.close >= ctx.pre_close:
                self._exited = True
                return [Order("sell", self._shares)]
            if self.exit_time and bar.time >= self.exit_time:
                self._exited = True
                return [Order("sell", self._shares)]
        return None

    def on_fill(self, side: str, shares: int, px: float) -> None:
        if side == "buy":
            self._entry_px = px
            self._shares = shares

    def on_eod(self, runner, ctx: DayCtx) -> list[Order] | None:
        if self._entered and not self._exited and runner.bought_today > 0:
            return [Order("sell", runner.bought_today)]
        return None


# ── 反T：冲高回落 → 高位先卖底仓，收盘竞价买回 ──

class SpikeFadeShortT(_Strategy):
    """盘中高点较昨收涨 >= spike_pct 且自高点回落 >= fade_pct 时卖出，
    收盘竞价买回等量（吃到冲高回落的一段）."""

    def __init__(self, spike_pct: float = 0.02, fade_pct: float = 0.015):
        self.spike_pct = spike_pct
        self.fade_pct = fade_pct
        self.name = f"spike_fade_short@{spike_pct:.0%}/{fade_pct:.1%}"
        self._running_max = 0.0
        self._sold = False

    def reset(self) -> None:
        self._running_max = 0.0
        self._sold = False

    def on_bar(self, i: int, bar: Bar, ctx: DayCtx) -> list[Order] | None:
        if bar.high > self._running_max:
            self._running_max = bar.high
        if self._sold or self._running_max < ctx.pre_close * (1 + self.spike_pct):
            return None
        fade = (self._running_max - bar.close) / self._running_max
        if fade >= self.fade_pct:
            self._sold = True
            return [Order("sell", ctx.trade_shares)]
        return None

    def on_eod(self, runner, ctx: DayCtx) -> list[Order] | None:
        if self._sold and runner.sold_today > 0:
            return [Order("buy", runner.sold_today)]
        return None


# ── 网格：前日大振幅 → 围绕昨收双向挂档，收盘轧平净额 ──

@dataclass
class AmplitudeGrid(_Strategy):
    """前一交易日振幅 >= prev_amp_th 时启动：昨收 ±k×step 档位各触发一次
    买/卖（bar 收盘价穿越判定），收盘竞价由引擎轧平净头寸."""

    prev_amp_th: float = 0.05
    step_pct: float = 0.015
    rungs: int = 2

    def __post_init__(self) -> None:
        self.name = f"amp_grid@{self.prev_amp_th:.0%}/{self.step_pct:.1%}x{self.rungs}"
        self._armed = False
        self._used_buy: set[int] = set()
        self._used_sell: set[int] = set()

    def reset(self) -> None:
        self._armed = False
        self._used_buy = set()
        self._used_sell = set()

    def on_bar(self, i: int, bar: Bar, ctx: DayCtx) -> list[Order] | None:
        if i == 0:
            self._armed = ctx.prev_amplitude >= self.prev_amp_th
        if not self._armed:
            return None
        orders: list[Order] = []
        for k in range(1, self.rungs + 1):
            if k not in self._used_buy and bar.close <= ctx.pre_close * (1 - k * self.step_pct):
                self._used_buy.add(k)
                orders.append(Order("buy", ctx.trade_shares))
            if k not in self._used_sell and bar.close >= ctx.pre_close * (1 + k * self.step_pct):
                self._used_sell.add(k)
                orders.append(Order("sell", ctx.trade_shares))
        return orders or None
