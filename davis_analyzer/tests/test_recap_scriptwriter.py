# davis_analyzer/tests/test_recap_scriptwriter.py
"""recap 剧本:prompt 注入/LLM JSON 解析/自纠错循环/Episode 装配。"""
from __future__ import annotations

import json

import pytest

from davis_analyzer.systems.recap.scriptwriter import (
    ScriptGenError, assemble_episode, build_user_prompt, generate_episode, parse_episode_json,
)
from davis_analyzer.systems.recap.types import Candidate


def _cand():
    return Candidate(
        ts_code="605577.SH", name="龙版传媒", sector="出版", drama_score=98.0,
        replay_start="09:27:00", replay_end="14:51:00",
        notes=["5 连板", "3 度炸板后回封"], educational=True,
        facts=[{"id": "605577.SH_boards", "value": "5", "unit": "板", "display": "5连板",
                "as_of": "2026-09-18", "source": {"kind": "stockhot", "ref": "r"}},
               {"id": "605577.SH_broken", "value": "3", "unit": "次", "display": "3次炸板",
                "as_of": "2026-09-18", "source": {"kind": "stockhot", "ref": "r"}}])


def _bundle():
    return {"index": [{"code": "000001.SH", "name": "上证指数", "close": 3875.6, "pct_chg": -0.411},
                      {"code": "399001.SZ", "name": "深证成指", "close": 12345.6, "pct_chg": 0.52},
                      {"code": "399006.SZ", "name": "创业板指", "close": 2710.2, "pct_chg": 0.85}],
            "breadth": {"up": 3200, "down": 1900}, "limit_up_count": 62,
            "boards": [{"board_count": 5, "stocks": [{"code": "605577.SH", "name": "龙版传媒"}]}],
            "names": {}, "pool": [], "broken": [], "down": [], "lhb_codes": set(),
            "lhb_detail": [], "brokers": [], "amplitude_top": []}


def _llm_json():
    return json.dumps({"title": "五连板封神与三度回封之夜", "segments": [
        {"seg_id": "open", "kind": "scoreboard", "ts_code": None, "lines": [
            {"speaker": "pb",
             "text": "今日战报,欢迎收看A股全场回放:上证收在3876点,下跌0.41%,"
                     "全场3200家上涨、1900家下跌,今晚的高光时刻一个比一个精彩。"}]},
        {"seg_id": "s1", "kind": "stock", "ts_code": "605577.SH", "lines": [
            {"speaker": "pb",
             "text": "看这段回放,5连板!第3次炸板,又给硬生生封了回去,这个统治力什么水平?"},
            {"speaker": "color",
             "text": "出版板块今天集体起立,资金抱团的意图非常明确,每一波炸板都被更坚决的买盘接住,"
                     "这就是今天最硬的高光时刻。"}]},
        {"seg_id": "close", "kind": "outlook", "ts_code": None, "lines": [
            {"speaker": "color",
             "text": "天梯的高度明天继续量,断板与晋级的故事还会上演。"
                     "本内容仅为盘面复盘记录,不构成投资建议。"}]},
    ]}, ensure_ascii=False)


class _FakeProvider:
    def __init__(self, contents: list[str]):
        self._contents = list(contents)
        self.calls: list[str] = []

    def complete(self, prompt: str, system: str = "", max_tokens: int = 800,
                 temperature: float = 0.3):
        self.calls.append(prompt)
        class _R:
            content = self._contents.pop(0)
        return _R()


def test_prompt_contains_facts_and_style():
    p = build_user_prompt([_cand()], _bundle())
    assert "5连板" in p and "605577.SH" in p
    assert "压哨绝杀" in p            # NBA 映射表注入
    assert "09:27" in p               # 回放窗注入


def test_parse_episode_json_extracts_from_markdown_fence():
    content = f"好的,以下是剧本:\n```json\n{_llm_json()}\n```"
    d = parse_episode_json(content)
    assert d["title"].startswith("五连板")


def test_generate_ok_and_validated():
    ep = generate_episode("2026-09-18", [_cand()], _bundle(),
                          provider=_FakeProvider([_llm_json()]))
    assert ep.segments[0].kind == "scoreboard"
    assert ep.facts and any(f["id"] == "idx_sh_close" for f in ep.facts)


def test_generate_retries_then_raises():
    bad = json.dumps({"title": "x", "segments": [
        {"seg_id": "open", "kind": "stock", "ts_code": None, "lines":
         [{"speaker": "pb", "text": "这票可以买入,涨3个点。"}]}]}, ensure_ascii=False)
    with pytest.raises(ScriptGenError):
        generate_episode("2026-09-18", [_cand()], _bundle(),
                         provider=_FakeProvider([bad, bad, bad]))


def test_assemble_rejects_unknown_segment():
    with pytest.raises(ScriptGenError):
        assemble_episode("2026-09-18", [_cand()], _bundle(),
                         {"title": "t", "segments": [
                             {"seg_id": "x", "kind": "wild", "ts_code": None, "lines": []}]})


def test_assemble_rejects_bare_string_line():
    """v4 实锤:LLM 会把 lines 写成裸字符串数组——必须抛 ScriptGenError 进自纠错,而非 AttributeError。"""
    import pytest as _pytest
    from davis_analyzer.systems.recap.scriptwriter import ScriptGenError, assemble_episode
    bad = {"title": "t", "segments": [
        {"seg_id": "open", "kind": "scoreboard", "ts_code": None, "lines": ["裸字符串"]}]}
    with _pytest.raises(ScriptGenError):
        assemble_episode("2026-09-18", [_cand()], _bundle(), bad)
