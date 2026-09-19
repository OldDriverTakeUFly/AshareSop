"""E 影子:首板隔夜 chase_shadow 每日驱动(2026-09-13 立项,复盘 73e4a50 落地).

形态(与 fb_base 回放同口径,保持 227 笔样本可比): T 日收盘买首板候选
(limitup.candidates.build_candidates, 涨停可成交性 haircut 由 executor 内建),
T+1 开盘卖(电平型, 一字跌停顺延重试)。

影子E 三项尾部风控(复盘 §三, 经 config 注入, 策略默认值不变):
  同日新开 ≤2(max_new_per_day) | 单票权重 ≤20%(max_single_weight) |
  跌停链台账(本驱动逐日落 held_days, 隔夜策略 held>1 日=顺延锁仓标记)。

判定纪律(预注册): ≥20 个交易日; 主看月度回撤分布而非均值; 判定时必须带
2026-05(-31.2%)归因对照。台账: logs/chase_shadow/daily_ledger.json。

用法: .venv/bin/python scripts/ops/chase_shadow_daily.py [--as-of YYYYMMDD] [--account NAME]
调度: crontab 19:40 T 晚(limitup daily_refresh 19:20 供候选后, paper-push 19:50 前)。
"""
import os, sys, json, sqlite3, time
from datetime import datetime
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="INFO")

from stockhot.data_layer.market_db import get_connection as get_market_conn
from stockhot.storage.database import init_database, DB_PATH
from davis_analyzer.systems.paper_trading.account import PaperAccount
from davis_analyzer.systems.paper_trading.strategy import BoardChasingStrategy
from davis_analyzer.systems.paper_trading.executor import DailyExecutor
init_database()

ACCOUNT = "chase_shadow"
LEDGER = "logs/chase_shadow/daily_ledger.json"
INITIAL_CAPITAL = 1_000_000
# 影子E 风控配置(复盘 §三): 同日新开≤2, 单票≤20%
STRATEGY_CFG = dict(enhanced_filter=False, max_positions=3,
                    max_consecutive_losses=5, loss_pause_days=3,
                    daily_loss_limit_pct=2.0,
                    max_new_per_day=2, max_single_weight=0.20)


def ensure_account(name: str) -> PaperAccount:
    with sqlite3.connect(DB_PATH) as c:
        if not c.execute("SELECT 1 FROM paper_accounts WHERE name=?", (name,)).fetchone():
            c.commit()
            acc = PaperAccount.create(name=name, strategy_name="board_chasing",
                                      initial_capital=INITIAL_CAPITAL, config={})
            print(f"创建账户 {name} #{acc.account_id}")
            return acc
    return PaperAccount.load(name)


def latest_trade_date() -> str:
    with get_market_conn() as c:
        return c.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()[0]


def held_days(acc: PaperAccount, today: str) -> list[dict]:
    """持仓的持有时长(买入日→today 的交易日差, 用自然日近似+1)。held>1=跌停顺延锁仓."""
    with sqlite3.connect(DB_PATH) as c:
        aid = acc.account_id
        buys = {r[0]: r[1] for r in c.execute(
            "SELECT ts_code, MAX(trade_date) FROM paper_trades WHERE account_id=? AND action='BUY' GROUP BY ts_code",
            (aid,))}
    out = []
    for p in acc.get_positions():
        bd = buys.get(p.ts_code)
        try:
            days = (datetime.strptime(today, "%Y%m%d") - datetime.strptime(bd, "%Y%m%d")).days if bd else 0
        except Exception:
            days = 0
        out.append({"code": p.ts_code, "name": p.name, "calendar_days_held": days,
                    "locked": days > 1})  # 隔夜策略: 次日应已卖出, >1 日≈顺延
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--account", default=ACCOUNT)
    args = ap.parse_args()
    day = args.as_of or latest_trade_date()
    acc = ensure_account(args.account)
    if acc.has_run_on(day):
        print(f"[{args.account}] {day} 已执行过, 跳过(幂等)")
        acc.close()
        return
    strategy = BoardChasingStrategy(**STRATEGY_CFG)
    executor = DailyExecutor(acc, strategy)
    print(f"[{args.account}] 执行 {day} (候选由 limitup 管线供给; 0 候选日=正常防守行为,"
          "首板质量过滤本就稀疏——09-01 仅 4 只)")
    # 空分数字典跳过 run_day 的活算因子路径(board_chasing 不读因子分; 活算路径
    # 会全量评分并暴露于瞬时 API 故障——首跑曾因此崩溃)
    result = executor.run_day(day, factor_scores={"_davis_scores": {}, "_factor_scores": {}})
    acc2 = PaperAccount.load(args.account)
    nav_rows = acc2.get_nav_history()
    nav = nav_rows[-1].total_equity if nav_rows else INITIAL_CAPITAL
    holdings = held_days(acc2, day)
    trades_today = [t for t in acc2.get_trades() if t.trade_date == day]
    entry = {"date": day, "run_at": datetime.now().isoformat(timespec="seconds"),
             "status": result.get("status"), "nav": nav,
             "n_buys": sum(1 for t in trades_today if t.action == "BUY"),
             "n_sells": sum(1 for t in trades_today if t.action == "SELL"),
             "positions": holdings,
             "locked_count": sum(1 for h in holdings if h["locked"])}
    acc2.close()
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    ledger = []
    try:
        ledger = json.load(open(LEDGER))
    except Exception:
        pass
    ledger.append(entry)
    with open(LEDGER, "w") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False, default=str)
    print(f"  买{entry['n_buys']} 卖{entry['n_sells']} NAV {nav:,.0f} "
          f"持仓{len(holdings)} 锁仓{entry['locked_count']} → {LEDGER}")
    acc.close()


if __name__ == "__main__":
    main()
