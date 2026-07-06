"""Index technical analysis module.

为大盘/指数提供技术面分析能力，补充现有 technical_analyzer（仅个股）的缺口。

核心能力：
- 采集指数 OHLCV（AKShare + Tushare 双源 fallback）
- 复用 technical_analyzer.indicators 的指标函数（MA/MACD/RSI/KDJ/布林/支撑压力/量价）
- 6 阶段趋势识别（主升/上涨中回调/高位震荡筑顶/主跌/下跌中反弹/低位筑底）
- composite_technical_score 评分（强势/震荡/弱势）
- 盘前预期行为（每个阶段对应一个操作建议，避免梭哈）

入口：run_index_technical_analysis(date, indices=None)
"""

from stockhot.index_technical.analyzer import (
    DEFAULT_INDICES,
    INDEX_NAMES,
    run_index_technical_analysis,
)
from stockhot.index_technical.data_loader import fetch_index_ohlcv
from stockhot.index_technical.stages import classify_stage

__all__ = [
    "DEFAULT_INDICES",
    "INDEX_NAMES",
    "run_index_technical_analysis",
    "fetch_index_ohlcv",
    "classify_stage",
]
