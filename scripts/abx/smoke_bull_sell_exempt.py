"""烟测: 牛市卖出豁免在 924 脉冲窗口(20240910→20241115)是否生效."""
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
        row = c.execute("SELECT id FROM paper_accounts WHERE name=?", (f"smk4_{label}",)).fetchone()
        if row:
            for tbl in ("paper_positions", "paper_trades", "paper_nav_history", "paper_shadow_trades"):
                c.execute(f"DELETE FROM {tbl} WHERE account_id=?", (row[0],))
            c.execute("DELETE FROM paper_accounts WHERE id=?", (row[0],))
            c.commit()
    acct = PaperAccount.create(name=f"smk4_{label}", strategy_name="factor_threshold",
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
            r = t.signal_reason or ""
            if "高位放量" in r: cats["高位放量"] += 1
            elif "T+减仓" in r: cats["T+减仓"] += 1
            elif "止损" in r: cats["硬止损"] += 1
            elif "止盈" in r: cats["止盈"] += 1
    acct.close()
    print(f"  {label}: ret={ret:+.2f}% | 卖出结构: "
          f"高位放量{cats['高位放量']} T+减仓{cats['T+减仓']} 硬止损{cats['硬止损']} 止盈{cats['止盈']} "
          f"总{sum(1 for t in trades if t.action=='SELL')}卖 ({time.time()-t0:.0f}s)", flush=True)
    return ret, cats


print(f"[924窗口烟测] {START}→{END}")
r0, c0 = run("base", {})
r2, c2 = run("e2", dict(bull_highvol_sell_exempt=True, bull_tplus_trim_exempt=True))
print(f"\n[判定] 豁免生效 = e2的高位放量+T+减仓次数 < base")
print(f"  base: 高位放量{c0['高位放量']} T+减仓{c0['T+减仓']} → e2: 高位放量{c2['高位放量']} T+减仓{c2['T+减仓']}")
