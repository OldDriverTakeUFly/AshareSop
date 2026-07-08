"""Volatility observation module — A 股"中国版 VIX"五层观察体系。

为大盘/指数提供波动率分析能力，补全 daily-market-scan 缺失的"恐慌温度计"维度。
方法论详见 ``docs/方法论/A股波动率观察框架方法论深度研报.md``。

核心能力（Layer 1/2/5，可日频计算部分）：
- 采集指数日线 + iVIX 历史（AKShare + Tushare 双源 fallback）
- 已实现波动率 RV20/RV60（对数收益率 × √242 年化）
- RV 历史分位数（P0-P100 标准化恐慌刻度）
- iVIX 隐含波动率 + V/R 比率（期权昂贵度）
- 恐慌等级判定（极度自满→极度恐慌 6 档）

Layer 3（涨跌停行为代理）不入库——已在 limit_up 模块，盘后总结联读。
Layer 4（期权期限结构）暂未实现——AKShare iVIX 仅单点。

入口：``run_volatility_analysis(date, indices=None)``
"""

from stockhot.volatility.analyzer import (
    DEFAULT_INDICES,
    INDEX_NAMES,
    analyze_iv_rv_basis,
    analyze_single_index,
    classify_panic_level,
    percentile_rank,
    realized_vol,
    run_volatility_analysis,
    vr_ratio,
)
from stockhot.volatility.data_loader import fetch_index_history, fetch_ivix_history

__all__ = [
    "DEFAULT_INDICES",
    "INDEX_NAMES",
    "analyze_iv_rv_basis",
    "analyze_single_index",
    "classify_panic_level",
    "fetch_index_history",
    "fetch_ivix_history",
    "percentile_rank",
    "realized_vol",
    "run_volatility_analysis",
    "vr_ratio",
]
