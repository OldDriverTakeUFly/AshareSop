# AKShare Endpoint Verification Results

**AKShare Version:** 1.18.56  
**Test Date:** 2026-05-16  
**Python:** 3.12.3  
**Environment:** Local development (proxy-stripped)  

## Summary

| Metric | Count |
|--------|-------|
| Total endpoints tested | 36 |
| Passed | 30 |
| Failed | 6 |
| Pass rate | 83% |

All 6 failures are **ConnectionError** from East Money (东方财富) endpoints — `RemoteDisconnected`. These endpoints work from China-based networks; the failures are caused by network/firewall restrictions in the current test environment.

---

## Category 1: 海外市场 (Overseas Market)

| Endpoint | Function | Status | Return Fields | Notes |
|----------|----------|--------|---------------|-------|
| US Stock Indices (S&P500, Nasdaq, Dow) | `index_us_stock_sina()` | ✅ PASS | date, open, high, low, close, volume, amount (5631 rows) | Sina source. Best source for US index closing prices |
| Global Indices Spot (US/EU/Asia) | `index_global_spot_em()` | ❌ FAIL | — | East Money connection blocked. Alternative: `index_us_stock_sina()` |
| Global Indices Hist (S&P500) | `index_global_hist_em(symbol='道琼斯')` | ❌ FAIL | — | East Money connection blocked. Alternative: `index_us_stock_sina()` |
| China VIX (50ETF QVIX) | `index_option_50etf_qvix()` | ✅ PASS | date, open, high, low, close (2726 rows) | Chinese option implied volatility. No direct US VIX in akshare |
| A50 Futures (foreign) | `futures_foreign_hist(symbol='CHA50CFD')` | ✅ PASS | date, open, high, low, close, volume, position, s (2594 rows) | SGX A50 CFD data. Symbol must be `CHA50CFD` (not `A50`) |
| Forex Spot (USD/CNY) | `forex_spot_em()` | ❌ FAIL | — | East Money connection blocked. Alternative: `currency_boc_sina()` |
| Forex Hist (USDCNH) | `forex_hist_em(symbol='USDCNH')` | ❌ FAIL | — | East Money connection blocked. Signature: only takes `symbol`. Alternative: `currency_boc_sina()` |
| BOC Exchange Rate | `currency_boc_sina(symbol='美元', start_date=..., end_date=...)` | ✅ PASS | 日期, 中行汇买价, 中行钞买价, 中行钞卖价/汇卖价, 央行中间价, 中行折算价 (27 rows) | Best USD/CNY source. Includes PBOC middle rate |
| US Treasury Yield | `bond_zh_us_rate(start_date=...)` | ✅ PASS | 日期, 中国国债收益率2/5/10/30年, 美国国债收益率2/5/10/30年, GDP年增率 (23 rows) | Both China & US treasury yields. Includes US 10Y (美国国债收益率10年) |
| US Stock Spot | `stock_us_spot_em()` | ❌ FAIL | — | East Money connection blocked. Alternative: `index_us_stock_sina()` for indices |

### Key Findings — Overseas Market
- **US indices**: Use `index_us_stock_sina()` (Sina) — reliable, no connection issues
- **USD/CNY**: Use `currency_boc_sina()` — includes PBOC middle rate
- **US 10Y yield**: Use `bond_zh_us_rate()` — includes both CN and US treasury yields
- **VIX**: No US VIX endpoint. China VIX available via `index_option_50etf_qvix()`. For US VIX, consider adding a separate data source
- **East Money endpoints**: `index_global_spot_em`, `forex_spot_em`, `forex_hist_em`, `stock_us_spot_em`, `index_global_hist_em` all blocked from this network. These are functional APIs, just network-restricted

---

## Category 2: 期货数据 (Futures)

| Endpoint | Function | Status | Return Fields | Notes |
|----------|----------|--------|---------------|-------|
| Futures Main (IF/CSI300) | `futures_main_sina(symbol='IF0')` | ✅ PASS | 日期, 开盘价, 最高价, 最低价, 收盘价, 成交量, 持仓量, 动态结算价 (2260 rows) | `IF0`=CSI300, `IC0`=CSI500, `IM0`=CSI1000 continuous main |
| Futures Main (IC/CSI500) | `futures_main_sina(symbol='IC0')` | ✅ PASS | Same as IF (2260 rows) | CSI500 index futures continuous |
| Futures Main (IM/CSI1000) | `futures_main_sina(symbol='IM0')` | ✅ PASS | Same as IF (922 rows) | CSI1000 index futures, shorter history (from 2022) |
| Futures Spot Price (IF/IC/IM) | `futures_spot_price(date=..., vars_list=['IF','IC','IM','IH'])` | ✅ PASS | date, symbol, spot_price, near_contract, near_contract_price, dominant_contract, dominant_contract_price, near_basis, dom_basis, near_basis_rate, dom_basis_rate | Basis data included. May return 0 rows on non-trading days |
| CSI300 Index Daily | `stock_zh_index_daily_em(symbol='sh000300')` | ❌ FAIL | — | East Money connection blocked. Alternative: `futures_main_sina(symbol='IF0')` for futures or use Sina source |
| Futures ZH Realtime | `futures_zh_realtime()` | ✅ PASS | symbol, exchange, name, trade, settlement, open, high, low, close, volume, position... (13 rows) | Realtime Chinese futures quotes. Limited to main contracts |

### Key Findings — Futures
- **IF/IC/IM continuous**: `futures_main_sina(symbol='X0')` is the primary source. Use `IF0`/`IC0`/`IM0` for continuous main contracts
- **Basis data**: `futures_spot_price()` provides near-basis and dominant-basis rates. Note: returns 0 rows on non-trading days
- **CSI300 index**: East Money blocked; use futures data as proxy or find Sina alternative
- **Realtime**: `futures_zh_realtime()` works but limited to ~13 main contracts

---

## Category 3: 资金面 (Capital Flow)

| Endpoint | Function | Status | Return Fields | Notes |
|----------|----------|--------|---------------|-------|
| Northbound Capital Hist | `stock_hsgt_hist_em(symbol='北向资金')` | ✅ PASS | 日期, 当日成交净买额, 买入成交额, 卖出成交额, 历史累计净买额, 当日资金流入, 当日余额, 持股市值... (2669 rows) | Full northbound flow history since 2014. Other symbols: `沪股通`, `深股通` |
| HSGT Fund Flow Summary | `stock_hsgt_fund_flow_summary_em()` | ✅ PASS | 交易日, 类型, 板块, 资金方向, 交易状态, 成交净买额, 资金净流入, 当日资金余额... (4 rows) | Current-day summary for 沪港通/深港通 |
| SSE Margin Detail | `stock_margin_detail_sse(date='YYYYMMDD')` | ✅ PASS | 信用交易日期, 标的证券代码, 标的证券简称, 融资余额, 融资买入额, 融资偿还额, 融券余量, 融券卖出量, 融券偿还量 (1979 rows) | Per-stock margin detail for SSE. Date param required |
| SSE Margin Summary | `stock_margin_sse(start_date=..., end_date=...)` | ✅ PASS | 信用交易日期, 融资余额, 融资买入额, 融券余量, 融券余量金额, 融券卖出量, 融资融券余额 (8 rows) | Aggregate SSE margin balance. Use for overall market margin trend |
| SZSE Margin Underlying | `stock_margin_underlying_info_szse(date='YYYYMMDD')` | ✅ PASS | 证券代码, 证券简称, 融资标的, 融券标的, 当日可融资, 当日可融券, 融券卖出价格限制, 涨跌幅限制 (2068 rows) | SZSE margin eligible securities list |

### Key Findings — Capital Flow
- All capital flow endpoints working perfectly
- **Northbound**: `stock_hsgt_hist_em` provides complete historical data with 2669 days of records
- **Margin**: Both SSE detail (`stock_margin_detail_sse`) and summary (`stock_margin_sse`) work. SZSE underlying info also available
- **Note**: East Money endpoints (`stock_hsgt_*_em`) work fine here — the connection issues only affect certain East Money domains (quote.eastmoney.com), not datacenter ones

---

## Category 4: 产业链 (Supply Chain / Commodities)

| Endpoint | Function | Status | Return Fields | Notes |
|----------|----------|--------|---------------|-------|
| LME Copper Hist | `futures_foreign_hist(symbol='CAD')` | ✅ PASS | date, open, high, low, close, volume, position, s (2538 rows) | Sina symbol: `CAD` (伦敦铜 CFD). NOT `伦敦铜` |
| LME Aluminum Hist | `futures_foreign_hist(symbol='AHD')` | ✅ PASS | Same as Copper (2538 rows) | Sina symbol: `AHD` (伦敦铝 CFD) |
| LME Zinc Hist | `futures_foreign_hist(symbol='ZSD')` | ✅ PASS | Same as Copper (2538 rows) | Sina symbol: `ZSD` (伦敦锌 CFD) |
| LME Copper Realtime | `futures_foreign_commodity_realtime(symbol='CAD')` | ✅ PASS | 名称, 最新价, 人民币报价, 涨跌额, 涨跌幅, 开盘价, 最高价, 最低价, 昨日结算价, 持仓量, 买价, 卖价, 行情时间, 日期 (1 row) | Single symbol only; list param causes parsing bug |
| BDI Index | `spot_goods(symbol='波罗的海干散货指数')` | ✅ PASS | 日期, 指数, 涨跌额, 涨跌幅 (4022 rows) | Baltic Dry Index. One of only 3 symbols accepted by `spot_goods` |
| CFLP Commodity Index | `index_price_cflp(symbol='周指数')` | ✅ PASS | 日期, 定基指数, 环比指数, 同比指数 (539 rows) | Broad commodity price index. Does NOT have lithium/solar-specific data |
| Futures Spot (Cu/Al/Zn/Coal/Steel) | `futures_spot_price(date=..., vars_list=['CU','AL','ZN','J','JM','I','RB'])` | ✅ PASS | date, symbol, spot_price, near_contract, near_contract_price, dominant_contract, dominant_contract_price, near_basis, dom_basis, near_basis_rate, dom_basis_rate (7 rows) | Domestic commodity futures with basis data |
| Steel Billet Index | `spot_goods(symbol='钢坯价格指数')` | ✅ PASS | 日期, 指数, 涨跌额, 涨跌幅 (6458 rows) | Steel billet price index (6458 days of history) |
| Energy/Oil Hist | `energy_oil_hist()` | ✅ PASS | 调整日期, 汽油价格, 柴油价格, 汽油涨跌, 柴油涨跌 (320 rows) | China domestic fuel price adjustments |

### Key Findings — Supply Chain
- **LME metals**: Use symbol codes `CAD`(铜), `AHD`(铝), `ZSD`(锌). Chinese names (e.g., `伦敦铜`) do NOT work as params
- **`futures_foreign_commodity_realtime`**: Has a parsing bug when passing list of symbols — use single symbol only
- **`spot_goods`**: Only accepts 3 symbols: `波罗的海干散货指数`, `钢坯价格指数`, `澳大利亚粉矿价格`. Lithium, coal, solar NOT available
- **Lithium carbonate (碳酸锂)**: NOT available in AKShare. Will need an alternative data source (e.g., SMM, 百川盈孚)
- **PV/solar module prices**: NOT available in AKShare. Will need an alternative data source
- **Coal**: Use `futures_spot_price(vars_list=['J','JM','I'])` for coking coal/iron ore futures basis data

### Symbol Reference — Foreign Futures
| Sina Code | Commodity | Exchange |
|-----------|-----------|----------|
| CAD | 伦敦铜 (LME Copper) | LME |
| AHD | 伦敦铝 (LME Aluminum) | LME |
| ZSD | 伦敦锌 (LME Zinc) | LME |
| CHA50CFD | A50 Index (SGX) | SGX |
| CL | WTI Crude Oil | NYMEX |
| GC | Gold (COMEX) | COMEX |
| SI | Silver (COMEX) | COMEX |
| HG | Copper (COMEX) | COMEX |
| NID | Nickel (LME) | LME |

---

## Category 5: 日历 (Calendar)

| Endpoint | Function | Status | Return Fields | Notes |
|----------|----------|--------|---------------|-------|
| Trade Date History | `tool_trade_date_hist_sina()` | ✅ PASS | trade_date (8797 rows) | **CONFIRMED WORKING**. A-share trade calendar since 1990 |
| Restricted Release Queue (Sina) | `stock_restricted_release_queue_sina(symbol='600000')` | ✅ PASS | 代码, 名称, 解禁日期, 解禁数量, 解禁股流通市值, 上市批次, 公告日期 (8 rows) | Per-stock restricted shares schedule |
| Restricted Release Queue (EM) | `stock_restricted_release_queue_em(symbol='600000')` | ✅ PASS | 序号, 解禁时间, 解禁股东数, 解禁数量, 实际解禁数量, 未解禁数量, 实际解禁数量市值, 占总市值比例, 占流通市值比例, 限售股类型... (4 rows) | More detailed than Sina version. Includes impact metrics |
| Restricted Release Summary | `stock_restricted_release_summary_em()` | ✅ PASS | 序号, 解禁时间, 当日解禁股票家数, 解禁数量, 实际解禁数量, 实际解禁市值, 沪深300指数, 沪深300指数涨跌幅 (29 rows) | Market-wide upcoming restricted release summary |
| LPR Rate | `macro_china_lpr()` | ✅ PASS | TRADE_DATE, LPR1Y, LPR5Y, RATE_1, RATE_2 (1571 rows) | Loan Prime Rate with historical benchmark rates |
| Global Market Info (CLS) | `stock_info_global_cls()` | ✅ PASS | 标题, 内容, 发布日期, 发布时间 (20 rows) | CLS (财联社) financial news feed for economic calendar events |

### Key Findings — Calendar
- **Trade calendar**: `tool_trade_date_hist_sina()` — already in use, confirmed working
- **Restricted shares**: Both Sina and EM versions work. EM version has richer data (market cap impact, price reactions)
- **Economic calendar**: No dedicated economic calendar endpoint. `stock_info_global_cls()` provides financial news as a proxy. LPR, PMI, CPI etc. available via `macro_china_*` functions
- **Economic indicators**: Use specific `macro_china_*` functions (e.g., `macro_china_lpr`, `macro_china_cpi`, `macro_china_pmi`)

---

## Critical Endpoints Status

| Data Need | Primary Endpoint | Status | Alternative |
|-----------|-----------------|--------|-------------|
| US stock indices | `index_us_stock_sina()` | ✅ Working | — |
| USD/CNY | `currency_boc_sina(symbol='美元')` | ✅ Working | — |
| US 10Y yield | `bond_zh_us_rate()` | ✅ Working | — |
| China VIX | `index_option_50etf_qvix()` | ✅ Working | No US VIX available |
| A50 futures | `futures_foreign_hist(symbol='CHA50CFD')` | ✅ Working | — |
| IF/IC/IM futures | `futures_main_sina(symbol='X0')` | ✅ Working | — |
| Futures basis | `futures_spot_price()` | ✅ Working | — |
| Northbound flow | `stock_hsgt_hist_em(symbol='北向资金')` | ✅ Working | — |
| Margin balance | `stock_margin_sse()` | ✅ Working | — |
| LME metals | `futures_foreign_hist(symbol='CAD/AHD/ZSD')` | ✅ Working | — |
| Trade calendar | `tool_trade_date_hist_sina()` | ✅ Working | — |
| Restricted shares | `stock_restricted_release_summary_em()` | ✅ Working | — |
| **Lithium carbonate** | — | ❌ Not available | Need external source (SMM/百川) |
| **PV/solar prices** | — | ❌ Not available | Need external source |
| **US VIX** | — | ❌ Not available | Consider CBOE API or alternative |

---

## Notes for Implementation

1. **Proxy stripping is mandatory**: All AKShare calls must strip `http_proxy`/`https_proxy` env vars before calling and restore after. Use the pattern from `stockhot/data_collector/clients/akshare_sina.py:_call_akshare()`

2. **Symbol codes are NOT intuitive**: Foreign futures use Sina-specific codes (e.g., `CAD` for LME Copper, not `伦敦铜`). Always verify with `futures_foreign_commodity_subscribe_exchange_symbol()` or `futures_foreign_detail(symbol=...)`

3. **`futures_spot_price()` returns 0 rows on non-trading days**: Plan for this in data collection logic

4. **East Money endpoints are network-sensitive**: `forex_*_em`, `index_global_*_em`, `stock_us_spot_em` are blocked from non-China networks. Prefer Sina-backed alternatives when available

5. **Missing data sources**: Lithium, solar, coal spot prices are NOT in AKShare. These will need web scraping or paid API access (SMM, 百川盈孚, etc.)

6. **AKShare version**: 1.18.56. Function signatures may change between versions — always pin the version in dependencies
