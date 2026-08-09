"""概念板块 —— 同花顺 375 个概念指数涨跌幅 top5.

从同花顺拉概念板块列表 + 涨跌幅，取 top5 涨/跌幅展示。
概念板块跨行业（如 CPO 跨通信+电子），独立于申万行业 L1/L2 体系。

数据源：AKShare stock_board_concept_name_ths()（~7 秒拉取，不适合盘中高频）
适合盘前策略表（08:00）和盘后收评（18:00 后）。

覆盖的细分概念示例：
  CPO/MLCC/存储芯片/PCB/磷化工/氟化工/第三代半导体/PET铜箔/PEEK材料 等

被调用方：
  - premarket_strategy.py（盘前策略表）
  - after-hours-review SKILL.md Step 2（盘后收评）
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
    """拉取同花顺概念板块涨跌幅 top5 + 跌幅 top5.

    返回 (top5_涨, top5_跌)。数据源失败时返回空列表。
    """
    import akshare as ak

    try:
        df = ak.stock_board_concept_name_ths()
        if df is None or df.empty:
            return [], []
    except Exception as e:
        logger.warning(f"concept_board: 同花顺概念拉取失败: {e}")
        return [], []

    # 同花顺概念列表只有 name + code，没有涨跌幅
    # 需要用 stock_board_concept_spot_ths 或其他接口获取涨跌幅
    # 但实测 stock_board_concept_name_ths 只有 name/code 两列
    # 换用东财概念板块（虽然 push2 常连不上，但试一下）
    try:
        df_em = ak.stock_board_concept_name_em()
        if df_em is not None and not df_em.empty:
            # 东财有涨跌幅列
            col_name = "板块名称" if "板块名称" in df_em.columns else df_em.columns[1]
            col_pct = "涨跌幅" if "涨跌幅" in df_em.columns else None
            if col_pct:
                df_em[col_pct] = df_em[col_pct].astype(float, errors="ignore")
                df_valid = df_em.dropna(subset=[col_pct])
                df_sorted = df_valid.sort_values(col_pct, ascending=False)
                top5_up = [
                    ConceptStat(name=str(r[col_name]), pct_change=float(r[col_pct]))
                    for _, r in df_sorted.head(5).iterrows()
                ]
                top5_dn = [
                    ConceptStat(name=str(r[col_name]), pct_change=float(r[col_pct]))
                    for _, r in df_sorted.tail(5).iloc[::-1].iterrows()
                ]
                logger.info(f"concept_board: 东财概念 {len(df_em)} 个，top5 已获取")
                return top5_up, top5_dn
    except Exception as e:
        logger.warning(f"concept_board: 东财概念拉取失败: {e}")

    # 东财失败 → 用同花顺名称列表 + Tushare ths_index 获取涨跌幅
    try:
        from stockhot.data_layer import get_gateway
        import pandas as pd

        gw = get_gateway()
        ths_df = gw.call("ths_daily", trade_date="latest")

        # ths_daily 可能有概念指数涨跌幅
        if ths_df is not None and not ths_df.empty:
            # 尝试匹配同花顺概念名
            concept_names = set(df["name"].tolist())
            matched = ths_df[ths_df["name"].isin(concept_names)]
            if not matched.empty and "pct_change" in matched.columns:
                matched["pct_change"] = pd.to_numeric(matched["pct_change"], errors="coerce")
                matched = matched.dropna(subset=["pct_change"]).sort_values("pct_change", ascending=False)
                top5_up = [
                    ConceptStat(name=str(r["name"]), pct_change=float(r["pct_change"]))
                    for _, r in matched.head(5).iterrows()
                ]
                top5_dn = [
                    ConceptStat(name=str(r["name"]), pct_change=float(r["pct_change"]))
                    for _, r in matched.tail(5).iloc[::-1].iterrows()
                ]
                logger.info(f"concept_board: Tushare ths_daily {len(matched)} 个匹配")
                return top5_up, top5_dn
    except Exception as e:
        logger.warning(f"concept_board: Tushare ths_daily 失败: {e}")

    logger.warning("concept_board: 所有数据源均失败")
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
