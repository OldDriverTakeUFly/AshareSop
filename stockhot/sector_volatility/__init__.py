"""Sector volatility module — 申万一级行业板块情绪与恐慌程度。

把指数波动率体系（RV + 历史分位 + 恐慌等级）扩展到 31 个申万一级行业板块。
用成分股等权 RV 度量板块情绪温度，与方法论研报 §2.2 的 P90 体系兼容。

核心能力：
- 31 个申万一级板块的成分股等权 RV20/RV60
- 各板块自身历史分位（跨板块可比的恐慌刻度）
- 涨跌停行为代理联读（板块级 Layer 3）
- 截面排名（当日哪个板块最恐慌/最平静）

⚠️ 计算量较大（~4650 只成分股 × 1300 日），**不进 daily-market-scan Wave 编排**，
独立 CLI/cron 触发，结果存 daily_data['sector_volatility']。

入口：run_sector_volatility_analysis(date, sectors=None, days=1300)
"""

from stockhot.sector_volatility.analyzer import (
    run_sector_volatility_analysis,
)
from stockhot.sector_volatility.aggregator import aggregate_sector_rv

__all__ = ["run_sector_volatility_analysis", "aggregate_sector_rv"]
