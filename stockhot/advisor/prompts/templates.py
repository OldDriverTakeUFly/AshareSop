from stockhot.advisor.prompts.registry import (
    PromptTemplate,
    default_registry,
)

ANTI_HALLUCINATION = (
    "重要：你只能基于提供的数据回答，不能编造未提供的股票名、价格、指标数值。"
    "如果数据不足，请明确说明。"
)

BUILD_POSITION_V1 = PromptTemplate(
    name="build_position",
    version="v1",
    system=("你是A股投资助理，根据技术面和基本面数据提供建仓建议。" + ANTI_HALLUCINATION),
    user_template=(
        "股票代码: {code}\n"
        "当前价: {current_price}\n"
        "技术评分: {technical_score}\n"
        "技术状态: {technical_state}\n"
        "davis评分: {davis_score}\n"
        "百分位: {davis_percentile}\n"
        "支撑位: {support_levels}\n"
        "压力位: {resistance_levels}\n"
        "量比: {volume_ratio}\n\n"
        "请输出JSON:\n"
        '{"action": "buy", "confidence": "HIGH|MEDIUM|LOW", '
        '"entry_zone": [low, high], "stop_loss": float, '
        '"target": float, "reasoning": str}'
    ),
    expected_output_schema={
        "action": "buy",
        "confidence": "HIGH|MEDIUM|LOW",
        "entry_zone": [0, 0],
        "stop_loss": 0.0,
        "target": 0.0,
        "reasoning": "",
    },
)

ADJUST_POSITION_V1 = PromptTemplate(
    name="adjust_position",
    version="v1",
    system=("你是A股投资助理，根据持仓和信号变化提供调仓建议。" + ANTI_HALLUCINATION),
    user_template=(
        "股票代码: {code}\n"
        "当前价: {current_price}\n"
        "当前仓位: {position_pct}\n"
        "信号列表: {signals}\n"
        "成本: {avg_cost}\n"
        "浮盈/浮亏百分比: {unrealized_pnl_pct}\n\n"
        "请输出JSON:\n"
        '{"action": "trim|add", "trim_pct": float, "reasoning": str}'
    ),
    expected_output_schema={
        "action": "trim",
        "trim_pct": 0.0,
        "reasoning": "",
    },
)

CLEAR_POSITION_V1 = PromptTemplate(
    name="clear_position",
    version="v1",
    system=("你是A股投资助理，根据卖出信号提供清仓建议。" + ANTI_HALLUCINATION),
    user_template=(
        "股票代码: {code}\n"
        "触发的卖出信号: {triggered_signals}\n"
        "当前价: {current_price}\n"
        "硬止损: {stop_loss_hard}\n"
        "逻辑状态: {thesis_status}\n\n"
        "请输出JSON:\n"
        '{"action": "exit", "urgency": "HIGH|MEDIUM|LOW", "reasoning": str}'
    ),
    expected_output_schema={
        "action": "exit",
        "urgency": "HIGH",
        "reasoning": "",
    },
)

T_TRADE_V1 = PromptTemplate(
    name="t_trade",
    version="v1",
    system=(
        "你是A股投资助理，根据支撑压力位和量价关系提供粗略做T建议。"
        "注意：做T建议仅供参考，风险较高。" + ANTI_HALLUCINATION
    ),
    user_template=(
        "股票代码: {code}\n"
        "当前价: {current_price}\n"
        "支撑位: {support_levels}\n"
        "压力位: {resistance_levels}\n"
        "量比: {volume_ratio}\n"
        "近期量能趋势: {recent_volume_trend}\n\n"
        "请输出JSON:\n"
        '{"action": "swing_buy|swing_sell|hold", '
        '"intraday_buy_zone": [low, high], '
        '"intraday_sell_zone": [low, high], '
        '"confidence": "LOW", "disclaimer": str}'
    ),
    expected_output_schema={
        "action": "hold",
        "intraday_buy_zone": [0, 0],
        "intraday_sell_zone": [0, 0],
        "confidence": "LOW",
        "disclaimer": "",
    },
)

for _tpl in (
    BUILD_POSITION_V1,
    ADJUST_POSITION_V1,
    CLEAR_POSITION_V1,
    T_TRADE_V1,
):
    default_registry.register(_tpl)
