# davis_analyzer/tests/test_recap_post.py
"""recap 二期合成:变速/叠层几何/烧录字幕时间轴(单元)+ compose 烟测(integration)。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from davis_analyzer.recap import post_compose as pc


def test_setpts_speed_formula():
    # 解说 12s / 素材 18s → 加速 1.5x;素材不足不减速;防零
    assert pc.speed_factor(clip=18.0, need=12.0) == pytest.approx(1.5)
    assert pc.speed_factor(clip=10.0, need=12.0) == pytest.approx(1.0)
    assert pc.speed_factor(clip=0.0, need=12.0) == 1.0
    assert pc.speed_factor(clip=100.0, need=10.0) == 4.0   # 封顶 4x


def test_overlay_geometry():
    # 1080p 合成层:卡片缩放后 420 高,底部留 120 → y = 1920-420-120
    assert pc.overlay_y(video_h=1920, card_h=420, margin=120) == 1380


def test_build_burn_srt_video_timeline():
    """烧录字幕用视频时间轴:段内 mp3 无缝(无 0.2s 句隙)+每段尾 _PAD_TAIL 留白。"""
    lines = [
        {"seg_id": "open", "speaker": "pb", "text": "开场", "dur": 2.0, "file": "a"},
        {"seg_id": "s1", "speaker": "pb", "text": "第一句", "dur": 3.0, "file": "b"},
        {"seg_id": "s1", "speaker": "color", "text": "第二句", "dur": 4.0, "file": "c"},
        {"seg_id": "close", "speaker": "pb", "text": "收尾", "dur": 2.0, "file": "d"},
    ]
    srt, total = pc.build_burn_srt(lines)
    # open 段视频 2.0+0.6=2.6;s1 段 3+4+0.6=7.6;close 2.0+0.6=2.6;全片 12.8
    assert total == pytest.approx(12.8)
    assert "00:00:00,000 --> 00:00:02,000" in srt          # 开场 0-2
    assert "00:00:02,600 --> 00:00:05,600" in srt          # s1 句1:2.6-5.6(无缝)
    assert "00:00:05,600 --> 00:00:09,600" in srt          # s1 句2:5.6-9.6
    assert "00:00:10,200 --> 00:00:12,200" in srt          # close:10.2-12.2
    assert srt.count("开场") == 1


def _ff_make(src_args: list[str], out: Path) -> None:
    r = subprocess.run([pc.ffmpeg(), "-y", *src_args, str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-300:]


@pytest.mark.integration
def test_compose_smoke(tmp_path, monkeypatch):
    """ffmpeg lavfi 生成真素材(黑帧视频/静音mp3/纯色png),compose 全链路出 mp4。"""
    day = "2026-09-18"
    ep_dir = tmp_path / "episodes" / day
    ep_dir.mkdir(parents=True)
    (ep_dir / "episode.json").write_text(json.dumps({
        "trade_date": day, "title": "t", "facts": [],
        "segments": [
            {"seg_id": "open", "kind": "scoreboard", "ts_code": None,
             "lines": [{"speaker": "pb", "text": "开场。"}]},
            {"seg_id": "s1", "kind": "stock", "ts_code": "601091.SH",
             "lines": [{"speaker": "color", "text": "深V。"}]},
            {"seg_id": "close", "kind": "outlook", "ts_code": None,
             "lines": [{"speaker": "pb", "text": "本内容仅为盘面复盘记录,不构成投资建议。"}]},
        ]}, ensure_ascii=False), "utf-8")
    pack = ep_dir / "原料包"
    (pack / "audio").mkdir(parents=True)
    (pack / "cards").mkdir()
    for name in ("open_00_pb.mp3", "s1_00_color.mp3", "close_00_pb.mp3"):
        _ff_make(["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "1"],
                 pack / "audio" / name)
    _ff_make(["-f", "lavfi", "-i", "color=c=navy:s=2160x3840:d=1", "-frames:v", "1"],
             pack / "cards" / "scoreboard.png")
    _ff_make(["-f", "lavfi", "-i", "color=c=navy:s=2160x840:d=1", "-frames:v", "1"],
             pack / "cards" / "stock_01_601091.png")
    (pack / "durations.json").write_text(json.dumps(
        {"lines": [], "segments": {"open": 1.0, "s1": 1.0, "close": 1.0}}), "utf-8")
    inbox = tmp_path / "inbox" / day
    inbox.mkdir(parents=True)
    _ff_make(["-f", "lavfi", "-i", "color=c=gray:s=1080x1920:d=1",
              "-c:v", "libx264", "-preset", "ultrafast", "-t", "1"],
             inbox / "20260918_601091.SH_01.mp4")
    from davis_analyzer.recap import recorder_sheet as rs
    monkeypatch.setattr(rs, "INBOX_DIR", tmp_path / "inbox")  # match_clips 读自己的 INBOX_DIR
    monkeypatch.setattr(pc, "EPISODES_DIR", tmp_path / "episodes")
    monkeypatch.setattr(pc, "audio_duration", lambda p: 1.0)
    out = pc.compose(day)
    assert out.exists() and out.stat().st_size > 0


@pytest.mark.integration
def test_compose_missing_clip_exits(tmp_path, monkeypatch):
    """缺素材段:明确 SystemExit 报缺哪些段,不产出半成品。"""
    day = "2026-09-18"
    ep_dir = tmp_path / "episodes" / day
    ep_dir.mkdir(parents=True)
    (ep_dir / "episode.json").write_text(json.dumps({
        "trade_date": day, "title": "t", "facts": [],
        "segments": [
            {"seg_id": "s1", "kind": "stock", "ts_code": "601091.SH",
             "lines": [{"speaker": "pb", "text": "x"}]},
            {"seg_id": "close", "kind": "outlook", "ts_code": None,
             "lines": [{"speaker": "pb", "text": "本内容仅为盘面复盘记录,不构成投资建议。"}]},
        ]}, ensure_ascii=False), "utf-8")
    (ep_dir / "原料包").mkdir(parents=True)
    (ep_dir / "原料包" / "durations.json").write_text(
        json.dumps({"lines": [], "segments": {"s1": 1.0, "close": 1.0}}), "utf-8")
    from davis_analyzer.recap import recorder_sheet as rs
    monkeypatch.setattr(rs, "INBOX_DIR", tmp_path / "empty_inbox")
    monkeypatch.setattr(pc, "EPISODES_DIR", tmp_path / "episodes")
    with pytest.raises(SystemExit) as ei:
        pc.compose(day)
    assert "s1" in str(ei.value)
