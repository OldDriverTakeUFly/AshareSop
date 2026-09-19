"""surge 工作长图(750px, longpic_kit 骨架/板块热点肤) + 出口数字闸(2026-09-19 用户拍板).

数字闸两道体系(用户定义):
- 源头闸: 数据取用分级——API(Tushare cyq_perf)与本地库直接置信;巨潮为官方披露
  平台的 API 化拉取(原始层全量落库可回溯),归 API 级;引入 web 搜索数据源时才需
  多源头验证(surge 当前无此类来源,纪律记录于 spec)。
- 出口闸(本模块落地): 长图全部数字 token 对「台账自动锚」零未锚定才放行——
  锚不是手工 facts,而是 surge_snapshot/聚合统计的机器生成 Fact,天然与库一致;
  模板层手拼/抄错数字即被拦。引擎复用 cardgen.numbers.unmatched_tokens。

kit.css 内联说明: 发布工程目录约定 link 引用;本工作长图在 davis_analyzer 树外,
跨目录中文路径 file:// 解析有险,故渲染时读 kit.css 内联进 <style>(闸的 style
mask + 色值豁免本就覆盖)。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd
from loguru import logger

from davis_analyzer.core.config import SURGE_REPORTS_DIR

_KIT_CSS = (Path(__file__).resolve().parents[3]
            / "docs" / "小红书卡片" / "未发布" / "longpic_kit" / "kit.css")

_ROOT_CSS = """
:root {
  --bg:#0a0f1e; --card:#131a30; --border:#26335c; --text:#eef2ff; --dim:#8a96c4;
  --accent1:#f5b942; --accent2:#6db9ff; --tagbg:#3a2e12; --tagfg:#f5c96a;
  --up:#ff6b6b; --down:#4ade80; --pos:#6ee7b7; --neg:#fda4af; --th:#6db9ff;
}
body { width:750px; margin:0 auto; padding:24px 22px 18px; box-sizing:border-box;
       background:var(--bg); color:var(--text);
       font-family:'Noto Sans CJK SC',sans-serif; }
"""

_EXTRA_CSS = """
.rank-chip { display:inline-block; min-width:30px; padding:2px 8px; border-radius:8px;
             background:#1a2340; color:var(--accent2); font-weight:800; font-size:17px;
             text-align:center; }
.deep-card table { margin-top:8px; }
.ladder { color:var(--dim); font-size:17px; line-height:1.6; }
.tagline { margin:2px 0; }
.tagline .tl { display:inline-block; margin:2px 6px 2px 0; padding:2px 10px;
               border-radius:10px; font-size:16px; }
.tl.h { background:#12362a; color:var(--pos); }
.tl.r { background:#3a1a26; color:var(--neg); }
.pv { color:var(--dim); font-size:16px; }
td { font-size:17px; }
"""


# ── 聚合统计(生成器与数字闸共用同源) ──

@dataclass
class SurgeStats:
    day: str
    pool_n: int
    cyq_days: int
    event_counts: dict[str, int]
    event_stock_n: int
    tag_top: list[tuple[str, int]]
    pattern_n: int
    top1: tuple[str, str, float] | None  # (code, name, composite)


def collect_stats(conn: sqlite3.Connection, day: str) -> SurgeStats:
    day = day.replace("-", "")
    pool_n = conn.execute(
        "SELECT COUNT(*) FROM surge_snapshot WHERE trade_date=?", (day,)).fetchone()[0]
    cyq_days = conn.execute(
        "SELECT COUNT(DISTINCT trade_date) FROM cyq_perf_cache").fetchone()[0]
    event_counts = dict(conn.execute(
        "SELECT event_type, COUNT(*) FROM major_events WHERE ann_date>=? "
        "GROUP BY event_type", (day[:4] + "0101",)).fetchall())
    event_stock_n = conn.execute(
        "SELECT COUNT(DISTINCT ts_code) FROM major_events WHERE ann_date>=?",
        (day[:4] + "0101",)).fetchone()[0]
    tag_top = conn.execute(
        "SELECT tag, COUNT(*) FROM surge_tags WHERE trade_date=? "
        "GROUP BY tag ORDER BY COUNT(*) DESC LIMIT 5", (day,)).fetchall()
    pattern_n = conn.execute(
        "SELECT COUNT(*) FROM surge_pattern_hits WHERE trade_date=?",
        (day,)).fetchone()[0]
    top1_row = conn.execute(
        "SELECT ts_code, name, composite FROM surge_snapshot WHERE trade_date=? "
        "ORDER BY rank LIMIT 1", (day,)).fetchone()
    top1 = tuple(top1_row) if top1_row else None
    return SurgeStats(day, pool_n, cyq_days, event_counts, event_stock_n,
                      list(tag_top), pattern_n, top1)


# ── 台账自动锚(出口闸的 facts 来源) ──

def snapshot_facts(conn: sqlite3.Connection, day: str, stats: SurgeStats) -> list:
    """从台账+聚合统计机器生成 Fact——锚与库天然一致,闸只拦模板层错数."""
    from davis_analyzer.cardgen.types import Fact

    facts: list[Fact] = []

    def add(value: float, unit: str = "", ref: str = "") -> None:
        try:
            facts.append(Fact(id=f"auto-{len(facts)}", value=Decimal(str(value)),
                              unit=unit, display="", as_of=day,
                              source_kind="stockhot", source_ref=ref,
                              expires=""))
        except Exception:
            pass

    # 聚合统计(呈现精度:整数;五类事件含零值全锚——html 渲染 ev.get(et,0))
    # hook 大数字不带单位(单位在标签文案)→ 双锚(带单位+裸数);错数值仍必被拦
    add(stats.pool_n, "只", "surge_snapshot count")
    add(stats.pool_n, "", "surge_snapshot count")
    add(stats.cyq_days, "日", "cyq_perf_cache distinct dates")
    for et in ("ma", "divest", "refinance", "distress", "ma_halt"):
        n = stats.event_counts.get(et, 0)
        add(n, "条", f"major_events {et}")
        add(n, "", f"major_events {et}")
    # 版式/口径常量(系统数字,锚定合法:筛选阈值与固定栏目数)
    add(7, "%", "筛选阈值 pct_chg>7")
    add(20, "", "Top20 栏目")
    add(12, "", "Top12 深析栏目")
    add(120, "", "VMA 均量周期")
    add(stats.event_stock_n, "只", "major_events distinct stocks")
    for tag, n in stats.tag_top:
        add(n, "次", f"surge_tags {tag}")
        add(n, "", f"surge_tags {tag}")
    add(stats.pattern_n, "只", "surge_pattern_hits count")
    add(stats.pattern_n, "", "surge_pattern_hits count")
    if stats.top1:
        add(round(float(stats.top1[2]), 1), "", "top1 composite")

    # 每股数值字段(与 html 呈现格式一一对应;NULL 安全——次新/缺失字段跳过锚)
    rows = conn.execute(
        "SELECT ts_code, pct_chg, pos_250d, dist_ma60, elg_net_d0, "
        "winner_rate, winner_delta_5d, resistance_dist, support_dist, "
        "resistance_price, support_price, weight_avg, cost_5pct, lg_net_5d, "
        "composite, hype_count, risk_flag_count, rank "
        "FROM surge_snapshot WHERE trade_date=? "
        "ORDER BY rank", (day,)).fetchall()
    for r in rows:
        ref = f"surge_snapshot {r[0]}"

        def A(idx: int, nd: int, unit: str, scale: float = 1.0) -> None:
            v = r[idx]
            if v is None:
                return
            try:
                add(round(float(v) * scale, nd), unit, ref)
            except (TypeError, ValueError):
                return

        A(1, 1, "%")            # 涨幅
        A(2, 1, "%", 100)       # 位置分位
        A(3, 1, "%", 100)       # MA60 偏离
        A(4, 0, "万")           # 超大单净额(整数万)
        A(5, 1, "%")            # 获利盘
        A(6, 1, "")             # Δ5d(点)
        A(7, 1, "%", 100)       # 压力距(绝对值)
        A(8, 1, "%", 100)       # 支撑距(绝对值)
        A(9, 2, "元")           # 压力价
        A(10, 2, "元")          # 支撑价
        A(11, 2, "元")          # 成本中枢
        A(12, 2, "元")          # 主力低位筹码
        A(13, 0, "万")          # 5日大单
        A(14, 1, "")            # 综合分
        A(15, 0, "")            # hype 计数
        A(16, 0, "")            # risk 计数
        A(17, 0, "")            # 排名
    # 形态命中参数(副本长图呈现口径)
    for p_row in conn.execute(
            "SELECT ts_code, boom_pct, boom_vol_ratio, pullback_depth, "
            "vol_decay, plateau_high, breakout_pct FROM surge_pattern_hits "
            "WHERE trade_date=?", (day,)):
        ref = f"surge_pattern_hits {p_row[0]}"
        add(round(float(p_row[1]), 1), "%", ref)
        add(round(float(p_row[2]), 1), "倍", ref)
        add(round(float(p_row[3]) * 100, 1), "%", ref)
        add(round(float(p_row[4]) * 100), "%", ref)
        add(round(float(p_row[5]), 2), "元", ref)
        add(round(float(p_row[6]) * 100, 1), "%", ref)
    return facts


# ── 出口闸 ──

def gate_longpic(html: str, facts: list) -> list[str]:
    """mask style 后扫 token 对锚零未锚定;返回未锚定清单(空=放行)."""
    import re
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from davis_analyzer.cardgen.numbers import unmatched_tokens

    masked = re.sub(r"<style[^>]*>.*?</style>", "<style>□</style>", html, flags=re.S)
    masked = re.sub(r'style="[^"]*"', 'style="□"', masked)
    return [t.raw for t in unmatched_tokens(masked, facts)]


# ── html 组装(kit 骨架:tag→h1→sub→hook→编号小节→insight→foot) ──

def _head(title_tag: str, h1: str, sub: str) -> str:
    css = _KIT_CSS.read_text(encoding="utf-8") if _KIT_CSS.exists() else ""
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<style>{css}</style><style>{_ROOT_CSS}</style>"
            f"<style>{_EXTRA_CSS}</style></head><body>"
            f"<span class='tag'>{title_tag}</span>"
            f"<h1>{h1}</h1><div class='sub'>{sub}</div>")


def _foot(note: str) -> str:
    return (f"<div class='foot'>{note}<br>口径:docs/superpowers/specs/"
            f"2026-09-18-surge-screener-design.md · 程序化筛选不构成投资建议</div>"
            f"</body></html>")


def _fmt(v, nd=1) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    return "—" if v != v else f"{v:.{nd}f}"


def build_full_html(snap: pd.DataFrame, tags_map: dict[str, list[str]],
                    stats: SurgeStats) -> str:
    """全量长图: hook统计概览 + Top20 表 + Top12 深析卡."""
    top1 = stats.top1
    h1 = (f"{stats.pool_n} 只涨超 7%<br>今晚谁在台面上" if stats.pool_n
          else "今晚无人涨超 7%")
    parts = [_head(
        f"Surge 全量筛选 · {stats.day[:4]}-{stats.day[4:6]}-{stats.day[6:]}",
        h1, "九维台账全量入库,长图取综合分 Top20 与深析;全数据见 md 报告。")]
    ev = stats.event_counts
    parts.append(f"""
<div class='hook'>
  <div class='stats'>
    <div class='stat'><div class='v'>{stats.pool_n}</div><div class='k'>涨幅&gt;7%命中(只)</div></div>
    <div class='stat'><div class='v ice'>{ev.get('ma', 0)}</div><div class='k'>并购重组事件(条)</div></div>
    <div class='stat'><div class='v'>{stats.pattern_n}</div><div class='k'>形态副本命中(只)</div></div>
  </div>
  <p>筹码库 {stats.cyq_days} 日就位;巨潮覆盖 {stats.event_stock_n} 只个股;
     定增 {ev.get('refinance', 0)} 条 / 爆雷监管 {ev.get('distress', 0)} 条。</p>
</div>""")
    parts.append("""
<div class='card'><h2>一、综合分 Top 20</h2>
<div class='st'>九维加权(资金/筹码/获利盘/位置/压力支撑/炒作预期/扫雷),先验权重未校准</div>
<table><tr><th>#</th><th>代码</th><th>名称</th><th>涨幅</th><th>获利盘</th>
<th>压力距</th><th>支撑距</th><th> hype </th><th>雷</th><th>分</th></tr>""")
    for _, r in snap.sort_values('rank').head(20).iterrows():
        tlist = tags_map.get(r["ts_code"], [])
        parts.append(
            f"<tr><td>{int(r['rank'])}</td><td>{r['ts_code'].split('.')[0]}</td>"
            f"<td>{r.get('name') or ''}</td>"
            f"<td class='up'>{_fmt(r['pct_chg'])}%</td>"
            f"<td>{_fmt(r['winner_rate'])}%</td>"
            f"<td>+{_fmt(abs(r['resistance_dist']) * 100 if r['resistance_dist'] == r['resistance_dist'] else float('nan'))}%</td>"
            f"<td class='down'>-{_fmt(abs(r['support_dist']) * 100 if r['support_dist'] == r['support_dist'] else float('nan'))}%</td>"
            f"<td class='pos'>{int(r['hype_count'])}</td>"
            f"<td class='neg'>{int(r['risk_flag_count'])}</td>"
            f"<td><b>{_fmt(r['composite'])}</b></td></tr>")
    parts.append("</table></div>")
    parts.append("<div class='card'><h2>二、Top 12 深析</h2>"
                 "<div class='st'>压力支撑为最近档位;标签命中即列不互斥</div>")
    import json as _json
    for _, r in snap.sort_values('rank').head(12).iterrows():
        hype = _json.loads(r["hype_tags"] or "[]")
        risk = _json.loads(r["risk_flags"] or "[]")
        tl = "".join(f"<span class='tl h'>{t}</span>" for t in hype)
        tl += "".join(f"<span class='tl r'>{t}</span>" for t in risk)
        dd = r["dist_ma60"]
        parts.append(f"""
<div class='card deep-card'>
  <div><span class='rank-chip'>#{int(r['rank'])}</span>
    <b style='font-size:20px'>{r.get('name') or ''}</b>
    <span class='pv'>{r['ts_code']} · {r.get('industry') or '—'} · {_fmt(r['pct_chg'])}%</span></div>
  <table>
    <tr><td>位置分位</td><td>{_fmt(r['pos_250d'] * 100 if r['pos_250d'] == r['pos_250d'] else float('nan'))}%</td>
        <td>MA60 偏离</td><td>{_fmt(dd * 100 if dd == dd else float('nan'))}%</td></tr>
    <tr><td>超大单净额</td><td>{_fmt(r['elg_net_d0'], 0)}万</td>
        <td>五日大单+超大单</td><td>{_fmt(r['lg_net_5d'], 0)}万</td></tr>
    <tr><td>获利盘</td><td>{_fmt(r['winner_rate'])}%</td>
        <td>Δ五日</td><td>{_fmt(r['winner_delta_5d'])}</td></tr>
    <tr><td>成本中枢</td><td>{_fmt(r['weight_avg'], 2)}元</td>
        <td>主力低位筹码</td><td>{_fmt(r['cost_5pct'], 2)}元</td></tr>
    <tr><td>压力</td><td>{_fmt(r['resistance_price'], 2)}元(+{_fmt(abs(r['resistance_dist']) * 100 if r['resistance_dist'] == r['resistance_dist'] else float('nan'))}%)</td>
        <td>支撑</td><td>{_fmt(r['support_price'], 2)}元(-{_fmt(abs(r['support_dist']) * 100 if r['support_dist'] == r['support_dist'] else float('nan'))}%)</td></tr>
  </table>
  <div class='tagline'>{tl or '<span class=\'pv\'>无事件标签</span>'}</div>
</div>""")
    parts.append("</div>")
    tag_line = "、".join(f"{t}({n})" for t, n in stats.tag_top)
    parts.append(f"<div class='insight'>高频形态标签:{tag_line or '—'}。"
                 "获利盘拥挤与上方套牢近是本轮最普遍的结构特征。</div>")
    parts.append(_foot("数据:Tushare cyq_perf / 巨潮公告 / 本地台账;"
                       "出口数字闸:台账自动锚零未锚定。"))
    return "".join(parts)


def build_pattern_html(pat: pd.DataFrame, snap: pd.DataFrame,
                       stats: SurgeStats) -> str:
    """副本长图: 命中标的全部观察卡(两路径,事前不判别)."""
    h1 = (f"{stats.pattern_n} 只走出<br>放量阳→缩量回调→平台突破" if stats.pattern_n
          else "今晚没有标的走出<br>放量阳→缩量回调→平台突破")
    parts = [_head(
        f"Surge 形态副本 · {stats.day[:4]}-{stats.day[4:6]}-{stats.day[6:]}",
        h1, "C1 量能纪律(120 均量)∧C2 回调结构∧C3 平台突破;两路径观察,事前不判别。")]
    if pat.empty:
        parts.append("<div class='card'><p>当日无形态命中。"
                     "C1/C2/C3 为严苛复合条件,单日零命中属正常值。</p></div>")
    else:
        merged = pat.merge(snap, on=["trade_date", "ts_code"], how="left")
        parts.append("<div class='card'><h2>命中观察卡</h2>")
        for _, r in merged.iterrows():
            parts.append(f"""
<div class='card'>
  <div><b style='font-size:20px'>{r.get('name') or ''}</b>
    <span class='pv'>{r['ts_code']} · {_fmt(r.get('pct_chg'))}% · 综合分 {_fmt(r.get('composite'))}</span></div>
  <table>
    <tr><td>放量阳</td><td>{r['boom_date']}(+{_fmt(r['boom_pct'])}%,{_fmt(r['boom_vol_ratio'])}倍量)</td>
        <td>回调段</td><td>{r['pullback_start']}~{r['pullback_end']}</td></tr>
    <tr><td>高点回撤</td><td>{r['pullback_depth'] * 100:.1f}%</td>
        <td>量能衰减</td><td>{r['vol_decay'] * 100:.0f}%</td></tr>
    <tr><td>平台高点</td><td>{r['plateau_high']:.2f}元</td>
        <td>突破幅度</td><td>+{r['breakout_pct'] * 100:.1f}%</td></tr>
  </table>
  <p class='ladder'>路径①回抽平台确认:观察位 {r['plateau_high']:.2f} 元,缩量企稳可关注
     | 路径②直接续涨不回抽:更强</p>
</div>""")
        parts.append("</div>")
    parts.append(_foot("参数:PATTERN_PARAMS 冻结先验;命中即二元待测因子,"
                       "台账可回测两路径前向收益差。"))
    return "".join(parts)


# ── 渲染与总入口 ──

async def _render(html: str, png: Path) -> None:
    from playwright.async_api import async_playwright
    tmp = png.with_suffix(".tmp.html")
    tmp.write_text(html, encoding="utf-8")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(
            viewport={"width": 750, "height": 1200}, device_scale_factor=2)
        await page.goto(tmp.as_uri())
        await page.wait_for_timeout(300)
        h = await page.evaluate("document.body.scrollHeight")
        if h > 6000:
            mid = h // 2
            await page.screenshot(path=str(png.with_name(
                png.stem + "_上.png")),
                clip={"x": 0, "y": 0, "width": 750, "height": mid}, full_page=True)
            await page.screenshot(path=str(png.with_name(
                png.stem + "_下.png")),
                clip={"x": 0, "y": mid, "width": 750, "height": h - mid},
                full_page=True)
            logger.info("长图 {} → {h}px 拆上下", png.name, h=h)
        else:
            await page.screenshot(path=str(png), full_page=True)
            logger.info("长图 {} → {h}px 整张", png.name, h=h)
        await browser.close()
    tmp.unlink()


def run_longpics(conn: sqlite3.Connection, day: str,
                 snapshot_df: pd.DataFrame, pattern_df: pd.DataFrame,
                 tags_df: pd.DataFrame, *, out_dir: Path | None = None) -> list[Path]:
    """生成两张长图: 组装→出口数字闸(零未锚定才放行)→playwright 渲染."""
    import asyncio
    day = day.replace("-", "")
    out_dir = out_dir or SURGE_REPORTS_DIR
    out_dir = out_dir / day  # 按日期分目录归档
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = collect_stats(conn, day)
    facts = snapshot_facts(conn, day, stats)
    tags_map: dict[str, list[str]] = {}
    if tags_df is not None and not tags_df.empty:
        for _, t in tags_df.iterrows():
            tags_map.setdefault(t["ts_code"], []).append(t["tag"])
    outputs: list[Path] = []
    for name, html in (
            (f"长图_全量_{day}", build_full_html(snapshot_df, tags_map, stats)),
            (f"长图_副本_{day}", build_pattern_html(pattern_df, snapshot_df, stats))):
        bad = gate_longpic(html, facts)
        if bad:
            raise RuntimeError(
                f"surge 长图数字闸未过({name}),未锚定 {len(bad)} 个: {bad[:8]}")
        logger.info("数字闸放行: {} 零未锚定", name)
        png = out_dir / f"{name}.png"
        asyncio.run(_render(html, png))
        (out_dir / f"{name}.html").write_text(html, encoding="utf-8")
        outputs.append(png)
    return outputs
