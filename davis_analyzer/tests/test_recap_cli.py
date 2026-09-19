# davis_analyzer/tests/test_recap_cli.py
"""recap CLI:run 串联/冰点降级/台账推进/文件落盘。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """注入:内存台账 conn + 假 bundle + 假 LLM + 假推送。"""
    from davis_analyzer.systems.recap import cli

    class _KeepOpenConn(sqlite3.Connection):
        """cli 各阶段用完即 close;同一内存库跨阶段复用需 close 变 no-op(brief 修正)。"""

        def close(self) -> None:
            pass

    conn = sqlite3.connect(":memory:", factory=_KeepOpenConn)
    from davis_analyzer.systems.recap import db
    db.ensure_tables(conn)
    monkeypatch.setattr(cli, "_conn", lambda: conn)
    monkeypatch.setattr(cli, "EPISODES_DIR", tmp_path / "episodes")
    bundle = {
        "pool": [{"ts_code": "605577.SH", "name": "龙版传媒", "sector": "出版",
                  "change_pct": 9.97, "consecutive_boards": 5, "broken_count": 3,
                  "first_seal_time": "09:47:00", "last_seal_time": "14:46:00",
                  "turnover_rate": 11.9}],
        "broken": [], "down": [],
        "boards": [{"board_count": 5, "stocks": [{"code": "605577.SH", "name": "龙版传媒"}]}],
        "lhb_codes": set(), "lhb_detail": [], "brokers": [],
        "index": [{"code": "000001.SH", "name": "上证指数", "close": 3875.6, "pct_chg": -0.411},
                  {"code": "399001.SZ", "name": "深证成指", "close": 12345.6, "pct_chg": 0.52},
                  {"code": "399006.SZ", "name": "创业板指", "close": 2710.2, "pct_chg": 0.85}],
        "amplitude_top": [], "breadth": {"up": 3200, "down": 1900},
        "names": {}, "limit_up_count": 1,
    }
    monkeypatch.setattr(cli.data, "fetch_bundle", lambda day: bundle)
    pushed: list[str] = []
    monkeypatch.setattr(cli.sheet, "push_sheet", lambda day, md, dry_run=False:
                        pushed.append(day) or True)

    class _OK:
        # 注:片头行尾追加一句(brief 原稿 162 字/38.6s < generate_episode 内部 40s 下限,
        # 会触发自纠错循环后 ScriptGenError;措辞取自 test_recap_scriptwriter._llm_json)
        content = json.dumps({"title": "五连板之夜", "segments": [
            {"seg_id": "open", "kind": "scoreboard", "ts_code": None, "lines": [
                {"speaker": "pb", "text": "今日战报:上证收在3876点,下跌0.41%,"
                                          "全场3200家上涨、1900家下跌,今晚的高光时刻一个比一个精彩。"}]},
            {"seg_id": "s1", "kind": "stock", "ts_code": "605577.SH", "lines": [
                {"speaker": "pb", "text": "看这段回放,5连板!第3次炸板又硬生生封回去,"
                                          "统治力拉满,这就是今天最硬的高光时刻。"},
                {"speaker": "color", "text": "出版板块今天集体起立,资金抱团意图明确,"
                                             "每一波炸板都被更坚决的买盘接住。"}]},
            {"seg_id": "close", "kind": "outlook", "ts_code": None, "lines": [
                {"speaker": "color", "text": "天梯高度明天继续量,断板与晋级的故事还会上演。"
                                             "本内容仅为盘面复盘记录,不构成投资建议。"}]},
        ]}, ensure_ascii=False)

    class _Provider:
        def complete(self, prompt, system="", max_tokens=800, temperature=0.3):
            return _OK()

    # 先取原函数再包装(直接在 lambda 里再调 cli.scriptwriter.generate_episode 会递归)
    _orig_gen = cli.scriptwriter.generate_episode
    monkeypatch.setattr(
        cli.scriptwriter, "generate_episode",
        lambda day, cands, b, provider=None: _orig_gen(day, cands, b, provider=_Provider()))
    return {"conn": conn, "tmp": tmp_path, "pushed": pushed, "bundle": bundle}


def test_run_full_flow(env):
    from davis_analyzer.systems.recap import cli, db
    args = cli.build_parser().parse_args(["run", "--date", "2026-09-18"])
    args.func(args)
    row = db.get_episode(env["conn"], "2026-09-18")
    assert row["status"] == "sheeted"
    assert env["pushed"] == ["2026-09-18"]
    ep_dir = env["tmp"] / "episodes" / "2026-09-18"
    assert (ep_dir / "episode.json").exists()
    assert (ep_dir / "candidates.json").exists()
    assert (ep_dir / "facts.json").exists()
    assert (ep_dir / "录制单.md").exists()


def test_run_ice_day_degrades(env, monkeypatch):
    """冰点日:无候选 → 内置极简剧本照常推单。"""
    from davis_analyzer.systems.recap import cli, db
    empty = dict(env["bundle"], pool=[], boards=[], amplitude_top=[], limit_up_count=0,
                 broken=[], down=[])
    monkeypatch.setattr(cli.data, "fetch_bundle", lambda day: empty)
    args = cli.build_parser().parse_args(["run", "--date", "2026-09-18"])
    args.func(args)
    row = db.get_episode(env["conn"], "2026-09-18")
    assert row["status"] == "sheeted"
    ep = json.loads((env["tmp"] / "episodes" / "2026-09-18" / "episode.json").read_text("utf-8"))
    assert ep["segments"][0]["kind"] == "scoreboard"
    assert "不构成投资建议" in ep["segments"][-1]["lines"][-1]["text"]
