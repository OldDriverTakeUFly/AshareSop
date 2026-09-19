# davis_analyzer/tests/test_recap_audio.py
"""recap 原料包:SRT 时间轴/守恒校验/对位报告(TTS 全 mock)。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from davis_analyzer.systems.recap import audio_pack as ap
from davis_analyzer.systems.recap.types import Episode


def _ep() -> Episode:
    return Episode.from_dict({
        "trade_date": "2026-09-18", "title": "t", "facts": [],
        "segments": [
            {"seg_id": "open", "kind": "scoreboard", "ts_code": None, "lines": [
                {"speaker": "pb", "text": "第一句开场白。"}]},
            {"seg_id": "s1", "kind": "stock", "ts_code": "605577.SH", "lines": [
                {"speaker": "pb", "text": "五连板!"},
                {"speaker": "color", "text": "出版板块集体起立。"}]},
            {"seg_id": "close", "kind": "outlook", "ts_code": None, "lines": [
                {"speaker": "color", "text": "本内容仅为盘面复盘记录,不构成投资建议。"}]},
        ]})


def test_build_srt_cumulative_timeline():
    timings = [
        {"seg_id": "open", "speaker": "pb", "text": "第一句开场白。", "dur": 2.0, "file": "a.mp3"},
        {"seg_id": "open", "speaker": "pb", "text": "(第二句)", "dur": 3.0, "file": "b.mp3"},
    ]
    srt = ap.build_srt(timings)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,000")
    assert "00:00:02,200 --> 00:00:05,200" in srt   # 0.2s 句间隙


def test_fit_report_flags_short_clip():
    ep = _ep()
    timings = [{"seg_id": "s1", "speaker": "pb", "text": "x", "dur": 9.0, "file": "f"},
               {"seg_id": "s1", "speaker": "color", "text": "y", "dur": 9.5, "file": "f"}]
    clip_dur = {"s1": 12.0}   # 解说 18.5s > 素材 12s
    rep = ap.fit_report(ep, timings, clip_dur)
    assert any("s1" in r and ("慢放" in r or "砍" in r) for r in rep)
    assert ap.fit_report(ep, timings, {"s1": 30.0}) == []


def test_fit_report_ignores_non_stock_segments():
    # match_clips 只对位 stock 段:open/close 无素材属系统语义(数据卡/静态段),不得告警
    ep = _ep()
    timings = [
        {"seg_id": "open", "speaker": "pb", "text": "a", "dur": 2.0, "file": "f"},
        {"seg_id": "s1", "speaker": "pb", "text": "x", "dur": 9.0, "file": "f"},
        {"seg_id": "s1", "speaker": "color", "text": "y", "dur": 9.5, "file": "f"},
        {"seg_id": "close", "speaker": "color", "text": "b", "dur": 2.0, "file": "f"},
    ]
    assert ap.fit_report(ep, timings, {"s1": 30.0}) == []


def test_make_pack_end_to_end(tmp_path, monkeypatch):
    # episodes/{day}/episode.json + inbox 素材 + mock TTS/时长
    day = "2026-09-18"
    ep_dir = tmp_path / "episodes" / day
    ep_dir.mkdir(parents=True)
    (ep_dir / "episode.json").write_text(json.dumps(_ep().to_dict(), ensure_ascii=False), "utf-8")
    inbox = tmp_path / "inbox" / day
    inbox.mkdir(parents=True)
    (inbox / "20260918_605577.SH_01.mp4").write_bytes(b"fake")
    from davis_analyzer.systems.recap import recorder_sheet as rs
    monkeypatch.setattr(rs, "INBOX_DIR", tmp_path / "inbox")  # match_clips 在 recorder_sheet 内读自己的 INBOX_DIR

    def fake_communicate(text, voice):   # 真实 edge_tts:同步构造 + async save,mock 同口径
        class _C:
            async def save(self, path):
                Path(path).write_bytes(b"mp3")   # 与真实 save 同口径:不建父目录
        return _C()

    import edge_tts
    monkeypatch.setattr(edge_tts, "Communicate", fake_communicate)
    monkeypatch.setattr(ap, "audio_duration", lambda p: 2.0)   # 每句固定 2s
    monkeypatch.setattr(ap, "EPISODES_DIR", tmp_path / "episodes")
    monkeypatch.setattr(ap, "INBOX_DIR", tmp_path / "inbox")

    out = ap.make_pack(day)
    assert (out / "字幕.srt").exists()
    assert (out / "durations.json").exists()
    assert (out / "拼接说明.md").exists()
    assert (out / "音频守恒报告.txt").exists()
    durs = json.loads((out / "durations.json").read_text("utf-8"))
    assert durs["segments"]["s1"] == pytest.approx(4.2)   # 两句 2s + 2×0.2 间隙? → 4.4-0.2


def test_make_pack_purges_stale_audio(tmp_path, monkeypatch):
    """重跑幂等:剧本改短后,旧段残留 mp3 必须被清掉,否则合成段通配会把旧解说拼回去
    (2026-09-19 实锤:v3 的 open_01_color 残留导致 v4 开场仍是 16s 全场播报)。"""
    day = "2026-09-18"
    ep_dir = tmp_path / "episodes" / day
    ep_dir.mkdir(parents=True)
    (ep_dir / "episode.json").write_text(json.dumps(_ep().to_dict(), ensure_ascii=False), "utf-8")
    pack = ep_dir / "原料包"
    (pack / "audio").mkdir(parents=True)
    (pack / "audio" / "open_01_color.mp3").write_bytes(b"stale-v3")   # 剧本已无此句
    inbox = tmp_path / "inbox" / day
    inbox.mkdir(parents=True)
    (inbox / "20260918_605577.SH_01.mp4").write_bytes(b"x")
    from davis_analyzer.systems.recap import recorder_sheet as rs
    monkeypatch.setattr(rs, "INBOX_DIR", tmp_path / "inbox")

    def fake_communicate(text, voice):
        class _C:
            async def save(self, path):
                Path(path).write_bytes(b"mp3")
        return _C()

    import edge_tts
    monkeypatch.setattr(edge_tts, "Communicate", fake_communicate)
    monkeypatch.setattr(ap, "audio_duration", lambda p: 2.0)
    monkeypatch.setattr(ap, "EPISODES_DIR", tmp_path / "episodes")
    ap.make_pack(day)
    assert not (pack / "audio" / "open_01_color.mp3").exists()        # 旧音轨已清
    names = {p.name for p in (pack / "audio").glob("*.mp3")}
    expect = {f"{s.seg_id}_{i:02d}_{l.speaker}.mp3"
              for s in _ep().segments for i, l in enumerate(s.lines)}
    assert names == expect                                            # 与台词一一对应
