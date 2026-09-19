"""G2 信号导出 — 尾盘轮动 g2_shadow 账户的名单生产器(0010 预注册步骤①,2026-09-12 落地).

口径:
  - T-1 = 生产 daily_price 中 < 今日 的最大交易日(不混退市研究库——本脚本属生产
    信号管线, 退市股本就不可被买入);
  - 宇宙 = T-1 成交额 top200(与 G2 五年验证同口径的滚动版);
  - G2 十道闸 = **用真实执行器跑 T-1 单日**(scratch 账户 max_positions 调大, 取
    全量放行集而非 5 槽截断), BUY 成交顺序 = 综合分名次; 复用
    executor 的 _compute_davis_scores_at/_compute_factor_scores_at, T-1 因果;
  - 空名单 = 防守特性(bear 日 0 买入), 非错误(预注册 D2: 空名单日轮动只卖不买);
  - 执行器涨停可成交性 haircut 会滤掉 T-1 涨停股——名单语义 = 「T-1 口径可成交
    放行集」, 14:40 实时另有涨停拒买兜底, 口径一致.

用法: .venv/bin/python scripts/g2_signal_export.py [--as-of YYYYMMDD](重放指定日)
输出: logs/g2_signals/g2_list_<T-1>.json
调度(crontab 19:25 槽, 2026-09-17 重定时——须在 19:20 行情刷新后):
  25 19 * * 1-5 cd /home/leo/Projects/CodeAgentDashboard && .venv/bin/python scripts/g2_signal_export.py >> logs/g2_signals/export.log 2>&1
"""
import os, sys, json, sqlite3, time
from datetime import datetime
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")

import davis_analyzer.factors.market_regime as mr
mr._MA120_BEAR_THRESHOLD = -999.0

from stockhot.data_layer.market_db import get_connection as get_market_conn
from stockhot.storage.database import init_database, DB_PATH
from davis_analyzer.systems.paper_trading.account import PaperAccount
from davis_analyzer.systems.paper_trading.strategy import FactorThresholdStrategy
from davis_analyzer.systems.paper_trading.executor import run_backfill_auto
init_database()

OUT_DIR = "logs/g2_signals"
SCRATCH = "g2_export_tmp"

# G2 生产参数(与 gx_G2_bull60/0010/0011/0012 完全一致); max_positions 调大取全量放行集
G2_BASE = dict(
    max_positions=200, risk_stop_multiplier=0.70, sell_momentum=30,
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


def t_minus_1() -> str:
    """最近已完整收盘日(2026-09-17 时序修复: 19:25 槽在 19:20 刷新后运行, 当日收盘可用;
    原 18:30 槽 + "<今日" 口径永远比轮动消费所需晚一个收盘, 造成名单系统性过期)."""
    with get_market_conn() as c:
        row = c.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()
    return row[0]


def universe(as_of: str) -> list[str]:
    """成交额 top200 ∩ 上市满1年(2026-09-17 反缺失虚高审计: 次新股短窗动量
    重归一化虚高——60日+30%即满分无长窗证据, 生产宇宙此前无年限闸)."""
    from datetime import datetime, timedelta
    age_cut = (datetime.strptime(as_of, "%Y%m%d") - timedelta(days=365)).strftime("%Y%m%d")
    with get_market_conn() as c:
        rows = c.execute(
            """SELECT d.ts_code FROM daily_price d JOIN stock_basic s ON d.ts_code=s.ts_code
               WHERE d.trade_date=? AND d.close>0 AND d.vol>0
                 AND COALESCE(s.list_date,'') <= ? AND s.list_status='L'
               ORDER BY d.amount DESC LIMIT 200""", (as_of, age_cut)).fetchall()
    return [r[0] for r in rows]


def reset_scratch():
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT id FROM paper_accounts WHERE name=?", (SCRATCH,)).fetchone()
        if row:
            aid = row[0]
            for tbl in ("paper_positions", "paper_trades", "paper_nav_history", "paper_shadow_trades"):
                c.execute(f"DELETE FROM {tbl} WHERE account_id=?", (aid,))
            c.execute("DELETE FROM paper_accounts WHERE id=?", (aid,))
            c.commit()
    return PaperAccount.create(name=SCRATCH, strategy_name="factor_threshold",
                               initial_capital=100_000_000, config={})


def drop_scratch():
    with sqlite3.connect(DB_PATH) as c:
        aid = c.execute("SELECT id FROM paper_accounts WHERE name=?", (SCRATCH,)).fetchone()[0]
        for tbl in ("paper_positions", "paper_trades", "paper_nav_history", "paper_shadow_trades"):
            c.execute(f"DELETE FROM {tbl} WHERE account_id=?", (aid,))
        c.execute("DELETE FROM paper_accounts WHERE id=?", (aid,))
        c.commit()


def sample_verify(codes: list[str], as_of: str):
    """预注册抽样核验: 放行股的动量/次维度确实过闸."""
    from davis_analyzer.core.tushare_client import TushareClient
    client = TushareClient()
    from davis_analyzer.systems.paper_trading.executor import _compute_factor_scores_at
    fs = _compute_factor_scores_at(client, datetime.strptime(as_of, "%Y%m%d").date(), codes)
    for code in codes:
        f = fs.get(code, {})
        mom = f.get("momentum")
        dims = {k: round(f[k], 1) for k in ("holder", "dividend", "forecast_leading", "prosperity")
                if f.get(k) is not None}
        passed = [k for k, v in (("筹码>40", f.get("holder")), ("红利>55", f.get("dividend")),
                                 ("前瞻>70", f.get("forecast_leading")), ("景气>45", f.get("prosperity")))
                  if v is not None and v > {"筹码>40": 40, "红利>55": 55, "前瞻>70": 70, "景气>45": 45}[k]]
        print(f"  核验 {code}: 动量={mom and round(mom,1)}(闸70/牛市60) 次维度={dims} 过闸:{passed}")
        if mom is None or mom <= 60:
            print(f"    [WARN] 动量 {mom} 不超过放宽闸 60, 请人工复核")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None, help="重放指定日 YYYYMMDD(默认 T-1)")
    args = ap.parse_args()
    as_of = args.as_of or t_minus_1()
    uni = universe(as_of)
    print(f"[g2_signal_export] as_of={as_of} 宇宙={len(uni)}(成交额top200) 生成于 {datetime.now():%F %T}")

    account = reset_scratch()
    strategy = FactorThresholdStrategy(**G2_BASE)
    t0 = time.time()
    run_backfill_auto(account, strategy, as_of, as_of,
                      universe_codes=uni, scoring_frequency=1)
    trades = [t for t in account.get_trades() if t.action == "BUY"]
    account.close()
    drop_scratch()

    entries = [{"ts_code": t.ts_code, "name": t.name, "composite": 100 - i,
                "price": t.price, "reason": t.signal_reason}
               for i, t in enumerate(trades)]  # id 序 = 执行器综合分名次; composite=100-名次
    # (保序分: 消费端 DavisDoubleStrategy 有 min_score=60 候选闸, 100-名次严格保序
    #  且 ≤40 只名单全部过闸——分值仅用于排序/闸兼容, 名单本身的取舍已由真实闸门决定)
    regime = mr.get_market_regime(as_of)
    out = {"as_of": as_of, "universe_n": len(uni), "regime": regime,
           "n_pass": len(entries), "config": "G2=gx_G2_bull60(max_positions放大取全量)",
           "generated_at": datetime.now().isoformat(timespec="seconds"),
           "elapsed_s": round(time.time() - t0, 1), "list": entries}
    os.makedirs(OUT_DIR, exist_ok=True)
    path = f"{OUT_DIR}/g2_list_{as_of}.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"  regime={regime} 放行 {len(entries)} 只 ({out['elapsed_s']}s) → {path}")
    for e in entries[:5]:
        print(f"    {e['composite']:>3}. {e['ts_code']} {e['name']} @ {e['price']} | {e['reason'][:50]}")
    if entries:
        print("  抽样核验(预注册: 3 只过闸验证):")
        sample_verify([e["ts_code"] for e in entries[:3]], as_of)
    else:
        print("  空名单(防守特性, 预注册 D2: 轮动只卖不买), 非错误")


if __name__ == "__main__":
    main()
