# davis_analyzer/cardgen/facts.py
"""facts.json 读写、自检、digest 与时效计算。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from davis_analyzer.cardgen.types import Fact

DEFAULT_TTL_DAYS = 7
SOURCE_KINDS = ("report", "tushare", "manual")


def load_facts(path: Path) -> list[Fact]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Fact.from_dict(d) for d in data["facts"]]


def save_facts(path: Path, facts: list[Fact]) -> None:
    path.write_text(json.dumps({"facts": [f.to_dict() for f in facts]},
                               ensure_ascii=False, indent=2), encoding="utf-8")


def _display_selfcheck(f: Fact) -> bool:
    """display 必须以数字边界包含 str(value),且 unit 为空或出现在 display 中。"""
    if not re.search(rf"(?<![\d.]){re.escape(str(f.value))}(?!\d)", f.display):
        return False
    return f.unit == "" or f.unit in f.display


def check_facts(facts: list[Fact]) -> list[str]:
    """事实清单自检(spec §5.1 闸4),返回错误描述列表。"""
    errors: list[str] = []
    seen: set[str] = set()
    for f in facts:
        if f.id in seen:
            errors.append(f"fact id 重复: {f.id}")
        seen.add(f.id)
        if f.source_kind not in SOURCE_KINDS:
            errors.append(f"fact {f.id}: source_kind 非法 {f.source_kind}(须 report/tushare/manual)")
        if not f.source_ref.strip():
            errors.append(f"fact {f.id}: source.ref 必填")
        if f.expires:
            if f.expires < f.as_of:
                errors.append(f"fact {f.id}: expires {f.expires} 早于 as_of {f.as_of}")
        if not _display_selfcheck(f):
            errors.append(f"fact {f.id}: display '{f.display}' 与 value={f.value} unit='{f.unit}' 不自洽")
        if not f.as_of:
            errors.append(f"fact {f.id}: as_of 必填")
    return errors


def effective_expires(f: Fact) -> str:
    if f.expires:
        return f.expires
    return (date.fromisoformat(f.as_of) + timedelta(days=DEFAULT_TTL_DAYS)).isoformat()


def earliest_expires(facts: list[Fact]) -> str:
    return min(effective_expires(f) for f in facts) if facts else ""


def latest_as_of(facts: list[Fact]) -> str:
    return max(f.as_of for f in facts) if facts else ""


def facts_digest(facts: list[Fact]) -> str:
    payload = json.dumps([f.to_dict() for f in sorted(facts, key=lambda x: x.id)],
                         ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
