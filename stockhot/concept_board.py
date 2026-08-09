"""概念板块 —— 细分概念涨跌幅 top5 展示.

从多源拉取概念板块涨跌幅，取 top5 涨/跌幅展示。
概念板块跨行业（如 CPO 跨通信+电子），独立于申万行业 L1/L2 体系。

数据源优先级：
  1. 新浪 stock_sector_spot(indicator='概念') — 175 个概念，有涨跌幅，稳定
  2. 东财 stock_board_concept_name_em() — ~400 个概念（含 CPO/PCB 等细分），常被屏蔽
  3. 同花顺 stock_board_concept_name_ths() — 375 个概念，只有 name+code 无涨跌幅（需额外拉行情）

展示位置：盘前策略表（08:00）+ 盘后收评（after-hours-review Step 2）
不适合盘中高频（拉取 ~2-7 秒）。
"""

from __future__ import annotations

from dataclasses import dataclass
from loguru import logger


@dataclass
class ConceptStat:
    """概念板块统计."""
    name: str
    pct_change: float


def fetch_concept_top5() -> tuple[list[ConceptStat], list[ConceptStat]]:
    """拉取概念板块涨跌幅 top5 涨 + top5 跌.

    返回 (top5_up, top5_down)。所有数据源失败时返回空列表。
    """
    # 源 1：新浪概念（稳定，有涨跌幅）
    top5_up, top5_dn = _fetch_sina_concepts()
    if top5_up:
        return top5_up, top5_dn

    # 源 2：东财概念（更细但常被屏蔽）
    top5_up, top5_dn = _fetch_em_concepts()
    if top5_up:
        return top5_up, top5_dn

    logger.warning("concept_board: 所有概念数据源均失败")
    return [], []


def _fetch_sina_concepts() -> tuple[list[ConceptStat], list[ConceptStat]]:
    """新浪概念板块（175 个，有涨跌幅，稳定）."""
    import akshare as ak
    import pandas as pd

    try:
        df = ak.stock_sector_spot(indicator="概念")
        if df is None or df.empty:
            return [], []

        df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
        df = df.dropna(subset=["涨跌幅"])

        df_sorted = df.sort_values("涨跌幅", ascending=False)
        top5_up = [
            ConceptStat(name=str(r["板块"]), pct_change=float(r["涨跌幅"]))
            for _, r in df_sorted.head(5).iterrows()
        ]
        top5_dn = [
            ConceptStat(name=str(r["板块"]), pct_change=float(r["涨跌幅"]))
            for _, r in df_sorted.tail(5).iloc[::-1].iterrows()
        ]
        logger.info(f"concept_board: 新浪概念 {len(df)} 个，top5 已获取")
        return top5_up, top5_dn
    except Exception as e:
        logger.warning(f"concept_board: 新浪概念失败: {e}")
        return [], []


def _fetch_em_concepts() -> tuple[list[ConceptStat], list[ConceptStat]]:
    """东财概念板块（~400 个含 CPO/PCB 等细分，常被屏蔽）."""
    import akshare as ak
    import pandas as pd

    try:
        df = ak.stock_board_concept_name_em()
        if df is None or df.empty:
            return [], []

        col_name = "板块名称" if "板块名称" in df.columns else df.columns[1]
        col_pct = "涨跌幅" if "涨跌幅" in df.columns else None
        if not col_pct:
            return [], []

        df[col_pct] = pd.to_numeric(df[col_pct], errors="coerce")
        df = df.dropna(subset=[col_pct])
        df_sorted = df.sort_values(col_pct, ascending=False)

        top5_up = [
            ConceptStat(name=str(r[col_name]), pct_change=float(r[col_pct]))
            for _, r in df_sorted.head(5).iterrows()
        ]
        top5_dn = [
            ConceptStat(name=str(r[col_name]), pct_change=float(r[col_pct]))
            for _, r in df_sorted.tail(5).iloc[::-1].iterrows()
        ]
        logger.info(f"concept_board: 东财概念 {len(df)} 个，top5 已获取")
        return top5_up, top5_dn
    except Exception as e:
        logger.warning(f"concept_board: 东财概念失败: {e}")
        return [], []


def format_concept_section(top5_up: list[ConceptStat], top5_dn: list[ConceptStat]) -> str:
    """格式化概念板块章节文本."""
    if not top5_up and not top5_dn:
        return ""

    lines = ["🏷️ 概念板块："]

    if top5_up:
        parts = [f"{c.name}{c.pct_change:+.1f}%" for c in top5_up]
        lines.append(f"  🔺 {'  '.join(parts)}")

    if top5_dn:
        parts = [f"{c.name}{c.pct_change:+.1f}%" for c in top5_dn]
        lines.append(f"  🔻 {'  '.join(parts)}")

    return "\n".join(lines)
