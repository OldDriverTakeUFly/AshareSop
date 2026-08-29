# davis_analyzer/cardgen/video.py — 卡片 → 竖屏视频(Ken Burns 动效 + edge-tts 中文旁白)
# 产出:output/<topic>_video.mp4(1080x1920, 每卡一段:模糊底+整卡居中+缓推拉+淡入淡出+旁白)
# 用法:python -m davis_analyzer.cardgen video --topic GPU四小龙 [--voice zh-CN-YunxiNeural] [--no-tts]
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from loguru import logger

REPO_ROOT = Path(__file__).resolve().parents[2]
W, H = 1080, 1920          # 小红书竖屏
CARD_W, CARD_H = 1080, 1440
FADE = 0.4                 # 每段淡入淡出
PAD_TAIL = 0.8             # 旁白后的留白
DEFAULT_VOICE = "zh-CN-YiaoxiaoNeural"  # 备选: zh-CN-YunxiNeural(男)/zh-CN-XiaoyiNeural


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def narration_texts(spec: dict) -> list[str]:
    """从物化 spec 每卡导出旁白文本:标题+副题+首条正文,截断到 90 字。"""
    out = []
    for c in spec.get("cards", []):
        parts = [_strip_html(c.get("title", ""))]
        if c.get("subtitle"):
            parts.append(_strip_html(c["subtitle"]))
        body = ""
        for k in ("rows", "kboxes"):
            for item in c.get(k, []) or []:
                t = _strip_html(item.get("desc") or item.get("html") or "")
                if t:
                    body = t
                    break
            if body:
                break
        if not body:
            t = c.get("table")
            if t and t.get("rows"):
                body = _strip_html(" ".join(t["rows"][0]["cells"][:2]))
        if body:
            parts.append(body)
        text = ",".join(p for p in parts if p)
        out.append(text[:90])
    return out


def tts_sync(texts: list[str], voice: str, outdir: Path) -> list[Path]:
    """逐卡合成旁白 mp3(edge-tts,同步包装)。"""
    import edge_tts

    files: list[Path] = []

    async def one(i: int, text: str) -> None:
        p = outdir / f"nar_{i:02d}.mp3"
        await edge_tts.Communicate(text, voice).save(str(p))
        files.append(p)  # noqa: B023 (asyncio 顺序保证)

    async def run() -> None:
        for i, t in enumerate(texts):
            await one(i, t)
    asyncio.run(run())
    return sorted(outdir.glob("nar_*.mp3"))


def audio_duration(path: Path) -> float:
    """ffmpeg -i 解析 Duration(imageio 静态包无 ffprobe)。"""
    r = subprocess.run([ffmpeg(), "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", r.stderr)
    if not m:
        return 3.0
    h_, m_, s_ = m.groups()
    return int(h_) * 3600 + int(m_) * 60 + float(s_)


def build_clip(png: Path, audio: Path | None, idx: int, out: Path) -> Path:
    """单卡片段:模糊放大底 + 原卡居中 + 交替缓推拉 + 淡入淡出 + 旁白音轨。"""
    dur = (audio_duration(audio) if audio else 4.0) + PAD_TAIL
    # 交替推/拉(zoompan 240帧≈1s@25fps 输入帧,d=总帧数)
    frames = int(dur * 25)
    z_in = f"z='min(zoom+0.0006,1.10)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    z_out = f"z='if(eq(on,1),1.10,max(zoom-0.0006,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    z = z_in if idx % 2 == 0 else z_out
    vf = (
        # 底:卡放大裁成 9:16 后高斯模糊
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"gblur=sigma=28,eq=brightness=-0.05[bg];"
        # 前景:整卡缩放到宽1080,居中,缓推拉
        f"[0:v]scale={W}:-1,zoompan={z}:d={frames}:s={W}x{CARD_H}:fps=25,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black@0[fg];"
        f"[bg][fg]overlay=0:0,"
        f"fade=t=in:st=0:d={FADE},fade=t=out:st={dur - FADE:.2f}:d={FADE},"
        f"format=yuv420p[v]"
    )
    cmd = [ffmpeg(), "-y", "-loop", "1", "-t", f"{dur:.2f}", "-i", str(png)]
    if audio:
        cmd += ["-i", str(audio), "-shortest"]
    cmd += ["-filter_complex", vf, "-map", "[v]"]
    if audio:
        cmd += ["-map", "1:a", "-c:a", "aac", "-b:a", "128k",
                "-af", f"adelay=300|300,apad,afade=t=out:st={dur - FADE:.2f}:d={FADE}"]
    cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "22", "-r", "25",
            "-t", f"{dur:.2f}", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"clip {idx} 失败: {r.stderr[-400:]}")
    return out


def concat_clips(clips: list[Path], out: Path) -> Path:
    """concat demuxer 拼接(同参编码,直接 copy)。"""
    lst = out.parent / "concat.txt"
    lst.write_text("\n".join(f"file '{c}'" for c in clips), encoding="utf-8")
    r = subprocess.run([ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"concat 失败: {r.stderr[-400:]}")
    return out


def gen_video(project_dir: Path, topic: str, voice: str = DEFAULT_VOICE,
              with_tts: bool = True) -> Path:
    """主入口:工程 output/ 的卡片 PNG → output/<topic>_video.mp4。"""
    out_dir = project_dir / "output"
    pngs = sorted(out_dir.glob(f"{topic}_0*.png"))
    if not pngs:
        raise SystemExit(f"无卡片 PNG: {out_dir}/{topic}_0*.png(先 build)")
    spec = json.loads((out_dir / "spec.materialized.json").read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory(prefix="cardvid_") as td:
        tdp = Path(td)
        audios: list[Path | None] = []
        if with_tts:
            texts = narration_texts(spec)
            texts += [""] * (len(pngs) - len(texts))
            for i, t in enumerate(texts):
                if t:
                    p = tdp / f"nar_{i:02d}.mp3"
                    import edge_tts
                    asyncio.run(edge_tts.Communicate(t, voice).save(str(p)))
                    audios.append(p)
                else:
                    audios.append(None)
            logger.info(f"旁白合成 {sum(1 for a in audios if a)}/{len(pngs)} 段")
        clips = [build_clip(png, audios[i] if i < len(audios) else None, i,
                            tdp / f"clip_{i:02d}.mp4")
                 for i, png in enumerate(pngs)]
        final = concat_clips(clips, out_dir / f"{topic}_video.mp4")
    size_mb = final.stat().st_size / 1048576
    logger.info(f"视频完成: {final} ({size_mb:.1f}MB, {audio_duration(final):.0f}s)")
    return final
