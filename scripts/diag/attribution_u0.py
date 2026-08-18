"""生产基线 U0 账户的 PnL 归因分解.

回答三个诊断问题:
  1. 14% 最大回撤发生在哪段, 由哪几笔交易贡献?
  2. 哪些月份是最大负贡献, 当时市场状态是什么?
  3. 亏损交易有没有共性 (行业/入场时机/持仓时长/卖出原因)?

同时核算真·年化 Sharpe (日收益口径) vs 我们一直报告的 收益/MDD 口径.

数据源:
  stockhot.db   : paper_nav_history / paper_trades (账户 fp_U0_main_pool)
  market_data.db: stock_basic.industry / index_daily(000001.SH) / ivix_history
  market_regime : HMM 季度扩展 regime (与策略实跑一致)
"""
import os, sys, json, sqlite3
from collections import defaultdict, deque

import numpy as np

PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="ERROR")

ACCOUNT = "fp_U0_main_pool"
INITIAL_CAPITAL = 1_000_000

# ── 数据加载 ─────────────────────────────────────────────────────────────


def load_nav():
    conn = sqlite3.connect("storage/database/stockhot.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT n.trade_date, n.total_equity, n.daily_return
        FROM paper_nav_history n JOIN paper_accounts a ON n.account_id=a.id
        WHERE a.name=? ORDER BY n.trade_date""", (ACCOUNT,)).fetchall()
    conn.close()
    return [(r["trade_date"], r["total_equity"], r["daily_return"]) for r in rows]


def load_trades():
    conn = sqlite3.connect("storage/database/stockhot.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT t.id, t.trade_date, t.ts_code, t.name, t.action, t.shares, t.price,
               t.amount, t.cost, t.signal_reason
        FROM paper_trades t JOIN paper_accounts a ON t.account_id=a.id
        WHERE a.name=? ORDER BY t.id""", (ACCOUNT,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_industry():
    conn = sqlite3.connect("storage/database/market_data.db")
    rows = conn.execute("SELECT ts_code, industry FROM stock_basic").fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def load_market_context():
    """index 000001.SH close → ret20/ret60/ma200_rel; ivix close."""
    conn = sqlite3.connect("storage/database/market_data.db")
    idx = conn.execute(
        "SELECT trade_date, close FROM index_daily WHERE ts_code='000001.SH' ORDER BY trade_date"
    ).fetchall()
    ivix_rows = conn.execute(
        "SELECT trade_date, close FROM ivix_history ORDER BY trade_date").fetchall()
    conn.close()
    dates = [r[0] for r in idx]
    closes = np.array([r[1] for r in idx], dtype=float)
    ctx = {}
    for i, d in enumerate(dates):
        ret20 = closes[i] / closes[i - 20] - 1 if i >= 20 else None
        ret60 = closes[i] / closes[i - 60] - 1 if i >= 60 else None
        ma200 = closes[max(0, i - 199):i + 1].mean() if i >= 199 else None
        ctx[d] = {
            "ret20": ret20, "ret60": ret60,
            "above_ma200": (closes[i] / ma200 - 1) if ma200 else None,
            "idx_close": closes[i],
        }
    ivix = {r[0]: r[1] for r in ivix_rows}
    return ctx, ivix, dict(zip(dates, closes))


def get_regime_series(dates):
    from davis_analyzer.market_regime import get_market_regime
    out = {}
    for d in sorted(set(dates)):
        try:
            out[d] = get_market_regime(d)
        except Exception:
            out[d] = "unknown"
    return out


# ── FIFO 交易回合 ────────────────────────────────────────────────────────


def build_round_trips(trades):
    """按 ts_code 聚合: 份额 0→正 开仓, 回到 0 平仓. 输出回合列表."""
    by_code = defaultdict(list)
    for t in trades:
        by_code[t["ts_code"]].append(t)

    trips = []
    for code, tl in by_code.items():
        outstanding = 0
        cur = None
        for t in tl:
            if t["action"] == "BUY":
                if outstanding == 0:
                    cur = {
                        "ts_code": code, "name": t["name"],
                        "entry_date": t["trade_date"], "buy_amount": 0.0,
                        "buy_cost": 0.0, "sells": [],
                    }
                outstanding += t["shares"]
                cur["buy_amount"] += t["amount"]
                cur["buy_cost"] += t["cost"] or 0.0
            else:  # SELL
                if cur is None:
                    continue  # 数据异常防御
                outstanding -= t["shares"]
                cur["sells"].append({
                    "date": t["trade_date"], "amount": t["amount"],
                    "cost": t["cost"] or 0.0, "reason": t["signal_reason"] or "",
                })
                if outstanding <= 0:
                    cur["exit_date"] = t["trade_date"]
                    cur["sell_amount"] = sum(s["amount"] for s in cur["sells"])
                    cur["sell_cost"] = sum(s["cost"] for s in cur["sells"])
                    cur["reason"] = cur["sells"][-1]["reason"]
                    trips.append(cur)
                    cur = None
                    outstanding = 0
        if cur is not None:  # 回测结束仍持仓 → 未实现
            cur["exit_date"] = None
            cur["sell_amount"] = sum(s["amount"] for s in cur["sells"])
            cur["sell_cost"] = sum(s["cost"] for s in cur["sells"])
            cur["reason"] = "STILL_OPEN"
            trips.append(cur)
    return trips


def annotate_trips(trips, industry, ctx, ivix, regime):
    for tr in trips:
        buy_total = tr["buy_amount"] + tr["buy_cost"]
        sell_total = tr["sell_amount"] - tr["sell_cost"]
        tr["invested"] = buy_total
        tr["pnl"] = sell_total - buy_total
        tr["pnl_pct"] = tr["pnl"] / buy_total * 100 if buy_total > 0 else 0.0
        ed = tr["entry_date"]
        tr["industry"] = industry.get(tr["ts_code"], "?")
        if tr["exit_date"]:
            d0 = int(ed); d1 = int(tr["exit_date"])
            tr["hold_days"] = max(1, (d1 - d0) // 10000 * 365 + ((d1 // 100) % 100 - (d0 // 100) % 100) * 30 + (d1 % 100 - d0 % 100))
        else:
            tr["hold_days"] = None
        c = ctx.get(ed, {})
        tr["entry_ret20"] = c.get("ret20")
        tr["entry_ivix"] = ivix.get(ed)
        tr["entry_regime"] = regime.get(ed, "?")
        r = tr["reason"]
        if "超跌" in r or "反弹" in r: tr["reason_cat"] = "超跌反弹"
        elif "止损" in r: tr["reason_cat"] = "硬止损"
        elif "止盈" in r: tr["reason_cat"] = "止盈"
        elif "高位放量" in r: tr["reason_cat"] = "高位放量"
        elif "动量" in r: tr["reason_cat"] = "动量崩塌"
        elif "景气" in r: tr["reason_cat"] = "景气拐点"
        elif "赛道" in r or "行业" in r or "板块" in r: tr["reason_cat"] = "板块切换"
        elif "筹码" in r: tr["reason_cat"] = "筹码分散"
        elif r == "STILL_OPEN": tr["reason_cat"] = "期末持仓"
        else: tr["reason_cat"] = "其他"
        tr["is_bounce"] = "超跌" in (tr.get("first_buy_reason", "") or "") or any(
            "超跌" in s["reason"] for s in tr.get("sells", []))
    return trips


def sell_reason_cat(r: str) -> str:
    if "止损" in r: return "硬止损"
    if "止盈" in r: return "止盈"
    if "高位放量" in r: return "高位放量"
    if "动量" in r: return "动量崩塌"
    if "景气" in r: return "景气拐点"
    if "赛道" in r or "行业" in r or "板块" in r: return "板块切换"
    if "筹码" in r: return "筹码分散"
    return "其他"


# ── 指标 ─────────────────────────────────────────────────────────────────


def drawdown_episodes(nav, threshold=6.0):
    """回撤 episode: 从创新高到修复创新高. 返回超过 threshold 的片段."""
    episodes = []
    peak_val, peak_date = nav[0][1], nav[0][0]
    cur = None
    for d, v, _ in nav:
        if v >= peak_val:
            if cur and cur["depth"] > threshold:
                cur["recover_date"] = d
                episodes.append(cur)
            cur = None
            peak_val, peak_date = v, d
        else:
            dd = (peak_val - v) / peak_val * 100
            if cur is None:
                cur = {"peak_date": peak_date, "peak_val": peak_val,
                       "trough_date": d, "trough_val": v, "depth": dd}
            elif dd > cur["depth"]:
                cur["trough_date"], cur["trough_val"], cur["depth"] = d, v, dd
    if cur and cur["depth"] > threshold:
        cur["recover_date"] = None
        episodes.append(cur)
    episodes.sort(key=lambda e: -e["depth"])
    return episodes


def month_key(d): return d[:6]


def main():
    nav = load_nav()
    trades = load_trades()
    industry = load_industry()
    ctx, ivix, idx_close = load_market_context()

    print(f"\n{'=' * 78}")
    print(f"  U0 生产基线归因 — {nav[0][0]} → {nav[-1][0]} ({len(nav)} 交易日, {len(trades)} 笔流水)")
    print(f"{'=' * 78}")

    # ── 1. 总体指标 + 真 Sharpe 口径 ──
    navs = np.array([v for _, v, _ in nav], dtype=float)
    rets = navs[1:] / navs[:-1] - 1  # 从净值推导, 不信任 daily_return 列
    total_ret = (navs[-1] / INITIAL_CAPITAL - 1) * 100
    mdd = ((np.maximum.accumulate(navs) - navs) / np.maximum.accumulate(navs) * 100).max()
    sharpe_true = rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else 0.0
    calmar = total_ret / mdd if mdd > 0 else 0
    print(f"\n[1] 总体指标")
    print(f"    总收益 {total_ret:+.2f}% | MDD {mdd:.2f}%")
    print(f"    真·年化Sharpe(rf=0) {sharpe_true:.3f} | Calmar(收益/MDD) {calmar:.3f}  ← 我们一直叫它 Sharpe")
    print(f"    日胜率 {(rets > 0).mean() * 100:.1f}% | 日波动 {rets.std() * 100:.2f}% | 年化波动 {rets.std() * np.sqrt(252) * 100:.1f}%")

    # ── 2. 月度收益矩阵 + 基准 ──
    monthly = {}
    for d, v, _ in nav:
        mk = month_key(d)
        monthly.setdefault(mk, v)  # 月初第一个 NAV
        monthly[mk] = v  # 最后覆盖
    prev = INITIAL_CAPITAL
    idx_monthly = {}
    # 指数月度 (每月最后收盘)
    im = {}
    for d, c in sorted(idx_close.items()):
        im[d[:6]] = c
    year_end_nav = {}
    for mk in sorted(monthly):
        year_end_nav[mk[:4]] = monthly[mk]
    years = sorted(year_end_nav)
    print(f"    {'年份':<6} {'策略':>9} {'指数':>9} {'超额':>9} {'月胜率':>7}")
    prev_s, prev_i = INITIAL_CAPITAL, None
    for y in years:
        s_ret = (year_end_nav[y] / prev_s - 1) * 100
        i_ms = [mk for mk in sorted(im) if mk.startswith(y)]
        if i_ms and prev_i is not None:
            i_ret = (im[i_ms[-1]] / prev_i - 1) * 100
        elif i_ms:
            first_idx_year = sorted(im)[0][:4]
            i_ret = (im[i_ms[-1]] / im[i_ms[0]] - 1) * 100 if y == first_idx_year else None
        else:
            i_ret = None
        ms = [mk for mk in sorted(monthly) if mk.startswith(y)]
        # 月胜率
        pv = prev_s
        wins = 0; n = 0
        for mk in ms:
            r = (monthly[mk] / pv - 1) * 100
            if abs(r) > 0.01: wins += (r > 0); n += 1
            pv = monthly[mk]
        wr = wins / n * 100 if n else 0
        ex = (s_ret - i_ret) if i_ret is not None else None
        print(f"    {y:<6} {s_ret:>+8.2f}% {i_ret if i_ret is not None else float('nan'):>+8.2f}% "
              f"{ex if ex is not None else float('nan'):>+8.2f}% {wr:>6.0f}%")
        prev_s = year_end_nav[y]
        if i_ms: prev_i = im[i_ms[-1]]

    # 最差月份
    all_months = []
    pv = INITIAL_CAPITAL
    for mk in sorted(monthly):
        r = (monthly[mk] / pv - 1) * 100
        all_months.append((mk, r))
        pv = monthly[mk]
    worst = sorted(all_months, key=lambda x: x[1])[:8]

    trips_raw = build_round_trips(trades)
    regime_dates = [t["entry_date"] for t in trips_raw] + [w[0] + "01" for w in worst]
    regime = get_regime_series(regime_dates)

    print(f"\n    最差 8 个月 (市场上下文):")
    print(f"    {'月份':<8} {'收益':>8} {'指数20d':>9} {'iVIX':>6} {'regime':>8}")
    for mk, r in worst:
        dd = f"{mk}01"
        # 找该月任意一天上下文
        ctx_d = next((d for d in ctx if d.startswith(mk)), None)
        c = ctx.get(ctx_d, {})
        r20 = c.get("ret20")
        iv = ivix.get(ctx_d)
        print(f"    {mk:<8} {r:>+7.2f}% {r20 * 100 if r20 is not None else float('nan'):>+8.1f}% "
              f"{iv if iv is not None else float('nan'):>6.1f} {regime.get(dd, '?'):>8}")

    # ── 3. 回撤 episode ──
    eps = drawdown_episodes(nav)
    print(f"\n[3] 回撤 episodes (depth>6%): {len(eps)} 段")
    for i, e in enumerate(eps[:5]):
        print(f"    #{i + 1}: {e['peak_date']} 峰值{e['peak_val'] / 1e4:.1f}万 → {e['trough_date']} 谷值{e['trough_val'] / 1e4:.1f}万 "
              f"深度{e['depth']:.1f}% 修复日 {e.get('recover_date') or '未修复'}")

    trips = annotate_trips(trips_raw, industry, ctx, ivix, regime)
    closed = [t for t in trips if t["exit_date"]]
    print(f"\n    交易回合: {len(trips)} 个 (平仓 {len(closed)}, 期末持仓 {len(trips) - len(closed)})")

    for i, e in enumerate(eps[:3]):
        w0, w1 = e["peak_date"], e.get("recover_date") or nav[-1][0]
        in_win = [t for t in closed if w0 <= t["exit_date"] <= w1]
        losers = sorted([t for t in in_win if t["pnl"] < 0], key=lambda t: t["pnl"])[:6]
        gross_loss = sum(t["pnl"] for t in in_win if t["pnl"] < 0)
        print(f"\n    Episode #{i + 1} ({e['peak_date']}→{e['trough_date']}, -{e['depth']:.1f}%): "
              f"窗口内平仓 {len(in_win)} 笔, 累计亏损 {gross_loss / 1e4:.1f}万")
        print(f"      贡献最大的亏损:")
        for t in losers:
            print(f"        {t['entry_date']}→{t['exit_date']} {t['ts_code']} {t['name'][:6]:<6} "
                  f"{t['industry'][:4]:<4} {t['pnl_pct']:>+6.1f}% {t['pnl'] / 1e4:>+6.1f}万 [{t['reason_cat']}]")

    # ── 4. 回合聚合 ──
    print(f"\n[4] 交易回合聚合 (仅平仓 {len(closed)} 笔)")
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    pf = sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses)) if losses else float("inf")
    print(f"    胜率 {len(wins) / len(closed) * 100:.1f}% | 均盈 {np.mean([t['pnl_pct'] for t in wins]):+.1f}% "
          f"| 均亏 {np.mean([t['pnl_pct'] for t in losses]):+.1f}% | 盈亏比 {pf:.2f}")

    by = defaultdict(lambda: [0, 0.0, 0])  # year → [n, pnl, wins]
    for t in closed:
        y = t["entry_date"][:4]
        by[y][0] += 1; by[y][1] += t["pnl"]; by[y][2] += (t["pnl"] > 0)
    print(f"\n    按入场年份:")
    for y in sorted(by):
        n, pnl, w = by[y]
        print(f"      {y}: {n:>3} 笔 {pnl / 1e4:>+7.1f}万 胜率{w / n * 100:>5.1f}%")

    br = defaultdict(lambda: [0, 0.0])
    for t in closed:
        br[t["reason_cat"]][0] += 1; br[t["reason_cat"]][1] += t["pnl"]
    print(f"\n    按卖出原因:")
    for k, (n, pnl) in sorted(br.items(), key=lambda x: x[1][1]):
        print(f"      {k:<6} {n:>4} 笔 {pnl / 1e4:>+8.1f}万 均{pnl / n / 1e3:>+7.1f}千")

    bi = defaultdict(lambda: [0, 0.0])
    for t in closed:
        bi[t["industry"]][0] += 1; bi[t["industry"]][1] += t["pnl"]
    print(f"\n    按行业 (盈/亏 top6):")
    for k, (n, pnl) in sorted(bi.items(), key=lambda x: -x[1][1])[:6]:
        print(f"      +{k:<6} {n:>3} 笔 {pnl / 1e4:>+7.1f}万")
    for k, (n, pnl) in sorted(bi.items(), key=lambda x: x[1][1])[:6]:
        print(f"      -{k:<6} {n:>3} 笔 {pnl / 1e4:>+7.1f}万")

    def bucket(h):
        if h is None: return "未平仓"
        if h <= 5: return "01-5天"
        if h <= 10: return "06-10天"
        if h <= 20: return "11-20天"
        if h <= 40: return "21-40天"
        return "40天+"
    bh = defaultdict(lambda: [0, 0.0, 0])
    for t in closed:
        b = bucket(t["hold_days"])
        bh[b][0] += 1; bh[b][1] += t["pnl"]; bh[b][2] += (t["pnl"] > 0)
    print(f"\n    按持仓时长:")
    for b in ["01-5天", "06-10天", "11-20天", "21-40天", "40天+"]:
        n, pnl, w = bh.get(b, [0, 0, 0])
        if n: print(f"      {b:<7} {n:>4} 笔 {pnl / 1e4:>+8.1f}万 胜率{w / n * 100:>5.1f}%")

    # 入场市场状态 vs 结果
    print(f"\n    按入场时市场状态 (regime × ret20):")
    bg = defaultdict(lambda: [0, 0.0, 0])
    for t in closed:
        r20 = t["entry_ret20"]
        g = t["entry_regime"]
        sub = "强" if (r20 is not None and r20 > 0.03) else ("弱" if (r20 is not None and r20 < -0.03) else "平")
        key = f"{g}/{sub}"
        bg[key][0] += 1; bg[key][1] += t["pnl"]; bg[key][2] += (t["pnl"] > 0)
    for k, (n, pnl, w) in sorted(bg.items(), key=lambda x: x[1][1]):
        print(f"      {k:<8} {n:>4} 笔 {pnl / 1e4:>+8.1f}万 胜率{w / n * 100:>5.1f}%")

    # 盈利集中度
    srt = sorted(closed, key=lambda t: -t["pnl"])
    top5 = sum(t["pnl"] for t in srt[:5])
    bot5 = sum(t["pnl"] for t in srt[-5:])
    gross_w = sum(t["pnl"] for t in wins)
    print(f"\n    集中度: Top5 赢家 {top5 / 1e4:+.1f}万 (占毛盈 {top5 / gross_w * 100:.0f}%) | Top5 输家 {bot5 / 1e4:+.1f}万")
    print(f"    Top5 赢家:")
    for t in srt[:5]:
        print(f"      {t['entry_date']}→{t['exit_date']} {t['ts_code']} {t['name'][:6]:<6} {t['industry'][:4]:<4} "
              f"{t['pnl_pct']:>+7.1f}% {t['pnl'] / 1e4:>+6.1f}万 [{t['reason_cat']}] 持{t['hold_days']}天")

    # ── 5. 暴露时间线 ──
    dates = [d for d, _, _ in nav]
    dset = {d: i for i, d in enumerate(dates)}
    expo = np.zeros(len(dates))
    for t in trips:
        i0 = dset.get(t["entry_date"])
        i1 = dset.get(t["exit_date"], len(dates) - 1) if t["exit_date"] else len(dates) - 1
        if i0 is not None:
            expo[i0:i1 + 1] += 1
    print(f"\n[5] 仓位暴露: 平均 {expo.mean():.2f} 格 | 空仓天数 {(expo == 0).sum()} ({(expo == 0).mean() * 100:.0f}%) | 满仓≥4格 {(expo >= 4).mean() * 100:.0f}%")
    by_year_expo = defaultdict(list)
    for i, d in enumerate(dates):
        by_year_expo[d[:4]].append(expo[i])
    for y in sorted(by_year_expo):
        arr = np.array(by_year_expo[y])
        print(f"      {y}: 平均{arr.mean():.2f}格 空仓{(arr == 0).mean() * 100:>4.0f}% ≥4格{(arr >= 4).mean() * 100:>4.0f}%")

    # 保存明细
    out = {
        "account": ACCOUNT,
        "total_ret": total_ret, "mdd": mdd, "sharpe_true": sharpe_true, "calmar": calmar,
        "worst_months": worst,
        "episodes": [{k: v for k, v in e.items()} for e in eps[:8]],
        "round_trips": [{k: v for k, v in t.items() if k != "sells"} for t in trips],
    }
    with open("logs/attribution_u0.json", "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False, default=str)
    print(f"\n  明细已存 logs/attribution_u0.json")


if __name__ == "__main__":
    main()
