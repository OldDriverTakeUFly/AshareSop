"""烟测: 回调结构门控卖出(实验0009)在 924 脉冲窗口(20240910→20241115)是否生效.

镜像 0004 的 smoke_bull_sell_exempt.py. 判据(机制验证, 非采纳判定):
  e2 开启 pb_struct 双豁免后, 高位放量/T+减仓卖出次数应显著下降,
  窗口收益应高于 base(0004 教训: 烟测正向≠全期可外推, 只验机制).
"""
import os, sys, time, sqlite3
from collections import Counter
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="ERROR")

import davis_analyzer.market_regime as mr
mr._MA120_BEAR_THRESHOLD = -999.0

from stockhot.storage.database import init_database, DB_PATH
from davis_analyzer.paper_trading.account import PaperAccount
from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
from davis_analyzer.paper_trading.executor import run_backfill_auto
init_database()

START, END = "20240910", "20241115"
INITIAL = 1_000_000
BASE = dict(
    max_positions=5, risk_stop_multiplier=0.70, sell_momentum=30,
    volume_weight=0.05, enable_volume_risk=True, pe_exemption_for_volume=True,
    max_intraday_amplitude=0.08, quality_weight=0.10, gap_weight=0.05,
    enable_event_filter=False, event_penalty_weight=0.0, tech_weight=0.0,
    enable_adaptive_sell=False, enable_dynamic_weight=False,
    amihud_weight=0.0, dragon_tiger_weight=0.0, repurchase_weight=0.0,
    low_vol_stop_exemption=0.0, holder_momentum_synergy=0.0,
    ivix_pause_threshold=25.0, enable_oversold_bounce=True,
    vol_ratio_defense=1.2, oversold_bounce_slots=1,
    oversold_candidate_min_drop=-3.0,
    trailing_drawback=0.0, min_hold_days=0, quick_stop_pct=0.0,
    buy_momentum=70, buy_holder_min=40, buy_dividend_min=55,
    buy_forecast_min=70, buy_prosperity_min=45, min_secondary_dims=1,
    bull_relaxed_buy_momentum=60.0,
)

with sqlite3.connect("storage/database/market_data.db") as c:
    rows = c.execute("SELECT ts_code FROM daily_price WHERE trade_date='20210104' "
                     "AND close>0 AND vol>0 ORDER BY amount DESC LIMIT 200").fetchall()
universe = [r[0] for r in rows]


def run(label, extra):
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT id FROM paper_accounts WHERE name=?", (f"smk9_{label}",)).fetchone()
        if row:
            for tbl in ("paper_positions", "paper_trades", "paper_nav_history", "paper_shadow_trades"):
                c.execute(f"DELETE FROM {tbl} WHERE account_id=?", (row[0],))
            c.execute("DELETE FROM paper_accounts WHERE id=?", (row[0],))
            c.commit()
    acct = PaperAccount.create(name=f"smk9_{label}", strategy_name="factor_threshold",
                               initial_capital=INITIAL, config={})
    strat = FactorThresholdStrategy(**{**BASE, **extra})
    t0 = time.time()
    run_backfill_auto(acct, strat, START, END, universe_codes=universe, scoring_frequency=1)
    trades = acct.get_trades()
    nav = [r.total_equity for r in acct.get_nav_history()]
    ret = (nav[-1] / INITIAL - 1) * 100 if nav else 0
    cats = Counter()
    for t in trades:
        if t.action == "SELL":
            reason = t.signal_reason or ""
            if "高位放量" in reason:
                cats["高位放量"] += 1
            elif "T+减仓" in reason:
                cats["T+减仓"] += 1
            elif "硬止损" in reason:
                cats["硬止损"] += 1
            elif "止盈" in reason:
                cats["止盈"] += 1
            elif "动量" in reason:
                cats["动量崩塌"] += 1
            else:
                cats["其他卖出"] += 1
    print(f"\n[{label}] ret={ret:+.2f}%  卖出结构={dict(cats)}  "
          f"总卖出={sum(1 for t in trades if t.action=='SELL')}  "
          f"({(time.time()-t0)/60:.1f}min)", flush=True)
    acct.close()
    return ret, cats


if __name__ == "__main__":
    run("base", {})
    run("e2_pbstruct", dict(pb_struct_highvol_exempt=True, pb_struct_tplus_exempt=True))
