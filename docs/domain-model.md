# 领域模型

本文档描述 CodeAgent Dashboard 的核心领域对象、对象关系和主要业务约束。它的目的不是替代数据库设计，而是让产品、后端、前端和运营对系统语义保持一致。

## 1. 建模原则

本项目采用以下建模原则：

- 业务目标和执行尝试分离，避免状态混乱
- 审批、审计、分析都是一等领域对象，不是附属表
- Agent 是可管理的执行资源，不是黑盒
- 先建清晰边界，再考虑实现细节

## 2. 核心实体

### 2.1 Task

`Task` 表示需要完成的一项业务目标，是用户在界面中直接管理的主对象。

建议字段：

- `id`
- `title`
- `description`
- `status`
- `priority`
- `createdBy`
- `ownerUserId`
- `preferredAgentId`
- `requiresApproval`
- `isSplittable`
- `parentTaskId`
- `createdAt`
- `updatedAt`
- `targetAt`
- `latestRunId`

说明：

- `Task` 不承载某一次执行的细节输出
- 一个 `Task` 可以没有任何 `TaskRun`
- 一个 `Task` 可以对应多个 `TaskRun`
- 若启用子任务，`parentTaskId` 用于表达层级关系
- `ownerUserId` 表示对任务负责的人类拥有者，`preferredAgentId` 表示优先或默认的执行 Agent，两者语义不能混用

建议状态：

- `draft`
- `ready`
- `running`
- `waiting_approval`
- `completed`
- `failed`
- `cancelled`

### 2.2 TaskRun

`TaskRun` 表示任务的一次具体执行尝试，是调度、追踪、审计和分析的核心对象。

建议字段：

- `id`
- `taskId`
- `status`
- `triggeredBy`
- `agentId`
- `orchestrationId`
- `startedAt`
- `finishedAt`
- `failureReason`
- `outputSummary`
- `parentRunId`
- `sequenceNumber`

说明：

- 同一个任务每次重试都应产生新的 `TaskRun`
- MVP 中并行子任务通过子 `Task` 表达，而不是依赖 `parentRunId` 组织主业务层级
- `parentRunId` 只作为后续扩展复杂运行树时的预留字段，第一版不应把它作为核心建模前提
- `TaskRun` 用于保留运行事实，不应被后续执行覆盖

建议状态：

- `queued`
- `dispatching`
- `running`
- `waiting_approval`
- `succeeded`
- `failed`
- `cancelled`

### 2.3 Approval

`Approval` 表示一个需要人工确认的决策点。

建议字段：

- `id`
- `taskId`
- `taskRunId`
- `type`
- `status`
- `requestedBy`
- `requestedAt`
- `decidedBy`
- `decidedAt`
- `decision`
- `comment`

常见审批类型：

- 最终结果验收审批

MVP 中默认只要求支持“最终结果验收审批”。开始前审批和执行中高风险动作审批可作为后续扩展能力，而不是第一版的基础闭环。

建议状态：

- `pending`
- `approved`
- `rejected`
- `cancelled`

### 2.4 AuditEvent

`AuditEvent` 表示系统中的关键业务事件，用于追踪、解释和合规。

建议字段：

- `id`
- `entityType`
- `entityId`
- `eventType`
- `actorType`
- `actorId`
- `timestamp`
- `payload`
- `correlationId`

事件示例：

- 任务创建
- 任务字段更新
- 任务开始执行
- Agent 分派完成
- 执行失败
- 提交审批
- 审批通过
- 审批拒绝

### 2.5 Agent

`Agent` 表示可被调度的 AI 执行单元。它需要明确能力、状态和基本元数据，便于系统选择合适执行者。

建议字段：

- `id`
- `name`
- `type`
- `status`
- `capabilities`
- `maxConcurrency`
- `lastSeenAt`
- `metadata`

说明：

- `type` 可用于区分不同运行方式或提供方
- `capabilities` 用于任务匹配
- `maxConcurrency` 用于控制并行调度上限

建议状态：

- `active`
- `paused`
- `offline`
- `deprecated`

### 2.6 Orchestration

`Orchestration` 表示一次调度过程的上下文。它连接任务拆分、运行创建、依赖判断和汇总逻辑。

建议字段：

- `id`
- `taskId`
- `strategy`
- `status`
- `createdAt`
- `updatedAt`
- `summary`

说明：

- 若一个任务被拆成多个并行子任务，`Orchestration` 负责连接根任务、子任务和关联运行的共同上下文
- 它帮助系统从任务视角切换到调度视角

### 2.7 Analytics Snapshot 或 Metric

Analytics 既可以通过事件流实时计算，也可以通过定时聚合得到快照。无论采用哪种技术形式，领域上都需要一组可解释指标。

常见指标：

- 任务总量与完成率
- 平均 `TaskRun` 时长
- 失败率
- Agent 成功率
- 审批平均等待时长
- 并行任务平均完成收益

## 3. 关键关系

### 3.1 Task 与 TaskRun

- 一个 `Task` 对应零个或多个 `TaskRun`
- 一个 `TaskRun` 必须属于一个 `Task`
- `Task.status` 表示业务目标总体状态
- `TaskRun.status` 表示单次执行尝试状态

这是系统中最重要的关系。很多混乱都来自把两者合并，所以这里必须明确。

### 3.2 TaskRun 与 Approval

- 一个 `TaskRun` 可以对应零个或多个 `Approval`
- 一个 `Approval` 必须关联到一个明确的审批上下文
- 审批结论会影响 `TaskRun` 继续执行、结束或回退

### 3.3 TaskRun 与 Agent

- 一个 `TaskRun` 通常由一个主执行 `Agent` 负责
- 一个 `Agent` 可以在并发限制内执行多个 `TaskRun`

如果未来要支持更复杂协作，可在 v1 以后扩展协作者模型，但 MVP 不必引入多执行者绑定的复杂结构。

### 3.4 Task 与 Task

- 通过 `parentTaskId` 表达子任务关系
- 子任务可以用于任务拆分、并行执行和汇总结果

MVP 中建议只支持树状结构，不支持任意图，以保持规则简单。

## 4. 关键业务约束

### 4.1 Task 与 TaskRun 状态不能混用

不能因为某一次 `TaskRun` 失败，就自动抹去任务曾经成功的事实。相反，任务状态应由业务规则决定，例如“最近一次运行失败且当前未重试”才把任务显示为失败。

MVP 建议采用以下最小收敛规则：

- `draft`：任务必要字段未补全，不能执行
- `ready`：任务已满足执行条件，且当前没有活跃运行或待审批项
- `running`：存在至少一个活跃中的 `TaskRun`，或存在仍在执行中的必要子任务
- `waiting_approval`：当前有效执行已完成，但存在待处理的最终结果审批
- `completed`：任务本身或所有必要子任务都已完成，且不存在待审批项
- `failed`：最近一次有效执行失败，当前没有活跃运行，也没有挂起的重试计划
- `cancelled`：任务被明确取消，且不再进入执行流程

前端不得自行推断这套规则，应以后端统一计算结果为准。

### 4.2 审批必须基于明确对象

每个审批都应指向清楚的任务、运行或结果，不能出现只知道“有个审批待处理”却看不到上下文的情况。

### 4.3 审计事件不可被静默覆盖

`AuditEvent` 一旦写入，应视作事实记录。后续修正应以新事件补充，而不是原地抹除。

### 4.4 并行执行必须受限

系统支持多 Agent 并行，但前提是任务或子任务之间相互独立，并且 Agent 具备可用容量。

MVP 建议采用以下约束：

- 只有无依赖关系的任务或子任务可以并行
- 并行度不能超过 Agent 的 `maxConcurrency`
- 汇总节点必须等待所有必要分支结束
- 并行分支之间不共享可变工作记忆，不发生自动协商或自动移交

### 4.5 失败重试生成新运行

重试不能复用原有 `TaskRun`。必须新建运行记录，才能保证历史完整与分析准确。

## 5. 关于多 Agent 并行的领域边界

领域上需要明确区分两类能力：

### 5.1 MVP 支持的能力

- 多个独立任务并行执行
- 单个任务拆成多个独立子任务后并行执行
- 每个并行分支拥有独立运行记录和审计事件
- 一个 `TaskRun` 只对应一个主执行 Agent，不允许多个 Agent 同时写入同一个运行实例

### 5.2 v1 再考虑的能力

- 多个 Agent 共享工作记忆
- 一个 Agent 对另一个 Agent 发起审查或反馈
- 多 Agent 联合决策同一工作项
- 动态调整协作角色和责任边界
- Agent 之间自动接力或动态移交上下文

这样的分层有助于让模型保持清楚。MVP 先解决“多个 Agent 同时工作”，v1 再解决“多个 Agent 一起协作”。

## 6. 建议的聚合边界

从实现角度看，可以把以下对象视为主要聚合边界：

- `Task` 聚合，管理任务元数据和任务级状态
- `TaskRun` 聚合，管理执行生命周期
- `Approval` 聚合，管理审批生命周期
- `Agent` 聚合，管理可执行资源目录

`AuditEvent` 与 Analytics 更适合作为跨聚合读模型或支撑模型存在，但在产品语义上仍然是核心概念。

## 7. 建模结论

这套领域模型强调一个中心思想，任务是业务意图，运行是执行事实，审批和审计负责可控与可追溯，编排器与 Agent 负责把工作真正做起来，Analytics 负责把经验沉淀下来。只要这几个角色边界稳定，后续无论 UI、数据库还是调度策略如何演进，系统都能保持一致性。
