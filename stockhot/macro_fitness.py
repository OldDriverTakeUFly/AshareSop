"""宏观适配度打分 —— 把宏观景气度映射到行业偏好.

不改 composite score（不破坏现有回测），只在选股输出里标注
每只股票在当前宏观环境下的适配度，供决策参考。

映射逻辑：
  PMI < 50（制造业收缩）→ 科技/消费/医药适配，顺周期不适配
  PMI > 50（制造业扩张）→ 顺周期适配，防御型相对不适配
  M1-M2 剪刀差 < 0（资金空转）→ 整体偏谨慎

被调用方：
  - screen_top20.py（选股输出加标注）
  - premarket_strategy.py（盘前策略表标注持仓适配度）
"""

from __future__ import annotations

from loguru import logger

# 行业 → 宏观敏感度分类
# 顺周期：PMI > 50 时受益，PMI < 50 时承压
_CYCLICAL = {"钢铁", "有色金属", "煤炭", "基础化工", "建筑材料", "建筑装饰",
             "机械设备", "石油石化"}

# 反周期/防御：PMI < 50 时相对受益（资金避险）
_DEFENSIVE = {"公用事业", "银行", "交通运输", "农林牧渔"}

# 独立周期：受 PMI 影响小（有自己的产业周期）
_INDEPENDENT = {"电子", "计算机", "通信", "传媒", "医药生物", "电力设备",
                "国防军工", "汽车", "家用电器", "食品饮料", "商贸零售",
                "美容护理", "纺织服饰", "轻工制造", "社会服务", "综合",
                "环保", "非银金融", "房地产"}


def get_macro_fitness(industry: str, pmi: float | None = None,
                      m1_m2_gap: float | None = None) -> dict:
    """计算单个行业的宏观适配度.

    参数：
        industry: 行业名（申万一级口径，如"电子"/"钢铁"）
        pmi: 当前 PMI 值（None 时从 DB 读）
        m1_m2_gap: M1-M2 剪刀差（None 时从 DB 读）

    返回：
        {"label": "宏观适配"/"中性"/"宏观逆风", "reason": "...", "score": 0-100}
    """
    # 从 DB 读宏观值（如果没传入）
    if pmi is None or m1_m2_gap is None:
        pmi, m1_m2_gap = _load_macro_from_db()

    # 判定行业类型
    if industry in _CYCLICAL:
        sector_type = "顺周期"
    elif industry in _DEFENSIVE:
        sector_type = "防御型"
    else:
        sector_type = "独立周期"

    # 判定宏观状态
    pmi_expansionary = pmi is not None and pmi >= 50
    pmi_below_48 = pmi is not None and pmi < 48

    # 适配度逻辑
    reasons = []
    if sector_type == "顺周期":
        if pmi_expansionary:
            label = "宏观适配"
            score = 80
            reasons.append(f"PMI {pmi:.1f}≥50 制造业扩张，顺周期行业受益")
        elif pmi_below_48:
            label = "宏观逆风"
            score = 30
            reasons.append(f"PMI {pmi:.1f}<48 制造业深度收缩，顺周期承压")
        else:
            label = "中性"
            score = 50
            reasons.append(f"PMI {pmi:.1f} 接近荣枯线，顺周期观望")
    elif sector_type == "防御型":
        if pmi_below_48:
            label = "宏观适配"
            score = 75
            reasons.append(f"PMI {pmi:.1f}<48 制造业收缩，资金避险利好防御型")
        elif pmi_expansionary:
            label = "中性"
            score = 45
            reasons.append(f"PMI {pmi:.1f}≥50 扩张期，防御型吸引力下降")
        else:
            label = "中性"
            score = 55
    else:  # 独立周期
        label = "中性"
        score = 60
        if pmi_below_48:
            reasons.append(f"PMI {pmi:.1f} 偏弱，但该行业有独立产业周期")
        else:
            reasons.append("该行业主要受产业周期驱动，宏观敏感度低")

    # M1-M2 剪刀差修正
    if m1_m2_gap is not None and m1_m2_gap < -3:
        score = max(20, score - 10)
        reasons.append(f"M1-M2剪刀差{m1_m2_gap:.1f}pp 资金活化度低，整体偏谨慎")

    return {
        "label": label,
        "score": score,
        "sector_type": sector_type,
        "reason": " | ".join(reasons),
        "pmi": pmi,
    }


def _load_macro_from_db() -> tuple[float | None, float | None]:
    """从 macro_indicator 表读最新 PMI 和 M1-M2 剪刀差."""
    try:
        import sqlite3
        from stockhot.data_layer import MARKET_DB_PATH

        with sqlite3.connect(str(MARKET_DB_PATH)) as conn:
            row = conn.execute(
                "SELECT value FROM macro_indicator "
                "WHERE indicator_name='PMI' ORDER BY report_date DESC LIMIT 1"
            ).fetchone()
            pmi = float(row[0]) if row else None

            m1_row = conn.execute(
                "SELECT value FROM macro_indicator "
                "WHERE indicator_name='M1_YoY' ORDER BY report_date DESC LIMIT 1"
            ).fetchone()
            m2_row = conn.execute(
                "SELECT value FROM macro_indicator "
                "WHERE indicator_name='M2_YoY' ORDER BY report_date DESC LIMIT 1"
            ).fetchone()

            if m1_row and m2_row:
                m1_m2_gap = float(m1_row[0]) - float(m2_row[0])
            else:
                m1_m2_gap = None

        return pmi, m1_m2_gap
    except Exception as e:
        logger.warning(f"macro_fitness: DB读取失败: {e}")
        return None, None


def format_macro_fitness(industry: str) -> str:
    """格式化宏观适配度为简短标注（用于报告）.

    返回如："✅宏观适配" / "⚪中性" / "⚠️宏观逆风"
    """
    fitness = get_macro_fitness(industry)
    emoji = {"宏观适配": "✅", "中性": "⚪", "宏观逆风": "⚠️"}.get(fitness["label"], "⚪")
    return f"{emoji}{fitness['label']}({fitness['sector_type']})"
