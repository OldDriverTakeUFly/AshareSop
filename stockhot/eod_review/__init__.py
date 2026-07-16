"""盘后日线复盘引擎（Quantitative EOD Review Engine）.

独立于 daily-market-scan 的盘后复盘系统——直接从 Tushare 拉日线数据，
做量化归因与情绪温度计，产出结构化 markdown 报告。

与 after-hours-review 的关系：两者并存、互补。
- after-hours-review：读 scan 采集结果 + web 搜索催化（定性）
- eod_review：直连 Tushare + 量化计算 + 归因分类（定量）

核心入口::

    from stockhot.eod_review.engine import EODReviewEngine
    result = EODReviewEngine().run_review("20260715")

或 CLI::

    python -m stockhot.eod_review review --date 20260715
    python -m stockhot.eod_review backtest --date 20260708
"""

# 子模块通过完整路径 import，避免 __init__ 阶段循环依赖。
# 公开的类/函数见各子模块的 __all__。
