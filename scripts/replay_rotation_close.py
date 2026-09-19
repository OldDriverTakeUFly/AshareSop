"""盘后收盘价补轮动 — 行情源故障日的兜底重放(2026-09-18 事故沉淀).

场景: 14:40 尾盘轮动因实时行情源故障(东财+新浪双败)三连败放弃时——live/mini 有
19:00 inject 兜底, g2_shadow/distress_shadow 没有。本工具在 19:20 行情刷新后运行,
以 daily_price 当日**收盘价**补跑全部轮动账户(与 inject 成交语义一致: 盘后按收盘价),
幂等(已执行账户自动跳过), pct_map 取当日 pct_chg(跌停顺延判断用)。

用法: .venv/bin/python scripts/replay_rotation_close.py [--date YYYYMMDD]
挂载: systemd 一次性/常态 timer(19:25, 刷新后)——常态挂载亦无害: 正常日 14:40 已
成功, 本工具全账户跳过空转退出。
"""
import os, sys, sqlite3
from datetime import datetime
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT / "stockhot" if False else f"{PROJECT_ROOT}/stockhot/invest_sop/scripts")
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="INFO")

from stockhot.data_layer.market_db import get_connection as get_market_conn
from stockhot.storage.database import DB_PATH
from davis_analyzer.paper_trading.account import PaperAccount

sys.path.insert(0, f"{PROJECT_ROOT}/stockhot")
import importlib
import intraday_rotation as rot  # stockhot/invest_sop/scripts 已在 path
from inject_screen_to_paper import bridge_to_davis_scores


def close_prices(day: str) -> tuple[dict[str, float], dict[str, float]]:
    """当日收盘价 + pct_chg(pct key=code6)."""
    with get_market_conn() as c:
        rows = c.execute(
            "SELECT ts_code, close, pct_chg FROM daily_price WHERE trade_date=? AND close>0",
            (day,)).fetchall()
    prices, pcts = {}, {}
    for ts, close, pct in rows:
        prices[ts] = float(close)
        pcts[ts.split(".")[0]] = float(pct) if pct is not None else 0.0
    return prices, pcts


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    args = ap.parse_args()
    day = args.date

    with get_market_conn() as c:
        have = c.execute("SELECT COUNT(*) FROM daily_price WHERE trade_date=?", (day,)).fetchone()[0]
    if not have:
        print(f"[replay] {day} 当日行情未入daily_price(19:20刷新未跑?), 放弃")
        sys.exit(1)
    prices, pcts = close_prices(day)
    print(f"[replay] {day} 收盘价 {len(prices)} 只就绪")

    # 名单(与 run_rotation 同口径)
    as_of, top20 = rot._load_latest_top20()
    davis_scores = bridge_to_davis_scores(top20) if top20 else {}
    g2_as_of, g2_list = rot._load_latest_g2_list()
    davis_scores_g2 = bridge_to_davis_scores(g2_list)
    ds_as_of, ds_list = rot._load_latest_list_file(rot.DISTRESS_SIGNAL_DIR, "distress_list")
    davis_scores_ds = bridge_to_davis_scores(ds_list)
    print(f"[replay] 基准 top20@{as_of} {len(davis_scores)} | G2@{g2_as_of} {len(davis_scores_g2)} "
          f"| 困境@{ds_as_of} {len(davis_scores_ds)}")

    for name in [*rot.ROTATION_ACCOUNTS, *rot.SHADOW_ACCOUNTS]:
        if name == rot.G2_SHADOW_ACCOUNT and not g2_as_of:
            print(f"[replay] {name} 无新鲜名单, 停跑"); continue
        if name == rot.DISTRESS_SHADOW_ACCOUNT and not ds_as_of:
            print(f"[replay] {name} 无新鲜名单, 停跑"); continue
        try:
            acc = PaperAccount.load(name)
        except ValueError:
            continue
        scores = (davis_scores_g2 if name == rot.G2_SHADOW_ACCOUNT
                  else davis_scores_ds if name == rot.DISTRESS_SHADOW_ACCOUNT
                  else davis_scores)
        ok = rot._rotate_one(acc, scores, prices, pcts, day, dry_run=False)
        print(f"[replay] {name}: {'完成' if ok else '未完成'}")
        acc.close()


if __name__ == "__main__":
    main()
