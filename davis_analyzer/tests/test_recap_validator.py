# davis_analyzer/tests/test_recap_validator.py
"""recap 三道闸:数字全命中 facts / 敏感词 / 完整性+免责。"""
from __future__ import annotations

from davis_analyzer.systems.recap.types import DialogueLine, Episode, EpisodeSegment


def _fact(fid, value, unit, display):
    return {"id": fid, "value": value, "unit": unit, "display": display,
            "as_of": "2026-09-18", "source": {"kind": "stockhot", "ref": f"x@{fid}"}}


def _ep(lines_open=None, lines_close=None, facts=None, segments=None):
    segs = segments if segments is not None else [
        EpisodeSegment("open", "scoreboard", None,
                       lines_open or [DialogueLine("pb", "今日战报,上证收3875点。")]),
        EpisodeSegment("s1", "stock", "605577.SH",
                       [DialogueLine("color", "5连板,3次炸板后回封,戏剧性拉满。")]),
        EpisodeSegment("close", "outlook", None,
                       lines_close or [DialogueLine("pb", "明日看点看天梯。本内容仅为盘面复盘记录,不构成投资建议。")]),
    ]
    return Episode("2026-09-18", "测试", segs,
                   facts if facts is not None else [
                       _fact("idx_sh_close", "3875", "点", "上证收3875点"),
                       _fact("605577.SH_boards", "5", "板", "5连板"),
                       _fact("605577.SH_broken", "3", "次", "3次炸板"),
                   ])


def test_clean_episode_passes():
    from davis_analyzer.systems.recap.validator import validate_episode
    assert validate_episode(_ep(), min_seconds=5.0) == []


def test_number_not_in_facts_fails():
    from davis_analyzer.systems.recap.validator import validate_episode
    ep = _ep(lines_open=[DialogueLine("pb", "上证大涨百分之2。")])
    fails = validate_episode(ep)
    assert any("数字" in f for f in fails)


def test_sensitive_word_fails():
    from davis_analyzer.systems.recap.validator import validate_episode
    ep = _ep(lines_open=[DialogueLine("pb", "这位置可以上车,上证收3875点。")])
    fails = validate_episode(ep)
    assert any("敏感" in f for f in fails)


def test_missing_disclaimer_fails():
    from davis_analyzer.systems.recap.validator import validate_episode
    ep = _ep(lines_close=[DialogueLine("pb", "明天见。")])
    fails = validate_episode(ep)
    assert any("不构成投资建议" in f for f in fails)


def test_bad_speaker_and_length_fails():
    from davis_analyzer.systems.recap.validator import validate_episode
    segs = [EpisodeSegment("open", "scoreboard", None, [DialogueLine("narrator", "开场")]),
            EpisodeSegment("close", "outlook", None,
                           [DialogueLine("pb", "本内容仅为盘面复盘记录,不构成投资建议。" * 60)])]
    fails = validate_episode(Episode("2026-09-18", "t", segs, []))
    assert any("speaker" in f for f in fails)
    assert any("时长" in f for f in fails)


def test_allowed_stock_codes_gate():
    """2026-09-18 首跑实锤:LLM 会擅自增段/写候选外的票——stock 段必须与候选一一对应。"""
    from davis_analyzer.systems.recap.types import Episode, EpisodeSegment
    from davis_analyzer.systems.recap.validator import validate_episode
    facts = [_fact("idx_sh_close", "3875", "点", "上证收3875点")]
    base = {
        "trade_date": "2026-09-18", "title": "t", "facts": facts,
        "segments": [
            {"seg_id": "open", "kind": "scoreboard", "ts_code": None,
             "lines": [{"speaker": "pb", "text": "上证收3875点。"}]},
            {"seg_id": "s1", "kind": "stock", "ts_code": "601091.SH",
             "lines": [{"speaker": "pb", "text": "史诗深V。"}]},
            {"seg_id": "close", "kind": "outlook", "ts_code": None,
             "lines": [{"speaker": "color", "text": "本内容仅为盘面复盘记录,不构成投资建议。"}]},
        ]}
    ep = Episode.from_dict(base)
    assert validate_episode(ep, min_seconds=5.0,
                            allowed_stock_codes={"601091.SH"}) == []
    extra = dict(base)
    extra["segments"] = base["segments"] + [
        {"seg_id": "s2", "kind": "stock", "ts_code": "002555.SZ",
         "lines": [{"speaker": "pb", "text": "自由发挥段。"}]}]
    fails = validate_episode(Episode.from_dict(extra), min_seconds=5.0,
                             allowed_stock_codes={"601091.SH"})
    assert any("候选数" in f for f in fails) and any("不在候选清单内" in f for f in fails)
