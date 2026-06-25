# Tushare API 数据接口参考

本文件汇总本项目在研报写作中**实测验证可用**的 Tushare 接口、正确的调用模式、已知的频率限制与权限边界，以及多源 fallback 链设计。数据接口目录参见 <https://tushare.pro/document/2>。

> **本指南中的每个接口都在 2026-06 用新 token + 新端点实测通过**（除非单独标注权限不足）。照着用不会踩坑。

## 1. 端点迁移背景（务必先读）

Tushare 正在从旧端点迁移到新端点，**两者的请求格式不兼容**：

| 维度 | 旧端点（已弃用） | 新端点（当前） |
|------|------------------|----------------|
| URL | `http://api.waditu.com/dataapi` | `http://api.tushare.pro/dataapi` |
| `api_name` 位置 | 追加到 URL 路径 `/{api_name}` | 放在 JSON **body** 里 |
| 旧 token | 失效（服务端弃用） | 新 token（2026-06 生效） |
| HTTPS | — | **超时不通**，必须用 HTTP |

**结论**：官方 `tushare` Python 库默认仍走旧 URL 并把 api_name 追加到路径，在新端点下会**静默超时**。本项目用自封装的 `stockhot.tushare_config.get_pro_api()` 替代。

### 1.1 get_pro_api() 使用方式

```python
from stockhot.tushare_config import get_pro_api

pro = get_pro_api()              # 从 .env 读 TUSHARE_TOKEN
df = pro.daily_basic(ts_code="000001.SZ", trade_date="20260625")   # 同 pro.xxx() 风格
df = pro.query("daily_basic", ts_code="000001.SZ", trade_date="20260625")  # 等价显式写法
```

- `pro.<api_name>(**kwargs)` 是 `pro.query(api_name, **kwargs)` 的语法糖（`__getattr__` 实现）
- `fields` 参数可选：`pro.query("daily_basic", ts_code=..., fields="ts_code,trade_date,pe_ttm")`
- 返回 `pd.DataFrame`，空数据返回空 DataFrame（不抛错）

### 1.2 token 与 .env

`.env`（gitignored，不入库）：
```
TUSHARE_TOKEN=<你的新 token>
TUSHARE_MCP_URL=https://api.tushare.pro/mcp/?token=<同上>
```

**坑点**：token 泄露会被盗刷积分。`.env` 必须在 `.gitignore` 中，**绝不 commit**。

## 2. 已验证可用接口清单

### 2.1 A 股行情与估值（高频用）

| 接口 | 用途 | 关键参数 | 关键字段 | 状态 |
|------|------|----------|----------|------|
| `daily_basic` | 每日估值（PE/PB/PS/MV） | `ts_code`, `trade_date` 或 `ts_code`+日期区间 | `pe_ttm`, `pb`, `ps`, `total_mv`（万元）, `turnover_rate`, `dv_ratio` | ✅ 可用 |
| `daily` | 日线行情 | `ts_code` + 日期区间 | `open/high/low/close/vol/amount/pct_chg` | ✅ 可用 |
| `stock_basic` | 股票列表（含行业） | `fields="ts_code,name,industry"` | `ts_code`, `name`, `industry` | ✅ 可用 |
| `index_daily` | 指数日线 | `ts_code="000300.SH"` + 日期区间 | `close`, `pct_chg` | ✅ 可用 |
| `index_dailybasic` | **指数估值（PE/PB）** | `ts_code="000300.SH"` + 日期区间 | `pe`, `pb` | ✅ 可用 |

**坑点（指数覆盖边界）**：
- `index_dailybasic` **不覆盖** `000688.SH`（科创50）。科创板标的做相对估值时 fallback 到 `000300.SH`（沪深300），见 `stockhot/valuation/__init__.py` 的 `BENCHMARK_MAP`。
- 常用基准：主板→`000300.SH`（沪深300）/`000016.SH`（上证50）；创业板→`399006.SZ`；科创板→`000300.SH`（fallback）。

### 2.2 财务数据

| 接口 | 用途 | 关键参数 | 状态 |
|------|------|----------|------|
| `income` | 利润表 | `ts_code`, `period`（如 "20251231"） | ✅ 可用 |
| `balancesheet` | 资产负债表 | `ts_code`, `period` | ✅ 可用 |
| `cashflow` | 现金流量表 | `ts_code`, `period` | ✅ 可用 |
| `fina_indicator` | 财务指标（ROE/ROA/负债率等） | `ts_code`, `period` | ✅ 可用 |
| `dividend` | 分红送股 | `ts_code` | ✅ 可用（实测返回 53 条） |
| `forecast` | 业绩预告 | `ts_code` | ✅ 可用（实测返回 16 条） |

> **注意**：研报场景一般不直接调这些底层接口，而是通过 `davis_analyzer.financial_fetcher.fetch_financial_data(client, ts_code, periods=12)` 一次性取整理好的 `list[FinancialData]`（见 `engine-usage.md` §2）。本表仅列底层接口以便灵活取数。

### 2.3 资金流与龙虎榜

| 接口 | 用途 | 关键参数 | 状态 |
|------|------|----------|------|
| `moneyflow` | 个股资金流（东财分类） | `ts_code` 或 `trade_date` | ✅ 可用（实测返回 5880 行/日） |
| `moneyflow_dc` | **大盘资金流（东财分类）** | `start_date`, `end_date` | ✅ 可用 |
| `moneyflow_mkt_dc` | 市场分类资金流 | `start_date`, `end_date` | ✅ 可用 |
| `top_list` | 龙虎榜每日明细 | `trade_date` | ✅ 可用（实测返回 97 行/日） |
| `top_inst` | 龙虎榜机构席位明细 | `trade_date` | ✅ 可用 |
| `top10_holders` | 十大股东 | `ts_code` | ✅ 可用（实测返回 298 行） |
| `top10_floatholders` | 十大流通股东 | `ts_code` | ✅ 可用 |

> **用途**：`stockhot/fund_flow/__init__.py` 用 `moneyflow` + `stock_basic` 按行业聚合得到板块资金流，作为东财 akshare 被封 IP 时的首选数据源。

### 2.4 宏观经济（macro 模块）

| 接口 | 用途 | 关键参数 | 状态 |
|------|------|----------|------|
| `cn_gdp` | GDP | `start_m`, `end_m` | ✅ 可用（实测 176 行） |
| `cn_pmi` | 制造业 PMI | `start_m` | ✅ 可用 |
| `cn_cpi` | CPI | `start_m` | ✅ 可用 |
| `cn_ppi` | PPI | `start_m` | ✅ 可用 |
| `cn_m` | 货币供应量（M0/M1/M2） | `start_m` | ✅ 可用 |
| `shibor` | Shibor 利率 | `start_date`, `end_date` | ✅ 可用 |
| `shibor_lpr` | LPR 贷款基准利率 | `start_date`, `end_date` | ✅ 可用 |

> **用途**：`stockhot/macro/__init__.py` 的 `collect_macro_snapshot()` 聚合 PMI/CPI/PPI/M/LPR 计算 0-100 的宏观景气评分，权重 PMI 35% + M1-M2 差 20% + M2 15% + CPI-PPI 差 15% + LPR/Shibor 15%。

### 2.5 港股（受限）

| 接口 | 状态 | 说明 |
|------|------|------|
| `hk_basic` | ✅ 低权限可用 | 港股基础信息；**新股上市后 1-3 个月才收录** |
| `hk_daily` | ⚠️ **1 次/小时限频** | 港股日线；频繁调用触发 429 |
| `hk_income` / `hk_balancesheet` / `hk_cashflow` | ❌ **需 5000 积分** | 普通账户无权限 |

**港股标的策略**：先用 `pro.hk_basic(ts_code="01191.HK")` 探测是否收录（code 补零至 5 位）；未收录或财务无权限时，改用招股书 + 手动 PS 估值，遵循 `valuation-loss-making-targets` skill。

### 2.6 已确认无权限（需更高积分）

| 接口 | 错误码 | 说明 |
|------|--------|------|
| `news` | 40203 | 资讯数据，需更高积分 |
| `ths_hot` | 40203 | 同花顺热点，需更高积分 |
| `dc_hot` | 40203 | 东财热点，需更高积分 |

**资讯数据 fallback**：无权限时用 WebSearch 作为资讯来源（与盘后总结 skill 相同的方法），不编造。

## 3. 大历史数据获取：分批策略（关键）

**单个请求拉 3 年（700+ 行）历史会超时**。必须按 6 个月切片分批，拼接去重：

```python
from datetime import date, timedelta
import pandas as pd

def _batch_fetch(pro, api_name, ts_code, lookback_years=3.0, chunk_days=183):
    """分批拉取历史，拼接去重。"""
    end = date.today()
    total_days = int(lookback_years * 365)
    frames = []
    cursor = end - timedelta(days=total_days)
    while cursor <= end:
        nxt = min(cursor + timedelta(days=chunk_days), end)
        df = getattr(pro, api_name)(
            ts_code=ts_code,
            start_date=cursor.strftime("%Y%m%d"),
            end_date=nxt.strftime("%Y%m%d"),
        )
        if df is not None and not df.empty:
            frames.append(df)
        cursor = nxt + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    # 按日期去重（切片边界可能重叠）
    if "trade_date" in out.columns:
        out = out.drop_duplicates(subset=["trade_date"]).sort_values("trade_date").reset_index(drop=True)
    return out
```

> `stockhot/valuation/__init__.py` 的 `_batch_fetch()` 已实现此逻辑，直接复用。

## 4. MCP Server（备用数据源）

Tushare 提供 MCP server（基于 SSE 的 JSON-RPC），适合**小批量、低频**取数（不适合拉大历史）：

```python
from stockhot.mcp_client import TushareMCP

mcp = TushareMCP()                        # 从 .env 读 TUSHARE_MCP_URL
df = mcp.query("daily_basic", ts_code="000001.SZ", trade_date="20260625")
pro = mcp.get_pro_api()                   # 返回 shim，支持 pro.xxx() 风格
df = pro.daily_basic(ts_code="000001.SZ")
```

**MCP 适用场景**：
- 取最新一日数据（单点查询）
- 作为 HTTP API 超时/失败时的 fallback
- 字段逗号串自动转 list：`fields="a,b,c"` → `["a","b","c"]`

**MCP 不适用**：
- 拉 3 年历史（SSE 流式传输，大批量慢且易断）
- 频繁调用（同样有频率限制）

**坑点**：MCP 的 SSE 响应用 `\r\n` 换行，解析时按 `\n` split 后每个元素 `strip("\r\n")`，否则 `json.loads` 报错。

## 5. 多源数据韧性（fallback 链设计）

本项目对关键数据采用三层 fallback，避免单点失败：

| 数据 | 首选 | 次选 | 末选 |
|------|------|------|------|
| 个股估值历史 | `daily_basic`（API 分批） | MCP `daily_basic` | — |
| 指数估值历史 | `index_dailybasic`（API 分批） | MCP fallback | — |
| 板块资金流 | `moneyflow` + `stock_basic` 聚合 | 同花顺 HTML 解析 | — |
| 大盘资金流 | `moneyflow_dc` / `moneyflow_mkt_dc` | — | — |
| 宏观指标 | `cn_pmi`/`cn_cpi`/`cn_ppi`/`cn_m`/`shibor_lpr` | — | — |
| 港股财务 | `hk_income`（5000 积分） | 招股书 + WebSearch | HKEX 披露 |
| 资讯热点 | `news`/`ths_hot`（需更高积分） | WebSearch | — |

**原则**：上层失败时**降级而非中断**，并在报告/日志中标注实际使用的数据源，不掩盖降级。

## 6. 频率限制速查

| 接口 | 限制 | 应对 |
|------|------|------|
| `hk_daily` | 1 次/小时 | 缓存结果，不要循环调 |
| 多数 A 股接口 | 按积分有每分钟上限 | 避免无 sleep 的紧密循环 |
| 大历史单请求 | 超时（非限频） | 分批 6 个月切片 |

## 7. 常见错误速查

| 报错 | 原因 | 解决 |
|------|------|------|
| `requests.exceptions.ReadTimeout` | 单次拉 3 年历史超时 | 用 `_batch_fetch` 分批 |
| HTTPS 连接 hang 死 | api.tushare.pro 的 443 超时 | 用 HTTP（`http://api.tushare.pro/dataapi`） |
| `抱歉，您每天最多访问该接口 X 次` | 频率限制 | 缓存 + 降频，必要时换 MCP |
| `40001`/`40203` 权限错误 | 积分不足 | 查积分要求，用 fallback 数据源 |
| 旧 token 返回空 | api.waditu.com 端点弃用旧 token | 换新 token + 新端点 |
| 代码张冠李戴（数据正常但公司不对） | ts_code 用错，引擎不报错 | 取数后核对 `df['name']` 或 `fin[0].ts_code` |
| 429 retry | 频率超限 | 读 `retry_after` 指数退避（见 notification skill 同款逻辑） |

## 8. 与 davis_analyzer 引擎的关系

研报写作时**优先用 davis_analyzer 引擎**而非直接调底层 Tushare 接口：

| 需求 | 用引擎 | 直接调 Tushare |
|------|--------|----------------|
| 单股财务四维评分 | ✅ `fetch_financial_data` + `calculate_prosperity_score` | — |
| 个股 PE/PB 历史分位 | ✅ `client.get_daily_basic` | 兜底 |
| 相对市场估值锚定 | ✅ `stockhot.valuation.analyze_relative_valuation` | — |
| 宏观景气快照 | ✅ `stockhot.macro.collect_macro_snapshot` | — |
| 板块/大盘资金流 | ✅ `stockhot.fund_flow` 各函数 | — |
| 引擎未封装的特殊字段 | — | ✅ `pro.<api_name>(...)` |

引擎封装细节见 `references/engine-usage.md`；本指南只覆盖底层接口的可用性与调用模式。

## Source of Truth

本指南基于 2026-06 的实测。接口可用性、积分要求、频率限制可能随 Tushare 平台调整变化——以 <https://tushare.pro/document/2> 官方文档与实际调用结果为准。接口签名以 `stockhot/tushare_config.py`（HTTP API）和 `stockhot/mcp_client.py`（MCP）源码为准。
