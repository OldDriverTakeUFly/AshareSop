# davis_analyzer/recap/card_renderer.py
"""recap 数据卡:片头比分牌(1080x1920)+个股下三分之一条(1080x420),html→png。"""
from __future__ import annotations

import asyncio
import html
import json
from pathlib import Path

from loguru import logger

from davis_analyzer.recap.constants import EPISODES_DIR

_CSS = """
body{margin:0;font-family:'PingFang SC','Noto Sans SC',sans-serif;background:transparent}
.scoreboard{width:1080px;height:1920px;box-sizing:border-box;padding:80px 60px;
  background:linear-gradient(160deg,#0b1220 0%,#101a30 60%,#0b1220 100%);color:#eef2f8}
.title{font-size:72px;font-weight:800;letter-spacing:4px;margin:0 0 8px}
.date{font-size:34px;color:#8fa3c0;margin-bottom:56px}
.idxrow{display:flex;justify-content:space-between;align-items:center;
  background:#16233c;border-radius:24px;padding:36px 44px;margin-bottom:28px}
.idxname{font-size:44px;font-weight:700}.idxval{font-size:40px;color:#c9d6ea}
.idxchg{font-size:46px;font-weight:800}
.up{color:#ff4d57}.dn{color:#2ecc8f}
.breadth{display:flex;gap:28px;margin-top:48px}
.bcard{flex:1;background:#16233c;border-radius:24px;padding:34px;text-align:center}
.bnum{font-size:66px;font-weight:800}.blab{font-size:32px;color:#8fa3c0;margin-top:8px}
.footer{position:absolute;bottom:210px;left:60px;right:60px;font-size:28px;color:#63748f}
.stockcard{width:1080px;height:420px;box-sizing:border-box;padding:40px 56px;
  background:linear-gradient(90deg,#101a30ee,#0b1220ee);color:#eef2f8;
  display:flex;flex-direction:column;justify-content:center}
.sname{font-size:58px;font-weight:800}.scode{font-size:32px;color:#8fa3c0;margin-left:20px}
.stags{margin-top:18px;font-size:36px;color:#ffd34d;font-weight:700}
.sfacts{margin-top:16px;font-size:34px;color:#c9d6ea}
"""


def _page(body: str) -> str:
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{_CSS}</style></head><body>{body}</body></html>"


def _strip(display: str, prefix: str) -> str:
    """display 去掉标签前缀后转义(槽位已带同名标签,防重复印);数字/符号原样,不重排。"""
    return html.escape(display.removeprefix(prefix))


def scoreboard_html(ep: dict) -> str:
    facts = {f["id"]: f["display"] for f in ep.get("facts", [])}

    def row(key: str, name: str) -> str:
        val = _strip(facts.get(f"idx_{key}_close", "-"), name)
        chg = _strip(facts.get(f"idx_{chg_key(key)}", ""), name)
        cls = "up" if chg.startswith("+") or "涨" in chg else "dn"
        return (f"<div class='idxrow'><span class='idxname'>{html.escape(name)}</span>"
                f"<span class='idxval'>{val}</span>"
                f"<span class='idxchg {cls}'>{chg}</span></div>")

    def chg_key(key: str) -> str:
        return f"{key}_chg"

    breadth = (f"<div class='bcard'><div class='bnum up'>{_strip(facts.get('breadth_up', '-'), '上涨')}</div>"
               "<div class='blab'>上涨家数</div></div>"
               f"<div class='bcard'><div class='bnum dn'>{_strip(facts.get('breadth_down', '-'), '下跌')}</div>"
               "<div class='blab'>下跌家数</div></div>"
               f"<div class='bcard'><div class='bnum'>{_strip(facts.get('limit_up_count', '-'), '涨停')}</div>"
               "<div class='blab'>涨停家数</div></div>")
    body = (f"<div class='scoreboard'><h1 class='title'>今日战报</h1>"
            f"<div class='date'>{html.escape(str(ep['trade_date']))} · A股全场回放</div>"
            + row("sh", "上证指数") + row("sz", "深证成指") + row("cyb", "创业板指")
            + f"<div class='breadth'>{breadth}</div>"
            f"<div class='footer'>数据来源:盘后公开行情 · 仅为盘面复盘记录,不构成投资建议</div></div>")
    return _page(body)


def stock_card_html(cand: dict) -> str:
    notes = [str(n) for n in cand.get("notes", [])[:4]]
    tags = " · ".join(notes) or "今日高光"
    joined = " · ".join(notes)
    # 与 notes 语义重复的 fact(互为子串)不再在 facts 行重印;清空则整行省略
    kept = [f["display"] for f in cand.get("facts", [])[:6]
            if f["display"] not in joined and not any(n in f["display"] for n in notes)]
    replay = f"{cand['replay_start'][:5]}-{cand['replay_end'][:5]}"
    facts_line = (f"<div class='sfacts'>{html.escape(' / '.join(kept))}</div>" if kept else "")
    body = (f"<div class='stockcard'><div><span class='sname'>{html.escape(cand['name'])}</span>"
            f"<span class='scode'>{html.escape(cand['ts_code'])} · "
            f"{html.escape(cand.get('sector') or '')} · 回放 {html.escape(replay)}</span></div>"
            f"<div class='stags'>{html.escape(tags)}</div>"
            f"{facts_line}</div>")
    return _page(body)


async def _shoot(html: str, out: Path, w: int, h: int) -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
        await page.set_content(html, wait_until="load")
        await page.wait_for_timeout(400)
        await page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": w, "height": h})
        await browser.close()


def render_cards(day_dash: str) -> list[Path]:
    ep_dir = EPISODES_DIR / day_dash
    ep = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    cands = json.loads((ep_dir / "candidates.json").read_text(encoding="utf-8"))
    out_dir = ep_dir / "原料包" / "cards"
    out_dir.mkdir(parents=True, exist_ok=True)
    pngs: list[Path] = []
    board = out_dir / "scoreboard.png"
    asyncio.run(_shoot(scoreboard_html(ep), board, 1080, 1920))
    pngs.append(board)
    for i, c in enumerate(cands, 1):
        card = out_dir / f"stock_{i:02d}_{c['ts_code'].split('.')[0]}.png"
        asyncio.run(_shoot(stock_card_html(c), card, 1080, 420))
        pngs.append(card)
    logger.info(f"recap 数据卡 {len(pngs)} 张 → {out_dir}")
    return pngs
