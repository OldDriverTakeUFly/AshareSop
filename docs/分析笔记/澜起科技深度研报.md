# 澜起科技深度研究：三大核心赛道全景分析

> 分析时间：2026 年 5 月 | 数据来源：公司年报、JEDEC 官方公告、Astera Labs SEC 文件、SemiAnalysis、Frost & Sullivan、多家券商研报

---

## 一、CXL 生态系统竞争格局

### 1.1 CXL 协议演进路线

| 版本 | 物理层 | 双向带宽 (x16) | 核心特性 | 部署状态 |
|------|--------|---------------|---------|---------|
| CXL 2.0 | PCIe 5.0 (32 GT/s) | ~64 GB/s | 单级交换、内存池化、持久内存 | 2024-2025 主流部署 |
| CXL 3.0 | PCIe 5.0-6.0 | ~64-128 GB/s | 多级交换 fabric、最多 256 端点对等访问 | 2025-2026 过渡期 |
| CXL 3.1 | PCIe 6.1-6.2 (64 GT/s) | ~128 GB/s | Global Fabric Attached Memory (GFAM) | **2026 年新平台默认标准** |
| CXL 4.0 | PCIe 7.0 (128 GT/s) | ~256 GB/s | 多机架 fabric、向后兼容所有前代 | 2025 年底发布规范，2027 年原型 |

### 1.2 核心玩家定位

| 角色 | 代表公司 | 产品/定位 |
|------|---------|----------|
| DRAM 模组 | Samsung, SK hynix, Micron | CMM-D (Samsung)、CMM-DDR5 (SK hynix)，三者合占模块营收 58-62% |
| **CXL 控制器芯片** | **澜起 (MXC)**, Astera Labs (Leo), Rambus | 澜起为 Samsung/SK hynix/Micron 提供控制器 |
| CXL Switch | Marvell (Structera S), Broadcom, Astera Labs (Scorpio) | Marvell 2026 Q3 开始送样 |
| CXL Retimer | Astera Labs (Aries), 澜起, Broadcom, Marvell | Astera 先发优势明显 |
| 平台支持 | Intel (Xeon 6), AMD (EPYC Turin), ARM | 2024 年起新服务器处理器标配 CXL |
| 新兴/颠覆性 | Celestial AI (光互连), MemVerge (管理软件) | Celestial AI 融资超 $350M |

### 1.3 澜起 MXC 芯片竞争定位

**核心产品**：M88MX6852 (CXL 3.1 Type 3 Memory eXpander Controller)

| 参数 | 规格 |
|------|------|
| CXL 规范 | CXL 1.1/2.0/**3.1** |
| 物理层 | PCIe 6.2, 最高 64 GT/s (x8) |
| 内存控制器 | 双通道 DDR5, 最高 8000 MT/s |
| 额外延迟 | 仅 70ns |
| 内部处理器 | 双 RISC-V + 安全处理单元 (SPU) |
| 形态支持 | EDSFF E3.S + PCIe AIC |
| 状态 | 2025 年 9 月发布，**正在向核心客户送样** |

**关键竞争优势**：
- Samsung 和 SK hynix 通过 CXL 2.0 合规测试时均使用澜起 MXC 芯片
- 三大 DRAM 巨头均采用澜起控制器
- AMD 和 Intel 均公开背书澜起的 CXL 3.1 策略

**直接竞争者**：Astera Labs Leo CXL Memory Controller
- 部署于 Microsoft Azure M-series VM
- Penguin Solutions 展示：75% 更高 GPU 利用率、2x 推理吞吐量
- 但澜起拥有更深的 DRAM 生态关系

### 1.4 CXL 市场规模与部署时间线

| 时间 | 规模 | 关键里程碑 |
|------|------|-----------|
| 2023 年 | ~$14M | CXL 3.0 规范定稿，开始送样 |
| 2024 年 | ~$500M | Intel Sapphire Rapids 支持 CXL 2.0，试点部署 |
| 2025 年 | ~$1B+ | Azure M-series CXL 预览，Astera Labs Leo 量产 |
| **2026 年** | **$1.8-2.5B** | **CXL 3.1 主流采用，多机架池化部署** |
| 2028 年 | ~$16B | CXL 4.0 生产级部署 |

**实际部署案例**：
- **Microsoft Azure**：2025 年 11 月上线 CXL 预览，使用 Intel Xeon 6 + Astera Labs Leo
- **Lenovo ThinkSystem V4**：支持 CXL 2.0 E3.S 2T 内存
- **Penguin Solutions**：GTC 2026 展示基于 Leo 的 CXL 方案，实现 3.6x 内存扩展
- **SK Telecom Petasus AI Cloud**：部署 SK hynix CMM-Ax

---

## 二、PCIe Retimer 竞争格局

### 2.1 Astera Labs — 绝对龙头

| 财务指标 | FY2024 | FY2025 | 增速 |
|---------|--------|--------|------|
| 营收 | $396.3M | **$852.5M** | +115% |
| 毛利率 | 76.4% | **75.7%** | — |
| 净利润 | -$83.4M | **$219.1M** | 扭亏 |
| Q1 2026 营收 | — | **$308.4M** | +93% YoY |

**产品矩阵**：

| 产品线 | 代际 | 状态 |
|--------|------|------|
| Aries Retimer (PCIe 4.0/5.0) | PCIe 4.0-5.0 | 量产 |
| **Aries 6 Retimer** | **PCIe 6.x** | **Pre-Production** |
| Scorpio P-Series Switch | PCIe 6.0 | 2026 H2 出货 |
| **Scorpio X-Series (320-lane AI Fabric)** | 自定义协议 | **已开始出货** |
| Leo CXL Memory Controller | CXL 3.x | 量产 |
| Taurus Smart Cable Module | Ethernet 400G | 量产 |

**核心优势**：
- PCIe Retimer 市场先发者（2019 年 PCIe 4.0 首批 design win）
- 拥有最广泛的 hyperscaler 客户基础
- 毛利率 ~76%，远高于行业平均
- 自研 DSP/SerDes，产品覆盖 Retimer + Switch + SCM + CXL Controller 全栈

### 2.2 澜起 Retimer 产品线

| 产品 | 代际 | 关键规格 | 状态 |
|------|------|---------|------|
| PCIe 5.0/CXL 2.0 Retimer | PCIe 5.0 | 32 GT/s, 36dB link budget | 量产 |
| **PCIe 6.x/CXL 3.x Retimer** | **PCIe 6.x** | **64 GT/s, 43dB, 自研 PAM4 SerDes** | **2025 年 1 月送样** |
| PCIe 6.x AEC 有源电缆 | PCIe 6.x | OSFP-XD form factor | 2026 年 1 月发布 |
| PCIe 7.0 Retimer | PCIe 7.0 | 128 GT/s (在研) | 研发阶段 |
| PCIe Switch | — | — | 工程研发中 |

### 2.3 澜起 vs Astera Labs 对比

| 维度 | Astera Labs | 澜起科技 |
|------|------------|---------|
| Retimer 市场地位 | **#1**（~86% 份额） | #2（~10.9% 份额） |
| FY2025 营收 | $852.5M（全部产品） | ~$750M（全部产品） |
| PCIe 6.0 进度 | Aries 6 Pre-Production | 2025 年 1 月送样 |
| 客户基础 | 海外 hyperscaler 为主 | 国内 + Samsung/SK hynix/Micron 生态 |
| 技术自主性 | 自研 DSP/SerDes | 自研 PAM4 SerDes IP |
| 产品广度 | Retimer + Switch + SCM + CXL | Retimer + MXC + 内存接口芯片 |
| 毛利率 | ~76% | 互连芯片整体 65.6% |

### 2.4 AI 服务器 BOM 中的 Retimer

- 典型 8-GPU AI 服务器需要 **8-24 个 Retimer**
- Retimer 单价约 $20-50，BOM 占比较小但不可或缺
- HGX 架构（CPU-GPU 分板）大量使用 Retimer
- GB200 架构（CPU-GPU 同板）参考设计减少了部分 Retimer 需求，但实际部署中仍需大量 Retimer 用于 NIC、存储等连接

### 2.5 其他竞争者

| 公司 | 代际 | 特点 |
|------|------|------|
| Broadcom | PCIe 5.0/6.0 | 行业首个 Gen6 Retimer，配合 PEX Switch 端到端方案 |
| Marvell Alaska P | PCIe 6.0 | 16-lane 仅 10W，2024 年 5 月发布 |
| Rambus | PCIe 7.0 | 纯 IP 授权模式，128 GT/s |
| Parade Technologies | PCIe 5.0 | 前 3 份额，消费级也强 |

### 2.6 PCIe 路线图

| PCIe 版本 | 速率 | 信令 | 状态 |
|-----------|------|------|------|
| PCIe 5.0 | 32 GT/s | NRZ | **2024-2025 主流** |
| PCIe 6.0 | 64 GT/s | PAM4 + FEC | **2025-2026 迁移期** |
| PCIe 7.0 | 128 GT/s | PAM4 | 2027-2028 预计 |

---

## 三、DDR6 路线图

### 3.1 JEDEC 标准化进展

| 时间节点 | 事件 |
|---------|------|
| 2024 年 | JEDEC 发布 DDR6 初始草案 |
| 2025 年 Q2 | JEDEC 完成 DDR6 Specification 1.0 |
| **2025 年 7 月** | **JEDEC 正式发布 LPDDR6 标准 (JESD209-6)** |
| 2025 年底-2026 Q1 | DDR6 最终标准正式发布 |
| 2026 年 4 月 | JEDEC 预览 LPDDR6 路线图扩展（数据中心 + PIM + SOCAMM2） |

### 3.2 DDR5 vs DDR6 核心规格对比

| 参数 | DDR5 (当前) | DDR6 (新标准) | 变化 |
|------|------------|-------------|------|
| 基础速率 | 4800 MT/s | **8400-8800 MT/s** | +75-83% |
| 最大速率 (标准) | 6400 MT/s | **17,000-17,600 MT/s** | **+166-175%** |
| 通道架构 | 2x 32-bit sub-channel | **4x 24-bit sub-channel** | 架构重构 |
| 电压 | 1.1V | **~1.0V** | 降低 ~9% |
| 最大 Die 密度 | 64Gb | **128Gb+** | 翻倍 |
| 接口形态 | DIMM/SO-DIMM | **CAMM2** | 形态革命 |

**LPDDR6 已发布规格**：
- 速率：10,667 - 14,400 MT/s
- 双 sub-channel，每个 12 数据信号
- 可编程链接保护、增强型 on-die ECC

### 3.3 部署时间线

| 市场 | 预计时间 |
|------|---------|
| LPDDR6 (移动) | 2025 年底-2026 年初 |
| 服务器/企业级 DDR6 | **2026 年中-年底** |
| 消费级桌面 DDR6 | 2027 Q2-Q3 |
| 大规模商业部署 | **2028-2029** |

### 3.4 对澜起的影响

**DDR6 架构从 2x32-bit 变为 4x24-bit，将大幅增加接口芯片复杂度和价值量。**

澜起的 DDR6 布局：

| 规划项 | 进展 |
|--------|------|
| 参与 JEDEC DDR6 内存接口芯片标准定义 | 已启动 |
| 启动第一代 DDR6 内存互连产品工程开发 | 已启动 |
| 第六代 DDR5 RCD + 第三代 MRCD/MDB 工程开发 | 2026 年完成 |
| 128GT/s SerDes 技术开发 | 进行中 |

**关键判断**：
- 澜起是全球仅有的两家 MRCD/MDB 芯片供应商之一（另一家为 Rambus）
- DDR5 仍在上升期（第四代 RCD 量产，第五代完成研发），DDR6 是 2029 年后的增长引擎
- DDR6 大规模商用预计 2029 年左右，届时将打开新的增长曲线

---

## 四、综合结论

### 4.1 三条赛道的定位

| 赛道 | 澜起位置 | 竞争强度 | 增长确定性 |
|------|---------|---------|-----------|
| 内存互连（DDR5/DDR6） | **全球第一** | 低（三寡头） | 高（DDR5 还在放量，DDR6 在布局） |
| PCIe Retimer | **全球第二**（追赶者） | 高（Astera 龙头） | 中高（AI 服务器需求确定，但份额竞争激烈） |
| CXL MXC | **全球先发者** | 中（生态早期） | 中高（市场从 $500M 到 $16B，但变现节奏待观察） |

### 4.2 关键风险

| 风险因素 | 影响评估 |
|---------|---------|
| GB200 参考设计减少 Retimer 需求 | **中风险** — hyperscaler 自定义设计仍大量使用 |
| CXL 采用速度不及预期 | **低风险** — 2026 年已确认为主流部署 |
| DDR6 延期 | **中风险** — 商业化可能推迟至 2029 年 |
| 地缘政治/供应链隔离 | **高风险** — 71% 收入来自海外 |
| Astera Labs 份额侵蚀 | **中风险** — 澜起在 Retimer 领域仍是追赶者 |
| HBM 替代风险 | **中长期风险** — 集成内存架构可能绕过传统接口芯片 |

### 4.3 一句话总结

> CXL 是澜起最独特的差异化机会（MXC 全球首发 + 三大 DRAM 厂商采用），PCIe Retimer 是增量但不是护城河（Astera 龙头地位稳固），DDR6 是远期增长引擎但还需要 3 年以上等待。澜起的核心投资逻辑仍然是"内存互连护城河 + CXL 先发优势 + AI 运力需求爆发"。
