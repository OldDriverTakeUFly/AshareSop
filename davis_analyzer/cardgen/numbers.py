# davis_analyzer/cardgen/numbers.py
"""数值 token 提取与事实命中判定——cardgen 数字溯源核心。

规则(spec §5.1):先以掩码剔除豁免片段(日期/中文数字之外,含标识符、十六进制色值、
时间与比例),再提取剩余数值 token;区间(N-M亿 / N~M%)两端各成一个 token 并共享尾随单位。
命中判定为 (绝对值, 单位字符串) 与某条 Fact 严格相等;token 的正负号只表方向,取绝对值比较。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from davis_analyzer.cardgen.types import Fact

# ── 豁免掩码(顺序敏感:长的/具体的在前)──
_EXEMPT_RE = [
    re.compile(r"#[0-9a-fA-F]{3,8}"),        # 十六进制色值 #ff2442
    re.compile(r"\d{4}-\d{2}-\d{2}"),        # ISO 日期
    re.compile(r"\d{8}"),                    # 紧凑交易日 20260828
    re.compile(r"\d{4}H\d"),                 # 2026H2
    re.compile(r"(?<![\d.])\d{4}(?![\d.%xX倍万亿家条道份张只个次笔股强城])"),  # 年份(后不接单位/小数,防误杀 2700亿)
    re.compile(r"(?<!\d)\d{1,2}[/-]\d{1,2}(?!\d)"),  # mm/dd、12/07、9-10(月)(两侧无邻位数字,防咬区间 400-600)
    re.compile(r"\d{1,2}月(?:底|初|末)?"),   # 10月底、12月(裸月视作时间标签)
    re.compile(r"[A-Za-z]+\d+[A-Za-z0-9]*"), # 型号/代号 C600、BR166、B30A、L600、H1
    re.compile(r"\d{6}"),                    # 股票代码 688801
    re.compile(r"\d+:\d+"),                  # 时间/比例 20:00、3:4
]

# 单位:长在前(亿港元 先于 亿);CJK 计数单位一并纳入(4家 这类计数也需溯源)
# 捕获组:extract_tokens 以 m.group(2)/m.group(3) 读单位
_UNIT = r"(亿港元|万亿|港元|%|x|X|倍|亿|万|元|家|条|道|份|张|只|个|次|笔|股|强|城)?"
_RANGE_RE = re.compile(rf"[+≈~～\-]?(\d+(?:\.\d+)?)\s*(?:-|~|～|—|至)\s*(\d+(?:\.\d+)?)\s*{_UNIT}")
_NUM_RE = re.compile(rf"[+≈~～\-]?(\d+(?:\.\d+)?)\s*{_UNIT}")


@dataclass(frozen=True)
class NumberToken:
    raw: str
    value: Decimal  # 绝对值
    unit: str


def mask_exemptions(text: str) -> str:
    out = text
    for pat in _EXEMPT_RE:
        out = pat.sub("□", out)
    return out


def _mk(raw: str, num: str, unit: str) -> NumberToken:
    return NumberToken(raw=raw, value=Decimal(num).__abs__(), unit=unit)


def extract_tokens(text: str) -> list[NumberToken]:
    masked = mask_exemptions(text)
    tokens: list[NumberToken] = []
    spans: list[tuple[int, int]] = []
    for m in _RANGE_RE.finditer(masked):
        unit = m.group(3) or ""
        tokens.append(_mk(m.group(0), m.group(1), unit))
        tokens.append(_mk(m.group(0), m.group(2), unit))
        spans.append(m.span())
    rest = masked
    for s, e in spans:
        rest = rest[:s] + "□" * (e - s) + rest[e:]
    for m in _NUM_RE.finditer(rest):
        tokens.append(_mk(m.group(0), m.group(1), m.group(2) or ""))
    return tokens


def unmatched_tokens(text: str, facts: Iterable[Fact]) -> list[NumberToken]:
    known = {(f.value.__abs__(), f.unit) for f in facts}
    return [t for t in extract_tokens(text) if (t.value, t.unit) not in known]
