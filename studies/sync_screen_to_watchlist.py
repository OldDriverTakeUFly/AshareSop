"""同步多因子选股 top20 结果到 invest_watchlist 候选池.

读取 ``studies/output/top20_screen_<date>.json``，把选股结果写入
``invest_watchlist``（source='screen_top20'）：

- 新进榜单的股票 → INSERT（status='watching'），advisor 会自动给出建议
- 已在榜单的股票 → UPDATE composite_score/sector/updated_at（刷新排名）
- 跌出本次榜单的旧 screen_top20 候选 → status 改 'archived'（软标记，不删）

⚠️ 本脚本只更新候选池（invest_watchlist），不碰实盘持仓（invest_holdings）。
   实盘调仓仍需人工确认后通过 Web API / CLI 手动执行。

Usage:
    .venv/bin/python studies/sync_screen_to_watchlist.py [--date YYYY-MM-DD] [--dry-run]

Crontab (screen_top20 跑完后):
    20 17 * * 1-5 cd /path && PYTHONPATH=/path \\
        .venv/bin/python studies/sync_screen_to_watchlist.py \\
        >> stockhot/invest_sop/logs/screen_sync.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "studies" / "output"

SOURCE_TAG = "screen_top20"  # watchlist.source 标记，区分人工/选股候选


def _ts_code_to_code(ts_code: str) -> str:
    """603629.SH → 603629（与 invest_holdings/advisor 的 code 格式对齐）."""
    return ts_code.split(".")[0]


def load_top20(as_of: str) -> list[dict]:
    """读取指定日期的 top20 JSON，返回 top20 列表."""
    json_path = OUTPUT_DIR / f"top20_screen_{as_of}.json"
    if not json_path.exists():
        raise FileNotFoundError(f"选股结果不存在: {json_path}（请先运行 screen_top20.py）")
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("top20", [])


def sync(as_of: str, dry_run: bool = False) -> dict:
    """同步 top20 到 watchlist 候选池.

    Args:
        as_of: 选股日期 YYYY-MM-DD
        dry_run: 只打印不写库

    Returns:
        统计 dict: {inserted, updated, archived, total}
    """
    from stockhot.storage.database import get_connection

    top20 = load_top20(as_of)
    today = datetime.now().strftime("%Y-%m-%d")
    current_codes = {_ts_code_to_code(r["ts_code"]) for r in top20}

    inserted = updated = archived = 0
    conn = get_connection()
    try:
        # ── 处理本次 top20：INSERT 或 UPDATE ──
        for r in top20:
            code = _ts_code_to_code(r["ts_code"])
            name = r.get("name", "")
            sector = r.get("industry", "")
            composite = r.get("composite")
            domain = r.get("domain", "")
            trigger = f"四因子选股 top20（综合分 {composite}，{domain}）"

            existing = conn.execute(
                "SELECT id, status FROM invest_watchlist WHERE code = ?", (code,)
            ).fetchone()

            if existing is None:
                # 新候选：INSERT
                if dry_run:
                    print(f"  [DRY-RUN] INSERT {code} {name} score={composite}")
                else:
                    conn.execute(
                        """INSERT INTO invest_watchlist
                           (code, name, sector, added_date, trigger_reason,
                            stop_loss_pct, priority, status, source, composite_score, updated_at)
                           VALUES (?, ?, ?, ?, ?, -0.12, 2, 'watching', ?, ?, ?)""",
                        (code, name, sector, today, trigger,
                         SOURCE_TAG, composite, datetime.now().isoformat()),
                    )
                inserted += 1
            else:
                # 已存在：刷新评分/行业/触发原因，保持 status（除非 archived 要重新激活）
                new_status = "watching" if existing["status"] == "archived" else existing["status"]
                if dry_run:
                    print(f"  [DRY-RUN] UPDATE {code} {name} score={composite} (was {existing['status']})")
                else:
                    conn.execute(
                        """UPDATE invest_watchlist SET
                           name = ?, sector = ?, trigger_reason = ?,
                           composite_score = ?, status = ?, updated_at = ?
                           WHERE code = ?""",
                        (name, sector, trigger, composite, new_status,
                         datetime.now().isoformat(), code),
                    )
                updated += 1

        # ── 归档跌出本次 top20 的旧 screen_top20 候选 ──
        if not dry_run and current_codes:
            placeholders = ",".join("?" for _ in current_codes)
            cur = conn.execute(
                f"""UPDATE invest_watchlist SET status = 'archived', updated_at = ?
                    WHERE source = ? AND status = 'watching'
                      AND code NOT IN ({placeholders})""",
                (datetime.now().isoformat(), SOURCE_TAG, *current_codes),
            )
            archived = cur.rowcount or 0
        else:
            # dry-run 统计将被归档的
            rows = conn.execute(
                f"""SELECT code, name FROM invest_watchlist
                    WHERE source = ? AND status = 'watching'
                      AND code NOT IN ({','.join('?' for _ in current_codes)})""",
                (SOURCE_TAG, *current_codes),
            ).fetchall() if current_codes else []
            archived = len(rows)
            for row in rows:
                print(f"  [DRY-RUN] ARCHIVE {row['code']} {row['name']}（跌出本次 top20）")

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    stats = {"inserted": inserted, "updated": updated, "archived": archived, "total": len(top20)}
    return stats


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：解析 --date/--dry-run，执行同步，返回 0/1."""
    parser = argparse.ArgumentParser(description="同步选股 top20 到 watchlist 候选池")
    parser.add_argument(
        "--date", default=None,
        help="选股日期 YYYY-MM-DD（默认：今天）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只打印不写数据库",
    )
    args = parser.parse_args(argv)

    as_of = args.date or datetime.now().strftime("%Y-%m-%d")
    print(f"=== sync_screen_to_watchlist @ {datetime.now().isoformat()} | AS_OF={as_of} ===")

    try:
        stats = sync(as_of, dry_run=args.dry_run)
        mode = "[DRY-RUN] " if args.dry_run else ""
        print(
            f"{mode}同步完成: 新增 {stats['inserted']}, 更新 {stats['updated']}, "
            f"归档 {stats['archived']}, top20 共 {stats['total']} 只"
        )
        return 0
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1
    except Exception as e:
        import traceback

        print(f"[ERROR] 同步失败: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
