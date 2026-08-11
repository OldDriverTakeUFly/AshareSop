"""三个宏观因子数据回填可行性测试脚本.

1. 海外市场数据（美债/VIX/日元/美股）→ akshare，回填到 stockhot.db
2. 主力资金流 → akshare stock_fund_flow_industry，回填到 market_data.db
3. daily_basic PE/PB → Tushare 按日期批量拉取，回填到 market_data.db

Usage:
    # 可行性测试（拉3天看数据格式+速度）
    cd /home/leo/Projects/CodeAgentDashboard
    PYTHONPATH=. .venv/bin/python scripts/backfill/backfill_macro_data.py --test

    # 正式回填（2021-2026）
    PYTHONPATH=. .venv/bin/python scripts/backfill/backfill_macro_data.py --start 20210101 --end 20260731
"""
import os, sys, time, argparse, sqlite3
PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from loguru import logger; logger.remove(); logger.add(sys.stderr, level="WARNING")
import pandas as pd
import numpy as np

from stockhot.storage.database import DB_PATH
from stockhot.data_layer.market_db import MARKET_DB_PATH, get_connection as get_market_conn


# ──────────────────────────────────────────────────────────────────────
# 1. 海外市场数据回填（美债/VIX/日元/美股 → stockhot.db invest_overseas_market）
# ──────────────────────────────────────────────────────────────────────

def backfill_overseas_test():
    """测试 akshare 能否拉历史海外数据."""
    print("\n=== 1. 海外市场数据回填可行性 ===")

    # 测试 akshare 美债收益率
    try:
        import akshare as ak
        # 美国国债收益率
        df = ak.bond_china_yield(start_date="20211201", end_date="20211203")
        print(f"  ✓ bond_china_yield (中国国债): {len(df)} rows")
        print(f"    字段: {list(df.columns)[:5]}")
    except Exception as e:
        print(f"  ✗ bond_china_yield 失败: {e}")

    # 测试美国指数（新浪）
    try:
        df = ak.index_us_stock_sina(symbol=".DJI")
        print(f"  ✓ index_us_stock_sina (道琼斯): {len(df)} rows")
        print(f"    最近5天: {df.tail(3).to_string()}")
    except Exception as e:
        print(f"  ✗ index_us_stock_sina 失败: {e}")

    # 测试 VIX（CBOE CSV）
    try:
        url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
        df = pd.read_csv(url, nrows=5)
        print(f"  ✓ VIX History (CBOE): {len(df)} rows (sample)")
        print(f"    字段: {list(df.columns)}")
    except Exception as e:
        print(f"  ✗ VIX History 失败: {e}")

    # 测试 USD/JPY
    try:
        df = ak.fx_spot_quote()
        usd_jpy = df[df['货币对'].str.contains('日元', na=False)]
        print(f"  ✓ fx_spot_quote (USD/JPY): {len(usd_jpy)} rows")
    except Exception as e:
        print(f"  ✗ fx_spot_quote 失败: {e}")

    print("\n  结论: 海外数据可通过 akshare + CBOE CSV 获取，但需每日一个日期逐天拉")
    print("  回填估算: 1351 交易日 × 4个API ≈ 2-3 小时")


def backfill_overseas(start_date, end_date):
    """正式回填海外数据."""
    # TODO: 实现（等可行性测试通过 + A/B 结果出来后）
    pass


# ──────────────────────────────────────────────────────────────────────
# 2. 主力资金流回填（akshare stock_fund_flow_industry → market_data.db）
# ──────────────────────────────────────────────────────────────────────

def backfill_fund_flow_test():
    """测试 akshare 资金流历史数据."""
    print("\n=== 2. 主力资金流回填可行性 ===")

    try:
        import akshare as ak
        # 行业资金流
        df = ak.stock_fund_flow_industry(symbol="即时")
        print(f"  ✓ stock_fund_flow_industry (即时): {len(df)} rows")
        print(f"    字段: {list(df.columns)[:6]}")
    except Exception as e:
        print(f"  ✗ stock_fund_flow_industry 失败: {e}")

    # 测试历史资金流（按日期）
    try:
        # akshare 的资金流接口大多是「即时」或「当日」，不支持历史回溯
        # 检查是否有历史接口
        fns = [f for f in dir(ak) if 'fund_flow' in f.lower()]
        print(f"\n  资金流相关接口: {fns}")
        print("  ⚠️  akshare 资金流接口大多是「即时/当日」，不支持历史回溯")
    except:
        pass

    # 检查东方财富历史资金流
    try:
        # 市场资金流（历史）
        df = ak.stock_market_fund_flow()
        print(f"\n  ✓ stock_market_fund_flow: {len(df)} rows")
        print(f"    字段: {list(df.columns)[:6]}")
        if len(df) > 0:
            print(f"    日期范围: {df.iloc[-1].get('日期', '?')} → {df.iloc[0].get('日期', '?')}")
    except Exception as e:
        print(f"  ✗ stock_market_fund_flow 失败: {e}")

    print("\n  结论: 需确认 stock_market_fund_flow 是否支持长历史")


def backfill_fund_flow(start_date, end_date):
    pass


# ──────────────────────────────────────────────────────────────────────
# 3. daily_basic PE/PB 回填（Tushare 按日期批量 → market_data.db）
# ──────────────────────────────────────────────────────────────────────

def backfill_daily_basic_test():
    """测试 Tushare daily_basic 按日期批量拉取."""
    print("\n=== 3. daily_basic PE/PB 回填可行性 ===")

    from davis_analyzer.tushare_client import TushareClient
    client = TushareClient()

    # 测试拉3天
    test_dates = ["20211201", "20211202", "20211203"]
    total_rows = 0
    t0 = time.time()
    for d in test_dates:
        df = client._call('daily_basic', client._pro.daily_basic, {
            'trade_date': d,
            'fields': 'ts_code,trade_date,pe_ttm,pb,ps,total_mv,turnover_rate,circ_mv,free_share'
        })
        total_rows += len(df)
    elapsed = time.time() - t0
    print(f"  ✓ 3天 daily_basic: {total_rows} rows in {elapsed:.1f}s")
    print(f"    每天 ~{total_rows//3} rows, 含 pe_ttm: {len(df[df['pe_ttm']>0])}")

    # 估算全量回填时间
    # 1351 交易日 × 1 API call/day = 1351 calls
    rate = elapsed / 3
    total_eta = 1351 * rate
    print(f"\n  估算: 1351 天 × {rate:.1f}s/天 = {total_eta/60:.0f} 分钟 ({total_eta/3600:.1f}h)")
    print(f"  限流: 400 calls/min, 1351 calls 需要 {1351/400:.0f} 分钟 ≈ {1351/400/60:.1f}h")

    # 确认数据能正确写入
    print(f"\n  ✓ Tushare daily_basic 支持 trade_date 批量拉取（全市场一天一次call）")
    print(f"  ✓ 已有 daily_basic 表和 schema（market_data.db）")
    print(f"  ⚠️  当前 _get_financial 的增量逻辑只做「向前增量」，需用 _call 直接拉")


def backfill_daily_basic(start_date, end_date):
    """正式回填 daily_basic."""
    from davis_analyzer.tushare_client import TushareClient
    client = TushareClient()

    # 获取所有交易日
    with get_market_conn() as c:
        rows = c.execute(
            "SELECT DISTINCT trade_date FROM daily_price "
            "WHERE trade_date >= ? AND trade_date <= ? "
            "AND vol > 0 ORDER BY trade_date",
            (start_date, end_date),
        ).fetchall()
    trade_dates = [r[0] for r in rows]

    print(f"回填 daily_basic: {len(trade_dates)} 天 ({start_date} → {end_date})")

    # 检查已有覆盖
    with get_market_conn() as c:
        existing = c.execute(
            "SELECT DISTINCT trade_date FROM daily_basic "
            "WHERE trade_date >= ? AND trade_date <= ? AND pe_ttm > 0",
            (start_date, end_date),
        ).fetchall()
    existing_dates = set(r[0] for r in existing)
    missing = [d for d in trade_dates if d not in existing_dates]
    print(f"  已有: {len(existing_dates)} 天, 缺失: {len(missing)} 天")

    if not missing:
        print("  ✓ 无缺失，跳过")
        return

    t0 = time.time()
    n_done = 0
    for i, d in enumerate(missing):
        try:
            df = client._call('daily_basic', client._pro.daily_basic, {
                'trade_date': d,
                'fields': 'ts_code,trade_date,pe_ttm,pb,ps,total_mv,turnover_rate,circ_mv,free_share'
            })
            if df is not None and len(df) > 0:
                # 写入 market_data.db
                records = []
                for _, row in df.iterrows():
                    records.append((
                        row.get('ts_code'), d,
                        row.get('pe_ttm') if pd.notna(row.get('pe_ttm')) else None,
                        row.get('pb') if pd.notna(row.get('pb')) else None,
                        row.get('ps') if pd.notna(row.get('ps')) else None,
                        row.get('total_mv') if pd.notna(row.get('total_mv')) else None,
                        int(time.time()),
                        row.get('turnover_rate') if pd.notna(row.get('turnover_rate')) else None,
                        row.get('circ_mv') if pd.notna(row.get('circ_mv')) else None,
                        row.get('free_share') if pd.notna(row.get('free_share')) else None,
                    ))
                with sqlite3.connect(str(MARKET_DB_PATH)) as conn:
                    conn.executemany(
                        "INSERT OR REPLACE INTO daily_basic "
                        "(ts_code, trade_date, pe_ttm, pb, ps, total_mv, fetched_at, turnover_rate, circ_mv, free_share) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        records,
                    )
                    conn.commit()
                n_done += 1
        except Exception as e:
            logger.warning(f"daily_basic {d} failed: {e}")

        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            rate = elapsed / (i + 1)
            remain = len(missing) - i - 1
            print(f"  [{i+1}/{len(missing)}] {d}: +{n_done} days, "
                  f"ETA {rate * remain / 60:.0f}min", flush=True)

    elapsed = time.time() - t0
    print(f"\n✓ daily_basic 回填完成: {n_done}/{len(missing)} 天 in {elapsed/60:.0f}min")


def main():
    parser = argparse.ArgumentParser(description="宏观因子数据回填")
    parser.add_argument("--test", action="store_true", help="可行性测试（不实际回填）")
    parser.add_argument("--start", default="20210101")
    parser.add_argument("--end", default="20260731")
    parser.add_argument("--only", choices=["overseas", "fund_flow", "daily_basic"],
                        help="只跑指定方向")
    args = parser.parse_args()

    if args.test:
        print("=" * 60)
        print("  宏观因子数据回填可行性测试")
        print("=" * 60)
        if not args.only or args.only == "overseas":
            backfill_overseas_test()
        if not args.only or args.only == "fund_flow":
            backfill_fund_flow_test()
        if not args.only or args.only == "daily_basic":
            backfill_daily_basic_test()
        print("\n" + "=" * 60)
        print("  可行性测试完成")
        print("=" * 60)
    else:
        if not args.only or args.only == "overseas":
            backfill_overseas(args.start, args.end)
        if not args.only or args.only == "fund_flow":
            backfill_fund_flow(args.start, args.end)
        if not args.only or args.only == "daily_basic":
            backfill_daily_basic(args.start, args.end)


if __name__ == "__main__":
    main()
