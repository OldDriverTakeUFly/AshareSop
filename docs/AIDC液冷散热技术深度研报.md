# AIDC液冷散热技术深度研报

> 研究日期：2026年5月31日 | 研究员：AI Librarian

---

## 目录

1. [液冷技术完整分类体系](#一液冷技术完整分类体系)
2. [服务器液冷需求与技术方案](#二服务器液冷需求与技术方案)
3. [电源系统液冷需求与技术方案](#三电源系统液冷需求与技术方案)
4. [服务器液冷 vs 电源液冷：技术差异分析](#四服务器液冷-vs-电源液冷技术差异分析)
5. [英伟达GB200/Rubin液冷架构深度分析](#五英伟达gb200rubin液冷架构深度分析)
6. [液冷 vs 风冷对比与演进趋势](#六液冷-vs-风冷对比与演进趋势)
7. [冷却液技术与供应链](#七冷却液技术与供应链)
8. [CDU（冷量分配单元）市场分析](#八cdu冷量分配单元市场分析)
9. [投资要点总结](#九投资要点总结)
10. [数据来源索引](#十数据来源索引)

---

## 一、液冷技术完整分类体系

### 1.1 总体分类框架

液冷技术按照冷却液是否与电子元件直接接触，可分为**接触式**和**非接触式**两大类：

| 类别 | 技术 | 冷却液接触元件 | 典型方案 |
|------|------|:---:|------|
| **非接触式** | 冷板式液冷（Cold Plate / DLC） | ✗ | 微通道铜冷板 + 水乙二醇 |
| **接触式** | 单相浸没式 | ✓ | 介电液体（合成油/氟化液） |
| **接触式** | 两相浸没式 | ✓ | 低沸点氟化液（相变） |
| **接触式** | 喷淋式液冷 | ✓ | 介电液体喷射 |
| **辅助** | 背门热交换器（RDHx） | ✗ | 水冷盘管替代机柜后门 |

### 1.2 冷板式液冷（Direct-to-Chip / DLC）

**原理**：将铜制微通道冷板（Cold Plate）直接贴附在GPU/CPU等高功耗芯片表面，冷却液（通常为水乙二醇溶液）在冷板内部微通道中流动，通过对流传热带走热量。冷却液不直接接触电子元件。

**关键技术参数**：

| 参数 | 数值 |
|------|------|
| 热阻 | 0.02–0.10 °C/W |
| 可处理芯片功耗 | 500–2000W/芯片 |
| 最大机柜功率密度 | 60–132 kW/柜 |
| 可实现PUE | 1.05–1.15 |
| GPU结温控制 | 50–65°C（风冷为85–95°C） |
| 液冷覆盖热量比例 | 70–80%（其余仍需风冷辅助） |

**优缺点**：

- ✅ 技术成熟度最高，产业链最完善
- ✅ 与现有服务器架构兼容性好，改造难度低
- ✅ 单相冷板在液冷数据中心应用占比超90%
- ✅ 可与传统风冷混合部署
- ❌ 仅覆盖GPU/CPU等主要热源，内存、网络等仍需风冷
- ❌ 漏液风险（虽然可控）
- ❌ 盲插接头（QD）要求精密

**成本**：CapEx约$2,000–$4,000/kW，机柜级$5,000–$15,000/柜（不含CDU）

**数据来源**：[ToneCooling](https://tonecooling.com/direct-to-chip-cooling-vs-air-vs-immersion/), [Ecotherm](https://ecothermgroup.com/the-liquid-revolution-liquid-cooling-for-ai-servers-in-the-high-tdp-era/), [Castle Rock Digital](https://www.castlerockdigital.com/insights/air-vs-liquid-vs-immersion-cooling-ai-data-centers)

### 1.3 浸没式液冷（Immersion Cooling）

#### 1.3.1 单相浸没式（Single-Phase Immersion）

**原理**：将整个服务器完全浸没在介电冷却液（绝缘、不腐蚀）中。冷却液吸收热量后保持液态（不发生相变），泵送到热交换器冷却后回流。

**关键参数**：

| 参数 | 数值 |
|------|------|
| 最大机柜功率密度 | 100–200+ kW/柜 |
| 可实现PUE | 1.02–1.08 |
| GPU结温控制 | 45–55°C |
| 热流密度 | 可达1000W/cm² |
| 冷却液类型 | 合成烃/矿物油/氟化液 |
| 风扇需求 | 无（完全消除） |

**冷却液选择**：
- **合成油/矿物油**：成本低（80–120元/kg），国内主导方案，润禾材料第三代改性硅油热导率>6W/mK
- **氟化液**：性能最优但价格高（300–400元/kg），3M Novec系列曾是主流，3M退出后由巨化股份、新宙邦等国产替代

#### 1.3.2 两相浸没式（Two-Phase Immersion）

**原理**：使用低沸点介电冷却液，冷却液在芯片表面受热蒸发（相变），吸收大量潜热，蒸气上升至冷凝器重新液化回流。

**关键参数**：

| 参数 | 数值 |
|------|------|
| 最大机柜功率密度 | 250+ kW/柜 |
| 可实现PUE | 1.01–1.05 |
| GPU结温控制 | 40–50°C |
| 冷却液类型 | 氟化液（Novec 7100/7200等） |
| 潜热利用率 | 远高于显热（单相） |

**优缺点**：
- ✅ 散热效率最高，PUE最低
- ✅ 完全消除风扇和空调
- ❌ PFAS毒性风险（氟化液环保争议）
- ❌ 冷却液成本极高
- ❌ 密封系统维护复杂
- ❌ 蒸发损耗需要补充

**数据来源**：[Castle Rock Digital](https://www.castlerockdigital.com/insights/air-vs-liquid-vs-immersion-cooling-ai-data-centers), [Savrn](https://savrn.com/liquid-cooling-for-ai/)

### 1.4 喷淋式液冷（Spray / Jet Impingement）

**原理**：将介电冷却液通过喷嘴精确喷射到高热元件表面，利用射流冲击（Jet Impingement）进行高效散热。

**关键特点**：
- 可分为单相喷淋和相变喷淋
- 热流密度极高（精准定点冷却）
- 冷却液用量远少于浸没式（约减少80%）
- 技术难度高，尚未大规模商用

**代表产品**：AIRSYS LiquidRack——全球首个服务器级喷淋液冷解决方案，喷头3D打印定制适配热源分布。

**数据来源**：[AIRSYS](https://airsysnorthamerica.com/immersion-or-liquid-spray-cooling-for-ai-data-centers-which-fits-best/)

### 1.5 背门热交换器（RDHx）

**原理**：将机柜后门替换为水冷盘管热交换器，服务器排出的热空气经过盘管时被冷却水带走热量。

**关键参数**：
- 最大机柜功率密度：30–50 kW/柜
- PUE：1.2–1.4
- 改造复杂度：中等
- 最适合混合密度机柜和过渡方案

### 1.6 各技术散热能力与成本综合对比

| 指标 | 风冷 | 冷板式DLC | RDHx | 单相浸没 | 两相浸没 | 喷淋式 |
|------|------|------|------|------|------|------|
| **最大机柜密度(kW)** | 15–25 | 80–132 | 30–50 | 100–200+ | 250+ | 100+ |
| **PUE** | 1.3–1.6 | 1.05–1.15 | 1.2–1.4 | 1.02–1.08 | 1.01–1.05 | ~1.05 |
| **GPU结温(°C)** | 85–95 | 50–65 | 75–85 | 45–55 | 40–50 | 45–55 |
| **CapEx/kW** | 低 | 中高 | 中 | 高 | 极高 | 中高 |
| **OpEx/kW** | 高 | 低 | 中 | 极低 | 最低 | 低 |
| **技术成熟度** | 成熟 | 最成熟 | 成熟 | 新兴 | 实验性 | 早期 |
| **改造复杂度** | 基准 | 高 | 中 | 极高 | 极高 | 中 |
| **适用场景** | 传统IT | AI训练 | 混合柜 | 超密HPC | 实验超频 | 新兴方案 |
| **液冷渗透率(2026)** | — | ~90% | — | ~8% | ~2% | <1% |

**数据来源**：[Castle Rock Digital](https://www.castlerockdigital.com/insights/air-vs-liquid-vs-immersion-cooling-ai-data-centers), [ToneCooling](https://tonecooling.com/direct-to-chip-cooling-vs-air-vs-immersion/), [Markets and Markets](https://www.marketsandmarkets.com/Market-Reports/data-center-liquid-cooling-market-84374345.html)

---

## 二、服务器液冷需求与技术方案

### 2.1 GPU液冷：最大驱动力

#### 2.1.1 GPU功耗演进路线

| GPU型号 | 发布年份 | 单颗功耗(TDP) | HBM | 冷却要求 |
|---------|---------|-------------|-----|---------|
| NVIDIA H100 | 2023 | ~700W | HBM3 | 部分可风冷 |
| NVIDIA B200 | 2024 | ~1000W | HBM3e | **必须液冷** |
| NVIDIA GB200模块 | 2024 | ~1200W(2GPU+1CPU) | HBM3e | **必须液冷** |
| NVIDIA Rubin (R100) | 2026 | ~1800–2300W | HBM4 | **100%液冷，无风冷选项** |
| NVIDIA Rubin Ultra | 2027 | 预计更高 | HBM4e | 600kW/柜 |

**数据来源**：[ToneCooling GB200](https://tonecooling.com/nvidia-gb200-nvl72-cooling-requirements/), [Barrack AI](https://blog.barrack.ai/nvidia-rubin-specs-architecture-2026/), [Introl](https://introl.com/blog/nvidia-rubin-full-production-ces-2026-ai-infrastructure)

#### 2.1.2 GB200 NVL72机柜液冷需求

| 参数 | 数值 |
|------|------|
| 机柜配置 | 36 Grace CPU + 72 Blackwell GPU |
| 单机柜功耗 | **132 kW**（峰值~150 kW） |
| GB200模块功耗 | ~1200W（2×B200 GPU + 1×Grace CPU） |
| 芯片热流密度 | 超过50W/cm²，热点可达150W/cm² |
| 温升限制 | GPU<30°C，芯片整体<40°C |
| 需要热阻 | <0.03°C/W |
| 冷板进液温度 | 32–45°C |
| 流量 | ~30–40 L/min/柜 |
| 压降 | 0.5–1.5 bar |
| 冷板材料 | 铜（C1100/C1020），真空钎焊 |
| 快接头标准 | Stäubli/CPC/Parker无滴漏型，>10000次插拔 |

**关键认知**：NVIDIA已确认GB200 NVL72**强制要求液冷**，这不是可选项而是架构需求。B200单芯片功耗~1000W，热流密度超过500W/cm²，任何风冷方案均无法满足。

**数据来源**：[NVIDIA Blog](https://developer.nvidia.com/blog/nvidia-contributes-nvidia-gb200-nvl72-designs-to-open-compute-project/), [KenFa Tech](https://www.kenfatech.com/gb200-liquid-cooling-plate-design/), [howtostoreelectricity](https://howtostoreelectricity.com/gb200-nvl72-liquid-cooling-data-centre/)

#### 2.1.3 Rubin平台液冷需求跃升

| 参数 | Blackwell GB300 NVL72 | Vera Rubin NVL72 |
|------|---------------------|-----------------|
| 单GPU功耗 | ~1000W | ~2300W |
| 机柜功耗 | ~140 kW | **190–230 kW** |
| 冷却方式 | 液冷为主 | **100%液冷，无风冷选项** |
| 进液温度 | 32–45°C | **45°C**（高温回水） |
| 冷却液流量 | 30–40 L/min | 45–60 L/min |
| 架构 | 混合（液冷+风冷辅助） | 完全液冷（包括网络模块） |
| 供电架构 | 48V | 800V DC |

**Rubin的关键变革**：
1. **45°C进液温度**：可利用更高温度的冷却水，大幅延长自然冷却时间，多数气候条件下**无需冷水机组**
2. **100%液冷**：不再需要风扇，机柜内气流需求降低~80%
3. **回水温度高达65°C**：可直接用于区域供暖
4. **Rubin Ultra（2027）目标600kW/柜**：需要更先进的两相液冷技术

**数据来源**：[gandgcontrols](https://www.gandgcontrols.co.uk/post/data-centre-cooling-for-vera-rubin), [AI Consulting Network](https://www.theaiconsultingnetwork.com/blog/nvidia-vera-rubin-nvl72-liquid-cooling-data-center-cre-investors-2026), [Arc Compute](https://www.arccompute.io/arc-blog/beyond-blackwell-preparing-enterprise-data-centers-for-the-nvidia-rubin-architecture-and-the-hbm-crunch)

### 2.2 CPU液冷

- Intel Xeon高功耗型号TDP可达350–400W
- AMD EPYC高功耗型号TDP可达400W
- 在GB200架构中，Grace CPU功耗约200W，集成在同一冷板组件中

### 2.3 内存/HBM液冷

- HBM3e单颗功耗约30–50W
- 在GB200中HBM集成在GPU封装内，与GPU共用冷板
- 高带宽内存的热管理随GPU功耗提升而日益重要

### 2.4 网络交换机液冷

- NVSwitch ASIC功耗约50–100W/颗
- GB200 NVL72中9个NVSwitch托盘，部分设计中与计算托盘共用液冷回路
- Rubin架构中NVLink Switch也需液冷

### 2.5 服务器液冷的技术挑战

| 挑战 | 描述 | 影响程度 |
|------|------|---------|
| **漏液风险** | 冷却液泄漏可能导致短路和硬件损坏 | 高（但可控） |
| **快接头（QD）可靠性** | 盲插接头需>10000次插拔寿命，无滴漏 | 高 |
| **维护成本** | 液冷系统需定期冷却液更换、泄漏检查 | 中 |
| **流量均衡** | 36个冷板并联需±5%流量均衡 | 高 |
| **重量** | 满载液冷机柜>3500磅，超传统架空地板承载 | 高 |
| **培训** | 运维人员需液冷专项培训 | 中 |

---

## 三、电源系统液冷需求与技术方案

### 3.1 电源系统液冷概述

电源系统液冷是AIDC液冷中**被严重低估但至关重要的维度**。随着机柜功耗从几十kW跃升至数百kW，电源系统（SST、HVDC整流、UPS、PCS、PSU、BBU）的热管理需求同样剧增。电源液冷与服务器液冷存在根本性的技术差异。

### 3.2 SST固态变压器液冷

**SST背景**：固态变压器（Solid-State Transformer）是下一代AI数据中心的核心电力设备，直接将中压交流电（13.8–34.5kV）转换为800V DC，替代传统变压器+UPS的多级转换架构。

**SST热管理需求**：

| 参数 | 数值 |
|------|------|
| 单机功率 | 200kW–1.25MW |
| 功率密度 | 极高（体积仅为传统变压器的1/15） |
| 转换效率 | 96–99% |
| 热损耗 | 1–4%×额定功率 = 数十kW热耗 |
| 冷却方案 | 室外：强制风冷（IP55）；室内：**液冷（液-空热交换器顶帽）** |

**主要SST厂商的液冷方案**：

| 厂商 | 产品 | 功率 | 冷却方案 | 半导体技术 |
|------|------|------|---------|---------|
| **Enphase** | IQ SST | 1.25MW/柜（342个模块） | 室外风冷/室内液冷选项 | GaN HEMT |
| **DG Matrix** | Interport Flex | 200–400kW/单元，可扩展至MW | **模块化液冷** | Infineon SiC |
| **SolarEdge** | SST | 2–5MW模块化 | 液冷设计 | SiC |
| **Heron Power** | Heron Link | MV级模块化 | 液冷 | HV SiC MOSFET |
| **Wolfspeed生态** | — | — | SiC器件本身散热需求降低50% | 10kV SiC MOSFET |

**SST液冷的核心意义**：
1. SST功率密度极高（15×小型化），散热需求集中
2. SST效率虽高（98.5–99%），但兆瓦级设备仍有10–15kW热耗
3. 室内部署（灰空间）需要液冷避免热量进入白空间
4. Wolfspeed 10kV SiC MOSFET转换效率99%，可减少50%散热系统需求

**数据来源**：[Enphase White Paper](https://enphase.com/download/iq-sst-white-paper), [DG Matrix White Paper](https://media.datacenterdynamics.com/media/documents/Transforming_Data_Centers_into_AI_Factories_Multi-Port_Solid-State_Transformer_OrJ5uMb.pdf), [Power Electronics News](https://www.powerelectronicsnews.com/real-world-solid-state-transformers-overcome-barriers-to-meet-adoption-needs-of-ac-and-dc-networks/), [Wolfspeed](https://www.wolfspeed.com/knowledge-center/article/powering-ai-with-reliable-silicon-carbide-based-solid-state-transformers/)

### 3.3 HVDC整流模块液冷

**背景**：NVIDIA Rubin平台引入800V DC供电架构，OCP Mt. Diablo标准定义±400V DC架构。HVDC整流模块需要从中压AC转换为800V DC。

**散热需求**：
- 800V DC架构可减少5%端到端电力损耗
- HVDC整流模块效率目标>97%，但仍需处理3%×MW级热耗
- SST本质上就是HVDC整流的高级形态

### 3.4 UPS液冷

**背景**：传统UPS在AI数据中心中面临根本性挑战——多级转换效率低（损耗7–13%），而SST架构可将UPS功能集成。

**关键数据**（SST vs 传统UPS效率对比）：
- SST架构30天平均损耗：**1.924%**
- 传统UPS架构30天平均损耗：**9.553%**
- SST比UPS节省约**8.5%**输入能量

**UPS液冷趋势**：
- 传统UPS体积大、效率低，正被SST+分布式BESS替代
- Infineon等厂商提供SiC/GaN器件支持高效率UPS设计
- 未来UPS功能可能被集成到SST中（如DG Matrix Interport平台的内置UPS功能）

**数据来源**：[arXiv SST-800VDC Paper](https://www.arxiv.org/pdf/2601.16502), [Infineon](https://www.infineon.com/applications/ai-data-center/data-center-power-solutions/data-center-power-distribution)

### 3.5 储能PCS液冷

**阳光电源PowerTitan系列——全球储能液冷标杆**：

| 参数 | PowerTitan 2.0 | PowerTitan 3.0 |
|------|---------------|---------------|
| 集成方式 | 2.5MW PCS + 5MWh电池 / 20ft柜 | 12.5MW/50MWh / 30ft柜 |
| PCS类型 | **全液冷SiC PCS** | **全液冷SiC PCS（全球首次大规模量产）** |
| PCS单机功率 | — | 450kW/单元 |
| PCS效率 | 92.5% RTE | **99.3%峰值效率**，93.5% RTE |
| 温差控制 | ≤2.5°C（近5000个电芯） | ±1°C精度 |
| 辅助功耗 | 比传统ESS降低40% | 再降20% |
| 运行温度 | -30~50°C | **-40~55°C** |
| 冷却液 | 液冷（电池+PCS均液冷） | AI仿生热平衡2.0 |

**PCS液冷的关键差异**：
1. **功率级别**：PCS功率450kW/单元，远高于单GPU的1–2kW级别
2. **热耗分布**：PCS热耗集中（SiC器件开关损耗），温度更均匀
3. **环境适应**：PCS需在-40~55°C宽温域运行
4. **冷却介质**：PCS液冷通常使用水乙二醇而非介电液
5. **集成度高**：PCS与电池同柜，液冷系统统一设计

**数据来源**：[Sungrow PowerTitan 2.0 White Paper](https://us.sungrowpower.com/upload/file/20240821/PowerTitan%202%20WhitePaper.pdf), [ESS News](https://www.ess-news.com/2025/06/10/sungrow-introduces-powertitan-3-0-bess-based-on-684-ah-cell-fully-liquid-cooled-silicon-carbide-pcs/), [PR Newswire](https://www.prnewswire.com/apac/news-releases/sungrow-releases-the-groundbreaking-powertitan-3-0-energy-storage-system-platform-302476065.html)

### 3.6 BBU（电池备份单元）液冷

**背景**：
- NVIDIA Rubin Ultra NVL576机柜功耗600kW，需要sidecar电源柜
- BBU为机柜级短时备电，在800V DC架构下需要配合电容备份单元（CBU）
- BBU充放电产生热量需要管理

**趋势**：
- SST方案可减少/消除机柜侧BBU需求（SST响应时间<1ms）
- Enphase IQ SST的亚毫秒级控制响应消除了本地能量缓冲需求
- DG Matrix Interport将电池集成为"一级能源资产"

### 3.7 PSU（服务器电源）液冷

**背景**：
- 传统PSU占机柜空间<10%，但随着机柜功耗从30kW升至>130kW，PSU空间需求大幅增长
- NVIDIA Rubin平台引入sidecar电源柜概念：每个计算机柜配套一个独立的电源柜
- PSU效率通常为90–96%，130kW机柜PSU热耗可达5–13kW

**液冷需求**：
- 800V DC架构下PSU可进一步小型化
- 在液冷机柜中PSU可通过冷板或空气对流冷却
- 高密度PSU（3–5kW/单元）的液冷方案正在开发

---

## 四、服务器液冷 vs 电源液冷：技术差异分析

| 维度 | 服务器液冷（GPU/CPU） | 电源液冷（SST/HVDC/PCS） |
|------|---------------------|------------------------|
| **热流密度** | 极高（500–1500W/cm²芯片级） | 中高（功率器件级分布） |
| **冷却精度** | 结温控制±1–2°C | 温差控制±2.5°C |
| **冷却液类型** | 水乙二醇/去离子水（冷板式）；氟化液（浸没式） | 水乙二醇（主流） |
| **冷却温度** | 进液30–45°C，回液55–65°C | 环境温度到60°C |
| **流量需求** | 2–3 L/min/模块（精细控制） | 大流量（系统级循环） |
| **安全性要求** | 极高（漏水损坏昂贵GPU） | 高（电力设备） |
| **标准化程度** | OCP/ASHRAE标准成熟 | 多样化（各厂商自定义） |
| **维护频率** | 定期检查，冷却液更换 | 长周期维护 |
| **漏液后果** | 可能短路，GPU损坏（万元级/卡） | 设备停机（可切换冗余） |
| **功率级别** | 单柜100–600kW | 单设备200kW–5MW |
| **散热占比** | 机柜功耗100%需要管理 | 设备功耗的1–4%（效率损耗热） |
| **技术路线** | 冷板式为主（>90%） | 强制风冷→液冷过渡中 |

**核心结论**：服务器液冷的核心挑战是**超高热流密度的精确温控**，而电源液冷的核心挑战是**大功率系统的可靠散热与环境适应性**。两者技术路线不同但正在趋同——Rubin Ultra 600kW/柜的需求将迫使电源和计算使用统一的液冷架构。

---

## 五、英伟达GB200/Rubin液冷架构深度分析

### 5.1 GB200 NVL72液冷架构

**NVIDIA ACS参考设计关键要素**：

```
[冷却塔/干冷器] ←→ [设施水回路] ←→ [CDU（ brazed plate HX）] ←→ [机柜分配管] ←→ [36个冷板组件]
```

**详细架构**：

1. **冷板组件**：
   - 每个计算托盘1个冷板（覆盖2×B200 GPU + 1×Grace CPU + ConnectX NIC）
   - 铜制微通道冷板，真空钎焊
   - 翅片厚度≤0.5mm，间距≤0.5mm，翅高≥3mm
   - 接触面平面度≤0.05mm，粗糙度Ra≤0.8μm

2. **机柜分配管（Manifold）**：
   - 盲配液冷分配管（Blind Mate Liquid Cooling Manifold）
   - 浮动盲配托盘连接（Floating Blind Mate Tray connection）
   - 36个分支需±5%流量均衡
   - 设计压力6 bar，爆破压力≥12 bar

3. **CDU**：
   - Vertiv Liebert XDU600（600kW/skid，支持4个NVL72机柜）
   - Chilldyne CDU-300（300kW/单元）
   - Chilldyne CDU-1500（1.5MW/单元，2N配置）
   - 冗余：N+1泵，双回路隔离

4. **快接头（UQD）**：
   - Stäubli、CPC、Parker无滴漏型
   - 10000+插拔寿命
   - 允许单托盘热插拔维护

**数据来源**：[NVIDIA OCP贡献](https://developer.nvidia.com/blog/nvidia-contributes-nvidia-gb200-nvl72-designs-to-open-compute-project/), [NVIDIA DGX GB200 User Guide](https://docs.nvidia.com/dgx/dgxgb200-user-guide/hardware.html), [Chilldyne Reference Design](https://chilldyne.com/wp-content/uploads/2025/03/Chilldyne-Reference-Design-for-AI-NVIDIA-NVL72-X.3.pdf), [SemiAnalysis](https://semianalysis.substack.com/p/gb200-hardware-architecture-and-component)

### 5.2 NVIDIA对液冷供应商的认证要求

| 要求 | 详情 |
|------|------|
| CFD热仿真 | 在开模前验证热性能 |
| 原型交付 | 7–15天（MOQ 5片） |
| 压力测试 | 爆破压力12 bar |
| PPAP Level 3文档 | OEM供应商资质必须 |
| 材料兼容性 | 50/50 EGW冷却液 per ASHRAE W55 |
| 流量均衡验证 | ±5%全分支 |

### 5.3 Rubin NVL72架构升级

**Rubin的革命性变化**：
1. **45°C进液温度** → 可全年自然冷却，无需冷水机组
2. **100%液冷** → 完全消除风扇
3. **800V DC供电** → 减少铜缆重量200kg/柜
4. **模块化托盘设计** → 5分钟安装（Blackwell需2小时）
5. **流量翻倍** → 45–60 L/min/柜（Blackwell为30–40 L/min）

**数据来源**：[NVIDIA Vera Rubin](https://www.nvidia.com/en-eu/data-center/technologies/rubin/), [NVIDIA Blog OCP](https://blogs.nvidia.com/blog/gigawatt-ai-factories-ocp-vera-rubin/)

---

## 六、液冷 vs 风冷对比与演进趋势

### 6.1 风冷的物理极限

| 指标 | 风冷极限 | 说明 |
|------|---------|------|
| 最大机柜密度 | 15–25 kW | 超过后需飓风级风速 |
| PUE下限 | ~1.3 | 无法进一步优化 |
| 噪音 | >90 dB | 风扇全速运转 |
| 水耗 | 高（蒸发冷却） | 2–5百万加仑/MW/年 |
| 每kW冷却成本 | 随密度非线性上升 | 密度>20kW后急剧恶化 |

**关键物理约束**：空气的体积热容约为水的3300分之一，热导率极低。100kW机柜若用风冷，需在机柜内产生飓风级气流。

### 6.2 液冷的功率密度上限

| 技术路线 | 当前上限 | 理论上限 |
|---------|---------|---------|
| 冷板式DLC | ~132kW/柜 | ~200kW/柜 |
| 单相浸没 | ~200kW/柜 | ~300kW/柜 |
| 两相浸没 | ~250kW/柜 | ~500kW+ |
| 两相DTC（2026年预计出现） | — | 可能>500kW |

### 6.3 演进拐点

**"液冷拐点"定义**：10年TCO优势超过CapEx溢价的机柜功率密度阈值。

- 电价$0.12/kWh时：拐点约**30kW/柜**
- 电价$0.15/kWh时：拐点降至**24kW/柜**
- 2026年AI数据中心平均机柜密度约27kW → 已越过拐点

**渗透率数据**：
- 2024年液冷在AI数据中心渗透率：**14%**
- 2025年预计：**24%**
- 2026年预计：**34%**（同比增长118%）
- Goldman Sachs估计2026年底**76%的AI服务器**将采用液冷

**NVIDIA/Vertiv联合研究的量化结果**：
- 从100%风冷过渡到75%液冷：
  - 设施功耗降低**27%**
  - 服务器风扇功耗降低**80%**
  - 总数据中心功耗降低**10.2%**
  - TUE（总使用效率）改善**15.5%**

**数据来源**：[NVIDIA/Vertiv PUE Study](https://www.datacenterdynamics.com/en/opinions/what-happens-when-you-introduce-liquid-cooling-into-an-air-cooled-data-center/), [Schneider Electric](https://blog.se.com/datacenter/2026/05/14/how-liquid-cooling-redefining-data-center-efficiency-beyond-pue/), [Adam Silva Consulting](https://www.adamsilvaconsulting.com/insights/data-center-cooling-economics-2026)

### 6.4 混合冷却方案

**当前主流策略**：液冷+风冷混合部署
- 冷板DLC处理70–80%热量（GPU/CPU）
- 风冷处理20–30%余量（内存、SSD、网络、PSU）
- RDHx作为过渡方案用于混合密度机柜

**演进方向**：Rubin架构100%液冷后，风冷仅用于白空间环境温度控制。

### 6.5 TCO对比（64机柜AI集群10年）

| 指标 | 先进风冷 | 冷板DLC | 单相浸没 |
|------|---------|---------|---------|
| PUE | 1.45–1.60 | 1.10–1.20 | 1.03–1.08 |
| 10年TCO | **$42M** | **$31M** | **$28M** |
| CapEx/kW | $1,800–3,200 | $3,500–5,000 | $4,300–6,500 |
| 占地面积 | 基准 | -30~45% | -60~75% |
| 水耗 | 2–5M加仑/MW/年 | 封闭循环 | 近零 |
| 回收期 | — | ~3年 | ~3年 |

**数据来源**：[Adam Silva Consulting](https://www.adamsilvaconsulting.com/insights/data-center-cooling-economics-2026)

---

## 七、冷却液技术与供应链

### 7.1 冷却液分类

| 冷却液类型 | 主要用途 | 代表产品 | 价格 | 特点 |
|-----------|---------|---------|------|------|
| **水/乙二醇** | 冷板式主流 | 25%PG/EG溶液 | 低 | 高比热、成熟、低成本 |
| **去离子水** | 冷板式备选 | ASHRAE W55标准 | 低 | 最高热导率，需防腐 |
| **氟化液（HFE）** | 浸没式/精密冷却 | 3M Novec 7100/7200 | 极高（300–400元/kg） | 最佳介电性能，低粘度 |
| **全氟聚醚（PFPE）** | 两相浸没/半导体 | 3M FC-3283 | 极高 | 化学惰性，热稳定性 |
| **合成烃/矿物油** | 单相浸没替代 | PAO、改性硅油 | 中（80–120元/kg） | 成本优势，非PFAS |
| **HFO新型环保液** | 两相浸没升级 | Chemours Opteon 2P50 | 高 | 低GWP，环保合规 |

### 7.2 3M退出后的全球供应链重构

**3M退出时间线**：
- 2022年底：3M宣布2025年底前退出所有PFAS产品
- 2025年8月：3M**提前数月**停产所有Novec/Fluorinert系列
- 影响：3M原占电子级氟化液市场约70%份额，对应**百亿级人民币市场重组**

**3M原产能与价格**：年产能约5000–8000吨，单价约50万元/吨

### 7.3 国产替代进展

| 中国企业 | 产品/产能 | 进展 |
|---------|---------|------|
| **巨化股份** | "巨芯"冷却液，5000吨/年规划（一期1000吨已投产） | 最完整产业链（萤石→氟化氢→六氟丙烯→电子级氟化液），产品入选浙江省优秀工业新产品 |
| **新宙邦** | 氟化液产品 | 已在全球主要晶圆厂量产使用多年，新一代冷却液通过验证，2026Q1净利润同比增长>100% |
| **长芦新材料** | 氢氟醚7100产品，300吨/年+2000吨/年二期 | 2026年2月一次性投料试车成功，打破国外技术垄断 |
| **东阳光** | 通过并购大图热控 | 大图热控是首家获得OCP认证的中国液冷供应商，2026年海外营收占比预计40% |
| **昊华科技** | R134A等制冷剂 | HFC-245fa制冷剂通过NVIDIA GB200兼容性测试 |
| **润禾材料** | 第三代改性硅油 | 热导率>6W/mK，通过阿里云、字节跳动验证，成本仅为氟化液1/4 |
| **中化蓝天** | HFE 7100产品 | 已获Meta等国际企业验证 |

**国产替代率预测**：
- 当前国产高端电子级氟化液市场占有率：<10%
- 预计2–3年后提升至：**40%以上**
- 硅油/氟化液整体国产替代率2026年预计超**70%**

**数据来源**：[Futunn/China Galaxy Securities](https://news.futunn.com/en/post/69893974/china-galaxy-securities-aigc-and-new-energy-drive-the-upward), [163.com](https://www.163.com/dy/article/KSGI6OIN05568W0A.html), [Toutiao](https://www.toutiao.com/article/7539210692939792911/)

### 7.4 冷却液成本占比

- 冷板式液冷系统中冷却液成本占比约**5–10%**
- 浸没式液冷系统中冷却液成本占比**25–35%**（大量填充液）
- 单柜浸没式用量：氟化液约400–500L，硅油约1.5–2吨
- 冷却液更换周期：3–5年（浸没式），5–10年（冷板式）

---

## 八、CDU（冷量分配单元）市场分析

### 8.1 CDU的功能与技术要求

CDU是液冷系统的**核心枢纽**，连接IT设备冷却回路与设施冷却回路：

**核心功能**：
- 热交换（brazed plate HX隔离一/二次回路）
- 泵送（冗余泵+自动切换）
- 温度控制（维持GPU进液温度）
- 压力控制（6–8 bar一次回路）
- 泄漏检测与紧急关断
- 流量监控与均衡

### 8.2 CDU功率范围与分类

| CDU类型 | 功率范围 | 部署方式 | 适用场景 |
|---------|---------|---------|---------|
| 机柜级CDU | 50–200kW | 底部/顶部安装 | 单机柜/小集群 |
| 行级CDU | 200–600kW | 行间独立柜 | 4–8机柜行 |
| 设施级CDU | 600kW–2MW+ | 独立机房/模块化 | 大规模集群 |

**GB200 NVL72典型配置**：
- 单机柜CDU：150–160kW/柜（含15–20%余量）
- 多机柜CDU：Vertiv XDU600（600kW/skid，4个NVL72机柜）
- 大型CDU：Chilldyne CDU-1500（1.5MW），Delta L2L CDU（2000kW）
- DCX ECDU系列：600kW–2.6MW

### 8.3 CDU市场规模

| 指标 | 数值 |
|------|------|
| 2025年市场规模 | ~$2.4B |
| 2026年市场规模 | ~$2.88B |
| 2035年预测 | ~$14.61B |
| CAGR（2026–2035） | ~19.8% |

**按技术路线市场份额（2025）**：
- Direct-to-Chip CDU：40%
- 浸没式CDU：30%
- RDHx：30%

**按冷却容量（2026）**：
- 200–500kW：48%（最大份额）
- >1MW：增速最快（CAGR 23.1%）

### 8.4 CDU供应商格局

| 供应商 | 代表产品 | 特点 |
|--------|---------|------|
| **Vertiv** | Liebert XDU系列（XDU600） | 最完整端到端方案，GB200最大份额 |
| **Schneider Electric** | 液冷CDU+EcoStruxure | 收购Motivair ~$850M |
| **CoolIT Systems** | DLC系统 | Dell合作伙伴 |
| **Submer** | Stargate-1 CDU | 欧洲市场强势 |
| **nVent** | 液冷方案 | 北美市场 |
| **JetCool** | SmartPlate | 射流冷板技术，高端溢价 |
| **Chilldyne** | CDU-300/1500 | 负压防漏技术 |
| **Eaton** | 模块化液冷 | GB200即插即用方案 |
| **Delta（台达）** | L2L CDU 2000kW | 全球首款2000kW CDU |
| **DCX** | ECDU 600kW–2.6MW | 2026年3月发布 |

**CDU在液冷系统成本中占比约15–20%**

**CDU单价参考**：
- 约250–350 EUR/kW热移除能力（Vertiv典型项目规模）
- JetCool高端方案约350–450 EUR/kW

**数据来源**：[Precedence Research](https://www.precedenceresearch.com/data-center-cdu-market), [Fortune Business Insights](https://www.fortunebusinessinsights.com/coolant-distribution-units-market-115841), [Persistence Market Research](https://www.persistencemarketresearch.com/market-research/coolant-distribution-unit-cdu-market.asp), [Fact.MR](https://www.factmr.com/report/coolant-distribution-units-market)

---

## 九、投资要点总结

### 9.1 核心结论

1. **液冷已跨过拐点，进入加速渗透期**：2026年AI机柜平均密度27kW已超过30kW TCO拐点，Goldman Sachs预计2026年底76% AI服务器将液冷

2. **冷板式DLC是当前绝对主流**：占液冷数据中心应用>90%，Rubin之前不会改变

3. **英伟达是最大驱动力**：GB200强制液冷（132kW/柜）→ Rubin 100%液冷（190–230kW/柜）→ Rubin Ultra（600kW/柜），每一代需求翻倍

4. **电源液冷是被低估的增量**：SST（1.25MW/柜）、PCS（450kW/单元全液冷SiC）、800V DC架构都在驱动电源系统液冷需求

5. **3M退出创造百亿替代空间**：巨化股份、新宙邦、长芦新材料等国产替代加速

6. **CDU是液冷系统核心枢纽**：市场规模2025年$2.4B→2035年$14.61B，CAGR 19.8%

### 9.2 投资主线

| 投资方向 | 核心逻辑 | 关注标的维度 |
|---------|---------|------------|
| **冷板/液冷基础设施** | NVIDIA强制液冷驱动 | 冷板制造商、液冷集成商 |
| **CDU** | 液冷系统核心，高壁垒 | Vertiv、台达、英维克等 |
| **快接头（QD/UQD）** | 高可靠性要求，高毛利 | 精密连接器企业 |
| **冷却液国产替代** | 3M退出百亿缺口 | 巨化股份、新宙邦、昊华科技 |
| **电源液冷（SST/PCS）** | 被低估的增量市场 | 阳光电源（PCS液冷标杆）、SST生态 |
| **SiC功率器件** | SST/PCS核心使能技术 | Wolfspeed、Infineon、意法半导体 |
| **浸没式液冷** | 远期方向（>200kW/柜） | 浸没式方案商 |

### 9.3 关键风险提示

1. **技术路线风险**：Rubin Ultra 600kW/柜可能需要两相液冷，技术路线存在不确定性
2. **PFAS监管风险**：氟化液面临全球PFAS限制，影响两相浸没方案
3. **供应链集中度**：CDU和冷板供应商集中，可能出现产能瓶颈
4. **数据中心建设周期**：液冷改造周期12–18个月，可能延迟Rubin部署
5. **竞争格局变化**：喷淋式等新技术可能改变冷板式主导地位

---

## 十、数据来源索引

### 行业报告与市场研究
- [Markets and Markets: Data Center Liquid Cooling Market 2026–2033](https://www.marketsandmarkets.com/Market-Reports/data-center-liquid-cooling-market-84374345.html) — 全球液冷市场$4.07B→$27.65B，CAGR 31.5%
- [Precedence Research: Data Center CDU Market 2026–2035](https://www.precedenceresearch.com/data-center-cdu-market) — CDU市场$2.4B→$14.61B，CAGR 19.8%
- [Fortune Business Insights: CDU Market 2026–2034](https://www.fortunebusinessinsights.com/coolant-distribution-units-market-115841) — CDU市场$2.24B→$7.38B
- [Persistence Market Research: CDU Market 2026–2033](https://www.persistencemarketresearch.com/market-research/coolant-distribution-unit-cdu-market.asp) — CDU市场$1.9B→$6.1B，CAGR 18.2%
- [IDTechEx: Thermal Management for Data Centers 2026–2036](https://www.idtechex.com/en/research-report/thermal-management-for-data-centers/1128)
- [ScienceDirect: AI-driven cooling technologies review](https://www.sciencedirect.com/science/article/pii/S221313882500342X)

### 技术分析
- [Castle Rock Digital: Air vs Liquid vs Immersion Comparison](https://www.castlerockdigital.com/insights/air-vs-liquid-vs-immersion-cooling-ai-data-centers) — 最全面的风冷/液冷/浸没对比
- [ToneCooling: DTC vs Air vs Immersion 2026](https://tonecooling.com/direct-to-chip-cooling-vs-air-vs-immersion/) — 冷板技术参数详解
- [ToneCooling: GB200 NVL72 Cooling Requirements](https://tonecooling.com/nvidia-gb200-nvl72-cooling-requirements/) — GB200冷板设计规范
- [KenFa Tech: GB200 Liquid Cooling Plate Design](https://www.kenfatech.com/gb200-liquid-cooling-plate-design/) — 冷板微通道设计原理
- [Ecotherm: Liquid Revolution for AI Servers](https://ecothermgroup.com/the-liquid-revolution-liquid-cooling-for-ai-servers-in-the-high-tdp-era/) — 液冷技术分类
- [Savrn: Liquid Cooling for AI 2026 Playbook](https://savrn.com/liquid-cooling-for-ai/) — 四种液冷架构对比
- [Compute Forecast: Cold Plates vs Immersion](https://www.computeforecast.com/long-reads/cold-plates-vs-immersion-ai-data-centers-cooling/) — 冷板vs浸没运维分析

### NVIDIA GB200/Rubin架构
- [NVIDIA Blog: GB200 NVL72 OCP Contribution](https://developer.nvidia.com/blog/nvidia-contributes-nvidia-gb200-nvl72-designs-to-open-compute-project/) — GB200液冷架构官方设计
- [NVIDIA DGX GB200 User Guide](https://docs.nvidia.com/dgx/dgxgb200-user-guide/hardware.html) — 硬件架构
- [NVIDIA GB200 NVL72 Official](https://www.nvidia.com/en-us/data-center/gb200-nvl72/) — 官方规格
- [NVIDIA Blog: Gigawatt AI Factories for Vera Rubin](https://blogs.nvidia.com/blog/gigawatt-ai-factories-ocp-vera-rubin/) — Rubin液冷45°C设计
- [NVIDIA Vera Rubin Platform](https://www.nvidia.com/en-eu/data-center/technologies/rubin/) — Rubin官方规格
- [SemiAnalysis: GB200 Hardware Architecture](https://semianalysis.substack.com/p/gb200-hardware-architecture-and-component) — GB200 BOM深度拆解
- [Chilldyne: GB200 NVL72 Reference Design](https://chilldyne.com/wp-content/uploads/2025/03/Chilldyne-Reference-Design-for-AI-NVIDIA-NVL72-X.3.pdf) — NVL72液冷参考设计
- [Leviathan Systems: Liquid Cooling for GPU Data Centers](https://www.leviathansystems.co/blog/liquid-cooling-gpu-data-centers) — CDU和管路设计

### 电源系统（SST/HVDC/PCS）
- [Enphase: IQ SST White Paper](https://enphase.com/download/iq-sst-white-paper) — 1.25MW GaN SST，GaN BDS技术
- [DG Matrix: Multi-Port SST White Paper](https://media.datacenterdynamics.com/media/documents/Transforming_Data_Centers_into_AI_Factories_Multi-Port_Solid-State_Transformer_OrJ5uMb.pdf) — 多端口SST架构
- [Power Electronics News: Real-World SSTs](https://www.powerelectronicsnews.com/real-world-solid-state-transformers-overcome-barriers-to-meet-adoption-needs-of-ac-and-dc-networks/) — SST厂商全景
- [Wolfspeed: SiC-based SST](https://www.wolfspeed.com/knowledge-center/article/powering-ai-with-reliable-silicon-carbide-based-solid-state-transformers/) — SiC在SST中的应用
- [Infineon: Data Center Power Distribution](https://www.infineon.com/applications/ai-data-center/data-center-power-solutions/data-center-power-distribution) — SST/UPS/冷却全景
- [arXiv: SST-driven 800V DC Architecture](https://www.arxiv.org/pdf/2601.16502) — SST vs UPS效率对比

### 储能PCS液冷
- [Sungrow PowerTitan 2.0 White Paper](https://us.sungrowpower.com/upload/file/20240821/PowerTitan%202%20WhitePaper.pdf) — 全液冷ESS
- [ESS News: PowerTitan 3.0](https://www.ess-news.com/2025/06/10/sungrow-introduces-powertitan-3-0-bess-based-on-684-ah-cell-fully-liquid-cooled-silicon-carbide-pcs/) — 全球首个量产全液冷SiC PCS
- [PR Newswire: PowerTitan 3.0 Launch](https://www.prnewswire.com/apac/news-releases/sungrow-releases-the-groundbreaking-powertitan-3-0-energy-storage-system-platform-302476065.html)

### 冷却液与国产替代
- [China Galaxy Securities: 液冷散热景气上行](https://news.futunn.com/en/post/69893974/china-galaxy-securities-aigc-and-new-energy-drive-the-upward) — 国产替代全景
- [163.com: 3M退场百亿缺口](https://www.163.com/dy/article/KSGI6OIN05568W0A.html) — 3M退出影响分析
- [Toutiao: 液冷冷却液概念股](https://www.toutiao.com/article/7539210692939792911/) — 产业链标的梳理

### 液冷vs风冷经济学
- [NVIDIA/Vertiv PUE Study (Data Center Dynamics)](https://www.datacenterdynamics.com/en/opinions/what-happens-when-you-introduce-liquid-cooling-into-an-air-cooled-data-center/) — 首个大规模液冷PUE影响研究
- [Schneider Electric: Liquid Cooling Beyond PUE](https://blog.se.com/datacenter/2026/05/14/how-liquid-cooling-redefining-data-center-efficiency-beyond-pue/) — PCE/WUE新指标
- [Adam Silva Consulting: Cooling Economics 2026](https://www.adamsilvaconsulting.com/insights/data-center-cooling-economics-2026) — 10年TCO对比
- [Eaton: Air vs Liquid Cooling](https://www.eaton.com/us/en-us/markets/data-centers/data-center-cooling/efficiency/energy-consumption-in-data-centers-air-versus-liquid-cooling.html) — 能耗对比
- [The Diligence Stack: Liquid Cooling Constraint](https://www.thediligencestack.com/p/liquid-cooling-the-thermal-prerequisite) — 投资视角分析
- [Delta OCP 2025](https://www.delta-americas.com/en-us/news/delta-to-demonstrate-seamlessly-integrated-high-voltage-dc-power,-advanced-cooling,-and-networking-solutions-to-drive-ai-data-center-evolution-at-ocp-global-summit-2025) — 2000kW CDU发布

---

> **免责声明**：本研报仅供投资研究参考，不构成投资建议。数据来源于公开信息，已尽可能标注来源，但不对数据准确性做出保证。

---

Ultraworked with [Sisyphus](https://github.com/code-yeongyu/oh-my-openagent)
Co-authored-by: Sisyphus <clio-agent@sisyphuslabs.ai>
