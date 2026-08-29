# davis_analyzer/cardgen/compliance.py
"""合规扫描:敏感词(spec 全部内容文本)、尾卡免责话术、foot 来源标注、waivers 豁免。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from davis_analyzer.cardgen.types import Failure

# 诱导交易句式(2026-08-29 金融专项治理):词表拦不住的交易指向框架,按正则拦截
import re

INDUCEMENT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"买(的)?(是|什么)|买(弹性|确定)"), "交易框架句(买的是什么/买弹性)"),
    (re.compile(r"赌[A-Za-z0-9\u4e00-\u9fa5]{1,6}|赌徒"), "赌博化表述(赌X/赌徒)"),
    (re.compile(r"下一个(动作|步骤)"), "行为指令句"),
    (re.compile(r"你(应该|可以|要|不妨)"), "第二人称劝导"),
]

REQUIRED_PHRASES = ("不构成投资建议",)
_WORDS_FILE = Path(__file__).resolve().parents[2] / "scripts" / "card_factory" / "sensitive_words.txt"

# 内容字段白名单:只扫/只校验这些键(数值闸复用),theme/color/cls 等展示键跳过
_SKIP_KEYS = {"type", "name", "theme", "color", "tag_color", "bg", "text_color",
              "cls", "on_dark", "size", "first_left", "pct"}


def iter_content_strings(node: object, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _SKIP_KEYS:
                continue
            yield from iter_content_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, x in enumerate(node):
            yield from iter_content_strings(x, f"{path}[{i}]")
    elif isinstance(node, str):
        yield node, path


def load_words() -> list[str]:
    return [w.strip() for w in _WORDS_FILE.read_text(encoding="utf-8").splitlines()
            if w.strip() and not w.strip().startswith("#")]


def load_waivers(project_dir: Path) -> list[dict]:
    p = project_dir / "compliance_waivers.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def scan_compliance(cards: list[dict], waivers: list[dict]) -> list[Failure]:
    """敏感词命中(可用 {word, card, reason} 豁免)+ 诱导句式 + 尾卡免责 + 每卡 foot 来源。"""
    failures: list[Failure] = []
    words = load_words()
    waiv = {(w["word"], w["card"]) for w in waivers}
    for idx, card in enumerate(cards):
        name = card.get("name", f"cards[{idx}]")
        for text, path in iter_content_strings(card, f"cards[{idx}]"):
            for w in words:
                if w in text and (w, name) not in waiv:
                    failures.append(Failure("compliance", name, path, f"敏感词命中: {w}"))
            for pat, desc in INDUCEMENT_PATTERNS:
                if pat.search(text):
                    failures.append(Failure("compliance", name, path, f"诱导交易句式: {desc}"))
    if cards:
        last = cards[-1]
        last_name = last.get("name", f"cards[{len(cards) - 1}]")
        foot = str(last.get("foot", ""))
        for phrase in REQUIRED_PHRASES:
            if phrase not in _all_text(last):
                failures.append(Failure("compliance", last_name, "foot", f"尾卡缺少必备话术: {phrase}"))
        if "数据来源" not in foot and "来源" not in foot:
            failures.append(Failure("compliance", last_name, "foot", "尾卡 foot 缺少数据来源标注"))
    for idx, card in enumerate(cards):
        foot = str(card.get("foot", ""))
        if not foot:
            failures.append(Failure("compliance", card.get("name", f"cards[{idx}]"), "foot",
                                    "foot 缺失(每卡必须标注数据来源)"))
        elif "来源" not in foot and "数据" not in foot:
            failures.append(Failure("compliance", card.get("name", f"cards[{idx}]"), "foot",
                                    "foot 缺少数据来源标注"))
    return failures


def _all_text(card: dict) -> str:
    return " ".join(s for s, _ in iter_content_strings(card, ""))
