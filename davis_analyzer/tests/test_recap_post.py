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
    # 1080p 合成层:卡片缩放后 420 高,底部留 230 避开字幕带(1780-1860) → y = 1920-420-230
    assert pc.overlay_y(video_h=1920, card_h=420, margin=230) == 1270


def test_build_burn_ass_video_timeline():
    """烧录字幕(ASS)用视频时间轴:段内 mp3 无缝(无 0.2s 句隙)+每段尾 _PAD_TAIL 留白;
    样式写进 ASS 文件本体(绕开 force_style 逗号解析坑,2026-09-18 首跑实锤超宽裁切)。"""
    lines = [
        {"seg_id": "open", "speaker": "pb", "text": "开场", "dur": 2.0, "file": "a"},
        {"seg_id": "s1", "speaker": "pb", "text": "第一句", "dur": 3.0, "file": "b"},
        {"seg_id": "s1", "speaker": "color", "text": "第二句", "dur": 4.0, "file": "c"},
        {"seg_id": "close", "speaker": "pb", "text": "收尾", "dur": 2.0, "file": "d"},
    ]
    ass, total = pc.build_burn_ass(lines)
    # open 段视频 2.0+0.6=2.6;s1 段 3+4+0.6=7.6;close 2.0+0.6=2.6;全片 12.8
    assert total == pytest.approx(12.8)
    assert "PlayResX: 1080" in ass and "Style: Default,Noto Sans CJK SC" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:02.00" in ass          # 开场 0-2
    assert "Dialogue: 0,0:00:02.60,0:00:05.60" in ass          # s1 句1:2.6-5.6(无缝)
    assert "Dialogue: 0,0:00:05.60,0:00:09.60" in ass          # s1 句2:5.6-9.6
    assert "Dialogue: 0,0:00:10.20,0:00:12.20" in ass          # close:10.2-12.2
    assert ass.count("开场") == 1


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


def test_sfx_offsets():
    """impact@0,whoosh@段起点(首段外);段间含 _PAD_TAIL。"""
    lines = [
        {"seg_id": "open", "speaker": "pb", "text": "a", "dur": 2.0, "file": "a"},
        {"seg_id": "s1", "speaker": "pb", "text": "b", "dur": 3.0, "file": "b"},
        {"seg_id": "s1", "speaker": "color", "text": "c", "dur": 4.0, "file": "c"},
        {"seg_id": "close", "speaker": "pb", "text": "d", "dur": 2.0, "file": "d"},
    ]
    # 段音频:open=2.0, s1=7.0, close=2.0;起点 [0, 2.6, 10.2]
    assert pc.sfx_offsets(lines) == [("impact", 0.0), ("whoosh", 2.6), ("whoosh", 10.2)]
    assert pc.sfx_offsets([]) == []


def test_resolve_bgm(tmp_path, monkeypatch):
    """用户自备优先;空目录走合成。"""
    monkeypatch.setattr(pc, "_BGM_DIR", tmp_path / "bgm")
    assert pc.resolve_bgm() is None
    (tmp_path / "bgm").mkdir()
    (tmp_path / "bgm" / "hype.mp3").write_bytes(b"x")
    assert pc.resolve_bgm() == tmp_path / "bgm" / "hype.mp3"


def test_build_burn_ass_with_intros():
    """段首冲击卡(intros)把该段字幕与总时长后移。"""
    lines = [
        {"seg_id": "open", "speaker": "pb", "text": "开场", "dur": 2.0, "file": "a"},
        {"seg_id": "s1", "speaker": "pb", "text": "正片", "dur": 3.0, "file": "b"},
    ]
    ass, total = pc.build_burn_ass(lines, intros={"s1": 1.2})
    # open 2.0+0.6=2.6;s1 冲击卡 1.2 + 正片 3.0 + 尾 0.6 → 总 7.4;正片字幕 3.8-6.8
    assert total == pytest.approx(7.4)
    assert "Dialogue: 0,0:00:03.80,0:00:06.80" in ass


def test_sfx_offsets_with_intros():
    lines = [
        {"seg_id": "open", "speaker": "pb", "text": "a", "dur": 2.0, "file": "a"},
        {"seg_id": "s1", "speaker": "pb", "text": "b", "dur": 3.0, "file": "b"},
    ]
    # open 起点仍 0;s1 起点 = 2.6(含其冲击卡 1.2 在起点处)
    assert pc.sfx_offsets(lines, intros={"s1": 1.2}) == [
        ("impact", 0.0), ("whoosh", 2.6)]


def test_stock_vf_cover_bands():
    """素材段遮幅几何(2026-09-18 用户拍板:挡录屏上下杂区)——单测锁定 drawbox 参数。"""
    vf = pc._stock_vf(sp=1.5, fg_x=108, seg_dur=40.0)
    assert f"drawbox=x=0:y=0:w=1080:h={pc._COVER_TOP_H}:color={pc._COVER_COLOR}:t=fill" in vf
    assert (f"drawbox=x=0:y={pc._COVER_BOTTOM_Y}:w=1080:h={1920 - pc._COVER_BOTTOM_Y}:"
            f"color={pc._COVER_COLOR}:t=fill") in vf
    # 遮幅在卡/横幅叠层之前(m 链上),overlay 数字坐标不变
    assert "overlay=0:40[m3]" in vf and "format=yuv420p[v]" in vf
