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
from davis_analyzer.recap.constants import EPISODES_DIR, RECAP_ROOT
from davis_analyzer.recap.types import Episode

W, H = 1080, 1920
_PAD_TAIL = 0.6                 # 每段旁白后的留白(与 cardgen.video 同口径)
_MAX_SPEED = 4.0                # 变速封顶(再快就看不清盘口了)
_BGM_DIR = RECAP_ROOT / "assets" / "bgm"    # 用户自备 mp3(建议平台曲库导出,版权自担)
_SFX_DIR = RECAP_ROOT / "assets" / "sfx"    # 合成音效缓存(零外部音频,规避版权)
_BGM_VOL = 0.22                 # BGM 垫底音量(spec §八),人声经 sidechain 自动压它
_SFX_VOL = 0.6


def speed_factor(clip: float, need: float) -> float:
    """素材加速到解说时长;素材足够长才加速,不够长保持 1x(守恒报告已提示重录)。"""
    if clip <= 0 or need <= 0 or clip <= need + 0.3:
        return 1.0
    return min(clip / need, _MAX_SPEED)


def overlay_y(video_h: int, card_h: int, margin: int) -> int:
    return video_h - card_h - margin


# ── 娱乐版素材:BGM(用户mp3优先,否则合成节拍)与 SFX(全部合成,零外部音频) ──

def resolve_bgm() -> Path | None:
    """用户自备 BGM(recap/assets/bgm/*.mp3 第一个);无则 None(走合成节拍)。"""
    if _BGM_DIR.exists():
        files = sorted(_BGM_DIR.glob("*.mp3"))
        if files:
            return files[0]
    return None


def synth_bgm(out: Path, dur: float) -> Path:
    """合成 hype 节拍占位(kick 四踩 + hat 反拍 + 低音线,aevalsrc 单表达式)。
    版权零风险;想要更好的音乐:丢 mp3 进 recap/assets/bgm/ 即自动替换。"""
    # kick: 55Hz 衰减冲击 every 0.5s;hat: 高频短噪 on off-beat;bass: 110/98Hz 交替小节
    # 注意:aevalsrc 作为输入URL解析,表达式内逗号必须转义(否则被当滤镜分隔符)
    expr = (
        "0.55*sin(2*PI*55*t)*exp(-22*mod(t,0.5))"
        "+0.10*sin(2*PI*8000*t)*exp(-70*mod(t+0.25,0.5))"
        "+0.22*(lt(mod(t,4),2))*sin(2*PI*110*t)*(0.6+0.4*sin(PI*t/2))"
        "+0.22*(gte(mod(t,4),2))*sin(2*PI*98*t)*(0.6+0.4*sin(PI*t/2))"
    ).replace(",", "\\,")
    _run([ffmpeg(), "-y", "-f", "lavfi",
          "-i", f"aevalsrc={expr}:s=44100:d={dur:.2f}", "-c:a", "aac", "-b:a", "96k",
          str(out)], "synth_bgm")
    return out


def ensure_sfx(cache_dir: Path | None = None) -> dict[str, Path]:
    """合成 whoosh(白噪扫频)/impact(低频下坠)并缓存(幂等;cache_dir 供测试注入)。"""
    sdir = cache_dir or _SFX_DIR
    sdir.mkdir(parents=True, exist_ok=True)
    whoosh = sdir / "whoosh.wav"
    impact = sdir / "impact.wav"
    if not whoosh.exists():
        _run([ffmpeg(), "-y", "-f", "lavfi", "-i",
              "anoisesrc=color=pink:d=0.5:a=0.8",
              "-af", "lowpass=f=1000,highpass=f=150,"
                     "volume='if(lt(t,0.25),t*4,1-(t-0.25)*2.2)':eval=frame",
              str(whoosh)], "synth_whoosh")
    if not impact.exists():
        _run([ffmpeg(), "-y", "-f", "lavfi", "-i",
              "sine=frequency=110:duration=0.35",
              "-af", "volume='exp(-t*10)':eval=frame,lowpass=f=300", str(impact)],
             "synth_impact")
    return {"whoosh": whoosh, "impact": impact}


def sfx_offsets(timings: list[dict]) -> list[tuple[str, float]]:
    """SFX 时间点:impact@0;whoosh@每个段起点(首段除外——开场已有 impact)。"""
    seg_audio: dict[str, float] = {}
    order: list[str] = []
    for t in timings:
        if t["seg_id"] not in seg_audio:
            seg_audio[t["seg_id"]] = 0.0
            order.append(t["seg_id"])
        seg_audio[t["seg_id"]] += t["dur"]
    starts: list[float] = []
    acc = 0.0
    for sid in order:
        starts.append(acc)
        acc += seg_audio[sid] + _PAD_TAIL
    out = [("impact", 0.0)] if order else []
    out += [("whoosh", s) for s in starts[1:]]
    return out


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
        # ASS 时间 H:MM:SS.CC(厘秒):秒=cs//100,厘秒=cs%100(勿照抄毫秒分母)
        cs = int(round(sec * 100))
        return f"{cs // 360000}:{cs % 360000 // 6000:02d}:{cs % 6000 // 100:02d}.{cs % 100:02d}"

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


def _stock_clip(clip: Path, card_png: Path, banner_png: Path, replay_png: Path,
                seg_audio: Path, out: Path, need: float) -> Path:
    """素材段:变速对齐 + 等比适配(不裁内容:高缩放到 1920,不足 1080 宽处模糊底填充,
    手机录屏常为 9:20 等长条比例,crop 填满会吃掉上下盘口)+ 底部数据卡 + 顶部五佳横幅
    (淡入淡出)+ REPLAY 角标。注意:overlay 坐标必须用数字——表达式坐标在本
    ffmpeg 7.0.2 静态包下静默产出 0 帧(2026-09-18 首跑实锤)。"""
    dur = audio_duration(clip)
    sp = speed_factor(dur, need + _PAD_TAIL)
    seg_dur = need + _PAD_TAIL
    cw, ch = probe_resolution(clip)
    fg_w = max(2, round(cw * H / ch / 2) * 2)          # 前景等比宽(取偶)
    fg_x = max(0, (W - fg_w) // 2)                     # 居中横坐标(数字)
    vf = (
        f"[0:v]setpts=PTS/{sp:.4f},scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},gblur=sigma=22,eq=brightness=-0.06[bg];"
        f"[0:v]setpts=PTS/{sp:.4f},scale=-2:{H}[fg];"
        f"[bg][fg]overlay={fg_x}:0[m];"
        f"[2:v]scale={W}:-2[card];"
        f"[m][card]overlay=0:{overlay_y(H, 420, 230)}[m2];"
        f"[3:v]scale={W}:-2,fade=t=in:st=0:d=0.3,"
        f"fade=t=out:st={max(0.0, seg_dur - 0.5):.2f}:d=0.4[bn];"
        f"[m2][bn]overlay=0:40[m3];"
        f"[4:v]scale=320:-2[rb];"
        f"[m3][rb]overlay={W - 320 - 40}:1080,format=yuv420p[v]"
    )
    _run([ffmpeg(), "-y", "-i", str(clip), "-i", str(seg_audio),
          "-i", str(card_png), "-loop", "1", "-t", f"{seg_dur:.2f}", "-i", str(banner_png),
          "-loop", "1", "-t", f"{seg_dur:.2f}", "-i", str(replay_png),
          "-filter_complex", vf,
          "-map", "[v]", "-map", "1:a",
          "-c:v", "libx264", "-preset", "fast", "-crf", "23",
          "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
          "-t", f"{seg_dur:.2f}", str(out)], "stock_clip")
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
    """素材+原料包 → final/{day}_recap.mp4(1080x1920,烧字幕,统一 30fps/aac44100)。
    娱乐版:顶部五佳横幅+REPLAY 角标(素材段),终混 BGM(人声闪避)+SFX。"""
    from davis_analyzer.recap import card_renderer
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
    deco = card_renderer.render_banners(day_dash)     # 五佳横幅 + REPLAY 角标

    with tempfile.TemporaryDirectory(prefix="recap_post_") as td:
        tdp = Path(td)
        parts: list[Path] = []
        stock_idx = 0
        for seg in ep.segments:
            audios = seg_audio_files(pack, seg.seg_id)
            if not audios:
                raise SystemExit(f"缺音轨: {seg.seg_id}(先跑 audio)")
            merged = (_concat_mp3(audios, tdp / f"aud_{seg.seg_id}.mp3") if len(audios) > 1
                      else audios[0])
            if seg.kind == "stock":
                stock_idx += 1
                card = next((pack / "cards").glob(
                    f"stock_*_{seg.ts_code.split('.')[0]}.png"), None)
                if card is None:
                    raise SystemExit(f"缺数据卡: {seg.ts_code}")
                parts.append(_stock_clip(
                    clip_map[seg.seg_id], card,
                    deco["banners"].get(stock_idx, deco["banners"][1]),
                    deco["replay"], merged,
                    tdp / f"part_{seg.seg_id}.mp4",
                    durs["segments"].get(seg.seg_id, audio_duration(merged))))
            else:
                parts.append(_board_clip(pack / "cards" / "scoreboard.png", merged,
                                         tdp / f"part_{seg.seg_id}.mp4"))
        rough = concat_clips(parts, tdp / "rough.mp4")
        total = audio_duration(rough)

        # 终混:字幕烧录 + BGM(人声闪避) + SFX,单次编码
        final = final_dir / f"{day_dash}_recap.mp4"
        bgm_src = resolve_bgm()
        if bgm_src is None:
            bgm_src = synth_bgm(tdp / "bgm_synth.m4a", total + 1.0)
        sfx = ensure_sfx()
        plan = sfx_offsets(durs.get("lines", []))
        inputs = ["-i", str(rough), "-stream_loop", "-1", "-i", str(bgm_src)]
        chains: list[str] = []
        mix_labels = ["[com]"]
        chains.append(f"[0:a]asplit=2[com][key]")
        chains.append(f"[1:a]atrim=0:{total:.2f},volume={_BGM_VOL}[bgm0]")
        chains.append("[bgm0][key]sidechaincompress=threshold=0.02:ratio=6:"
                      "attack=25:release=350[duck]")
        mix_labels.append("[duck]")
        for j, (kind, t) in enumerate(plan):
            inputs += ["-i", str(sfx[kind])]
            ms = int(t * 1000)
            chains.append(f"[{j + 2}:a]adelay={ms}|{ms},volume={_SFX_VOL}[s{j}]")
            mix_labels.append(f"[s{j}]")
        chains.append("".join(mix_labels) +
                      f"amix=inputs={len(mix_labels)}:duration=first:normalize=0[a]")
        vchain = ""
        if burn_subs and durs.get("lines") and _has_subtitles_filter():
            ass_text, _ = build_burn_ass(durs["lines"])
            ass_path = tdp / "burn.ass"          # ASCII 路径,规避 libass 路径转义坑
            ass_path.write_text(ass_text, encoding="utf-8")
            vchain = f"[0:v]subtitles={ass_path}[v]"
        else:
            vchain = "[0:v]null[v]"
            if burn_subs:
                logger.warning("成片未烧字幕(无 libass 或无字幕轴),沿用无字幕版")
        _run([ffmpeg(), "-y", *inputs, "-filter_complex",
              ";".join([vchain] + chains),
              "-map", "[v]", "-map", "[a]",
              "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-r", "30",
              "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
              "-t", f"{total:.2f}", str(final)], "final_mix")
    size_mb = final.stat().st_size / 1048576
    logger.info(f"recap 成片(娱乐版): {final} ({size_mb:.1f}MB, {audio_duration(final):.0f}s, "
                f"BGM={'自备' if resolve_bgm() else '合成节拍'})")
    return final
