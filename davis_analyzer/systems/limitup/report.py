"""Markdown 报告输出（study/backtest 共用）."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def df_to_md_table(df: pd.DataFrame) -> str:
    def _fmt(v: object) -> str:
        if isinstance(v, float):
            return f"{v:.4f}"
        return str(v)

    lines = ["| " + " | ".join(str(c) for c in df.columns) + " |",
             "|" + "|".join(["---"] * len(df.columns)) + "|"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(_fmt(v) for v in row) + " |")
    return "\n".join(lines)


def write_report(
    path: Path, title: str, sections: list[tuple[str, str]]
) -> Path:
    parts = [f"# {title}", ""]
    for heading, body in sections:
        parts += [f"## {heading}", "", body, ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    return path
