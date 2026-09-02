# scripts/daily_bulletin.py
"""公告日报管线(2026-09-01):巨潮当日公告 → 分类清单 markdown,供「公告+解读」卡片选题。
2026-09-01 晚新增:全市场大事件雷达模式(默认开启)——同 API 按 seDate 拉全市场当日公告,
品类过滤 + (公司×品类)去重 + 标题信号打分,输出 Top N。定位:研报层扩品选题入口,不做卡。
2026-09-03 新增:--feishu 自动推送——生成后把 watchlist+雷达节推到飞书群(复用
stockhot/notification/feishu_bot;幂等锁 logs/.radar_feishu_push/{date}.ok;零公告日静默跳过)。
systemd user timer radar-feishu.timer 每日 21:35 触发(与盘后 18:30 飞书推送平行)。

用法: .venv/bin/python scripts/daily_bulletin.py [--date 20260901] [--out docs/小红书卡片/未发布/公告日报]
                [--no-radar] [--radar-top 15] [--feishu [--force] | --dry]
品类边界(合规闸裁定):回购/定增/收购=可做卡;增持/减持类=敏感词命中,只列清单不做卡。
watchlist 可维护:重点池 + 已覆盖标的(研报/卡片工程)。
雷达已知边界:巨潮该接口不含北交所公告;金额多数不在标题里,打分以事件类型信号为主。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UA = {"User-Agent": "Mozilla/5.0"}
QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
PAGE_SIZE = 30  # API 硬顶,实测 100/300/500 均被压回 30

# 代码池:已覆盖标的 + 大盘蓝筹公告样本池(按需增删)
WATCH = {
    "603683": "晶华新材", "301526": "国际复材", "603086": "先达股份",
    "600183": "生益科技", "688519": "南亚新材", "605589": "圣泉集团", "601208": "东材科技",
    "600276": "恒瑞医药", "603259": "药明康德", "688235": "百济神州", "688506": "百利天恒",
    "002230": "科大讯飞", "000425": "徐工机械", "002870": "香山股份",
}
RULES = [  # (分类, 关键词正则, 是否可做卡)
    ("回购", r"回购", True),
    ("定增/募资", r"向特定对象发行|募集资金|定增", True),
    ("收购/重组", r"收购|重大资产重组|停牌", True),
    ("业绩", r"业绩预告|业绩快报|预增|预减", True),
    ("增持", r"增持", False),  # 敏感词,只列不做卡
    ("减持", r"减持|询价转让", False),
]
# 雷达排除:负面/排雷类公告不是扩品选题,命中即跳过(只计数)。
# 注意不用裸「调查」——会误杀 M&A 配套的尽职调查报告。
RADAR_SKIP = re.compile(r"终止上市|退市|无法在法定期限内|立案|处罚|警示函|监管函|纪律处分|违法|责令")
# 雷达静音:存量募集资金管理类日常公告(三方监管协议/置换/现金管理等),非事件,只计数
RADAR_MUTE = re.compile(r"募集资金置换|置换预先投入|三方监管协议|管理及使用制度|存放与使用|存放与实际|现金管理|闲置募集资金|超募资金")
# 雷达标题信号:(加分, 标签, 正则);组内取首个命中的标签,分数累加
RADAR_SIGNALS = [
    (4, "重组", re.compile(r"重大资产重组|发行股份.{0,8}购买资产")),
    (3, "停牌", re.compile(r"停牌")),
    (3, "首次回购", re.compile(r"首次回购|回购报告书")),
    (2, "定增预案", re.compile(r"向特定对象发行.{0,12}预案")),
    (2, "发行完成", re.compile(r"发行情况报告书")),
    (1, "注册稿", re.compile(r"募集说明书|注册稿")),
    (1, "预增", re.compile(r"预增")),
    (-3, "进展", re.compile(r"进展|期限届满|实施完成|结果")),
    (-2, "终止", re.compile(r"终止|取消|解除|撤回")),
]
_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*(亿|万)")


def _post(payload: dict) -> dict:
    req = urllib.request.Request(QUERY_URL, data=urllib.parse.urlencode(payload).encode(), headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def _org_map() -> dict[str, str]:
    raw = urllib.request.urlopen(urllib.request.Request(
        "http://www.cninfo.com.cn/new/data/szse_stock.json", headers=UA), timeout=15).read()
    return {s["code"]: s["orgId"] for s in json.loads(raw)["stockList"]}


def fetch_day(code: str, org: str, day: str) -> list[dict]:
    col = "sse" if code.startswith(("6", "9")) else "szse"
    anns = _post({"pageNum": 1, "pageSize": 30, "column": col, "tabName": "fulltext",
                  "stock": f"{code},{org}", "searchkey": "", "category": "",
                  "seDate": ""}).get("announcements") or []
    def _d(a: dict) -> str:
        ts = a.get("announcementTime")
        try:
            return datetime.fromtimestamp(int(ts) / 1000).strftime("%Y%m%d")
        except (TypeError, ValueError):
            return ""
    return [a for a in anns if _d(a) == day and a.get("adjunctUrl")]


def fetch_market_day(day: str) -> tuple[list[dict], int]:
    """按 seDate 拉全市场当日公告(沪深,不含北交所),分页 30 条/页,页失败重试 1 次。"""
    se = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    items: list[dict] = []
    fails = 0
    for page in range(1, 61):  # 上限 60 页=1800 条,超限截断并在统计注明
        try:
            r = _post({"pageNum": page, "pageSize": PAGE_SIZE, "column": "szse",
                       "tabName": "fulltext", "stock": "", "searchkey": "",
                       "category": "", "seDate": f"{se}~{se}"})
        except Exception:
            try:
                time.sleep(1)
                r = _post({"pageNum": page, "pageSize": PAGE_SIZE, "column": "szse",
                           "tabName": "fulltext", "stock": "", "searchkey": "",
                           "category": "", "seDate": f"{se}~{se}"})
            except Exception:
                fails += 1
                continue
        anns = [a for a in (r.get("announcements") or []) if a.get("adjunctUrl")]
        if not anns:
            break
        items += anns
        if page >= int(r.get("totalpages") or 0):
            break
        time.sleep(0.2)
    return items, fails


def _title_amount_yi(title: str) -> float:
    """标题里的最大金额,折成亿;无金额返回 0。"""
    best = 0.0
    for m in _AMOUNT.finditer(title):
        val = float(m.group(1))
        if m.group(2) == "万":
            val /= 10000
        best = max(best, val)
    return best


def radar_section(items: list[dict], top_n: int, fails: int) -> tuple[list[str], dict]:
    """全市场公告 → 品类过滤/负面排除 → (公司×品类)去重 → 标题信号打分 → Top N 行。"""
    stats: dict = {"total": len(items), "skip_neg": 0, "skip_mute": 0, "sensitive": 0, "fails": fails}
    groups: dict[tuple[str, str], list[tuple[int, list[str], dict]]] = {}
    for a in items:
        title = a.get("announcementTitle", "")
        if RADAR_SKIP.search(title):
            stats["skip_neg"] += 1
            continue
        if RADAR_MUTE.search(title):
            stats["skip_mute"] += 1
            continue
        for cat, pat, doable in RULES:
            if not re.search(pat, title):
                continue
            if not doable:
                stats["sensitive"] += 1
                break
            score, tags = 0, []
            for pt, tag, cre in RADAR_SIGNALS:
                if cre.search(title):
                    score += pt
                    tags.append(tag)
            amt = _title_amount_yi(title)
            if amt >= 50:
                score += 4
                tags.append("≥50亿")
            elif amt >= 20:
                score += 3
                tags.append("≥20亿")
            elif amt >= 10:
                score += 2
                tags.append("≥10亿")
            elif amt > 0:
                score += 1
                tags.append(f"{amt:g}亿")
            groups.setdefault((a.get("secCode", ""), cat), []).append((score, tags, a))
            stats.setdefault(f"grp_{cat}", set()).add(a.get("secCode", ""))
            break
    rows = []
    for (code, cat), members in groups.items():
        score, tags, a = max(members, key=lambda m: m[0])
        if len(members) > 1:
            score += min(len(members) - 1, 3)
            tags.append(f"共{len(members)}条")
        rows.append((score, cat, code, a, tags))
    rows.sort(key=lambda r: -r[0])
    lines = ["## 全市场大事件雷达(Top %d)" % top_n,
             "> 定位:研报层扩品选题入口——大事件→补短研报→转卡片(方法论§九.3);本节不做卡。",
             "> 信号说明:标题级打分(重组/停牌/首次回购>预案>注册稿>进展);金额多不在标题,以事件类型为主。"]
    for score, cat, code, a, tags in rows[:top_n]:
        title = a.get("announcementTitle", "")
        if code in WATCH:
            tags = tags + ["已覆盖"]
        tag_s = "|".join(tags) if tags else "-"
        lines.append(f"- [{cat}] {a.get('secName')}({code}) {title} [{tag_s}]")
        lines.append(f"  http://static.cninfo.com.cn/{a['adjunctUrl']}")
    grp_stat = " ".join(f"{cat}{len(stats.get('grp_' + cat, ()))}组"
                        for cat, _pat, doable in RULES if doable)
    lines.append("")
    lines.append(f"(全市场当日公告 {stats['total']} 条;负面/排雷类跳过 {stats['skip_neg']} 条,"
                 f"存量资金管理类跳过 {stats['skip_mute']} 条;"
                 f"增减持等敏感品类 {stats['sensitive']} 条不列明细;{grp_stat})"
                 + (f";⚠️拉取失败 {fails} 页,覆盖可能不全" if fails else ""))
    return lines, stats


# ── 飞书自动推送(2026-09-03):复用 stockhot 通道,幂等锁与 push_eod_feishu 同款 ──
_PUSH_LOCK_DIR = REPO / "logs" / ".radar_feishu_push"


def _pushed(day: str) -> bool:
    return (_PUSH_LOCK_DIR / f"{day}.ok").exists()


def _mark_pushed(day: str) -> None:
    _PUSH_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    (_PUSH_LOCK_DIR / f"{day}.ok").write_text(
        f"pushed at {datetime.now().isoformat()}\n", encoding="utf-8")


def build_feishu_text(day: str, lines: list[str], n_do: int, n_skip: int) -> str:
    """md 行 → 飞书纯文本:去 # 标题/> 引语/合计/选题规则行,保留条目与 PDF 链接。"""
    keep = [ln for ln in lines[2:]
            if ln.strip() and not ln.startswith("> ")
            and not ln.startswith(("合计", "选题规则"))]
    wd = "一二三四五六日"[datetime.strptime(day, "%Y%m%d").weekday()]
    head = [f"📡 全市场公告雷达 {day[:4]}-{day[4:6]}-{day[6:]} 周{wd}",
            f"watchlist 可做卡 {n_do} / 敏感 {n_skip};雷达 Top 与原文链接 ↓"]
    return "\n".join(head + keep + ["", "🤖 每日 21:35 自动推送 · 公告雷达=研报选题入口,不做卡"])


def push_radar_feishu(day: str, lines: list[str], n_do: int, n_skip: int, *, force: bool) -> int:
    """推送日报摘要到飞书群。已推过/未配置返回 0,发送失败返回 1(md 已落盘不受影响)。"""
    if not force and _pushed(day):
        print(f"{day} 已推送过,跳过(--force 可重推)")
        return 0
    import sys as _sys
    if str(REPO) not in _sys.path:
        _sys.path.insert(0, str(REPO))  # scripts/ 直跑时让 stockhot 包可导入
    try:
        from stockhot.core.logging import logger  # noqa: F401  文件 sink,让飞书日志可追溯
    except Exception:
        pass
    from stockhot.notification.feishu_bot import get_feishu_notifier
    notifier = get_feishu_notifier()
    if notifier is None:
        print("飞书未配置(FEISHU_* 缺失),跳过推送")
        return 0
    text = build_feishu_text(day, lines, n_do, n_skip)
    try:
        asyncio.run(notifier.send_text(text))
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 飞书推送失败: {type(e).__name__}: {e}")
        return 1
    _mark_pushed(day)
    print(f"{day} ✓ 已推送飞书({len(text)} 字符)")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--out", default=str(REPO / "docs/小红书卡片/未发布/公告日报"))
    ap.add_argument("--no-radar", action="store_true", help="只跑 watchlist,不跑全市场雷达")
    ap.add_argument("--radar-top", type=int, default=15, help="雷达 Top N,默认 15")
    ap.add_argument("--feishu", action="store_true", help="生成后推送飞书群(stockhot 通道,按日幂等)")
    ap.add_argument("--force", action="store_true", help="忽略当日推送锁,强制重推")
    ap.add_argument("--dry", action="store_true", help="只打印推送文本不发送(联调用)")
    args = ap.parse_args()
    day = args.date
    out = Path(args.out) / f"{day[:4]}-{day[4:6]}-{day[6:]}.md"
    out.parent.mkdir(parents=True, exist_ok=True)

    orgs = _org_map()
    lines = [f"# 公告日报 {day[:4]}-{day[4:6]}-{day[6:]}", ""]
    n_do = n_skip = 0
    for code, name in WATCH.items():
        try:
            anns = fetch_day(code, orgs.get(code, ""), day)
        except Exception as e:  # noqa: BLE001
            lines.append(f"- ⚠️ {name}({code}) 拉取失败: {e!r:.60}")
            continue
        for a in anns:
            title = a.get("announcementTitle", "")
            for cat, pat, doable in RULES:
                if re.search(pat, title):
                    mark = "✅可做卡" if doable else "⛔敏感品类(只列不做)"
                    url = f"http://static.cninfo.com.cn/{a['adjunctUrl']}"
                    lines.append(f"- [{cat}] {name}({code}) {title} {mark}")
                    lines.append(f"  {url}")
                    n_do += doable
                    n_skip += (not doable)
                    break
    lines += ["", f"合计:可做卡 {n_do} 条,敏感品类 {n_skip} 条,watchlist {len(WATCH)} 只。",
              "选题规则:优先「金额大+动作落地+与已覆盖产业链相关」;增减持类已由合规闸否决为卡片品类。"]

    summary = f"{out} | 可做卡 {n_do} / 敏感 {n_skip}"
    radar_stats: dict = {}
    if not args.no_radar:
        items, fails = fetch_market_day(day)
        r_lines, radar_stats = radar_section(items, args.radar_top, fails)
        lines += [""] + r_lines
        summary += f" | 雷达Top{args.radar_top}"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(summary)

    if args.dry or args.feishu:
        if radar_stats.get("total", 0) == 0 and n_do == 0:
            print(f"{day} 全市场零公告(休市?),跳过飞书推送")
            return
        if args.dry:
            print("---- feishu dry run ----")
            print(build_feishu_text(day, lines, n_do, n_skip))
            return
        raise SystemExit(push_radar_feishu(day, lines, n_do, n_skip, force=args.force))


if __name__ == "__main__":
    main()
