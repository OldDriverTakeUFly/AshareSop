#!/usr/bin/env python3
# daily_longpic.py —— 每日复盘卡(连板天梯/龙虎榜)长图渲染器(2026-09-18)
# 消费当日工程 cards.spec.json + facts.json(物化 $fact) → 750px html → playwright 2x 长图
# 与 cardgen 短卡管线并行,不改 daily.py/daily_market_cards/card_factory;数字同源 facts(stockhot 指纹)。
# 用法: .venv/bin/python scripts/daily_longpic.py --kind {ladder,lhb} [--day YYYYMMDD]
from __future__ import annotations

import argparse
import asyncio
import html as html_mod
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path("/home/leo/Projects/CodeAgentDashboard")
CARDS_ROOT = ROOT / "docs/小红书卡片/未发布"
KIND_MAP = {"ladder": "连板天梯", "lhb": "龙虎榜"}

THEMES = {
    "ladder": {
        "bg": "#0f1014", "card": "#191b22", "border": "#343846", "text": "#f2f3f7",
        "dim": "#9aa0b5", "accent1": "#ff4d4f", "accent2": "#ffd166",
        "tagbg": "#3a1d1f", "tagfg": "#ff9c9c", "pos": "#ff6b6b", "neg": "#4ade80", "th": "#ff9c9c",
    },
    "ladder_c": {  # 候选C:与龙虎榜同深蓝底,红强调(家族感方案)
        "bg": "#0b1026", "card": "#141b40", "border": "#2b3775", "text": "#eaf0ff",
        "dim": "#8d97c9", "accent1": "#ff5a52", "accent2": "#ffd166",
        "tagbg": "#3a1d22", "tagfg": "#ff9c9c", "pos": "#ff6b6b", "neg": "#4ade80", "th": "#ff9c9c",
    },
    "lhb": {  # A 风格(2026-09-18 用户拍板):炭黑底+正红+金,与天梯统一;tag 蓝示资金卡区分
        "bg": "#0f1014", "card": "#191b22", "border": "#343846", "text": "#f2f3f7",
        "dim": "#9aa0b5", "accent1": "#ff4d4f", "accent2": "#ffd166",
        "tagbg": "#1d2a4a", "tagfg": "#8fb5ff", "pos": "#ff6b6b", "neg": "#4ade80", "th": "#8fb5ff",
    },
}

CSS = """
  * { margin:0; padding:0; box-sizing:border-box; font-family:"PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif; }
  body { width:750px; background:{bg}; color:{text}; padding:36px 30px; }
  .tag { display:inline-block; background:{tagbg}; color:{tagfg}; border-radius:6px; padding:4px 10px; font-size:20px; }
  h1 { font-size:42px; margin:16px 0 6px; line-height:1.38; color:{accent1}; font-weight:800; }
  .sub { color:{dim}; font-size:21px; margin-bottom:22px; line-height:1.55; }
  .hook { border:2px solid {accent1}; background:{card}; border-radius:16px; padding:22px; margin-bottom:20px; }
  .stats { display:flex; gap:14px; margin-top:14px; }
  .stat { flex:1; background:{border}; border-radius:12px; padding:14px 8px; text-align:center; }}
  .stat .v { font-size:32px; font-weight:800; color:{accent2}; }
  .stat .k { font-size:16px; color:{dim}; margin-top:4px; }
  .card { background:{card}; border:1px solid {border}; border-radius:16px; padding:22px; margin-bottom:20px; }
  .card h2 { font-size:25px; color:{accent2}; margin-bottom:6px; }
  .card .st { color:{dim}; font-size:18px; margin-bottom:12px; }
  table { width:100%; border-collapse:collapse; font-size:18px; }
  th { color:{th}; text-align:left; padding:8px 6px; border-bottom:1px solid {border}; }
  td { padding:8px 6px; border-bottom:1px solid {border}66; color:{text}; line-height:1.55; }
  td.up { color:{pos}; }} td.down { color:{neg}; }}
  .note { font-size:16px; color:{dim}; margin-top:8px; line-height:1.6; }
  .insight { border-left:4px solid {accent2}; background:{border}55; padding:14px 16px; border-radius:0 10px 10px 0; font-size:19px; line-height:1.75; margin-bottom:20px; }
  .rows li { font-size:19px; line-height:1.8; color:{text}; margin-left:20px; margin-bottom:6px; }
  .rows b { color:{accent2}; }
  .foot { font-size:16px; color:{dim}; line-height:1.7; margin-top:8px; padding:16px; background:{card}; border-radius:12px; }
"""

SAFE_HTML = re.compile(r"</?(b|br|i|strong)\s*/?>", re.I)


def esc(s, allow_safe: bool = True) -> str:
    """转义后放回受控标签(b/br/i/strong)——spec 为自家产物,仅允许白名单内联标签。"""
    out = html_mod.escape(str(s), quote=False)
    if allow_safe:
        out = out.replace("&lt;br&gt;", "<br>").replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
        out = out.replace("&lt;i&gt;", "<i>").replace("&lt;/i&gt;", "</i>").replace("&lt;strong&gt;", "<strong>").replace("&lt;/strong&gt;", "</strong>")
        out = SAFE_HTML.sub(lambda m: m.group(0).lower(), out)
    return out


def materialize(node, facts: dict):
    if isinstance(node, dict):
        if "$fact" in node:
            f = facts.get(node["$fact"])
            return esc(f["display"]) if f else "?"
        return {k: materialize(v, facts) for k, v in node.items()}
    if isinstance(node, list):
        return [materialize(x, facts) for x in node]
    return node


def page_html(p: dict, theme: dict) -> str:
    t = theme
    ptype = p.get("type", "")
    buf = []
    if ptype == "cover":
        buf.append('<div class="hook">')
        if p.get("tag_top"):
            buf.append(f'<span class="tag">{esc(p["tag_top"])}</span>')
        buf.append(f'<h1>{esc(p.get("title",""))}</h1>')
        if p.get("sub"):
            buf.append(f'<div class="sub">{esc(p["sub"])}</div>')
        stats = p.get("stats") or []
        if stats:
            buf.append('<div class="stats">')
            for s in stats:
                buf.append(f'<div class="stat"><div class="v">{materialize(s["v"], {}) if isinstance(s["v"], dict) else esc(s["v"])}</div><div class="k">{esc(s["k"])}</div></div>')
            buf.append("</div>")
        buf.append("</div>")
        return "\n".join(buf)
    # 表格页
    if p.get("table"):
        buf.append('<div class="card">')
        if p.get("tag_top"):
            buf.append(f'<span class="tag">{esc(p["tag_top"])}</span>')
        buf.append(f'<h2>{esc(p.get("title",""))}</h2>')
        if p.get("subtitle"):
            buf.append(f'<div class="st">{esc(p["subtitle"])}</div>')
        tb = p["table"]
        buf.append("<table><tr>" + "".join(f"<th>{esc(h)}</th>" for h in tb["headers"]) + "</tr>")
        for row in tb["rows"]:
            cls = row.get("cls") or []
            tds = "".join(
                f'<td class="{c}">{materialize(c_, {}) if isinstance(c_, dict) else esc(c_)}</td>'
                for c_, c in zip(row["cells"], cls + [""] * len(row["cells"])))
            buf.append(f"<tr>{tds}</tr>")
        buf.append("</table></div>")
        return "\n".join(buf)
    # 收束/说明页(kbox→insight, rows→列表)
    buf.append('<div class="card">')
    if p.get("tag_top"):
        buf.append(f'<span class="tag">{esc(p["tag_top"])}</span>')
    buf.append(f'<h2>{esc(p.get("title",""))}</h2>')
    if p.get("subtitle"):
        buf.append(f'<div class="st">{esc(p["subtitle"])}</div>')
    if p.get("rows"):
        buf.append('<ul class="rows">')
        for r in p["rows"]:
            buf.append(f'<li>{esc(r.get("desc",""))}</li>')
        buf.append("</ul>")
    if p.get("kbox"):
        kb = p["kbox"]
        buf.append(f'</div><div class="insight"><b>{esc(kb.get("date","盘后观察"))}</b><br>{esc(kb.get("html",""))}')
        buf.append("</div>")
        return "\n".join(buf)
    buf.append("</div>")
    return "\n".join(buf)


def build_html(kind: str, day_dir: Path, theme: dict) -> str:
    spec = json.loads((day_dir / "cards.spec.json").read_text(encoding="utf-8"))
    facts_raw = json.loads((day_dir / "facts.json").read_text(encoding="utf-8"))
    facts_list = facts_raw["facts"] if isinstance(facts_raw, dict) else facts_raw
    facts = {f["id"]: f for f in facts_list}
    pages = [materialize(p, facts) for p in spec["cards"]]
    # 封面 stats 已物化(page_html 里对 dict 的兜底不再需要,统一走物化后的纯文本)
    body = "\n".join(page_html(p, theme) for p in pages)
    foot = pages[0].get("foot") or "数据来源:沪深交易所/东方财富(经 stockhot 采集) · 仅供研究参考,不构成投资建议"
    tags = pages[-1].get("tags") or spec["cards"][0].get("tags", "")
    css = CSS
    for k, v in theme.items():
        css = css.replace("{" + k + "}", v)
    return (f'<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><style>{css}</style></head><body>\n'
            f'{body}\n<div class="foot">{esc(foot)}</div>\n</body></html>')


async def shoot(html_path: Path, png_path: Path) -> int:
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 750, "height": 1200}, device_scale_factor=2)
        await page.goto(html_path.as_uri())
        await page.wait_for_timeout(500)
        h = await page.evaluate("document.body.scrollHeight")
        await page.screenshot(path=str(png_path), full_page=True)
        await browser.close()
        return h


def main() -> None:
    ap = argparse.ArgumentParser(description="每日复盘卡长图渲染(天梯/龙虎榜)")
    ap.add_argument("--kind", required=True)
    ap.add_argument("--day", default=date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--out", default=None, help="png 输出路径(默认工程目录/长图.png)")
    args = ap.parse_args()

    base_kind = "ladder" if args.kind.startswith("ladder") else "lhb"
    day_dir = CARDS_ROOT / KIND_MAP[base_kind] / args.day
    if not (day_dir / "cards.spec.json").exists():
        raise SystemExit(f"工程不存在: {day_dir}(先跑 daily_market_cards 生成)")
    theme = THEMES.get(args.kind) or THEMES[base_kind]
    html_path = day_dir / "长图.html"
    html_path.write_text(build_html(args.kind, day_dir, theme), encoding="utf-8")
    png = Path(args.out) if args.out else day_dir / "长图.png"
    h = asyncio.run(shoot(html_path, png))
    print(f"{args.kind}({KIND_MAP[base_kind]}) {args.day}: {h}px -> {png}")


if __name__ == "__main__":
    main()
