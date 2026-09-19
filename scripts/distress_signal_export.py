"""D 影子:戴维斯困境反转信号导出(2026-09-13 立项,用户批准开工).

三要素量化闸(T-1 因果):
  ① 深度回撤(SQL 预筛, 毫秒级): close_T-1 / 过去120交易日最高收盘 - 1 ≤ -40%
  ② 估值分位低: PE 3年分位 <30% 或 PB 3年分位 <30%(周期股/负EPS 由 valuation
     模块内置口径处理, 与主管线研报同源)
  ③ 景气拐点: delta_g > 0 或 stage=上升拐点(复用 executor 因子评分管道)

宇宙: 生产 daily_price(stock_basic L 状态 ∩ 上市≥3年 ∩ 现名非 ST/退)。
设计要点: distress 名单在成交额 top 池之外的冷门票里——先用廉价 SQL 把全市场
筛到几十只, 再对幸存者跑昂贵因子闸(与 G2 导出的架构相反)。
输出: logs/distress_signals/distress_list_<T-1>.json(composite=100-名次, 与 G2
名单同消费口径); 空名单=常态(防守语义, 消费端只卖不买)。
调度: crontab 18:40(G2 导出 18:30 后, 19:20 refresh 前——估值窗口止于 T-1)。
用法: .venv/bin/python scripts/distress_signal_export.py [--as-of YYYYMMDD]
"""
import os, sys, json, time, sqlite3
from datetime import datetime, timedelta
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")

from stockhot.data_layer.market_db import get_connection as get_market_conn
from stockhot.storage.database import init_database
from davis_analyzer.core.tushare_client import TushareClient
init_database()

OUT_DIR = "logs/distress_signals"
DRAWDOWN_WINDOW_CAL = 170   # 自然日 ≈ 120 交易日
DRAWDOWN_MIN = -0.40        # 深回撤闸
VAL_PCTL_MAX = 0.30         # PE 或 PB 3年分位闸


def t_minus_1() -> str:
    """最近已完整收盘日(19:27 槽在 19:20 刷新后, 当日收盘可用——时序修复 2026-09-17)."""
    with get_market_conn() as c:
        return c.execute("SELECT MAX(trade_date) FROM daily_price").fetchone()[0]


def deep_drawdown_candidates(as_of: str) -> dict[str, float]:
    """① SQL 预筛: 全市场深回撤(返回 {code: drawdown}), 附存活性/上市时长/ST 过滤."""
    w_start = (datetime.strptime(as_of, "%Y%m%d") - timedelta(days=DRAWDOWN_WINDOW_CAL)).strftime("%Y%m%d")
    age_cut = (datetime.strptime(as_of, "%Y%m%d") - timedelta(days=3 * 365)).strftime("%Y%m%d")
    with get_market_conn() as c:
        basics = {r[0]: (r[1] or "", r[2] or "") for r in c.execute(
            "SELECT ts_code, name, list_date FROM stock_basic WHERE list_status='L'")}
        latest = {r[0]: r[1] for r in c.execute(
            "SELECT ts_code, close FROM daily_price WHERE trade_date=? AND close>0 AND vol>0", (as_of,))}
        highs = {r[0]: r[1] for r in c.execute(
            "SELECT ts_code, MAX(close) FROM daily_price WHERE trade_date BETWEEN ? AND ? "
            "AND close>0 GROUP BY ts_code", (w_start, as_of))}
    out = {}
    for code, close in latest.items():
        name, ld = basics.get(code, (None, None))
        if name is None or ld == "" or ld > age_cut:
            continue
        if "ST" in name or "退" in name:
            continue
        hi = highs.get(code)
        if hi and hi > 0:
            dd = close / hi - 1
            if dd <= DRAWDOWN_MIN:
                out[code] = round(dd, 4)
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None)
    args = ap.parse_args()
    as_of = args.as_of or t_minus_1()
    client = TushareClient()
    t0 = time.time()

    dd = deep_drawdown_candidates(as_of)
    cand = sorted(dd)
    print(f"[distress_export] as_of={as_of} ①深回撤(≤{DRAWDOWN_MIN:.0%})幸存 {len(cand)} 只 "
          f"({time.time()-t0:.1f}s)", flush=True)
    if not cand:
        _dump(as_of, [], 0, 0, t0)
        return

    # ③ 景气拐点(因子分管道; 瞬时 API 故障重试一次——首跑曾因 stk_holdernumber 抖动中断)
    from davis_analyzer.systems.paper_trading.executor import _compute_factor_scores_at
    fs = {}
    for attempt in (1, 2):
        try:
            fs = _compute_factor_scores_at(client, datetime.strptime(as_of, "%Y%m%d").date(), cand)
            break
        except Exception as e:
            if attempt == 2:
                raise
            print(f"  [WARN] 因子评分失败({type(e).__name__}), 30s 后重试一次", flush=True)
            time.sleep(30)
    inflected = []
    for code in cand:
        f = fs.get(code, {})
        dg, stage = f.get("delta_g"), f.get("stage")
        if (dg is not None and dg > 0) or stage == "上升拐点":
            inflected.append((code, dg, stage))
    print(f"  ③景气拐点(delta_g>0/上升拐点)幸存 {len(inflected)} 只 ({time.time()-t0:.1f}s)", flush=True)
    if not inflected:
        _dump(as_of, [], len(cand), 0, t0)
        return

    # ② 估值分位(valuation 模块, 与研报管线同源)
    from davis_analyzer.core.types import StockInfo
    from davis_analyzer.factors.valuation import batch_valuation
    infos = [StockInfo(ts_code=code, name=fs.get(code, {}).get("name", code),
                       industry="", list_status="L", is_cyclical=False)
             for code, _, _ in inflected]
    # name 从 stock_basic 补
    with get_market_conn() as c:
        names = {r[0]: r[1] for r in c.execute("SELECT ts_code, name FROM stock_basic")}
    infos = [StockInfo(ts_code=i.ts_code, name=names.get(i.ts_code, i.ts_code),
                       industry="", list_status="L", is_cyclical=False) for i in infos]
    val = batch_valuation(client, infos)
    passed = []
    for code, dg, stage in inflected:
        v = val.get(code)
        if v is None:
            continue
        _, pe_p, pb_p = v
        if (pe_p is not None and pe_p < VAL_PCTL_MAX) or (pb_p is not None and pb_p < VAL_PCTL_MAX):
            passed.append((code, dg, stage, pe_p, pb_p))
    print(f"  ②估值分位(<{VAL_PCTL_MAX:.0%})幸存 {len(passed)} 只 ({time.time()-t0:.1f}s)", flush=True)

    passed.sort(key=lambda x: (x[1] if x[1] is not None else -99), reverse=True)
    # 宽进严排: 三闸选择性宽(332/全市场)是常态——名单截断至 ΔG 前 40, 与消费端
    # min_score=60 的可达范围一致; 闸门收紧与否留给影子期校准, 不预优化。
    passed = passed[:40]
    entries = [{"ts_code": code, "name": names.get(code, code), "composite": 100 - i,
                "reason": f"回撤{dd[code]:.0%} PE分位{_fmt(pe_p)} PB分位{_fmt(pb_p)} "
                          f"ΔG{dg if dg is not None else '—'} {stage or ''}"}
               for i, (code, dg, stage, pe_p, pb_p) in enumerate(passed)]
    _dump(as_of, entries, len(cand), len(passed), t0, client=client, sample=entries[:3])


def _fmt(v):
    return f"{v:.0%}" if v is not None else "—"


def _dump(as_of, entries, n_dd, n_pass, t0, client=None, sample=None):
    out = {"as_of": as_of, "n_drawdown_candidates": n_dd, "n_pass": len(entries),
           "gates": f"回撤≤{DRAWDOWN_MIN:.0%} | PE/PB分位<{VAL_PCTL_MAX:.0%} | ΔG>0/上升拐点",
           "generated_at": datetime.now().isoformat(timespec="seconds"),
           "elapsed_s": round(time.time() - t0, 1), "list": entries}
    os.makedirs(OUT_DIR, exist_ok=True)
    path = f"{OUT_DIR}/distress_list_{as_of}.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"  放行 {len(entries)} 只 → {path} ({out['elapsed_s']}s)")
    for e in (sample or [])[:5]:
        print(f"    {e['composite']:>3}. {e['ts_code']} {e['name']} | {e['reason']}")
    if not entries:
        print("  空名单(困境反转常态, 消费端只卖不买)")


if __name__ == "__main__":
    main()
