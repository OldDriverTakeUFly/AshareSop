"""盘中恐慌预警检测模块.

三大信号独立检测（任一达标即触发预警）：
1. 系统性恐慌：≥3 个指数 RV20 历史分位 ≥ 90
2. 行为面恐慌抛售：涨跌停比 < 0.5 或 跌停占比 > 50%
3. iVIX/V-R 极端值：iVIX > 25 或 V/R > 1.3

每个检测独立 try/except，单源失败不影响其他信号（降级为"数据不可用"）。

与 ``stockhot.volatility.analyzer`` 的盘后分析互补：
- 盘后分析用日线算精确 RV20；本模块盘中用实时价替换序列最后一点
- 盘后 limit_list_d；本模块盘中用 AKShare stock_zt_pool_em（盘中实时）
"""

from stockhot.alert.panic_detector import (
    PanicReport,
    SignalResult,
    detect_panic_signals,
    format_alert_message,
)

__all__ = [
    "PanicReport",
    "SignalResult",
    "detect_panic_signals",
    "format_alert_message",
]
