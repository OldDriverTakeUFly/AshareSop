# davis_analyzer/recap/post_compose.py
"""recap 二期:素材+原料包 → 1080x1920 成片(变速对齐/叠层/字幕烧录/concat)。

时间轴口径(2026-09-18 首跑设计):视频合成轴 = 段内 mp3 无缝拼接 + 每段尾 _PAD_TAIL 留白;
与原料包 字幕.srt 的「剪辑轴」(句间 0.2s 间隙)不同——烧录字幕用 build_burn_ass 重建,
否则全片漂移约 0.2s×句数+0.6s×段数。
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from davis_analyzer.cardgen.video import audio_duration, concat_clips, ffmpeg
from davis_analyzer.recap.constants import EPISODES_DIR
from davis_analyzer.recap.types import Episode

W, H = 1080, 1920
_PAD_TAIL = 0.6                 # 每段旁白后的留白(与 cardgen.video 同口径)
_MAX_SPEED = 4.0                # 变速封顶(再快就看不清盘口了)


def speed_factor(clip: float, need: float) -> float:
    """素材加速到解说时长;素材足够长才加速,不够长保持 1x(守恒报告已提示重录)。"""
    if clip <= 0 or need <= 0 or clip <= need + 0.3:
        return 1.0
    return min(clip / need, _MAX_SPEED)


def overlay_y(video_h: int, card_h: int, margin: int) -> int:
    return video_h - card_h - margin


def _run(cmd: list[str], tag: str) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{tag} 失败: {r.stderr[-400:]}")


def _concat_mp3(paths: list[Path], out: Path) -> Path:
    _run([ffmpeg(), "-y", "-i", "concat:" + "|".join(str(p) for p in paths),
          "-c", "copy", str(out)], "concat_mp3")
    return out


def _fmt_ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    return (f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:"
            f"{ms % 60000 // 1000:02d},{ms % 1000:03d}")


_ASS_HEADER = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
    "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
    "MarginV, Encoding\n"
    "Style: Default,Noto Sans CJK SC,17,&H00FFFFFF,&H00FFFFFF,&H90000000,&H00000000,"
    "0,0,0,0,100,100,0,0,1,1.4,0,2,40,40,64,1\n\n"
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
    "Effect, Text\n"
)


def build_burn_ass(timings: list[dict]) -> tuple[str, float]:
    """视频时间轴 ASS 字幕(样式写进文件本体):段内无缝,段尾 _PAD_TAIL。
    返回 (ass文本, 全片总时长)。ASS 时间 H:MM:SS.CC(厘秒)。"""
    seg_order: list[str] = []
    by_seg: dict[str, list[dict]] = {}
    for t in timings:
        if t["seg_id"] not in by_seg:
            seg_order.append(t["seg_id"])
            by_seg[t["seg_id"]] = []
        by_seg[t["seg_id"]].append(t)

    def ts(sec: float) -> str:
        cs = int(round(sec * 100))
        return f"{cs // 360000}:{cs % 360000 // 60000:02d}:{cs % 60000 // 1000:02d}.{cs % 100:02d}"

    events: list[str] = []
    seg_start = 0.0
    for seg_id in seg_order:
        t0 = seg_start
        for line in by_seg[seg_id]:
            text = line["text"].replace("\n", "\\N")   # ASS 换行转义
            events.append(f"Dialogue: 0,{ts(t0)},{ts(t0 + line['dur'])},Default,,0,0,0,,{text}")
            t0 += line["dur"]
        seg_start = t0 + _PAD_TAIL
    return _ASS_HEADER + "\n".join(events) + "\n", seg_start


def seg_audio_files(pack: Path, seg_id: str) -> list[Path]:
    return sorted((pack / "audio").glob(f"{seg_id}_*.mp3"))


def probe_resolution(path: Path) -> tuple[int, int]:
    """ffmpeg -i stderr 解析视频分辨率(imageio 静态包无 ffprobe,与 audio_duration 同源)。"""
    r = subprocess.run([ffmpeg(), "-i", str(path)], capture_output=True, text=True)
    m = re.search(r",\s*(\d{2,5})x(\d{2,5})[,\s]", r.stderr)
    if not m:
        raise RuntimeError(f"无法解析分辨率: {path}")
    return int(m.group(1)), int(m.group(2))


def _stock_clip(clip: Path, card_png: Path, seg_audio: Path, out: Path,
                need: float) -> Path:
    """素材段:变速对齐 + 等比适配(不裁内容:高缩放到 1920,不足 1080 宽处模糊底填充,
    手机录屏常为 9:20 等长条比例,crop 填满会吃掉上下盘口)+ 底部数据卡叠层。
    注意:overlay 坐标必须用数字——表达式坐标 (ow-iw)/2 在本 ffmpeg 7.0.2 静态包下
    静默产出 0 帧(2026-09-18 首跑实锤)。"""
    dur = audio_duration(clip)
    sp = speed_factor(dur, need + _PAD_TAIL)
    cw, ch = probe_resolution(clip)
    fg_w = max(2, round(cw * H / ch / 2) * 2)          # 前景等比宽(取偶)
    fg_x = max(0, (W - fg_w) // 2)                     # 居中横坐标(数字)
    vf = (
        f"[0:v]setpts=PTS/{sp:.4f},scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},gblur=sigma=22,eq=brightness=-0.06[bg];"
        f"[0:v]setpts=PTS/{sp:.4f},scale=-2:{H}[fg];"
        f"[bg][fg]overlay={fg_x}:0[m];"
        f"[2:v]scale={W}:-2[card];"
        f"[m][card]overlay=0:{overlay_y(H, 420, 120)},format=yuv420p[v]"
    )
    _run([ffmpeg(), "-y", "-i", str(clip), "-i", str(seg_audio),
          "-i", str(card_png), "-filter_complex", vf,
          "-map", "[v]", "-map", "1:a",
          "-c:v", "libx264", "-preset", "fast", "-crf", "23",
          "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
          "-t", f"{need + _PAD_TAIL:.2f}", str(out)], "stock_clip")
    return out


def _board_clip(board_png: Path, seg_audio: Path, out: Path) -> Path:
    """比分牌静态段:整卡完整呈现+淡入淡出(不做推拉——首跑实锤 zoompan 会裁掉
    footer 免责文字,信息完整性优先于动感)。"""
    need = audio_duration(seg_audio) + _PAD_TAIL
    vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"fps=30,format=yuv420p,"
          f"fade=t=in:st=0:d=0.4,fade=t=out:st={max(0.0, need - 0.4):.2f}:d=0.4[v]")
    _run([ffmpeg(), "-y", "-loop", "1", "-t", f"{need:.2f}", "-i", str(board_png),
          "-i", str(seg_audio), "-filter_complex", vf, "-map", "[v]", "-map", "1:a",
          "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-r", "30",
          "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-shortest", str(out)],
         "board_clip")
    return out


def _has_subtitles_filter() -> bool:
    r = subprocess.run([ffmpeg(), "-hide_banner", "-filters"], capture_output=True, text=True)
    return " subtitles " in r.stdout or r.stdout.find("\n ... subtitles ") >= 0


def compose(day_dash: str, burn_subs: bool = True) -> Path:
    """素材+原料包 → final/{day}_recap.mp4(1080x1920,烧字幕,统一 30fps/aac44100)。"""
    from davis_analyzer.recap.recorder_sheet import match_clips
    ep_dir = EPISODES_DIR / day_dash
    ep = Episode.from_dict(json.loads((ep_dir / "episode.json").read_text(encoding="utf-8")))
    pack = ep_dir / "原料包"
    durs = json.loads((pack / "durations.json").read_text(encoding="utf-8"))
    clip_map, missing = match_clips(day_dash, ep)
    if missing:
        raise SystemExit(f"缺素材段落: {missing}(先补录丢 inbox)")
    final_dir = ep_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="recap_post_") as td:
        tdp = Path(td)
        parts: list[Path] = []
        for seg in ep.segments:
            audios = seg_audio_files(pack, seg.seg_id)
            if not audios:
                raise SystemExit(f"缺音轨: {seg.seg_id}(先跑 audio)")
            merged = (_concat_mp3(audios, tdp / f"aud_{seg.seg_id}.mp3") if len(audios) > 1
                      else audios[0])
            if seg.kind == "stock":
                card = next((pack / "cards").glob(
                    f"stock_*_{seg.ts_code.split('.')[0]}.png"), None)
                if card is None:
                    raise SystemExit(f"缺数据卡: {seg.ts_code}")
                parts.append(_stock_clip(clip_map[seg.seg_id], card, merged,
                                         tdp / f"part_{seg.seg_id}.mp4",
                                         durs["segments"].get(seg.seg_id,
                                                              audio_duration(merged))))
            else:
                parts.append(_board_clip(pack / "cards" / "scoreboard.png", merged,
                                         tdp / f"part_{seg.seg_id}.mp4"))
        rough = concat_clips(parts, tdp / "rough.mp4")

        final = final_dir / f"{day_dash}_recap.mp4"
        if burn_subs and durs.get("lines") and _has_subtitles_filter():
            ass_text, _ = build_burn_ass(durs["lines"])
            ass_path = tdp / "burn.ass"          # ASCII 路径,规避 libass 路径转义坑
            ass_path.write_text(ass_text, encoding="utf-8")
            _run([ffmpeg(), "-y", "-i", str(rough),
                  "-vf", f"subtitles={ass_path}",
                  "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-r", "30",
                  "-c:a", "copy", str(final)], "burn_subs")
        else:
            final.write_bytes(rough.read_bytes())
            if burn_subs:
                logger.warning("成片未烧字幕(无 libass 或无字幕轴),沿用无字幕版")
    size_mb = final.stat().st_size / 1048576
    logger.info(f"recap 成片: {final} ({size_mb:.1f}MB, {audio_duration(final):.0f}s)")
    return final
