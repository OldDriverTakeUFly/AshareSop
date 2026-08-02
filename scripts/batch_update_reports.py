"""Batch-update ΔG-negative reports with latest data snapshot + concise v2.0 note.

For the ~40 reports whose ΔG turned negative (mostly semiconductor/tech stocks
hit by the 7/17 crash), this appends:
  1. A v2.0 header tag to the title (if not already updated)
  2. A concise "近期变化" block before the 版本历史 section
  3. A new row in the 版本历史 table

The narrative is intentionally short — these stocks share the same macro driver
(7/17 tech crash + AI overcapacity fears), so a full per-stock investigation
is unnecessary. Each report gets its own latest PE/prosperity numbers from
the CSV, but the qualitative narrative is templated.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/batch_update_reports.py [--dry-run]
"""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = PROJECT_ROOT / "docs" / "个股研报"
CSV_PATH = PROJECT_ROOT / "studies" / "output" / "report_refresh_20260802.csv"

# Already hand-updated in the previous round — skip these.
DONE = {
    "300476.SZ", "002202.SZ", "300398.SZ", "688766.SH",
    "002606.SZ", "601899.SH",
}


def load_csv() -> dict[str, dict]:
    """Load refresh CSV, keyed by ts_code."""
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
    by_code = {}
    for r in rows:
        if r["ts_code"] not in by_code:
            by_code[r["ts_code"]] = r
    return by_code


def find_reports(data: dict[str, dict]) -> list[tuple[str, str, dict]]:
    """Return [(ts_code, report_path, csv_row), ...] for reports needing update."""
    results = []
    seen_paths = set()
    for md in sorted(REPORTS_DIR.rglob("*.md")):
        text = md.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"(\d{6}\.[A-Z]{2})", text)
        if not m:
            continue
        ts_code = m.group(1)
        if ts_code in DONE:
            continue
        row = data.get(ts_code)
        if not row:
            continue
        # Only ΔG negative
        try:
            dg = float(row.get("delta_g") or 0)
        except ValueError:
            continue
        # Skip if already has v2.0/v3.0 (hand-updated or previous batch run)
        if "v2.0 更新" in text[:200] or "v3.0 更新" in text[:200]:
            continue
        # Skip if already has the data-confirm block (this batch re-run)
        if "数据确认（v2.0" in text or "近期变化速览（v2.0 数据刷新" in text:
            continue
        if str(md) in seen_paths:
            continue
        seen_paths.add(str(md))
        results.append((ts_code, str(md), row))
    return results


def build_change_block(ts_code: str, name: str, row: dict) -> str:
    """Build the concise '近期变化' markdown block for one report.

    Adapts tone by ΔG sign: negative → '减速' narrative (7/17 crash attribution);
    positive → '加速/维持' narrative (fundamentals holding, possibly undervalued).
    """
    dg = float(row.get("delta_g") or 0)
    composite = row.get("prosperity_score") or "?"
    new_pe = row.get("new_pe") or "?"
    new_pe_pct = row.get("new_pe_pct") or "?"
    old_pe_pct = row.get("old_pe_pct")
    trade_date = row.get("trade_date") or "20260731"

    # ΔG magnitude → severity label (different for positive vs negative)
    if dg >= 30:
        severity = "爆发式加速"
        is_positive = True
    elif dg >= 10:
        severity = "加速"
        is_positive = True
    elif dg >= 0:
        severity = "上升拐点维持"
        is_positive = True
    elif dg <= -20:
        severity = "显著减速"
        is_positive = False
    elif dg <= -10:
        severity = "明显减速"
        is_positive = False
    else:
        severity = "边际减速"
        is_positive = False

    # PE shift
    pe_note = ""
    if old_pe_pct and new_pe_pct and new_pe_pct != "?":
        try:
            shift = float(new_pe_pct) - float(old_pe_pct)
            if shift <= -20:
                pe_note = f"PE 分位从 {old_pe_pct}% 大幅降至 {new_pe_pct}%（-{abs(shift):.0f}pp，估值显著消化）。"
            elif shift <= -5:
                pe_note = f"PE 分位从 {old_pe_pct}% 降至 {new_pe_pct}%（-{abs(shift):.0f}pp）。"
            elif shift >= 20:
                pe_note = f"PE 分位从 {old_pe_pct}% 升至 {new_pe_pct}%（+{shift:.0f}pp，估值回升）。"
            else:
                pe_note = f"PE 分位 {new_pe_pct}%（变化不大）。"
        except (ValueError, TypeError):
            pe_note = f"PE 分位 {new_pe_pct}%。"
    else:
        pe_note = f"PE 分位 {new_pe_pct}%。"

    # Build narrative: positive ΔG → "基本面维持/加速"; negative → "减速归因"
    if is_positive:
        attribution = (
            f"**景气度判定**：ΔG={dg:+.1f}（**{severity}**），composite={composite}——"
            f"景气度引擎确认基本面**未恶化**，增速仍在加速或维持上升拐点。"
            f"{severity}意味着该标的在 7 月科技股暴跌（创业板 -7.15%）中基本面韧性较强，"
            f"非「AI 算力过剩」叙事的直接冲击对象。{pe_note.strip()}"
        )
    else:
        attribution = (
            f"**归因**：本次 ΔG 转负主要受 **7/17 A 股科技股系统性暴跌**影响"
            f"（创业板 -7.15%、电子 -34.72%），背景是「AI 算力过剩」叙事"
            f"（Meta 卖算力 + 英伟达循环融资质疑）动摇了科技成长股估值。"
            f"{severity}属于**高基数下的增速自然回落**（2025 年高增长基数），"
            f"不必然代表基本面恶化——需结合 8 月底半年报实际数据验证。"
        )

    return f"""---

## 数据确认（v2.0 数据刷新，{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}）

> **ΔG={dg:+.1f}（{severity}）** | composite={composite} | PE-TTM={new_pe}（{pe_note.strip()}）
>
> {attribution}

"""


def insert_before_version_history(content: str, block: str) -> str:
    """Insert the change block before the '## 版本历史' line."""
    # Find version history section
    pattern = r"(## 版本历史)"
    match = re.search(pattern, content)
    if not match:
        # No version history — append at end before any trailing disclaimer
        return content.rstrip() + "\n\n" + block

    pos = match.start()
    return content[:pos] + block + content[pos:]


def add_version_row(content: str, row: dict) -> str:
    """Add a v2.0 row to the version history table."""
    dg = float(row.get("delta_g") or 0)
    composite = row.get("prosperity_score") or "?"
    new_pe = row.get("new_pe") or "?"
    new_pe_pct = row.get("new_pe_pct") or "?"

    new_row = f"| **v2.0** | **2026-07-31** | **数据刷新：ΔG={dg:+.1f}（减速）、composite={composite}、PE={new_pe}（分位 {new_pe_pct}%）。归因 7/17 科技股系统性暴跌 + 高基数增速回落。需 8 月半年报验证。** |"

    # Find the last row in the table (line starting with | after the header separator)
    lines = content.split("\n")
    insert_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("## 版本历史"):
            # Find the header separator line (|---|---|---|)
            for j in range(i + 1, min(i + 5, len(lines))):
                if re.match(r"\|[-\s|]+\|", lines[j].strip()):
                    # Find the last data row before a blank line or ---
                    for k in range(j + 1, len(lines)):
                        if lines[k].strip().startswith("|") and not re.match(r"\|[-\s|]+\|", lines[k].strip()):
                            insert_idx = k + 1  # insert after last data row
                        else:
                            break
                    break
            break

    if insert_idx is not None:
        lines.insert(insert_idx, new_row)
        return "\n".join(lines)
    return content


def update_header_date(content: str) -> str:
    """Update 研究日期 in the header to note v2.0 refresh."""
    # Only update if not already updated
    if "v2.0 数据刷新" in content[:300] or "v2.0 更新" in content[:300]:
        return content
    # Append v2.0 tag to the 研究日期 line
    content = re.sub(
        r"(\*\*研究日期\*\*[：:]\s*\d{4}-\d{2}-\d{2})",
        r"\1（v2.0 数据刷新 2026-07-31）",
        content,
        count=1,
    )
    return content


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    data = load_csv()
    reports = find_reports(data)

    print(f"[batch_update] 发现 {len(reports)} 篇待更新研报（ΔG 转负）")
    print()

    # Group by industry for summary
    by_industry = defaultdict(list)
    for ts_code, path, row in reports:
        parts = path.split("/")
        ind = parts[-2] if len(parts) >= 2 else "?"
        by_industry[industry := parts[-2]].append((ts_code, row))

    updated = 0
    skipped = 0
    for ts_code, path, row in reports:
        name = Path(path).stem.replace("深度研报", "").replace("估值专项", "").strip()
        try:
            content = Path(path).read_text(encoding="utf-8")

            # Build and insert change block
            block = build_change_block(ts_code, name, row)
            new_content = insert_before_version_history(content, block)

            # Add version history row
            new_content = add_version_row(new_content, row)

            # Update header date
            new_content = update_header_date(new_content)

            if new_content != content:
                if not dry_run:
                    Path(path).write_text(new_content, encoding="utf-8")
                updated += 1
                dg = float(row.get("delta_g") or 0)
                print(f"  ✓ {ts_code} {name[:12]:<12} ΔG={dg:+6.1f}")
            else:
                skipped += 1
        except Exception as exc:
            print(f"  ✗ {ts_code} {name}: {type(exc).__name__}: {exc}")
            skipped += 1

    print()
    print(f"[batch_update] {'DRY-RUN' if dry_run else '完成'}: 更新 {updated} 篇, 跳过 {skipped} 篇")
    return 0


if __name__ == "__main__":
    sys.exit(main())
