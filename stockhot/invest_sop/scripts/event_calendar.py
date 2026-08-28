"""Forward-looking event calendar collector — 前瞻事件日历（盘前 SOP §2.1）.

与死掉的 economic_calendar.py（事后"实际值 vs 预期"意外度信号，AKShare 百度源
长期 403，表从未建立）用途不同：本脚本采集**尚未发生**的决定性事件，供盘前
报告"§1.6 前瞻事件日历"渲染，解决 SOP 有纸面"特殊事件日历"却无人知道
FOMC/非农/英伟达财报在哪天的问题。

三类来源（按可靠性分层）：
  1. 宏观日历   ak.news_economic_baidu 查询未来 N 天（实测含 FOMC 联邦基金利率
                决议、非农、CPI、MLF/LPR、日英央行决议；2026-08-28 实测可通，
                历史上有 403/TLS 失败期——失败只 WARN 不中断）
  2. A股财报    Tushare disclosure_date（预约披露日期），宇宙=active 持仓+
                active 自选，覆盖最近两个报告期
  3. 手动事件   --add 子命令：海外财报（英伟达等）/产品发布/医学 readout 由
                agent 周频 web 搜索后带 URL 写入（SOP 纪律：催化必须附来源）

Table: invest_event_calendar (stockhot.db)
  date        TEXT  -- 事件日期 YYYY-MM-DD
  time        TEXT  -- HH:MM 北京时间
  category    TEXT  -- macro_us / macro_cn / macro_global / earnings_a /
                      earnings_us / product / policy
  event       TEXT  -- 事件描述
  expected    TEXT  -- 预期值（原样文本）
  importance  INT   -- 1-3（3=决定性）
  impact_scope TEXT -- 关联板块/产业链
  source      TEXT  -- akshare_baidu / tushare_disclosure / agent_web
  source_url  TEXT

Usage:
    PYTHONPATH=. .venv/bin/python stockhot/invest_sop/scripts/event_calendar.py
    PYTHONPATH=. .venv/bin/python stockhot/invest_sop/scripts/event_calendar.py --days 21
    PYTHONPATH=. .venv/bin/python stockhot/invest_sop/scripts/event_calendar.py --skip-macro
    PYTHONPATH=. .venv/bin/python stockhot/invest_sop/scripts/event_calendar.py \
        --add "date=2026-09-10|event=英伟达FY2027Q2财报(盘后)|category=earnings_us|importance=3|scope=AI算力链|url=https://..."
"""

from __future__ import annotations

import argparse
import os
import re
from datetime import date as _date, timedelta

from stockhot.invest_sop.utils.db_helpers import upsert_record
from stockhot.storage.database import get_connection

TABLE = "invest_event_calendar"

# ── Schema ─────────────────────────────────────────────────────────────


def _ensure_table() -> None:
    """Create invest_event_calendar if not exists (idempotent)."""
    conn = get_connection()
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            date TEXT NOT NULL,
            time TEXT,
            category TEXT NOT NULL,
            event TEXT NOT NULL,
            expected TEXT,
            importance INTEGER DEFAULT 1,
            impact_scope TEXT,
            source TEXT NOT NULL,
            source_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (date, event)
        )
        """
    )
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_date ON {TABLE}(date)")
    conn.commit()
    conn.close()


# ── 宏观日历：AKShare 百度财经日历（前瞻查询） ─────────────────────────

# 只保留能撬动风险偏好的事件；钻井数/库存/ETF 持仓等周频噪音不收。
_US_PATTERNS = re.compile(
    r"联邦基金|利率决议|FOMC|非农|ADP就业|CPI|PPI|PCE|GDP|失业率|"
    r"零售销售|耐用品订单|ISM|Markit制造业|密歇根大学|消费者信心"
)
_CN_PATTERNS = re.compile(
    r"LPR|贷款市场报价|MLF|中期借贷便利|GDP|CPI|PPI|社融|PMI|贸易帐|"
    r"规模以上工业|社会消费品|固定资产投资|城镇调查失业率"
)
# 日英央行利率决议（日元套息/全球流动性）
_GLOBAL_PATTERNS = re.compile(r"央行基准利率|利率决议|政策利率")

# 对 A 股开盘具有决定性影响的子集 → importance 升 3（百度口径只给 2）
_CRITICAL_PATTERNS = re.compile(r"联邦基金|利率决议|非农就业人口变动季调后")


def _to_int(v: object) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _clean(v: object) -> str:
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def collect_macro(days: int = 21) -> int:
    """查询未来 days 天（含今日）的中美/全球关键宏观事件.

    Returns: 入库行数（0 if source failed — 不中断，报告侧标注数据不可用）。
    """
    import akshare as ak

    # Strip proxy for AKShare（与 overseas_market_data 同一处理）
    removed = {}
    for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
        if key in os.environ:
            removed[key] = os.environ.pop(key)

    stored = 0
    today = _date.today()
    try:
        for i in range(days):
            d = (today + timedelta(days=i)).strftime("%Y%m%d")
            try:
                df = ak.news_economic_baidu(date=d)
            except Exception as e:
                # 单日失败不中断整体（百度源历史上有间歇 403/TLS 失败）
                print(f"  [WARN] macro calendar {d} fetch failed: {str(e)[:100]}")
                continue
            if df is None or len(df) == 0:
                continue

            for _, r in df.iterrows():
                region = _clean(r.get("地区"))
                event = _clean(r.get("事件"))
                importance = _to_int(r.get("重要性"))
                if not event or importance < 2:
                    continue

                if "美国" in region and _US_PATTERNS.search(event):
                    category = "macro_us"
                elif region == "中国" and _CN_PATTERNS.search(event):
                    category = "macro_cn"
                elif region in ("日本", "英国") and _GLOBAL_PATTERNS.search(event):
                    category = "macro_global"
                else:
                    continue

                if _CRITICAL_PATTERNS.search(event):
                    importance = 3  # FOMC 决议/非农主指标 → 决定性

                upsert_record(
                    TABLE,
                    {
                        "date": _clean(r.get("日期")) or (
                            today + timedelta(days=i)
                        ).strftime("%Y-%m-%d"),
                        "time": _clean(r.get("时间")),
                        "category": category,
                        "event": event,
                        "expected": _clean(r.get("预期")),
                        "importance": importance,
                        "impact_scope": "",
                        "source": "akshare_baidu",
                        "source_url": "",
                    },
                    ["date", "event"],
                )
                stored += 1
    finally:
        os.environ.update(removed)

    print(f"[OK] macro calendar: {stored} events stored (next {days} days)")
    return stored


# ── A股财报预约披露：Tushare disclosure_date ────────────────────────────


def _to_ts_code(code: str) -> str:
    """6 位纯数字 → 带Exchange后缀 ts_code（兼容已带后缀的输入）."""
    c = code.split(".")[0]
    if c.startswith("6"):
        return f"{c}.SH"
    if c.startswith(("8", "4", "9")):
        return f"{c}.BJ"
    return f"{c}.SZ"


def _quarter_end(d: _date, quarters_back: int) -> str:
    """返回往前第 quarters_back 个季度末（YYYYMMDD）."""
    q_month = ((d.month - 1) // 3) * 3  # 0/3/6/9
    year, qm = d.year, q_month
    for _ in range(quarters_back):
        if qm == 0:
            year, qm = year - 1, 12
        else:
            qm -= 3
    return f"{year}{qm:02d}{_month_days(qm, year)}"


def _month_days(month: int, year: int) -> str:
    days = {3: 31, 6: 30, 9: 30, 12: 31}
    return f"{days[month]:02d}"


_PERIOD_LABEL = {3: "一季报", 6: "中报", 9: "三季报", 12: "年报"}


def _fetch_universe() -> dict[str, str]:
    """active 持仓 + watching 自选 → {ts_code: name}."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT code, name FROM invest_holdings WHERE status='active'
            UNION
            SELECT code, name FROM invest_watchlist WHERE status='watching'
            """
        ).fetchall()
        if not rows:
            # 无持仓时退化为全部非 archived 自选
            rows = conn.execute(
                "SELECT code, name FROM invest_watchlist WHERE status != 'archived'"
            ).fetchall()
        return {_to_ts_code(r["code"]): r["name"] for r in rows}
    finally:
        conn.close()


def collect_disclosure() -> int:
    """拉取持仓/自选的财报预约披露日期（最近两个报告期，未来未披露的入库）.

    注：预约时间表在报告期临近时才逐步公布（如三季报 9 月下旬起），
    查询为空属正常，不报警。
    """
    from dotenv import load_dotenv

    load_dotenv()
    import tushare as ts

    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        print("  [WARN] disclosure: TUSHARE_TOKEN not set, skip")
        return 0
    pro = ts.pro_api(token)

    universe = _fetch_universe()
    if not universe:
        print("[OK] disclosure: empty universe, skip")
        return 0

    today_str = _date.today().strftime("%Y-%m-%d")
    stored = 0
    for qb in (0, 1):  # 最近一个 + 上一个报告期
        end_date = _quarter_end(_date.today(), qb)
        month = int(end_date[4:6])
        label = _PERIOD_LABEL.get(month, f"{end_date}报告期")
        try:
            df = pro.disclosure_date(end_date=end_date)
        except Exception as e:
            print(f"  [WARN] disclosure {end_date} failed: {str(e)[:100]}")
            continue
        if df is None or len(df) == 0:
            continue

        for _, r in df.iterrows():
            ts_code = r.get("ts_code")
            if ts_code not in universe:
                continue
            # 预约日优先，实际披露日兜底；只保留今天及以后的
            d = r.get("actual_date") or r.get("pre_date")
            if not d or d < today_str.replace("-", ""):
                continue
            d_fmt = f"{str(d)[:4]}-{str(d)[4:6]}-{str(d)[6:8]}"
            name = universe[ts_code]
            upsert_record(
                TABLE,
                {
                    "date": d_fmt,
                    "time": "",
                    "category": "earnings_a",
                    "event": f"{name} {label}披露",
                    "expected": "",
                    "importance": 2,
                    "impact_scope": "",
                    "source": "tushare_disclosure",
                    "source_url": "",
                },
                ["date", "event"],
            )
            stored += 1

    print(f"[OK] disclosure: {stored} events stored (universe {len(universe)})")
    return stored


# ── 手动事件：agent 周频 web 搜索后写入 ────────────────────────────────


def add_manual(spec: str) -> int:
    """解析 key=value 竖线串并入库.

    必填 date/event；可选 category(默认 earnings_us)/importance(默认 3)/
    scope/url/time。分隔符用 | （事件文本常含逗号）。
    """
    kv: dict[str, str] = {}
    for part in spec.split("|"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        kv[k.strip()] = v.strip()

    if not kv.get("date") or not kv.get("event"):
        print("  [WARN] --add requires date=... and event=...")
        return 0

    upsert_record(
        TABLE,
        {
            "date": kv["date"],
            "time": kv.get("time", ""),
            "category": kv.get("category", "earnings_us"),
            "event": kv["event"],
            "expected": kv.get("expected", ""),
            "importance": int(kv.get("importance", "3")),
            "impact_scope": kv.get("scope", ""),
            "source": "agent_web",
            "source_url": kv.get("url", ""),
        },
        ["date", "event"],
    )
    print(f"[OK] manual event added: {kv['date']} {kv['event']}")
    return 1


# ── CLI ────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="前瞻事件日历采集")
    parser.add_argument("--days", type=int, default=21, help="宏观日历前瞻天数(默认21,确保FOMC提前可见)")
    parser.add_argument("--skip-macro", action="store_true", help="跳过宏观日历采集")
    parser.add_argument("--skip-disclosure", action="store_true", help="跳过财报披露采集")
    parser.add_argument(
        "--add",
        help='手动添加事件: --add "date=2026-09-10|event=英伟达财报|category=earnings_us|importance=3|scope=AI算力链|url=https://..."',
    )
    args = parser.parse_args()

    _ensure_table()

    if args.add:
        add_manual(args.add)
        return
    if not args.skip_macro:
        collect_macro(args.days)
    if not args.skip_disclosure:
        collect_disclosure()


if __name__ == "__main__":
    main()
