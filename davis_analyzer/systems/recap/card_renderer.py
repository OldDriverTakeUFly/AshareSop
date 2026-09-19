# davis_analyzer/recap/card_renderer.py
"""recap 数据卡:片头比分牌(1080x1920)+个股下三分之一条(1080x420),html→png。"""
from __future__ import annotations

import asyncio
import html
import json
from pathlib import Path

from loguru import logger

from davis_analyzer.systems.recap.constants import EPISODES_DIR

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
.banner{width:1080px;height:220px;box-sizing:border-box;padding:0 48px;
  background:linear-gradient(90deg,#0b1220f2,#1a2440f2 55%,#0b1220f2);color:#eef2f8;
  display:flex;align-items:center;gap:36px;border-bottom:4px solid #ffd34d}
.brank{min-width:300px;height:132px;border-radius:20px;display:flex;align-items:center;
  justify-content:center;font-size:64px;font-weight:900;letter-spacing:2px;
  background:linear-gradient(135deg,#ffd34d,#ff9d2e);color:#1a1206;
  box-shadow:0 6px 24px #ff9d2e55}
.brank .sub{font-size:30px;font-weight:700;margin-left:10px;letter-spacing:0}
.bname{font-size:56px;font-weight:800}.bcode{font-size:30px;color:#8fa3c0;margin-top:6px}
.badge{width:320px;height:120px;box-sizing:border-box;background:#c81e28;color:#fff;
  display:flex;align-items:center;justify-content:center;font-size:52px;font-weight:900;
  font-style:italic;letter-spacing:6px;border-radius:14px;border:3px solid #ffffffcc}
.rankintro{width:1080px;height:1920px;box-sizing:border-box;
  background:radial-gradient(circle at 50% 38%,#1c2a4a 0%,#0b1220 70%);color:#eef2f8;
  display:flex;flex-direction:column;align-items:center;justify-content:center}
.rilabel{font-size:44px;letter-spacing:24px;color:#8fa3c0;margin-bottom:30px}
.rirank{font-size:360px;font-weight:900;line-height:1;
  background:linear-gradient(180deg,#ffe89a,#ffb52e);-webkit-background-clip:text;
  -webkit-text-fill-color:transparent;text-shadow:0 20px 60px #ffb52e44}
.risingle{font-size:170px;font-weight:900;letter-spacing:8px;
  background:linear-gradient(180deg,#ffe89a,#ffb52e);-webkit-background-clip:text;
  -webkit-text-fill-color:transparent}
.riname{margin-top:40px;font-size:72px;font-weight:800}
.risub{margin-top:14px;font-size:36px;color:#8fa3c0;letter-spacing:12px}
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


def countdown_banner_html(rank: int | None, name: str, ts_code: str) -> str:
    """五佳倒计时横幅:rank=今晚第N佳(1=最高戏剧性);单候选(rank=None)显示「本场最佳」。"""
    badge = ("本场最佳" if rank is None
             else f"TOP {rank}<span class='sub'>今晚第{rank}佳</span>")
    body = (f"<div class='banner'><div class='brank'>{badge}</div>"
            f"<div><div class='bname'>{html.escape(name)}</div>"
            f"<div class='bcode'>{html.escape(ts_code)}</div></div></div>")
    return _page(body)


def replay_badge_html() -> str:
    return _page("<div class='badge'>REPLAY</div>")


def rank_intro_html(rank: int | None, name: str) -> str:
    """段首排名冲击卡(全屏):巨大 TOP N 数字(单候选「本场最佳」)+ 名称。"""
    mid = (f"<div class='rirank'>{rank}</div>" if rank is not None
           else "<div class='risingle'>本场最佳</div>")
    label = "TONIGHT'S TOP PLAY" if rank is not None else "BEST OF THE NIGHT"
    body = (f"<div class='rankintro'><div class='rilabel'>五佳时刻</div>{mid}"
            f"<div class='riname'>{html.escape(name)}</div>"
            f"<div class='risub'>{label}</div></div>")
    return _page(body)


def render_rank_intros(day_dash: str) -> dict[int, Path]:
    """每股票段一张段首冲击卡:{段序i(1基): Path};rank 与横幅同口径(N-i+1,单候选无rank)。"""
    ep_dir = EPISODES_DIR / day_dash
    ep = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    cand_path = ep_dir / "candidates.json"
    cands = ({c["ts_code"]: c for c in json.loads(cand_path.read_text(encoding="utf-8"))}
             if cand_path.exists() else {})
    out_dir = ep_dir / "原料包" / "cards"
    out_dir.mkdir(parents=True, exist_ok=True)
    stock_segs = [s for s in ep.get("segments", []) if s.get("kind") == "stock"]
    n = len(stock_segs)
    cards: dict[int, Path] = {}
    for i, seg in enumerate(stock_segs, 1):
        cand = cands.get(seg.get("ts_code") or {})
        name = cand.get("name") or seg.get("ts_code") or "?"
        rank = (n - i + 1) if n > 1 else None
        p = out_dir / f"rankintro_{i:02d}.png"
        asyncio.run(_shoot(rank_intro_html(rank, name), p, 1080, 1920))
        cards[i] = p
    logger.info(f"recap 排名冲击卡 {len(cards)} 张 → {out_dir}")
    return cards


def render_banners(day_dash: str) -> dict:
    """每股票段一张横幅(段序 i → rank = N-i+1,倒数排位)+ 一张 REPLAY 角标。
    返回 {"banners": {段序i(1基): Path}, "replay": Path}。"""
    ep_dir = EPISODES_DIR / day_dash
    ep = json.loads((ep_dir / "episode.json").read_text(encoding="utf-8"))
    cand_path = ep_dir / "candidates.json"
    cands = ({c["ts_code"]: c for c in json.loads(cand_path.read_text(encoding="utf-8"))}
             if cand_path.exists() else {})
    out_dir = ep_dir / "原料包" / "cards"
    out_dir.mkdir(parents=True, exist_ok=True)
    stock_segs = [s for s in ep.get("segments", []) if s.get("kind") == "stock"]
    n = len(stock_segs)
    banners: dict[int, Path] = {}
    for i, seg in enumerate(stock_segs, 1):
        cand = cands.get(seg.get("ts_code") or "", {"name": seg.get("ts_code", "?"),
                                                    "ts_code": seg.get("ts_code", "?")})
        rank = (n - i + 1) if n > 1 else None
        p = out_dir / f"banner_{i:02d}.png"
        asyncio.run(_shoot(countdown_banner_html(rank, cand["name"], cand["ts_code"]),
                           p, 1080, 220))
        banners[i] = p
    replay = out_dir / "replay_badge.png"
    asyncio.run(_shoot(replay_badge_html(), replay, 320, 120))
    logger.info(f"recap 五佳横幅 {len(banners)} 张 + REPLAY 角标 → {out_dir}")
    return {"banners": banners, "replay": replay}


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
