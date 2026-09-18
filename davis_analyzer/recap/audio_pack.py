# davis_analyzer/recap/audio_pack.py
"""recap 原料包:双TTS分段配音 + SRT 字幕 + 音画守恒校验 + 拼接说明。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger

from davis_analyzer.cardgen.video import audio_duration, ffmpeg  # noqa: F401 (ffmpeg 复用)
from davis_analyzer.recap.constants import EPISODES_DIR, INBOX_DIR, VOICE_COLOR, VOICE_PB
from davis_analyzer.recap.types import Episode

_GAP = 0.2           # 句间隙
VOICE_MAP = {"pb": VOICE_PB, "color": VOICE_COLOR}


def synth_lines(ep: Episode, outdir: Path) -> list[dict]:
    """逐句 TTS(edge-tts),返回 timings(含 dur/file)。"""
    import edge_tts

    timings: list[dict] = []
    outdir.mkdir(parents=True, exist_ok=True)
    for seg in ep.segments:
        for i, line in enumerate(seg.lines):
            rel = f"audio/{seg.seg_id}_{i:02d}_{line.speaker}.mp3"
            path = outdir / rel
            path.parent.mkdir(parents=True, exist_ok=True)   # edge_tts.save 只 open("wb") 不建目录
            asyncio.run(edge_tts.Communicate(line.text, VOICE_MAP[line.speaker]).save(str(path)))
            timings.append({"seg_id": seg.seg_id, "speaker": line.speaker,
                            "text": line.text, "dur": audio_duration(path), "file": rel})
    return timings


def _fmt_ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


def build_srt(timings: list[dict]) -> str:
    blocks, t = [], 0.0
    for i, item in enumerate(timings, 1):
        blocks.append(f"{i}\n{_fmt_ts(t)} --> {_fmt_ts(t + item['dur'])}\n{item['text']}\n")
        t += item["dur"] + _GAP
    return "\n".join(blocks)


def seg_durations(timings: list[dict]) -> dict[str, float]:
    """段合计(句间含 0.2s 间隙,段尾不计):s1 两句 2.0+2.0 → 4.2s。"""
    total: dict[str, float] = {}
    counts: dict[str, int] = {}
    for item in timings:
        total[item["seg_id"]] = total.get(item["seg_id"], 0.0) + item["dur"]
        counts[item["seg_id"]] = counts.get(item["seg_id"], 0) + 1
    return {k: v + _GAP * (n - 1) for k, v in total.items() for n in [counts[k]]}


def fit_report(ep: Episode, timings: list[dict], clip_dur: dict[str, float]) -> list[str]:
    rep: list[str] = []
    for k, need in seg_durations(timings).items():
        have = clip_dur.get(k)
        if have is None:
            rep.append(f"{k}: 未找到素材文件")
        elif have + 0.3 < need:
            rep.append(f"{k}: 解说 {need:.1f}s > 素材 {have:.1f}s —— 建议回放 0.8x 慢放重录,或砍一句解说")
    return rep


def _build_notes(ep: Episode, timings: list[dict], clip_map: dict[str, Path]) -> str:
    lines = [f"# {ep.trade_date} 拼接说明(剪映)", "",
             "1. 新建 1080x1920 竖屏项目", "2. 按下表顺序拖入素材与音轨,字幕导入 字幕.srt(套大字样式)",
             "3. 每段素材时长若长于解说,可加变速/卡点;数据卡 PNG 垫在片头与每段开头 2s", "",
             "| 段 | 素材 | 音轨文件 | 字幕行 | 段解说时长 |", "|---|---|---|---|---|"]
    row_i = 1
    for seg in ep.segments:
        seg_lines = [t for t in timings if t["seg_id"] == seg.seg_id]
        n = len(seg_lines)
        clip = clip_map.get(seg.seg_id)
        clip_s = clip.name if clip else ("(数据卡/比分牌静态段)" if seg.kind != "stock" else "缺失!")
        audios = " + ".join(t["file"] for t in seg_lines) or "-"
        dur = seg_durations(timings).get(seg.seg_id, 0.0)
        lines.append(f"| {seg.seg_id}({seg.kind}) | {clip_s} | {audios} | {row_i}-{row_i + n - 1} | {dur:.1f}s |")
        row_i += n
    return "\n".join(lines)


def make_pack(day_dash: str) -> Path:
    ep_dir = EPISODES_DIR / day_dash
    ep = Episode.from_dict(json.loads((ep_dir / "episode.json").read_text(encoding="utf-8")))
    out = ep_dir / "原料包"
    timings = synth_lines(ep, out)
    (out / "durations.json").write_text(
        json.dumps({"lines": timings, "segments": seg_durations(timings)},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "字幕.srt").write_text(build_srt(timings), encoding="utf-8")

    from davis_analyzer.recap.recorder_sheet import match_clips
    clip_map, missing = match_clips(day_dash, ep)
    clip_dur: dict[str, float] = {}
    for seg_id, p in clip_map.items():
        try:
            clip_dur[seg_id] = audio_duration(p)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"{p} 时长解析失败: {e!r}")
    rep = fit_report(ep, timings, clip_dur)
    (out / "音频守恒报告.txt").write_text("\n".join(rep) or "全部通过", encoding="utf-8")
    (out / "拼接说明.md").write_text(_build_notes(ep, timings, clip_map), encoding="utf-8")
    logger.info(f"recap 原料包完成: {out}(守恒 {'通过' if not rep else rep})")
    return out
