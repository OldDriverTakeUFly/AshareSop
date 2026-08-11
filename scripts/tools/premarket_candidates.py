"""盘后分析：用最新策略配置筛选明日买入候选.

基于当前生产配置（Sharpe 优化 + Stage-2 PE 豁免），用最新交易日的数据
扫描全市场，输出 top 候选清单。
"""
import os, sys, json
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")

from datetime import datetime
from stockhot.data_layer.market_db import get_connection as get_market_conn
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
from davis_analyzer.paper_trading.executor import (
    _compute_davis_scores_at, _compute_factor_scores_at,
    _compute_volume_signals, _compute_pe_percentiles, _compute_short_momentum,
    _get_industries, _get_market_regime, _infer_industry_trends,
    _compute_event_signals, _load_tech_scores,
)
from davis_analyzer.paper_trading.strategy import MarketSnapshot


def get_latest_trade_date() -> str:
    """Get the latest trade date with FULL market coverage (≥1000 stocks)."""
    with get_market_conn() as c:
        row = c.execute("""
            SELECT trade_date FROM daily_price
            WHERE vol > 0
            GROUP BY trade_date
            HAVING COUNT(*) >= 1000
            ORDER BY trade_date DESC LIMIT 1
        """).fetchone()
    return row[0] if row else "20260715"


def build_universe_top_performers(trade_date: str, top_n: int = 200) -> list[str]:
    """Top performers by 90-day return (same as backtest universe)."""
    with get_market_conn() as c:
        # 90 days before trade_date — use a fixed recent date for the "past" anchor
        # daily_price covers 2025-01 onwards, so use 2025-10-01 as the ~3-month-back anchor
        rows = c.execute("""
            SELECT a.ts_code, a.close AS p_end, b.close AS p_start
            FROM daily_price a
            JOIN daily_price b
              ON a.ts_code = b.ts_code
             AND b.trade_date = (
                SELECT MAX(trade_date) FROM daily_price
                WHERE ts_code = a.ts_code AND trade_date <= '20251001'
             )
            WHERE a.trade_date = ? AND a.close > 0 AND b.close > 0 AND a.vol > 0
            ORDER BY (a.close / b.close - 1) DESC LIMIT ?
        """, (trade_date, top_n)).fetchall()
    return [r[0] for r in rows]


def main():
    trade_date = get_latest_trade_date()
    print(f"\n{'='*70}")
    print(f"  盘后分析 — 交易日 {trade_date}")
    print(f"{'='*70}\n")

    # Build universe
    universe = build_universe_top_performers(trade_date, top_n=200)
    print(f"Universe: {len(universe)} top performers (90d return)")

    # Compute all factors (this takes ~1-2 min)
    client = TushareClient()
    from datetime import datetime as _dt
    as_of = _dt.strptime(trade_date, "%Y%m%d").date()

    # Load stock_infos
    import sqlite3
    from stockhot.data_layer.market_db import MARKET_DB_PATH
    stock_infos = {}
    with sqlite3.connect(str(MARKET_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        for code in universe:
            row = conn.execute(
                "SELECT ts_code, name, industry FROM stock_basic WHERE ts_code=?",
                (code,)
            ).fetchone()
            if row:
                from davis_analyzer.types import StockInfo
                stock_infos[code] = StockInfo(
                    ts_code=row["ts_code"], name=row["name"],
                    industry=row["industry"] or "",
                    list_status="L", is_cyclical=False,
                )

    print(f"Computing Davis scores for {len(universe)} stocks...")
    davis_scores = _compute_davis_scores_at(client, as_of, universe, stock_infos)
    print(f"Computing factor scores...")
    factor_scores = _compute_factor_scores_at(client, as_of, universe)

    # Compute context signals
    print(f"Computing context signals...")
    market_regime = _get_market_regime(trade_date)
    industries = _get_industries(universe)
    industry_trend = _infer_industry_trends(factor_scores, industries, trade_date=trade_date)
    short_momentum = _compute_short_momentum(universe, trade_date)
    pe_percentiles = _compute_pe_percentiles(universe, trade_date)
    volume_signals = _compute_volume_signals(universe, trade_date)
    event_signals = _compute_event_signals(universe, trade_date)
    tech_scores = _load_tech_scores(universe, trade_date)

    # Get prices
    print(f"Loading prices...")
    with get_market_conn() as c:
        price_rows = c.execute(
            f"SELECT ts_code, close FROM daily_price WHERE trade_date=? AND close > 0",
            (trade_date,)
        ).fetchall()
    prices = {r[0]: float(r[1]) for r in price_rows if r[0] in universe}

    # Use the OPTIMIZED strategy config (Stage-2 best: S1_pe_exempt)
    strategy = FactorThresholdStrategy(
        max_positions=5,               # Sharpe-optimized
        risk_stop_multiplier=0.70,     # Sharpe-optimized
        volume_weight=0.05,
        enable_volume_risk=True,
        pe_exemption_for_volume=True,  # Stage-2 (S1 winner)
        # buy thresholds same as backtest
        buy_momentum=65, buy_holder_min=35,
        buy_dividend_min=55, buy_forecast_min=70, buy_prosperity_min=45,
    )

    # Build snapshot
    snapshot = MarketSnapshot(
        trade_date=trade_date,
        prices=prices,
        davis_scores=davis_scores,
        factor_scores=factor_scores,
        stock_names={c: (stock_infos[c].name if c in stock_infos else c) for c in universe},
        market_regime=market_regime,
        industries=industries,
        industry_trend=industry_trend,
        short_momentum=short_momentum,
        pe_percentile=pe_percentiles,
        volume_signal=volume_signals,
        event_signal=event_signals,
        tech_score=tech_scores,
    )

    # Evaluate
    print(f"\nEvaluating with optimized strategy...")
    print(f"  Market regime: {market_regime}")
    signals = strategy.evaluate([], snapshot, 1_000_000)
    buys = [s for s in signals if s.action == "BUY"]

    # Output
    print(f"\n{'='*70}")
    print(f"  明日买入候选 ({len(buys)} 只)")
    print(f"{'='*70}")
    if not buys:
        print("  无候选（市场环境或筛选条件不达标）")
    else:
        print(f"  {'排名':<4} {'代码':<12} {'名称':<12} {'信号理由':<50}")
        print(f"  {'-'*4} {'-'*12} {'-'*12} {'-'*50}")
        for i, s in enumerate(buys, 1):
            name = snapshot.stock_names.get(s.ts_code, "")[:10]
            reason = s.signal_reason[:48]
            print(f"  {i:<4} {s.ts_code:<12} {name:<12} {reason}")

    # Save
    output = {
        "trade_date": trade_date,
        "market_regime": market_regime,
        "universe_size": len(universe),
        "candidates": [
            {"rank": i+1, "ts_code": s.ts_code,
             "name": snapshot.stock_names.get(s.ts_code, ""),
             "reason": s.signal_reason}
            for i, s in enumerate(buys)
        ],
    }
    out_path = f"logs/premarket_{trade_date}.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n保存到 {out_path}")


if __name__ == "__main__":
    main()
