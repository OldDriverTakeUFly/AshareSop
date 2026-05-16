"""Generate morning trading directive from pre-market report + morning data.

Usage:
    PYTHONPATH=. .venv/bin/python stockhot/invest_sop/scripts/generate_directive.py [--date YYYY-MM-DD]
"""

import argparse
from datetime import datetime
from pathlib import Path

from stockhot.invest_sop.config import INVEST_REPORTS_DIR
from stockhot.invest_sop.utils.db_helpers import query_by_date
from stockhot.storage.database import get_connection

NA = "数据不可用"


def _val(data: dict | None, key: str, fmt: str = "{}") -> str:
    if data is None:
        return NA
    v = data.get(key)
    if v is None:
        return NA
    return fmt.format(v)


def _pct(data: dict | None, key: str) -> str:
    if data is None:
        return NA
    v = data.get(key)
    if v is None:
        return NA
    return f"{v:+.2f}%"


def fetch_overseas(date: str) -> dict | None:
    rows = query_by_date("invest_overseas_market", date, date_column="date")
    return rows[0] if rows else None


def fetch_morning(date: str) -> dict | None:
    rows = query_by_date("invest_morning_data", date, date_column="date")
    return rows[0] if rows else None


def fetch_active_holdings() -> list[dict]:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM invest_holdings WHERE status='active' ORDER BY id"
        )
        return [dict(row) for row in cursor]
    finally:
        conn.close()


def read_premarket_report(date: str) -> str | None:
    path = INVEST_REPORTS_DIR / f"{date}_pre_market.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def build_morning_comparison(overseas: dict | None, morning: dict | None) -> str:
    lines = [
        "## 早盘数据对比\n",
        "| 指标 | 昨夜数据 | 早上数据 | 变化 |",
        "|------|----------|----------|------|",
    ]

    a50_night = _pct(overseas, "a50_pct")
    a50_morning = _pct(morning, "a50_morning_pct")
    lines.append(f"| A50 | {a50_night} | {a50_morning} | |")

    usd_cny_night = _val(overseas, "usd_cny")
    usd_cny_morning = _val(morning, "usd_cny_morning")
    lines.append(f"| USD/CNY | {usd_cny_night} | {usd_cny_morning} | |")

    nikkei = _pct(morning, "nikkei_pct")
    kospi = _pct(morning, "kospi_pct")
    lines.append(f"| 日经225 | - | {nikkei} | |")
    lines.append(f"| KOSPI | - | {kospi} | |")

    lines.append("")
    return "\n".join(lines)


def build_directive_table(holdings: list[dict]) -> str:
    lines = [
        "## 操作指令表\n",
        "| 标的 | 昨夜预案 | 早上修正 | 最终操作 | 价格触发条件 |",
        "|------|----------|----------|----------|-------------|",
    ]

    if not holdings:
        lines.append("| （无活跃持仓） | | | | |")
    else:
        for h in holdings:
            name = h.get("name", NA)
            code = h.get("code", NA)
            sl_hard = h.get("stop_loss_hard", "-")
            target = h.get("target_price", "-")
            trigger = f"支撑{sl_hard} / 压力{target}"
            lines.append(f"| {name}({code}) | | | | {trigger} |")

    lines.append("")
    return "\n".join(lines)


def generate_directive(date: str) -> str:
    weekday_map = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}
    dt = datetime.strptime(date, "%Y-%m-%d")
    weekday = weekday_map[dt.weekday()]

    overseas = fetch_overseas(date)
    morning = fetch_morning(date)
    holdings = fetch_active_holdings()

    parts = [
        f"# 交易指令 | {date} 星期{weekday}",
        "",
    ]

    pre_report = read_premarket_report(date)
    if pre_report:
        parts.append("> 基于盘前报告生成，请结合早上数据修正决策")
    else:
        parts.append(f"> 未找到盘前报告 {date}_pre_market.md，仅使用数据库数据")

    parts.append("")

    parts.append(build_morning_comparison(overseas, morning))
    parts.append(build_directive_table(holdings))

    if morning and morning.get("notes"):
        parts.append("## 早盘备注\n")
        parts.append(morning["notes"])
        parts.append("")

    parts.append("---\n")
    parts.append("## 待确认事项\n")
    parts.append("- [ ] 早上数据已复核")
    parts.append("- [ ] 操作指令已确认")
    parts.append("- [ ] 止损价已检查")
    parts.append("")

    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate morning trading directive")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()

    INVEST_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    content = generate_directive(args.date)

    out_path: Path = INVEST_REPORTS_DIR / f"{args.date}_directive.md"
    out_path.write_text(content, encoding="utf-8")
    print(f"[OK] Directive saved to {out_path}")


if __name__ == "__main__":
    main()
