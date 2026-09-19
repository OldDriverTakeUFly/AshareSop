"""recap 纯数据类型。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DramaEvent:
    kind: str            # reseal(炸板回封)/broken(收盘炸板)/earth_sky(地天板)/ladder(天梯)/lhb(龙虎榜)/long_leg(大长腿)
    label: str           # 中文标签,如「三度炸板回封」
    score: float
    detail: dict[str, object]  # 事件事实:封板时间/炸板次数/振幅等


@dataclass
class Candidate:
    ts_code: str         # 带后缀 605577.SH
    name: str
    sector: str
    drama_score: float
    events: list[DramaEvent] = field(default_factory=list)
    replay_start: str = "09:30"   # 回放区间(供录制单)
    replay_end: str = "15:00"
    notes: list[str] = field(default_factory=list)
    educational: bool = False      # 板块效应可讲=教育性段落
    facts: list[dict] = field(default_factory=list)  # Fact.to_dict() 形态


@dataclass
class DialogueLine:
    speaker: str         # "pb"(实况) / "color"(嘉宾)
    text: str


@dataclass
class EpisodeSegment:
    seg_id: str          # open / s1..sN / close
    kind: str            # scoreboard / stock / outlook
    ts_code: str | None
    lines: list[DialogueLine] = field(default_factory=list)


@dataclass
class Episode:
    trade_date: str      # YYYY-MM-DD
    title: str
    segments: list[EpisodeSegment] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "trade_date": self.trade_date, "title": self.title,
            "segments": [{
                "seg_id": s.seg_id, "kind": s.kind, "ts_code": s.ts_code,
                "lines": [{"speaker": l.speaker, "text": l.text} for l in s.lines],
            } for s in self.segments],
            "facts": self.facts,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Episode:
        return cls(
            trade_date=d["trade_date"], title=d["title"], facts=d.get("facts", []),
            segments=[EpisodeSegment(
                seg_id=s["seg_id"], kind=s["kind"], ts_code=s.get("ts_code"),
                lines=[DialogueLine(speaker=l["speaker"], text=l["text"]) for l in s.get("lines", [])],
            ) for s in d.get("segments", [])],
        )
