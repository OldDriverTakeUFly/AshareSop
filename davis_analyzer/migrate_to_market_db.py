#!/usr/bin/env python
"""迁移 davis tushare_cache.db 的存量数据到统一市场库 market_data.db.

把 1.4GB 的 davis 缓存（97万日线 + 24万财务 + 20万估值 + 股票列表 + 北向 + 研报）
合并到 storage/database/market_data.db，使 market_data.db 成为唯一市场数据源。

表名映射（去掉 _cache 后缀，对齐 DAL 命名）：
    stock_basic_cache   → stock_basic
    daily_basic_cache   → daily_basic
    daily_price_cache   → daily_price   （补 high/low/pre_close/pct_chg/vol/amount 为 NULL）
    financial_cache     → financial     （payload 格式完全一致）
    hk_hold_cache       → hk_hold
    research_cache      → research

用法：
    PYTHONPATH=. .venv/bin/python davis_analyzer/migrate_to_market_db.py [--dry-run]

迁移前自动备份 market_data.db → market_data.db.bak。
迁移后逐表对比行数，确保一致。
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# 表名映射：源表（davis _cache 后缀）→ 目标表（DAL 无后缀）
# value 是 (目标表, 源列→目标列的 SELECT 表达式)。None 表示直接 SELECT *。
TABLE_MAP: dict[str, tuple[str, str | None]] = {
    "stock_basic_cache": ("stock_basic", None),
    "daily_basic_cache": ("daily_basic", None),
    # daily_price_cache 只有 ts_code/trade_date/close/adj_factor/open，DAL 还需要 high/low/...
    # 用显式 SELECT 补 NULL 给 DAL 多出的列
    "daily_price_cache": (
        "daily_price",
        "ts_code, trade_date, open, NULL AS high, NULL AS low, close, "
        "NULL AS pre_close, NULL AS pct_chg, NULL AS vol, NULL AS amount, "
        "adj_factor, fetched_at",
    ),
    "financial_cache": ("financial", None),
    "hk_hold_cache": ("hk_hold", None),
    "research_cache": ("research", None),
}


def get_db_paths() -> tuple[Path, Path]:
    """返回 (源 davis cache 路径, 目标 market_data 路径)."""
    from davis_analyzer.config import CACHE_DIR
    from stockhot.data_layer.market_db import MARKET_DB_PATH

    src = CACHE_DIR / "tushare_cache.db"
    return src, MARKET_DB_PATH


def table_row_count(conn: sqlite3.Connection, table: str) -> int:
    """安全获取表行数（表不存在返回 0）."""
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def migrate(dry_run: bool = False) -> int:
    src_path, dst_path = get_db_paths()

    if not src_path.exists():
        print(f"✗ 源库不存在: {src_path}")
        return 1

    print(f"═ davis → market_data.db 迁移 ════════════════════════════")
    print(f"源: {src_path} ({src_path.stat().st_size / 1e9:.2f} GB)")
    print(f"目标: {dst_path} ({dst_path.stat().st_size / 1e6:.1f} MB)")

    # 确保 DAL schema 已初始化（含 research 表）
    from stockhot.data_layer.market_db import init_db
    init_db()

    if dry_run:
        print("\n[DRY RUN] 将迁移以下表：")
        with sqlite3.connect(str(src_path)) as conn:
            for src_table, (dst_table, _) in TABLE_MAP.items():
                n = table_row_count(conn, src_table)
                print(f"  {src_table} → {dst_table}: {n} rows")
        return 0

    # 备份目标库
    bak_path = dst_path.with_suffix(".db.bak")
    print(f"\n备份 {dst_path} → {bak_path}")
    shutil.copy2(dst_path, bak_path)

    # 用 ATTACH 把源库挂到目标库，跨库 INSERT
    print("\n开始迁移...")
    with sqlite3.connect(str(dst_path)) as dst:
        dst.execute(f"ATTACH DATABASE '{src_path}' AS src")

        total_migrated = 0
        for src_table, (dst_table, select_expr) in TABLE_MAP.items():
            src_count = table_row_count(dst, f"src.{src_table}")
            if src_count == 0:
                print(f"  {src_table} → {dst_table}: 源表为空，跳过")
                continue

            t0 = time.time()
            cols = select_expr or "*"
            # INSERT OR IGNORE 避免与 DAL 已有数据（如第一阶段测试写入的）冲突
            dst.execute(
                f"INSERT OR IGNORE INTO {dst_table} SELECT {cols} FROM src.{src_table}"
            )
            dst.commit()

            # 验证：目标表该批次的行数
            dst_count = table_row_count(dst, dst_table)
            elapsed = time.time() - t0
            print(f"  {src_table} → {dst_table}: 源 {src_count} 行 → 目标总计 {dst_count} 行 ({elapsed:.1f}s)")
            total_migrated += src_count

        dst.execute("DETACH DATABASE src")

    print(f"\n✓ 迁移完成，共迁移 {total_migrated} 行")
    print(f"  market_data.db 现在大小: {dst_path.stat().st_size / 1e9:.2f} GB")

    # 最终验证：逐表对比
    print("\n═ 迁移后验证 ════════════════════════════════════════════")
    with sqlite3.connect(str(src_path)) as src_conn, sqlite3.connect(str(dst_path)) as dst_conn:
        all_ok = True
        for src_table, (dst_table, _) in TABLE_MAP.items():
            s = table_row_count(src_conn, src_table)
            d = table_row_count(dst_conn, dst_table)
            # 目标可能比源多（DAL 之前写入的测试数据），不能少于源
            ok = d >= s
            mark = "✓" if ok else "✗"
            print(f"  {mark} {dst_table}: 源 {s} / 目标 {d}")
            if not ok:
                all_ok = False

    if all_ok:
        print("\n✓ 所有表迁移验证通过")
        return 0
    else:
        print("\n✗ 部分表行数不一致，请检查")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate davis cache to market_data.db")
    parser.add_argument("--dry-run", action="store_true", help="只预览不执行")
    args = parser.parse_args()
    sys.exit(migrate(dry_run=args.dry_run))
