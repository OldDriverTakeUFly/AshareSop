# davis_analyzer 引擎调用指南

本文件是 `research-report` skill Phase 2（数据采集）的**实战调用手册**。davis_analyzer 的底层函数签名复杂、返回类型多样，直接从零拼调用会踩坑（字段名不符、返回空 list、参数类型错误）。**本指南里的每个签名和返回类型都经过实测验证**，照着写不会出错。

如果只是要快速跑一个标的的完整四维评分，**直接复制 `davis_analyzer/studies/tianyue_scoring.py` 改 `TS_CODE`**，不要从零拼。本指南适用于需要灵活取数的场景。

> **数据源规范（2026-07-07）**：所有数据以 Tushare 为第一数据源，AKShare 为 fallback。研报场景调 Tushare 底层接口时，优先用 `stockhot.core.tushare_client_safe.safe_tushare_call`（限频+重试+空检查），避免裸调 `pro.xxx()`。详见 `.agents/skills/data-source-convention.md`。davis_analyzer 的 `TushareClient` 已封装新端点，可直接用。

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
| **分段批量拉取 daily_basic 后"当前值"取到旧行、全历史 vs 3y 分位互相矛盾** | 分段循环调用 `pro.daily_basic` 后 `pd.concat` 的各分段 index 均从 0 开始（重复 index），`sort_values("trade_date")` 后再 `sort_index()` 会按重复的整数 index 重排，`iloc[-1]` 取到随机旧行 | concat 后必须 `.reset_index(drop=True)` 再做后续排序/分位；或直接用 `df.iloc[-1]` 前打印 `df["trade_date"].iloc[-1]` 校验是否最新交易日。猪企三篇研报（2026-08）首次踩到此坑 |
| `TushareClient` 之外的裸 `pro.daily_basic` 长区间只返回部分数据 | 新端点单次调用对长区间（>500 天）可能截断 | 按 ≤500 天分段循环拉取+`drop_duplicates("trade_date")` 合并（见 pig3_scoring.py §4 实现） |
| `fetch_valuation_history` 只返回 ~22 天（3 年窗口缩水成 1 个月） | SQLite 缓存增量 fetch 未命中该股票的 3 年历史，只取到近期增量。症状：`len(val_history)` 仅 20+ 而非 ~730，分位/趋势/估值分全部失真（中国稀土 000831 首轮估值分 18.18 → 728 天重算 33.13） | **务必检查 `len(val_history)`**；不足时按 ≤120 天分段循环 `pro.daily_basic` 拉全量，手工构造 `ValuationData` 列表后调 `calculate_valuation_score`，并用 `batch_trend` 重算趋势分（注意 concat 后 `reset_index(drop=True)` + 校验末行 trade_date） |
| **`client.get_daily_basic` 同样只返回 ~22 天**（同根因：SQLite 增量 fetch 未命中） | 与上条同根因但接口不同——温氏 300498/新希望 000876 用 `client.get_daily_basic` 取 3 年数据只返回 22 交易日，PB 分位显示 36.4%（假象），直连 727 交易日重算实为 3.9%（温氏猪企研报 2026-08 首次踩到） | **凡用 `client.get_daily_basic`（davis 封装）算分位，必须校验返回行数 ≥700**；不足则改用 `stockhot.tushare_config.get_pro_api()` 的 `pro.daily_basic` 分段直连（≤500 天/段），concat 后 `reset_index(drop=True)` |
| `analyze_momentum` 的 `window_returns` 出现不可能值（如 60d +1062%） | 动量引擎依赖的日价缓存序列存在缺口/错位，窗口收益按错位价格计算（中国稀土 000831 首次踩到） | 用 `pro.daily(ts_code, start_date, end_date)` 手工复核 20/60/120/250d 收益（部分环境 `pro.pro_bar` 报"请指定正确的接口名"），报告引用复核值并披露引擎字段异常；派息接近零的标的未复权误差可忽略 |
| `analyze_holder_concentration` 返回对象的字段名记错（如 `hc.score`/`hc.holder_score` 报 AttributeError） | `HolderConcentration` dataclass 的实际字段是 `concentration_score`（0-100 分）、`trend`（"集中(动能增强)"/"分散(动能减弱)"/"数据不足"）、`latest_chg_pct`、`holder_counts`、`periods`（光伏三巨头研报 2026-08 首次踩到） | 读 `hc.concentration_score` 与 `hc.trend`；字段定义见 `davis_analyzer/types.py` 的 HolderConcentration |
| `pro.stk_holdernumber` 直连后 `int(r["holder_num"])` 报 `cannot convert float NaN to integer` | 新端点会返回 `end_date == ann_date` 且 `holder_num` 为 NaN 的垃圾行（疑似快照占位），最早的一条 NaN 行会炸掉整个循环（通威/隆基研报 2026-08 首次踩到） | 取数后先 `.dropna(subset=["holder_num"])` 再排序取尾；或逐行 `pd.isna()` 跳过 |
| 手工构造 `ValuationData` 列表调 `calculate_valuation_score` 报 `TypeError: '<' not supported between instances of 'NoneType' and 'float'` | 亏损股 daily_basic 的 `pe_ttm` 为 None，`handle_negative_eps(pe_series)` 对 None 直接比较崩溃——引擎自身的 `fetch_valuation_history` 会**过滤 NaN PE 行**（且按日期降序、latest 在首位），手工构造若不过滤就复现不了这个行为 | 构造时跳过 `pd.isna(pe_ttm)` 或 `pd.isna(pb)` 的行，并 `sort(key=trade_date, reverse=True)`；**注意过滤后 "latest PE" 锚定的是最后一个有效 PE 交易日（如通威/隆基为 2024-08-30），报告须标注 PE 分位失真，PB 分位改用全序列自算** |
| `analyze_relative_valuation` 对亏损股返回全空（pe_ratio/erp/quadrant 均 None，signals 空） | 个股 PE_TTM 为 None（亏损）时，三法中依赖 PE 的两项直接失效——这不是 bug 而是周期股 PE 陷阱的接口级体现 | 报告中如实标注"PE 法失效"，改用 **PB 相对锚定**（个股 PB 分位 vs 板块/同业分位）+ PS 锚定替代，并引用返回对象中仍有效的 `index_pe`/`index_pe_pct`（市场基准）与 `risk_free_rate`（光伏三巨头研报 2026-08 的处理方式） |
| `ProfitabilityQuality` 对象报 `AttributeError: 'ProfitabilityQuality' object has no attribute 'rd_intensity'` | 字段实际名是 `latest_rd_intensity`（types 之外，定义在 `profitability.py` 的 dataclass 中），另两个可用字段为 `latest_gross_margin`/`gross_margin_delta`——火电等公用事业股该因子常因 fina_indicator 毛利率缺失返回 quality_score=50.0 默认值+None 字段（华能/浙能研报 2026-08 首次踩到） | 读 `pq.latest_rd_intensity`；毛利率为 None 时在报告中诚实标注"数据不足"，用年报口径人工估算替代并标注"估算" |
| `analyze_relative_valuation` 的分位字段量纲混乱：pe_ratio_pct/stock_pe_pct/index_pe_pct 已是百分数，erp/risk_free_rate 是小数 | 同一 dataclass 里两套量纲（pe_ratio_pct=2.2 表示 2.2%，erp=0.028 表示 2.8%）——误把分位字段当小数乘 100 会显示 220% 的虚高值（水泥双标的研报 2026-08 首次踩到） | 分位字段直接当百分数引用；erp/risk_free_rate 乘 100 转百分数；使用前打印一次核对 |
| `detect_cyclical("水泥")` 返回 False（水泥未入周期行业表） | `constants.py` 的 CYCLICAL_INDUSTRIES 收录"钢铁/有色/煤炭/石油石化/化工/建材/造纸"，但 Tushare stock_basic 对水泥股的 industry 字段是"水泥"而非"建材"——`calculate_valuation_score` 按非周期权重（PE70%+PB30%）而非 PB-only 合成估值分（水泥双标的研报 2026-08 踩到） | 研报中披露口径差异并**方法论覆盖为 PB 主锚**；或调用前把 industry 手工映射为"建材"再传 detect_cyclical |
| 新端点 `pro.balancesheet` 裁剪字段：monetary_capital/tradable_fin_assets/bonds_payable 返回空，`pro.cashflow` 的 n_cashflow_end 同样被裁剪 | api.tushare.pro/dataapi 对这些字段只返回 NaN（海螺/塔牌 2026-08 实测：st_borr/lt_borr/total_assets/total_liab 正常，货币资金全 0），净现金计算会得出"-116 亿"这类错误结论 | 净现金改用 web 披露源交叉（搜狐证券资产负债表页 q.stock.sohu.com/{code}/zcfz.shtml 可取 monetary_capital/bonds_payable/未分配利润），报告标注来源与口径 |
| 任务书/上游 prompt 给的 ts_code 也可能张冠李戴（600500≠海螺水泥） | 海螺水泥真实代码 600585.SH、600500.SH 是中化国际——坑点 2b 的"任务书变体"：上游描述里的代码笔误会静默取到另一家公司完整数据（水泥研报 2026-08 实际发生） | 取数脚本对每个标的先 `pro.stock_basic(ts_code=...)` 核对 name+industry，与预期公司名不符即中止；报告中做勘误声明 |
| `analyze_momentum` 的 window_returns 出现负千分级不可能值（60d -241%/120d -1912%） | 引擎日价缓存缺口/错位的又一案例（海螺 -1912%/塔牌 -1932%，2026-08） | `pro.daily + pro.adj_factor` 手工复权复核并替换（可复用 `davis_analyzer/studies/cement_scoring.py` 的 `manual_returns`），报告引用复核值并披露引擎字段异常 |
| **研报引用股息率时只写 TTM 一个口径（高股息标的的隐形陷阱）** | TTM 股息率基于上一盈利年，盈利下滑年份（业绩预减）的 forward 股息率可能腰斩（塔牌：TTM 6.68% vs"分红率优先"分支 forward 仅 3.4-3.9%）；2026-08 起协调方将"股息可持续性三重校验"（盈利/现金流/资产负债表覆盖度 + TTM/forward 双口径）列为强制 | 股息章节必须并列 TTM 与 forward 双口径（"承诺优先"金额型 vs"分红率优先"比率型两分支），给出覆盖倍数表；`pro.dividend` 的 `div_proc="实施"` 过滤会漏掉预案阶段分红，需与公司公告口径交叉核对分红率 |
| **`rtk` 包装 `.venv/bin/python script.py` 报 `ModuleNotFoundError: No module named 'stockhot'`** | rtk 代理执行时工作目录/环境与 shell 的 `cd` 不一致，stockhot（editable 安装于仓库根）不在 sys.path；脚本模式的 sys.path[0] 是脚本所在目录而非 cwd（恒力/恒逸研报 2026-08 首次踩到） | 采集脚本顶部显式 `sys.path.insert(0, "/home/leo/Projects/CodeAgentDashboard")`；或跑长脚本时**不用 rtk 包装**，直接 `.venv/bin/python xxx.py > /tmp/out.log 2>&1` 重定向输出 |
| **`__editable__.stockhot-0.1.0.pth` 存在但脚本模式 import 仍失败（-c 模式却成功）** | editable 安装的 finder 对 script 模式失效（.pth 映射与实际包路径漂移）；`python -c` 因 cwd 在 sys.path 而侥幸通过，掩盖了安装损坏 | 同上：脚本内 sys.path.insert 兜底；或 `pip install -e .` 重装修复根因 |
| **`pro.forecast()` 不加过滤返回陈旧预告（如 2009FY/2013H1）** | 新端点对无 ann_date 过滤的请求返回任意历史行，排序不可靠——批量筛选时会把十年前的预告当成最新（创新药 29 标的筛选 2026-08 首次踩到，多只显示 2009/2013/2021 年预告） | 取数后必须 `pd.to_numeric(fc["ann_date"])>=YYYY0101` 过滤再按 end_date 排序取最新；研报场景只取覆盖目标报告期的最近一次预告 |
| **`pro.income` 新端点已裁剪 `gross_margin` 字段（KeyError）** | api.tushare.pro/dataapi 的 income 只返回营收/净利等基础字段，`gross_margin` 列不存在（中芯/华虹研报 2026-08 首次踩到，与 balancesheet 裁剪 monetary_capital 同族） | 毛利率改用 `pro.fina_indicator` 的 `grossprofit_margin`（该接口未裁剪）；切勿在 income fields 里带 gross_margin |
| **`pro.income` 的 revenue/n_income/n_income_attr_p 均为年初至今累计值（非单季）** | 把 H1 累计值当单季会导致 TTM/单季拆分全错——中芯 2025 归母曾因此算成 22.94 亿"单季"（实为 H1 累计），TTM PE 与 Tushare 211.75x 对不上才暴露（中芯/华虹研报 2026-08 踩到） | 单季值必须累计差分（Q2 单季 = H1 累计 − Q1 累计）；**与 `daily_basic.pe_ttm` 反推互验**是发现口径错误的最快方法：PE_TTM×隐含分母应等于"年报归母 − 上年同期季 + 本季"的滚动和 |
| **`pro.hk_daily` 频率超限（5 次/天）即报"频率超限"** | 港股日线接口配额极低，研报一天内多次取 H 股价必炸（中芯/华虹研报 2026-08 踩到） | H 股现价 fallback 新浪公开接口：`curl -s "https://hq.sinajs.cn/list=rt_hk00981,rt_hk01347" -H "Referer: https://finance.sina.com.cn"`（返回 GBK 需 iconv，含最新价/52 周高低/日期），A/H 溢价计算够用 |

## 9. dataclass 字段速查表

完整字段定义见 `davis_analyzer/types.py`，最常用的：

| Dataclass | 关键字段 |
|-----------|----------|
| `FinancialData` | ts_code, report_period, revenue, net_profit, eps, roe, operating_cf, total_debt, total_assets, yoy_revenue_growth, yoy_profit_growth, grossprofit_margin, rd_exp |
| `ProsperityScore` | composite_score, delta_g, revenue_score, profit_score, slope_score, duration_score, relative_delta_g |
| `ValuationData` | ts_code, trade_date, pe_ttm, pb, ps, total_mv |
| `DistressSignal` | total_score, layer1_score, layer2_score, layer3_score, signals_detail |
| `DavisDoubleScore` | final_score, rank, valuation_score, prosperity_score, distress_score, trend_score |
| `MomentumSignal` | momentum_score, absolute_momentum_score, rs_percentile, window_returns |
| `DividendSignal` | dividend_score, consecutive_years, latest_yield_pct, payout_years |
| `ForecastSignal` | leading_score, p_change_mid, type, is_stale |
| `ForecastRevision` | revision_direction (上调/下调/无修正), revision_pp, revision_score |

### PipelineResult 的补充因子字段（Step 7.6 自动产出）

`run_screening_pipeline()` 现在在返回的 `PipelineResult` 上额外挂三个补充因子 dict（**不改变 4 维 final_score**，仅作为 side-channel 信号供研报/选股消费）：

```python
result = run_screening_pipeline(top_n=30)

# 1. 价格动量 + 行业 RS（CANSLIM M+R 腿，真实复权收益，非估值趋势）
result.momentum_signals  # dict[ts_code, MomentumSignal]

# 2. 红利因子（连续派息年数 + 年化股息率，红利型选股用）
result.dividend_signals  # dict[ts_code, DividendSignal]  # 永不缺，非派息股地板分 10

# 3. 业绩预告前瞻信号（leading_score，0-100）
result.forecast_signals  # dict[ts_code, ForecastSignal]  # 无预告的股票不在 dict 里
```

**单股按需调用**（不跑全 pipeline 时）：

```python
from davis_analyzer.tushare_client import TushareClient
from davis_analyzer.momentum import analyze_momentum
from davis_analyzer.dividend import analyze_dividend
from davis_analyzer.forecast import analyze_forecast, analyze_forecast_revision
from davis_analyzer.holder_concentration import analyze_holder_concentration
from davis_analyzer.profitability import analyze_profitability_quality

client = TushareClient()
mom = analyze_momentum(client, "603690.SH")        # MomentumSignal | None
div = analyze_dividend(client, "603690.SH")         # DividendSignal (永不为 None)
# ⚠️ analyze_forecast 第三参是 ProsperityScore 对象（不是 float！），用于内部读 .delta_g
#   错误写法: analyze_forecast(client, "603690.SH", pscore.composite_score)  # → 'float' object has no attribute 'delta_g'
#   正确写法: analyze_forecast(client, "603690.SH", pscore)  # 传整个 ProsperityScore 对象
fc = analyze_forecast(client, "603690.SH", pscore)  # ForecastSignal | None（需先算 pscore）
rev = analyze_forecast_revision(client, "603690.SH")  # ForecastRevision | None
hc = analyze_holder_concentration(client, "603690.SH")  # HolderConcentration | None
pq = analyze_profitability_quality(fin_list)        # ProfitabilityQuality (纯计算，无需 client)
```

> **坑点 13（forecast 传参）**：`analyze_forecast(client, ts_code, prosperity_score)` 的第三参签名虽叫 `prosperity_score`，但**实际期望传入 `ProsperityScore` dataclass 对象**（内部访问 `.delta_g`），**不是 `pscore.composite_score` 浮点数**。若传 float 会报 `'float' object has no attribute 'delta_g'`。调用前必须先 `pscore = calculate_prosperity_score(fin)` 拿到 ProsperityScore 对象，再传入。智度股份研报（2026-07）首次踩到此坑。

> **坑点 14（forecast 单位是万元）**：`pro.forecast()` 返回的 `net_profit_min` / `net_profit_max` 字段单位是**万元**，不是元。如果直接 `/1e8` 转亿元，数值会偏小 10000 倍（如陕西煤业 H1 预增 112 亿显示为 0.01 亿）。正确转换：`net_profit_min / 1e4` 转亿元，或 `net_profit_min / 1e8` 转亿元后乘 1 万修正。煤炭/钢铁周期研报（2026-08）首次踩到此坑。**批量取数脚本务必验证一个已知标的的预告金额是否合理**。

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

---

## 11. 股东户数趋势分析（筹码集中度，每篇个股研报必备）

**核心逻辑**：股东户数是筹码集中度的领先信号——
- **户数逐期下降** → 筹码集中 → 主力收集 → **上涨动能增强 ✓**（看多信号）
- **户数逐期上升** → 筹码分散 → **上涨动能减弱 ⚠**（警示信号）

**接口**：`pro.stk_holdernumber`（注意拼写，一个词，**不是** `stk_holder_number`）。返回 `holder_num`（股东户数）。

```python
import os, pandas as pd
from dotenv import load_dotenv
load_dotenv(".env", override=True)
os.environ["PROJECT_ROOT"] = os.getcwd()
from stockhot.tushare_config import get_pro_api

pro = get_pro_api(timeout=30)

def holder_trend(ts_code, periods=8):
    """股东户数趋势：返回近 N 期户数 + 环比变化 + 趋势判断。"""
    h = pro.stk_holdernumber(
        ts_code=ts_code, fields="ts_code,ann_date,end_date,holder_num"
    ).sort_values("end_date").tail(periods)

    rows = []
    prev = None
    for _, r in h.iterrows():
        num = int(r["holder_num"])
        chg = (num - prev) / prev * 100 if prev else None
        rows.append({"end_date": r["end_date"], "holder_num": num, "chg_pct": chg})
        prev = num

    # 近 4 期趋势判断
    nums4 = [r["holder_num"] for r in rows[-4:]] if len(rows) >= 4 else [r["holder_num"] for r in rows]
    trend = "集中(动能增强✓)" if nums4[-1] < nums4[0] else "分散(动能减弱⚠)"
    return {"rows": rows, "trend": trend, "latest": nums4[-1], "base": nums4[0]}

# 用法
result = holder_trend("600309.SH")
for r in result["rows"]:
    chg = f"{'↑' if r['chg_pct']>0 else '↓'}{abs(r['chg_pct']):.1f}%" if r["chg_pct"] else "基期"
    print(f"  {r['end_date']}: {r['holder_num']:,} ({chg})")
print(f"  → 趋势: {result['trend']}")
```

**报告写法**：在财务分析章节或独立「股东户数与筹码集中度」小节，附户数变化表（近 4-8 期 + 环比）+ 趋势判断结论。可补充 `pro.top10_floatholders` 的十大流通股东持股比例合计变化作交叉验证（top10 比例上升 ≈ 户数下降，两者方向一致）。

**坑点 12（接口名）**：`stk_holdernumber` 是一个词，写成 `stk_holder_number` 会报"请指定正确的接口名"。
