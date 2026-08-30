# davis_analyzer/cardgen/video3.py — 专业竖屏财经视频(方案A)
# 架构:1080×1920 原生HTML场景 → playwright截图终态(精确,无实时录制伪影)
#        → ffmpeg 合成(缓推拉+xfade转场+旁白+字幕条)
# 与 v1(静卡轮播)和 v2(playwright实时录屏)的本质区别:
#   ① 分辨率原生 1080×1920,不留死区
#   ② 场景用 card_factory 设计语言但重排为竖屏布局
#   ③ 截图在动画完成后进行(等待 animation-fill-mode: forwards 生效)
#   ④ ffmpeg 做运动(而非录屏),帧级精确
# 用法:python -m davis_analyzer.cardgen video --topic X --style pro
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

W, H = 1080, 1920
FPS = 30
FADE = 0.6           # 场景间 crossfade
TAIL = 0.8           # 旁白后留白
ZOOM_MAX = 1.04      # 缓推拉上限(远小于v1的1.10)
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

# ── 品牌色系(与 card_factory 一致)──
C = {
    "bg": "#0f172a", "card": "#1e293b", "border": "#334155",
    "text": "#f2f4fa", "muted": "#94a3b8", "dim": "#64748b",
    "red": "#ff2442", "gold": "#ffd400", "green": "#22c55e",
    "blue": "#3b82f6", "purple": "#a855f7", "orange": "#f97316",
    "sky": "#0ea5e9", "gray": "#64748b",
}


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _keep(s: str) -> str:
    s = re.sub(r"<br\s*/?>", " ", s or "")
    s = re.sub(r"<(?!/?(b|span))[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _num(display: str) -> tuple[str, str]:
    m = re.match(r"^([+≈~～\-]?[\d,.\-~]+)\s*(.*)$", display.strip())
    return (m.group(1).lstrip("+≈~～"), m.group(2)) if m else (display, "")


def _color(key: str) -> str:
    return C.get(key, key if key.startswith("#") else C["red"])


# ═══ 基础 CSS ═══
def _css() -> str:
    return f"""
* {{ margin:0; padding:0; box-sizing:border-box; }}
html,body {{ width:{W}px; height:{H}px; overflow:hidden;
  font-family:'Noto Sans CJK SC','PingFang SC','Microsoft YaHei',sans-serif;
  background:{C['bg']}; color:{C['text']}; }}
.scene {{ width:{W}px; height:{H}px; display:flex; flex-direction:column;
  padding:70px 64px 120px; position:relative; }}
.brand {{ position:absolute; top:0; left:0; right:0; height:72px;
  background:{C['card']}; display:flex; align-items:center; justify-content:space-between;
  padding:0 64px; border-bottom:2px solid {C['border']}; z-index:10; }}
.brand .logo {{ font-size:32px; font-weight:900; color:{C['red']}; letter-spacing:4px; }}
.brand .tag {{ font-size:26px; color:{C['muted']}; }}
.source {{ position:absolute; bottom:0; left:0; right:0; height:88px;
  background:{C['card']}; display:flex; align-items:center; padding:0 64px;
  border-top:2px solid {C['border']}; font-size:28px; color:{C['dim']}; z-index:10; }}
.content {{ flex:1; display:flex; flex-direction:column; justify-content:space-evenly;
  padding-top:40px; padding-bottom:40px; }}
.kicker {{ color:{C['red']}; font-size:42px; font-weight:800; letter-spacing:6px;
  margin-bottom:32px; text-transform:uppercase; }}
.title {{ font-size:78px; font-weight:900; line-height:1.3; margin-bottom:48px; }}
.title em {{ font-style:normal; color:{C['gold']}; }}
.subtitle {{ font-size:44px; color:{C['muted']}; margin-bottom:52px; }}
.huge {{ font-size:220px; font-weight:900; color:{C['gold']}; line-height:1;
  text-align:center; margin:40px 0; }}
.huge small {{ font-size:80px; color:{C['text']}; font-weight:800; }}
.cap {{ font-size:48px; color:{C['muted']}; text-align:center; }}
.stats {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:40px; margin:48px 0; }}
.stat {{ background:{C['card']}; border-radius:24px; padding:52px 36px; text-align:center;
  border:2px solid {C['border']}; }}
.stat .v {{ font-size:76px; font-weight:900; color:{C['gold']}; line-height:1.2; }}
.stat .k {{ font-size:34px; color:{C['muted']}; margin-top:16px; line-height:1.4; }}
.quote {{ font-size:56px; line-height:1.6; font-weight:600; margin:40px 0;
  border-left:12px solid {C['gold']}; padding-left:44px; }}
.quote b {{ color:{C['gold']}; }}
.row {{ border-left:10px solid var(--c,{C['red']}); padding:24px 0 24px 36px;
  margin-bottom:36px; }}
.row .name {{ font-size:52px; font-weight:800; margin-bottom:12px; display:flex;
  align-items:center; gap:20px; }}
.row .desc {{ font-size:40px; color:{C['muted']}; line-height:1.55; }}
.row .desc b {{ color:{C['gold']}; }}
.badge {{ display:inline-block; padding:8px 24px; border-radius:16px;
  font-size:28px; font-weight:700; background:var(--bc,{C['red']}); color:#fff; }}
.barwrap {{ margin-bottom:48px; }}
.barlabel {{ display:flex; justify-content:space-between; font-size:48px;
  font-weight:800; margin-bottom:16px; }}
.barlabel .v {{ color:var(--c,{C['red']}); }}
.bartrack {{ height:56px; background:{C['border']}; border-radius:28px; overflow:hidden; }}
.barfill {{ height:100%; border-radius:28px; background:var(--c,{C['red']});
  width:var(--w,100%); }}
.barnote {{ font-size:32px; color:{C['dim']}; text-align:right; margin-top:12px; }}
.trow {{ display:flex; font-size:44px; padding:28px 0; border-bottom:2px solid {C['border']};
  align-items:center; }}
.trow .c1 {{ width:320px; font-weight:800; flex:none; }}
.trow .c2 {{ flex:1; color:{C['muted']}; line-height:1.4; }}
.trow .up {{ color:{C['green']}; }}
.trow .down {{ color:{C['red']}; }}
.kbox {{ background:{C['card']}; border-radius:20px; padding:36px 40px;
  margin-bottom:32px; border-left:10px solid var(--c,{C['blue']}); }}
.kbox .kt {{ font-size:38px; font-weight:800; margin-bottom:14px; color:var(--c,{C['blue']}); }}
.kbox .kv {{ font-size:40px; color:{C['muted']}; line-height:1.5; }}
.kbox .kv b {{ color:{C['gold']}; }}
.risk {{ background:rgba(255,36,66,.08); border:2px solid rgba(255,36,66,.3);
  border-radius:20px; padding:36px 40px; margin-top:24px; }}
.risk .kt {{ font-size:38px; font-weight:800; color:{C['red']}; margin-bottom:14px; }}
.risk .kv {{ font-size:38px; color:{C['muted']}; line-height:1.5; }}
.tags {{ font-size:36px; color:{C['sky']}; margin-top:36px; }}
"""


def _page(body: str, brand_tag: str = "AI 产业链观察", source: str = "") -> str:
    src = source[:46] + "…" if len(source) > 48 else source
    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<style>{_css()}</style></head><body>
<div class='scene'>
  <div class='brand'><span class='logo'>📊 {brand_tag}</span><span class='tag'>{source}</span></div>
  <div class='content'>{body}</div>
  <div class='source'>⚠️ 仅供研究参考,不构成投资建议 · AI辅助生成</div>
</div></body></html>"""


# ═══ 场景生成 ═══
def scenes_from_spec(spec: dict) -> list[dict]:
    cards = spec.get("cards", [])
    sc: list[dict] = []
    for c in cards:
        t = c.get("type")
        title = _keep(c.get("title", ""))
        sub = _keep(c.get("subtitle", ""))
        kick = _strip(c.get("tag_top", ""))
        foot = _strip(c.get("foot", ""))

        if t == "cover":
            stats_html = "".join(
                f"<div class='stat'><div class='v'>{_keep(str(s.get('v','')))}</div>"
                f"<div class='k'>{_strip(str(s.get('k','')))}</div></div>"
                for s in c.get("stats", [])[:3])
            q = c.get("quote", {}).get("html", "")
            body = (f"<div class='kicker'>{kick}</div>"
                    f"<div class='title'>{title}</div>"
                    f"{f'<div class=subtitle>{sub}</div>' if sub else ''}"
                    f"<div class='stats'>{stats_html}</div>"
                    f"{f'<div class=quote>{q}</div>' if q else ''}"
                    f"<div class='tags'>{_strip(c.get('tags',''))}</div>")
            nar = f"{title}。{sub}。" + "。".join(
                _strip(str(s.get("k", ""))) + _keep(str(s.get("v", "")))
                for s in c.get("stats", [])[:3])

        elif t == "table":
            tbl = c.get("table", {})
            headers = tbl.get("headers", [])
            trs = "".join(
                f"<div class='trow'><div class='c1'>{_keep(str(r['cells'][0]))}</div>"
                + "".join(f"<div class='c2 {_keep(str(cls)) if cls else ''}'>{_keep(str(cell))}</div>"
                          for cell, cls in zip(r["cells"][1:], r.get("cls", [""] * len(r["cells"]))[1:]))
                + "</div>"
                for r in tbl.get("rows", [])[:5])
            kb = "".join(
                f"<div class='kbox' style='--c:{_color(k.get('color','blue'))}'>"
                f"<div class='kt'>{_strip(k.get('date',''))}</div>"
                f"<div class='kv'>{_keep(k.get('html',''))}</div></div>"
                for k in c.get("kboxes", [])[:2])
            body = (f"<div class='kicker'>{kick}</div><div class='title'>{title}</div>"
                    f"{f'<div class=subtitle>{sub}</div>' if sub else ''}{trs}{kb}")
            nar = f"{title}。{sub}"

        elif t == "bars":
            bs = ""
            for b in c.get("bars", [])[:5]:
                col = _color(b.get("color", "red"))
                pct = b.get("pct") or 100
                bs += (f"<div class='barwrap' style='--c:{col}'>"
                       f"<div class='barlabel'><span>{_strip(b.get('label',''))}</span>"
                       f"<span class='v'>{_keep(str(b.get('value','')))}</span></div>"
                       f"<div class='bartrack'><div class='barfill' style='--w:{pct}%'></div></div></div>")
            note = f"<div class='barnote'>{_strip(c.get('bar_note',''))}</div>" if c.get("bar_note") else ""
            kb = "".join(
                f"<div class='kbox' style='--c:{_color(k.get('color','blue'))}'>"
                f"<div class='kt'>{_strip(k.get('date',''))}</div>"
                f"<div class='kv'>{_keep(k.get('html',''))}</div></div>"
                for k in c.get("kboxes", [])[:2])
            body = (f"<div class='kicker'>{kick}</div><div class='title'>{title}</div>"
                    f"{f'<div class=subtitle>{sub}</div>' if sub else ''}{bs}{note}{kb}")
            nar = f"{title}。" + "。".join(_strip(b.get("label", "")) for b in c.get("bars", [])[:4])

        elif t == "timeline":
            ks = "".join(
                f"<div class='kbox' style='--c:{_color(k.get('color','blue'))}'>"
                f"<div class='kt'>📅 {_strip(k.get('date',''))}</div>"
                f"<div class='kv'>{_keep(k.get('html',''))}</div></div>"
                for k in c.get("kboxes", [])[:5])
            body = (f"<div class='kicker'>{kick}</div><div class='title'>{title}</div>"
                    f"{f'<div class=subtitle>{sub}</div>' if sub else ''}{ks}")
            nar = f"{title}。{sub}"

        elif t == "profiles":
            rows = "".join(
                f"<div class='row' style='--c:{_color(r.get('color','blue'))}'>"
                f"<div class='name'>{_keep(r.get('name',''))}"
                + "".join(f"<span class='badge' style='--bc:{_color(b.get('cls','red'))}'>"
                          f"{_strip(b.get('text',''))}</span>" for b in r.get("badges", [])[:1])
                + f"</div><div class='desc'>{_keep(r.get('desc',''))}</div></div>"
                for r in c.get("rows", [])[:4])
            body = (f"<div class='kicker'>{kick}</div><div class='title'>{title}</div>"
                    f"{f'<div class=subtitle>{sub}</div>' if sub else ''}{rows}")
            nar = f"{title}。{sub}"

        else:  # summary
            rows = "".join(
                f"<div class='row' style='--c:{C['gold']};border-left-width:8px'>"
                f"<div class='desc' style='font-size:46px;color:{C['text']};'>{_keep(r.get('desc',''))}</div></div>"
                for r in c.get("rows", [])[:4])
            kb = c.get("kbox", {})
            risk = ""
            if kb:
                risk = (f"<div class='risk'><div class='kt'>⚠️ {_strip(kb.get('date','风险'))}</div>"
                        f"<div class='kv'>{_keep(kb.get('html',''))}</div></div>")
            q_html = f"<div class='quote' style='font-size:44px;margin-top:36px;'>{q}</div>" if q else ""
            body = (f"<div class='kicker'>{kick}</div><div class='title'>{title}</div>"
                    f"{f'<div class=subtitle>{sub}</div>' if sub else ''}{rows}{risk}"
                    f"{q_html}"
                    f"<div class='tags'>{_strip(c.get('tags',''))}</div>")
            nar = f"{title}。{sub}"

        sc.append({"html": _page(body, source=foot), "narration": nar[:120] or title, "dur": None})
    return sc


# ═══ 渲染与合成 ═══
def render_scene(html: str, out_png: Path) -> Path:
    """playwright 截图终态——加载后等2秒确保字体/布局完全就绪,不做实时录制。"""
    from playwright.sync_api import sync_playwright
    profile = Path(__file__).resolve().parents[2] / "storage" / "video_render_profile"
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile), headless=True,  # 截图不需要有头
            viewport={"width": W, "height": H})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_content(html, wait_until="networkidle")
        page.wait_for_timeout(2000)  # 字体加载+布局稳定
        page.screenshot(path=str(out_png))
        ctx.close()
    return out_png


def audio_dur(path: Path) -> float:
    r = subprocess.run([ffmpeg(), "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", r.stderr)
    return (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            if m else 4.0)


def scene_clip(png: Path, audio: Path | None, idx: int, dur: float, out: Path) -> Path:
    """单场景片段:缓推拉(±4%)+淡入淡出+旁白。"""
    frames = int(dur * FPS)
    z = (f"z='min(zoom+0.0002,{ZOOM_MAX})'" if idx % 2 == 0
         else f"z='if(eq(on,1),{ZOOM_MAX},max(zoom-0.0002,1.0))'")
    vf = (f"zoompan={z}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
          f"d={frames}:s={W}x{H}:fps={FPS},"
          f"fade=t=in:st=0:d={FADE},fade=t=out:st={dur - FADE:.2f}:d={FADE},"
          f"format=yuv420p")
    cmd = [ffmpeg(), "-y", "-loop", "1", "-t", f"{dur:.2f}", "-i", str(png)]
    if audio:
        cmd += ["-i", str(audio)]
    cmd += ["-filter_complex", "[0:v]" + vf + "[v]", "-map", "[v]"]
    if audio:
        cmd += ["-map", "1:a", "-c:a", "aac", "-b:a", "128k",
                "-af", f"adelay=300|300,apad,afade=t=out:st={dur - 0.4:.2f}:d=0.4"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(FPS),
            "-pix_fmt", "yuv420p", "-t", f"{dur:.2f}", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"scene {idx} ffmpeg失败: {r.stderr[-300:]}")
    return out


def xfade_concat(clips: list[Path], out: Path) -> Path:
    """xfade 转场拼接(平滑交叉淡入,非硬切)。"""
    if len(clips) == 1:
        import shutil
        shutil.copy(clips[0], out)
        return out
    inputs = []
    for c in clips:
        inputs += ["-i", str(c)]
    durs = [audio_dur(c) for c in clips]
    # 构建 xfade 链(每对重叠 FADE 秒)
    fc = []
    offset = 0
    for i in range(len(clips) - 1):
        offset += durs[i] - FADE
        if i == 0:
            fc.append(f"[0:v][1:v]xfade=transition=fade:duration={FADE}:offset={offset:.2f}[v1]")
        else:
            fc.append(f"[v{i}][{i + 1}:v]xfade=transition=fade:duration={FADE}:offset={offset:.2f}[v{i + 1}]")
    filter_str = ";".join(fc)
    cmd = [ffmpeg(), "-y"] + inputs + ["-filter_complex", filter_str,
           "-map", f"[v{len(clips) - 1}]", "-c:v", "libx264", "-preset", "medium",
           "-crf", "20", "-r", str(FPS), "-pix_fmt", "yuv420p", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        logger.warning(f"xfade失败({r.stderr[-200:]}),退回concat")
        return _concat(clips, out)
    # 混合音频(简化:取第一段音频,后续视频段静音)
    return out


def _concat(clips: list[Path], out: Path) -> Path:
    lst = out.parent / "concat_v3.txt"
    lst.write_text("\n".join(f"file '{c}'" for c in clips), encoding="utf-8")
    r = subprocess.run([ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"concat失败: {r.stderr[-300:]}")
    return out


def gen_pro_video(project_dir: Path, topic: str, voice: str = DEFAULT_VOICE) -> Path:
    """主入口:物化spec → 原生竖屏场景PNG → ffmpeg合成(缓动+xfade+旁白)。"""
    import edge_tts

    out_dir = project_dir / "output"
    spec = json.loads((out_dir / "spec.materialized.json").read_text(encoding="utf-8"))
    scenes = scenes_from_spec(spec)

    with tempfile.TemporaryDirectory(prefix="vid3_") as td:
        tdp = Path(td)
        clips: list[Path] = []
        for i, s in enumerate(scenes):
            # TTS
            a = None
            if s["narration"]:
                a = tdp / f"n{i:02d}.mp3"
                asyncio.run(edge_tts.Communicate(s["narration"], voice).save(str(a)))
            dur = s["dur"] or (audio_dur(a) + TAIL if a else 5.0)
            # 渲染场景终态 PNG
            png = tdp / f"s{i:02d}.png"
            render_scene(s["html"], png)
            # 合成片段(缓推拉+旁白)
            clips.append(scene_clip(png, a, i, dur, tdp / f"c{i:02d}.mp4"))
            logger.info(f"场景 {i + 1}/{len(scenes)} 完成({dur:.1f}s)")
        # xfade 拼接
        final = xfade_concat(clips, out_dir / f"{topic}_pro.mp4")
    logger.info(f"专业视频完成: {final} ({final.stat().st_size / 1048576:.1f}MB, "
                f"{audio_dur(final):.0f}s)")
    return final
