"""Weekly cycle assessment report generator and sector update CLI.

Usage:
    # Generate weekly cycle review report:
    PYTHONPATH=. .venv/bin/python stockhot/invest_sop/scripts/weekly_cycle.py [--date YYYY-MM-DD]

    # Update a sector's cycle assessment:
    PYTHONPATH=. .venv/bin/python stockhot/invest_sop/scripts/weekly_cycle.py --update-sector "AI" --position "复苏" --crowding 4 --notes "..."
"""

import argparse
from datetime import datetime
from pathlib import Path

from stockhot.invest_sop.config import INVEST_REPORTS_DIR
from stockhot.invest_sop.utils.db_helpers import upsert_record
from stockhot.invest_sop.utils.trading_calendar import get_recent_trade_day
from stockhot.storage.database import get_connection

SECTORS = ["AI", "半导体", "软件", "锂电", "光伏", "新能源车", "有色", "化工", "煤炭"]

CROWDING_ITEMS = [
    ("估值水平", "[0] 合理 [1] 偏高 [2] 极度高估"),
    ("资金集中度", "[0] 正常 [1] 较高 [2] 极端"),
    ("交易热度", "[0] 正常 [1] 活跃 [2] 过热"),
    ("情绪指标", "[0] 冷静 [1] 乐观 [2] 狂热"),
    ("机构持仓", "[0] 均衡 [1] 超配 [2] 极端超配"),
    ("跨界参与", "[0] 少量 [1] 增多 [2] 泛滥"),
]


def fetch_supply_chain_last_n(n_days: int = 5) -> list[dict]:
    conn = get_connection()
    try:
        recent_date = get_recent_trade_day()
        cursor = conn.execute(
            "SELECT DISTINCT date FROM invest_supply_chain "
            "WHERE date <= ? ORDER BY date DESC LIMIT ?",
            (recent_date, n_days),
        )
        dates = [row["date"] for row in cursor]
        if not dates:
            return []
        placeholders = ", ".join("?" for _ in dates)
        cursor = conn.execute(
            f"SELECT * FROM invest_supply_chain "
            f"WHERE date IN ({placeholders}) ORDER BY sector, date",
            tuple(dates),
        )
        return [dict(row) for row in cursor]
    finally:
        conn.close()


def fetch_cycle_assessments() -> list[dict]:
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT * FROM invest_cycle_assessments ORDER BY sector")
        return [dict(row) for row in cursor]
    finally:
        conn.close()


def compute_sector_trends(rows: list[dict]) -> dict[str, str]:
    from collections import defaultdict

    sector_metrics: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        sector = r.get("sector", "")
        metric = r.get("metric_name", "")
        value = r.get("value")
        if value is not None:
            sector_metrics[sector][metric].append(float(value))

    trends: dict[str, str] = {}
    for sector in SECTORS:
        metrics = sector_metrics.get(sector, {})
        up_count = 0
        down_count = 0
        for values in metrics.values():
            if len(values) >= 2:
                if values[-1] > values[0]:
                    up_count += 1
                elif values[-1] < values[0]:
                    down_count += 1
        if up_count > down_count:
            trends[sector] = "上行"
        elif down_count > up_count:
            trends[sector] = "下行"
        else:
            trends[sector] = "震荡"

    return trends


def build_crowding_scorecard(sector: str) -> str:
    lines = [
        f"#### {sector} 拥挤度打分卡",
        "",
        "``",
        f"评估赛道：{sector}",
        "",
    ]
    for item, options in CROWDING_ITEMS:
        lines.append(f"□ {item}    {options}")
    lines.append("")
    lines.append("总分：____ / 12")
    lines.append("``")
    return "\n".join(lines)


def build_cycle_speed_table(cycles: list[dict]) -> str:
    cycle_map = {c["sector"]: c for c in cycles}

    lines = [
        "## 板块周期位置速查表",
        "",
        f"| 更新日期：{datetime.now().strftime('%Y-%m-%d')} | | | |",
        "",
        "| 板块/赛道 | 周期位置 | 拥挤度 | 投资建议 |",
        "|-----------|----------|--------|----------|",
    ]

    for sector in SECTORS:
        c = cycle_map.get(sector, {})
        pos = c.get("cycle_position")
        if pos:
            pos_str = pos
        else:
            pos_str = "□复苏□繁荣□衰退"
        crowd = c.get("crowding_score")
        crowd_str = f"{crowd}/12" if crowd is not None else "__/12"
        lines.append(f"| {sector} | {pos_str} | {crowd_str} | |")

    lines.append("")
    lines.append("周期定位依据（一句话）：_________________________________")
    return "\n".join(lines)


def generate_report(date: str) -> str:
    supply_rows = fetch_supply_chain_last_n(5)
    trends = compute_sector_trends(supply_rows)
    cycles = fetch_cycle_assessments()

    parts = [
        f"# 周期评估报告 | {date}",
        "",
    ]

    parts.append("## 板块价格趋势（近5个交易日）\n")
    parts.append("| 板块 | 趋势方向 |")
    parts.append("|------|----------|")
    for sector in SECTORS:
        trend = trends.get(sector, "数据不足")
        parts.append(f"| {sector} | {trend} |")
    parts.append("")

    parts.append("---\n")
    parts.append("## 拥挤度打分卡\n")
    parts.append("（每项0-2分，满分12分）\n")
    for sector in SECTORS:
        parts.append(build_crowding_scorecard(sector))
        parts.append("")

    parts.append("---\n")
    parts.append(build_cycle_speed_table(cycles))
    parts.append("")

    parts.append("---\n")
    parts.append("## 解读\n")
    parts.append("- 0-3分  → 赛道不拥挤，可以积极参与")
    parts.append("- 4-6分  → 有拥挤迹象，需谨慎，控制仓位")
    parts.append("- 7-9分  → 明显拥挤，不宜新开仓，老仓逐步止盈")
    parts.append("- 10-12分 → 严重拥挤，考虑大幅减仓或清仓")
    parts.append("")

    return "\n".join(parts)


def update_sector(args: argparse.Namespace) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")

    data: dict = {
        "sector": args.update_sector,
        "updated_at": now,
        "assessment_date": today,
    }
    if args.position:
        data["cycle_position"] = args.position
    if args.crowding is not None:
        data["crowding_score"] = args.crowding
    if args.notes:
        data["notes"] = args.notes

    upsert_record("invest_cycle_assessments", data, unique_keys=["sector"])
    print(
        f"[OK] Updated sector={args.update_sector} "
        f"position={args.position or '-'} "
        f"crowding={args.crowding if args.crowding is not None else '-'}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly cycle assessment")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--update-sector", type=str, default=None, dest="update_sector")
    parser.add_argument("--position", type=str, default=None)
    parser.add_argument("--crowding", type=int, default=None)
    parser.add_argument("--notes", type=str, default=None)
    args = parser.parse_args()

    if args.update_sector:
        update_sector(args)
        return

    INVEST_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    content = generate_report(args.date)

    out_path: Path = INVEST_REPORTS_DIR / f"{args.date}_cycle_review.md"
    out_path.write_text(content, encoding="utf-8")
    print(f"[OK] Cycle review saved to {out_path}")


if __name__ == "__main__":
    main()
