#!/usr/bin/env python3
# card_factory/build_cards.py — 小红书卡片工厂:JSON spec → HTML(→ 配合 snap.cjs 截图 PNG)
# 用法: .venv/bin/python scripts/card_factory/build_cards.py examples/demo.json --out output/demo
# spec 结构见 examples/。卡片类型: cover / profiles / table / bars / timeline / summary
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CSS = (ROOT / "card.css").read_text(encoding="utf-8")

COLORS = {"green": "#16a34a", "blue": "#2563eb", "purple": "#7c3aed", "red": "#dc2626",
          "orange": "#c2410c", "gray": "#64748b", "sky": "#0ea5e9", "dark": "#0f172a"}


def _tag(text: str, color: str = "#ff2442") -> str:
    return f'<span class="tag-top" style="background:{color};">{text}</span>'


def _foot(text: str) -> str:
    return f'<div class="foot">{text}</div>' if text else ""


def _quote(q: dict) -> str:
    color = q.get("color", "#ffd400")
    style = (f'background:rgba(255,255,255,.14);color:inherit;'
             if q.get("on_dark") else
             f'background:{q.get("bg", "rgba(124,58,237,.10)")};color:{q.get("text_color", "#2e2440")};')
    return (f'<div class="quote" style="{style}border-left-color:{color};'
            f'font-size:{q.get("size", 30)}px;">{q["html"]}</div>')


def _kbox(k: dict) -> str:
    color = COLORS.get(k.get("color", "green"), k.get("color", "#16a34a"))
    return (f'<div class="kbox"><div class="kdate" style="background:{color};">{k["date"]}</div>'
            f'<div class="ktext">{k["html"]}</div></div>')


def _render_cover(c: dict) -> str:
    stats = "".join(
        f'<div class="nb"><div class="v">{s["v"]}</div><div class="k">{s["k"]}</div></div>'
        for s in c.get("stats", []))
    tags = f'<div class="tags">{c["tags"]}</div>' if c.get("tags") else ""
    return f"""
{_tag(c["tag_top"], c.get("tag_color", "#ff2442"))}
<h1>{c["title"]}</h1>
<div class="sub" style="margin-top:24px;">{c.get("sub", "")}</div>
<div class="num-badge">{stats}</div>
<div style="margin-top:30px;">{_quote(c["quote"]) if c.get("quote") else ""}</div>
{tags}{_foot(c.get("foot", ""))}"""


def _render_profiles(c: dict) -> str:
    rows = []
    for r in c["rows"]:
        badges = "".join(f'<span class="badge b-{b["cls"]}">{b["text"]}</span>' for b in r.get("badges", []))
        rows.append(
            f'<div class="row"><div class="name"><span class="dot" style="background:{COLORS[r["color"]]};"></span>'
            f'{r["name"]}{badges}</div><div class="desc">{r["desc"]}</div></div>')
    return f"""
{_tag(c["tag_top"], c.get("tag_color", "#7c3aed"))}
<h2 class="title" style="margin-top:24px;">{c["title"]}</h2>
<div class="subtitle">{c.get("subtitle", "")}</div>
{"".join(rows)}
{_foot(c.get("foot", ""))}"""


def _render_table(c: dict) -> str:
    headers = "".join(
        f'<th{" style=text-align:left" if i == 0 and c.get("first_left") else ""}>{h}</th>'
        for i, h in enumerate(c["table"]["headers"]))
    body_rows = []
    for row in c["table"]["rows"]:
        cells = "".join(
            f'<td class="l">{cell}</td>' if i == 0 and c.get("first_left")
            else f'<td class="{row.get("cls", [""] * len(row["cells"]))[i]}">{cell}</td>'
            for i, cell in enumerate(row["cells"]))
        body_rows.append(f"<tr>{cells}</tr>")
    kboxes = "".join(_kbox(k) for k in c.get("kboxes", []))
    quote = f'<div style="margin-top:26px;">{_quote(c["quote"])}</div>' if c.get("quote") else ""
    return f"""
{_tag(c["tag_top"], c.get("tag_color", "#2563eb"))}
<h2 class="title" style="margin-top:24px;">{c["title"]}</h2>
<div class="subtitle">{c.get("subtitle", "")}</div>
<table><tr>{headers}</tr>{"".join(body_rows)}</table>
<div style="margin-top:30px;">{kboxes}</div>{quote}
{_foot(c.get("foot", ""))}"""


def _render_bars(c: dict) -> str:
    bars = []
    for b in c["bars"]:
        pct = b["pct"]
        color = COLORS.get(b.get("color", "red"), b.get("color", "#dc2626"))
        track = "#e9e4f0"
        bars.append(
            f'<div><div style="display:flex;justify-content:space-between;font-size:30px;font-weight:800;'
            f'margin-bottom:10px;"><span>{b["label"]}</span><span style="color:{color};">{b["value"]}</span></div>'
            f'<div style="height:38px;border-radius:19px;background:linear-gradient(90deg,{color} {pct}%,{track} {pct}%);"></div></div>')
    note = f'<div style="font-size:24px;opacity:.6;text-align:right;margin-top:4px;">{c["bar_note"]}</div>' if c.get("bar_note") else ""
    kboxes = "".join(_kbox(k) for k in c.get("kboxes", []))
    return f"""
{_tag(c["tag_top"], c.get("tag_color", "#ff2442"))}
<h2 class="title" style="margin-top:24px;">{c["title"]}</h2>
<div class="subtitle">{c.get("subtitle", "")}</div>
<div style="display:flex;flex-direction:column;gap:24px;">{"".join(bars)}</div>
{note}
<div style="margin-top:34px;">{kboxes}</div>
{_foot(c.get("foot", ""))}"""


def _render_timeline(c: dict) -> str:
    kboxes = "".join(_kbox(k) for k in c["kboxes"])
    return f"""
{_tag(c["tag_top"], c.get("tag_color", "#16a34a"))}
<h2 class="title" style="margin-top:24px;">{c["title"]}</h2>
<div class="subtitle">{c.get("subtitle", "")}</div>
{kboxes}
{_foot(c.get("foot", ""))}"""


def _render_summary(c: dict) -> str:
    rows = "".join(f'<div class="row"><div class="desc">{r["desc"]}</div></div>' for r in c["rows"])
    kbox = _kbox(c["kbox"]) if c.get("kbox") else ""
    quote = _quote(c["quote"]) if c.get("quote") else ""
    tags = f'<div class="tags">{c["tags"]}</div>' if c.get("tags") else ""
    return f"""
{_tag(c["tag_top"], c.get("tag_color", "#0f172a"))}
<h2 class="title" style="margin-top:24px;">{c["title"]}</h2>
<div class="subtitle">{c.get("subtitle", "")}</div>
{rows}
<div style="margin-top:14px;">{kbox}</div>
<div style="margin-top:8px;">{quote}</div>
{tags}{_foot(c.get("foot", ""))}"""


RENDERERS = {"cover": _render_cover, "profiles": _render_profiles, "table": _render_table,
             "bars": _render_bars, "timeline": _render_timeline, "summary": _render_summary}

THEMES = {"purple_dark": "card1", "cream": "card2", "blue": "card3", "red": "card4",
          "green": "card5", "lavender": "card6"}


def build(spec_path: Path, out_dir: Path) -> Path:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    cards_html = []
    names = []
    for i, c in enumerate(spec["cards"], 1):
        typ = c["type"]
        if typ not in RENDERERS:
            raise ValueError(f"未知卡片类型: {typ}")
        theme = THEMES[c.get("theme", "cream")]
        name = c.get("name") or f"{i:02d}"
        cards_html.append(f'<div class="card {theme}" id="card{i}">{RENDERERS[typ](c)}</div>')
        names.append(name)
    html = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>"
            f"<style>{CSS}</style></head><body>{''.join(cards_html)}</body></html>")
    html_path = out_dir / "cards.html"
    html_path.write_text(html, encoding="utf-8")
    (out_dir / "manifest.json").write_text(
        json.dumps({"html": str(html_path), "names": names}, ensure_ascii=False), encoding="utf-8")
    return html_path


def main() -> None:
    ap = argparse.ArgumentParser(description="小红书卡片工厂")
    ap.add_argument("spec", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    html_path = build(args.spec, args.out)
    print(f"HTML 生成: {html_path}")
    print(f"截图命令: node {ROOT / 'snap.cjs'} {html_path} --outdir {args.out} --prefix {args.out.name}")


if __name__ == "__main__":
    sys.exit(main())
