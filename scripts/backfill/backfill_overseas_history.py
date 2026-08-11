"""回填 2021-2026 海外宏观数据到 invest_overseas_market.

数据源:
  - 美股指数(道琼斯/标普/纳斯达克): akshare index_us_stock_sina
  - US VIX: CBOE CSV (1990-至今)
  - 美债收益率(2Y/10Y): akshare bond_zh_us_rate
  - 美元日元: akshare currency_boc_safe

填补后 international_overlay 的 4 个信号（美债/VIX/日元/美股）可在 5 年回测中生效。

Usage:
    cd /home/leo/Projects/CodeAgentDashboard
    PYTHONPATH=. .venv/bin/python scripts/backfill/backfill_overseas_history.py
"""
import os, sys, sqlite3, time
import pandas as pd
import numpy as np

PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from stockhot.storage.database import DB_PATH

CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"


def main():
    print("=" * 60)
    print("  回填 2021-2026 海外宏观数据")
    print("=" * 60)

    import akshare as ak
    # 去代理
    removed = {}
    for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
        if key in os.environ:
            removed[key] = os.environ.pop(key)

    # ── 1. 美股指数 ──
    print("\n拉取美股指数...")
    us_indices = {}
    for name, sym in [("dow", ".DJI"), ("sp500", ".INX"), ("nasdaq", ".IXIC")]:
        df = ak.index_us_stock_sina(symbol=sym)
        df["date_str"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df = df[(df["date_str"] >= "2021-01-01") & (df["date_str"] <= "2026-07-31")]
        df = df.sort_values("date_str")
        # 日涨跌幅
        df["pct"] = df["close"].pct_change() * 100
        us_indices[name] = dict(zip(df["date_str"], df["pct"]))
        print(f"  {name}: {len(df)} days")

    # ── 2. US VIX (CBOE) ──
    print("\n拉取 US VIX...")
    vix_df = pd.read_csv(CBOE_URL)
    vix_df["date_str"] = pd.to_datetime(vix_df["DATE"], format="%m/%d/%Y").dt.strftime("%Y-%m-%d")
    vix_map = dict(zip(vix_df["date_str"], vix_df["CLOSE"]))
    print(f"  VIX: {len(vix_df)} rows total")

    # ── 3. 美债收益率 ──
    print("\n拉取美债收益率...")
    bond_df = ak.bond_zh_us_rate(start_date="2021")
    bond_df["date_str"] = pd.to_datetime(bond_df["日期"]).dt.strftime("%Y-%m-%d")
    bond_df = bond_df[(bond_df["date_str"] >= "2021-01-01")]
    us10y_map = {}
    us2y_map = {}
    for _, row in bond_df.iterrows():
        d = row["date_str"]
        us10y_map[d] = float(row["美国国债收益率10年"]) if pd.notna(row["美国国债收益率10年"]) else None
        us2y_map[d] = float(row["美国国债收益率2年"]) if pd.notna(row["美国国债收益率2年"]) else None
    print(f"  美债: {len(bond_df)} rows")

    # ── 4. 美元日元 ──
    print("\n拉取美元日元...")
    fx_df = ak.currency_boc_safe()
    fx_df["date_str"] = pd.to_datetime(fx_df["日期"]).dt.strftime("%Y-%m-%d")
    fx_df = fx_df[fx_df["date_str"] >= "2021-01-01"]
    # 日元列是 100日元兑人民币，需要转换为美元兑日元
    # USD/JPY ≈ (USD/CNY) / (JPY/CNY) × 100
    # 但这里日元列是"100日元兑人民币"，即 JPY100/CNY
    # USD/JPY = (USD/CNY) / (JPY100/CNY / 100) = USD * 100 / JPY100
    usd_jpy_map = {}
    for _, row in fx_df.iterrows():
        d = row["date_str"]
        usd = float(row["美元"]) if pd.notna(row["美元"]) else None
        jpy100 = float(row["日元"]) if pd.notna(row["日元"]) else None
        if usd and jpy100 and jpy100 > 0:
            usd_jpy_map[d] = round(usd * 100 / jpy100, 2)
    print(f"  USD/JPY: {len(usd_jpy_map)} days")

    # 恢复代理
    os.environ.update(removed)

    # ── 5. 写入 DB ──
    print("\n写入 invest_overseas_market...")

    # 获取所有交易日（从 daily_price）
    from stockhot.data_layer.market_db import get_connection
    with get_connection() as c:
        rows = c.execute(
            "SELECT DISTINCT trade_date FROM daily_price "
            "WHERE trade_date >= '20210101' AND trade_date <= '20260731' AND vol > 0 "
            "ORDER BY trade_date"
        ).fetchall()
    trade_dates = [r[0] for r in rows]
    print(f"  交易日: {len(trade_dates)} days")

    # 构建记录
    records = []
    for td in trade_dates:
        dash = f"{td[:4]}-{td[4:6]}-{td[6:8]}"

        dow_pct = us_indices.get("dow", {}).get(dash)
        sp500_pct = us_indices.get("sp500", {}).get(dash)
        nasdaq_pct = us_indices.get("nasdaq", {}).get(dash)

        us_vix = vix_map.get(dash)
        us_10y = us10y_map.get(dash)
        us_2y = us2y_map.get(dash)
        usd_jpy = usd_jpy_map.get(dash)

        # 美债日变化(bp)
        us_10y_change_bp = None
        if us_10y is not None:
            prev_td = None
            idx = trade_dates.index(td)
            if idx > 0:
                prev_dash = f"{trade_dates[idx-1][:4]}-{trade_dates[idx-1][4:6]}-{trade_dates[idx-1][6:8]}"
                prev_10y = us10y_map.get(prev_dash)
                if prev_10y is not None:
                    us_10y_change_bp = round((us_10y - prev_10y) * 100, 1)

        records.append((
            dash, sp500_pct, nasdaq_pct, dow_pct,
            us_10y, us_10y_change_bp, us_vix,
            usd_jpy, us_2y,
        ))

    # 写入（INSERT OR REPLACE，不覆盖已有的非空值）
    with sqlite3.connect(DB_PATH) as conn:
        # 先确保表存在
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invest_overseas_market (
                date TEXT PRIMARY KEY,
                sp500_pct REAL, nasdaq_pct REAL, dow_pct REAL,
                us_10y REAL, us_10y_change_bp REAL,
                vix REAL, a50_pct REAL, usd_cny REAL, created_at TEXT,
                us_vix REAL, usd_jpy REAL, us_2y REAL, us_vix_change REAL
            )
        """)

        inserted = 0
        updated = 0
        for rec in records:
            date_str = rec[0]
            existing = conn.execute(
                "SELECT * FROM invest_overseas_market WHERE date=?", (date_str,)
            ).fetchone()

            if existing is None:
                # 新插入
                conn.execute("""
                    INSERT INTO invest_overseas_market
                    (date, sp500_pct, nasdaq_pct, dow_pct, us_10y, us_10y_change_bp,
                     us_vix, usd_jpy, us_2y)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, rec)
                inserted += 1
            else:
                # 更新空值字段
                updates = {}
                cols_map = {
                    1: ("sp500_pct", rec[1]), 2: ("nasdaq_pct", rec[2]),
                    3: ("dow_pct", rec[3]), 4: ("us_10y", rec[4]),
                    5: ("us_10y_change_bp", rec[5]), 6: ("us_vix", rec[6]),
                    7: ("usd_jpy", rec[7]), 8: ("us_2y", rec[8]),
                }
                for idx, (col, val) in cols_map.items():
                    if val is not None and (existing[idx] is None):
                        updates[col] = val
                if updates:
                    set_clause = ", ".join(f"{k}=?" for k in updates)
                    values = list(updates.values()) + [date_str]
                    conn.execute(f"UPDATE invest_overseas_market SET {set_clause} WHERE date=?", values)
                    updated += 1

        conn.commit()

    print(f"\n✓ 新增: {inserted}, 更新: {updated}")

    # 验证
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN us_vix IS NOT NULL THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN us_10y IS NOT NULL THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN usd_jpy IS NOT NULL THEN 1 ELSE 0 END), "
            "MIN(date), MAX(date) "
            "FROM invest_overseas_market"
        ).fetchone()
        print(f"\n=== 覆盖验证 ===")
        print(f"  总行数: {row[0]}")
        print(f"  us_vix: {row[1]} ({row[1]/row[0]*100:.0f}%)")
        print(f"  us_10y: {row[2]} ({row[2]/row[0]*100:.0f}%)")
        print(f"  usd_jpy: {row[3]} ({row[3]/row[0]*100:.0f}%)")
        print(f"  日期范围: {row[4]} → {row[5]}")


if __name__ == "__main__":
    main()
