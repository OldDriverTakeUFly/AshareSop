"""烟测: 牛市扩容机制在 2025-05→07 (信号荒时段) 是否生效且不崩溃."""
import os, sys, time, sqlite3
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
from davis_analyzer.paper_trading.executor import run_backfill_auto, _compute_index_above_ma200
init_database()

START, END = "20250501", "20250731"
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
)

# 函数级检查
print("[函数检查] _compute_index_above_ma200:")
for d in ["20210104", "20220104", "20250601", "20260730"]:
    print(f"  {d} → {_compute_index_above_ma200(d)}")

s = FactorThresholdStrategy(**BASE)
print(f"\n[策略检查] bull_relaxed={s.bull_relaxed_buy_momentum} ma200_override={s.ma200_bear_override}")
print(f"  bear无豁免 cap={s._effective_max_positions('bear', 1.0, True)}")
s3 = FactorThresholdStrategy(**{**BASE, "ma200_bear_override": True})
print(f"  bear+MA200上+豁免 cap={s3._effective_max_positions('bear', 1.0, True)} (期望2)")
print(f"  bear+MA200下+豁免 cap={s3._effective_max_positions('bear', 1.0, False)} (期望0)")

with __import__("sqlite3").connect("storage/database/market_data.db") as c:
    rows = c.execute("SELECT ts_code FROM daily_price WHERE trade_date='20210104' "
                     "AND close>0 AND vol>0 ORDER BY amount DESC LIMIT 200").fetchall()
universe = [r[0] for r in rows]
print(f"\n[universe] {len(universe)} stocks, 窗口 {START}→{END}")


def run(label, extra):
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT id FROM paper_accounts WHERE name=?", (f"smk_{label}",)).fetchone()
        if row:
            for tbl in ("paper_positions", "paper_trades", "paper_nav_history", "paper_shadow_trades"):
                c.execute(f"DELETE FROM {tbl} WHERE account_id=?", (row[0],))
            c.execute("DELETE FROM paper_accounts WHERE id=?", (row[0],))
            c.commit()
    acct = PaperAccount.create(name=f"smk_{label}", strategy_name="factor_threshold",
                               initial_capital=INITIAL, config={})
    strat = FactorThresholdStrategy(**{**BASE, **extra})
    t0 = time.time()
    run_backfill_auto(acct, strat, START, END, universe_codes=universe, scoring_frequency=1)
    trades = acct.get_trades()
    nav = [r.total_equity for r in acct.get_nav_history()]
    ret = (nav[-1] / INITIAL - 1) * 100 if nav else 0
    buys = sum(1 for t in trades if t.action == "BUY")
    acct.close()
    print(f"  {label}: {buys} buys / {len(trades)} trades, ret={ret:+.2f}% ({time.time()-t0:.0f}s)", flush=True)
    return buys


print("\n[mini回测]")
b0 = run("base", {})
b1 = run("g1", dict(bull_relaxed_buy_momentum=65.0))
b3 = run("g3", dict(ma200_bear_override=True))
print(f"\n[判定] 基线{b0}买 | G1 {b1}买 | G3 {b3}买 → 机制生效判定: G1>{b0} 或 G3>{b0}")
