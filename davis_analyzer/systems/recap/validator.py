# davis_analyzer/recap/validator.py
"""recap 三道闸:①完整性(骨架/speaker/免责/时长预算) ②数字全命中 facts ③敏感词。"""
from __future__ import annotations

import re

from davis_analyzer.systems.cardgen.compliance import load_words
from davis_analyzer.systems.cardgen.numbers import unmatched_tokens
from davis_analyzer.systems.cardgen.types import Fact
from davis_analyzer.systems.recap.constants import (
    EXTRA_SENSITIVE_WORDS, INDUCEMENT_PATTERNS_NARROW, REQUIRED_DISCLAIMER,
)
from davis_analyzer.systems.recap.types import Episode

_MIN_SECONDS, _MAX_SECONDS = 25.0, 125.0   # 2026-09-18 v4:open/close 砍到各约3s,下限放宽
_CHARS_PER_SECOND = 4.2          # edge-tts 中文语速经验值
_MAX_LINE_SECONDS = 20.0


def _facts_of(ep: Episode) -> list[Fact]:
    """facts + 无单位变体(解说常省略单位:「3876」vs fact「3876点」;只放宽单位、
    数值本身仍必须命中,LLM 编不出新数字)。不动 cardgen/numbers.py 共享代码。"""
    out: list[Fact] = []
    for d in ep.facts:
        f = Fact.from_dict(d)
        out.append(f)
        if f.unit:
            out.append(Fact(id=f"{f.id}_bare", value=f.value, unit="", display=f.display,
                            as_of=f.as_of, source_kind=f.source_kind, source_ref=f.source_ref))
    return out


def validate_episode(ep: Episode, min_seconds: float = _MIN_SECONDS,
                     max_seconds: float = _MAX_SECONDS,
                     allowed_stock_codes: set[str] | None = None) -> list[str]:
    fails: list[str] = []
    if not ep.segments:
        return ["剧本为空"]
    if ep.segments[0].kind != "scoreboard":
        fails.append("完整性: 首段必须为 scoreboard(片头比分牌)")
    if ep.segments[-1].kind != "outlook":
        fails.append("完整性: 末段必须为 outlook(明日看点)")

    stock_segs = [s for s in ep.segments if s.kind == "stock"]
    if allowed_stock_codes is not None:
        # 段落与候选一一对应(2026-09-18 首跑实锤:LLM 会拿天梯信息自由发挥加段)
        if len(stock_segs) != len(allowed_stock_codes):
            fails.append(f"完整性: stock 段数 {len(stock_segs)} ≠ 候选数 {len(allowed_stock_codes)},"
                         f"只准为候选各写一段,不得增删")
        for s in stock_segs:
            if s.ts_code not in allowed_stock_codes:
                fails.append(f"完整性: {s.seg_id} 的 {s.ts_code} 不在候选清单内,删除该段")

    facts = _facts_of(ep)
    words = set(load_words()) | set(EXTRA_SENSITIVE_WORDS)
    total_chars = 0
    for seg in ep.segments:
        for line in seg.lines:
            total_chars += len(line.text)
            if line.speaker not in ("pb", "color"):
                fails.append(f"完整性: {seg.seg_id} 出现非法 speaker={line.speaker!r}(只允许 pb/color)")
            unmatched = unmatched_tokens(line.text, facts)
            if unmatched:
                toks = ", ".join(t.raw for t in unmatched[:3])
                fails.append(f"数字闸: {seg.seg_id}[{line.speaker}] 数字未命中 facts: {toks}")
            for w in sorted(words):
                if w and w in line.text:
                    fails.append(f"敏感词: {seg.seg_id}[{line.speaker}] 命中「{w}」")
            for pat, desc in INDUCEMENT_PATTERNS_NARROW:
                if pat.search(line.text):
                    fails.append(f"敏感词: {seg.seg_id}[{line.speaker}] {desc}")
            if len(line.text) / _CHARS_PER_SECOND > _MAX_LINE_SECONDS:
                fails.append(f"完整性: {seg.seg_id}[{line.speaker}] 单句超{_MAX_LINE_SECONDS:.0f}s,请拆句")
    close_text = "".join(l.text for l in ep.segments[-1].lines)
    if REQUIRED_DISCLAIMER not in close_text:
        fails.append(f"完整性: 末段缺少免责原话「不构成投资建议」")
    est = total_chars / _CHARS_PER_SECOND
    if not (min_seconds <= est <= max_seconds):
        fails.append(f"完整性: 预计时长 {est:.0f}s 超出预算 {min_seconds:.0f}-{max_seconds:.0f}s")
    return fails
