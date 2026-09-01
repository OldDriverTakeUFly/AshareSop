# davis_analyzer/cardgen/daily.py
"""每日盘面复盘卡生成器(2026-09-01):stockhot.db 当日数据 → facts+spec → validate。

叙事纪律(spec §4.3):零观点、纯数据描述句;所有数字 $fact 引用或与 facts 值+单位严格一致。
措辞红线:只用事实词汇(N连板/净买额/换手/封单);禁 追高/上车/抄作业/标的/庄家/主力/拉盘/内幕/赌;
金额口径只用「净买额/净卖额」(敏感词表含二字词 买入/卖出);正文日期用 ISO 或 09-01 形态,禁「9月1日」。"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from decimal import Decimal
from pathlib import Path

from loguru import logger

from davis_analyzer.cardgen import ledger
from davis_analyzer.cardgen.facts import save_facts
from davis_analyzer.cardgen.types import Fact, ValidateReport
from davis_analyzer.cardgen.validator import run_validation

REPO_ROOT = Path(__file__).resolve().parents[2]
PENDING_DIR = "未发布"
FOOT = "数据来源:沪深交易所/东方财富(经 stockhot 采集) · 仅供研究参考,不构成投资建议"
FOOT_LAST = FOOT + "。市场有风险,投资需谨慎。"

DEFAULT_STOCKHOT_DB = REPO_ROOT / "storage" / "database" / "stockhot.db"


class DailyDataMissing(RuntimeError):
    """当日采集数据不完整,拒绝生成。"""


# ── 数据层(只读 stockhot.db) ────────────────────────────────────────────

def _ro_conn(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _daily_json(con: sqlite3.Connection, day: str, data_type: str) -> list[dict]:
    row = con.execute("SELECT data_json FROM daily_data WHERE trade_date=? AND data_type=?",
                      (day, data_type)).fetchone()
    return json.loads(row[0]) if row else []


def _analysis_json(con: sqlite3.Connection, day: str, analysis_type: str) -> dict | None:
    row = con.execute("SELECT result_json FROM analysis_results WHERE trade_date=? AND analysis_type=?",
                      (day, analysis_type)).fetchone()
    return json.loads(row[0]) if row else None


def fetch_day_bundle(db_path: Path, day: str) -> dict:
    con = _ro_conn(db_path)
    try:
        lu = _analysis_json(con, day, "limit_up_analysis")
        if not lu or not lu.get("consecutive_boards"):
            raise DailyDataMissing(f"{day} 缺 limit_up_analysis.consecutive_boards(盘面扫描未完成?)")
        pool = _daily_json(con, day, "limit_up_pool")
        if not pool:
            raise DailyDataMissing(f"{day} 缺 limit_up_pool")
        prev_row = con.execute(
            "SELECT result_json FROM analysis_results WHERE analysis_type='limit_up_analysis' "
            "AND trade_date<? ORDER BY trade_date DESC LIMIT 1", (day,)).fetchone()
        prev_max = None
        if prev_row:
            prev_boards = json.loads(prev_row[0]).get("consecutive_boards") or []
            prev_max = max((int(t["board_count"]) for t in prev_boards), default=None)
        dt = _analysis_json(con, day, "dragon_tiger") or {}
        return {
            "pool": pool,
            "broken": _daily_json(con, day, "broken_pool"),
            "down": _daily_json(con, day, "limit_down_pool"),
            "boards": lu["consecutive_boards"],
            "prev_boards_max": prev_max,
            "lhb_detail": _daily_json(con, day, "dragon_tiger_detail"),
            "brokers": dt.get("brokers") or [],
            "institutional": dt.get("institutional") or [],
        }
    finally:
        con.close()


# ── 工具函数(格式化 + 无数字化) ─────────────────────────────────────────

def _fact(fid: str, value, unit: str, display: str, day: str, ref: str) -> Fact:
    return Fact(id=fid, value=Decimal(str(value)), unit=unit, display=display,
                as_of=day, source_kind="stockhot", source_ref=ref, expires=day)


def _yi_signed(amount: float) -> tuple[str, str]:
    """元→亿,带符号 display + 无符号数值字符串。0 亦带 +。

    去尾零(0.30→0.3):facts 序列化会把 value 归一成无尾零形态,display 须与之逐字匹配。"""
    v = f"{abs(amount) / 1e8:.2f}".rstrip("0").rstrip(".") or "0"
    sign = "-" if amount < 0 else "+"
    return v, f"{sign}{v}亿"


def _pct_signed(pct: float) -> tuple[str, str]:
    v = round(abs(pct), 2)
    return f"{v:.2f}", f"{'-' if pct < 0 else '+'}{v:.2f}%"


def _hhmm(hhmmss: str) -> str:
    s = str(hhmmss).zfill(6)
    return f"{s[:2]}:{s[2:4]}"


def _digit_safe(name: str) -> str | None:
    """板块名去阿拉伯数字:3D打印→三维打印;仍含数字则弃用(None)。"""
    out = name.replace("3D", "三维").replace("4D", "四维")
    out = _SECTOR_ALIASES.get(out, out)  # Tushare industry 字段源端截断补全
    return None if re.search(r"\d", out) else out


# Tushare stock_basic.industry 字段按宽度截断的常见板块名 → 全称
_SECTOR_ALIASES = {
    "农产品加": "农产品加工",
    "互联网电": "互联网电商",
}


# ── 连板天梯 5 页卡 ───────────────────────────────────────────────────────

def build_ladder(day: str, bundle: dict) -> tuple[list[Fact], dict]:
    ref_pool = f"stockhot.db:daily_data:limit_up_pool@{day}"
    ref_ana = f"stockhot.db:analysis_results:limit_up_analysis@{day}:consecutive_boards"
    pool, boards = bundle["pool"], bundle["boards"]
    facts: list[Fact] = []
    top_tier = boards[0]
    board_max = int(top_tier["board_count"])
    top_stock = max(pool, key=lambda r: (r.get("consecutive_boards") or 0,
                                         -(r.get("broken_count") or 0)))
    facts += [
        _fact("zt_count", len(pool), "只", f"{len(pool)}只", day, ref_pool + ":len"),
        _fact("broken_count", len(bundle["broken"]), "只",
              f"{len(bundle['broken'])}只", day, f"stockhot.db:daily_data:broken_pool@{day}:len"),
        _fact("down_count", len(bundle["down"]), "只",
              f"{len(bundle['down'])}只", day, f"stockhot.db:daily_data:limit_down_pool@{day}:len"),
        _fact("board_max", board_max, "", f"{board_max}板", day, f"{ref_ana}[0].board_count"),
    ]
    # 梯队表行:板数/家数/个股
    tier_rows = []
    for t in boards:
        n, stocks = int(t["board_count"]), t["stocks"]
        fid, cid = f"tier_{n}", f"tier_{n}_count"
        facts.append(_fact(fid, n, "", f"{n}板", day, f"{ref_ana}:board_count={n}"))
        facts.append(_fact(cid, len(stocks), "只", f"{len(stocks)}只", day,
                           f"{ref_ana}:board_count={n}:len"))
        tier_rows.append({"cells": [{"$fact": fid}, {"$fact": cid}, "、".join(s["name"] for s in stocks)],
                          "cls": ["up" if n == board_max else "", "", ""]})
    # 最高板个股明细
    seal_v, seal_d = _yi_signed(float(top_stock.get("seal_amount") or 0))
    turn_v, turn_d = _pct_signed(float(top_stock.get("turnover_rate") or 0))
    chg_v, chg_d = _pct_signed(float(top_stock.get("change_pct") or 0))
    facts += [
        _fact("top_seal_yi", seal_v, "亿", seal_d, day, f"{ref_pool}:{top_stock['code']}.seal_amount"),
        _fact("top_turnover_pct", turn_v, "%", turn_d, day, f"{ref_pool}:{top_stock['code']}.turnover_rate"),
        _fact("top_change_pct", chg_v, "%", chg_d, day, f"{ref_pool}:{top_stock['code']}.change_pct"),
    ]
    prev_max = bundle.get("prev_boards_max")
    if prev_max is None:
        compare_rows = [{"cells": ["昨日高度", "昨日无梯队数据"], "cls": ["", ""]}]
    else:
        delta = board_max - prev_max
        if delta != 0:
            facts.append(_fact("board_delta", abs(delta), "", f"{delta:+d}", day,
                               f"stockhot.db:analysis_results:limit_up_analysis@{day}:vs_prev"))
            # 渲染器按表头列数渲染,多余 cell 会溢出表格——晋级/回落并入标签列
            compare_rows = [{"cells": [f"较昨日高度 · {'晋级' if delta > 0 else '回落'}", {"$fact": "board_delta"}],
                             "cls": ["", "up" if delta > 0 else ""]}]
        else:
            compare_rows = [{"cells": ["较昨日高度", "持平"], "cls": ["", ""]}]
    sub_word = ("较昨日晋级" if (prev_max or 0) < board_max
                else "较昨日回落" if (prev_max or 0) > board_max else "梯队高度观察")

    # 板块联动(聚合 pool 的 sector,代表股取该板块最早 first_seal_time)
    sector_map: dict[str, list[dict]] = {}
    for r in pool:
        sec = _digit_safe(str(r.get("sector") or ""))
        if sec:
            sector_map.setdefault(sec, []).append(r)
    top_sectors = sorted(sector_map.items(), key=lambda kv: -len(kv[1]))[:4]
    sector_rows = []
    for i, (sec, rows) in enumerate(top_sectors, 1):
        rep = min(rows, key=lambda r: str(r.get("first_seal_time") or "999999"))
        cid = f"sector_{i}_count"
        facts.append(_fact(cid, len(rows), "只", f"{len(rows)}只", day,
                           f"{ref_pool}:sector={sec}:len"))
        sector_rows.append({"cells": [sec, {"$fact": cid}, rep["name"]], "cls": ["", "", ""]})

    spec = {
        "group": "每日复盘",
        "cards": [
            {"type": "cover", "theme": "red", "name": "01_封面",
             "tag_top": "连板天梯 · 每日数据复盘",
             "title": "今天的连板天梯<br>梯队与高度一览",
             "sub": f"封板结构 · {sub_word} · 板块联动<br>{day} 交易数据整理",
             "stats": [
                 {"v": {"$fact": "board_max"}, "k": "最高连板(板)"},
                 {"v": {"$fact": "zt_count"}, "k": "涨停(家)"},
                 {"v": {"$fact": "broken_count"}, "k": "炸板(家)"}],
             "tags": "#连板天梯 #每日复盘 #涨停数据 #市场结构",
             "foot": FOOT},
            {"type": "table", "theme": "cream", "name": "02_梯队", "first_left": True,
             "tag_top": "连板梯队", "tag_color": "#ea580c",
             "title": f"最高 {board_max} 连板 · 梯队全景",
             "subtitle": "按连板高度分层,个股按梯队归属",
             "table": {"headers": ["板数", "家数", "个股"], "rows": tier_rows},
             "foot": FOOT},
            {"type": "table", "theme": "blue", "name": "03_高度明细", "first_left": True,
             "tag_top": "空间高度", "tag_color": "#2563eb",
             "title": f"{top_stock['name']} · 今日最高板",
             "subtitle": sub_word,
             "table": {"headers": ["要点", "读数"], "rows": [
                 {"cells": ["今日连板高度", {"$fact": "board_max"}], "cls": ["", "up"]},
                 {"cells": ["当日涨跌幅", {"$fact": "top_change_pct"}], "cls": ["", ""]},
                 {"cells": ["封单金额", {"$fact": "top_seal_yi"}], "cls": ["", ""]},
                 {"cells": ["换手率", {"$fact": "top_turnover_pct"}], "cls": ["", ""]},
                 {"cells": ["首次封板时间", _hhmm(top_stock.get("first_seal_time", ""))], "cls": ["", ""]},
                 {"cells": ["所属板块", _digit_safe(str(top_stock.get("sector") or "")) or "-"],
                  "cls": ["", ""]},
                 compare_rows[0],
             ]},
             "foot": FOOT},
            {"type": "table", "theme": "green", "name": "04_板块联动", "first_left": True,
             "tag_top": "板块联动", "tag_color": "#16a34a",
             "title": "涨停家数居前板块",
             "subtitle": "代表股取该板块最早封板个股",
             "table": {"headers": ["板块", "涨停家数", "代表股"], "rows": sector_rows},
             "foot": FOOT},
            {"type": "summary", "theme": "lavender", "name": "05_收束",
             "tag_top": "数据说明", "tag_color": "#0f172a",
             "title": "天梯是结构数据",
             "subtitle": "不是操作清单",
             "rows": [
                 {"desc": "<b>今日高度</b> → 见封面与高度明细页"},
                 {"desc": "<b>梯队结构</b> → 高度分层与家数分布,反映当日封板结构"},
                 {"desc": "<b>联动主线</b> → 涨停家数居前板块,反映题材聚集度"}],
             "kbox": {"date": "栏目定位", "color": "blue",
                      "html": "本栏目每日盘后从交易所公开数据整理连板梯队——搬运数据,不输出操作指令"},
             "tags": "#连板天梯 #每日复盘 #涨停数据 #市场结构",
             "foot": FOOT_LAST},
        ],
    }
    return facts, spec


# ── 龙虎榜卡(Task 5 实现) ────────────────────────────────────────────────

def _reason_label(reason: str) -> str:
    """交易所上榜原因 → 无数字短标签(原文含 20%/前5 只等数字,直接进卡会撞数字闸)。"""
    if "连续三个交易日" in reason or "三日" in reason:
        return "三日涨幅偏离" if "涨幅" in reason else "三日跌幅偏离"
    if "换手率" in reason:
        return "换手达标"
    if "振幅" in reason:
        return "振幅达标"
    if "跌幅" in reason:
        return "日内跌幅偏离"
    if "涨幅" in reason:
        return "日内涨幅偏离"
    return "异动"


def _truncate(name: str, n: int = 18) -> str:
    return name if len(name) <= n else name[: n - 1] + "…"


def _aggregate_by_code(detail: list[dict]) -> list[dict]:
    """dragon_tiger_detail 一股可多行(不同上榜原因)——按 code 去重聚合。

    同 code 行 net_buy_amount/buy_amount/sell_amount 求和;reason/change_pct/close_price 取首条。
    排名与封面统计均须基于聚合后的个股列表,否则同一股会在 Top 表重复出现、计数虚高。"""
    agg: dict[str, dict] = {}
    for r in detail:
        code = str(r.get("code") or "")
        if not code:
            continue
        if code in agg:
            a = agg[code]
            for k in ("net_buy_amount", "buy_amount", "sell_amount"):
                a[k] = float(a.get(k) or 0) + float(r.get(k) or 0)
        else:
            row = dict(r)
            for k in ("net_buy_amount", "buy_amount", "sell_amount"):
                row[k] = float(row.get(k) or 0)
            agg[code] = row
    return list(agg.values())


def build_lhb(day: str, bundle: dict) -> tuple[list[Fact], dict]:
    detail = _aggregate_by_code(bundle["lhb_detail"])
    if not detail:
        raise DailyDataMissing(f"{day} 缺 dragon_tiger_detail(龙虎榜数据未落库?)")
    brokers = bundle["brokers"]
    # Task 3 交接:机构席位行带真实席位名,只取「机构专用」纯机构口径(沪股通/营业部不计入)
    inst = [r for r in bundle["institutional"] if r.get("inst_name") == "机构专用"]
    ref_detail = f"stockhot.db:daily_data:dragon_tiger_detail@{day}"
    ref_ana = f"stockhot.db:analysis_results:dragon_tiger@{day}"
    facts: list[Fact] = []

    net_total = sum(float(r.get("net_buy_amount") or 0) for r in detail)
    nt_v, nt_d = _yi_signed(net_total)
    facts.append(_fact("lhb_count", len(detail), "家", f"{len(detail)}家", day,
                       ref_detail + ":dedup(code):len"))
    facts.append(_fact("lhb_net_total_yi", nt_v, "亿", nt_d, day, ref_detail + ":sum(net_buy_amount)"))

    ladder_codes = {s["code"] for t in bundle["boards"] for s in t["stocks"]}
    cross = [r for r in detail if r.get("code") in ladder_codes]
    facts.append(_fact("cross_count", len(cross), "只", f"{len(cross)}只", day,
                       ref_detail + ":∩limit_up_analysis.consecutive_boards"))

    # 个股净买 Top10 / 净卖 Top5
    ranked = sorted(detail, key=lambda r: -(float(r.get("net_buy_amount") or 0)))
    buy_rows, sell_rows = [], []
    for i, r in enumerate(ranked[:10], 1):
        v, d = _yi_signed(float(r.get("net_buy_amount") or 0))
        p_v, p_d = _pct_signed(float(r.get("change_pct") or 0))
        facts += [_fact(f"nb{i}_yi", v, "亿", d, day, f"{ref_detail}:{r['code']}.net_buy_amount"),
                  _fact(f"nb{i}_pct", p_v, "%", p_d, day, f"{ref_detail}:{r['code']}.change_pct")]
        buy_rows.append({"cells": [r["name"], {"$fact": f"nb{i}_pct"}, {"$fact": f"nb{i}_yi"},
                                   _reason_label(str(r.get("reason") or ""))],
                         "cls": ["", "up" if float(r.get("change_pct") or 0) > 0 else "", "up", ""]})
    for i, r in enumerate(sorted(detail, key=lambda r: float(r.get("net_buy_amount") or 0))[:5], 1):
        v, d = _yi_signed(float(r.get("net_buy_amount") or 0))
        p_v, p_d = _pct_signed(float(r.get("change_pct") or 0))
        facts += [_fact(f"ns{i}_yi", v, "亿", d, day, f"{ref_detail}:{r['code']}.net_buy_amount"),
                  _fact(f"ns{i}_pct", p_v, "%", p_d, day, f"{ref_detail}:{r['code']}.change_pct")]
        sell_rows.append({"cells": [r["name"], {"$fact": f"ns{i}_pct"}, {"$fact": f"ns{i}_yi"},
                                    _reason_label(str(r.get("reason") or ""))],
                          "cls": ["", "", "", ""]})

    # 营业部 Top5(只呈现净额,禁 买入额/卖出额 措辞)
    broker_rows = []
    for i, b in enumerate(sorted(brokers, key=lambda x: -float(x.get("net_amount") or 0))[:5], 1):
        v, d = _yi_signed(float(b.get("net_amount") or 0))
        facts.append(_fact(f"bk{i}_yi", v, "亿", d, day, f"{ref_ana}:brokers.net_amount"))
        broker_rows.append({"cells": [_truncate(str(b.get("broker_name") or "")), {"$fact": f"bk{i}_yi"}],
                            "cls": ["", "up" if float(b.get("net_amount") or 0) > 0 else ""]})

    # 机构席位:纯机构口径按个股聚合净额 Top5(行=个股口径,机构专用);空则降级占位
    inst_agg: dict[str, float] = {}
    for r in inst:
        code = str(r.get("inst_code") or "")
        inst_agg[code] = inst_agg.get(code, 0.0) + float(r.get("net_amount") or 0)
    name_by_code = {r.get("code"): r.get("name") for r in detail}
    inst_rows = []
    top_inst = sorted(inst_agg.items(), key=lambda kv: -kv[1])[:5]
    for i, (code, amt) in enumerate(top_inst, 1):
        v, d = _yi_signed(amt)
        facts.append(_fact(f"ist{i}_yi", v, "亿", d, day, f"{ref_ana}:institutional.sum(net_amount)"))
        inst_rows.append({"cells": [name_by_code.get(code, code), {"$fact": f"ist{i}_yi"}],
                          "cls": ["", "up" if amt > 0 else ""]})

    # 交叉视角
    cross_rows = []
    for i, r in enumerate(cross[:5], 1):
        board = next((int(t["board_count"]) for t in bundle["boards"]
                      if any(s["code"] == r["code"] for s in t["stocks"])), 0)
        v, d = _yi_signed(float(r.get("net_buy_amount") or 0))
        facts += [_fact(f"cr{i}_board", board, "", f"{board}板", day,
                        f"stockhot.db:analysis_results:limit_up_analysis@{day}:consecutive_boards"),
                  _fact(f"cr{i}_yi", v, "亿", d, day, f"{ref_detail}:{r['code']}.net_buy_amount")]
        cross_rows.append({"cells": [r["name"], {"$fact": f"cr{i}_board"}, {"$fact": f"cr{i}_yi"}],
                           "cls": ["", "up", ""]})

    inst_page = (
        {"type": "table", "theme": "blue", "name": "05_机构席位", "first_left": True,
         "tag_top": "机构席位", "tag_color": "#7c3aed",
         "title": "机构席位净额居前个股",
         "subtitle": "机构专用席位合并口径",
         "table": {"headers": ["个股", "机构净额"], "rows": inst_rows},
         "foot": FOOT}
        if inst_rows else
        {"type": "table", "theme": "blue", "name": "05_机构席位", "first_left": True,
         "tag_top": "机构席位", "tag_color": "#7c3aed",
         "title": "机构席位动向",
         "subtitle": "数据以交易所披露为准",
         "table": {"headers": ["说明"], "rows": [{"cells": ["今日无机构席位数据"], "cls": [""]}]},
         "foot": FOOT})

    spec = {
        "group": "每日复盘",
        "cards": [
            {"type": "cover", "theme": "purple_dark", "name": "01_封面",
             "tag_top": "龙虎榜 · 每日数据复盘",
             "title": "今天的龙虎榜<br>资金动向一览",
             "sub": f"个股净额 · 营业部 · 机构席位<br>{day} 交易数据整理",
             "stats": [
                 {"v": {"$fact": "lhb_count"}, "k": "上榜(家)"},
                 {"v": {"$fact": "lhb_net_total_yi"}, "k": "整体净买额(亿)"},
                 {"v": {"$fact": "cross_count"}, "k": "上榜且连板(只)"}],
             "tags": "#龙虎榜 #每日复盘 #资金数据 #市场结构",
             "foot": FOOT},
            {"type": "table", "theme": "blue", "name": "02_净买额居前", "first_left": True,
             "tag_top": "个股净买额", "tag_color": "#2563eb",
             "title": "净买额居前个股",
             "subtitle": "口径:龙虎榜净买额(买额-卖额)",
             "table": {"headers": ["个股", "涨跌幅", "净买额", "上榜标签"], "rows": buy_rows},
             "foot": FOOT},
            {"type": "table", "theme": "green", "name": "03_净卖额居前", "first_left": True,
             "tag_top": "个股净卖额", "tag_color": "#16a34a",
             "title": "净卖额居前个股",
             "subtitle": "资金流出侧观察",
             "table": {"headers": ["个股", "涨跌幅", "净卖额", "上榜标签"], "rows": sell_rows},
             "foot": FOOT},
            {"type": "table", "theme": "cream", "name": "04_活跃营业部", "first_left": True,
             "tag_top": "活跃营业部", "tag_color": "#ea580c",
             "title": "净额居前营业部",
             "subtitle": "沪深交易所披露口径,全名截断显示",
             "table": {"headers": ["营业部", "净额"], "rows": broker_rows},
             "foot": FOOT},
            inst_page,
            {"type": "summary", "theme": "lavender", "name": "06_收束",
             "tag_top": "交叉视角", "tag_color": "#0f172a",
             "title": "龙虎榜 × 连板梯队",
             "subtitle": "两份公开数据的交集",
             "rows": (cross_rows and [
                 {"desc": "<b>上榜连板股</b> → 见下表(连板高度 × 龙虎榜净额)"}]
                 or [{"desc": "<b>今日交集为空</b> → 龙虎榜与连板梯队无重叠个股"}]),
             "kbox": {"date": "栏目定位", "color": "blue",
                      "html": "本栏目每日盘后整理交易所龙虎榜披露——搬运数据,不输出操作指令"},
             "tags": "#龙虎榜 #每日复盘 #资金数据 #市场结构",
             "foot": FOOT_LAST},
        ],
    }
    if cross_rows:
        # summary 卡不排表格——交叉明细作为第 6 页内 table 与 rows 共存(spec 支持,参照公告日报 rows+kbox)
        spec["cards"][-1]["table"] = {"headers": ["个股", "连板", "净额"], "rows": cross_rows}
    return facts, spec


# ── 工程落盘与编排 ───────────────────────────────────────────────────────

def write_project(projects_root: Path, topic: str, facts: list[Fact], spec: dict) -> Path:
    proj = projects_root / PENDING_DIR / topic
    (proj / "output").mkdir(parents=True, exist_ok=True)
    save_facts(proj / "facts.json", facts)
    (proj / "cards.spec.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return proj


def generate(kind: str, day: str, projects_root: Path, ledger_db: Path | None,
             stockhot_db: Path | None = None) -> tuple[Path, str, ValidateReport]:
    """kind ∈ {'ladder','lhb'};完整性自检→build→write→register→validate→log+set_status。

    返回 (project_dir, topic, report)。缺数据 raise DailyDataMissing。
    stockhot_db 缺省取 env CARDGEN_STOCKHOT_DB 或 DEFAULT_STOCKHOT_DB。
    """
    db = stockhot_db or Path(os.environ.get("CARDGEN_STOCKHOT_DB", DEFAULT_STOCKHOT_DB))
    bundle = fetch_day_bundle(db, day)
    if kind == "ladder":
        facts, spec = build_ladder(day, bundle)
        topic = f"连板天梯/{day}"
    elif kind == "lhb":
        facts, spec = build_lhb(day, bundle)   # Task 5 实现;先放占位 raise
        topic = f"龙虎榜/{day}"
    else:
        raise ValueError(f"kind 须 ladder/lhb: {kind}")
    # 同日重跑保护:rendered/queued 的工程禁止静默覆写(须 --bump 或先删工程);drafting/validated 照常
    guard_conn = ledger.connect(ledger_db)
    try:
        existing = ledger.get_card(guard_conn, topic)
        if existing and existing["status"] in ("rendered", "queued"):
            raise RuntimeError(
                f"topic {topic} 已是 {existing['status']} 状态,拒绝覆写——请用 --bump 或先删除工程")
    finally:
        guard_conn.close()
    proj = write_project(projects_root, topic, facts, spec)
    conn = ledger.connect(ledger_db)
    try:
        ledger.register_card(conn, topic, str(proj / "cards.spec.json"))
        report = run_validation(proj, topic=topic)
        row = ledger.get_card(conn, topic)
        ledger.log_validate(conn, topic, int(row["current_version"]), report.passed, report.failures)
        if report.passed:
            ledger.set_status(conn, topic, "validated")
    finally:
        conn.close()
    for f in report.failures:
        logger.warning(f"[daily] validate 未过 [{f.gate}] {f.card} {f.field}: {f.detail}")
    return proj, topic, report
