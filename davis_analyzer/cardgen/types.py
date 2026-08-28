# davis_analyzer/cardgen/types.py
"""cardgen 纯数据类型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


def _plain(d: Decimal) -> str:
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


@dataclass
class Fact:
    """一条可溯源的数字事实。value 为 display 中的主数值(不做单位换算,换算只在等价匹配)。"""

    id: str
    value: Decimal
    unit: str
    display: str
    as_of: str                 # ISO 日期
    source_kind: str           # report / tushare / manual
    source_ref: str
    expires: str = ""          # ISO 日期;缺省 as_of + DEFAULT_TTL_DAYS

    @classmethod
    def from_dict(cls, d: dict) -> Fact:
        src = d.get("source") or {}
        return cls(id=d["id"], value=Decimal(str(d["value"])), unit=d.get("unit", ""),
                   display=d["display"], as_of=d["as_of"],
                   source_kind=d.get("source_kind", src.get("kind", "manual")),
                   source_ref=d.get("source_ref", src.get("ref", "")), expires=d.get("expires", ""))

    def to_dict(self) -> dict:
        d = {"id": self.id, "value": _plain(self.value),
             "unit": self.unit, "display": self.display, "as_of": self.as_of,
             "source": {"kind": self.source_kind, "ref": self.source_ref}}
        if self.expires:
            d["expires"] = self.expires
        return d


@dataclass
class Failure:
    """一道闸的失败项。gate ∈ {facts, materialize, numbers, compliance, completeness}。"""

    gate: str
    card: str      # 卡片 name;工程级失败为 ""
    field: str     # 字段路径,如 cards[2].rows[0].desc
    detail: str


@dataclass
class ValidateReport:
    topic: str
    passed: bool
    failures: list[Failure] = field(default_factory=list)
    as_of: str = ""       # max(facts.as_of) 展示口径
    expires_at: str = ""  # min(facts.expires) 保守口径
    facts_digest: str = ""
