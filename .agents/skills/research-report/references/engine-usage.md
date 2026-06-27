# davis_analyzer 引擎调用指南

本文件是 `research-report` skill Phase 2（数据采集）的**实战调用手册**。davis_analyzer 的底层函数签名复杂、返回类型多样，直接从零拼调用会踩坑（字段名不符、返回空 list、参数类型错误）。**本指南里的每个签名和返回类型都经过实测验证**，照着写不会出错。

如果只是要快速跑一个标的的完整四维评分，**直接复制 `davis_analyzer/studies/tianyue_scoring.py` 改 `TS_CODE`**，不要从零拼。本指南适用于需要灵活取数的场景。

## 1. 前置：环境与 client

```python
import os
os.environ.setdefault("PROJECT_ROOT", ".")  # 防止 config.py import-time mkdir 报错
from davis_analyzer.tushare_client import TushareClient

client = TushareClient()  # 需要 TUSHARE_TOKEN 环境变量，否则 raise EnvironmentError
```

**坑点 1**：`TushareClient()` 在无 `TUSHARE_TOKEN` 时直接抛 `EnvironmentError`。研报场景下 token 应已配置；若缺失，标注"引擎数据不可用"，不要编造。

**坑点 1b（token 静默失效）**：如果环境变量里有一个**旧的/错误的 `TUSHARE_TOKEN`**（与 `.env` 文件里的不一致），`davis_analyzer/config.py` 的 `load_dotenv()` 默认**不覆盖**已存在的环境变量，导致引擎用了错误 token，报"您的token不对"。症状：直接用 tushare 库手动调能成功，但 davis_analyzer 引擎报 token 错误。修复：在脚本开头加 `load_dotenv('.env', override=True)` 强制用 .env 的正确 token 覆盖环境变量。

**坑点 2**：`stockhot/core/config.py` 在 import 时会 mkdir，依赖 `PROJECT_ROOT` 环境变量。如果脚本 import 了 stockhot 链路，先 `os.environ.setdefault("PROJECT_ROOT", os.getcwd())`。

**坑点 2b（代码张冠李戴）**：用错股票代码时引擎**不会报错**——`fetch_financial_data` 对任何有效 ts_code 都返回数据，即使它不是你要分析的公司。写春秋电子时曾误用 603096（正确应为 603890），取到了另一家公司的完整数据。**取数后务必用 `fin[0].ts_code` 或 `client.get_stock_list` 核对代码与公司名是否匹配**，尤其是凭记忆填代码时。可用 `client.get_stock_list()` 按名称模糊查代码。

## 2. 财务数据——fetch_financial_data

```python
from davis_analyzer.financial_fetcher import fetch_financial_data

fin_list = fetch_financial_data(client, "603690.SH", periods=12)
# 返回: list[FinancialData]，长度 ≤ periods（实际可用期数）
```

**返回类型是 `list[FinancialData]`，不是 dict、不是 DataFrame。** 这是最常见的坑。

### FinancialData 字段（dataclass，用属性访问，不是 `.get()`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `ts_code` | str | 股票代码 |
| `report_period` | str | 报告期，如 "20251231"（**注意：不是 end_date**） |
| `revenue` | float | 营收（元，非亿元） |
| `net_profit` | str/float | 归母净利润（**注意：不是 n_income**） |
| `eps` | float | 每股收益 |
| `roe` | float | ROE（%，如 -20.78 表示 -20.78%） |
| `operating_cf` | float | 经营现金流 |
| `total_debt` | float | 总负债 |
| `total_assets` | float | 总资产 |
| `yoy_revenue_growth` | float \| None | 营收同比（小数，如 -0.208 表示 -20.8%；**首期可能为 None**） |
| `yoy_profit_growth` | float \| None | 净利同比（小数；**首期可能为 None**） |

```python
# 正确访问方式
for item in fin_list[:4]:
    print(f"{item.report_period}: 营收={item.revenue}, 净利={item.net_profit}")
```

## 3. 估值数据——分两步取

### 3.1 取历史估值（fetch_valuation_history 或 get_daily_basic）

```python
# 方法 A：fetch_valuation_history（封装版）
from davis_analyzer.valuation import fetch_valuation_history
vh = fetch_valuation_history(client, "603690.SH")  # days 默认 PERCENTILE_DAYS=1095
# 返回: list[ValuationData]，可能为空 list（坑点 3）
```

**坑点 3**：`fetch_valuation_history` 对部分股票会返回**空 list `[]`**（增量 fetch 逻辑未命中）。遇到空 list 时改用方法 B：

```python
# 方法 B：直接调 get_daily_basic（更可靠）
from datetime import date, timedelta
import pandas as pd

end = date.today().strftime("%Y%m%d")
start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
db = client.get_daily_basic("603690.SH", start, end)
# 返回: pd.DataFrame，列名: ts_code, trade_date, pe_ttm, pb, ps, total_mv
```

### 3.2 算分位数（用 pandas，不要用 calculate_percentile）

```python
# ⚠️ get_daily_basic 返回 trade_date DESC（最新在前），必须先排序升序！
#    否则 iloc[-1] 取到最旧值，分位完全算反（坑点见 §8）
db = db.sort_values("trade_date")
pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna().sort_index()
pb = pd.to_numeric(db["pb"], errors="coerce").dropna().sort_index()
ps = pd.to_numeric(db["ps"], errors="coerce").dropna().sort_index()

# 当前分位（有多少比例的历史值低于当前值）
pe_pct = (pe < pe.iloc[-1]).sum() / len(pe) * 100  # 如 86.4
# 分位值表
for p in [10, 25, 50, 75, 90, 95]:
    print(f"PE {p}%分位: {pe.quantile(p/100):.2f}")
```

**坑点 4**：**亏损公司的 `pe_ttm` 列全是 `None`**（Tushare 对负 EPS 返回空）。这不是数据缺失，而是 PE 失效的信号——亏损标的必须改用 PB/PS，遵循 `valuation-loss-making-targets` skill。

**坑点 5**：`total_mv` 单位是**万元**，转亿元需 `/1e4`。

### 3.3 周期股判定（detect_cyclical）

```python
from davis_analyzer.valuation import detect_cyclical
# detect_cyclical(industry: str) → bool
# 注意：参数是行业名字符串，不是 ts_code！
is_cyc = detect_cyclical("半导体")  # ✓ 正确
# is_cyc = detect_cyclical("603690.SH")  # ✗ 错误！会返回 False（代码不在行业列表里）
```

**坑点 6**：`detect_cyclical` 参数是行业名，需先从 `StockInfo` 拿行业。若不确定行业，半导体/电子类通常不是周期股（返回 False），有色/化工/煤炭是周期股（返回 True，估值用 PB 而非 PE）。

## 4. 景气度——calculate_prosperity_score

```python
from davis_analyzer.prosperity import calculate_prosperity_score
pscore = calculate_prosperity_score(fin_list)
# 返回: ProsperityScore dataclass（需要 ≥4 个季度数据，否则结果不可靠）
```

### ProsperityScore 字段

| 字段 | 说明 |
|------|------|
| `revenue_score` | 营收分（0-100，权重 0.30） |
| `profit_score` | 利润分（0-100，权重 0.30） |
| `slope_score` | 趋势斜率分（0-100，权重 0.25） |
| `duration_score` | 持续时间分（0-100，权重 0.15） |
| `composite_score` | **复合分（0-100，核心指标）** |
| `delta_g` | **ΔG 边际增速变化（百分点）** |
| `relative_delta_g` | 相对行业的 ΔG（默认 0.0，需行业级计算才有意义） |

```python
print(f"景气度: {pscore.composite_score}, ΔG: {pscore.delta_g}")
print(f"营收分: {pscore.revenue_score}, 利润分: {pscore.profit_score}")
```

**坑点 7**：`calculate_delta_g(current_growth, previous_growth)` 需要**两个显式参数**，不能只传 `fin_list`。但 `calculate_prosperity_score` 内部已经算好了 `delta_g` 存在返回值里，**直接读 `pscore.delta_g` 即可**，不需要单独调 `calculate_delta_g`。

## 5. 困境反转——calculate_distress_score

```python
from davis_analyzer.distress import calculate_distress_score
# 12 个参数，从 fin_list + 估值分位手动组装
dscore = calculate_distress_score(
    eps_history=[f.eps for f in fin_list],
    pe_pct=pe_pct_value,       # 0-1 的分位数（不是百分比！）
    pb_pct=pb_pct_value,       # 0-1
    debt_ratio=...,            # total_debt / total_assets
    operating_cf=fin_list[0].operating_cf,
    total_debt=fin_list[0].total_debt,
    total_assets=fin_list[0].total_assets,
    roe_history=[f.roe for f in fin_list],
    revenue_history=[f.revenue for f in fin_list],
    profit_history=[f.net_profit for f in fin_list],
    delta_g=pscore.delta_g,
    ts_code="603690.SH",
)
# 返回: DistressSignal dataclass
```

**坑点 8**：`pe_pct`/`pb_pct` 参数期望 **0-1 的小数**（如 0.864），不是百分比（86.4）。传错会导致困境分失真。

**坑点 9**：这个函数参数多、组装复杂。**如果不需要困境分，可以跳过**。研报通常用景气度 + 估值就够了，困境分主要用于困境反转标的（遵循 `valuation-loss-making-targets` skill）。

## 6. 综合评分——calculate_davis_double_score

```python
from davis_analyzer.scoring import calculate_davis_double_score
final = calculate_davis_double_score(
    valuation_score=vscore,      # float
    prosperity_score=pscore,     # ProsperityScore（注意：传对象不是分数）
    distress_score=dscore,       # DistressSignal（注意：传对象）
    trend_score=tscore,          # float
    ts_code="603690.SH",
    name="至纯科技",
)
# 返回: DavisDoubleScore，核心字段 final_score + rank
```

**坑点 10**：`prosperity_score` 和 `distress_score` 参数传的是 **dataclass 对象**（ProsperityScore / DistressSignal），不是浮点分数。valuation_score 和 trend_score 才是 float。

## 7. 完整调用模板（单股四维评分）

以下是从财务取数到综合评分的**完整可运行模板**，复制改 `TS_CODE` 即可：

```python
import os
os.environ.setdefault("PROJECT_ROOT", os.getcwd())
from datetime import date, timedelta
import pandas as pd

from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.financial_fetcher import fetch_financial_data
from davis_analyzer.valuation import fetch_valuation_history, detect_cyclical
from davis_analyzer.prosperity import calculate_prosperity_score
from davis_analyzer.prosperity_sector import classify_stock_stage

TS_CODE = "603690.SH"  # 改这里
NAME = "至纯科技"       # 改这里

client = TushareClient()

# ── 1. 财务 ──
fin = fetch_financial_data(client, TS_CODE, periods=12)
print(f"财务: {len(fin)} 期, 最新 {fin[0].report_period}")

# ── 2. 估值（用 get_daily_basic 更可靠）──
end = date.today().strftime("%Y%m%d")
start = (date.today() - timedelta(days=1095)).strftime("%Y%m%d")
db = client.get_daily_basic(TS_CODE, start, end)
pe = pd.to_numeric(db["pe_ttm"], errors="coerce").dropna()
pb = pd.to_numeric(db["pb"], errors="coerce").dropna()
ps = pd.to_numeric(db["ps"], errors="coerce").dropna()
mv = pd.to_numeric(db["total_mv"], errors="coerce").dropna()
print(f"PE_TTM 有效点: {len(pe)}, PB: {pb.iloc[-1]:.2f} ({(pb<pb.iloc[-1]).sum()/len(pb)*100:.0f}%分位)")
print(f"PS: {ps.iloc[-1]:.2f} ({(ps<ps.iloc[-1]).sum()/len(ps)*100:.0f}%分位), 市值: {mv.iloc[-1]/1e4:.1f}亿")

# ── 3. 景气度 ──
pscore = calculate_prosperity_score(fin)
stage = classify_stock_stage(pscore)
print(f"景气度: composite={pscore.composite_score}, ΔG={pscore.delta_g}, 阶段={stage}")
```

> **注意**：这个模板刻意省略了 distress_score 和 davis_double_score——因为它们的参数组装复杂（坑点 8-10），且多数研报用景气度+估值就够。如需完整四维评分，参考 `davis_analyzer/studies/tianyue_scoring.py`（它展示了 distress + trend + davis 的完整调用链）。

## 8. 常见错误速查

| 报错 | 原因 | 解决 |
|------|------|------|
| `'list' object has no attribute 'columns'` | 把 `fin_list` 当 DataFrame 用 | 它是 `list[FinancialData]`，用属性访问 |
| `'FinancialData' object has no attribute 'get'` | 把 dataclass 当 dict 用 | 用 `item.revenue` 不是 `item.get('revenue')` |
| `'FinancialData' has no attribute 'end_date'` | 字段名记错 | 是 `report_period`，不是 `end_date` |
| `fetch_valuation_history` 返回 `[]` | 增量 fetch 未命中 | 改用 `client.get_daily_basic()` |
| `pe` 列全是 None | 公司亏损，PE 失效 | 改用 PB/PS（遵循 valuation-loss-making-targets skill） |
| `calculate_delta_g() missing argument` | 单独调 delta_g 缺参数 | 直接读 `pscore.delta_g`，不要单独调 |
| `detect_cyclical` 永远返回 False | 传了 ts_code 而非行业名 | 传行业名字符串，如 "半导体" |
| `total_mv` 数值异常大 | 单位是万元 | `/1e4` 转亿元 |
| 数据正确但公司不对（静默错误） | ts_code 用错（如 603096≠603890），引擎不报错 | 取数后核对 `fin[0].ts_code` 与预期公司名；用 `client.get_stock_list()` 按名查码 |
| 港股取不到数据（返回空/None） | Tushare **有**港股接口（`hk_basic`/`hk_daily`/`hk_income`），但受限：①`hk_basic`/`hk_daily` 低权限可用但**新股（如海光芯正 1191.HK，2026-06 IPO）上市后 1-3 个月才被收录**；②`hk_income`/`hk_balancesheet`/`hk_cashflow` **需 5000 积分**（普通账户无权限）。davis_analyzer 的 `fetch_financial_data`/`get_daily_basic` 只封装了 A 股端点 | 港股标的先用 `pro.hk_basic` + `pro.hk_daily` 探测是否已收录；未收录或财务无权限时，改用招股书 + 手动 PS 估值，遵循 `valuation-loss-making-targets` skill。code 格式用 `01191.HK`（补零至 5 位） |
| `TushareClient` 报"您的token不对"但 `get_pro_api()` 能用 | **shell 环境导出了 STALE token**（旧 api.waditu.com 的 `76f191c0`），`load_dotenv()` 默认不覆盖已存在的环境变量，导致引擎读到旧 token | 脚本里用 `load_dotenv(".env", override=True)` 强制 .env 的新 token 生效；或在 `TushareClient()` 前 `os.environ["TUSHARE_TOKEN"]=...` 显式覆盖 |
| **分位值算反（高分位写成低分位）** | `get_daily_basic` 返回 `ORDER BY trade_date DESC`（**最新在前**）。若不排序直接 `pe.iloc[-1]`，取到的是**最旧（3 年前）**的值当"当前值"，导致分位完全算反（如华泰 PE 实际 75% 分位被算成 8.3%） | **取数后必须 `db = db.sort_values("trade_date")` 升序排序**，再 `pe = pe.sort_index()`，确保 `iloc[-1]` 是最新交易日。§3.2 示例已隐含假设升序，但 `get_daily_basic` 默认降序——务必显式排序后再算分位 |
| `stockhot.core.config` 报 `FileNotFoundError: /app/data` | `.env` 里有 Docker 配置 `PROJECT_ROOT=/app`，`load_dotenv(override=True)` 后覆盖了本地真实路径，触发 stockhot 在 import 时 mkdir `/app/data` | **必须在 `load_dotenv` 之后重新 `os.environ["PROJECT_ROOT"]=<本地项目根>`**，顺序：先 load_dotenv（拿新 token）→ 再 pin PROJECT_ROOT（修路径）→ 再 import stockhot |

## 9. dataclass 字段速查表

完整字段定义见 `davis_analyzer/types.py`，最常用的：

| Dataclass | 关键字段 |
|-----------|----------|
| `FinancialData` | ts_code, report_period, revenue, net_profit, eps, roe, operating_cf, total_debt, total_assets, yoy_revenue_growth, yoy_profit_growth |
| `ProsperityScore` | composite_score, delta_g, revenue_score, profit_score, slope_score, duration_score, relative_delta_g |
| `ValuationData` | ts_code, trade_date, pe_ttm, pb, ps, total_mv |
| `DistressSignal` | total_score, layer1_score, layer2_score, layer3_score, signals_detail |
| `DavisDoubleScore` | final_score, rank, valuation_score, prosperity_score, distress_score, trend_score |

## 10. 数据时效性校验（写报告前必做）

**坑点 11（时效性盲区）**：报告写「研究日期 6/27」但财务数据其实是 Q1（4 月底披露），若不标注，读者会误以为是当周数据。半年报 8 月底、年报 4 月底才披露——中途财务快照只能到上一季报，这是结构性上限，不是缺陷，但**必须诚实标注时效边界**。此外，`forecast`（业绩预告）常先于正式财报披露（如 1 月底预告全年首亏、4 月预告 Q1），是更早的领先信号，不能漏。

**写报告前先跑这个校验**，对每个标的查三项新鲜度。实例脚本：`davis_analyzer/studies/wf6_freshness_check.py`。

```python
import os
from dotenv import load_dotenv
load_dotenv(".env", override=True)   # override=True: 防 shell 导出 stale token
os.environ["PROJECT_ROOT"] = os.getcwd()  # 防 .env 的 /app 值破坏 stockhot mkdir

from stockhot.tushare_config import get_pro_api

pro = get_pro_api(timeout=30)

def freshness_check(ts_code, report_date):
    """对单只标的查三项新鲜度，返回供报告标注的时效信息。"""
    # 1. 估值数据最新交易日（daily_basic）
    db = pro.daily_basic(ts_code=ts_code, limit=1)
    latest_trade = db.iloc[0]["trade_date"] if len(db) else "none"

    # 2. 财务最新报告期 + 披露日（income）
    inc = pro.income(ts_code=ts_code,
                     fields="ts_code,ann_date,end_date,f_ann_date", limit=1)
    latest_period = inc.iloc[0]["end_date"] if len(inc) else "none"
    latest_ann = inc.iloc[0]["ann_date"] if len(inc) else "none"

    # 3. 业绩预告（forecast，领先信号）
    fc = pro.forecast(ts_code=ts_code,
                      fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
    forecast_info = ""
    if len(fc):
        r = fc.iloc[0]
        forecast_info = (f"{r['type']} ann={r['ann_date']} end={r['end_date']} "
                         f"同比=[{r['p_change_min']}, {r['p_change_max']}]%")

    return {
        "latest_trade_date": latest_trade,
        "latest_report_period": latest_period,
        "latest_ann_date": latest_ann,
        "latest_forecast": forecast_info,
    }

# 用法：报告里据此标注
# 「财务截至 2026Q1（20260421 披露），2026 半年报预计 8 月底披露」
# 「估值快照 20260626（最新交易日）」
# 「业绩预告：2025 全年首亏 -299%~-239%（20260131 披露）」
```

**时效边界标注规范**（写进报告元数据块或财务章开头）：
- 财务：「财务快照截至 **YYYYQN（YYYYMMDD 披露）**，下季报 X 月披露」
- 估值：「估值快照 YYYYMMDD（最新交易日）」
- 预告：「业绩预告：YYYY 全年/QN {类型} {同比区间}（YYYYMMDD 披露）」
- 过时预告不参考（如 2 年前的预告），只取最近一次覆盖目标期的预告

**Source of Truth**：本指南与引擎实现可能随版本演进产生偏差。若调用结果与预期不符，以 `davis_analyzer/types.py`（字段定义）和各模块源码签名为准。
