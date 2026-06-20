# technical_analyzer — 技术分析引擎

## 模块定位

技术分析引擎，为个股提供标准化的技术指标计算与综合技术评分。作为
`stockhot` 后端的技术面数据层，供投资 SOP、研报生成等上游模块调用。

## 指标清单（已锁定）

| 分类 | 函数 | 输出 |
|------|------|------|
| 数据获取 | `fetch_ohlcv` | OHLCV DataFrame |
| 均线趋势 | `ma` | MA 序列 (MA5/10/20/60) |
| 超买超卖 | `rsi` | RSI 序列 [0, 100] |
| 趋势动量 | `macd` | DIF / DEA / HIST |
| 随机指标 | `kdj` | K / D / J |
| 波动通道 | `bollinger` | 上轨 / 中轨 / 下轨 |
| 结构分析 | `support_resistance` | 支撑位 / 阻力位 |
| 量价分析 | `volume_price_analysis` | 量价关系 dict |
| 综合评分 | `composite_technical_score` | state + score + signals |

共 9 个函数，**指标集已锁定，不再扩展**。

## 不做的事

- **不依赖 TA-Lib** — 使用 pandas-ta 0.4.71b0 + 原生 pandas/numpy 实现
- **不做可视化** — 无 matplotlib、无 HTML、无前端图表
- **不额外增加指标** — 不包含 WR / CCI / OBV / ATR / DMI
- **不做选股** — 仅对单只标的计算技术面，选股逻辑由上游模块处理

## 数据格式约定

所有指标函数的 DataFrame 输入必须满足：

- **列名**：英文 `open, high, low, close, volume`
- **索引**：`pandas.DatetimeIndex`（日频）
- **排序**：升序（最旧在前，最新在后）

`fetch_ohlcv` 负责从 AKShare 获取并转换为上述标准格式。

## 开发路线

| 任务 | 内容 | 状态 |
|------|------|------|
| T1 | 脚手架 + pandas-ta 依赖 | 完成 |
| T2 | 契约冻结 (`contract.py`) | 完成 |
| T7 | data_loader (`fetch_ohlcv`) | 待实现 |
| T11 | 趋势/动量指标 (MA/RSI/MACD/KDJ/Bollinger) | 待实现 |
| T12 | 结构/综合分析 (support_resistance/volume_price/composite) | 待实现 |

## 运行测试

```bash
python -m pytest stockhot/technical_analyzer/tests/ -v
```
