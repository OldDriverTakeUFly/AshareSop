"""Performance reporting for paper-trading accounts.

Generates a markdown report with:
- Summary metrics (total return, annualised, max drawdown, Sharpe, win rate)
- Market-environment context (regime distribution + overseas risk)
- Equity curve table
- Current holdings + unrealised P&L
- Recent trades
- vs benchmark (optional)
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from typing import Any

from davis_analyzer.paper_trading.account import NAVSnapshot, PaperAccount


def _max_drawdown(equities: list[float]) -> float:
    """Calculate maximum drawdown (%) from a list of equity values."""
    if len(equities) < 2:
        return 0.0
    peak = equities[0]
    max_dd = 0.0
    for eq in equities:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def _sharpe(daily_returns: list[float], annualisation: float = 252) -> float:
    """Annualised Sharpe ratio (risk-free = 0)."""
    if len(daily_returns) < 2:
        return 0.0
    mean_r = sum(daily_returns) / len(daily_returns)
    var = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std = math.sqrt(var) if var > 0 else 0
    if std == 0:
        return 0.0
    daily_mean_pct = mean_r / 100  # daily_return is in %
    daily_std_pct = std / 100
    return round(daily_mean_pct / daily_std_pct * math.sqrt(annualisation), 2)


def _win_rate(trades: list) -> float:
    """Win rate from sell trades (comparing sell price vs avg_cost)."""
    sells = [t for t in trades if t.action == "SELL"]
    if not sells:
        return 0.0
    # Simple proxy: count sells where signal_reason doesn't contain "止损"
    # True P&L per trade would need matched buy/sell lots
    return round(sum(1 for s in sells if "止损" not in s.signal_reason) / len(sells) * 100, 1)


# Matches the "regime/sector" tag embedded in risk-driven signal_reasons,
# e.g. "硬止损 P&L=-14.4% (止损线14% bull/flat)" → captures "bull", "flat".
# Anchored to the closing paren so it tolerates the Chinese prefix inside ().
_REGIME_TAG_RE = re.compile(r"([a-z]+)/(up|down|flat)\)")


def _regime_distribution(trades: list) -> dict[str, int]:
    """Tally how many trades occurred under each market regime.

    Derives the regime from the ``(regime/sector)`` tag that executor embeds
    in stop-loss / take-profit signal_reasons. Trades without the tag (e.g.
    buys, T-trades) are skipped. Returns a {regime: count} dict.
    """
    counts: Counter = Counter()
    for t in trades:
        m = _REGIME_TAG_RE.search(t.signal_reason or "")
        if m:
            counts[m.group(1)] += 1
    return dict(counts)


def _latest_overseas_risk(trade_date: str | None) -> tuple[float, str] | None:
    """Fetch the most recent overseas risk score for display.

    Returns (score, level) or None if no overseas data exists. Looks back
    up to 7 calendar days from the last trade date to tolerate weekends /
    data lags. Failures are silent — this is a display-only enhancement.
    """
    if not trade_date:
        return None
    try:
        from davis_analyzer.international_overlay import backfill_risk_scores

        # backfill_risk_scores walks calendar days; just take the last one.
        scores = backfill_risk_scores(trade_date, trade_date)
        if scores and scores[-1].data_sufficient:
            s = scores[-1]
            return (s.composite_score, s.level)
    except Exception:
        pass
    return None


def generate_report(account: PaperAccount, current_prices: dict[str, float] | None = None) -> str:
    """Generate a full markdown performance report."""
    nav_history = account.get_nav_history()
    trades = account.get_trades()
    positions = account.get_positions()
    initial = account.initial_capital
    cash = account.cash

    if not nav_history:
        return f"# 模拟盘报告：{account.name}\n\n> 无历史数据。请先运行 `run` 或 `backfill`。\n"

    latest = nav_history[-1]
    equities = [n.total_equity for n in nav_history]
    daily_returns = [n.daily_return for n in nav_history if n.daily_return is not None]

    total_return = round((latest.total_equity / initial - 1) * 100, 2)
    max_dd = _max_drawdown(equities)
    sharpe = _sharpe(daily_returns)
    win_rate = _win_rate(trades)

    # Annualised return (simple: total return / years)
    if len(nav_history) > 1:
        days = len(nav_history)
        years = days / 252
        annualised = round(((latest.total_equity / initial) ** (1 / years) - 1) * 100, 2) if years > 0 else 0
    else:
        annualised = 0

    # ── Build report ──
    lines: list[str] = []
    lines.append(f"# 模拟盘报告：{account.name}")
    lines.append("")
    lines.append(f"> **策略**：{account.strategy_name} | **初始资金**：{initial:,.0f} 元")
    lines.append(f"> **运行区间**：{nav_history[0].trade_date} → {nav_history[-1].trade_date}（{len(nav_history)} 个交易日）")
    lines.append(f"> **生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # Summary table
    lines.append("## 绩效概览")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|:----:|")
    lines.append(f"| 总资产 | **{latest.total_equity:,.0f}** 元 |")
    lines.append(f"| 现金 | {latest.cash:,.0f} 元 |")
    lines.append(f"| 持仓市值 | {latest.positions_value:,.0f} 元 |")
    lines.append(f"| 总收益率 | **{'+' if total_return >= 0 else ''}{total_return:.2f}%** |")
    lines.append(f"| 年化收益率 | {'+' if annualised >= 0 else ''}{annualised:.2f}% |")
    lines.append(f"| 最大回撤 | **-{max_dd:.2f}%** |")
    lines.append(f"| 夏普比率 | {sharpe:.2f} |")
    lines.append(f"| 胜率（估算） | {win_rate:.1f}% |")
    lines.append(f"| 总交易笔数 | {len(trades)} |")
    lines.append(f"| 持仓数量 | {len(positions)} |")
    lines.append("")

    # ── Market environment context ──
    # Shows which regimes the strategy traded through (derived from trade
    # records) + the latest international resonance risk. Helps the reader
    # judge whether performance was achieved in bull, bear, or mixed waters.
    regime_counts = _regime_distribution(trades)
    latest_risk = _latest_overseas_risk(nav_history[-1].trade_date)

    if regime_counts or latest_risk:
        lines.append("## 市场环境")
        lines.append("")
        if regime_counts:
            total_tagged = sum(regime_counts.values())
            emoji = {"bull": "🟢", "bear": "🔴", "mixed": "🟡", "neutral": "🟡"}
            parts = []
            for r in ("bull", "mixed", "neutral", "bear"):
                if r in regime_counts:
                    pct = regime_counts[r] / total_tagged * 100
                    parts.append(f"{emoji.get(r, '')} {r} {pct:.0f}%")
            lines.append(f"- **交易期间 regime 分布**：{' / '.join(parts)}（基于 {total_tagged} 笔风控触发交易）")
        if latest_risk:
            score, level = latest_risk
            risk_emoji = {"极端": "🔴", "偏高": "🟠", "中等": "🟡", "偏低": "🟢"}.get(level, "")
            lines.append(f"- **最新国际共振风险**：{score:.0f}/100 {risk_emoji}{level}")
        lines.append("")

    # Equity curve (sampled to ≤30 rows)
    lines.append("## 净值曲线")
    lines.append("")
    lines.append("| 日期 | 总资产 | 日收益率 | 累计收益率 |")
    lines.append("|------|-------:|--------:|----------:|")
    step = max(1, len(nav_history) // 30)
    for i, nav in enumerate(nav_history):
        if i % step == 0 or i == len(nav_history) - 1:
            cum_ret = (nav.total_equity / initial - 1) * 100
            dr = f"{'+' if (nav.daily_return or 0) >= 0 else ''}{nav.daily_return:.2f}%" if nav.daily_return else "—"
            lines.append(
                f"| {nav.trade_date} | {nav.total_equity:,.0f} | {dr} | "
                f"{'+' if cum_ret >= 0 else ''}{cum_ret:.2f}% |"
            )
    lines.append("")

    # Current holdings
    if positions:
        lines.append("## 当前持仓")
        lines.append("")
        lines.append("| 代码 | 名称 | 股数 | 均价 | 现价 | 市值 | 浮动盈亏 |")
        lines.append("|------|------|-----:|------:|------:|------:|---------:|")
        total_unrealised = 0.0
        for pos in sorted(positions, key=lambda p: p.shares * p.avg_cost, reverse=True):
            cur_px = (current_prices or {}).get(pos.ts_code, pos.avg_cost)
            mv = pos.shares * cur_px
            cost = pos.shares * pos.avg_cost
            pnl = mv - cost
            pnl_pct = (pnl / cost * 100) if cost > 0 else 0
            total_unrealised += pnl
            lines.append(
                f"| {pos.ts_code} | {pos.name} | {pos.shares:,} | {pos.avg_cost:.2f} | "
                f"{cur_px:.2f} | {mv:,.0f} | "
                f"{'+' if pnl >= 0 else ''}{pnl:,.0f} ({'+' if pnl_pct >= 0 else ''}{pnl_pct:.1f}%) |"
            )
        lines.append(
            f"| | **合计** | | | | **{sum(p.shares * (current_prices or {}).get(p.ts_code, p.avg_cost) for p in positions):,.0f}** | "
            f"**{'+' if total_unrealised >= 0 else ''}{total_unrealised:,.0f}** |"
        )
        lines.append("")

    # Recent trades (last 20)
    if trades:
        lines.append("## 近期交易（最近20笔）")
        lines.append("")
        lines.append("| 日期 | 代码 | 名称 | 方向 | 股数 | 价格 | 金额 | 信号原因 |")
        lines.append("|------|------|------|------|-----:|------:|------:|---------|")
        for t in trades[:20]:
            action_label = "买入" if t.action == "BUY" else "卖出"
            lines.append(
                f"| {t.trade_date} | {t.ts_code} | {t.name} | {action_label} | "
                f"{t.shares:,} | {t.price:.2f} | {t.amount:,.0f} | {t.signal_reason} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("> *本报告由模拟盘系统自动生成，不构成投资建议。*")
    lines.append("")

    return "\n".join(lines)
