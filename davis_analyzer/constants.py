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

# ── Super-cycle industries (结构性成长/超级周期) ──
# These sectors ride multi-year structural demand waves (AI hardware,
# semiconductor capex, cloud infrastructure) rather than commodity price
# mean-reversion.  Their ΔG should be *preserved* (not clamped) because
# persistent acceleration signals genuine structural growth, not cyclical noise.
SUPER_CYCLE_INDUSTRIES: list[str] = [
    "通信设备",    # 光模块（中际旭创/新易盛/天孚通信/长飞光纤）
    "元器件",      # PCB/覆铜板（沪电股份/生益科技/胜宏科技）
    "半导体",      # 设计/制造/存储（兆易创新/北京君正/北方华创）
    "IT设备",      # AI服务器/算力硬件
    "软件服务",    # EDA/半导体软件
    "专用机械",    # 半导体设备（北方华创/拓荆/微导）
]

# ── Classic-cyclical factor-blend weights ──
# Classical cyclicals (steel/coal/chemicals) have mean-reverting ΔG: a +50pp
# spike usually reverses within 1-2 quarters.  We clamp ΔG to suppress this
# amplitude and tilt the blend toward valuation (PB is the reliable anchor)
# away from prosperity (ΔG is unreliable).
CYCLICAL_DELTA_G_CLAMP: float = 25.0  # cap |ΔG| at ±25 percentage points

CYCLICAL_FACTOR_WEIGHTS: dict[str, float] = {
    "momentum": 0.20,
    "valuation": 0.40,
    "prosperity": 0.15,
    "distress": 0.25,
}

# ── Super-cycle persistence bonus ──
# When a super-cycle stock shows ΔG > 0 for >= N consecutive quarters, the
# trend is structural (not a one-off price spike).  We add a bounded bonus to
# reward this persistence, separate from the raw ΔG value.
SUPER_CYCLE_PERSISTENCE_BONUS: float = 10.0  # max bonus points
SUPER_CYCLE_MIN_POSITIVE_QUARTERS: int = 4   # consecutive ΔG>0 quarters needed

# ── Super-cycle early-detection thresholds (V2 tightened) ──
# Pattern A: high G requires momentum confirmation to avoid false positives
# (financial data looks great but stock goes nowhere = unsustainable/fraud).
SUPER_CYCLE_HIGH_G_THRESHOLD: float = 50.0       # G > this = high growth
SUPER_CYCLE_HIGH_G_MOMENTUM: float = 60.0        # ...requires momentum > this
SUPER_CYCLE_MID_G_LOW: float = 20.0              # mid-G band lower bound
SUPER_CYCLE_MID_G_DG_REQUIRED: float = 15.0      # mid-G requires ΔG > this
SUPER_CYCLE_NOISE_G_CAP: float = 500.0           # G > this = low-base noise, excluded
SUPER_CYCLE_SUSPECT_G_CAP: float = 200.0         # G in [200,500] = suspect, capped at emerging

# ── Financial thresholds ──
EPS_NEAR_ZERO_THRESHOLD: float = 0.01

# ── API limits ──
TUSHARE_RATE_LIMIT: int = 400  # headroom below Tushare's 500/min hard cap

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

# ── Forecast (业绩预告) leading-indicator scoring ──
# Forecast YoY net-profit change midpoints (p_change_min+p_change_max)/2 are
# mapped to a 0–100 leading-indicator score, mirroring the profit-growth
# bands so the forward signal is comparable to the realised one.
FORECAST_HIGH_THRESHOLD: float = 50.0  # >50% midpoint → 80-100
FORECAST_MID_THRESHOLD: float = 20.0  # 20-50% → 50-80
FORECAST_LOW_THRESHOLD: float = 0.0  # 0-20% → 25-50, <0 → 0-20

# Distance (in calendar days) from the forecast announcement to today, beyond
# which a forecast is considered stale. A forecast covers a fixed report
# period and is superseded by newer pre-announcements.
FORECAST_STALE_DAYS: int = 400

# ── Holder-concentration (筹码集中度) scoring ──
HOLDER_LOOKBACK_PERIODS: int = 4  # reporting periods scored over
# Fractional holder-count decline over the look-back window needed for a
# full (100) concentration score.
HOLDER_CONCENTRATION_FULL_DECLINE: float = 0.15  # -15% holders → 100

# ── Price-momentum / relative-strength (RS) scoring ──
# Look-back windows (calendar days) for absolute momentum. Multi-window blend
# is the canonical momentum construction (Jegadeesh-Titman style); 250d ≈ 1y.
MOMENTUM_WINDOWS_DAYS: tuple[int, ...] = (60, 120, 250)
# Weight of each window in the blended momentum score (must sum to 1.0).
MOMENTUM_WINDOW_WEIGHTS: tuple[float, ...] = (0.2, 0.3, 0.5)
# Minimum number of adjusted trading days required for a window to score.
MOMENTUM_MIN_PRICES: int = 40
# Per-window raw-return saturation points (%, positive) at which the
# absolute-momentum sub-score saturates at 100. We score RAW window returns
# (not annualised) because annualising short-window returns explodes (38% in
# 60d ≠ 500%/yr). Shorter windows get smaller saturation points since typical
# A-share volatility scales with horizon.
MOMENTUM_FULL_RETURN_PCT_BY_WINDOW: dict[int, float] = {
    60: 30.0,   # +30% in 3 months → 100
    120: 50.0,  # +50% in 6 months → 100
    250: 100.0, # +100% in 1 year → 100
}

# ── Dividend (红利) scoring ──
DIVIDEND_LOOKBACK_YEARS: int = 3  # consecutive-year payout history scored
# Indicative annual yield (%) above which the yield sub-score saturates at 100.
DIVIDEND_FULL_YIELD_PCT: float = 5.0

# ── Forecast-revision (一致预期修正) scoring ──
# Minimum calendar days between two forecasts for the same report period to
# count as a genuine revision rather than the same announcement re-keyed.
FORECAST_REVISION_MIN_GAP_DAYS: int = 30

# ── Forward overlay (前景估值调整) ──
# Forward-looking signals (cycle stage / earnings pre-announcement / ΔG /
# secondary ignition) adjust the backward-looking PE/PB percentile by a
# *bounded* amount. Asymmetric: downside room (-20) exceeds upside (+15)
# because the two growth forces (forward-E relief vs growth premium) cancel
# on the upside but reinforce on the downside. See valuation_forward.py.
FORWARD_OVERLAY_MAX: float = 15.0  # 上调上限
FORWARD_OVERLAY_MIN: float = -20.0  # 下调下限

# Profit-growth red line: when net-profit growth falls below this, upside
# adjustments are killed (min(overlay, 0)) because excess returns decay. The
# downside channel stays open so value-trap detection is preserved.
FORWARD_PROFIT_GROWTH_THRESHOLD: float = 30.0

# Minimum quarters of data for a reliable ΔG. Below this the overlay is zeroed
# (no adjustment) and flagged "ΔG 不可靠".
FORWARD_DELTA_G_MIN_QUARTERS: int = 2

# Price-in risk: an accelerating stock already at a high PE percentile likely
# has the growth priced in, so the upside cap is halved.
FORWARD_PRICEIN_PE_PERCENTILE: float = 0.80
FORWARD_PRICEIN_HALF_CAP: float = 7.0  # halved upside cap

# PS-PE divergence flag threshold (percentage points).
PS_DIVERGENCE_THRESHOLD_PP: float = 20.0

# Forecast leading-score bands for the forward overlay confirmation signal.
FORWARD_FORECAST_LEADING_HIGH: float = 50.0
FORWARD_FORECAST_LEADING_LOW: float = 30.0

# ── Forward overlay rule-table values (all hardcoded) ──
# Sub-signal 1: cycle-stage base adjustment (range −15 … +8).
BASE_ACCELERATING_AHEAD: float = 8.0      # 加速期 + relative_delta_g > 0
BASE_ACCELERATING_DECEL: float = -6.0     # 加速期 + relative_delta_g ≤ 0 (高位减速)
BASE_TURNING_UP: float = 7.0              # 上升拐点 + relative_delta_g > 0
BASE_TURNING_UNCONFIRMED: float = -4.0    # 上升拐点 + relative_delta_g ≤ 0
BASE_DECELERATING: float = -12.0          # 减速期
BASE_DECLINING: float = -15.0             # 下降拐点

# Sub-signal 2: forecast confirmation (stackable with revision).
FCST_RESONANCE: float = 7.0    # leading_score > HIGH 且 delta_g > 0 (前后向共振)
FCST_WEAK_CONFIRM: float = 3.0  # leading_score in (LOW, HIGH]
FCST_WEAKENING: float = -3.0   # leading_score ≤ LOW
REV_DOWNGRADE: float = -8.0    # revision_score == 0 (管理层下调,叠加)
REV_UPGRADE: float = 4.0       # revision_score == 100 (管理层上调,叠加)

# Sub-signal 3: secondary-ignition bonus.
IGNITION_BONUS: float = 3.0
