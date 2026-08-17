"""策略预设（首板/接力）与过滤预算（规格 §9.5/§7.6）."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

import pandas as pd
from loguru import logger

FILTER_BUDGET = 4


class ExitRule(str, Enum):
    OPEN_NEXT = "open_next"
    RIDE_BOARD = "ride_board"
    CLOSE_NEXT = "close_next"


@dataclass(frozen=True)
class StrategyPreset:
    name: str
    board_range: tuple[int, int]
    pattern_labels: tuple[str, ...] | None
    exit_rule: ExitRule
    rank_key: str = "seal_ratio"
    regime_allow: tuple[str, ...] | None = ("回暖", "高潮")
    min_seal_ratio: float | None = None
    min_sector_linkage: int | None = None
    exclude_negative_event: bool = True
    # IS 中位封单过滤是 relay_2 的规格内过滤；默认关闭，CLI 仅在该旗标
    # 为 True 时才把 seal_ratio_median 传入 apply_preset（防止规格外过滤）
    use_is_median_seal: bool = False


PRESETS: dict[str, StrategyPreset] = {
    "first_board": StrategyPreset(
        name="首板启动", board_range=(1, 1),
        pattern_labels=("突破型", "横盘首板型"), exit_rule=ExitRule.OPEN_NEXT,
    ),
    "relay_2": StrategyPreset(
        name="二板接力", board_range=(2, 2), pattern_labels=None,
        exit_rule=ExitRule.RIDE_BOARD, use_is_median_seal=True,
    ),
    "relay_3": StrategyPreset(
        name="三板接力", board_range=(3, 3), pattern_labels=None,
        exit_rule=ExitRule.RIDE_BOARD,
    ),
}


def apply_preset(
    events: pd.DataFrame,
    preset: StrategyPreset,
    regime: pd.DataFrame | None = None,
    *,
    seal_ratio_median: float | None = None,
    min_sector_linkage: int | None = None,
) -> pd.DataFrame:
    eff = replace(preset)
    if min_sector_linkage is not None:
        eff = replace(eff, min_sector_linkage=min_sector_linkage)
    n_filters = sum([
        True,  # board_range 恒为第 1 个过滤
        eff.pattern_labels is not None,
        eff.regime_allow is not None,
        seal_ratio_median is not None or eff.min_seal_ratio is not None,
        eff.min_sector_linkage is not None,
        eff.exclude_negative_event,
    ]) - 1
    if n_filters > FILTER_BUDGET:
        raise ValueError(f"过滤条件超过预算({FILTER_BUDGET})：当前 {n_filters} 条")

    ev = events.copy()
    lo, hi = eff.board_range
    mask = ev["consecutive_boards"].between(lo, hi)
    if eff.pattern_labels is not None:
        mask &= ev["pattern_label"].isin(eff.pattern_labels)
    if eff.regime_allow is not None and regime is not None:
        allowed = regime[regime["regime_label"].isin(eff.regime_allow)]["trade_date"]
        mask &= ev["trade_date"].isin(set(allowed))
    thr = eff.min_seal_ratio if eff.min_seal_ratio is not None else seal_ratio_median
    if thr is not None:
        mask &= ev["seal_ratio"].fillna(0) >= thr
    if eff.min_sector_linkage is not None:
        mask &= ev["sector_linkage"].fillna(0) >= eff.min_sector_linkage
    if eff.exclude_negative_event:
        mask &= ~ev["negative_event_30d"].fillna(False)
    out = ev[mask].sort_values(
        ["trade_date", eff.rank_key], ascending=[True, False]
    )
    logger.info("preset[{}]: {} → {} 候选", eff.name, len(ev), len(out))
    return out.reset_index(drop=True)
