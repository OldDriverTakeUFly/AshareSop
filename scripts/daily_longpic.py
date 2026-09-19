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
KIND_MAP = {"ladder": "连板天梯", "lhb": "龙虎榜", "thermo": "板块温度"}

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
    "thermo": {  # A 风格(2026-09-18 用户拍板):温度计入日更系,炭黑底上热红→冷蓝色带
        "bg": "#0f1014", "card": "#191b22", "border": "#343846", "text": "#f2f3f7",
        "dim": "#9aa0b5", "accent1": "#ff4d4f", "accent2": "#ffd166",
        "tagbg": "#3a1d1f", "tagfg": "#ff9c9c", "pos": "#ff6b6b", "neg": "#4ade80", "th": "#ff9c9c",
    },
}

KIT_CSS_PATH = CARDS_ROOT / "longpic_kit" / "kit.css"  # 方案B(2026-09-18):骨架组件单一真相源


def _kit_css() -> str:
    if not KIT_CSS_PATH.exists():
        raise SystemExit(f"kit.css 缺失: {KIT_CSS_PATH}(longpic_kit 是长图渲染的硬依赖)")
    return KIT_CSS_PATH.read_text(encoding="utf-8")


def _root_vars(t: dict) -> str:
    """日更系 :root 覆盖块——逐值对应换骨前内嵌模板(themes.md §3 文档化,像素回归依据)."""
    pairs = [f"--{k}:{v}" for k, v in t.items()]
    pairs += [
        "--h1-size:42px", "--h1-lh:1.38", "--h1-weight:800",
        "--sub-size:21px", "--sub-mb:22px",
        "--hook-bg:" + t["card"],
        "--stats-mt:14px", "--stat-bg:" + t["border"], "--stat-border:none", "--stat-pad:14px 8px",
        "--v-size:32px", "--v-color:" + t["accent2"],
        "--h2-size:25px", "--h2-color:" + t["accent2"],
        "--td-border:" + t["border"] + "66", "--td-color:" + t["text"], "--td-lh:1.55", "--td-wb:break-all",
        "--note-mt:8px", "--note-lh:1.6", "--note-color:" + t["dim"],
        "--insight-size:19px", "--insight-color:" + t["text"], "--insight-bar:" + t["accent2"],
        "--insight-bg:" + t["border"] + "55",
        "--rows-mb:6px", "--rows-color:" + t["text"], "--b-em:" + t["accent2"],
        "--foot-bg:" + t["card"], "--foot-color:" + t["dim"],
        "--vs-bg:" + t["card"], "--border-soft:" + t["border"] + "55",
    ]
    return ":root{" + ";".join(pairs) + "}"


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


def build_html(kind: str, day_dir: Path, theme: dict, spec: dict | None = None) -> str:
    if spec is None:
        spec = json.loads((day_dir / "cards.spec.json").read_text(encoding="utf-8"))
    facts_raw = json.loads((day_dir / "facts.json").read_text(encoding="utf-8"))
    facts_list = facts_raw["facts"] if isinstance(facts_raw, dict) else facts_raw
    facts = {f["id"]: f for f in facts_list}
    pages = [materialize(p, facts) for p in spec["cards"]]
    # 封面 stats 已物化(page_html 里对 dict 的兜底不再需要,统一走物化后的纯文本)
    body = "\n".join(page_html(p, theme) for p in pages)
    foot = pages[0].get("foot") or "数据来源:沪深交易所/东方财富(经 stockhot 采集) · 仅供研究参考,不构成投资建议"
    tags = pages[-1].get("tags") or spec["cards"][0].get("tags", "")
    css = _kit_css() + _root_vars(theme)
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
    ap.add_argument("--rebuild", action="store_true",
                    help="绕过工程spec,内存中重跑daily生成逻辑(当日工程已入池锁定时的热修通道)")
    ap.add_argument("--enqueue", action="store_true", help="thermo:长图+文案入发稿池(发布人工)")
    ap.add_argument("--push", action="store_true", help="thermo:长图推红薯运营群")
    args = ap.parse_args()

    if args.kind == "thermo":
        day_dir = CARDS_ROOT / "板块温度" / args.day
        facts_path = day_dir / "facts.json"
        if not facts_path.exists():
            raise SystemExit(f"工程不存在: {day_dir}(先跑 daily_market_cards --type thermo 生成+过闸)")
        html_path = day_dir / "长图.html"
        html_text = build_thermo_html(day_dir, THEMES["thermo"])
        # 数字闸:长图全部数字对工程 facts 零未锚定(校验层迁移,2026-09-18 用户拍板)
        bad = numbers_gate(html_text, facts_path)
        if bad:
            raise SystemExit(f"thermo 长图数字闸未过,未锚定数字: {bad[:10]}")
        html_path.write_text(html_text, encoding="utf-8")
        png = Path(args.out) if args.out else day_dir / "长图.png"
        h = asyncio.run(shoot(html_path, png))
        print(f"thermo(板块温度) {args.day}: {h}px -> {png} | 数字闸: 过")
        if args.enqueue or args.push:
            push_thermo(day_dir, do_enqueue=args.enqueue)
        return

    base_kind = "ladder" if args.kind.startswith("ladder") else "lhb"
    day_dir = CARDS_ROOT / KIND_MAP[base_kind] / args.day
    if not (day_dir / "cards.spec.json").exists():
        raise SystemExit(f"工程不存在: {day_dir}(先跑 daily_market_cards 生成)")
    theme = THEMES.get(args.kind) or THEMES[base_kind]
    spec_override = None
    if args.rebuild:
        import sys as _sys
        _sys.path.insert(0, str(ROOT))
        from davis_analyzer.systems.cardgen import daily as daily_mod
        con = daily_mod._ro_conn(daily_mod.stockhot_db_path())
        bundle = daily_mod.fetch_day_bundle(daily_mod.stockhot_db_path(), args.day)
        builder = daily_mod.build_ladder if base_kind == "ladder" else daily_mod.build_lhb
        facts_list, spec_override = builder(args.day, bundle)
        con.close()
        spec_override = {"group": spec_override.get("group", KIND_MAP[base_kind]), "cards": spec_override["cards"]} if isinstance(spec_override, dict) and "cards" in spec_override else spec_override
    html_path = day_dir / "长图.html"
    html_path.write_text(build_html(args.kind, day_dir, theme, spec=spec_override), encoding="utf-8")
    png = Path(args.out) if args.out else day_dir / "长图.png"
    h = asyncio.run(shoot(html_path, png))
    print(f"{args.kind}({KIND_MAP[base_kind]}) {args.day}: {h}px -> {png}")




# ── thermo:板块温度计长图(2026-09-18 用户拍板:A 风格/短卡停出/facts闸迁移) ──
# 专属构建器:消费当日 cardgen 工程 facts.json(四道闸已过的数字真相源)+ bundle,
# 输出 750px 单长图;渲染后内建数字闸(unmatched_tokens 对 facts,零未锚定才放行)。

_THERMO_CSS_EXTRA = """
  .bar-row { display:flex; align-items:center; gap:10px; padding:7px 0; border-bottom:1px solid var(--border-soft); }
  .bar-row .nm { width:120px; font-size:19px; color:var(--text); flex:none; }
  .bar-row .track { flex:1; background:var(--border-soft); border-radius:8px; height:22px; overflow:hidden; }
  .bar-row .fill { height:100%; border-radius:8px; }
  .bar-row .tv { width:64px; font-size:19px; font-weight:800; text-align:right; flex:none; }
  .bar-row .dv { width:72px; font-size:16px; flex:none; }
  .dim-row { display:flex; align-items:center; gap:10px; padding:8px 0; border-bottom:1px solid var(--border-soft); }
  .dim-row .nm { width:80px; font-size:19px; flex:none; }
  .dim-row .track { flex:1; background:var(--border-soft); border-radius:8px; height:16px; overflow:hidden; }
  .dim-row .fill { height:100%; background:var(--accent2); }
  .dim-row .tv { width:56px; font-size:17px; text-align:right; flex:none; color:var(--dim); }
"""

_THERMO_BANDS = [  # 五档色带(条形填充色;A 风格炭黑底上热红→冷蓝)
    (85, "#ff4d4f"), (65, "#ff9861"), (35, "#7d8499"), (15, "#5aa2f5"), (-1, "#3b6fd4"),
]


def _band_color(temp: float) -> str:
    for th, color in _THERMO_BANDS:
        if temp >= th:
            return color
    return _THERMO_BANDS[-1][1]


def _thermo_num(x) -> str:
    return f"{abs(float(x)):.1f}".rstrip("0").rstrip(".") or "0"


_STYLE_ATTR_RE = re.compile(r'style="[^"]*"')
_STYLE_BLOCK_RE = re.compile(r'<style[^>]*>.*?</style>', re.S)


def numbers_gate(html_text: str, facts_path: Path) -> list[str]:
    """长图数字闸:mask style 属性(条宽/色值)后扫 token,对工程 facts 零未锚定."""
    import sys as _sys
    _sys.path.insert(0, str(ROOT))
    from davis_analyzer.systems.cardgen.numbers import unmatched_tokens
    from davis_analyzer.systems.cardgen.types import Fact

    raw = json.loads(facts_path.read_text(encoding="utf-8"))
    flist = raw.get("facts") if isinstance(raw, dict) else raw
    if isinstance(flist, dict):
        # 手工工程「叙事锚」格式(键=含数字描述句,无结构化 value)——机器闸不可运行
        return None
    from decimal import Decimal, InvalidOperation
    facts = []
    for f in flist:
        src = f.get("source") or {}
        if not isinstance(src, dict):
            src = {"kind": "", "ref": str(src)}
        try:
            val = Decimal(str(f["value"]))
        except (InvalidOperation, KeyError):
            continue  # 非数值型 fact(板块名等)不参与数字锚定
        facts.append(Fact(id=f["id"], value=val, unit=f.get("unit", ""),
                          display=f.get("display", ""), as_of=f.get("as_of", ""),
                          source_kind=src.get("kind", ""), source_ref=src.get("ref", ""),
                          expires=f.get("expires", "")))
    masked = _STYLE_BLOCK_RE.sub("<style>□</style>", html_text)
    masked = _STYLE_ATTR_RE.sub('style="□"', masked)
    return [t.raw for t in unmatched_tokens(masked, facts)]


def build_thermo_html(day_dir: Path, theme: dict) -> str:
    """板块温度计长图:钩子两 stats + 温度条形榜 + 轮动 + 低温池 + 大盘五维 + insight."""
    import sys as _sys
    _sys.path.insert(0, str(ROOT))
    from davis_analyzer.systems.cardgen import daily as daily_mod

    day = day_dir.name
    bundle = daily_mod.fetch_thermo_bundle(day)
    t = theme
    css = _kit_css() + _root_vars(t) + _THERMO_CSS_EXTRA
    esc_ = esc
    mkt = bundle["market"]
    top1 = bundle["l1"][0]
    streak = int(top1.get("hot_streak") or 0)

    buf = [f"<!DOCTYPE html><html lang=\"zh\"><head><meta charset=\"utf-8\"><style>{css}</style></head><body>"]
    # 首屏钩子(用户拍板两枚 stats)
    buf.append('<div class="hook">')
    buf.append(f'<span class="tag">板块温度计 · 每日数据复盘</span>')
    buf.append(f'<h1>今天的板块温度<br>全景一张图</h1>')
    buf.append(f'<div class="sub">色越红越拥挤(风险提醒) 越蓝越冷清(关注线索) · 按温度降序<br>{day} 交易数据整理</div>')
    buf.append('<div class="stats">')
    buf.append(f'<div class="stat"><div class="v">{_thermo_num(mkt["temperature"])}</div>'
               f'<div class="k">大盘温度 · {esc_(mkt["regime_label"])}</div></div>')
    buf.append(f'<div class="stat"><div class="v">{_thermo_num(top1["temperature"])}</div>'
               f'<div class="k">最热 {esc_(top1["name"])} · 连热{streak}天</div></div>')
    buf.append('</div></div>')

    # 一、温度全景(31 板块条形榜,温度降序,条长∝温度,色带五档)
    buf.append('<div class="card"><span class="tag">温度全景</span>')
    buf.append('<h2>一级行业温度全景</h2>')
    buf.append('<div class="st">按温度降序 · 条长为温度 · 右侧为较昨日变化(红升绿降)</div>')
    for r in bundle["l1_full"]:
        nm = esc_(str(r["name"]))
        tv = _thermo_num(r["temperature"])
        dv = r.get("delta_temp1")
        if dv is None:
            dvs, dvc = "—", ""
        else:
            dvs = f"{'+' if dv > 0 else '-' if dv < 0 else '±'}{_thermo_num(dv)}"
            dvc = "up" if dv > 0 else ("down" if dv < 0 else "")
        buf.append(
            f'<div class="bar-row"><div class="nm">{nm}</div>'
            f'<div class="track"><div class="fill" style="width:{_thermo_num(r["temperature"])}%;'
            f'background:{_band_color(r["temperature"])};"></div></div>'
            f'<div class="tv">{tv}</div><div class="dv {dvc}">{dvs}</div></div>')
    buf.append('</div>')

    # 二、轮动脉搏
    rot = bundle.get("rotation") or {}
    if rot.get("moves"):
        buf.append('<div class="card"><span class="tag">轮动脉搏</span>')
        buf.append('<h2>近五日温度轮动</h2>')
        buf.append(f'<div class="st">档位迁移 {rot["n_moves"]} 个板块(升 {rot["n_up"]} / 降 {rot["n_down"]})'
                   ' · 升=左侧补涨 降=高位退潮</div>')
        buf.append('<table><tr><th>板块</th><th>档位迁移</th><th>五日温度变化</th></tr>')
        for m in rot["moves"]:
            lv = "一级" if m["level"] == "L1" else "二级"
            d5 = m["d5"]
            cls = "up" if d5 > 0 else ("down" if d5 < 0 else "")
            d5s = f"{'+' if d5 > 0 else '-' if d5 < 0 else '±'}{_thermo_num(d5)}"
            buf.append(f'<tr><td>{lv}·{esc_(str(m["name"]))}</td>'
                       f'<td>{esc_(m["from_band"])}→{esc_(m["to_band"])}</td>'
                       f'<td class="{cls}">{d5s}</td></tr>')
        buf.append('</table></div>')

    # 三、无人问津处(低温池+成因)
    buf.append('<div class="card"><span class="tag">低温关注池</span>')
    buf.append('<h2>无人问津处 · 低温板块与成因</h2>')
    buf.append('<div class="st">全市场温度最低方向 · 成因为数据事实标签,判断留给读者</div>')
    buf.append('<table><tr><th>板块</th><th>温度</th><th>较昨日</th><th>低温成因</th></tr>')
    for r in bundle["cold"]:
        lv = "一级" if r["level"] == "L1" else "二级"
        dv = r.get("delta_temp1")
        dvs = ("—" if dv is None else
               f"{'+' if dv > 0 else '-' if dv < 0 else '±'}{_thermo_num(dv)}")
        tags = "<br>".join(esc_(x) for x in (r.get("tags") or []))
        buf.append(f'<tr><td>{lv}·{esc_(str(r["name"]))}</td>'
                   f'<td>{_thermo_num(r["temperature"])}</td><td>{dvs}</td>'
                   f'<td>{tags}</td></tr>')
    buf.append('</table></div>')

    # 四、大盘五维(分位条)
    buf.append('<div class="card"><span class="tag">大盘五维</span>')
    buf.append('<h2>大盘温度的五维构成</h2>')
    buf.append('<div class="st">各维为自身历史分位(越长越热)</div>')
    for nm, v in mkt["dims"].items():
        if v is None:
            continue
        buf.append(f'<div class="dim-row"><div class="nm">{esc_(nm)}</div>'
                   f'<div class="track"><div class="fill" style="width:{_thermo_num(float(v) * 100)}%;"></div></div>'
                   f'<div class="tv">{_thermo_num(v)}</div></div>')
    buf.append('</div>')

    # insight + foot
    picks = daily_mod.thermo_insights(bundle)
    buf.append(f'<div class="insight"><b>盘后观察</b><br>{esc_("<br>".join(picks))}</div>')
    buf.append('<div class="foot">数据来源:交易所公开行情与申万指数(经 thermometer 采集) · '
               '仅供研究参考,不构成投资建议</div>')
    buf.append('</body></html>')
    return "\n".join(buf)


def push_thermo(day_dir: Path, do_enqueue: bool) -> None:
    """长图推送+入池(tags 末行铁律;幂等锁与短卡同目录)."""
    import asyncio
    import os as _os
    from datetime import datetime as _dt
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from davis_analyzer.systems.cardgen import daily as daily_mod

    day = day_dir.name
    png = day_dir / "长图.png"
    if not png.exists():
        raise SystemExit(f"长图未渲染: {png}")
    copy = daily_mod.publish_copy("thermo", day, daily_mod.fetch_thermo_bundle(day))
    # 文案.md(四件套)
    (day_dir / "文案.md").write_text(
        f"# {copy['title']}\n\n{copy['body']}\n\n{copy['tags']}\n", encoding="utf-8")
    if do_enqueue:
        import subprocess
        cmd = [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/content_publisher/queue.py"),
               "enqueue", "--title", copy["title"], "--body", copy["body"],
               "--images", str(png), "--tags", copy["tags"],
               "--source", str(day_dir.relative_to(ROOT))]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=120)
        print("入池:", (proc.stdout or proc.stderr).strip()[:120])
    chat = _os.environ.get("FEISHU_XHS_CHAT_ID", "")
    if not chat:
        print("! 未配置 FEISHU_XHS_CHAT_ID,跳过群推送")
        return
    from stockhot.notification.feishu_bot import EnterpriseFeishuNotifier

    async def _send() -> None:
        n = EnterpriseFeishuNotifier(_os.environ["FEISHU_APP_ID"],
                                     _os.environ["FEISHU_APP_SECRET"], chat)
        await n.send_image(str(png))
        # tags 必须是最后一行(头部括号行为运营提示)
        await n.send_text(f"【{day} 复盘卡·长图,发布人工】{copy['title']}\n\n{copy['body']}\n\n{copy['tags']}")
    asyncio.run(_send())
    lock = ROOT / "logs" / ".xhs_card_push" / f"{day.replace('-', '')}_thermo.ok"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(_dt.now().isoformat(timespec="seconds"), encoding="utf-8")
    print(f"已推长图: {png.name}")


if __name__ == "__main__":
    main()
