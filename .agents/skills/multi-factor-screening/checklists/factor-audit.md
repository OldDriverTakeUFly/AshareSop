# Factor Audit Checklist

在三层选股管线执行完成后、输出排名清单前使用此清单。验证五大因子族覆盖、分域权重分配、硬过滤规则均已正确落地。

## 第一层：硬过滤（Hard Filter）规则验证

- [ ] 排除了 ST、*ST 及最近一年被交易所严重警示的个股
- [ ] 检查了日均成交额，排除了流动性不足的标的（域相关阈值）
- [ ] 验证 ROE（TTM）≥ 12% 硬过滤条件已生效
- [ ] 验证收入 3 年 CAGR ≥ 15% 硬过滤条件已生效
- [ ] 验证净债务 / EBITDA < 2.5 杠杆过滤已生效（金融行业除外）
- [ ] 验证经营性现金流（TTM）> 0 已生效
- [ ] 验证 PE（TTM）< 25 且非负的估值过滤已生效
- [ ] 周期型域：PE 过滤已替换为 PB 过滤（PB 低于行业中位数）
- [ ] 价值型域：PE 上限放宽至 30，但质量过滤收紧
- [ ] 硬过滤输出为二元 pass/fail，无部分通过

## 第二层：打分（Scoring）因子族覆盖验证

### 五大因子族权重核对

- [ ] Growth 因子（30%）已配置：ROE（TTM）、收入 3 年 CAGR、毛利率同比变化
- [ ] Quality 因子已折叠进 Growth 桶（不独立计权）：经营现金流比率、应计项目比率、杠杆稳定性
- [ ] 红利型域 Growth 桶已接入真实 dividend_score（davis_analyzer DividendSignal），替换原 prosperity 近似
- [ ] Valuation 因子（20%）已配置：PE（TTM）倒数、EV/EBITDA、PB 百分位
- [ ] Technical 因子（25%）已配置：真实价格动量（davis_analyzer MomentumSignal.momentum_score，多窗口 60/120/250d + 行业内 RS），momentum 缺失时回退到 prosperity 近似
- [ ] Capital Sentiment 因子（25%）已配置：北向资金流向、融资余额变化、大宗交易折溢价
- [ ] 四桶权重合计 = 100%（30% + 20% + 25% + 25%）

### 0/1/2 三档打分法验证

- [ ] 每个因子均在行业内排序（非全市场排序）
- [ ] 行业后 50% 映射为 0 分
- [ ] 行业中位数到 80 分位映射为 1 分
- [ ] 行业前 20% 映射为 2 分
- [ ] 负向因子（PE、EV/EBITDA、换手率、杠杆）映射已反转
- [ ] 综合分 = growth×0.30 + valuation×0.20 + technical×0.25 + sentiment×0.25

### 因子预处理验证

- [ ] 缺失值已向前填充；核心因子全缺的股票已移出候选池
- [ ] 极端值已做 MAD 缩尾处理（5 倍 MAD 上限）
- [ ] 标准化在行业内完成后映射到 0/1/2 三档
- [ ] 行业中性化已执行（所有打分在行业组内进行）

## 第三层：分域选股（Domain-Specific Selection）权重验证

### 四域分类核对

- [ ] 每只股票均已归入以下四域之一：红利型 / 成长型 / 价值型 / 周期型
- [ ] 红利型域权重：Growth 20% / Valuation 25% / Technical 25% / Sentiment 30%
- [ ] 成长型域权重：Growth 40% / Valuation 15% / Technical 20% / Sentiment 25%
- [ ] 价值型域权重：Growth 25% / Valuation 35% / Technical 20% / Sentiment 20%
- [ ] 周期型域权重：Growth 20% / Valuation 30%（PB 基准）/ Technical 25% / Sentiment 25%
- [ ] 四域分别排名，不混合跨域综合分

## 第四层：加分层（Enhancement）验证

- [ ] 业绩预告强指引信号（+5%）：来源为 davis_analyzer ForecastSignal.leading_score ≥ 75（机构级前瞻指引）
- [ ] 机构加仓 / 筹码集中信号（+5%）：来源为 davis_analyzer HolderConcentration.trend == "集中(动能增强)"
- [ ] 高管/大股东增持信号（+3%）：来源为交易所披露文件
- [ ] ESG 评级 A 级以上（+2%）：来源为第三方 ESG 评级
- [ ] 上下游价差信号（+2%）：来源为产业链价格数据
- [ ] 加分项仅在信号存在时加，信号缺位不减分
- [ ] 加分总额上限为综合分的 10%

## 输出完整性抽查

- [ ] 每个域输出独立排名清单，各取前 N 名
- [ ] 每只候选标的附带：域归属、四桶因子分、综合分、加分项明细
- [ ] 无跨域混合排名
- [ ] 已知因子衰减已在输出注释中标注（如有）
