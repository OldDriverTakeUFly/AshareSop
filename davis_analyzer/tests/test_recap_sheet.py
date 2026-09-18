# davis_analyzer/tests/test_recap_sheet.py
"""recap 录制单:文件名协议/单据生成/素材对位/推送幂等。"""
from __future__ import annotations

import json

from davis_analyzer.recap import recorder_sheet as rs
from davis_analyzer.recap.types import Candidate, Episode


def _ep():
    return Episode.from_dict({
        "trade_date": "2026-09-18", "title": "测试之夜",
        "facts": [],
        "segments": [
            {"seg_id": "open", "kind": "scoreboard", "ts_code": None, "lines": []},
            {"seg_id": "s1", "kind": "stock", "ts_code": "605577.SH", "lines": []},
            {"seg_id": "s2", "kind": "stock", "ts_code": "001216.SZ", "lines": []},
            {"seg_id": "close", "kind": "outlook", "ts_code": None, "lines": []},
        ]})


def _cands():
    return [Candidate(ts_code="605577.SH", name="龙版传媒", sector="出版", drama_score=98,
                      replay_start="09:27:00", replay_end="14:51:00", notes=["5连板", "3度炸板回封"]),
            Candidate(ts_code="001216.SZ", name="华瓷股份", sector="陶瓷", drama_score=70,
                      replay_start="09:30:00", replay_end="15:00:00", notes=["首板"])]


def test_parse_clip_filename():
    assert rs.parse_clip_filename("20260918_605577.SH_01.mp4") == ("20260918", "605577.SH", 1)
    assert rs.parse_clip_filename("20260918_605577_01.mp4") is None
    assert rs.parse_clip_filename("xx.mp4") is None


def test_sheet_markdown_contains_ops_info():
    md = rs.build_sheet_markdown(_ep(), _cands())
    assert "605577.SH" in md and "龙版传媒" in md
    assert "09:27-14:51" in md           # 回放窗
    assert "20260918_605577.SH_01.mp4" in md   # 文件名协议指令
    assert "保存为" in md


def test_match_clips(tmp_path, monkeypatch):
    # 修复(brief 笔误):协议是 inbox/{day}/ 子目录,素材须落在 tmp_path/2026-09-18/ 下
    monkeypatch.setattr(rs, "INBOX_DIR", tmp_path)
    day_dir = tmp_path / "2026-09-18"
    day_dir.mkdir()
    (day_dir / "20260918_605577.SH_01.mp4").write_bytes(b"x")
    (day_dir / "20260918_001216.SZ_02.mp4").write_bytes(b"x")
    matched, missing = rs.match_clips("2026-09-18", _ep())
    assert matched["s1"].name == "20260918_605577.SH_01.mp4"
    assert matched["s2"].name == "20260918_001216.SZ_02.mp4"
    assert missing == []


def test_match_clips_reports_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "INBOX_DIR", tmp_path)
    matched, missing = rs.match_clips("2026-09-18", _ep())
    assert "s1" in missing and "s2" in missing
    assert matched == {}


def test_match_clips_stray_file_does_not_overwrite(tmp_path, monkeypatch):
    # 评审修复:同 ts_code 杂散 _02 不得经回退覆盖已精确对位的 s1
    monkeypatch.setattr(rs, "INBOX_DIR", tmp_path)
    day_dir = tmp_path / "2026-09-18"
    day_dir.mkdir()
    (day_dir / "20260918_605577.SH_01.mp4").write_bytes(b"x")
    (day_dir / "20260918_605577.SH_02.mp4").write_bytes(b"x")   # 杂散文件
    ep = Episode.from_dict({
        "trade_date": "2026-09-18", "title": "杂散测试", "facts": [],
        "segments": [
            {"seg_id": "open", "kind": "scoreboard", "ts_code": None, "lines": []},
            {"seg_id": "s1", "kind": "stock", "ts_code": "605577.SH", "lines": []},
            {"seg_id": "close", "kind": "outlook", "ts_code": None, "lines": []},
        ]})
    matched, missing = rs.match_clips("2026-09-18", ep)
    assert matched["s1"].name == "20260918_605577.SH_01.mp4"
    assert missing == []


def test_push_sheet_failure_no_lock_but_retryable(tmp_path, monkeypatch):
    # 评审修复:推送异常不落锁(下次可重试);无 chat id 跳过则视为完成落锁
    monkeypatch.setattr(rs, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("FEISHU_XHS_CHAT_ID", "oc_fake")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_fake")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec_fake")
    import stockhot.notification.feishu_bot as fb

    class _Boom:
        def __init__(self, *args, **kwargs):
            pass

        async def send_text(self, text: str) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(fb, "EnterpriseFeishuNotifier", _Boom)
    lock = tmp_path / "logs" / ".recap_sheet" / "2026-09-18.ok"
    assert rs.push_sheet("2026-09-18", "# 单") is True
    assert not lock.exists()            # 推送失败:不落锁,可重试
    monkeypatch.delenv("FEISHU_XHS_CHAT_ID", raising=False)
    assert rs.push_sheet("2026-09-18", "# 单") is True
    assert lock.exists()                # 无 chat 跳过:视为完成,落锁


def test_push_sheet_dry_run_and_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "REPO_ROOT", tmp_path)
    assert rs.push_sheet("2026-09-18", "# 单", dry_run=True) is True
    lock = tmp_path / "logs" / ".recap_sheet" / "2026-09-18.ok"
    assert not lock.exists()          # dry_run 不落锁
    # 无 FEISHU 环境变量:跳过推送但成功返回(与 push_one 同口径)
    monkeypatch.delenv("FEISHU_XHS_CHAT_ID", raising=False)
    assert rs.push_sheet("2026-09-18", "# 单") is True
    assert lock.exists()              # 幂等锁已落
    assert rs.push_sheet("2026-09-18", "# 单") is True   # 二次调用走锁跳过
