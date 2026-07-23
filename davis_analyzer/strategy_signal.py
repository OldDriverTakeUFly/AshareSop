"""统一策略信号输出模块.

把 AI 选股策略的所有输出整合成标准 JSON，供外部服务（盘前盘后总结）直接读取。

输出包含：
1. 市场环境（HMM 牛熊 + 波动率状态）
2. 当前持仓 + 止盈止损 + 仓位分配
3. 每日策略信号（准备开仓/平仓/减仓 + 价格 + 数量）
4. 选股候选清单（含因子得分）
5. 策略配置参数

用法：
    from davis_analyzer.strategy_signal import generate_daily_signal
    signal = generate_daily_signal("20260721")
    # signal 是一个 dict，可直接 JSON 序列化
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from loguru import logger
from typing import Any

from stockhot.data_layer.market_db import get_connection as get_market_conn
from stockhot.storage.database import DB_PATH, get_connection as get_stockhot_conn
from davis_analyzer.config import get_tushare_token


def _get_market_regime(trade_date: str) -> dict:
    """获取 HMM 牛熊状态 + 波动率状态."""
    try:
        from davis_analyzer.market_regime import get_market_regime
        regime = get_market_regime(trade_date)
    except Exception:
        regime = "neutral"

    try:
        from davis_analyzer.paper_trading.executor import _get_market_vol_regime
        vol_regime, vol_mult = _get_market_vol_regime(trade_date)
    except Exception:
        vol_regime, vol_mult = "normal_vol", 1.0

    # 上证均线
    ma_info = {}
    try:
        with get_market_conn() as c:
            rows = c.execute(
                "SELECT close FROM index_daily WHERE ts_code='000001.SH' "
                "AND trade_date<=? AND close > 0 ORDER BY trade_date DESC LIMIT 120",
                (trade_date,)
            ).fetchall()
        if len(rows) >= 60:
            import numpy as np
            closes = np.array([float(r[0]) for r in rows])[::-1]
            ma_info = {
                "close": round(float(closes[-1]), 1),
                "ma5": round(float(closes[-5:].mean()), 1),
                "ma20": round(float(closes[-20:].mean()), 1),
                "ma60": round(float(closes[-60:].mean()), 1),
            }
    except Exception:
        pass

    return {
        "regime": regime,
        "vol_regime": vol_regime,
        "vol_position_mult": vol_mult,
        "index_sh": ma_info,
    }


def _get_holdings() -> list[dict]:
    """获取当前 invest_sop 持仓."""
    with get_stockhot_conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT code, name, sector, quantity, avg_cost, current_price, "
            "stop_loss_hard, stop_loss_logic, stop_loss_technical, target_price, "
            "position_pct, entry_date, status, thesis_snapshot_json "
            "FROM invest_holdings WHERE status = 'active'"
        ).fetchall()

    holdings = []
    for r in rows:
        h = {
            "code": r["code"],
            "name": r["name"],
            "sector": r["sector"],
            "quantity": r["quantity"],
            "avg_cost": r["avg_cost"],
            "current_price": r["current_price"],
            "stop_loss_hard": r["stop_loss_hard"],
            "stop_loss_logic": r["stop_loss_logic"],
            "stop_loss_technical": r["stop_loss_technical"],
            "target_price": r["target_price"],
            "position_pct": r["position_pct"],
            "entry_date": r["entry_date"],
        }
        # P&L
        if r["avg_cost"] and r["avg_cost"] > 0 and r["current_price"]:
            h["pnl_pct"] = round((r["current_price"] / r["avg_cost"] - 1) * 100, 1)
        else:
            h["pnl_pct"] = None
        holdings.append(h)
    return holdings


def _get_sector_rules() -> list[dict]:
    """获取板块止损/止盈规则."""
    with get_stockhot_conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT sector, stop_loss_pct, target_pct FROM invest_sector_rules"
        ).fetchall()
    return [{"sector": r["sector"], "stop_loss_pct": r["stop_loss_pct"],
             "target_pct": r["target_pct"]} for r in rows]


def _get_strategy_config() -> dict:
    """获取当前策略配置参数."""
    from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
    s = FactorThresholdStrategy()
    return {
        "max_positions": s.max_positions,
        "risk_stop_multiplier": s.risk_stop_multiplier,
        "sell_momentum": s.sell_momentum,
        "buy_momentum": s.buy_momentum,
        "volume_weight": s.volume_weight,
        "enable_volume_risk": s.enable_volume_risk,
        "pe_exemption_for_volume": s.pe_exemption_for_volume,
        "max_pe_percentile": s.max_pe_percentile,
        "min_secondary_dims": s.min_secondary_dims,
        "composite_weights": {
            "momentum": "38%",
            "secondary": "38%",
            "prosperity": "19%",
            "volume_price": "5%",
        },
    }


def _get_paper_trading_signals(account_name: str | None = None) -> dict:
    """获取模拟盘最新信号（如果有的话）.

    从 paper_trading 的最新账户读取最后一天的交易信号，
    作为"策略建议"的参考。
    """
    with get_stockhot_conn() as c:
        c.row_factory = sqlite3.Row

        # 找最新的生产账户（非 sweep/abx 测试账户）
        if account_name:
            row = c.execute(
                "SELECT id, name FROM paper_accounts WHERE name=?", (account_name,)
            ).fetchone()
        else:
            row = c.execute(
                "SELECT id, name FROM paper_accounts "
                "WHERE name NOT LIKE 'sweep_%' AND name NOT LIKE 'abx_%' "
                "AND name NOT LIKE 'sp_%' AND name NOT LIKE 'sh_%' "
                "AND name NOT LIKE 'fp_%' AND name NOT LIKE 's2_%' "
                "AND name NOT LIKE 'hp_%' AND name NOT LIKE 'rf_%' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()

        if not row:
            return {"available": False, "reason": "no production account"}

        account_id = row["id"]

        # 最新交易日的信号
        latest_date_row = c.execute(
            "SELECT MAX(trade_date) FROM paper_trades WHERE account_id=?", (account_id,)
        ).fetchone()
        if not latest_date_row or not latest_date_row[0]:
            return {"available": False, "reason": "no trades"}

        latest_date = latest_date_row[0]
        trades = c.execute(
            "SELECT trade_date, ts_code, action, shares, price, amount, signal_reason "
            "FROM paper_trades WHERE account_id=? AND trade_date=? "
            "ORDER BY action, ts_code",
            (account_id, latest_date)
        ).fetchall()

        # 当前持仓
        positions = c.execute(
            "SELECT ts_code, shares, avg_cost, entry_date, signal_reason "
            "FROM paper_positions WHERE account_id=?",
            (account_id,)
        ).fetchall()

        # 最新 NAV
        nav_row = c.execute(
            "SELECT trade_date, total_equity, daily_return "
            "FROM paper_nav_history WHERE account_id=? "
            "ORDER BY trade_date DESC LIMIT 1",
            (account_id,)
        ).fetchone()

    signals = {
        "available": True,
        "account_name": row["name"],
        "latest_trade_date": latest_date,
        "latest_nav": float(nav_row["total_equity"]) if nav_row else None,
        "latest_daily_return": float(nav_row["daily_return"]) if nav_row and nav_row["daily_return"] else None,
        "positions": [
            {"code": p["ts_code"], "shares": p["shares"],
             "avg_cost": p["avg_cost"], "entry_date": p["entry_date"]}
            for p in positions
        ],
        "today_trades": [
            {"code": t["ts_code"], "action": t["action"],
             "shares": t["shares"], "price": t["price"],
             "reason": t["signal_reason"]}
            for t in trades
        ],
    }

    # 按动作分组
    buy_signals = [t for t in signals["today_trades"] if t["action"] == "BUY"]
    sell_signals = [t for t in signals["today_trades"] if t["action"] == "SELL"]
    signals["buy_count"] = len(buy_signals)
    signals["sell_count"] = len(sell_signals)
    signals["buys"] = buy_signals
    signals["sells"] = sell_signals

    return signals


def generate_daily_signal(trade_date: str | None = None,
                          include_candidates: bool = False) -> dict:
    """生成每日策略信号 JSON.

    Args:
        trade_date: 交易日 YYYYMMDD，默认最新
        include_candidates: 是否包含选股候选（耗时 1-2 分钟）

    Returns:
        标准化的策略信号 dict，可直接 JSON 序列化
    """
    if trade_date is None:
        with get_market_conn() as c:
            row = c.execute(
                "SELECT MAX(trade_date) FROM daily_price WHERE vol > 0 "
                "GROUP BY trade_date HAVING COUNT(*) > 1000 "
                "ORDER BY trade_date DESC LIMIT 1"
            ).fetchone()
            trade_date = row[0] if row else datetime.now().strftime("%Y%m%d")

    signal: dict[str, Any] = {
        "signal_date": trade_date,
        "generated_at": datetime.now().isoformat(),
    }

    # 1. 市场环境
    signal["market"] = _get_market_regime(trade_date)

    # 2. 策略配置
    signal["strategy_config"] = _get_strategy_config()

    # 3. 当前持仓
    signal["holdings"] = _get_holdings()

    # 4. 板块规则
    signal["sector_rules"] = _get_sector_rules()

    # 5. 模拟盘信号
    signal["paper_trading"] = _get_paper_trading_signals()

    # 6. 选股候选（可选，耗时）
    if include_candidates:
        try:
            signal["candidates"] = _get_candidates(trade_date)
        except Exception as e:
            signal["candidates"] = {"error": str(e)}

    return signal


def _get_candidates(trade_date: str) -> list[dict]:
    """获取选股候选清单（耗时 1-2 分钟）."""
    # 简化版：从最近的 premarket JSON 读取
    import os
    PROJECT_ROOT = os.environ.get("PROJECT_ROOT", "/home/leo/Projects/CodeAgentDashboard")
    json_path = os.path.join(PROJECT_ROOT, f"logs/premarket_{trade_date}.json")
    if os.path.exists(json_path):
        with open(json_path, "r") as f:
            data = json.load(f)
        return data.get("candidates", [])
    return []


def save_signal(trade_date: str | None = None, output_path: str | None = None):
    """生成并保存策略信号 JSON."""
    signal = generate_daily_signal(trade_date)
    trade_date = signal["signal_date"]

    if output_path is None:
        import os
        PROJECT_ROOT = os.environ.get("PROJECT_ROOT", "/home/leo/Projects/CodeAgentDashboard")
        output_path = os.path.join(PROJECT_ROOT, f"logs/strategy_signal_{trade_date}.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(signal, f, indent=2, ensure_ascii=False)
    logger.info(f"Strategy signal saved to {output_path}")
    return output_path


if __name__ == "__main__":
    path = save_signal()
    print(f"Signal saved to {path}")
    # Print summary
    signal = generate_daily_signal()
    print(f"\n=== Strategy Signal for {signal['signal_date']} ===")
    print(f"Market regime: {signal['market']['regime']}")
    print(f"Vol regime: {signal['market']['vol_regime']} (mult={signal['market']['vol_position_mult']})")
    print(f"Holdings: {len(signal['holdings'])} active")
    for h in signal["holdings"]:
        print(f"  {h['code']} {h['name']} qty={h['quantity']} pnl={h['pnl_pct']}%")
    pt = signal["paper_trading"]
    if pt.get("available"):
        print(f"Paper trading: {pt['account_name']}")
        print(f"  NAV={pt['latest_nav']} positions={len(pt['positions'])}")
        print(f"  Today: {pt['buy_count']} buys, {pt['sell_count']} sells")
