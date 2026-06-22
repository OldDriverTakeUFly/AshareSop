#!/usr/bin/env python3
"""景气度评分模板脚本 (通用版).

这是一个参数化模板：复制此文件后，仅需修改顶部 CONFIG 块即可对任意 A 股标的
执行完整景气度评分与周期定位。本脚本只读调用 davis_analyzer 核心函数，不修改
任何源码，不复制 prosperity.py 实现。

评分维度:
    1. 景气度复合评分 — 营收/利润/斜率/持续时间四维加权 (prosperity.py: calculate_prosperity_score)
    2. ΔG 计算 — 增速的边际变化 (prosperity.py: calculate_delta_g / composite delta_g)
    3. 周期阶段分类 — 加速期/减速期/上升拐点/下降拐点 (prosperity_sector.py: classify_stock_stage)
    4. 二次点火筛选 — G 和 ΔG 同时为正 + 经营 CF 为正 (prosperity_sector.py: screen_g_delta_g_ignition)
    5. 拐点分析 — 识别增速过零季度 + 催化剂/风险因子 (prosperity_inflection.py: analyze_inflection)
    6. 行业聚合对比 — 行业内排名 + 相对 ΔG (prosperity_sector.py: compute_relative_delta_g)

────────────────────────────────────────────────────────────────────────────────
【必填配置】
    打开本文件，修改 CONFIG 块中的变量：
        TARGET_CODE    : 目标股票的 ts_code (例如 "600519.SH", "000001.SZ")
        TARGET_NAME    : 目标公司中文名称 (用于日志与 JSON 输出)
        OUTPUT_DIR     : JSON 结果文件输出目录 (相对/绝对路径均可)
        PEER_CODES     : 同行业可比标的 ts_code 列表 (用于行业聚合和相对 ΔG)

【运行依赖】
    - Python 3.12+
    - 已安装 davis_analyzer (在仓库根目录执行 `pip install -e .`)
    - 项目根目录存在 .env 文件，内含 TUSHARE_TOKEN=xxx
    - loguru 第三方库

【用法】
    # 1. 复制并修改 CONFIG
    cp scoring-template.py my_prosperity_scoring.py
    # 编辑 my_prosperity_scoring.py 的 CONFIG 块

    # 2. 运行 (需在仓库根目录，确保 davis_analyzer 可被导入)
    .venv/bin/python .agents/skills/industry-prosperity/references/my_prosperity_scoring.py

【输出】
    JSON 文件写入 OUTPUT_DIR/{TARGET_CODE}_prosperity_score.json，
    同时将完整 JSON 打印到 stdout。
────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

# ── davis_analyzer 核心模块（只读调用，不修改源码，不复制实现）──
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.prosperity import (
    calculate_prosperity_score,
)
from davis_analyzer.prosperity_inflection import analyze_inflection
from davis_analyzer.prosperity_sector import (
    aggregate_industry_prosperity,
    classify_stock_stage,
    compute_relative_delta_g,
    generate_ignition_reasons,
    generate_risk_warnings,
    screen_g_delta_g_ignition,
)
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.types import FinancialData, ProsperityScore, StockInfo

# ========== CONFIG: 填入你的标的 ==========
TARGET_CODE = "000000.SH"  # 目标股票 ts_code (例: "600519.SH", "000001.SZ")
TARGET_NAME = "目标公司"  # 目标公司名称 (中文，用于日志/JSON 输出)
OUTPUT_DIR = "output"  # JSON 输出目录 (相对仓库根目录或绝对路径)
PEER_CODES: list[str] = []  # 同行业可比标的 ts_code 列表 (用于行业聚合和相对 ΔG)
# ==========================================

# ── 派生常量 (请勿手动修改) ──
PERIODS = 12  # 获取 3 年（12 季度）财务数据，确保 ΔG 有足够数据点
_OUTPUT_PATH = Path(OUTPUT_DIR) / f"{TARGET_CODE}_prosperity_score.json"

# 山峰理论阈值
THRESHOLD_30PCT = 30.0  # 30% 阈值：净利润增速降至 30% 以下时超额收益显著下滑


# ════════════════════════════════════════════════════════════════════════════
# 山峰理论定位解释器 — 将 ΔG 符号与 G 水平映射到山峰位置
# ════════════════════════════════════════════════════════════════════════════


def _classify_mountain_position(growth_rate: float, delta_g: float) -> dict[str, str]:
    """将 G 和 ΔG 映射到山峰理论的三个区域.

    输入: growth_rate (当前增速 %), delta_g (增速的边际变化，百分点).
    返回: {"zone": ..., "meaning": ..., "strategy": ...}
    """
    if delta_g > 0 and growth_rate > 0:
        return {
            "zone": "左山峰 (Left Side)",
            "meaning": "增速在加速，戴维斯双击机会区。EPS 和 PE 双双上行。",
            "strategy": "核心配置区：买在增速加速期",
        }
    elif delta_g <= 0 and growth_rate > THRESHOLD_30PCT:
        return {
            "zone": "右山坡起点 (Right Side Start)",
            "meaning": "增速虽高但开始减速，估值收缩风险区。EPS 上行但 PE 收缩。",
            "strategy": "减仓/切换：卖在减速刚开始时",
        }
    elif delta_g <= 0 and growth_rate > 0:
        return {
            "zone": "右山坡 (Right Side)",
            "meaning": "增速减速且已低于 30% 阈值，超额收益大概率下滑。",
            "strategy": "警惕 30% 阈值：增速降至 30% 以下时收益显著恶化",
        }
    else:
        return {
            "zone": "山后 (Behind Mountain)",
            "meaning": "增速转负，戴维斯双杀区。EPS 和 PE 双双下行。",
            "strategy": "回避：等待下一轮奇点",
        }


def _classify_clock_quadrant(stage: str) -> dict[str, str]:
    """将引擎阶段分类映射到成长股投资时钟四象限.

    输入: stage (引擎分类结果: "加速期" / "减速期" / "上升拐点" / "下降拐点").
    返回: {"quadrant": ..., "penetration_hint": ...}
    """
    mapping = {
        "加速期": {
            "quadrant": "加速 (Acceleration)",
            "penetration_hint": "渗透率 5-15%，主升浪，戴维斯双击核心区",
        },
        "减速期": {
            "quadrant": "减速 (Deceleration)",
            "penetration_hint": "渗透率 15-30%，增速放缓，博弈加重",
        },
        "上升拐点": {
            "quadrant": "奇点 (Singularity)",
            "penetration_hint": "渗透率刚突破临界点，早期布局，高风险高收益",
        },
        "下降拐点": {
            "quadrant": "反奇点 (Anti-singularity)",
            "penetration_hint": "景气拐点确认，回避或等待下一轮奇点",
        },
    }
    return mapping.get(
        stage,
        {"quadrant": "未知", "penetration_hint": "数据不足，无法定位"},
    )


# ════════════════════════════════════════════════════════════════════════════
# 主评分流程
# ════════════════════════════════════════════════════════════════════════════


def _build_stock_info(client: TushareClient, ts_code: str, name: str) -> StockInfo:
    """从 Tushare 获取股票行业信息，构造 StockInfo."""
    try:
        stock_df = client.get_stock_list()
        row = stock_df[stock_df["ts_code"] == ts_code]
        if not row.empty:
            industry = str(row.iloc[0].get("industry", "") or "")
            real_name = str(row.iloc[0].get("name", name) or name)
        else:
            industry = ""
            real_name = name
    except Exception:
        logger.warning("无法从 stock_list 获取 {} 的行业信息，使用默认值", ts_code)
        industry = ""
        real_name = name

    return StockInfo(
        ts_code=ts_code,
        name=real_name,
        industry=industry,
        list_status="L",
        is_cyclical=False,
    )


def score_prosperity() -> dict:
    """对 CONFIG 指定的标的执行完整景气度评分，返回 JSON 可序列化的 dict.

    流程:
        Step1  初始化 TushareClient
        Step2  fetch_financial_data → 3年(12季度)财务数据
        Step3  calculate_prosperity_score → 四维评分 + ΔG
        Step4  classify_stock_stage → 周期阶段分类
        Step5  山峰理论 + 成长股投资时钟定位
        Step6  analyze_inflection → 拐点分析与叙述
        Step7  二次点火筛选 + 风险警告
        Step8  (可选) 行业聚合对比
        Step9  组装最终 JSON dict
    """

    ts_code = TARGET_CODE
    stock_name = TARGET_NAME

    logger.info("=" * 70)
    logger.info("{} ({}) 景气度评分与周期定位", stock_name, ts_code)
    logger.info("=" * 70)

    # ── Step 1: 创建 TushareClient ──
    logger.info("Step 1: 初始化 TushareClient...")
    client = TushareClient()

    # ── Step 2: 获取财务数据 ──
    logger.info("Step 2: 获取 {} 财务数据 (periods={})...", ts_code, PERIODS)
    fin_data = fetch_financial_data(client, ts_code, periods=PERIODS)
    if not fin_data:
        logger.error("财务数据为空，无法继续评分")
        sys.exit(1)
    logger.info("获取到 {} 期财务数据", len(fin_data))
    for fd in fin_data:
        logger.debug(
            "  {} rev={:.0f} np={:.0f} " "yoy_rev={} yoy_prof={}",
            fd.report_period,
            fd.revenue or 0,
            fd.net_profit or 0,
            f"{fd.yoy_revenue_growth*100:.1f}%" if fd.yoy_revenue_growth else "N/A",
            f"{fd.yoy_profit_growth*100:.1f}%" if fd.yoy_profit_growth else "N/A",
        )

    # ── Step 3: 计算景气度复合评分 ──
    # 引擎: prosperity.calculate_prosperity_score
    #   输入: fin_data (FinancialData 列表)
    #   输出: ProsperityScore {
    #     composite_score = revenue*0.30 + profit*0.30 + slope*0.25 + duration*0.15
    #     delta_g = 近三季度营收增速均值的边际变化
    #   }
    logger.info("Step 3: 计算景气度复合评分...")
    prosp_score = calculate_prosperity_score(fin_data)
    logger.info(
        "景气度: composite={:.2f} revenue={:.2f} profit={:.2f} "
        "slope={:.2f} duration={:.2f} delta_g={:.2f}",
        prosp_score.composite_score,
        prosp_score.revenue_score,
        prosp_score.profit_score,
        prosp_score.slope_score,
        prosp_score.duration_score,
        prosp_score.delta_g,
    )

    # ── Step 4: 周期阶段分类 ──
    # 引擎: prosperity_sector.classify_stock_stage
    #   输入: ProsperityScore
    #   输出: "加速期" / "减速期" / "上升拐点" / "下降拐点"
    logger.info("Step 4: 周期阶段分类...")
    stage = classify_stock_stage(prosp_score)
    logger.info("周期阶段: {}", stage)

    # ── Step 5: 山峰理论 + 成长股投资时钟定位 ──
    logger.info("Step 5: 山峰理论 + 成长股投资时钟定位...")
    latest = fin_data[0]
    latest_growth = (latest.yoy_profit_growth or 0.0) * 100 if latest.yoy_profit_growth else 0.0
    mountain = _classify_mountain_position(latest_growth, prosp_score.delta_g)
    clock = _classify_clock_quadrant(stage)
    logger.info("山峰定位: {} | 时钟象限: {}", mountain["zone"], clock["quadrant"])

    # ── Step 6: 拐点分析 ──
    # 引擎: prosperity_inflection.analyze_inflection
    #   输入: ProsperityScore, stage, financial_data
    #   输出: InflectionAnalysis { inflection_quarter, catalysts, narrative }
    logger.info("Step 6: 拐点分析...")
    inflection = analyze_inflection(prosp_score, stage, fin_data)
    logger.info("拐点叙述: {}", inflection.narrative)

    # ── Step 7: 二次点火筛选 + 风险警告 ──
    # 引擎: prosperity_sector.screen_g_delta_g_ignition
    #   筛选条件: 增速高 + 相对 ΔG > 0 + 经营 CF > 0
    logger.info("Step 7: 二次点火筛选 + 风险警告...")
    stock_info = _build_stock_info(client, ts_code, stock_name)
    scores_map = {ts_code: prosp_score}
    infos_map = {ts_code: stock_info}
    fin_data_map = {ts_code: fin_data}

    ignition_set = screen_g_delta_g_ignition(scores_map, infos_map, fin_data_map)
    is_ignition = ts_code in ignition_set
    ignition_reasons = generate_ignition_reasons(prosp_score) if is_ignition else []
    risk_warnings = generate_risk_warnings(prosp_score, fin_data)

    logger.info(
        "二次点火: {} | 风险警告: {}",
        "是" if is_ignition else "否",
        risk_warnings if risk_warnings else "无",
    )

    # ── Step 8: (可选) 行业聚合对比 ──
    industry_result: dict | None = None
    if PEER_CODES:
        logger.info("Step 8: 行业聚合对比 ({} 个 peer)...", len(PEER_CODES))
        peer_scores: dict[str, ProsperityScore] = {}
        peer_infos: dict[str, StockInfo] = {}
        peer_fin_data: dict[str, list[FinancialData]] = {}

        peer_scores[ts_code] = prosp_score
        peer_infos[ts_code] = stock_info
        peer_fin_data[ts_code] = fin_data

        for peer_code in PEER_CODES:
            try:
                peer_fd = fetch_financial_data(client, peer_code, periods=PERIODS)
                if not peer_fd:
                    logger.warning("Peer {} 无财务数据，跳过", peer_code)
                    continue
                peer_score = calculate_prosperity_score(peer_fd)
                peer_info = _build_stock_info(client, peer_code, peer_code)
                peer_scores[peer_code] = peer_score
                peer_infos[peer_code] = peer_info
                peer_fin_data[peer_code] = peer_fd
            except Exception:
                logger.exception("Peer {} 评分失败，跳过", peer_code)

        # 计算相对 ΔG
        compute_relative_delta_g(peer_scores, peer_infos)

        # 行业聚合
        industry_scores = aggregate_industry_prosperity(peer_scores, peer_infos)
        if industry_scores:
            top_industry = industry_scores[0]
            industry_result = {
                "industry": top_industry.industry,
                "stock_count": top_industry.stock_count,
                "avg_composite_score": top_industry.avg_composite_score,
                "median_delta_g": top_industry.median_delta_g,
                "stage": classify_stock_stage(peer_scores[ts_code]),
            }
            logger.info(
                "行业聚合: {} ({} 只股票) 平均分={:.2f} 中位ΔG={:.2f}",
                industry_result["industry"],
                industry_result["stock_count"],
                industry_result["avg_composite_score"],
                industry_result["median_delta_g"],
            )

        relative_dg = peer_scores[ts_code].relative_delta_g
        logger.info(
            "相对 ΔG (vs 行业中位数): {:.2f} ({})",
            relative_dg,
            "行业内加速" if relative_dg > 0 else "行业内减速",
        )
    else:
        logger.info("Step 8: 跳过行业聚合 (未配置 PEER_CODES)")

    # ── Step 9: 组装最终 JSON 输出 ──
    result = {
        "ts_code": ts_code,
        "name": stock_name,
        "scored_at": datetime.now().isoformat(),
        "latest_report_period": latest.report_period,
        "financial_data_periods": len(fin_data),
        "prosperity": {
            "composite_score": prosp_score.composite_score,
            "revenue_score": prosp_score.revenue_score,
            "profit_score": prosp_score.profit_score,
            "slope_score": prosp_score.slope_score,
            "duration_score": prosp_score.duration_score,
            "delta_g": prosp_score.delta_g,
            "relative_delta_g": prosp_score.relative_delta_g,
            "weights": {
                "revenue": 0.30,
                "profit": 0.30,
                "slope": 0.25,
                "duration": 0.15,
            },
        },
        "cycle_classification": {
            "engine_stage": stage,
            "mountain_theory": mountain,
            "growth_clock": clock,
        },
        "inflection": {
            "inflection_quarter": inflection.inflection_quarter,
            "inflection_axis": inflection.inflection_axis,
            "primary_driver": inflection.primary_driver,
            "narrative": inflection.narrative,
            "catalysts": [
                {
                    "signal_type": c.signal_type,
                    "description": c.description,
                    "strength": c.strength,
                }
                for c in inflection.catalysts
            ],
        },
        "ignition": {
            "is_ignition": is_ignition,
            "reasons": ignition_reasons,
        },
        "risk_warnings": risk_warnings,
        "industry_context": industry_result,
    }

    return result


def main() -> None:
    """主入口：执行评分并输出 JSON.

    输出位置由 OUTPUT_DIR (CONFIG) 决定，文件名为 {TARGET_CODE}_prosperity_score.json。
    同时将完整 JSON 打印到 stdout。
    """
    result = score_prosperity()

    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    logger.info("✅ 评分完成，输出写入 {}", _OUTPUT_PATH)
    logger.info(
        "周期定位: stage={} mountain={} clock={} delta_g={:.2f} ignition={}",
        result["cycle_classification"]["engine_stage"],
        result["cycle_classification"]["mountain_theory"]["zone"],
        result["cycle_classification"]["growth_clock"]["quadrant"],
        result["prosperity"]["delta_g"],
        "是" if result["ignition"]["is_ignition"] else "否",
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
