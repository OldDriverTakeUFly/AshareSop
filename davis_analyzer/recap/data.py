# davis_analyzer/recap/data.py
"""recap 只读数据层:stockhot.db JSON blob + market_data.db 结构化表 → 当日 bundle。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from davis_analyzer.recap.constants import REPO_ROOT

_DEFAULT_STOCKHOT = REPO_ROOT / "storage" / "database" / "stockhot.db"
_DEFAULT_MARKET = REPO_ROOT / "storage" / "database" / "market_data.db"

INDEX_CODES = ("000001.SH", "399001.SZ", "399006.SZ")   # 上证/深成/创业板
INDEX_NAMES = {"000001.SH": "上证指数", "399001.SZ": "深证成指", "399006.SZ": "创业板指"}
MIN_AMPLITUDE, AMPLITUDE_TOP_N = 12.0, 30


class DailyDataMissing(RuntimeError):
    """当日采集数据不完整,拒绝选片(与 cardgen 同口径)。"""


def stockhot_db_path() -> Path:
    import os
    return Path(os.environ.get("RECAP_STOCKHOT_DB", _DEFAULT_STOCKHOT))


def market_db_path() -> Path:
    import os
    return Path(os.environ.get("RECAP_MARKET_DB", _DEFAULT_MARKET))


def _ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _norm_time(s: object) -> str:
    """'94700'/'144600' → '09:47:00'/'14:46:00';空/脏值 → ''。"""
    if not s or not str(s).strip():
        return ""
    t = str(s).strip().zfill(6)
    if not t.isdigit() or len(t) != 6:
        return ""
    return f"{t[:2]}:{t[2:4]}:{t[4:6]}"


def _dedup_pool(rows: list[dict]) -> list[dict]:
    """limit_up_pool 双写(带后缀+裸码)按 ts_code 根去重,保留带后缀行。"""
    out: dict[str, dict] = {}
    for r in rows:
        code = str(r.get("code", ""))
        root = code.split(".")[0]
        if root not in out or "." in code:
            r = dict(r, ts_code=code if "." in code else code, ts_root=root)
            out[root] = r
    return list(out.values())


def _daily_row(con: sqlite3.Connection, day: str, data_type: str) -> str | None:
    """daily_data 原始 JSON 文本;行不存在(未采集)= None,与「采集了但为空」('[]')区分。"""
    row = con.execute("SELECT data_json FROM daily_data WHERE trade_date=? AND data_type=?",
                      (day, data_type)).fetchone()
    return row[0] if row else None


def _daily_json(con: sqlite3.Connection, day: str, data_type: str) -> list[dict] | None:
    """daily_data 解析结果;未采集 = None,采集了但空池 = [](真冰点口径)。"""
    raw = _daily_row(con, day, data_type)
    return None if raw is None else json.loads(raw)


def _analysis_json(con: sqlite3.Connection, day: str, analysis_type: str) -> dict | None:
    row = con.execute("SELECT result_json FROM analysis_results WHERE trade_date=? AND analysis_type=?",
                      (day, analysis_type)).fetchone()
    return json.loads(row[0]) if row else None


def fetch_bundle(day_dash: str) -> dict:
    """一站式当日 bundle;仅「未采集」(行缺失)抛 DailyDataMissing。

    涨停池采集了但为空(真冰点)是合法 bundle:limit_up_count=0、boards=[],
    由 cli._ice_episode 走「今日无战事」降级剧本。
    """
    con = _ro(stockhot_db_path())

    try:
        lu = _analysis_json(con, day_dash, "limit_up_analysis")
        pool_raw = _daily_json(con, day_dash, "limit_up_pool")
        # 行缺失 = 盘面扫描未跑;行在而池空 = 真冰点,放行
        if lu is None or pool_raw is None:
            raise DailyDataMissing(
                f"{day_dash} 缺 limit_up_analysis/limit_up_pool(盘面扫描未完成?)")
        pool = _dedup_pool(pool_raw)
        broken = _dedup_pool(_daily_json(con, day_dash, "broken_pool") or [])
        down = _dedup_pool(_daily_json(con, day_dash, "limit_down_pool") or [])
        lhb_detail = _daily_json(con, day_dash, "dragon_tiger_detail") or []
        dt = _analysis_json(con, day_dash, "dragon_tiger") or {}
    finally:
        con.close()

    day_compact = day_dash.replace("-", "")
    mcon = _ro(market_db_path())
    try:
        index = []
        for code, close, pct in mcon.execute(
                "SELECT ts_code, close, pct_chg FROM index_daily WHERE trade_date=? "
                f"AND ts_code IN ({','.join('?' * len(INDEX_CODES))})",
                (day_compact, *INDEX_CODES)):
            index.append({"code": code, "name": INDEX_NAMES[code],
                          "close": float(close), "pct_chg": float(pct)})
        if not index:
            raise DailyDataMissing(f"{day_dash} 缺 index_daily(当日日线刷新未完成?)")
        amp = [{"ts_code": r[0], "amplitude_pct": float(r[1])} for r in mcon.execute(
            "SELECT ts_code, ROUND((high-low)/pre_close*100,2) AS amp FROM daily_price "
            "WHERE trade_date=? AND pre_close>0 AND high>0 "
            "ORDER BY amp DESC LIMIT ?", (day_compact, AMPLITUDE_TOP_N))]
        amp = [a for a in amp if a["amplitude_pct"] >= MIN_AMPLITUDE]
        up, down_n = mcon.execute(
            "SELECT SUM(pct_chg>0), SUM(pct_chg<0) FROM daily_price WHERE trade_date=?",
            (day_compact,)).fetchone()
    finally:
        mcon.close()

    names: dict[str, str] = {}
    for rowset in (pool, broken, down):
        for r in rowset:
            names[r["ts_code"]] = str(r.get("name", ""))

    return {
        "pool": [dict(r, first_seal_time=_norm_time(r.get("first_seal_time")),
                      last_seal_time=_norm_time(r.get("last_seal_time")),
                      consecutive_boards=int(r.get("consecutive_boards") or 1),
                      broken_count=int(r.get("broken_count") or 0)) for r in pool],
        "broken": broken, "down": down,
        "boards": sorted(lu.get("consecutive_boards") or [], key=lambda t: -int(t["board_count"])),
        "lhb_codes": {str(r.get("code", "")) for r in lhb_detail},
        "lhb_detail": lhb_detail,
        "brokers": dt.get("brokers") or [],
        "index": index, "amplitude_top": amp,
        "breadth": {"up": int(up or 0), "down": int(down_n or 0)},
        "names": names, "limit_up_count": len(pool),
    }
