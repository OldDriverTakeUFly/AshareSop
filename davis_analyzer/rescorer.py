"""Deep-research checklist parser and score rescorer.

Parses filled checklist markdown files produced by checklist_generator.py,
extracts user adjustment values, and applies them to the original
prosperity and distress scores.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from davis_analyzer.types import RescoredResult

if TYPE_CHECKING:
    from davis_analyzer.types import PipelineResult

_ADJUSTMENT_RANGE = 20.0
_PROSPERITY_HEADER = "景气度调整幅度（-20到+20）"
_DISTRESS_HEADER = "困境反转调整幅度（-20到+20）"
_FILLED_MARKER = "（请填写调整值）"
_NUMBER_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")


# ── public API ───────────────────────────────────────────────────────


def parse_checklist(filepath: str) -> dict:
    """Parse a filled checklist markdown file.

    Returns dict with keys: ts_code, name, rank,
    prosperity_adjustment, distress_adjustment, raw_research.
    """
    path = Path(filepath)
    text = path.read_text(encoding="utf-8")

    ts_code = _extract_table_field(text, "股票代码")
    name = _extract_table_field(text, "股票名称")
    rank = _extract_rank(text)

    prosperity_adjustment = _extract_adjustment(text, _PROSPERITY_HEADER)
    distress_adjustment = _extract_adjustment(text, _DISTRESS_HEADER)

    return {
        "ts_code": ts_code,
        "name": name,
        "rank": rank,
        "prosperity_adjustment": prosperity_adjustment,
        "distress_adjustment": distress_adjustment,
        "raw_research": _extract_raw_research(text),
    }


def rescore(
    original_prosperity: float,
    original_distress: float,
    checklist_data: dict,
) -> RescoredResult:
    """Apply parsed adjustments to original scores.

    Clamps results to [0, 100].
    """
    prosp_adj = checklist_data.get("prosperity_adjustment", 0.0)
    distress_adj = checklist_data.get("distress_adjustment", 0.0)

    adjusted_prosperity = _clamp(original_prosperity + prosp_adj, 0.0, 100.0)
    adjusted_distress = _clamp(original_distress + distress_adj, 0.0, 100.0)

    return RescoredResult(
        ts_code=checklist_data.get("ts_code", ""),
        name=checklist_data.get("name", ""),
        original_prosperity=original_prosperity,
        adjusted_prosperity=adjusted_prosperity,
        original_distress=original_distress,
        adjusted_distress=adjusted_distress,
        prosperity_adjustment=prosp_adj,
        distress_adjustment=distress_adj,
    )


def batch_rescore(
    pipeline_result: PipelineResult,
    checklist_dir: str,
) -> dict[str, RescoredResult]:
    """Batch-rescore all stocks that have filled checklists in *checklist_dir*."""
    score_lookup: dict[str, tuple[float, float, str]] = {}
    for score in pipeline_result.scores:
        score_lookup[score.ts_code] = (
            score.prosperity_score,
            score.distress_score,
            score.name,
        )

    results: dict[str, RescoredResult] = {}
    check_dir = Path(checklist_dir)

    for md_file in sorted(check_dir.glob("*.md")):
        parsed = parse_checklist(str(md_file))
        ts_code = parsed.get("ts_code", "")
        if ts_code not in score_lookup:
            continue
        orig_prosp, orig_distress, _ = score_lookup[ts_code]
        results[ts_code] = rescore(orig_prosp, orig_distress, parsed)

    return results


# ── internal helpers ─────────────────────────────────────────────────


def _extract_table_field(text: str, field_name: str) -> str:
    pattern = rf"\|\s*{re.escape(field_name)}\s*\|\s*(.*?)\s*\|"
    match = re.search(pattern, text)
    if match:
        return match.group(1).strip()
    return ""


def _extract_rank(text: str) -> int:
    title_match = re.match(r"#\s*(\d+)_", text)
    if title_match:
        return int(title_match.group(1))
    rank_str = _extract_table_field(text, "排名")
    if rank_str:
        try:
            return int(rank_str)
        except ValueError:
            pass
    return 0


def _extract_adjustment(text: str, header: str) -> float:
    """Extract numeric adjustment value following a header section.

    Finds the header, then searches subsequent lines for the first
    signed/unsigned number on a line that is the user fill line.
    """
    lines = text.splitlines()
    header_idx = -1
    for i, line in enumerate(lines):
        if header in line:
            header_idx = i
            break

    if header_idx == -1:
        return 0.0

    for line in lines[header_idx + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(">"):
            continue
        if _FILLED_MARKER in stripped:
            return _parse_number_from_line(stripped)
        if _NUMBER_RE.search(stripped):
            return _parse_number_from_line(stripped)
        break

    return 0.0


def _parse_number_from_line(line: str) -> float:
    match = _NUMBER_RE.search(line)
    if match:
        try:
            value = float(match.group())
        except ValueError:
            return 0.0
        return _clamp(value, -_ADJUSTMENT_RANGE, _ADJUSTMENT_RANGE)
    return 0.0


def _extract_raw_research(text: str) -> dict:
    """Extract all filled research items from the checklist sections."""
    research: dict[str, str] = {}
    lines = text.splitlines()
    current_section = ""

    for line in lines:
        heading = re.match(r"###\s+(\d+)\.\s*(.+)", line)
        if heading:
            current_section = heading.group(2).strip()
            continue
        item_match = re.match(r"-\s*\[[ xX]\]\s*(.+?)：(.*)", line)
        if item_match and current_section:
            label = item_match.group(1).strip()
            value = item_match.group(2).strip()
            if value and value != "___":
                key = f"{current_section}/{label}"
                research[key] = value

    return research


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
