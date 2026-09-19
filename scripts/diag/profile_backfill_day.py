"""一次性诊断: 剖析回测单日耗时 (0011 遗留——44s/天 vs 0010 的 6.4s/天).

临时账户 prof_probe 跑 3 个交易日(view off, G2 参数, 冻结宇宙), cProfile 输出
cumtime/tottime Top 函数。只读共享数据, 独立账户, 结束自动清理。
"""
import os, sys, cProfile, pstats, io, sqlite3, time
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.environ["MARKET_DB_ATTACH_DELISTED"] = "0"
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="ERROR")

import davis_analyzer.factors.market_regime as mr
mr._MA120_BEAR_THRESHOLD = -999.0
from stockhot.data_layer.market_db import get_connection as get_market_conn
from stockhot.storage.database import init_database, DB_PATH
from davis_analyzer.paper_trading.account import PaperAccount
from davis_analyzer.paper_trading.strategy import FactorThresholdStrategy
from davis_analyzer.paper_trading.executor import run_backfill_auto
init_database()

ACCT = "prof_probe"
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

with get_market_conn() as c:
    uni = [r[0] for r in c.execute(
        "SELECT ts_code FROM daily_price WHERE trade_date='20210104' AND close>0 AND vol>0 "
        "ORDER BY amount DESC LIMIT 200")]

with sqlite3.connect(DB_PATH) as c:
    if (r := c.execute("SELECT id FROM paper_accounts WHERE name=?", (ACCT,)).fetchone()):
        aid = r[0]
        for t in ("paper_positions", "paper_trades", "paper_nav_history", "paper_shadow_trades"):
            c.execute(f"DELETE FROM {t} WHERE account_id=?", (aid,))
        c.execute("DELETE FROM paper_accounts WHERE id=?", (aid,))
        c.commit()

account = PaperAccount.create(name=ACCT, strategy_name="factor_threshold",
                              initial_capital=1_000_000, config={})
strategy = FactorThresholdStrategy(**BASE)

t0 = time.time()
pr = cProfile.Profile()
pr.enable()
run_backfill_auto(account, strategy, "20210104", "20210108",
                  universe_codes=uni, scoring_frequency=1)
pr.disable()
wall = time.time() - t0
print(f"\n=== 3 个交易日墙钟 {wall:.1f}s ({wall/3:.1f}s/天) ===")

s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
ps.print_stats(28)
out = s.getvalue()
# 只保留函数表, 去掉路径前缀噪音
for line in out.splitlines():
    if "davis_analyzer" in line or "stockhot" in line or "tushare" in line or "{" in line or "ncalls" in line or "seconds" in line or line.strip().startswith("/") is False:
        print(line.replace(PROJECT_ROOT + "/", ""))

account.close()
with sqlite3.connect(DB_PATH) as c:
    aid = c.execute("SELECT id FROM paper_accounts WHERE name=?", (ACCT,)).fetchone()[0]
    for t in ("paper_positions", "paper_trades", "paper_nav_history", "paper_shadow_trades"):
        c.execute(f"DELETE FROM {t} WHERE account_id=?", (aid,))
    c.execute("DELETE FROM paper_accounts WHERE id=?", (aid,))
    c.commit()
print("\n探针账户已清理 ✓")
