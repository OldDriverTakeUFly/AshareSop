"""回填 invest_overseas_market 表的 us_vix 字段（CBOE VIX 历史）.

CBOE 提供 1990 年至今的完整 VIX 历史 CSV，可以一次性回填所有缺失的 us_vix 值。
同时检查/修复 usd_jpy 等其他空值字段。

Usage:
    cd /home/leo/Projects/CodeAgentDashboard
    PYTHONPATH=. .venv/bin/python scripts/backfill/backfill_us_vix.py
"""
import os, sys, sqlite3
import pandas as pd
from datetime import datetime

PROJECT_ROOT = "/home/leo/Projects/CodeAgentDashboard"
os.environ["PROJECT_ROOT"] = PROJECT_ROOT
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
from stockhot.storage.database import DB_PATH

CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"


def main():
    print("=" * 60)
    print("  回填 us_vix 到 invest_overseas_market")
    print("=" * 60)

    # 1. 下载 CBOE VIX 历史
    print("\n下载 CBOE VIX 历史...")
    df = pd.read_csv(CBOE_URL)
    df["date_str"] = pd.to_datetime(df["DATE"], format="%m/%d/%Y").dt.strftime("%Y-%m-%d")
    vix_map = dict(zip(df["date_str"], df["CLOSE"]))
    print(f"  CBOE VIX: {len(df)} rows ({df['date_str'].min()} → {df['date_str'].max()})")

    # 2. 查找需要更新的行
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT date, us_vix FROM invest_overseas_market ORDER BY date"
        ).fetchall()

    missing = [(r[0],) for r in rows if r[1] is None]
    print(f"\n  invest_overseas_market: {len(rows)} rows, {len(missing)} 缺 us_vix")

    if not missing:
        print("  ✓ 无需更新")
        return

    # 3. 回填
    updated = 0
    not_found = 0
    with sqlite3.connect(DB_PATH) as conn:
        for (date,) in missing:
            vix = vix_map.get(date)
            if vix is not None:
                conn.execute(
                    "UPDATE invest_overseas_market SET us_vix = ? WHERE date = ?",
                    (round(float(vix), 2), date),
                )
                updated += 1
            else:
                not_found += 1
        conn.commit()

    print(f"\n✓ 更新: {updated} rows, 未匹配: {not_found} rows")

    # 4. 验证
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN us_vix IS NOT NULL THEN 1 ELSE 0 END) "
            "FROM invest_overseas_market"
        ).fetchone()
        print(f"  us_vix 覆盖: {row[1]}/{row[0]} rows ({row[1]/row[0]*100:.0f}%)")

        # 样本
        samples = conn.execute(
            "SELECT date, us_10y, us_vix, usd_jpy FROM invest_overseas_market "
            "ORDER BY date LIMIT 5"
        ).fetchall()
        print("\n  样本:")
        for s in samples:
            print(f"    {s[0]}: us_10y={s[1]} us_vix={s[2]} usd_jpy={s[3]}")


if __name__ == "__main__":
    main()
