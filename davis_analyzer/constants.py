"""Scoring weights, thresholds, and configuration constants for davis_analyzer."""

# ── 景气度 (Prosperity) scoring weights ──
PROSPERITY_WEIGHTS: dict[str, float] = {
    "revenue": 0.30,
    "profit": 0.30,
    "slope": 0.25,
    "duration": 0.15,
}

# ── Davis Double final scoring weights ──
DAVIS_DOUBLE_WEIGHTS: dict[str, float] = {
    "valuation": 0.30,
    "trend": 0.15,
    "prosperity": 0.30,
    "distress": 0.25,
}

# ── Valuation percentile look-back ──
PERCENTILE_DAYS: int = 1095  # 3 years of calendar days

# ── PE/PB trend calculation ──
PE_PB_TREND_MONTHS: int = 36  # monthly data points for trend (3 years)
MIN_TREND_MONTHS: int = 12  # minimum monthly points for valid trend

# ── Valuation thresholds (percentile ranks) ──
UNDERVALUED_PE_THRESHOLD: float = 0.20  # 20th percentile
UNDERVALUED_PB_THRESHOLD: float = 0.30  # 30th percentile

# ── Cyclical industries (戴维斯双击 methodology) ──
CYCLICAL_INDUSTRIES: list[str] = [
    "钢铁",
    "有色金属",
    "煤炭",
    "石油石化",
    "化工",
    "建材",
    "造纸",
]

# ── Financial thresholds ──
EPS_NEAR_ZERO_THRESHOLD: float = 0.01

# ── API limits ──
TUSHARE_RATE_LIMIT: int = 500  # upgraded Tushare API tier

# ── Report generation ──
REPORT_MAX_WORDS: int = 1500

# ── Stock name patterns to exclude ──
EXCLUSION_PATTERNS: list[str] = ["ST", "*ST"]

# ── Prosperity sector thresholds ──
SECTOR_MIN_STOCKS: int = 5
PE_PERCENTILE_HIGH: float = 0.80
PB_PERCENTILE_HIGH: float = 0.80

# ── A1: Profit growth scoring thresholds (利润波动更大，门槛高于营收) ──
PROFIT_GROWTH_HIGH_THRESHOLD: float = 50.0  # >50% YoY → 80-100分
PROFIT_GROWTH_MID_THRESHOLD: float = 20.0  # 20-50% → 50-80分
PROFIT_GROWTH_LOW_THRESHOLD: float = 0.0  # 0-20% → 25-50分

# ── A2: Duration score magnitude bonus ──
DURATION_BONUS_MAX: float = 25.0  # bonus 上限
DURATION_BONUS_GROWTH_FACTOR: float = 0.5  # avg_growth × 此因子

# ── B2: Stage classification transition zone ──
HIGH_GROWTH_CONFIRMED_THRESHOLD: float = 85.0  # >85 确定高增长
HIGH_GROWTH_LOWER_BOUND: float = 75.0  # <75 确定非高增长
TRANSITION_DELTA_G_POSITIVE: float = 5.0  # 过渡区内 delta_g>5 偏向加速
TRANSITION_DELTA_G_NEGATIVE: float = -5.0  # 过渡区内 delta_g<-5 偏向减速

# ── D2: Risk warning thresholds (从 prosperity_sector.py 移入) ──
RISK_REVENUE_SCORE_LOW: float = 35.0  # 营收评分低于此值 → "增速不足"
RISK_SLOPE_SCORE_LOW: float = 40.0  # 趋势评分低于此值 → "趋势下行"
RISK_DURATION_SCORE_LOW: float = 25.0  # 持续评分低于此值 → "景气持续性存疑"

# ── D1/D3: Scoring magic-number constants ──
SCORING_DECAY_FACTOR: float = 0.8  # exponential decay weight for recent quarters
SLOPE_SIGMOID_K: float = 2.0  # sigmoid steepness in calculate_slope_score
DURATION_BASE_PER_QUARTER: float = 25.0  # base duration score per consecutive positive quarter

# ── Sector aggregation / ignition ──
INDUSTRY_TOP_STOCK_COUNT: int = 10  # top-N stocks per industry in aggregate
IGNITION_SLOPE_THRESHOLD: float = 60.0  # slope_score above this → "上行趋势确认"

# ── Inflection catalyst strength values ──
INFLECTION_CF_STRENGTH: float = 80.0  # operating cash-flow positive catalyst
INFLECTION_DEBT_STRENGTH: float = 70.0  # debt ratio declining catalyst
INFLECTION_REVENUE_STRENGTH: float = 60.0  # revenue stabilising catalyst

# ── Inflection risk-factor strength values ──
INFLECTION_RISK_ROE_STRENGTH: float = 60.0  # ROE declining risk
INFLECTION_RISK_CF_STRENGTH: float = 50.0  # cash-flow negative risk
INFLECTION_RISK_DEBT_STRENGTH: float = 55.0  # debt ratio rising risk
INFLECTION_RISK_GROWTH_STRENGTH: float = 65.0  # delta_g negative risk
