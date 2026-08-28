# davis_analyzer/cardgen/ingest.py
"""从 market_data.db daily_basic 拉估值事实(M1: ps/pe_ttm/pb/total_mv)。

只读缓存库,取每只股票该指标最新非空行;display 规则:
ps/pe_ttm → `<值>x` 两位小数;pb → `<值>`;total_mv 万元→亿取整 `≈NNNN亿`。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from davis_analyzer.cardgen.types import Fact

REPO_ROOT = Path(__file__).resolve().parents[2]
_MARKET_DB = REPO_ROOT / "storage" / "database" / "market_data.db"

# metric → (daily_basic 列名, display 格式)
METRICS: dict[str, tuple[str, str]] = {
    "ps": ("ps", "{:.2f}x"),
    "pe_ttm": ("pe_ttm", "{:.2f}x"),
    "pb": ("pb", "{:.2f}"),
    "total_mv": ("total_mv", "≈{:.0f}亿"),
}


def fetch_daily_basic(ts_code: str, metric: str,
                      conn: sqlite3.Connection | None = None) -> Fact:
    """拉取 ts_code 指定 metric 的最新非空行,组装成 Fact(id 由调用方赋值)。"""
    if metric not in METRICS:
        raise ValueError(f"未知 metric: {metric}(可选 {sorted(METRICS)})")
    col, fmt = METRICS[metric]
    own = conn is None
    c = conn or sqlite3.connect(_MARKET_DB)
    try:
        row = c.execute(
            f"SELECT trade_date, {col} FROM daily_basic "
            f"WHERE ts_code=? AND {col} IS NOT NULL "
            "ORDER BY trade_date DESC LIMIT 1", (ts_code,)).fetchone()
        if row is None:
            raise LookupError(f"daily_basic 无 {ts_code} 的非空 {col} 行")
        trade_date, raw = row
        # total_mv 库内单位为万元 → 亿取整;其余两位小数(金额/价格铁律:Decimal)
        value = (Decimal(str(raw)) / Decimal("10000")).quantize(Decimal("1")) \
            if metric == "total_mv" else Decimal(str(raw)).quantize(Decimal("0.01"))
        as_of = datetime.strptime(str(trade_date), "%Y%m%d").strftime("%Y-%m-%d")
        return Fact(id="", value=value,
                    unit="亿" if metric == "total_mv" else ("" if metric == "pb" else "x"),
                    display=fmt.format(value), as_of=as_of, source_kind="tushare",
                    source_ref=f"daily_basic:{ts_code}@{trade_date}:{metric}")
    finally:
        if own:
            c.close()
