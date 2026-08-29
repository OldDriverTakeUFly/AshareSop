# davis_analyzer/cardgen/video2.py — 动效财经解说风视频(土豆看财报式)
# 与 video.py(v1 Ken Burns 静卡轮播)的差异:
#   ① 分片重构:钩子场独立(大字+杀手数字),每场只讲一个论点,内容精选不再整卡复刻
#   ② 动效:数字滚动 countup / 条形图生长 / 逐行滑入 / 高亮下划线,CSS+JS 实时驱动
#   ③ 录制:playwright 直接录页面动画(real-time screencast),转场场内淡入淡出
# 用法:python -m davis_analyzer.cardgen video --topic X --style motion
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

W, H = 1080, 1920
PAD_TAIL = 0.5
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

_CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
html,body { width:1080px; height:1920px; overflow:hidden;
  font-family:'Noto Sans CJK SC','PingFang SC',sans-serif;
  background:#0b1020; color:#f2f4fa; }
.stage { width:1080px; height:1920px; padding:90px 70px; display:flex;
  flex-direction:column; justify-content:center; position:relative;
  animation: fadein .5s ease both; }
@keyframes fadein { from{opacity:0} to{opacity:1} }
@keyframes riseIn { from{opacity:0; transform:translateY(60px)} to{opacity:1; transform:none} }
@keyframes growBar { from{width:0} to{width:var(--w)} }
@keyframes flashHL { 0%,100%{background:transparent} 15%{background:rgba(255,36,66,.18)} }
.kicker { color:#ff2442; font-size:44px; font-weight:800; letter-spacing:6px;
  margin-bottom:36px; animation: riseIn .5s .1s ease both; }
.big { font-size:96px; font-weight:900; line-height:1.25; margin-bottom:44px;
  animation: riseIn .6s .25s ease both; }
.big em { font-style:normal; color:#ffd400; }
.huge { font-size:230px; font-weight:900; color:#ffd400; line-height:1;
  animation: riseIn .5s .3s ease both; }
.huge small { font-size:90px; color:#f2f4fa; font-weight:800; }
.cap { font-size:52px; color:#aab3c8; margin-top:30px; animation: riseIn .5s .55s ease both; }
.row { border-left:10px solid var(--c,#ff2442); padding:18px 0 18px 34px;
  margin-bottom:40px; animation: riseIn .55s ease both; }
.row .name { font-size:56px; font-weight:800; margin-bottom:10px; }
.row .desc { font-size:42px; color:#c3cad9; line-height:1.5; }
.row .desc b { color:#ffd400; }
.barwrap { margin-bottom:52px; animation: riseIn .5s ease both; }
.barlabel { display:flex; justify-content:space-between; font-size:52px;
  font-weight:800; margin-bottom:14px; }
.barlabel .v { color:var(--c,#ff2442); }
.bartrack { height:52px; background:#1a2236; border-radius:26px; overflow:hidden; }
.barfill { height:100%; border-radius:26px; background:var(--c,#ff2442);
  width:var(--w); animation: growBar 1.1s cubic-bezier(.2,.7,.2,1) both; }
.hl { animation: flashHL 1.6s .8s ease both; padding:4px 12px; border-radius:8px; }
.foot { position:absolute; bottom:56px; left:70px; right:70px; font-size:34px;
  color:#5f6a80; }
.trow { display:flex; font-size:46px; padding:22px 0; border-bottom:2px solid #1a2236;
  animation: riseIn .5s ease both; }
.trow .c1 { width:300px; font-weight:800; flex:none; }
.trow .c2 { color:#c3cad9; }
.quote { font-size:60px; line-height:1.6; font-weight:700;
  border-left:14px solid #ffd400; padding-left:40px; animation: riseIn .6s .3s ease both; }
"""


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _keep_bold(s: str) -> str:
    """保留 <b> 为高亮,其余标签剥掉。"""
    s = re.sub(r"<br\s*/?>", " ", s or "")
    s = re.sub(r"<(?!/?(b|em|span))[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _num_parts(value_display: str) -> tuple[str, str]:
    """'400-600亿'→('400-600','亿');'+130.4%'→('130.4','%');'143x'→('143','x')。"""
    m = re.match(r"^([+≈~～\-]?[\d,.\-~]+)\s*(.*)$", value_display.strip())
    return (m.group(1).lstrip("+≈~～"), m.group(2)) if m else (value_display, "")


def _page(body: str, extra_js: str = "") -> str:
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<style>{_CSS}</style></head><body><div class='stage'>{body}</div>"
            f"<script>{extra_js}</script></body></html>")


_COUNTUP_JS = """
function cu(el, target, dur, fmt) {
  const t0 = performance.now();
  function step(t) {
    const p = Math.min((t - t0) / dur, 1), e = 1 - Math.pow(1 - p, 3);
    el.textContent = fmt(target * e);
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
"""


# ── 场景生成(每场=一段 HTML+旁白文本)──
def scenes_from_spec(spec: dict, facts: dict[str, dict]) -> list[dict]:
    """从物化 spec+facts 生成动效场景:钩子场独立,每场一个论点,内容精选。"""
    cards = spec.get("cards", [])
    sc: list[dict] = []

    # 钩子场:封面标题 + 杀手数字(第一个最冲击的 stat)
    cov = cards[0] if cards else {}
    killer = None
    for st in cov.get("stats", []):
        v = st.get("v", "")
        if re.match(r"^[+≈~～\-]?[\\d.]", str(v)) and len(str(v)) <= 12:
            killer = (str(v), _strip_html(str(st.get("k", ""))))
            break
    hook_title = _keep_bold(cov.get("title", ""))[:24]
    hook_html = (f"<div class='kicker'>{_strip_html(cov.get('tag_top',''))}</div>"
                 f"<div class='big'>{hook_title}</div>")
    if killer:
        n, suf = _num_parts(killer[0])
        hook_html += (f"<div class='huge'><span data-count='{n}'>0</span>"
                      f"<small>{suf}</small></div><div class='cap'>{killer[1]}</div>")
    hook_html += f"<div class='foot'>{_strip_html(cov.get('foot',''))[:40]}</div>"
    nar = f"{hook_title}。{killer[1]}:{killer[0]}。" if killer else f"{hook_title}。"
    sc.append({"html": _page(hook_html, _COUNTUP_JS + _countup_call(killer[0]) if killer else ""),
               "narration": nar, "dur": 6.0})

    for c in cards[1:]:
        t = c.get("type")
        title = _keep_bold(c.get("title", ""))[:30]
        if t == "profiles":
            rows = "".join(
                f"<div class='row' style='--c:{['#ff2442','#22c55e','#3b82f6','#a855f7'][i % 4]};"
                f"animation-delay:{.3 + i * .5:.2f}s'>"
                f"<div class='name'>{_keep_bold(r.get('name',''))}</div>"
                f"<div class='desc'>{_keep_bold(r.get('desc',''))[:56]}</div></div>"
                for i, r in enumerate(c.get("rows", [])[:4]))
            body = f"<div class='kicker'>{_strip_html(c.get('tag_top',''))}</div><div class='big'>{title}</div>{rows}"
            nar = "。".join(_strip_html(r.get("name", "")) for r in c.get("rows", [])[:4])
        elif t == "table":
            trs = "".join(
                f"<div class='trow' style='animation-delay:{.35 + i * .55:.2f}s'>"
                f"<div class='c1'>{_keep_bold(str(r['cells'][0]))}</div>"
                f"<div class='c2'>{_keep_bold(' '.join(str(x) for x in r['cells'][1:2]))}</div></div>"
                for i, r in enumerate(c.get("table", {}).get("rows", [])[:4]))
            body = f"<div class='kicker'>{_strip_html(c.get('tag_top',''))}</div><div class='big'>{title}</div>{trs}"
            nar = title
        elif t == "bars":
            vals = []
            bs = []
            for i, b in enumerate(c.get("bars", [])[:4]):
                disp = _strip_html(str(b.get("value", "")))
                n, suf = _num_parts(disp)
                try:
                    num = float(re.sub(r"[^\d.]", "", n) or 0)
                except ValueError:
                    num = 0
                vals.append(num)
                pct = b.get("pct") or 100
                bs.append(f"<div class='barwrap' style='--c:{b.get('color','#ff2442').replace('red','#ff2442').replace('orange','#f97316').replace('green','#22c55e').replace('purple','#a855f7').replace('gray','#64748b')};animation-delay:{.25 + i * .4:.2f}s'>"
                          f"<div class='barlabel'><span>{_strip_html(b.get('label',''))}</span>"
                          f"<span class='v' data-count='{n}'>{n}</span></div>"
                          f"<div class='bartrack'><div class='barfill' style='--w:{pct}%'></div></div></div>")
            body = (f"<div class='kicker'>{_strip_html(c.get('tag_top',''))}</div>"
                    f"<div class='big'>{title}</div>{''.join(bs)}")
            nar = title + "。" + "。".join(_strip_html(b.get("label", "")) for b in c.get("bars", [])[:4])
        else:  # timeline / summary / 其他 → kbox/row 逐条揭示
            items = c.get("kboxes") or c.get("rows") or []
            rows = "".join(
                f"<div class='row' style='--c:{['#ff2442','#f97316','#3b82f6','#a855f7','#22c55e'][i % 5]};"
                f"animation-delay:{.3 + i * .45:.2f}s'>"
                f"<div class='name'>{_strip_html(k.get('date') or '')} {_keep_bold(k.get('html','') if k.get('html') else k.get('desc',''))[:52]}</div></div>"
                for i, k in enumerate(items[:5]))
            body = f"<div class='kicker'>{_strip_html(c.get('tag_top',''))}</div><div class='big'>{title}</div>{rows}"
            nar = title
        body += f"<div class='foot'>{_strip_html(c.get('foot',''))[:40]}</div>"
        sc.append({"html": _page(body), "narration": nar[:110], "dur": None})
    return sc


def _countup_call(display: str) -> str:
    n, _ = _num_parts(display)
    nnum = re.sub(r"[^\d.]", "", n) or "0"
    dec = 1 if "." in nnum else 0
    return (f"cu(document.querySelector('.huge span'),{float(nnum) or 0},900,"
            f"v=>v.toFixed({dec}))")


def audio_duration(path: Path) -> float:
    r = subprocess.run([ffmpeg(), "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", r.stderr)
    return (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            if m else 4.0)


def record_scene(html: str, dur: float, out_webm: Path) -> Path:
    """playwright 录制单场动画(real-time)。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(Path(__file__).resolve().parents[2] / "storage" / "video_render_profile"),
            headless=False, viewport={"width": W, "height": H},
            record_video_dir=str(out_webm.parent),
            record_video_size={"width": W, "height": H})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_content(html, wait_until="domcontentloaded")
        page.wait_for_timeout(int(dur * 1000) + 400)
        video = page.video
        vpath = Path(video.path()) if video else None
        ctx.close()
        if vpath and vpath.exists():
            import shutil
            shutil.move(str(vpath), str(out_webm))
    return out_webm


def scene_mp4(webm: Path, audio: Path | None, dur: float, out: Path) -> Path:
    cmd = [ffmpeg(), "-y", "-i", str(webm)]
    if audio:
        cmd += ["-i", str(audio)]
    cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "22", "-r", "30",
            "-vf", "format=yuv420p", "-t", f"{dur:.2f}"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-af",
                f"adelay=200|200,apad,afade=t=out:st={dur - 0.3:.2f}:d=0.3", "-shortest"]
    cmd += [str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"scene 转码失败: {r.stderr[-300:]}")
    return out


def concat(clips: list[Path], out: Path) -> Path:
    lst = out.parent / "concat2.txt"
    lst.write_text("\n".join(f"file '{c}'" for c in clips), encoding="utf-8")
    r = subprocess.run([ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"concat 失败: {r.stderr[-300:]}")
    return out


def gen_motion_video(project_dir: Path, topic: str, voice: str = DEFAULT_VOICE) -> Path:
    """主入口:物化 spec → 动效场景 → 逐场录制+旁白 → 拼接 mp4。"""
    import edge_tts

    out_dir = project_dir / "output"
    spec = json.loads((out_dir / "spec.materialized.json").read_text(encoding="utf-8"))
    facts = {}  # 场景直接用物化 display,无需 facts 再查
    scenes = scenes_from_spec(spec, facts)
    with tempfile.TemporaryDirectory(prefix="vid2_") as td:
        tdp = Path(td)
        clips: list[Path] = []
        for i, s in enumerate(scenes):
            nar = s["narration"]
            a: Path | None = None
            if nar:
                a = tdp / f"n{i:02d}.mp3"
                asyncio.run(edge_tts.Communicate(nar, voice).save(str(a)))
            dur = s["dur"] or (audio_duration(a) + PAD_TAIL if a else 4.5)
            webm = tdp / f"s{i:02d}.webm"
            record_scene(s["html"], dur, webm)
            clips.append(scene_mp4(webm, a, dur + 0.3, tdp / f"c{i:02d}.mp4"))
            logger.info(f"场景 {i + 1}/{len(scenes)} 完成({dur:.1f}s)")
        final = concat(clips, out_dir / f"{topic}_motion.mp4")
    logger.info(f"动效视频完成: {final} ({final.stat().st_size / 1048576:.1f}MB, "
                f"{audio_duration(final):.0f}s)")
    return final
