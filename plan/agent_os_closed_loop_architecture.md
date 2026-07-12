# Cognitrade Agent OS 闭环架构

## 1. 文档定位

本文定义 Cognitrade 的目标运行架构，以及当前代码与目标之间的差距。

目标不是“把多个 Agent 串成一条固定流程”，而是建立一个由 Agent OS 管理的闭环运行系统：

> Agent OS 负责理解任务、规划和修改执行图、调度 Agent、管理证据、执行治理规则，并持续根据运行产出决定下一步。

DomainPack 只提供可安装的专业能力，不拥有完整运行时，也不能绕过 Agent OS 的治理边界。

## 1.1 Agent OS 的架构定位与演进判断

Cognitrade 的目标不是把多个固定 Agent 串成更复杂的流程，而是形成一种“动态编排型 Agent OS”（Adaptive Agent Runtime）：由一个 Main Agent 负责理解请求、决定执行深度、选择能力，并在运行过程中根据 Artifact、Tool 和 Subagent 的真实产出继续执行、修改图、重试、局部重跑或结束交付。

动态 ExecutionGraph 本身不是 Agent 的全部，也不是固定工作流模板。它是 Main Agent 在任务复杂度达到阈值后临时物化的编排与执行载体。简单请求可以留在 Core Agent Loop；只有当请求需要 DomainPack、多 Subagent、并发、专业 Artifact、Gate、审批、暂停恢复或可回放治理时，才升级到 Graph Mode。

这一方向具备成为下一代 Agent 系统运行时的可能，但成立条件不是“使用了动态图”这一项，而是以下能力同时成立：

- Main Agent 是唯一 Root，PlanAgent 是其规划角色，不注册第二个主 Agent；
- Subagent 通过 Capability、输入输出 Schema 和 Artifact 契约被选择，可以被兼容实现替换；
- Tool、Skill 和 Subagent 都是受治理的可调用能力，但只有需要独立生命周期、预算、并发、取消或用户可见状态的能力才提升为 Graph leaf；
- Graph 根据当前请求、已安装 Pack、Board、Memory、示例和治理预算实时生成，固定模板最多只能提供规划先验；
- 每轮产出都回到同一个 ArtifactBoard，由 Main Agent 结合运行事实决定继续、补图、改图、换能力、重跑或交付；
- 权限、预算、并发、取消、来源追踪、质量 Gate、人工审批、审计和回放始终由 Agent OS 固定控制；
- 经过评测的运行经验才能进入 Policy Memory，未经验证的 Subagent 产出不能直接改变长期方法论。

因此，准确的架构表述是：

> 单一 Main Agent + 能力契约 + 按需动态 Graph + 统一 Artifact 数据面 + 可治理运行时。

它区别于传统 Tool Agent、ReAct Loop 和固定 DAG 的地方，不是增加了更多 Agent，而是让任务分解、专家组合和执行图都可以根据运行事实演进，同时让安全边界和证据链保持不可绕过。

当前 Cognitrade 已具备该方向的核心雏形：Main Agent、Core Agent Loop、Capability Planner、动态 ExecutionGraph、统一 Runner、ArtifactBoard、DomainPack、Gate、Memory 和审计事件。当前仍属于“动态 Graph + DomainPack 的受治理运行时”，还不能宣称已经实现完全自演化的 Agent OS。后续必须继续验证：

1. Planner 能否生成此前不存在的任务图，而不是只在固定 intent 图之间切换；
2. 替换 Subagent 后，只要契约兼容，上下游是否无需修改；
3. Planner 是否能依据 Board 的中间结果安全地改变任意子图；
4. 评测和 Policy Memory 是否能证明某种规划策略有效后再复用；
5. 在成本、延迟、失败、并发和高风险交付约束下，系统是否仍能稳定完成闭环。

## 2. 核心原则

### 2.1 ExecutionGraph 是运行状态

ExecutionGraph 不是一次性生成后不可改变的静态计划，而是当前任务的版本化运行状态。

- compound node 表示宏观任务或可展开的子图。
- leaf subagent node 才是真正执行工作的节点。
- graph revision 表示图的版本。
- 每个 patch 都必须记录原因、来源、操作者、时间和变更内容。
- 运行中的节点只能在安全点通过取消协议终止。
- 结构性 patch（重连、移除、替换或重跑）必须计算受影响节点，自动将其旧输出标记为当前 Run 的失效 Artifact；
  下游节点回到 `pending`，新输出必须通过 `supersedes_artifact_id` 建立版本链。

### 2.2 ArtifactBoard 是唯一协作数据面

Agent 之间不直接传递进程内结果字典。所有可复用事实必须写入 ArtifactBoard。

```text
Artifact
├─ artifact_id
├─ artifact_type
├─ schema_version
├─ producer
├─ content
├─ source_refs
├─ parent_artifact_ids
├─ confidence
├─ risk_flags
├─ status
└─ artifact_version
```

Graph 保存执行状态和 Artifact 引用；Planner、Run Evaluator、Subagent、Memory 和用户读取同一份事实状态。
失效 Artifact 仍保留用于审计和回放，但不参与新的 `active` 输入选择；Board 从 Run Snapshot 恢复失效集合。

### 2.3 DomainPack 是能力扩展，不是运行时

DomainPack 提供：

- 专业 Artifact 类型和 Schema；
- 专业 Subagent capability；
- Pack-owned RequestIntake intent definitions、槽位约束和候选能力提示；
- 专业数据工具和 Skill；
- 专业 Planner policy；
- 质量 Gate；
- 报告契约；
- 评测和方法论。

DomainPack 不负责：

- 并发调度；
- 重试和取消；
- 预算和权限；
- Graph 版本控制；
- 运行审计；
- 跨领域的基础对话能力。

### 2.4 Subagent 与 Graph Node 的边界

DomainPack 定义的是可执行的专业 Subagent，而不是一套绕过 Agent OS 的独立运行时。每个 Subagent 都遵守统一的输入、工具调用、产出和治理协议：

```text
读取输入 Artifact
  → 局部规划
  → 调用受治理的 Tool / Skill
  → 校验工具结果
  → 写入 ArtifactBoard
  → 继续调用、请求重规划或结束
```

Subagent 可以是确定性执行器，也可以是有限次数的 Plan-and-Execute Loop。Loop 必须受 Agent OS 的时间、Token、预算、Tool 调用次数、并发和取消协议限制，不能无限自主运行。

ExecutionGraph 中需要区分三类节点或运行角色：

```text
Agent OS 控制面
├─ TaskIntake
├─ PlanAgent
├─ Run Evaluator / Quality Gate Evaluator
├─ Gate
└─ ExecutionGraphRunner

ExecutionGraph 结构与工作节点
├─ compound node：宏观任务或延迟展开的子图容器
└─ leaf node：真正执行工作的 Core Subagent 或 DomainPack Subagent
```

- 每一个真正执行工作的 leaf node 都是一个 Subagent。
- compound node 是图结构和规划边界，不是普通业务 Subagent。
- TaskIntake、PlanAgent、Run Evaluator、Gate 和 Runner 属于 Agent OS 的固定治理骨架，不由 DomainPack 定义。
- Tool / Skill 是 Subagent 可以调用的受治理能力，不等于 Subagent。
- 如果某个 Tool 调用本身需要独立持久化、独立预算、并发调度、取消或用户可见的执行状态，PlanAgent 可以把它提升为单独的 leaf Subagent 节点；普通 Tool 调用则留在当前 Subagent 的执行过程中。

Subagent 可以发现输入缺口并提出补图、换 Tool 或重跑建议，但不能直接修改全局 ExecutionGraph。建议必须写入可审计的结果或控制信号，由 PlanAgent 在安全点审核并提交 Graph patch。

### 2.5 Agent OS Core、Tool 与 Subagent 的关系

抛开 DomainPack，Agent OS Core 本身应具备完整的 Codex-like 基础 Agent 能力：理解用户请求、进行有限规划、调用 Tool、读取 Tool 结果、继续循环并生成最终回答。Agent OS 不只是一个 Codex 对话循环，还要额外提供 ExecutionGraph、ArtifactBoard、并发调度、治理、审计和动态改图能力。

Tool 和 Subagent 不应被合并成同一种抽象，也不应保留两套独立运行时：

```text
Tool
  └─ 原子能力：查询行情、读取文件、调用 MCP 或执行一个受约束的 Skill

Subagent
  └─ 有目标的执行者：局部规划、调用多个 Tool、循环判断并产出 Artifact

ExecutionGraph
  └─ 编排多个 Subagent 的依赖、并发、版本和动态调整
```

两者的接入规则是：

```text
普通 Tool 调用
  → 保留为 Tool
  → 当前 Subagent 通过 Tool Registry 调用

自主 Agent / Codex 子线程
  → 通过 Subagent Runtime Adapter 接入
  → 作为 leaf Subagent 纳入 ExecutionGraph
```

不要把每个 Tool 都强行包装成真正的自主 Agent。这样会引入不必要的模型调用、状态和治理复杂度。只有当一个 Tool 调用需要独立预算、并发、暂停、重试、取消、持久化或用户可见状态时，才可以用轻量的 `ToolAgent` 将其提升为独立 leaf；其内部仍然执行 Tool，而不是凭空增加一层自主推理。

Subagent 可以由同一个 Agent Runtime 支持多种执行模式：

```text
deterministic      单次确定性执行
reactive_loop      观察 → 调 Tool → 再观察
plan_and_execute   先生成局部计划 → 执行 → 修正
```

这些模式都必须遵守统一的 Subagent 输入输出契约、Artifact 产出契约和 Agent OS 治理约束。DomainPack 只新增专业 Subagent、Tool / Skill、Artifact Schema、Planner policy 和 Gate，不新增第二套调度器或黑板。

最终运行关系是：

```text
PlanAgent
  → 选择 Core Subagent 或 DomainPack Subagent
  → Subagent 调用 Tool / Skill
  → ArtifactBoard
  → Run Evaluator / Quality Gate Evaluator
  → 继续、改图、重跑或结束
```

### 2.6 DomainPack 可选性不变量

删除或卸载所有 DomainPack 后，Agent OS 仍然必须可以作为通用 Codex-like Agent 使用。DomainPack 的存在只决定系统拥有哪些专业能力，不决定 Agent OS 是否能够启动、对话、规划、调用 Tool 或执行基础任务。

无 Pack 时仍应保留：

- Task Intake、PlanAgent 和最小 ExecutionGraph 生成；
- Core Agent Loop 和 Core Subagent 调度；
- Tool Registry、通用 Skill、文件和终端等受治理工具；
- ArtifactBoard、Layered Memory、暂停、恢复、重试和回放；
- CLI、API、Chat、SSE、权限、预算和审计；
- 基础问答、任务总结、Artifact 检查和记忆查询。

无 Pack 时不应提供或假装提供：

- Finance、Legal 或其他专业领域结论；
- 专业 Domain Artifact Schema；
- 专业数据源和专业方法论；
- 专业 Gate、报告契约和领域评测。

```text
无 DomainPack
  → Core Capability Catalog
  → Core Agent Loop / Core Subagent
  → Tool Registry
  → 通用 Artifact / assistant_message

安装 DomainPack
  → 在上面增加专业 Subagent、Tool、Schema、Policy 和 Gate
  → Agent OS Core 无需修改
```

Agent OS Core 不得直接导入或硬编码任何 DomainPack。Pack 的安装、卸载和版本切换都应通过 Registry 完成；卸载 Pack 只能减少可用 Capability，不能破坏 Core Runtime。

这是架构完成的必要验收条件：在没有任何 DomainPack 的干净 Registry 中，系统仍能完成基础对话、通用 Tool 调用、Core Subagent 执行、Artifact 持久化和运行回放。

### 2.7 单一 Root / Main Agent 约束

每个用户会话或 Run 只允许存在一个 Root / Main Agent。它是用户请求的唯一主控者，负责在同一运行上下文中完成：

- 理解请求和必要的澄清；
- 判断是否需要 Tool、Skill、Subagent 或 DomainPack；
- 进入 PlanAgent 规划模式并实时生成或修改 ExecutionGraph；
- 接收 Runner 和 Subagent 的结果；
- 根据 Board 状态继续执行、重规划、重试、局部重跑或结束；
- 生成最终的 `assistant_message`。

`PlanAgent` 不是第二个主 Agent，也不是默认需要单独创建的线程。它是 Main Agent 的控制角色或规划阶段，可以与 Main Agent 使用同一个模型和运行上下文，但必须拥有独立的 Graph patch 协议和权限边界。

```text
一个 Main Agent
├─ intake / clarify
├─ plan：PlanAgent role
├─ execute：Tool Loop 或 Graph delegation
├─ evaluate / replan
└─ respond
```

DomainPack 只能提供被 Main Agent 调度的专业 Subagent、Tool / Skill、Schema、Policy 和 Gate，不能注册另一个 Main Agent。真正需要并发或独立生命周期的工作，必须作为受治理的 leaf Subagent 纳入同一个 Run 的 ExecutionGraph。

这样可以避免多个主 Agent 之间出现上下文分裂、决策冲突和重复调度，同时保留多个专业 Subagent 的并行协作能力。

### 2.8 按需激活 Graph 与三种执行模式

ExecutionGraph 不需要在每个请求开始时立即创建。Agent OS 先由 Main Agent 运行 Core Agent Loop；只有当任务需要结构化产出、DomainPack、多个 Subagent、并发、Gate 或持久化治理时，才按当前上下文进入 Graph Mode。

“按需激活”只表示延迟生成 Graph 的时机，不表示 Graph 是固定模板。进入 Graph Mode 后，图仍然必须由 Main Agent 根据当前请求、已安装 Pack 的能力目录、Subagent 输入输出契约、Tool、示例、Policy、Memory、ArtifactBoard 和治理约束实时生成。

Agent OS 定义三种执行模式：

```text
Core Agent Loop
  普通对话、简单 Core Tool 调用、轻量总结和快速回答

Task Mode
  需要结构化 Artifact、长任务、可暂停、可恢复或可回放

Graph Mode
  需要 DomainPack Subagent、多专家协作、并发、动态补图或专业 Gate
```

三种模式不是三套并行运行时，而是同一个 Main Agent 的不同执行深度：

- `Core Agent Loop` 可以直接调用允许的 Core Tool / Skill，并生成 `assistant_message`；不强制创建持久化 Graph。
- `Task Mode` 仅用于 Core 任务建立最小运行上下文和必要 Artifact；如果复杂度继续上升，可以升级为 Graph Mode。Task Mode 一旦涉及 DomainPack Subagent，必须立即升级为 Graph Mode。
- `Graph Mode` 由 Main Agent 的 PlanAgent role 实时物化 ExecutionGraph，由 Runner 调度 leaf Subagent，并在安全点进行评估、补图、重试或局部重跑。

典型升级路径是：

```text
用户请求
  ↓
Main Agent / Core Agent Loop
  ├─ 普通请求 → Core Tool / Core Subagent → assistant_message
  └─ 发现需要专业协作
       ↓
     创建任务 Artifact 和 Graph Context
       ↓
     Main Agent 进入 PlanAgent role
       ↓
     实时生成 Graph v1
       ↓
     Graph Runner 执行 Pack Subagent
       ↓
     Board / Evaluator / Graph patch
       ↓
     回到 Main Agent 输出最终回答
```

模式升级时必须复用同一个 `run_id`、`thread_id`、Main Agent 上下文、Tool Registry、Memory 和事件流，不能启动第二套隐式 Agent Runtime。Core Loop 中已经获得的用户请求、Tool 结果和对话上下文应转为受约束的 Artifact，作为 Graph 的输入。

进入 Graph Mode 的主要触发条件包括：

- 需要 DomainPack 专业能力或专业 Tool；
- 需要两个或更多 Subagent 协作；
- 需要并发采集、长时间运行或独立生命周期；
- 需要结构化报告、Artifact Schema 或 blocking Gate；
- 需要暂停、恢复、局部重跑、完整回放或人工审批；
- 当前 Subagent 发现输入缺口，需要补图、换能力或重规划。

Core Tool 和基础 Core Subagent 可以在 Core Agent Loop 中直接使用。DomainPack Subagent 必须通过 Graph Mode 调度，接受统一的 Runner、ArtifactBoard、来源追踪、Gate 和审计治理，不能被 Core Agent Loop 直接绕过。只有明确声明为安全、原子且无需专业 Artifact、来源追踪和交付 Gate 的 Pack Tool，才可以由 Policy 授权给 Core Agent Loop 直接调用。

### 2.9 已确认的七项运行约束

以下约束属于 Agent OS Core 的固定协议，不由 DomainPack 或单个 Subagent 自行修改。

1. **RequestIntake 的身份**

   `TaskIntake` 更准确地称为 `RequestIntake`。它是 Agent OS 的固定入口服务或 Main Agent 的固定 intake 阶段，不是第二个 Agent，也不负责生成最终 Graph。它只输出请求类型、实体、候选领域、候选 Capability、缺失信息、风险和置信度。

2. **Task Mode 的范围**

   `Task Mode` 只服务 Core 任务的轻量上下文和必要 Artifact。只要请求需要 DomainPack Subagent，就必须升级到 `Graph Mode`，不能在 Task Mode 中直接执行专业 Subagent。

3. **Main Agent 状态持久化**

   每个 Run / Thread 只有一个 Main Agent。暂停、恢复、重试、局部重跑和 Graph revision 变化时，必须恢复同一个 Main Agent 的请求上下文、Memory 引用、Tool 权限、预算、Graph handle 和事件游标。不能通过启动第二个主 Agent 代替状态恢复。

4. **Core Loop 产出写入 Board 的条件**

   普通、临时性的 Core Loop 回答可以只写入响应和事件流，不强制创建持久化 Graph 或 Artifact。只要结果需要被后续 Agent 复用、进入 Graph、接受审计、参与 Memory 晋升或作为专业结论依据，就必须转换成声明过 Schema 的 Artifact 写入 ArtifactBoard。

5. **Subagent Loop 上限**

   每个 Subagent 的局部 Loop 必须受 Run / Graph Governance 限制，包括最大循环轮数、Tool 调用次数、并发 Tool 数、Token、时间、费用、重试次数和取消信号。Subagent 不能无限自主运行，也不能自行提高这些上限。

6. **Graph 修改权**

   只有 Main Agent 的 `PlanAgent role` 可以在安全点提交 Graph patch。Subagent、Tool 和 DomainPack 只能提交缺口、风险、补图、换能力或重跑建议；它们不能直接改变全局 Graph、依赖关系或治理配置。

7. **Tool 与 Pack Subagent 的权限**

   Core Tool 由 Agent OS Core 管理；Pack Tool 由 DomainPack 声明并由 Registry 注册。DomainPack Subagent 必须在 Graph Mode 中调用允许的 Tool，不能绕过 Artifact、来源、Gate 和审计。安全、原子且无需专业交付 Gate 的 Pack Tool，才可以由 Policy 授权给 Core Agent Loop 直接调用。

### 2.10 Agent OS Core Tool 与 Graph 状态下的调用

Agent OS Core 必须拥有不依赖 DomainPack 的 Core Tool Catalog。Core Tool 是 Agent OS 的基础能力，不属于 Finance、Legal 或其他专业包，例如：

- `core.artifact.inspect`：读取和解释当前 Run 的 Artifact、来源、版本和风险；
- `core.memory.query`：查询当前 Main Agent 有权限访问的分层记忆；
- `core.graph.inspect`：查看当前 Graph、revision、节点状态和待处理 patch；
- `core.run.status`：读取 Run、预算、审批、暂停和取消状态；
- `core.task.clarify`：生成结构化澄清请求；
- `core.skill.invoke`：在 Core Policy 允许范围内调用通用 Skill；
- `core.message.compose`：根据已验证上下文生成 `assistant_message`。

Core Tool 按作用分为四类：

```text
Observation Tools
├─ core.artifact.inspect
├─ core.memory.query
├─ core.graph.inspect
├─ core.run.status
├─ core.file.read
└─ core.file.search

Interaction Tools
├─ core.task.clarify
└─ core.message.compose

Execution Tools
├─ core.skill.invoke
├─ core.shell.exec
└─ core.file.write

Control Tools
├─ core.plan.request_replan
└─ core.graph.patch
```

文件读取、文件搜索、Skill 调用和 Shell 执行都应以 Tool 形式进入 Tool Registry，不应形成绕过 Agent OS 的隐藏系统调用。文件写入、Shell 执行和其他有副作用的 Core Tool 必须额外经过权限、沙箱、审批、幂等和审计检查。

这些 Core Tool 在 `Core Agent Loop`、`Task Mode` 和 `Graph Mode` 都可以被 Main Agent 使用。进入 Graph Mode 不等于 Main Agent 被挂起或失去工具能力：

```text
Graph Mode
  ├─ Main Agent / PlanAgent role
  │    ├─ core.artifact.inspect
  │    ├─ core.memory.query
  │    ├─ core.graph.inspect
  │    ├─ core.task.clarify
  │    └─ core.message.compose
  │
  └─ ExecutionGraphRunner
       └─ DomainPack / Core leaf Subagent
            └─ 按节点 Policy 调用 Core Tool 或 Pack Tool
```

Main Agent 在 Graph Mode 中可以使用允许的 Observation、Interaction 和 Execution Tool 观察 Board、读取 Memory、读取文件、检查节点结果、调用 Skill、判断是否需要澄清和提出重规划。`core.graph.patch` 属于高权限 Control Tool，仍必须通过 `PlanAgent role` 的受治理提交协议完成；普通 Subagent 不得直接调用它。Subagent 的 Tool 调用必须绑定到具体 leaf node；Main Agent 的控制面 Tool 调用必须绑定到同一 Run / Thread / Graph revision，并记录 actor、Tool、参数摘要、结果、来源和审计事件。

因此，Core Tool 与 DomainPack Tool 不是两套调用机制，而是同一个 Tool Registry 下的两个命名空间：

```text
Core Tool Catalog + Installed DomainPack Tool Catalog
  → 统一 Tool Registry
  → 统一 Schema、权限、预算、取消、审计和事件流
```

## 3. Agent OS 闭环

```text
┌─────────────────────────────────────────────────────┐
│ CLI / API / Chat / Frontend                          │
└──────────────────────┬──────────────────────────────┘
                       │ RequestEnvelope
                       ▼
┌─────────────────────────────────────────────────────┐
│ Task Intake                                          │
│ 识别任务类型、目标、槽位、权限和缺失信息              │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│ Main Agent / PlanAgent                                │
│ 读取 Capability Catalog、任务上下文和分层记忆          │
│ 生成或修改 ExecutionGraph                             │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│ ExecutionGraphRunner                                 │
│ 选取 ready leaf，统一并发执行和治理                    │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│ Professional Subagents / Core Subagents              │
│ 输出 ArtifactDraft                                    │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│ ArtifactBoard                                         │
│ 写入事实、来源、版本、置信度和风险                     │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│ Run Evaluator / Quality Gate Evaluator               │
│ 检查 Gate、预算、证据完整性和任务目标                  │
└───────────────┬───────────────┬─────────────────────┘
                │               │
          continue          graph patch / rerun
                │               │
                └───────┬───────┘
                        ▼
                 下一轮安全点执行
```

闭环在以下条件之一成立时结束：

- 最终交付物通过所有 blocking Gate；
- Planner 判断任务已经完成；
- 请求人工审批；
- 预算、时间或节点上限耗尽；
- 任务无法继续并生成可解释的失败结果。

## 4. 组件职责

| 组件 | 类型 | 职责 | 是否是工作节点 |
|---|---|---|---|
| RequestIntake | 控制面 | 识别请求类型、候选能力、目标和缺失槽位 | 否 |
| Main Agent | Root Agent | 面向用户，统一 intake、规划、执行、重规划和最终响应 | 否，负责控制 |
| PlanAgent role | Main Agent 控制角色 | 生成、评估和修改 ExecutionGraph | 否 |
| RuntimePlanner | 入口适配器 | 根据 DomainPack 找到初始能力范围 | 否 |
| CapabilityPlanner | 图构建器 | 根据输入输出契约生成候选图 | 否 |
| ExecutionGraphRunner | 调度器 | 执行 ready leaf、并发、重试、暂停和取消 | 否 |
| Run Evaluator / Quality Gate Evaluator | 控制面 | 检查产出并向 Main Agent 提供继续、修改、重跑或结束所需的事实 | 否 |
| ArtifactBoard | 数据面 | 保存所有共享事实和产出 | 否 |
| LayeredMemory | 数据面 | 保存运行记忆、领域记忆和策略记忆 | 否 |
| Compound node | 图结构 | 表示宏观任务或延迟展开的子图 | 否 |
| Core / DomainPack Subagent | 工作节点 | 执行一个系统或专业能力，可在边界内调用 Tool / Skill | 是，必须是 leaf |
| Report Composer Subagent | 工作节点 | 将结构化产出编排成专业报告或 `assistant_message_draft` | 是，必须是 leaf |

## 5. Core Capability 与 DomainPack Capability

Agent OS 即使没有任何专业 DomainPack，也应能执行一组系统级基础能力：

- `conversation.answer`：回答身份、使用方式和普通概念问题；
- `task.summarize`：总结已有 Artifact 或运行结果；
- `artifact.inspect`：解释 Artifact、来源和风险；
- `memory.query`：查询被允许访问的运行或长期记忆；
- `skill.invoke`：在权限和工具沙箱内调用通用 Skill；
- `assistant_message.compose`：生成最终对话消息。

这些能力都必须经过 Agent OS Core、统一的 Tool Registry 和治理边界，但不要求每次都创建持久化 ExecutionGraph。简单请求可以在 Core Agent Loop 中完成；需要结构化 Artifact、持久化、并发、Gate 或可回放控制时，才升级到 Task Mode 或 Graph Mode。任何升级都必须复用同一个 Main Agent、Run、Thread、Memory 和事件流，不能形成第二套隐式运行时。

Finance Pack 则提供金融专业能力，例如：

- A 股目标解析和 Security Master；
- 行情、基本面、公告、板块、政策、资金流、情绪、事件和龙虎榜快照；
- 财务分析、估值、现金流韧性和同业比较；
- 股票筛选、板块研究、观点复核和方法论蒸馏；
- 金融来源 Gate、报告契约和专业评测。

没有 Finance Pack 时，Agent OS 仍可回答基础问题；没有 Finance Pack 时，Agent OS 不应假装具备金融专业结论能力。

因此，Capability 是“能够完成什么”，Subagent 是“由哪个可执行节点完成以及如何完成”。Agent OS 根据 Capability Catalog 选择能力并实例化 leaf Subagent；DomainPack 负责提供专业 Subagent 的实现、输入输出契约、可用 Tool / Skill 和方法论约束。

DomainPack 的 intent 只提供 Intake 和规划先验，不是固定 Graph。每个 intent 必须声明候选 Capability、必需槽位和预期交付 Artifact；Main Agent 仍需结合当前请求、Board 和治理预算决定是否采用这些建议以及最终生成哪些节点。

## 6. 典型运行流程

### 6.1 基础问题

```text
“你是谁”
  ↓
Main Agent / Core Agent Loop
  ↓
Core Tool 或 Core Subagent: answer_agent
  ↓
assistant_message
  ↓
ResponseEnvelope.answer
```

### 6.2 个股研究

```text
“分析 600000 的估值和现金流”
  ↓
Task Intake: stock_check
  ↓
PlanAgent 选择 Finance Pack 能力
  ↓
query_normalizer
  ↓
stock_snapshot_collector
  ├─ market
  ├─ fundamental
  ├─ notice
  ├─ capital_flow
  ├─ sentiment
  ├─ events
  └─ lhb
  ↓
evidence.select + financial_analyst
  ↓
finance.report.compose
  ↓
research_report + assistant_message
```

### 6.3 动态调整

如果基本面快照缺少现金流字段，Planner 可以在安全点：

1. 读取 Board 中的缺口和风险标记；
2. 增加现金流或公告采集节点；
3. 修改下游节点的依赖关系；
4. 提交新的 Graph patch；
5. 只重跑受影响的子图；
6. 保留旧 Artifact 版本和新 Artifact 版本。

## 7. 当前实现状态

当前代码已经具备：

- `ExecutionGraph`、revision 和 patch 数据结构；
- `ExecutionGraphRunner` 的 ready 节点调度、并发、重试、暂停、取消和重跑；
- `ArtifactBoard` 持久化和 Artifact 版本控制；
- Capability contract 驱动的初始图构建；
- Finance Pack 和 Legal Pack；
- Finance 的 Task Intake、专业 Subagent 和专属报告 Composer；
- Episodic Memory 写入和基础 Gate 检查。

当前已完成运行闭环的核心协议：

- 标准入口统一经过 RequestIntake，由 Main Agent 的 PlanAgent role 创建 lazy Graph；
- Core Agent Loop 支持有限的模型原生 Tool-call loop，Task/Graph 模式复用同一 Run、Thread 和事件流；
- Core、DomainPack、MCP Tool 通过同一个 Registry，具备 Schema、权限、审批、幂等、速率和审计约束；
- DomainPack 可以声明版本化 Skill；Finance 的九个快照 Skill 已归属到 Pack，旧 `research_system.skills` 仅保留兼容导出；
- RequestIntake 支持可选模型分类提案，模型提案必须重新经过 Pack Intake 和确定性治理校验，模型不可用时回退到 Core/Pack 规则；
- Graph Patch 在 revision、safe point、原子校验和 cancellation 协议下应用；
- Main Agent PlanAgent role 输出统一的 `PlanDecision`，Runner 同时兼容旧 Patch 返回值并将其规范化为决策事件；
- 标准 Agent OS 事件统一使用 `EventEnvelope`，包含 Run、Thread、Request、Graph revision 和 Actor 身份；
- Human Approval 绑定 Graph revision、DomainPack 版本、策略指纹和 user/tenant scope；图修订或治理上下文变化时旧批准不能复用；
- Capability 可以声明 `halt` 或 `isolate` 失败模式；隔离失败会在 safe point 由 PlanAgent 排除失败 Subagent 并从能力目录选择替代节点；
- ArtifactBoard 支持批量输出提交、版本、父子血缘和显式线程作用域；
- Artifact source refs 统一归一化为 `source_type/source_id/uri/retrieved_at/content_hash/locator` 契约，同时保留历史 `type/id` 别名；
- Graph Patch Impact 持久化受影响节点和失效 Artifact；Board 在局部重跑和动态重规划期间隐藏旧的 active 输入，
  并在新输出提交时恢复版本链；
- Snapshot 持久化先写入可恢复的 commit-intent 事件，再写入状态并记录确认事件；回放可从提交意图收据恢复；
- Episodic、Domain、Policy Memory 具备 Gate 晋升、身份作用域、过期和撤销语义。

仍需继续增强的是生产化能力，而不是另起运行时：

- Planner 的通用替代能力路径已经具备；Pack 可以通过 capability 的
  `fallback_capabilities` / `fallback_subagent_types` 声明替代优先级；Pack Evaluation Runner 已支持成功路径、
  capability/tool/gate 失败注入和事件/终态断言；更复杂的质量回退策略仍需扩展；
- 后台 Run 已具备跨进程 Worker 租约和过期恢复；Tool 资源租约已抽象为可替换后端，默认使用文件后端，也支持 Redis 原子获取、续租和释放。
- Domain/Policy Memory 已支持可选 Qdrant 混合检索（`MEMORY_VECTOR_SEARCH_ENABLED=true` 或配置 `QDRANT_URL`），
  无索引时回退词法排序；API lifespan 已通过受治理的 `MemoryExpirationScheduler` 自动执行过期归档，
  人工审核界面和更完整的调度保留策略仍需补齐；显式过期归档和 Admin 审计操作已具备；
- DomainPack Registry 已支持依赖范围校验、确定性 payload SHA-256 校验、外部可信 digest allowlist，以及在无活动 Run
  时的 staging 安装、reload、卸载和失败回滚；远程包获取、签名验真和多版本并存仍需生产化；
- Chat、SSE 和前端已统一消费 `execution_events.jsonl`，前端会按 `Last-Event-ID` 断线重连并去重，
  实时展示 `agent_progress` 和 `assistant_message_ready`；模型 Token 级流式输出、跨进程主动推送和更完整的部分 Artifact 展示仍需增强；
- API v1 的 Run、SSE、Chat Thread、Dashboard 和 Admin 资源已经绑定 user/tenant scope；Admin 副作用现在支持显式
  `COGNITRADE_API_ADMIN_TOKENS` 角色映射；外部身份认证和正式 RBAC/租户目录映射仍需生产化。
- API transport 对 intent 的校验优先读取已安装 DomainPack Registry；未安装 Pack 的核心 intent 仍可直接进入 Core Agent Loop。

当前入口已经具备以下闭环基础：Agent OS 先创建 lazy compound，`MainAgentPlanRole` 在 safe point 调用
`CapabilityDynamicPlanner`，Runner 再物化 leaf；Core Tool、DomainPack Tool 和 External Tool 通过同一个
Tool Registry 执行。旧的 eager 构图仍作为直接调用 `CapabilityPlanner.build_graph` 的兼容能力保留，正常
Agent OS 入口不再使用它。

## 8. 目标验收标准

满足以下条件，才算完成闭环 Agent OS：

- 没有 DomainPack 时仍可执行基础对话和通用 Artifact 操作；
- 安装 Finance Pack 后无需修改核心代码即可获得金融能力；
- Planner 能根据 Board 产出决定继续、暂停、补图、重试或局部重跑；
- 所有 Graph patch 都有 revision、原因和审计记录；
- 所有 ready leaf 都由统一 Runner 调度；
- 所有专业结论都带来源、版本、置信度和责任节点；
- 最终回答同时包含自然语言 `assistant_message` 和结构化 Artifact；
- 高风险交付物必须通过 blocking Gate 或人工审批；
- 任意运行都能暂停、恢复、局部重跑和完整回放；
- 替换一个契约兼容的 Subagent 不会影响上下游节点；
- 新增非金融 DomainPack 时，Agent OS 核心代码零修改。

## 9. 演进顺序

1. 完成 Core/Pack/External Tool 的超时、重试、取消和资源隔离协议。
2. 将 Planner 的 frontier 评估、替代能力选择和 Pack 级补图规则扩展为可验证策略。
3. 完成 Domain Memory、Policy Memory 的审核晋升和撤销流程。
4. 完成 Core Agent 的通用多轮 Tool loop 与模型 Tool-call 适配。
5. 统一长时间运行任务的 Chat、SSE 和前端增量分析展示。
6. 将 Finance Pack 的全部专业工具、方法论和 Gate 完整接入闭环。
7. 使用第二个非金融 Pack 验证核心领域无关性。

## 10. Agent 线程调度与 Tool 调用

### 10.1 两层并发模型

Agent OS 的线程调度需要区分两种并发。线程不是新的业务协议，而是 Runner 执行图节点时使用的运行资源。

```text
ExecutionGraphRunner
  ├─ Graph-level concurrency
  │    └─ 并发执行互不依赖的 ready leaf Subagent
  │
  └─ Agent-level tool calls
       └─ 某个 Subagent 在线程内调用一个或多个受治理 Tool
```

- Graph-level concurrency 由 `ExecutionGraphRunner` 统一控制，受全局并发、Capability 并发和 `parallel_safe` 约束。
- Agent-level tool calls 属于当前 Subagent 的执行过程，不能绕过 Runner 直接启动未登记的外部调用。
- 一个 Agent 可以连续调用多个 Tool，也可以在 Tool 无副作用、输入相互独立且预算允许时并行调用多个 Tool。
- Tool 调用完成后，Agent 决定继续调用、产出 Artifact、请求补图、请求重试或返回失败。
- 只有独立的图节点才可以共享 Graph-level 并发额度；同一 Agent 内部的 Tool 并发必须另有 Tool-level 限制。

### 10.2 Tool Registry 是唯一调用入口

Agent OS 需要固定一个领域无关的 `Tool Registry`。DomainPack 和 Core Capability 只声明 Tool，不直接管理线程、HTTP 客户端或供应商连接。

每个 Tool 至少声明以下契约：

```text
ToolDefinition
├─ tool_name / version
├─ input_schema / output_schema
├─ capabilities
├─ permissions
├─ side_effect_level
├─ parallel_safe / max_concurrency
├─ timeout / retry_policy
├─ idempotency_policy
├─ source_and_provenance_policy
└─ implementation adapter
```

Tool 来源分为三类：

- Core Tool：例如 `conversation.answer` 所需的上下文读取、Artifact 检查、记忆查询和通用 Skill 调用。
- DomainPack Tool：例如 Finance Pack 的行情、财务、公告、板块和资金流数据适配器。
- External / MCP Tool：通过明确注册、Schema 校验、权限和沙箱访问外部服务。

DomainPack 可以提供 Tool 实现，但 Tool 的调度、取消、重试、预算、审计和权限检查仍由 Agent OS Core 负责。没有 Finance Pack 时，Core Tool 仍然可以支持基础对话、摘要、Artifact 检查和允许的通用 Skill。

### 10.3 一次 Agent-Tool 调用的标准时序

```text
ready leaf 被 Runner 选中
  ↓
Agent 读取输入 Artifact 和当前 Graph revision
  ↓
Agent 请求 Tool Registry.call(tool_name, arguments)
  ↓
Runner 检查：白名单、Schema、权限、副作用、审批、预算、并发和幂等键
  ↓
Tool adapter 执行；持续检查 timeout / cancellation
  ↓
记录 tool_call_started / completed / failed 审计事件
  ↓
Tool result 写入受约束的 observation / source Artifact，或作为当前 Agent 的受控返回值
  ↓
Agent 继续决策：再次调用、输出 Artifact、请求 graph patch 或结束
  ↓
Runner 校验并提交 Agent 输出到 ArtifactBoard
```

Tool 的原始返回值不能成为跨 Agent 的隐式共享状态。需要被其他节点复用的数据必须转换成声明过 Schema 的 Artifact，并包含来源、版本、置信度、风险标记和生产节点信息。

### 10.4 治理边界

每次 Tool 调用都必须同时满足以下条件：

- `node.tools` 是 Tool 的显式请求，且是 `graph.governance.allowed_tools` 的子集。
- Tool 输入通过 `input_schema` 校验，输出通过 `output_schema` 校验。
- Tool 所需权限属于当前 Graph 和运行身份的允许集合。
- `write_local`、`write_external` 等副作用调用必须具备幂等键；高风险调用必须先获得人工审批。
- Tool 的 timeout、重试次数、token、费用和并发额度计入当前 Run 预算。
- 取消、暂停和 Graph patch 必须能传递到正在执行的 Tool；不支持协作取消的 Tool 只能在隔离边界内运行。
- 可复用的外部事实必须带有 `source_refs`；不可验证的 Tool 结果不能直接升级为 Domain Memory 或 Policy Memory。
- Tool 调用失败应成为可观察的 Artifact / 事件，交由 Planner 判断重试、替换 Tool、补图或终止，而不是在 Agent 内静默吞掉。

### 10.5 Agent 不依赖 DomainPack 也能调用 Tool

Agent OS 的能力模型应是：

```text
Core Capability Catalog
  + 已安装 DomainPack Capability
  + 已注册 Tool Catalog
  → PlanAgent 组合 ExecutionGraph
```

因此“你是谁”“总结这份 Artifact”“查询本次运行状态”等基础请求在能力目录中也可以被表示为可执行能力，
但这不等于每次都要物化 Graph：

```text
user_request
  → conversation.answer
  → assistant_message
```

当 `conversation.answer` 需要读取运行记录、记忆或用户上传材料时，可以在同一 Main Agent 线程中调用 `memory.query`、`artifact.inspect` 或通用 Skill。普通请求仍留在 Core Agent Loop；只有请求升级到 Task/Graph Mode 后，才把这些调用纳入持久化任务上下文或 ExecutionGraph。无论哪种模式，它们都受到同一 Tool Registry 治理，不形成第二条隐式运行链。

### 10.6 当前实现状态与计划缺口

当前代码已经具备：

- ready leaf 的线程池调度、全局并发和 Capability 并发限制；
- `ExecutionNode.tools` 与 `ExecutionGovernance.allowed_tools` 字段；
- 工具白名单和权限的图级治理校验；
- Core / DomainPack Tool 的统一 Registry、版本、Schema、权限和审计事件；
- Core / DomainPack / External Tool 共用 `ToolDefinition` 静态契约，并支持声明式资源键；
- `ExecutionNodeContext.call_tool()` 和 Run 级/Tool 级并发、Tool 调用预算；
- Finance Pack 的 `financial_data_provider` 通过 Registry 调用 Skill 和数据源适配器；
- Core 与 Graph 的事件流、SSE 游标和 Tool source refs。
- Pack `evaluations/` 已通过统一 Evaluation Schema 加载；真实执行评测复用 CapabilityPlanner、ExecutionGraphRunner、
  ArtifactBoard 和 Quality Gate，并支持可审计的失败注入。

当前仍属于生产化缺口：

- Tool 失败后的 Pack 级替代优先级和更复杂的失败注入组合策略；通用 `isolate`/replan 协议和基础 Evaluation Runner 已具备；
- Tool 资源租约已具备共享文件后端、可选 Redis 后端和长调用续租心跳；生产部署通过 `TOOL_RESOURCE_LEASE_BACKEND=redis`、
  `TOOL_RESOURCE_LEASE_REDIS_URL`、`TOOL_RESOURCE_LEASE_KEY_PREFIX` 和
  `TOOL_RESOURCE_LEASE_REDIS_TIMEOUT_SECONDS` 配置外部后端；Redis 高可用部署策略仍属于运维职责。
- Chat / 前端对长时间运行中间分析叙述已通过 `agent_progress` 事件时间线覆盖；断线期间的更高级别流式模型
  输出和服务端推送仍需继续增强；
- API 认证主体与 Memory/Run tenant scope 的正式绑定。

### 10.7 实施顺序

1. 完成 External / MCP Tool 的 Registry 适配和统一来源契约。
2. 增加 Tool timeout、速率限制、重试、幂等和跨 Run 资源隔离；资源竞争通过声明式租约键协调。
3. 实现 Agent 的通用有限 Tool loop：每轮读取 Board，最多执行配置的 Tool 调用次数，然后产出 Artifact 或请求 Planner 决策。
4. 将 Tool 失败、替代能力和局部重跑规则纳入 Pack Planner policy 并做失败注入评测。
5. 将 Tool observation、source refs 和中间叙述完整接入 ArtifactBoard、事件流、SSE、Chat 和前端。
6. 用基础对话、Finance 数据查询、Legal 文档审查和动态补图分别验证 Core Tool、DomainPack Tool、External Tool 和失败恢复。

### 10.8 Tool 调度验收标准

- 任意 ready leaf 都能通过统一 Registry 调用被允许的 Tool；不允许的 Tool 在执行前被拒绝。
- 多个 ready leaf 可以并发调用各自的 Tool，且不突破 Graph 和 Tool 两层并发上限。
- 一个 Agent 可以在同一线程内完成多步 Tool loop，并在每一步看到已提交的 Board 状态。
- Tool 调用可以被暂停、取消、超时、重试和局部重跑，且每次行为都有审计记录。
- Tool 产出的可复用事实都能追溯到 Tool、版本、参数摘要、来源、Agent 节点和 Artifact 版本。
- Tool 失败时 Planner 能选择重试、替换 Tool、修改子图或终止，不能静默降级成无来源结论。
- 不安装 Finance Pack 时，Core Tool 仍能支持基础问答和通用 Artifact / Memory 操作。
- 前端 Chat、SSE、Execution Graph 和 Admin 对同一次 Tool 调用看到同一份运行状态。

## 11. 运行契约补充

### 11.1 Thread、Run 与 Turn

- 一个用户消息创建一个 `Run`；同一个 Chat `Thread` 可以包含多个 Run。
- 每个 Run 只有一个 `Main Agent` 状态；`PlanAgent` 是该 Main Agent 的规划角色，不是第二个 Agent。
- `graph_id` 只标识该 Run 的 ExecutionGraph，不能替代 Chat `thread_id`。
- Core、Task 和 Graph 模式升级时复用同一个 `run_id`、`thread_id`、事件流和 ArtifactBoard。
- Graph pause/resume 和局部 rerun 保持同一个 Run；每次重跑产生新的 Artifact 版本并保留父版本。
- Core Run 没有 leaf 节点，不能使用按节点的局部 rerun；需要重做时重新创建一个 Turn/Run。

### 11.2 状态与模式转换

```text
received
  -> running(core/task/graph)
  -> paused -> running
  -> waiting_approval -> running
  -> completed | gated | failed | cancelled | timed_out
```

`RequestIntake` 只能产生 accepted 或 needs_clarification。需要 DomainPack、多个 Subagent、并发、Gate、
持久化或恢复控制时，Main Agent 将 Core Loop 升级为 Graph Mode；升级不是新建第二个 Run。

### 11.3 Planner、Evaluator 与 Patch

- `MainAgentPlanRole` 是 Graph patch 的唯一提交者。
- `CapabilityDynamicPlanner` 的输入必须包含当前请求、Pack 能力目录、输入输出契约、Board Artifact、Memory
  引用和治理限制。
- Subagent 和 Tool 只能提交缺口、风险、补图或重跑建议，不能直接修改 Graph。
- Patch 必须携带 `base_revision`、`actor`、原因、策略来源和操作列表；Runner 在 safe point 原子应用。
- 每个 Run 受 `max_graph_patches` 限制；revision 冲突或超限必须生成可观察失败，不得静默覆盖。
- Runner 每个 frontier 完成后允许 Planner 重新评估 Board；没有新增决策时不得生成空 patch。

### 11.4 Artifact 与最终回答

- `ArtifactBoard` 是跨 Subagent 的唯一共享事实面；Tool 原始返回值不能直接作为下游输入。
- 可复用结果必须写成声明过 Schema 的 Artifact，并带有 `source_refs`、版本、置信度、风险、生产节点和父 Artifact。
- 专业交付物使用 `final_artifact_ids`；面向用户的自然语言结果使用 `assistant_message` Artifact。
- `assistant_message` 可以引用专业报告，但不取代专业 Artifact，也不改变 Gate 对专业 Artifact 的要求。
- Core Run 的 `assistant_message` 由 Main Agent 产生；Graph Run 的 `assistant_message` 由 Main Agent 根据通过治理的报告产生。
- Report Composer 只能生成结构化报告或 `assistant_message_draft`；它不能直接提交最终 `assistant_message`。
- Main Agent 是唯一最终回答责任节点，必须在报告通过 blocking Gate 后生成 Graph Run 的 `assistant_message`。

### 11.5 API、Chat 与 SSE

- API、CLI 和 Chat 都调用 Agent OS 的标准入口；外围层只负责鉴权、线程存储、传输和展示。
- `ResponseEnvelope` 同时返回自然语言 `message`、运行模式、Artifact IDs、最终交付 Artifact 和状态。
- Graph 与 Core 都向同一 `execution_events.jsonl` 写入带 `event_id` 的事件；SSE 使用 `Last-Event-ID` 恢复游标。
- `agent_progress` 事件既通过 SSE 增量传输，也从 Run 回放重新构建进度时间线；前端必须按 `event_id` 去重，
  不能只保留最后一条消息。
- Tool 调用事件至少包含 Run、Thread、Graph revision、Node、actor、Tool 版本、参数摘要、状态和来源。
- Chat Thread 持久化用户原文、Run ID、状态、回答和 Artifact 引用，不复制一套独立的业务结果状态。
- API v1 的身份主体会贯穿 Run、SSE、Chat Thread、Dashboard、Approval 和 Memory 查询；越权资源统一表现为不存在，不泄露资源是否存在。

### 11.6 失败、澄清和部分结果

- 缺少必要输入时返回结构化 `needs_clarification`，不得用猜测填充专业事实。
- Tool 失败必须写入事件和失败信息，由 Planner 决定重试、替换、补图或结束。
- Gate 失败返回 `gated`/失败语义，同时保留已经产生的 Artifact；不能把未通过 Gate 的报告伪装成完成。
- 预算、节点数、Tool 调用数、超时或取消命中时，Run 进入可解释终态，并保留回放所需的状态和事件。

## 12. 实现冻结协议

本节把前述原则收敛为实现必须遵守的运行协议，避免不同入口对同一概念产生不同解释。

### 12.1 三种执行模式

| 模式 | 适用范围 | 持久化 | 允许能力 |
|---|---|---|---|
| `Core Agent Loop` | 普通对话、短回答、无需结构化产出的 Core 能力 | Main Agent 状态和事件 | Core Tool / Core Skill |
| `Task Mode` | 需要 Core Tool、可恢复上下文或结构化 Core Artifact，但不需要专业 Pack | Main Agent 状态、Tool 结果和必要 Artifact | Core Tool / Core Subagent |
| `Graph Mode` | DomainPack、多个 Subagent、并发、专业 Artifact、Gate、审批或局部重跑 | 完整 Graph、Board、Gate、Memory 和事件 | 所有受治理的 Core/Pack Subagent |

`Task Mode` 不是第二个运行时，而是同一个 Main Agent 的受持久化 Core Loop。任何 DomainPack Subagent
一旦被选择，必须立即升级到 `Graph Mode`。升级复用同一个 Run、Thread、Main Agent、Tool Registry、
ArtifactBoard 和事件流。

### 12.2 Main Agent Tool Loop

Main Agent 的模型调用必须通过有限循环完成：

```text
读取上下文 -> 模型输出消息或 Tool-call
                    | Tool-call
                    v
              Registry 执行
                    v
              结果写入转录/Board
                    v
              下一轮模型判断
```

Provider 只负责把原生模型协议转换为 `message` 或 `tool_calls`；循环上限、权限、预算、取消、审批和审计
由 Agent OS 负责。每轮都必须持久化 `turn_count`、Tool call IDs 和受限转录；模型不可用时可以使用确定性
Core fallback，但不得绕过 Registry。

### 12.3 Approval 生命周期

高风险节点或声明 `requires_approval` 的 Tool 在执行前必须拥有绑定到 Run、Node、Capability 和版本的审批：

```text
pending -> approved -> execution
pending -> rejected
approved -> revoked / expired
```

没有有效审批的 Graph Run 进入 `waiting_approval`，而不是继续执行或伪装成普通失败。审批完成后通过同一
Run 的恢复协议重新进入 Runner。被拒绝、撤销或过期的审批永远不能被重用。

### 12.4 Tool Registry 的强制字段

所有 Core、DomainPack 和 External/MCP Tool 的静态声明统一使用一个
`ToolDefinition` Schema。`CoreToolDefinition` 和 `PackToolDefinition` 只保留为旧导入路径的兼容别名，
Registry 内部不再根据来源维护第二套 Tool 定义。每个声明必须归一化为同一个 `name/tool_id`，并带有
`namespace`（`core`、`pack` 或 `external`）；Pack 在 Registry 解析边界保留只读和 `subagent` 调用者的安全默认值。

Tool 可以声明 `resource_key_fields` 和 `resource_lease_seconds`。Registry 会根据调用参数和当前运行身份
生成资源键，通过 `ResourceLeaseStore` 取得租约；默认文件后端使用共享运行目录，Redis 后端使用原子
`SET NX` 和 owner-checked Lua 续租/释放；租约冲突返回可重试的拒绝，过期租约可以回收，长调用由 Registry
续租心跳维持；续租失败会取消当前 Tool 调用并生成租约丢失事件。
释放只允许租约持有者执行，获取、冲突、续租、释放和失效都必须进入审计事件。文件后端适合单机或共享运行目录，
生产多 Worker/多主机部署应使用 Redis 后端；两种后端都不能改变 Tool 调用协议。

每个 Tool 的执行必须经过 Registry，并至少执行：输入/输出 Schema 校验、调用者和权限校验、Tool 级并发
和速率限制、超时/取消、重试策略、幂等键、来源要求、审批要求以及审计事件。副作用 Tool 的结果必须
持久化幂等收据；重复调用同一收据只能返回原结果，不能再次产生外部副作用。

### 12.5 Graph Safe Point 与 Patch

Safe point 是一个 frontier 的节点结果已经提交到 ArtifactBoard、运行中的节点已经完成或响应取消、且
Runner 尚未开始下一轮 ready frontier 的时刻。只有 Main Agent 的 PlanAgent role 可以在此处原子应用 patch。
Patch 失败、revision 冲突、节点上限或审批约束失败时，旧 Graph 保持不变并记录失败事件；不能部分应用。
结构性 patch 应同时产生 `GraphPatchImpact`，记录受影响节点和失效 Artifact；Runner 将受影响节点重置为
`pending`，并让 Board 暂时排除旧 Artifact。新节点成功产出后通过版本链替代旧 Artifact；未成功产出前不能继续使用旧结果。

### 12.6 回放与重跑

回放默认只重建已持久化 Snapshot 和事件，不重新执行 Tool。局部重跑必须产生新的 Artifact version，保留
父 Artifact 和原始来源；副作用 Tool 只能通过幂等收据恢复。无法证明幂等性的外部调用必须进入人工审批或
终止为可解释失败。

## 13. 已冻结的补充协议

### 13.1 标准入口与职责边界

- `/api`、`/api/v1`、CLI 和 Chat 的正常请求都进入 `AgentOS.handle_request`；它只调用一次 `RequestIntake`。
- `TaskIntakeService` 和 `RuntimePlanner` 只允许作为旧调用的兼容适配器，不能被标准入口再次调用。
- `RequestIntake` 只输出请求类型、候选 Domain/Capability、槽位、约束、置信度和澄清结果，不决定最终 Graph。
- `Main Agent` 是唯一 Root Agent；`PlanAgent role` 是它的规划角色，负责初始 Graph 和 Graph Patch。
- Core 请求默认不创建 `execution_snapshot.json`；需要 Core Tool 持久化上下文时进入 Task Mode；一旦选择 DomainPack Subagent，立即进入 Graph Mode。
- 一个请求在模式升级时继续使用同一个 `run_id`、`thread_id`、Main Agent、Board、Memory 和事件流。

### 13.2 Graph、Patch 与 Board 事务

- lazy compound 只保存当前请求、Pack 能力目录、输入输出契约和治理限制；叶子选择必须由 PlanAgent 在运行时物化。
- Patch 必须有非空操作列表、`base_revision`、唯一 `patch_id`、actor、策略来源和原因。
- Runner 在提交前校验 revision、操作目标、治理限制和在途取消目标；校验失败不得触发取消。
- 同一 `patch_id` 重复提交只生成审计事件，不重复改变 Graph；过期 Patch 不得覆盖新 revision。
- 重连、移除、替换和重跑操作必须记录 `GraphPatchImpact`；受影响节点的旧输出加入 Snapshot 的
  `invalidated_artifact_ids`，不再作为新的 `active` 输入，但仍可被回放和审计读取。
- 一个节点的多个 Artifact 作为一个批次提交；批次校验失败时不得留下部分输出。
- `ArtifactBoard.list_artifacts()` 使用绑定的默认 Thread；显式传入 `thread_id=None` 表示跨 Thread 的治理查询。

### 13.3 Memory 作用域

- Run Board 保存当前运行工作记忆；Episodic Memory 跨 Run 保存经过 Gate 的案例；Domain/Policy Memory 只能通过评分评测晋升。
- Memory 可按 `run`、`thread`、`user`、`tenant`、`domain` 或 `global` 作用域存储，并可设置过期时间。
- Core Tool 查询必须携带当前 user/tenant/thread 身份；调用者只能看到匹配作用域或公开的 global/domain 记录。
- 过期和撤销记录不得参与普通 verified 查询；所有晋升、撤销和过期事件都必须审计。
- Memory 查询可通过 `MemoryRetrievalBackend` 接入混合向量/词法检索；检索结果仍必须经过本地状态、过期和身份
  作用域过滤，向量索引不是权限边界。索引不可用时必须回退到词法查询并记录可观察的索引失败事件。
- `expires_at` 到期后由受治理的 Memory expiration 操作持久化为 `expired` 状态，并写入审计；API 不得只在
  查询响应中临时伪造过期状态。

### 13.4 模式升级与最终回答权

- Core Agent Loop 只在当前 Main Agent 上下文内完成普通对话和 Core Tool 调用；能力目录中的 Core capability 不因存在于目录中而自动创建 Graph。
- 需要 Core Artifact 持久化或可恢复 Tool 上下文时进入 Task Mode；需要 DomainPack Subagent、并发、专业 Artifact、Gate、审批或局部重跑时进入 Graph Mode。
- 一旦进入 Graph Mode，Pack Subagent 只能由 Runner 调度，不能被 Chat、CLI 或 API 直接调用。
- `Report Composer Subagent` 只能提交报告或草稿 Artifact；最终 `assistant_message` 由同一 Run 的 Main Agent 根据已通过治理的事实生成。
- `gated` 是独立终态，表示交付物仍需 Gate 或人工处理；外围 API、SSE、Chat 和前端不得将其映射为 `failed` 或继续当作运行中。

### 13.5 API 身份作用域

- 生产部署可以通过 `COGNITRADE_API_PRINCIPALS` 声明 JSON 主体映射；每个绑定包含 `token`、`actor_id`、
  `user_id`、`tenant_id` 和 `roles`。命中显式绑定后，身份字段以绑定为真源，`X-User-ID`/`X-Tenant-ID` 只能
  与绑定一致，不能覆盖主体身份；未配置绑定时保留单静态 Token 的本地迁移兼容行为。
- 已认证请求的 `ApiPrincipal` 提供 `user_id` 和 `tenant_id`，创建 Run 和 Chat Thread 时必须持久化到 canonical request 或 Thread metadata。
- Run 的历史、回放、事件、SSE、Trace、Steps、Metrics、恢复、取消、重跑和 Approval 都必须校验相同的 user/tenant scope。
- Chat Thread、Dashboard Artifact、Admin Memory 和 Run-bound Approval 也必须使用同一 scope；跨用户或跨租户访问统一返回资源不存在。
- 未版本化 `/api` 工作区的 Run 列表、详情、回放、暂停、取消、恢复、重跑和审批也必须复用同一 scope；它只是旧传输路径，不能成为绕过治理的旁路。
- 单静态 Bearer token 部署下，缺少身份字段的历史文件只允许默认 `api_token/api_token` principal 读取，作为迁移兼容；带明确 user/tenant 的请求不能回退到无主历史数据。
- 显式主体映射已经提供最小角色边界；外部 IdP、动态租户目录、细粒度策略和完整 RBAC 仍需接入后，清理、重建索引、Pack 管理等管理性副作用才可作为多租户生产能力开放。

### 13.6 后台 Run Worker 租约

- `AgentOSRunService` 创建或恢复后台 Run 前，必须在 Run 目录以独占创建方式取得 Worker lease。
- 有效 lease 包含 `worker_id`、`claimed_at` 和 `expires_at`；其他进程不能同时认领有效 lease。
- Worker 正常结束或失败时释放自身 lease；进程崩溃留下的 lease 只能在过期后被新 Worker 重新认领。
- lease 只防止同一后台 Run 的重复认领，不替代 Graph 节点的幂等收据、Tool 资源锁或分布式队列。

### 13.7 状态与澄清协议

- Run 的 canonical 状态为 `received`、`ready`、`running`、`paused`、
  `waiting_approval`、`completed`、`gated`、`failed`、`cancelled` 和 `timed_out`。
- Leaf Node 的状态为 `pending`、`waiting`、`waiting_approval`、`running`、
  `completed`、`skipped`、`degraded`、`failed`、`cancelled`、`timed_out` 和 `gated`。
- Tool Call 的 `waiting_approval`、`rejected`、`cancelled` 和 `timed_out` 必须与普通
  `failed` 区分；重试通过新的 Tool Call ID 或节点 attempt 表示，不能覆盖原事件。
- `RequestIntake` 只产生 `accepted` 或 `needs_clarification`。澄清不是专业事实，也不是失败。
  Main Agent 在执行中请求澄清时，当前 Run 进入 `paused`，并持久化问题和 `missing_fields`；外围 API
  将其映射为 `needs_clarification`。
- 状态转换必须通过 Agent OS 的状态协议校验；终态只能通过明确的 pause/rerun/recovery 操作重新进入执行，
  不能由普通 Runner 循环隐式复活。

### 13.8 Subagent 控制请求与安全点

- Leaf Subagent 可以在 `NodeExecutionOutput.control_requests` 中提交
  `request_replan`、`request_clarification`、`request_retry`、`request_tool` 或 `stop` 建议。
- 每个建议必须有唯一 `request_id` 和非空 `reason`，并可声明所需 Artifact、首选 Capability 或目标节点。
- Runner 将建议写入 `subagent_control_request` 事件和 Run Snapshot 的待处理队列；Subagent 不能直接修改
  Graph、依赖关系或治理配置。
- Main Agent 的 PlanAgent role 只在 safe point 消费这些建议。`request_retry` 可以转成受版本保护的
  `rerun_from` patch；补充 Artifact 或 Capability 必须经过 Capability Planner 重新校验。
- Planner 成功应用或明确忽略建议后，才清除待处理队列；建议、Patch 和结果仍保留在事件历史中供回放。

### 13.9 Pack Intake 声明协议

- `DomainPackManifest.intake` 是 Pack intent 的正式声明入口；每个 intent 具有唯一 `intent_id`，并可声明
  `required_slots`、`optional_slots`、`examples`、`candidate_capabilities`、`output_artifact_types` 和澄清问题。
- `intake.implementation` 是 Pack-owned 的解析执行器。它可以完成领域实体解析、槽位填充和澄清判断，
  但不能创建或修改 ExecutionGraph，也不能绕过 Main Agent 的 RequestIntake。
- Registry 在加载 Pack 时校验 intent 引用的 Capability 和 Artifact 是否存在。未知引用、重复 intent 或缺少
  resolver 的正式 intent 必须使 Pack 加载失败。
- `planner_policies` 中的 `supported_intents`、`task_intake`、`intent_outputs` 和 `intent_capabilities` 只作为
  旧 Pack 的兼容回退；正常入口优先使用 `DomainPackManifest.intake` 和 intent 声明。
- `RequestIntakeDecision.candidate_capabilities` 是建议集合，不是最终执行清单；CapabilityPlanner 必须重新校验
  输入输出连通性、权限、预算、版本和治理限制。

### 13.10 Quality Gate Evaluator 协议

- `QualityGateDefinition.evaluator_capability` 必须引用当前 Pack 已注册的 Capability；Gate evaluator 是只读的
  Pack-owned 检查器，不是第二个调度器，也不能直接修改 Board 或 Graph。
- Runner 在结构化报告通过基础契约检查后调用 evaluator。Evaluator 接收只读的 Gate Evaluation Context，
  可读取目标 Artifact、同类 Artifact、Board、Graph 和 Gate config。
- Evaluator 必须返回 `verdict`（`pass`、`fail` 或 `needs_review`）、`reason`、可选 `score` 和 `evidence_refs`。
- evaluator 不存在、返回值不符合 Schema 或执行异常时，Runner 必须 fail closed 为 `needs_review`，并生成
  `gate_evaluator_failed` 审计事件；blocking Gate 在此状态下不得生成已交付的成功语义。
- Gate 评估结果必须写入 Run Snapshot 和事件流，包含 Gate、Artifact、Evaluator、证据引用和评分；重跑时生成
  新评估记录，不覆盖旧记录。

### 13.11 Pack Evaluation Runner 协议

- `DomainPackManifest.evaluations` 中的每个文件必须解析为 `PackEvaluationDefinition`；Registry 必须校验路径
  不得逃逸 Pack 根目录，并拒绝重复 evaluation ID。
- `mode=contract` 只验证 Artifact、Gate、Capability 和 Tool 声明；`mode=execution` 必须提供目标 Artifact、
  输入 Artifact 和期望终态，并通过标准 `CapabilityPlanner -> ExecutionGraphRunner -> ArtifactBoard` 执行。
- 评测可声明 capability、Tool 或 Gate 失败注入。注入只能在 Runner/Registry 治理边界内发生，必须写入
  `evaluation_failure_injected` 事件，不能直接修改 Pack 产出或绕过权限检查。
- 评测结果必须断言 Run 终态、Gate verdict、必需/禁止事件和必要 Artifact；节点失败后已持久化的 Snapshot
  仍可被评测层读取，预期失败不能被误判为 Runner 自身崩溃。
- Evaluation Runner 只能决定 Pack 是否满足声明的评测门槛，不能修改生产 Graph、Memory 或长期 Policy Memory。

### 13.12 DomainPack 生命周期与可信加载

- `DomainPackManifest.version` 使用数值点版本（例如 `1.2.0`）。Pack 可以声明依赖，每个依赖可以设置包含边界的
  `min_version` / `max_version`；非 optional 依赖缺失或版本不在范围内时，Registry 加载失败，不能进入能力目录。
- `DomainPackManifest.integrity.payload_sha256` 是 Pack payload 的 SHA-256。计算输入按相对路径排序，包含除
  `domain.yaml`、`__pycache__`、`.pyc` 和 `.pyo` 之外的全部文件；路径、分隔符和文件内容都参与摘要，避免不同机器产生不同结果。
- `DOMAIN_PACK_REQUIRE_INTEGRITY=true` 时，Pack 必须声明合法 digest，或命中 `DOMAIN_PACK_TRUSTED_DIGESTS` 的外部 allowlist。
  如果 manifest 声明 digest，实际 payload 不匹配时无论开发或生产模式都必须拒绝加载。
- Registry 的 `install` 只接受本地 Pack 目录，先复制到 registry root 下的 staging 目录，再验证完整布局、Schema、依赖和 digest，
  最后原子切换；切换后的 reload 失败时必须恢复原目录。`uninstall` 也先移动到临时备份，只有移除后依赖仍然满足才真正删除。
- Agent OS 的 lifecycle 操作必须在没有活动 Run、没有准备中 Run 时执行；Core、Planner、Intake、Tool Registry 和 Dynamic Planner
  必须一起绑定到同一个新 Registry snapshot，不能只刷新其中一个服务。
- 活动 Pack 的依赖只能解析活动 Pack；归档 Graph 的依赖可以解析活动 Pack 或版本范围兼容的归档 Pack。
  Pack 替换或卸载时必须先把旧 payload 放入归档路径，再执行 Registry reload；归档、reload 和失败回滚必须作为一个原子操作，
  不能让依赖校验观察到“旧 Pack 已移走但归档尚未建立”的中间状态。
- 当前实现只保证本地目录的完整性和生命周期原子性，不把 payload digest 当作签名或身份认证；替换或卸载时旧版本会归档到
  `.versions/<domain_id>/<version>`，Graph 会按持久化的 `domain_version` 解析旧 Capability、Schema 和 Gate。远程来源、签名验证和依赖包仓库
  仍必须在生产部署层补齐。

### 13.13 边界命名、澄清续接与生产实现顺序

- `Supervisor` 不是第二个 Agent。最终运行时只保留一个 `Main Agent`；`PlanAgent role` 负责规划和提交 Graph Patch，
  `ExecutionGraphRunner` 负责调度，`QualityGateEvaluator` 负责只读质量检查，继续、重规划、重跑和最终回答由 Main Agent 决定。
  旧 `RuntimeSupervisor`、旧 Handler 和兼容 facade 不得进入正常研究执行路径。
- 一个用户请求对应一个 Run，一个 Thread 可以包含多个 Run。RequestIntake 在首次判断缺少槽位时可以返回
  `needs_clarification`；用户对该问题的回答必须作为同一 Thread 的新 Turn 续接原 Run，恢复原 Main Agent、Memory、Board、
  Graph handle 和事件游标。用户发起无关主题时才创建新的 Run。
- 澄清续接必须保留原始问题、澄清问题、补充回答和合并后的 RequestIntake decision 版本；补充回答不能覆盖原始请求，
  也不能直接写入专业 Artifact，直到重新经过 RequestIntake 和 Main Agent 校验。
- Skill 是版本化的指令、Schema、可选 Tool 和方法论约束集合，不是第二套 Agent Runtime。Skill 的文件、网络、MCP、Shell
  和数据源操作必须通过统一 Tool Registry；Skill 不能直接写 Board、Memory、Graph 或运行控制文件。Core 的
  `core.skill.invoke` 只能调用显式注入并通过治理注册的通用 Skill runtime；没有安装或注入 Skill 时必须拒绝调用，
  不能隐式加载任何 Finance、Legal 或其他领域实现。
- 治理策略的优先级固定为：Agent OS Core 安全边界 > 租户策略 > Run 约束 > DomainPack 策略 > Subagent 建议。
  下层策略只能收紧上层限制，不能提高节点、Tool、Token、费用、并发或权限上限。
- `gated` 允许 Main Agent 生成一条只说明“等待 Gate 或人工处理”的状态消息，但不得生成声称专业交付已经通过的
  `assistant_message`；只有 blocking Gate 通过后，才允许生成包含专业结论的最终回答。
- 生产实现优先补齐顺序为：Graph/Artifact/Tool 协议一致性，单机 Runner 的暂停恢复和回放，多 Worker 队列与租约，
  外部身份和 RBAC，Pack 签名与远程仓库，最后再增强 Token streaming 和前端高级展示。模型供应商、队列产品和 UI 组件
  不属于 Agent OS 核心协议。

## 14. 执行契约与实现状态

前面的章节已经冻结了组件边界，以下细节是 Core、Pack、API 和前端必须共享的执行契约，不是新的 Agent 类型，也不是
新的运行时。已落地的条目由标准 Agent OS 路径直接执行；尚未完成的条目仍必须遵守这里冻结的字段和状态语义。

### 14.1 RequestIntake 是混合识别阶段，不是固定业务节点

- 每个请求只经过一次 Agent OS `RequestIntake`。它可以组合 Core 识别器、已安装 Pack resolver 和模型识别器，采用
  `explicit > pack resolver > model > deterministic fallback` 的顺序；关键词规则只能作为低置信度 fallback。
- Intake 的输出是 `RequestIntakeDecision`：`request_kind`、候选 Domain、候选 intent、候选 Capability、槽位、约束、
  置信度、风险和澄清结果。候选结果不是最终 Graph，也不能直接启动 Subagent。
- 多个 Domain 或 intent 无法在治理要求的置信度下区分时，必须返回结构化澄清；不能随机选择 Pack，也不能用猜测填充专业槽位。
- `Main Agent` 可以在 Core Loop 中回答基础问题；只有 Main Agent 判断需要 Core Task 或 DomainPack 能力时，才升级执行模式。
  DomainPack 不得通过注册固定 Graph 节点替代这个判断。

### 14.2 Main Agent 与 PlanAgent role 的决策契约

- `PlanAgent role` 与 Main Agent 共享同一模型上下文、Run、Thread、Tool Registry、Memory 和预算，不得创建第二个主 Agent。
- Planner 每次只产生一个结构化 `PlanDecision`，其动作只能是 `continue`、`replan`、`retry`、`clarify`、`stop` 或
  `wait_approval`；结构化动作之外的自然语言只能作为说明，不能改变运行状态。
- Planner 的输入必须包含当前 RequestIntake decision、Graph revision、Board active Artifact、失效 Artifact、待处理控制请求、
  可用 Capability/Skill/Tool 目录、Memory 引用和治理剩余预算。
- Planner 可以使用受治理的 Core Observation Tool；它不能绕过 Runner 直接执行 Pack Subagent。模型提出的 Graph patch 必须先经过
  确定性 Schema、权限、连通性、节点上限和 revision 校验，再由 safe point 原子提交。
- 模型不可用、输出无法解析或 Planner 超限时，必须使用明确的 deterministic fallback 或进入可解释失败，不能静默采用任意 Graph。

### 14.3 Graph 的输入绑定、ready 规则和物化规则

- `ExecutionNode.consumes` 是节点输入契约，`ArtifactPort.selector` 是从 Board 选择版本的唯一规则；`input_artifact_ids` 是物化后的
  快照引用，不能作为另一个事实来源。
- 必需输入必须存在、Schema 版本兼容且处于当前 active 版本；失效、superseded、rejected 和未通过 blocking Gate 的 Artifact
  不得作为 active 输入。允许部分依赖时必须显式声明 `allow_partial_dependencies`。
- leaf 节点只有在依赖节点完成、输入绑定成功、工具和权限通过、审批有效且预算可用时才是 `ready`。compound 节点不执行业务，
  只由 PlanAgent 在需要时展开为新的 leaf 或子 compound。
- 每次 frontier 完成后，Runner 先提交节点输出和状态，再让 Main Agent 评估是否继续、补图、重跑或结束；不能在未提交 Board 的情况下规划下一轮。
- executor 必须按 `domain_id + domain_version + capability + subagent_type` 从当前 Registry snapshot 解析，禁止在 Core 中直接导入 Pack 实现。

### 14.4 Subagent 与 Skill 的声明契约

- Pack 必须能够声明 `artifact_types`、`skills`、`tools`、`capabilities/subagents`、`intake`、`quality_gates`、`report_contracts` 和
  `evaluations`。Skill 至少包含名称、版本、指令/Schema、允许调用者、允许 Tool 和实现入口。
- Capability 的 `implementation` 必须解析为统一的 Subagent runtime contract：接收受治理的 Node Context，读取绑定输入，调用
  Registry Tool，返回 `NodeExecutionOutput`，并只能通过 `control_requests` 提交重规划建议。
- Skill 不是 Subagent，也不能直接写 Board、Memory、Graph 或控制文件。Skill 的文件、网络、MCP、Shell 和数据源访问都必须经过
  Tool Registry。Core `core.skill.invoke` 只能调用显式注入且通过权限校验的 Skill。
- Tool 只有在需要独立生命周期、独立预算、并发、暂停、重试、持久化或用户可见状态时，才由 PlanAgent 提升为 leaf Subagent；普通
  Tool 调用留在当前 Agent 的 Tool Loop 中。

### 14.5 Artifact、来源和用户可见消息

- `ArtifactBoard` 必须校验 Artifact type、schema version、producer、parent lineage、source refs、confidence、risk flags 和
  status；一个节点的多个输出必须作为一个事务批次提交。
- `source_refs` 的规范字段为 `source_type`、`source_id` 或 `uri`、`retrieved_at`、`content_hash`、`locator` 和可选的
  `provider/version`。来源不足时，专业 Artifact 只能标记为 `needs_review`，不得生成通过 Gate 的结论。
- `agent_progress` 只表达可展示的阶段、状态、摘要和依据，不包含隐藏推理过程；它可以在运行中增量发送并可从事件回放重建。
  `assistant_message_draft` 只能是草稿，`assistant_message` 由同一 Run 的 Main Agent 生成并承担最终责任。
- Graph Run 的最终回答必须同时返回自然语言消息、最终 Artifact IDs、Gate/Approval 状态和可追溯来源；`gated` 不得伪装为 `completed`。

### 14.6 失败、重试、缓存和副作用

- Tool/Node 失败必须区分 `rejected`、`waiting_approval`、`cancelled`、`timed_out`、`failed` 和 `degraded`，并记录原始 attempt；
  重试只能创建新的 attempt 和事件，不能覆盖历史结果。
- 纯读取 Tool 可以按声明的 cache scope 缓存；缓存键必须包含 Tool 版本、规范化参数、身份作用域、Pack version 和必要的来源新鲜度。
  Graph revision 或输入 Artifact 版本变化时，受影响缓存必须失效。
- `write_local` 和 `write_external` 必须有幂等键和持久化收据；无法证明幂等的外部调用在 replay/rerun 时只能恢复为人工审批或可解释失败。
- 不支持协作取消的 Tool 必须在隔离边界执行；取消、超时、租约丢失和进程崩溃都不得把未确认的外部副作用报告为成功。

### 14.7 事件、API 和持久化边界

- 每个事件必须包含 `event_id`、`event_type`、`created_at`、`run_id`、`thread_id`、`request_id`、`graph_revision`、`actor` 和
  可选的 `node_id/tool_call_id/artifact_ids`；同一 Run 内按 event_id 顺序回放，重复投递必须幂等。
- API、CLI、Chat 和 SSE 只负责传输与展示，统一调用 `AgentOS.handle_request`；同步完成、后台运行、澄清、Gate、审批和失败的 HTTP
  状态及 `ResponseEnvelope.status` 映射必须一致，越权资源统一返回不存在。
- Snapshot 是当前可恢复状态，事件流是不可变审计历史；写入状态前先写入包含完整 Snapshot 的提交意图收据，状态写入完成后再写入
  持久化确认事件，崩溃恢复不得重复执行已确认的节点或副作用 Tool。
- SSE 断线重连使用 `Last-Event-ID`；服务端必须补发游标之后的事件，前端按 `event_id` 去重，并同时支持运行中进度和最终回答事件。
