"""巨潮公告拉取(原始层全量) + 冻结规则匹配(规则层可重放). 网页API,单点防御(spec §4.1/§7).

数据源例外授权: 2026-09-18 用户批准接入巨潮(只读白名单域 cninfo.com.cn),
与 intraday/baostock 模式同构——落本地表后分析只读本地。
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime, timedelta

import requests
from loguru import logger

from davis_analyzer.constants import MAJOR_EVENT_RULES
from davis_analyzer.surge import db

_BASE = "https://www.cninfo.com.cn"  # 2026-09-18 安全审查:明文HTTP改HTTPS
_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
_COMPILED = [(r["event_type"], r["direction"], re.compile(r["pattern"]))
             for r in MAJOR_EVENT_RULES]
_RATE_LIMIT_S = 0.2
_MAX_PAGES = 10  # 防御死循环: 单股上限 300 条


# ── 规则层 ──

def apply_rules(title: str) -> list[tuple[str, str]]:
    """按 MAJOR_EVENT_RULES 顺序匹配(列表序即优先级).

    互斥: 命中 ma_halt(终止类)后跳过 ma——「终止发行股份购买资产」语义为
    重组结束,不再同时标「并购重组进行中」;其余类型可共存。
    """
    clean = re.sub(r"</?em>", "", title or "")
    hits: list[tuple[str, str]] = []
    halted = False
    for etype, direction, pat in _COMPILED:
        if etype == "ma" and halted:
            continue
        if pat.search(clean):
            hits.append((etype, direction))
            if etype == "ma_halt":
                halted = True
    return hits


# ── 原始层拉取 ──

def fetch_org_id(session: requests.Session, code: str) -> str | None:
    try:
        r = session.post(f"{_BASE}/new/information/topSearch/query",
                         data={"keyWord": code, "maxNum": "10"},
                         headers=_HEADERS, timeout=10,
                         allow_redirects=False)
        rows = r.json()
        return rows[0]["orgId"] if rows else None
    except Exception as e:  # 网页API防御:单点失败不阻塞整批
        logger.warning("cninfo orgId {} 失败: {}", code, e)
        return None


def fetch_announcements(
    session: requests.Session, code: str, org_id: str,
    start_dash: str, end_dash: str,
) -> list[dict] | None:
    """按股分页拉近 window 日全量公告标题;整股失败返回 None(调用方降级)."""
    column = "bj" if code.startswith(("4", "8", "92")) else "szse"
    out: list[dict] = []
    for page in range(1, _MAX_PAGES + 1):
        try:
            r = session.post(
                f"{_BASE}/new/hisAnnouncement/query",
                data={"pageNum": str(page), "pageSize": "30", "column": column,
                      "tabName": "fulltext", "stock": f"{code},{org_id}",
                      "searchkey": "", "seDate": f"{start_dash}~{end_dash}",
                      "isHLtitle": "true"},
                headers=_HEADERS, timeout=15,
                allow_redirects=False)
            d = r.json()
            assert isinstance(d, dict) and "announcements" in d, "响应结构变更"
        except Exception as e:
            logger.warning("cninfo ann {} p{} 失败: {}", code, page, e)
            return None if page == 1 else out  # 首页失败=整股失败
        anns = d.get("announcements") or []
        for a in anns:
            try:
                ts = a.get("announcementTime")
                if not ts:
                    continue
                out.append({
                    "ann_date": datetime.fromtimestamp(ts / 1000).strftime("%Y%m%d"),
                    "title": re.sub(r"</?em>", "", a.get("announcementTitle") or ""),
                })
            except (TypeError, ValueError, OSError) as e:
                # 单条畸形记录跳过(安全审查A2:不放大为整股/整批降级)
                logger.warning("cninfo ann {} 单条解析失败: {!r}", code, e)
        if len(anns) < 30:
            break
        time.sleep(_RATE_LIMIT_S)
    return out


# ── 同步与重放 ──

def _cached_org_id(conn: sqlite3.Connection, session, ts_code: str) -> str | None:
    row = conn.execute(
        "SELECT org_id FROM cninfo_org_map WHERE ts_code=?", (ts_code,)).fetchone()
    if row and row[0]:
        return row[0]
    code = db.strip_code_suffix(ts_code)
    org = fetch_org_id(session, code)
    if org:
        conn.execute("INSERT OR REPLACE INTO cninfo_org_map VALUES (?,?,?)",
                     (ts_code, org, time.time()))
        conn.commit()
    return org


def sync_cninfo(
    conn: sqlite3.Connection, ts_codes: list[str], day: str,
    window_days: int = 180, session: requests.Session | None = None,
) -> dict[str, int]:
    """命中池逐股拉近公告: 全量入原始层,规则命中入规则层(spec §4.1)."""
    session = session or requests.Session()
    end = datetime.strptime(db.normalize_date(day), "%Y%m%d")
    start_dash = (end - timedelta(days=window_days)).strftime("%Y-%m-%d")
    end_dash = end.strftime("%Y-%m-%d")
    stats = {"ok": 0, "fail": 0, "events": 0}
    now = time.time()
    for ts_code in ts_codes:
        org = _cached_org_id(conn, session, ts_code)
        if not org:
            stats["fail"] += 1
            continue
        anns = fetch_announcements(session, db.strip_code_suffix(ts_code), org,
                                   start_dash, end_dash)
        if anns is None:
            stats["fail"] += 1
            continue
        for a in anns:
            conn.execute(
                "INSERT OR REPLACE INTO cninfo_announcement VALUES (?,?,?,?)",
                (ts_code, a["ann_date"], a["title"], now))
            for etype, direction in apply_rules(a["title"]):
                conn.execute(
                    "INSERT OR REPLACE INTO major_events VALUES (?,?,?,?,?,?,?)",
                    (ts_code, a["ann_date"], etype, a["title"], direction,
                     "cninfo", now))
                stats["events"] += 1
        stats["ok"] += 1
        conn.commit()  # 健壮性审查I1:每股一提交,锁持有毫秒级(防与19:35/19:40 timer写锁互斥)
        time.sleep(_RATE_LIMIT_S)
    logger.info("cninfo sync {} 股: ok={} fail={} events={}",
                len(ts_codes), stats["ok"], stats["fail"], stats["events"])
    return stats


def replay_rules(conn: sqlite3.Connection) -> int:
    """原始层→规则层重建(规则迭代用,幂等)."""
    conn.execute("DELETE FROM major_events")
    now = time.time()
    n = 0
    for ts_code, ann_date, title in conn.execute(
            "SELECT ts_code, ann_date, title FROM cninfo_announcement"):
        for etype, direction in apply_rules(title):
            conn.execute(
                "INSERT OR REPLACE INTO major_events VALUES (?,?,?,?,?,?,?)",
                (ts_code, ann_date, etype, title, direction, "cninfo", now))
            n += 1
    conn.commit()
    return n
