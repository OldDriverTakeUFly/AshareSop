# scripts/daily_bulletin.py
"""公告日报管线(2026-09-01):巨潮当日公告 → 分类清单 markdown,供「公告+解读」卡片选题。

用法: .venv/bin/python scripts/daily_bulletin.py [--date 20260901] [--out docs/小红书卡片/公告日报]
品类边界(合规闸裁定):回购/定增/收购=可做卡;增持/减持类=敏感词命中,只列清单不做卡。
watchlist 可维护:重点池 + 已覆盖标的(研报/卡片工程)。
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UA = {"User-Agent": "Mozilla/5.0"}

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


def _org_map() -> dict[str, str]:
    raw = urllib.request.urlopen(urllib.request.Request(
        "http://www.cninfo.com.cn/new/data/szse_stock.json", headers=UA), timeout=15).read()
    return {s["code"]: s["orgId"] for s in json.loads(raw)["stockList"]}


def fetch_day(code: str, org: str, day: str) -> list[dict]:
    col = "sse" if code.startswith(("6", "9")) else "szse"
    data = urllib.parse.urlencode({
        "pageNum": 1, "pageSize": 30, "column": col, "tabName": "fulltext",
        "stock": f"{code},{org}", "searchkey": "", "category": "", "seDate": ""}).encode()
    req = urllib.request.Request("http://www.cninfo.com.cn/new/hisAnnouncement/query",
                                 data=data, headers=UA)
    anns = json.loads(urllib.request.urlopen(req, timeout=15).read()).get("announcements") or []
    def _d(a: dict) -> str:
        ts = a.get("announcementTime")
        try:
            return datetime.fromtimestamp(int(ts) / 1000).strftime("%Y%m%d")
        except (TypeError, ValueError):
            return ""
    return [a for a in anns if _d(a) == day and a.get("adjunctUrl")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--out", default=str(REPO / "docs/小红书卡片/公告日报"))
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
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"{out} | 可做卡 {n_do} / 敏感 {n_skip}")


if __name__ == "__main__":
    main()
