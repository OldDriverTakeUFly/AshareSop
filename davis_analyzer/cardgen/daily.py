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

# 发稿文案(2026-09-02 入发布规划;2026-09-03 增盘后观察)——无数字口吻,
# 数字一律留给卡片本身;敏感词/诱导句式由测试锁定;见解句按当日形态从洞察库机械选用
_PUBLISH_COPY: dict[str, dict[str, str]] = {
    "ladder": {
        "title": "连板天梯 | 每日数据复盘",
        "tags": "#连板天梯 #每日复盘 #涨停数据 #市场结构",
        "body": (
            "每天盘后几分钟,读懂当日市场情绪结构📈\n\n"
            "①最高连板与梯队全景——高度代表空间,家数代表广度;\n"
            "②空间高度明细——封单、换手、首次封板时间,以及较昨日晋级/持平/回落;\n"
            "③板块联动——涨停家数居前的方向,看资金聚集度。\n\n"
            "数据来自沪深交易所/东方财富公开披露;盘后观察为方法论视角解读,不构成投资建议。"
        ),
    },
    "lhb": {
        "title": "龙虎榜 | 每日数据复盘",
        "tags": "#龙虎榜 #每日复盘 #资金数据 #市场结构",
        "body": (
            "每天盘后,一张卡看完当日龙虎榜资金动向💰\n\n"
            "①个股净买额/净卖额居前——先看资金主攻与流出的方向;\n"
            "②活跃营业部净额——市场热门席位的当日动向;\n"
            "③机构专用席位净额——按个股合并的机构口径;\n"
            "④龙虎榜×连板梯队交集——情绪与资金的重叠区。\n\n"
            "口径说明:龙虎榜为交易所披露的席位当日合计数据,反映的是榜单事实而非后续走势。"
            "数据来自沪深交易所/东方财富,盘后观察为方法论视角解读,不构成投资建议。"
        ),
    },
    "thermo": {
        "title": "板块温度计 | 每日市场热度",
        "tags": "#板块温度计 #每日复盘 #市场结构 #资金流向",
        "body": (
            "每天盘后,一张卡看懂板块冷热🌡️\n\n"
            "①大盘温度——趋势、宽度、量能、资金流向与涨停情绪五维合成;\n"
            "②一级/二级行业温度榜——量能、资金流向、动量、趋势与涨停密度合成;\n"
            "③高温看拥挤,低温看冷清——温度高是预期打得过满的提醒,"
            "温度低是关注度不足的线索。\n\n"
            "买在无人问津,卖在人声鼎沸——温度计只测温,不替人做决策;"
            "数据来自公开行情与交易所披露,盘后观察为方法论视角解读,不构成投资建议。"
        ),
    },
    "screener": {
        "title": "筛选器温度计 | 每日数据观察",
        "tags": "#筛选器温度计 #每日复盘 #量化研究 #市场情绪",
        "body": (
            "同一台量化筛选器,行情热的时候放行一堆,冷的时候一只都不放🌡️\n\n"
            "①当日读数——动量筛选器与困境反转筛选器各放行几只;\n"
            "②状态判读——空名单不是故障,是筛选器明确说「这摊我不碰」;\n"
            "③近五次轨迹——放行数量的伸缩,比单日读数更有信息量。\n\n"
            "只报筛选器通过数量,不含任何个股与操作建议;"
            "名单由盘后管线自动生成,盘后观察为方法论视角解读,不构成投资建议。"
        ),
    },
}


def publish_copy(kind: str, day: str, bundle: dict | None = None) -> dict[str, str]:
    """发稿层文案(title/body/tags);title 冠 mm-dd 日期,正文无数字。

    bundle 提供时追加当日「盘后观察」(洞察库按数据形态机械选用,见 *_insights)。"""
    c = _PUBLISH_COPY[kind]
    body = c["body"]
    if bundle is not None:
        picker = {"ladder": ladder_insights, "lhb": lhb_insights,
                  "thermo": thermo_insights,
                  "screener": screener_insights}.get(kind)
        picks = picker(bundle) if picker else []
        if picks:
            body += "\n\n盘后观察:\n" + "\n".join(f"· {p}" for p in picks)
    return {"title": f"{day[5:]} {c['title']}", "body": body, "tags": c["tags"]}


# ── 盘后观察洞察库(2026-09-03 用户需求:文字带见解/方法论实践点) ──────────
# 纪律:见解句全部预审入库——零阿拉伯数字、敏感词全表与诱导句式零命中(测试锁定),
# 脚本按当日数据形态机械选用,运行时不自造观点。锚点:
#   高度/广度/封板质量框架 ← 方法论§8.8 与周期三部曲(空间/广度/结构);
#   机构专用与营业部分层口径 ← stockhot/dragon_tiger 采集口径(席位类型语义);
#   「单日净额非趋势结论」 ← 交易所龙虎榜披露规则口径。
_LADDER_DEFAULT_INSIGHT = ("天梯三列的完整读法:高度看空间,家数看广度,封板质量看成色——"
                           "合起来读,比单看最高板完整得多")
_LHB_DEFAULT_INSIGHT = ("龙虎榜的三层读法:个股净额看方向,营业部看热度,机构席位看中期态度——"
                        "三层互相印证,比任何单层数据都可靠")


def ladder_insights(bundle: dict) -> list[str]:
    """按梯队形态选至多两条盘后观察(有序匹配,先结构后质量)。"""
    zt, broken = len(bundle["pool"]), len(bundle["broken"])
    board_max = int(bundle["boards"][0]["board_count"]) if bundle["boards"] else 0
    prev = bundle.get("prev_boards_max")
    picks: list[str] = []
    if prev is not None and board_max < prev:
        picks.append("高度回落期,天梯的正确读法是看谁还在晋级——抗跌的高度比高度本身"
                     "更有信息量,梯队缩圈阶段尤其如此")
    if zt and broken / max(zt, 1) >= 0.3:
        picks.append("炸板家数明显偏多时,封板质量的权重应高于封板数量——首封时间早、"
                     "炸板次数少的涨停含金量更高,高度明细页正是按这个口径选股")
    if prev is not None and board_max > prev and not picks:
        picks.append("高度晋级叠加板块联动,是梯队相对健康的形态——空间与广度同步扩张时,"
                     "梯队数据的参考价值最高")
    if not picks:
        picks.append(_LADDER_DEFAULT_INSIGHT)
    return picks[:2]


def lhb_insights(bundle: dict) -> list[str]:
    """按资金形态选至多两条盘后观察(机构口径优先,其次榜单整体/交叉)。"""
    detail = [r for r in bundle["lhb_detail"] if not _is_bond(r)]
    picks: list[str] = []
    if detail:
        net_total = sum(float(r.get("net_buy_amount") or 0) for r in detail)
        inst_net = sum(float(r.get("net_amount") or 0) for r in bundle["institutional"]
                       if r.get("inst_name") == "机构专用")
        ladder_codes = {s["code"] for t in bundle["boards"] for s in t["stocks"]}
        cross = len({r.get("code") for r in detail} & ladder_codes)
        if inst_net > 0:
            picks.append("机构专用席位净流入的日子,建议把机构口径与营业部分开读——"
                         "两类席位的持有周期通常不同,合并看会互相稀释信号")
        if cross >= 4:
            picks.append("上榜与连板重叠较多时,情绪与资金在共振——交叉页是两份公开数据"
                         "拼出来的第三张地图")
        if not picks and net_total < 0:
            picks.append("榜单整体净流出时,先看流出是集中还是分散——龙虎榜是当日榜单事实,"
                         "单日净额不构成趋势结论")
        if not picks and net_total > 0:
            picks.append("整体净流入时,主攻方向比流入总量更有信息量——净买榜前列的板块归属,"
                         "值得与天梯的联动页对照着看")
    if not picks:
        picks.append(_LHB_DEFAULT_INSIGHT)
    return picks[:2]



DEFAULT_STOCKHOT_DB = REPO_ROOT / "storage" / "database" / "stockhot.db"


def stockhot_db_path() -> Path:
    """stockhot.db 解析(env 重定向优先,与 generate 同口径)。"""
    return Path(os.environ.get("CARDGEN_STOCKHOT_DB", DEFAULT_STOCKHOT_DB))


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
    """涨跌幅 display + 数值字符串。去尾零(57.30→57.3)与 _yi_signed 同因:
    facts 序列化把 value 归一成无尾零形态,display 须与之逐字匹配。"""
    v = f"{abs(pct):.2f}".rstrip("0").rstrip(".") or "0"
    return v, f"{'-' if pct < 0 else '+'}{v}%"


_BOND_NAME_RE = re.compile(r"转\d|转债")


def _is_bond(row: dict) -> bool:
    """龙虎榜混排的可转债(名字如「震裕转02」/代码 11x/12x 段)——个股榜剔除,防名字数字撞数字闸。"""
    code = str(row.get("code") or "").split(".")[0]
    return bool(_BOND_NAME_RE.search(str(row.get("name") or ""))) or code[:2] in ("11", "12")


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
            {"type": "summary", "theme": "lavender", "name": "06_收束",
             "tag_top": "数据说明", "tag_color": "#0f172a",
             "title": "天梯是结构数据",
             "subtitle": "不是操作清单",
             "rows": [
                 {"desc": "<b>今日高度</b> → 见封面与高度明细页"},
                 {"desc": "<b>梯队结构</b> → 高度分层与家数分布,反映当日封板结构"},
                 {"desc": "<b>联动主线</b> → 涨停家数居前板块,反映题材聚集度"}],
             "kbox": {"date": "盘后观察", "color": "blue",
                      "html": "<br>".join(ladder_insights({"pool": pool, "broken": bundle["broken"],
                                                           "boards": boards,
                                                           "prev_boards_max": bundle.get("prev_boards_max")}))},
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


def _truncate(name: str, n: int = 26) -> str:
    # 2026-09-18 用户反馈:18字截断致游资席位显示不全(营业部全名普遍22-24字),放宽到26全容纳
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
    detail = _aggregate_by_code([r for r in bundle["lhb_detail"] if not _is_bond(r)])
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
             "kbox": {"date": "盘后观察", "color": "blue",
                      "html": "<br>".join(lhb_insights(bundle))},
             "tags": "#龙虎榜 #每日复盘 #资金数据 #市场结构",
             "foot": FOOT_LAST},
        ],
    }
    if cross_rows:
        # summary 卡不排表格——交叉明细作为第 6 页内 table 与 rows 共存(spec 支持,参照公告日报 rows+kbox)
        spec["cards"][-1]["table"] = {"headers": ["个股", "连板", "净额"], "rows": cross_rows}
    return facts, spec


# ── 工程落盘与编排 ───────────────────────────────────────────────────────

# ── 板块温度卡(thermometer 子系统,2026-09-13;数据源 market_data.db) ───

_THERMO_DEFAULT_INSIGHT = ("温度计的反向读法:高温是人声鼎沸处,预期往往打得过满;"
                           "低温是无人问津时,研究的价值反而更高——温度计只测温,方向自己定")

_THERMO_FOOT = "数据来源:交易所公开行情与申万指数(经 thermometer 采集) · 仅供研究参考,不构成投资建议"
_THERMO_FOOT_LAST = _THERMO_FOOT + "。市场有风险,投资需谨慎。"


def _market_db_path() -> Path:
    return REPO_ROOT / "storage" / "database" / "market_data.db"


def _cold_diagnosis(con: sqlite3.Connection, d: str, r: dict) -> list[str]:
    """低温成因的数据事实标签(零观点纪律:只述事实,不下「见底/利空」判断).

    维度:资金(近5/20日主力净流入方向)/深度(近60日涨跌幅)/时长(近20日温度均值)/
    动能(5日温度变化)。标签零数字,不触数字闸。
    """
    lv, code = r["level"], r["index_code"]
    flow = con.execute(
        "SELECT trade_date, main_net_pct FROM sector_moneyflow_daily "
        "WHERE level=? AND index_code=? AND trade_date<=? "
        "ORDER BY trade_date DESC LIMIT ?", (lv, code, d, 20)).fetchall()
    flow5 = sum(x[1] or 0.0 for x in flow[:5])
    flow20 = sum(x[1] or 0.0 for x in flow)
    px = con.execute(
        "SELECT close FROM sw_daily WHERE ts_code=? AND trade_date<=? "
        "ORDER BY trade_date DESC LIMIT ?", (code, d, 61)).fetchall()
    ret60 = (px[0][0] / px[-1][0] - 1.0) if len(px) >= 61 and px[-1][0] else 0.0
    temps = con.execute(
        "SELECT AVG(temperature) FROM thermometer_sector "
        "WHERE level=? AND index_code=? AND trade_date<=?", (lv, code, d)).fetchone()
    tags: list[str] = []
    if flow20 < 0 and flow5 < 0:
        tags.append("大单资金持续流出")
    elif flow20 < 0 <= flow5:
        tags.append("资金止流出回暖")
    elif flow5 < 0 <= flow20:
        tags.append("近期资金转流出")
    if ret60 <= -0.15:
        tags.append("中期跌幅深")
    elif ret60 >= 0:
        tags.append("阶段反弹中")
    if temps and temps[0] is not None and temps[0] < 20:
        tags.append("长期低温")
    if r.get("delta_temp5") is not None:
        if r["delta_temp5"] > 0:
            tags.append("温度回升中")
        elif r["delta_temp5"] < 0:
            tags.append("温度仍下行")
    return tags[:3]


def fetch_thermo_bundle(day: str) -> dict:
    """day 为 dash 日期;读 thermometer_sector/market 当日快照 + 昨日温度(Δ1)."""
    d = day.replace("-", "")
    con = _ro_conn(_market_db_path())
    try:
        prev_row = con.execute(
            "SELECT MAX(trade_date) FROM thermometer_sector WHERE trade_date<?",
            (d,)).fetchone()
        prev_d = prev_row[0] if prev_row else None
        prev_map: dict[str, float] = {}
        if prev_d:
            prev_map = {r[0]: float(r[1] or 0.0) for r in con.execute(
                "SELECT index_code, temperature FROM thermometer_sector WHERE trade_date=?",
                (prev_d,)).fetchall()}
        sec = con.execute(
            "SELECT level, index_code, name, temperature, delta_temp5, hot_streak "
            "FROM thermometer_sector WHERE trade_date=? ORDER BY index_code", (d,)).fetchall()
        mkt = con.execute(
            "SELECT trend_dim, width_dim, volume_dim, flow_dim, sentiment_dim, "
            "temperature, regime_label FROM thermometer_market WHERE trade_date=?",
            (d,)).fetchone()
        if not sec or not mkt:
            raise DailyDataMissing(f"{day} 温度数据缺失(thermometer run 未完成?)")
        rows = []
        for r in sec:
            temp = float(r[3] or 0.0)
            prev_t = prev_map.get(r[1])
            rows.append({"level": r[0], "index_code": r[1], "name": r[2] or "-",
                         "temperature": temp,
                         "delta_temp1": (round(temp - prev_t, 1)
                                         if prev_t is not None else None),
                         "delta_temp5": (float(r[4]) if r[4] is not None else None),
                         "hot_streak": int(r[5] or 0)})
        l1 = sorted((r for r in rows if r["level"] == "L1"),
                    key=lambda r: -r["temperature"])
        l2 = sorted((r for r in rows if r["level"] == "L2"),
                    key=lambda r: -r["temperature"])
        l1_full = sorted((r for r in rows if r["level"] == "L1"),
                         key=lambda r: -r["temperature"])  # 按温度降序(用户拍板)
        cold = sorted(rows, key=lambda r: r["temperature"])[:5]  # L1+L2 最低温关注池
        for r in cold:  # 低温成因诊断(纯数据事实标签,判断留给读者)
            r["tags"] = _cold_diagnosis(con, d, r)
        from davis_analyzer.thermometer.scoring import rotation_signals
        rot = rotation_signals(con, d)
        rot_moves = rot["moves"][:8]
        return {
            "day": day, "prev_date": prev_d,
            "rotation": {"ac5": rot["ac5"], "n_moves": len(rot["moves"]),
                         "n_up": rot["n_up"], "n_down": rot["n_down"],
                         "moves": rot_moves},
            "l1": l1[:5], "l1_full": l1_full, "l2": l2[:5], "cold": cold,
            "market": {"temperature": float(mkt[5] or 0.0), "regime_label": mkt[6],
                       "dims": {"趋势": mkt[0], "宽度": mkt[1], "量能": mkt[2],
                                "资金": mkt[3], "情绪": mkt[4]}},
        }
    finally:
        con.close()


def thermo_insights(bundle: dict) -> list[str]:
    """按温度形态选至多两条盘后观察(反向语义:高温=拥挤预警,低温=冷清线索)."""
    picks: list[str] = []
    l1 = bundle.get("l1") or []
    mkt = bundle.get("market") or {}
    if l1:
        top = l1[0]
        bot = min(l1, key=lambda r: r.get("temperature", 0))
        if top["temperature"] - bot["temperature"] > 70:
            picks.append("冷热分化极端的日子,主线集中度比大盘涨跌更能定义这个市场——"
                         "结构行情里,板块间的温差比指数读数更值得看")
        if any((r.get("hot_streak") or 0) >= 3 for r in l1):
            picks.append("连续高温不是继续升温的理由,而是拥挤度的警报——"
                         "人声鼎沸处的预期,通常随温度升高而打得更满")
        if bot["temperature"] < 15:
            picks.append("温度垫底的板块,是当下的无人问津处——关注度的低谷"
                         "往往比热度的顶峰更值得花时间研究")
    if not picks:
        picks.append(_THERMO_DEFAULT_INSIGHT)
    return picks[:2]


def _thermo_num(x: float) -> str:
    """温度/分位 display:去尾零(与 facts 序列化一致)."""
    return f"{abs(float(x)):.1f}".rstrip("0").rstrip(".") or "0"


def _thermo_signed(x: float) -> tuple[str, str]:
    """带符号 display(升温用;0 亦带 +)."""
    v = _thermo_num(x)
    return v, f"{'-' if x < 0 else '+'}{v}"


# 温度五档色带(反向语义:红=拥挤风险,蓝=冷清机会;style 只用 background/color,
# hex 色值走数字闸掩码豁免,禁带 padding/radius 等数字属性)
_THERMO_BANDS: list[tuple[float, str, str]] = [
    (85.0, "#fecaca", "#7f1d1d"),  # 过热(深红字,视觉检对比度建议)
    (65.0, "#ffedd5", "#c2410c"),  # 偏热
    (35.0, "#f1f5f9", "#475569"),  # 中性
    (15.0, "#dbeafe", "#1d4ed8"),  # 偏冷
    (-1.0, "#bfdbfe", "#1e40af"),  # 冰点
]


def _temp_cell(temp: float, delta1: str | None = None) -> str:
    """温度色块单元格:整列上色(display:block 撑满列宽,等宽对齐),数字裸文本.

    delta1 提供时在温度下方内嵌较昨日小字(<small> 缩字,方向色)——
    一格看全「温度+变化」。style 纪律:禁数字属性(width/padding 等),
    width:100% 的 100 会撞数字闸;display:block 天然撑满 td 实现整格上色。
    """
    for th, bg, tx in _THERMO_BANDS:
        if temp >= th:
            bg_, tx_ = bg, tx
            break
    else:
        bg_, tx_ = _THERMO_BANDS[-1][1], _THERMO_BANDS[-1][2]
    delta_html = (f' <small style="color:#dc2626;">{delta1}</small>' if delta1 and delta1.startswith("+")
                  else f' <small style="color:#16a34a;">{delta1}</small>' if delta1
                  else "")
    return (f'<span style="display:block;background:{bg_};color:{tx_};'
            f'font-weight:bold;text-align:center;">{_thermo_num(temp)}{delta_html}</span>')


def build_thermo(day: str, bundle: dict) -> tuple[list[Fact], dict]:
    """动态五至六页:封面 / 温度全景 / 低温关注池(成因) / 轮动脉搏(有迁移才出) / 大盘五维 / 收束.

    全景行固定申万代码序(每天同一位置);温度格为五档色块(红=拥挤风险,
    蓝=冷清机会,反向语义);较昨日独立成页(固定同序,红升绿降)。
    紧凑排版:每行三组(板块|温度),31 板块 = 11 行,高度过 1440 溢出闸。
    数字闸:色块数字裸文本,依赖同值 facts 锚定;style 只用 background/color。
    """
    ref_sec = f"market_data.db:thermometer_sector@{day}"
    ref_mkt = f"market_data.db:thermometer_market@{day}"
    facts: list[Fact] = []
    mkt = bundle["market"]
    top1 = bundle["l1"][0]

    mkt_v = _thermo_num(mkt["temperature"])
    facts.append(_fact("mkt_temp", mkt_v, "", mkt_v, day, f"{ref_mkt}:temperature"))
    top_name = _digit_safe(str(top1["name"])) or "-"
    hot_v = _thermo_num(top1["temperature"])
    facts.append(_fact("hot_temp", hot_v, "", hot_v, day,
                       f"{ref_sec}:{top1['index_code']}.temperature"))
    # 长图首屏钩子用(六页 spec 不渲染,但 facts 层保持完备供长图数字闸锚定)
    streak_n = int(top1.get("hot_streak") or 0)
    facts.append(_fact("hot_streak", streak_n, "", f"{streak_n}天", day,
                       f"{ref_sec}:{top1['index_code']}.hot_streak"))

    def _row(r: dict, prefix: str, with_level: bool) -> tuple[dict, list[Fact]]:
        name = _digit_safe(str(r["name"])) or "-"
        tv = _thermo_num(r["temperature"])
        fids: list[Fact] = []
        cells = ([r["level"], name] if with_level else [name])
        cls = ["", ""] if with_level else [""]
        fids.append(_fact(f"{prefix}_temp", tv, "", tv, day,
                          f"{ref_sec}:{r['index_code']}.temperature"))
        cells.append(_temp_cell(r["temperature"]))
        cls.append("")
        if r.get("delta_temp1") is not None:
            dv, dd = _thermo_signed(r["delta_temp1"])
            fids.append(_fact(f"{prefix}_delta", dv, "", dd, day,
                              f"{ref_sec}:{r['index_code']}:vs_prev"))
            cells.append({"$fact": f"{prefix}_delta"})
            cls.append("up" if r["delta_temp1"] > 0
                       else ("down" if r["delta_temp1"] < 0 else ""))
        else:
            cells.append("—")
            cls.append("")
        return {"cells": cells, "cls": cls}, fids

    l1_full = bundle["l1_full"]
    cold_rows = []
    # 单页全景:每行三组(板块|温度),31 板块 = 11 行(余位补空)
    _CHIP_COLS = 4  # 芯片格:每行 4 板块,31 个 = 8 行

    def _chip_cell(r: dict, idx: int) -> str:
        """纵向芯片:板块名在上,温度色块(内嵌较昨日小字)在下,整格一个板块."""
        name = _digit_safe(str(r["name"])) or "-"
        tv = _thermo_num(r["temperature"])
        facts.append(_fact(f"p{idx}_temp", tv, "", tv, day,
                           f"{ref_sec}:{r['index_code']}.temperature"))
        dd = None
        if r.get("delta_temp1") is not None:
            dv, ds = _thermo_signed(r["delta_temp1"])
            facts.append(_fact(f"p{idx}_delta", dv, "", ds, day,
                               f"{ref_sec}:{r['index_code']}:vs_prev"))
            dd = ds if r["delta_temp1"] != 0 else f"±{dv}"
        return f"{name}<br>{_temp_cell(r['temperature'], dd)}"

    def _dense_rows() -> list[dict]:
        """温度降序四列芯片网格(用户拍板:按分数排序,一格看全温度+较昨日)."""
        out = []
        for gi in range(0, len(l1_full), _CHIP_COLS):
            cells = [_chip_cell(r, gi + j)
                     for j, r in enumerate(l1_full[gi:gi + _CHIP_COLS], 1)]
            while len(cells) < _CHIP_COLS:
                cells.append("")
            out.append({"cells": cells, "cls": [""] * len(cells)})
        return out

    rows_temp = _dense_rows()
    for i, r in enumerate(bundle["cold"], 1):
        name = _digit_safe(str(r["name"])) or "-"
        tv = _thermo_num(r["temperature"])
        facts.append(_fact(f"cd{i}_temp", tv, "", tv, day,
                           f"{ref_sec}:{r['index_code']}.temperature"))
        dd = None
        if r.get("delta_temp1") is not None:
            dv, ds = _thermo_signed(r["delta_temp1"])
            facts.append(_fact(f"cd{i}_delta", dv, "", ds, day,
                               f"{ref_sec}:{r['index_code']}:vs_prev"))
            dd = ds
        lv_label = "一级" if r["level"] == "L1" else "二级"
        cold_rows.append({"cells": [f"{lv_label}·{name}",
                                    _temp_cell(r["temperature"], dd),
                                    "<br>".join(r.get("tags") or [])],
                          "cls": ["", "", ""]})

    # 轮动脉搏页(有档位迁移才出页)
    rot_rows = []
    rot = bundle.get("rotation") or {}
    # 轮动计数 facts(长图钩子/文案用;六页 spec 不引用,仅登记保数字闸完备)
    facts.append(_fact("rot_n", int(rot.get("n_moves") or 0), "个",
                       f"{int(rot.get('n_moves') or 0)}个", day,
                       f"market_data.db:thermometer_sector@{day}:rotation:n_moves"))
    facts.append(_fact("rot_up", int(rot.get("n_up") or 0), "",
                       str(int(rot.get("n_up") or 0)), day,
                       f"market_data.db:thermometer_sector@{day}:rotation:n_up"))
    facts.append(_fact("rot_down", int(rot.get("n_down") or 0), "",
                       str(int(rot.get("n_down") or 0)), day,
                       f"market_data.db:thermometer_sector@{day}:rotation:n_down"))
    for i, m in enumerate(rot.get("moves") or [], 1):
        lv_label = "一级" if m["level"] == "L1" else "二级"
        name = _digit_safe(str(m["name"])) or "-"
        d5v = _thermo_num(abs(m["d5"]))
        d5d = f"{'+' if m['d5'] > 0 else '-'}{d5v}" if m["d5"] != 0 else f"±{d5v}"
        facts.append(_fact(f"rt{i}_d5", d5v, "", d5d, day,
                           f"{ref_sec}:{m['index_code']}:d5"))
        rot_rows.append({"cells": [f"{lv_label}·{name}",
                                   f"{m['from_band']}→{m['to_band']}",
                                   {"$fact": f"rt{i}_d5"}],
                         "cls": ["", "", "up" if m["d5"] > 0
                                 else ("down" if m["d5"] < 0 else "")]})

    # 大盘五维(分位 0-1 逐项 facts;NaN→文字占位)
    dim_rows = []
    for dim_name, v in mkt["dims"].items():
        if v is None:
            dim_rows.append({"cells": [dim_name, "数据待齐"], "cls": ["", ""]})
            continue
        fid = f"dim_{dim_name}"
        dv = _thermo_num(v)
        facts.append(_fact(fid, dv, "", dv, day, f"{ref_mkt}:{fid}"))
        dim_rows.append({"cells": [dim_name, {"$fact": fid}],
                         "cls": ["", "up" if float(v) >= 0.65 else
                                 (" " if float(v) >= 0.35 else "")]})

    legend = "色越红越拥挤(风险提醒) 越蓝越冷清(关注线索) · 按温度降序 · 色块内为温度与较昨日变化"
    spec = {
        "group": "每日复盘",
        "cards": [
            {"type": "cover", "theme": "red", "name": "01_封面",
             "tag_top": "板块温度计 · 每日数据复盘",
             "title": "今天的板块温度<br>全景一张图",
             "sub": f"大盘{mkt['regime_label']} · 最热{top_name}<br>{day} 交易数据整理",
             "stats": [
                 {"v": {"$fact": "mkt_temp"}, "k": "大盘温度"},
                 {"v": {"$fact": "hot_temp"}, "k": f"最热一级·{top_name}"}],
             "tags": "#板块温度计 #每日复盘 #市场结构 #资金流向",
             "foot": _THERMO_FOOT},
            {"type": "table", "theme": "cream", "name": "02_温度全景",
             "tag_top": "温度全景", "tag_color": "#ea580c",
             "title": "一级行业温度全景",
             "subtitle": legend,
             "table": {"headers": ["", "", "", ""], "rows": rows_temp},
             "foot": _THERMO_FOOT},
            {"type": "table", "theme": "green", "name": "03_低温关注池", "first_left": True,
             "tag_top": "低温关注池", "tag_color": "#16a34a",
             "title": "无人问津处 · 低温板块与成因",
             "subtitle": "全市场温度最低方向 · 成因为数据事实标签,判断留给读者",
             "table": {"headers": ["板块", "温度", "低温成因"], "rows": cold_rows},
             "foot": _THERMO_FOOT},
            {"type": "table", "theme": "blue", "name": "04_轮动脉搏", "first_left": True,
             "tag_top": "轮动脉搏", "tag_color": "#2563eb",
             "title": "近五日温度轮动",
             "subtitle": "档位迁移=左侧补涨与高位退潮 · 按温度变化幅度排序",
             "table": {"headers": ["板块", "档位迁移", "五日温度变化"], "rows": rot_rows},
             "foot": _THERMO_FOOT},
            {"type": "table", "theme": "lavender", "name": "05_大盘五维", "first_left": True,
             "tag_top": "大盘五维", "tag_color": "#7c3aed",
             "title": "大盘温度的五维构成",
             "subtitle": "各维为自身历史分位",
             "table": {"headers": ["维度", "历史分位"], "rows": dim_rows},
             "foot": _THERMO_FOOT},
            {"type": "summary", "theme": "lavender", "name": "06_收束",
             "tag_top": "数据说明", "tag_color": "#0f172a",
             "title": "温度是结构数据",
             "subtitle": "不是操作清单",
             "rows": [
                 {"desc": "<b>高温</b> → 人声鼎沸处,预期打得过满,是风险提醒"},
                 {"desc": "<b>低温</b> → 无人问津时,关注度低谷,是研究线索"},
                 {"desc": "<b>较昨日</b> → 温度日变化,升温降温都比温度本身更值得盯"}],
             "kbox": {"date": "盘后观察", "color": "blue",
                      "html": "<br>".join(thermo_insights(bundle))},
             "tags": "#板块温度计 #每日复盘 #市场结构 #资金流向",
             "foot": _THERMO_FOOT_LAST},
        ],
    }
    return facts, spec


def write_project(projects_root: Path, topic: str, facts: list[Fact], spec: dict) -> Path:
    proj = projects_root / PENDING_DIR / topic
    (proj / "output").mkdir(parents=True, exist_ok=True)
    save_facts(proj / "facts.json", facts)
    (proj / "cards.spec.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return proj


def _ledger_db(ledger_db: Path | None) -> Path | None:
    """台账库路径;测试经 CARDGEN_LEDGER_DB 重定向,避免污染真实 content_cards.db。"""
    env = os.environ.get("CARDGEN_LEDGER_DB")
    return Path(env) if env else ledger_db


def generate(kind: str, day: str, projects_root: Path, ledger_db: Path | None,
             stockhot_db: Path | None = None) -> tuple[Path, str, ValidateReport]:
    """kind ∈ {'ladder','lhb'};完整性自检→build→write→register→validate→log+set_status。

    返回 (project_dir, topic, report)。缺数据 raise DailyDataMissing。
    stockhot_db 缺省取 env CARDGEN_STOCKHOT_DB 或 DEFAULT_STOCKHOT_DB。
    """
    db = stockhot_db or Path(os.environ.get("CARDGEN_STOCKHOT_DB", DEFAULT_STOCKHOT_DB))
    if kind == "thermo":
        # 数据源是 market_data.db 的 thermometer 表,不走 stockhot bundle
        facts, spec = build_thermo(day, fetch_thermo_bundle(day))
        topic = f"板块温度/{day}"
    elif kind == "screener":
        # 数据源是影子名单文件(18:30/18:40 cron 产出),19:00 槽单独跑
        facts, spec = build_screener(day, fetch_screener_bundle(day))
        topic = f"筛选器温度计/{day}"
    elif kind == "ladder":
        bundle = fetch_day_bundle(db, day)
        facts, spec = build_ladder(day, bundle)
        topic = f"连板天梯/{day}"
    elif kind == "lhb":
        bundle = fetch_day_bundle(db, day)
        facts, spec = build_lhb(day, bundle)   # Task 5 实现;先放占位 raise
        topic = f"龙虎榜/{day}"
    else:
        raise ValueError(f"kind 须 ladder/lhb/thermo: {kind}")
    # 同日重跑保护:rendered/queued 的工程禁止静默覆写(须 --bump 或先删工程);drafting/validated 照常
    guard_conn = ledger.connect(_ledger_db(ledger_db))
    try:
        existing = ledger.get_card(guard_conn, topic)
        if existing and existing["status"] in ("rendered", "queued"):
            raise RuntimeError(
                f"topic {topic} 已是 {existing['status']} 状态,拒绝覆写——请用 --bump 或先删除工程")
    finally:
        guard_conn.close()
    proj = write_project(projects_root, topic, facts, spec)
    conn = ledger.connect(_ledger_db(ledger_db))
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


# ── 筛选器温度计(2026-09-13 上线):G2/困境反转影子名单规模 = 市场情绪温度 ──
# 数据源:logs/g2_signals/g2_list_*.json + logs/distress_signals/distress_list_*.json
# (盘后 18:30/18:40 cron 自动产出);卡片 19:00 槽单独跑(名单就绪后),不入 all。
# 合规口径:只报筛选器通过数量与状态词,零个股代码/零操作建议。
_SCREENER_DIRS = {"g2": REPO_ROOT / "logs" / "g2_signals",
                  "distress": REPO_ROOT / "logs" / "distress_signals"}
_REGIME_CN = {"bull": "牛市", "bear": "熊市", "neutral": "中性", "mixed": "混合"}


def fetch_screener_bundle(day: str, dirs: dict | None = None) -> dict:
    """读两台筛选器的最新名单(数据日 ≤ day)+ 全部历史;缺文件 raise DailyDataMissing."""
    ddirs = dirs or _SCREENER_DIRS
    hist: dict[str, dict[str, dict]] = {"g2": {}, "distress": {}}
    for k, d in ddirs.items():
        prefix = "g2" if k == "g2" else "distress"
        for p in Path(d).glob(f"{prefix}_list_*.json"):
            m = re.search(r"_(\d{8})\.json$", p.name)
            if not m:
                continue
            try:
                hist[k][m.group(1)] = json.loads(p.read_text())
            except Exception:  # noqa: BLE001 —— 损坏文件跳过,取更早的
                continue
    d8_limit = day.replace("-", "")
    latest = {}
    for k, files in hist.items():
        cand = sorted([d for d in files if d <= d8_limit], reverse=True)
        if not cand:
            raise DailyDataMissing(f"无 {k} 名单文件(≤{day})——影子导出 cron(18:30/18:40)未产出?")
        latest[k] = files[cand[0]]
    return {"latest": latest, "history": hist}


def screener_insights(bundle: dict | None) -> list[str]:
    """按名单形态机械选用(零数字/敏感词零命中,锚点见测试);bundle 为空时返回空。"""
    if not bundle:
        return []
    g2 = bundle.get("latest", {}).get("g2", {})
    n = int(g2.get("n_pass", 0) or 0)
    if n == 0:
        return ["动量筛选器空名单的正确读法:不是坏了,是它明确表态——当前行情里它一只都不想碰,"
                "防守本身就是输出"]
    return ["筛选器数量伸缩的正确读法:放行变多说明强趋势标的在变密,变少说明门槛正在拦人——"
            "看变化方向比看单日数量有用"]


def build_screener(day: str, bundle: dict) -> tuple[list[Fact], dict]:
    """筛选器温度计卡(封面+当日读数+近五次轨迹+收束)。facts 全锚名单文件指纹。"""
    g2j = bundle["latest"]["g2"]
    dsj = bundle["latest"]["distress"]
    as_g2, as_ds = g2j["as_of"], dsj["as_of"]
    ref_g2 = f"logs/g2_signals/g2_list_{as_g2}.json"
    ref_ds = f"logs/distress_signals/distress_list_{as_ds}.json"
    g2_n = int(g2j.get("n_pass", 0) or len(g2j.get("list", [])))
    ds_n = int(dsj.get("n_pass", 0) or len(dsj.get("list", [])))
    regime = _REGIME_CN.get(str(g2j.get("regime", "")), "未知")

    facts: list[Fact] = [
        _fact("g2_pass_n", g2_n, "只", f"{g2_n}只", day, ref_g2 + ":n_pass"),
        _fact("ds_pass_n", ds_n, "只", f"{ds_n}只", day, ref_ds + ":n_pass"),
    ]

    # 近五次轨迹(两台流并集按数据日降序;行标签用中文序数,零阿拉伯数字)
    dates_union = sorted(set(bundle["history"]["g2"]) | set(bundle["history"]["distress"]),
                         reverse=True)[:5]
    labels = ["最新", "次新", "第三档", "第四档", "第五档"]
    track_rows, empty_days = [], 0
    for i, d8 in enumerate(dates_union):
        cells, cls = [labels[i]], ["", "", ""]
        for j, (k, fidp) in enumerate((("g2", "hist_g2"), ("distress", "hist_ds"))):
            f = bundle["history"][k].get(d8)
            if f is None:
                cells.append("—")
                continue
            n = int(f.get("n_pass", 0) or len(f.get("list", [])))
            fid = f"{fidp}_{i + 1}"
            facts.append(_fact(fid, n, "只", f"{n}只", day,
                               f"logs/{'g2_signals/g2' if k == 'g2' else 'distress_signals/distress'}_list_{f.get('as_of', d8)}.json:n_pass"))
            cells.append({"$fact": fid})
            if k == "g2" and n == 0:
                empty_days += 1
        track_rows.append({"cells": cells, "cls": cls})
    if not track_rows:
        track_rows = [{"cells": ["暂无历史", "—", "—"], "cls": ["", "", ""]}]
    facts.append(_fact("g2_empty_5", empty_days, "次", f"{empty_days}次", day,
                       ref_g2 + ":近五次空名单统计"))

    foot = f"名单基准日:动量 {as_g2[:4]}-{as_g2[4:6]}-{as_g2[6:]} / 困境反转 {as_ds[:4]}-{as_ds[4:6]}-{as_ds[6:]} · " + FOOT
    g2_state = "空名单 · 防守姿态" if g2_n == 0 else "正常放行"
    ds_state = "空名单 · 无深度回撤标的" if ds_n == 0 else "正常放行"

    spec = {
        "group": "每日复盘",
        "cards": [
            {"type": "cover", "theme": "blue", "name": "01_封面",
             "tag_top": "筛选器温度计 · 每日数据观察",
             "title": "两台量化筛选器<br>今天放行了几只",
             "sub": "动量闸与困境反转闸 · 空名单=防守语义<br>盘后管线自动生成",
             "stats": [
                 {"v": {"$fact": "g2_pass_n"}, "k": "动量放行(只)"},
                 {"v": {"$fact": "ds_pass_n"}, "k": "困境反转放行(只)"},
                 {"v": {"$fact": "g2_empty_5"}, "k": "近五次空名单(次)"}],
             "tags": "#筛选器温度计 #每日复盘 #量化研究 #市场情绪",
             "foot": foot},
            {"type": "table", "theme": "cream", "name": "02_当日读数", "first_left": True,
             "tag_top": "当日读数", "tag_color": "#ea580c",
             "title": "今日两台筛选器的读数",
             "subtitle": "动量口径=强趋势门槛 · 困境口径=深回撤+低估值+景气拐点",
             "table": {"headers": ["筛选器", "放行", "状态"], "rows": [
                 {"cells": ["动量筛选器", {"$fact": "g2_pass_n"}, g2_state],
                  "cls": ["", "" if g2_n else "up", ""]},
                 {"cells": ["困境反转筛选器", {"$fact": "ds_pass_n"}, ds_state], "cls": ["", "", ""]},
                 {"cells": ["市场状态(模型判读)", regime, "名单规模的背景板"], "cls": ["", "", ""]},
             ]},
             "foot": foot},
            {"type": "table", "theme": "green", "name": "03_近五次轨迹", "first_left": True,
             "tag_top": "温度轨迹", "tag_color": "#16a34a",
             # 期数用中文数字(2026-09-18 事故修复: 阿拉伯数字进卡面触发数字闸,
             # 0916/0917 连续两天 validate 拒绝未渲染——发稿层数字纪律同样适用于卡面标题)
             "title": ("近五次放行数量轨迹" if len(dates_union) >= 5
                       else "放行数量轨迹 · 积累中(第" +
                       ("一二三四五六七八九十"[len(dates_union) - 1]
                        if 2 <= len(dates_union) <= 10 else "多") + "期)"
                       if len(dates_union) >= 2
                       else "放行数量轨迹 · 首期"),
             "subtitle": "伸缩方向比单日读数更有信息量(逐日自动累积,基准日见脚注)",
             "table": {"headers": ["时点", "动量放行", "困境反转"], "rows": track_rows},
             "foot": foot},
            {"type": "summary", "theme": "lavender", "name": "04_收束",
             "tag_top": "读法说明", "tag_color": "#0f172a",
             "title": "温度计是结构数据",
             "subtitle": "不是操作清单",
             "rows": [
                 {"desc": "<b>动量名单变短</b> → 强趋势品种变稀,筛选器进入谨慎档;"
                          "空名单是明确防守表态,不是故障"},
                 {"desc": "<b>困境名单变长</b> → 深回撤且出现景气拐点迹象的品种在增多,"
                          "市场在挤泡沫的另一面"},
                 {"desc": "<b>两台互为镜像</b> → 一热一冷同时读,才是完整的市场温度"}],
             "foot": foot + "。市场有风险,投资需谨慎。"},
        ],
    }
    return facts, spec
