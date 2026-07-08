# 数据源规范：Tushare 第一优先，AKShare 兜底

> 本文档是所有 stockhot 模块和 agent skill 的**数据源选择权威规范**。2026-07-07 建立。

## 1. 核心原则

**所有数据获取以 Tushare 为第一数据源，AKShare 作为 fallback。** 双源架构增强健壮性——Tushare 故障时自动回退到 AKShare，反之亦然。

## 2. 为什么 Tushare 优先

| 维度 | Tushare | AKShare |
|------|---------|---------|
| 端点稳定性 | api.tushare.pro/dataapi（新端点，稳定） | 东方财富/新浪/同花顺（经常 RemoteDisconnected） |
| 数据结构 | 字段标准化（英文）、token 鉴权 | 中文字段、无鉴权（易被限流） |
| 覆盖度 | 95%（含涨停池/龙虎榜/资金流/指数/OHLCV/宏观/财务） | 100%（含 Tushare 缺口） |
| 限频 | 500/min（充足） | 易被 IP 限流 |
| 鉴权 | token（TUSHARE_TOKEN env） | 无 |

## 3. AKShare → Tushare 接口映射

| AKShare 函数 | Tushare 接口 | 覆盖场景 |
|--------------|--------------|---------|
| `stock_zh_a_hist`（个股 OHLCV） | `pro_bar(adj=qfq)` + `daily` | 技术分析、回测 |
| `stock_zh_index_daily`（指数） | `index_daily` | 大盘技术面 |
| `stock_zt_pool_em`（涨停池） | `limit_list_d(limit=U)`* | limit_up 模块 |
| `stock_zt_pool_zbgc_em`（炸板池） | `limit_list_d(limit=Z)`* | limit_up 模块 |
| `stock_zt_pool_dtgc_em`（跌停池） | `limit_list_d(limit=D)`* | limit_up 模块 |
| `stock_lhb_detail_em`（龙虎榜明细） | `top_list` | dragon_tiger 模块 |
| `stock_lhb_jgmmtj_em`（机构交易） | `top_inst` | dragon_tiger 模块 |
| `stock_market_fund_flow`（大盘资金流） | `moneyflow_mkt_dc` | fund_flow 模块 |
| `stock_individual_fund_flow`（个股资金流） | `moneyflow` | fund_flow 模块 |
| `stock_zh_a_st_em`（ST 股票） | `stock_basic` 过滤 name 含 ST | risk_alert 模块 |
| `stock_zh_a_spot_em`（实时价） | `daily_basic`（最新交易日收盘） | advisor 模块 |
| 宏观（PMI/CPI/PPI/M2/Shibor/LPR） | `cn_pmi`/`cn_cpi`/`cn_ppi`/`cn_m`/`shibor`/`shibor_lpr` | macro 模块 |

> \* `limit_list_d` 的 `limit` 参数过滤不生效，返回 U/D/Z 混合数据，需客户端按 `limit` 列自行过滤。

## 4. Tushare 无等价接口的场景（保留 AKShare only）

以下两类数据 Tushare 暂无等价接口，**保留 AKShare 作为唯一数据源**（双源架构的缺口，诚实标注）：

| 数据 | AKShare 函数 | 用途 | 缺口说明 |
|------|--------------|------|---------|
| **停牌股票池** | `stock_zh_a_stop_em` | risk_alert 模块 | Tushare 无停牌股票池接口 |
| **活跃营业部龙虎榜** | `stock_lhb_hyyyb_em` | dragon_tiger 模块 | Tushare top_list/top_inst 无营业部维度 |
| **iVIX/QVIX 波动率指数** | `index_option_50etf_qvix` | volatility 模块 Layer 5 | Tushare 无 QVIX/iVIX 等价接口；数据来自期权论坛（optbbs.com）第三方重建版，2018 年官方 iVIX 停发后持续更新 |

当这两类数据的 AKShare 调用失败时，对应模块功能降级（标"数据不可用"），不影响其他功能。

## 5. 代码规范（写新模块或改现有模块时遵循）

### 5.1 必须使用 `safe_tushare_call`（不要裸调 `pro.xxx()`）

```python
from stockhot.core.tushare_client_safe import safe_tushare_call

# ✅ 正确：通过 safe_tushare_call（限频+重试+空检查）
df = safe_tushare_call("limit_list_d", trade_date="20260707", limit="U")

# ❌ 错误：裸调 pro.xxx（无限频/重试，且可能用旧端点）
pro = ts.pro_api()
df = pro.limit_list_d(trade_date="20260707", limit="U")
```

### 5.2 双源场景用 `fetch_with_fallback`

```python
from stockhot.core.datasource import fetch_with_fallback
from stockhot.core.tushare_client_safe import safe_tushare_call
from stockhot.core.rate_limiter import safe_akshare_call
import akshare as ak

df = fetch_with_fallback(
    primary_fn=lambda: safe_tushare_call("limit_list_d", trade_date=date),
    fallback_fn=lambda: safe_akshare_call(ak.stock_zt_pool_em, date=date),
    label="涨停池",
)
```

### 5.3 统一端点：`api.tushare.pro/dataapi`

所有 Tushare 调用走新端点（`safe_tushare_call` 和 `get_pro_api()` 内置），**废弃旧版 `ts.set_token() + ts.pro_api()`**（旧端点 waditu.com 易超时）。

### 5.4 字段映射在模块内部完成

Tushare/AKShare 字段名不同（如 Tushare `trade_date` vs AKShare `date`），映射在每个 fetch 函数里完成，**对外暴露统一 schema**，不破坏下游消费者。

## 6. 验证清单（改造后检查）

- [ ] 日志显示 `via Tushare`（除非 Tushare 故障 fallback 到 AKShare）
- [ ] `safe_tushare_call` 永不抛异常（失败返回空 DataFrame）
- [ ] 双源场景：Tushare 失败时自动走 AKShare，日志显示 fallback
- [ ] 输出 schema 与改造前一致（下游 run_*_analysis 不受影响）

## 7. 相关文件

- 基础设施：`stockhot/core/tushare_client_safe.py`、`stockhot/core/datasource.py`
- 配置：`stockhot/tushare_config.py`（`get_pro_api()` 新端点）
- 各模块 fetch 函数：`limit_up/`、`dragon_tiger/`、`fund_flow/`、`risk_alert/`、`technical_analyzer/data_loader.py`、`index_technical/data_loader.py`、`advisor/data_sources/technical.py`

## 8. 维护

当发现新的"Tushare 无等价"场景，或 AKShare 接口变更时，更新本文档第 4 节（缺口清单）和第 3 节（映射表）。
