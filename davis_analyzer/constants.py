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
