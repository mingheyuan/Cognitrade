# Cognitrade Codex 风格 Turn/Task 架构方案

> 状态：第一阶段的 TurnRunner/RegularTask/GraphExecutor 适配壳已实施；Core provider/tool loop、TaskSnapshot、ReviewTask、CompactTask、StopHook 和 Follow-upRuntime 仍保留在迁移阶段，尚未全部下沉到 RegularExecutor
> 分支：plan/design-and-issues
> 范围：描述 Cognitrade Main Agent 的运行时架构重构及当前第一阶段实现边界
> 参考：Cognitrade 当前 Agent OS / Main Agent / GraphRunner，以及 Codex codex-rs 的 Turn、Task、Session 和 child thread 设计

## 目录

- [1. 文档定位](#1-文档定位)
- [2. 执行摘要](#2-执行摘要)
- [3. 当前 Cognitrade 结构](#3-当前-cognitrade-结构)
- [4. Codex 的参考模型](#4-codex-的参考模型)
- [5. 目标架构](#5-目标架构)
- [6. 核心术语与边界](#6-核心术语与边界)
- [7. 核心接口设计](#7-核心接口设计)
- [8. TurnRunner 的职责](#8-turnrunner-的职责)
- [9. Task 执行策略](#9-task-执行策略)
- [10. Turn 状态机](#10-turn-状态机)
- [11. 普通 Turn 的完整流程](#11-普通-turn-的完整流程)
- [12. Graph Turn 的完整流程](#12-graph-turn-的完整流程)
- [13. MainAgent 的职责调整](#13-mainagent-的职责调整)
- [14. ChildTurn 与动态 Runtime Role](#14-childturn-与动态-runtime-role)
- [15. Thread、Run、Turn、Task、Graph、Node、Artifact 关系](#15-threadrunturntaskgraphnodeartifact-关系)
- [16. 统一事件协议](#16-统一事件协议)
- [17. 暂停、取消、超时和审批](#17-暂停取消超时和审批)
- [18. 错误模型与恢复](#18-错误模型与恢复)
- [19. 持久化设计](#19-持久化设计)
- [20. 迁移计划](#20-迁移计划)
- [21. 测试方案](#21-测试方案)
- [22. 观测与指标](#22-观测与指标)
- [23. 兼容策略](#23-兼容策略)
- [24. 风险与取舍](#24-风险与取舍)
- [25. 不建议的方案](#25-不建议的方案)
- [26. 实施顺序](#26-实施顺序)
- [27. 验收标准](#27-验收标准)
- [28. 一个普通 Turn 的示例](#28-一个普通-turn-的示例)
- [29. 一个 Graph Turn 的示例](#29-一个-graph-turn-的示例)
- [30. 总结](#30-总结)
- [31. 完整 Turn 链路：原始骨架与配置挂载](#31-完整-turn-链路原始骨架与配置挂载)

## 1. 文档定位

本文回答一个核心问题：

> 如何把 Cognitrade 当前“普通对话走 Main Agent Loop、领域任务走 GraphRunner”的两条顶层代码路径，收敛为类似 Codex 的统一 Turn 生命周期，同时保留不同任务的执行策略？

这里的“统一”不是把所有工作都塞进一个大循环，也不是让普通对话强制创建复杂 Execution Graph。正确的拆分是：

~~~text
一次用户请求
  -> 一个可持久化的 Turn
       -> 一个执行策略 Task
            -> RegularTask / ReviewTask / CompactTask
                 -> RegularExecutor
                      -> CoreLoopExecutor 或（GraphExecutor）
~~~

其中：

- Turn 表示一次用户意图的生命周期；
- Task 表示该 Turn 当前采用的执行策略；
- MainAgent 负责理解、路由、计划和最终回答；
- TurnRunner 负责统一生命周期、控制、恢复、事件和持久化；
- GraphRunner 负责 Graph 内部的节点调度，不再负责整个 Agent OS 的顶层生命周期；
- Tool、Node、ChildTurn 分别表示工具调用、图节点和子任务，不应互相替代。

## 2. 执行摘要

### 2.1 推荐方案

新增一个顶层 TurnRunner，把原始 Turn 固定在同一个生命周期内，再按配置选择 RegularTask 的执行模式：

~~~text
AgentOS.handle_request()
  -> RequestIntake
  -> MainAgent.route()
  -> TurnRunner.start_or_resume()
       -> TaskFactory
            -> RegularTask       Core / Task / Graph mode
                 -> RegularExecutor
                      -> CoreLoopExecutor
                      ->（GraphExecutor）
            -> ReviewTask        对既有结果进行审查
            -> CompactTask       压缩上下文或整理状态
       -> TurnRunner.finalize()
  -> ResponseEnvelope
~~~

第一阶段不重写 GraphRunner 的节点调度算法。RegularTask 始终进入同一个 RegularExecutor loop；当 TurnConfig 启用 Graph 时，RegularExecutor 在自己的 step 内挂载 GraphExecutor，由 GraphExecutor 通过适配器调用现有 GraphRunner。GraphRunner 只负责 Graph 内部调度，不拥有 Turn 生命周期，也不成为 MainAgent 的第二条顶层执行路径。

### 2.2 解决的问题

该方案解决以下结构性问题：

1. 普通对话和领域任务不再由两个顶层生命周期分别处理。
2. 暂停、恢复、取消、超时、幂等和最终响应由一个组件管理。
3. MainAgent 不再同时承担路由、循环、状态机、存储和响应封装。
4. GraphRunner 保持自己的领域能力，但边界清晰为“Graph 内部调度器”；GraphExecutor 只负责把它装载进 RegularExecutor 的单一 loop。
5. 子 agent、动态 runtime role 和后续 review/compact 能力可以接入同一个模型。
6. skill/plugin 是否安装只影响能力目录和上下文，不再导致另一套 Turn 代码路径。
7. API、Chat、SSE、CLI 看到同一种 Turn 事件和状态。

### 2.3 不解决的问题

本文不试图在一次改造中解决：

- 更换 LLM Provider；
- 重写 Graph scheduler；
- 设计新的前端交互；
- 引入全局分布式 scheduler；
- 重新定义所有 DomainPack 的业务逻辑；
- 把所有历史持久化文件一次性迁移；
- 用一个抽象消灭 Core、Task 和 Graph 之间的实际差异。

### 2.4 第一阶段的实现边界

当前代码实现的是“统一入口和统一生命周期适配层”，不是一次性完成最终职责迁移：

- `TurnRunner` 已统一创建/恢复 `TurnSnapshot`、Task 选择、事件游标、终态校验和最终响应持久化；
- `RegularTask`/`RegularExecutor` 已成为 Core 与 Graph 的共同选择边界；
- `CoreLoopExecutor` 当前调用 `MainAgent._run_core_task()`，所以 provider/tool loop 仍在 MainAgent 内部；
- `GraphExecutor` 当前通过 `GraphRunnerAdapter` 调用完整的既有 GraphRunner 执行/恢复流程，GraphRunner 的内部 frontier 并发、Gate、patch 和 Node 状态逻辑暂不重写；
- StopHook、SubagentStop、Follow-upRuntime、Mailbox 和独立 `TaskSnapshot` 已在目标协议中定义，但本阶段只保留配置字段和迁移接口，不宣称已经完成运行时接线。
- 第一阶段没有新增由 `TurnRunner` 直接暴露的 `pause/resume/cancel` API；AgentOS 的兼容控制入口仍按 Core/Graph 适配到共享的控制文件或 GraphRunner，统一控制 API 是后续阶段退出条件。
- 第一阶段的 `GraphRunnerAdapter` 返回既有 `ExecutionRunResult`，`GraphExecutor` 仍包裹完整的 GraphRunner run/resume；`GraphStepOutcome`、Graph safe-point Hook 和 Graph 内 follow-up 仍是后续迁移工作。
- 路由、澄清和部分兼容入口产生的事件可能发生在根 Turn 创建前，属于 legacy pre-Turn 事件，使用 Run/Request 身份但不保证 `turn_id`；TurnRunner 执行阶段的事件才使用根 Turn 身份和连续游标。
- 兼容 Graph 适配层仍负责生成/补齐 assistant Artifact，后台恢复也保留一层 ResponseEnvelope 兼容转换；“TurnRunner 唯一最终响应收敛”是目标态，尚未作为第一阶段完成声明。

因此，“一个 RegularTask、一个 Turn 生命周期”是当前已接入的外层边界；“RegularExecutor 完全拥有原始 provider/tool loop、统一控制 API 和 Hook 决策”是后续迁移目标。

这些问题可以在 Turn 协议稳定后单独处理。

## 3. 当前 Cognitrade 结构

### 3.1 当前顶层入口

目前 AgentOS.handle_request() 大致承担以下职责：

~~~text
接收 RequestEnvelope
  -> 合并待澄清请求
  -> 幂等键查找已有 Run
  -> 创建或复用 Run
  -> 创建 thread_id
  -> 调用 MainAgent.route()
  -> 处理 needs_clarification
  -> 应用 route decision
  -> 按 request_kind 分流
       -> domain_task: run_request() -> GraphRunner
       -> 其他请求: MainAgent Core Agent Loop
  -> 封装 ResponseEnvelope
  -> 持久化最终 response
~~~

相关代码位置：

- research_system/runtime/agent_os.py:428 附近：AgentOS.handle_request()；
- research_system/runtime/core/main_agent.py:699 附近：MainAgent._run_agent_loop()；
- research_system/runtime/execution/core/graph_runner.py:538 附近：Graph 主执行循环；
- research_system/runtime/core/agent_control.py:73 附近：暂停、恢复、取消控制。

### 3.2 当前两条路径

~~~text
                          +----------------------+
                          | AgentOS.handle_request|
                          +----------+-----------+
                                     |
                              MainAgent.route
                                     |
                    +----------------+----------------+
                    |                                 |
             普通对话 / Core                    domain_task
                    |                                 |
          MainAgent._run_agent_loop             run_request
                    |                                 |
          Provider + Tool Loop                 GraphRunner.run
                    |                                 |
          Core response                        Graph result
                    |                                 |
                    +---------------+-------------+
                                    |
                             ResponseEnvelope
~~~

两条路径都能完成工作，但它们不是同一个生命周期的两个执行策略，而是两个顶层控制器：

- Core 路径的循环、工具结果、进度和状态主要在 MainAgent 内部处理；
- Graph 路径的状态、节点、frontier、审批、暂停和结果主要在 GraphRunner 内部处理；
- AgentOS 需要知道什么时候进入哪条路径以及如何分别生成响应；
- 相同的 run_id、thread_id、事件和控制语义容易在两处出现细微差异。

### 3.3 当前问题

#### 问题 A：生命周期和执行策略耦合

MainAgent._run_agent_loop() 既是“普通任务的执行策略”，又隐含承担 Turn 的开始、循环、状态更新、暂停和结束。

GraphRunner.run() 同样既是“Graph 执行器”，又包含 Graph Run 的终态、持久化、控制检查和事件发射。

这会导致以后增加 ReviewTask 或 CompactTask 时出现第三套生命周期。

#### 问题 B：顶层恢复语义不统一

普通 Core Loop 需要根据 transcript 和 Main Agent state 恢复；

Graph 需要根据 graph snapshot、node state、Board 和 pending approval 恢复。

它们的内部状态不同是合理的，但“恢复一个用户 Turn”不应该由调用者了解这些细节。

#### 问题 C：状态边界容易重复

当前至少涉及：

- Run 状态；
- Main Agent 状态；
- Graph 状态；
- Node 状态；
- Agent control 状态；
- pending clarification；
- response 持久化状态。

如果每条路径都自己解释这些状态，前端和 API 会遇到同一业务状态在不同入口表现不一致的问题。

#### 问题 D：Main Agent 变成过深模块

Main Agent 同时拥有：

- 请求意图判断；
- Core/Domain 路由；
- Provider 调用；
- Tool loop；
- 进度事件；
- transcript；
- pause/cancel；
- 最终回答；
- response 持久化协作。

它的调用接口看似简单，但内部深度过大，测试和替换执行策略的成本都会上升。

#### 问题 E：子 agent 与 Turn 概念没有统一投影

Graph node 可以代表一个 subagent，但普通 Core tool loop 也可能产生多个 provider turn。动态 runtime role 还需要通过 registry、adapter 或 materialization 接入。

如果不先定义统一边界，很容易出现：

- 一个 Tool call 被当成一个 Turn；
- 一个 Graph node 被当成一个 Thread；
- 一个 child thread 直接修改 parent Run；
- 动态 role 绕过统一权限和事件协议。

## 4. Codex 的参考模型

### 4.1 核心思想

Codex 将“用户请求的一次推进”抽象成 Turn，将“如何推进”抽象成 Task。

概念关系可以简化为：

~~~text
Session / Thread
  -> Turn
       -> Task
            -> step / model call / tool call
~~~

Turn 负责可观察的生命周期，Task 负责策略。

Codex 中可以有不同 Task：

| Task 类型 | 主要职责 | 是否与普通 Turn 共用生命周期 |
|---|---|---|
| RegularTask | 普通 agent 工作循环 | 是 |
| ReviewTask | 审查已有结果或上下文 | 是 |
| CompactTask | 压缩或整理上下文 | 是 |
| child task | 在子线程中执行派生工作 | 是，但拥有独立 child thread |

这带来两个重要结论：

1. “任务类型不同”不意味着“顶层运行时不同”；
2. “安装了什么能力”也不应该决定使用另一套生命周期。

### 4.2 对 Cognitrade 的对应关系

| Codex 概念 | Cognitrade 建议对应物 |
|---|---|
| Session / Thread | Chat Thread |
| Turn | 用户请求在一个 Run 中的一次可恢复执行 |
| RegularTask | 普通 Core 对话和 Core Tool Loop |
| ReviewTask | 对报告、Artifact 或执行结果进行审查 |
| CompactTask | transcript、Board 引用或上下文压缩 |
| child thread | 子 agent 的 ChildTurn |
| task output | TaskOutcome |
| turn state | 统一 TurnSnapshot |
| session event | TurnEvent / execution event |
| model/tool step | provider call 或 Tool invocation |

### 4.3 不应照搬的部分

Cognitrade 是研究与交易数据系统，不能机械复制 Codex 的所有实现：

- Graph 有明确的节点、依赖、Artifact 和 Gate；
- 一个 Graph frontier 可能并行执行多个 subagent；
- 运行状态需要保存 Board、Graph revision 和产物 lineage；
- 某些工具有数据源速率限制、资源租约和审批；
- 业务任务需要 gated 这类“结果存在但不允许交付”的终态。

因此，建议照搬 Codex 的“统一 Turn + Task 策略”边界，保留 Cognitrade 的 Graph、Board、Gate 和数据源治理。

## 5. 目标架构

### 5.1 总体结构

~~~text
+-------------------+
| API / Chat / CLI   |
+---------+---------+
          |
          v
+-------------------+
| AgentOS            |
| - auth/scope       |
| - intake bridge    |
| - compatibility    |
+---------+---------+
          |
          v
+-------------------+
| TurnRunner         |
| - create/resume    |
| - lifecycle        |
| - control          |
| - event            |
| - persistence      |
| - finalize         |
+---------+---------+
          |
          v
+-------------------+
| TaskFactory        |
| - mode selection   |
| - task restore     |
+---------+---------+
          |
          v
+-------------------+
| RegularTask       |
| - original Turn   |
|   loop container   |
+---------+---------+
          |
          v
+-------------------+
| RegularExecutor   |
| - one serial loop |
| - control/checkpoint |
+----+---------+----+
     |         |
     |         +-----------------------------+
     |                                       |
     v                                       v
+-------------+                   +----------------------+
| CoreLoop    |                   | GraphExecutor        |
| Executor    |                   | - optional mode     |
|             |                   | - GraphRunnerAdapter|
+------+------+                   +----------+-----------+
       |                                     |
       |                                     v
       |                            +----------------------+
       |                            | GraphRunner          |
       |                            | frontier/concurrency|
       |                            | node state/Board    |
       |                            | Gate/patch/recovery |
       |                            +----------+-----------+
       |                                       |
       +-------------------+-------------------+
                           v
                    ArtifactBoard
                    ToolRegistry
                    RoleRegistry
                    EventStore

+-------------------+
| Special Tasks     |
| ReviewTask        |
| CompactTask       |
+-------------------+
~~~

### 5.2 设计原则

#### 原则 1：只有一个顶层 Turn 生命周期

创建、恢复、运行、暂停、等待审批、取消、超时、失败、完成和最终响应都由 TurnRunner 统一协调。

#### 原则 2：RegularTask 只有一个原始 Turn loop

TurnRunner 不知道 provider 细节，也不实现 Graph scheduler。它调用 RegularTask；RegularTask 只拥有一个 RegularExecutor loop。RegularExecutor 每一轮根据 TurnConfig 选择 CoreLoopExecutor 或可选的 GraphExecutor。GraphExecutor 是这个 loop 的内部 step executor，不是 RegularTask 的并列 Task，也不是第二个 MainAgent loop。ReviewTask 和 CompactTask 可以作为需要特殊语义的 Task 接入同一生命周期。

Graph 的并发不改变这条边界：RegularExecutor 可以让 GraphRunner 在一个 frontier 内受控并发执行多个 Node 或 ChildTurn，但所有完成、失败、超时和子 agent 反馈都必须回到 RegularExecutor 的事件队列，在单一安全点按顺序写入 TurnTaskState/GraphSnapshot，再决定下一步。

#### 原则 3：状态由拥有者保存，生命周期由 TurnRunner 管理

- TurnSnapshot 保存 Turn 级状态；
- RegularTaskState 保存 Turn loop transcript、Tool state 和模式状态；
- RegularTaskState 逻辑上拥有 GraphSnapshot，GraphSnapshot 保存 Graph/Node/frontier/Board 状态；物理上可以单独存储并通过 graph_state_ref 引用；
- TurnRunner 负责在这些状态之间统一恢复和收敛。

#### 原则 4：模式升级不创建第二条请求

Core 请求切换为 task mode 或 graph mode 时，继续使用同一个 run_id、thread_id、turn_id、MainAgent 上下文、ArtifactBoard 和事件流。

#### 原则 5：适配器先于重写

第一阶段通过 GraphRunnerAdapter 接入当前 GraphRunner。确认统一协议稳定后，再逐步移动 GraphRunner 中属于顶层生命周期的代码；GraphRunner 的 frontier 并发、Node 状态推进、Gate、patch 和恢复能力保留在 GraphExecutor/GraphRunner 内部，但不再拥有顶层 Turn 控制权。

## 6. 核心术语与边界

### 6.1 Thread

用户可见的会话容器。一个 Thread 可以包含多个 Run，也可以包含多个用户请求形成的多个 Turn。

Thread 负责：

- 对话上下文索引；
- 用户可见历史；
- parent/child thread 关系；
- 访问作用域。

Thread 不负责：

- 执行节点；
- 调用 Tool；
- 决定 Graph frontier；
- 保存单次请求的终态。

### 6.2 Run

一次用户请求在系统内的业务运行记录。历史协议已经以 run_id 作为持久化和控制的主要标识。

一个 Run 通常对应一个根 Turn，但为了兼容已有系统，建议在迁移阶段允许：

~~~text
一个 Run
  -> root Turn
  -> 若干 retry/resume continuation
  -> 若干 ChildTurn
~~~

Run 是 API、审批、回放和最终结果的边界。

### 6.3 Turn

一个 Turn 是一次执行推进的统一生命周期对象。它包含：

- turn_id；
- 所属 run_id / thread_id；
- 输入；
- 选择的 Task 类型；
- 当前生命周期状态；
- 当前 Task 状态引用；
- 产生的消息、Artifact 和错误；
- continuation 信息。

Turn 不等于模型的一次调用，也不等于一个 Tool call。

### 6.4 Task

Task 是 Turn 的执行策略。Root Turn 默认使用 RegularTask；RegularTask 可以在 Core mode 或 Graph mode 下调用不同的内部执行器。ReviewTask 和 CompactTask 是需要特殊生命周期语义时使用的 Task。

RegularTask、ReviewTask 和 CompactTask 都不拥有顶层请求的 API 响应提交权，只返回 TaskOutcome，由 TurnRunner 统一收敛。

### 6.5 Graph / Node

- Graph 是一个 Run 内的执行计划和版本；
- Node 是 Graph 中的一个受治理执行单元；
- Node 可以执行一个 subagent、一个 role 或一组受限工具；
- Node 不是 Turn，但一个 Node 可以通过 ChildTurn 执行复杂子 agent 工作。

### 6.6 Artifact

跨 Task、Node 和 Run 传递的结构化结果。Artifact 必须有 schema、版本、来源和 lineage。

模型原始文本和 Tool 原始返回值都不能自动成为下游可靠事实。

## 7. 核心接口设计

以下接口是建议的运行时边界，采用接近现有 Python 类型系统的伪代码表达。字段名可以根据现有模型实际命名调整，但语义应保持稳定。

### 7.1 Turn 状态

~~~python
class TurnStatus(str, Enum):
    RECEIVED = "received"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    GATED = "gated"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
~~~

### 7.2 TurnInput

~~~python
class TurnInput(BaseModel):
    request_id: str
    run_id: str
    thread_id: str
    parent_turn_id: str | None = None
    parent_thread_id: str | None = None
    user_id: str
    tenant_id: str
    user_query: str
    intent: str | None = None
    slots: dict[str, Any] = Field(default_factory=dict)
    requested_mode: str | None = None
    idempotency_key: str | None = None
    continuation_of: str | None = None
~~~

要求：

- 输入必须经过统一身份和租户作用域校验；
- run_id、thread_id 和 request_id 必须在进入 Task 前确定；
- 恢复请求不得重新生成新的根 Run；
- retry 或 resume 应通过 continuation_of 记录来源；
- 用户原文保持不变，澄清后的 resolved request 作为新版本输入保存。

### 7.3 TurnContext

~~~python
class TurnContext:
    turn: TurnSnapshot
    input: TurnInput
    main_agent: MainAgent
    llm_provider: LLMProvider
    tool_registry: ToolRegistry
    role_registry: RoleRegistry
    artifact_board: ArtifactBoard
    run_store: RunStore
    event_store: EventStore
    hook_runtime: HookRuntime
    control: AgentControl
    policy: ExecutionPolicy
    clock: Clock
    emit: Callable[[TurnEvent], None]
~~~

TurnContext 是 Task 所需依赖的窄投影。建议不要把整个 AgentOS 传入 Task，避免 Task 重新调用入口、重复做 intake 或越过作用域检查。

### 7.4 TurnTask

~~~python
class TurnTask(Protocol):
    task_type: ClassVar[str]

    def start(self, context: TurnContext) -> TaskOutcome:
        ...

    def resume(self, context: TurnContext) -> TaskOutcome:
        ...

    def cancel(self, context: TurnContext, reason: str) -> TaskOutcome:
        ...
~~~

第一阶段也可以使用统一的 execute(context, operation) 方法，但 start、resume、cancel 的语义最好在接口层明确，避免把不同操作编码成任意字符串。GraphExecutor 不作为另一个 Root TurnTask，而是由 RegularExecutor 在 Graph mode 下装载。

### 7.4.1 RegularExecutor

RegularExecutor 是 RegularTask 内部唯一的执行 loop。它承接原 `MainAgent._run_agent_loop()` 的单循环语义；GraphExecutor 只是其中一个可选的 step executor：

~~~python
class RegularExecutor(Protocol):
    def start_or_resume(self, context: TurnContext) -> TaskOutcome:
        ...

    def step(self, context: TurnContext) -> TurnStepOutcome:
        ...

    def cancel(self, context: TurnContext, reason: str) -> TaskOutcome:
        ...
~~~

Turn loop 的共同步骤：

~~~text
RegularExecutor.loop
  -> 读取输入、控制和 checkpoint
  -> 选择本轮 step executor
       -> CoreLoopExecutor（core / task）
       ->（GraphExecutor（graph））
  -> 收集 Provider / Tool / Node / ChildTurn / Artifact 结果
  -> 将并发结果按 event sequence 折叠进 TurnTaskState / GraphSnapshot
  -> 持久化状态
  -> FollowUpRuntime
  -> 完成候选时运行 StopHook / SubagentStop
  -> continue / wait / pause / complete
~~~

GraphExecutor 负责把 Graph 状态机装入上述 loop；GraphRunner 负责 Graph frontier、Node 并发和状态推进。二者都不拥有 Turn loop 的最终完成权。

### 7.5 TaskOutcome

~~~python
class TaskOutcome(BaseModel):
    status: TurnStatus
    task_type: str
    assistant_message: str | None = None
    assistant_message_artifact_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    final_artifact_ids: list[str] = Field(default_factory=list)
    pending_approval_ids: list[str] = Field(default_factory=list)
    child_turn_ids: list[str] = Field(default_factory=list)
    continuation: Continuation | None = None
    usage: UsageSnapshot | None = None
    error: TaskError | None = None
    state_ref: str | None = None
~~~

TaskOutcome 只描述当前执行结果，不直接写 HTTP 响应。TurnRunner 根据结果决定是否更新 Turn snapshot、生成最终 ResponseEnvelope、保留可恢复 continuation 或允许结束 Run。

### 7.6 TurnRunner

~~~python
class TurnRunner:
    def start_or_resume(
        self,
        turn_input: TurnInput,
        *,
        operation: Literal["start", "resume"] = "start",
        event_callback: EventCallback | None = None,
    ) -> ResponseEnvelope:
        ...

    def pause(self, run_id: str, *, reason: str) -> TurnSnapshot:
        ...

    def cancel(self, run_id: str, *, reason: str) -> TurnSnapshot:
        ...

    def resume(self, run_id: str) -> ResponseEnvelope:
        ...
~~~

内部流程：

~~~text
load or create TurnSnapshot
  -> validate identity / idempotency / legal transition
  -> select or restore Task
  -> execute Task
  -> persist Task state and Turn state
  -> normalize outcome
  -> finalize response or return continuation
~~~

### 7.7 TaskFactory

~~~python
class TaskFactory:
    def select(
        self,
        *,
        request: TurnInput,
        decision: RequestIntakeDecision,
        context: PlanningContext,
    ) -> TaskSelection:
        ...

    def restore(
        self,
        *,
        turn: TurnSnapshot,
        task_snapshot: TaskSnapshot,
    ) -> TurnTask:
        ...
~~~

选择结果需要记录原因，而不是只保存一个 mode 字符串：

~~~python
class TaskSelection(BaseModel):
    task_type: Literal[
        "regular",
        "review",
        "compact",
    ]
    execution_mode: Literal["core", "task", "graph"] = "core"
    reason_codes: list[str]
    required_capabilities: list[str]
    graph_required: bool = False
    requires_persistence: bool = False
    requires_approval: bool = False
~~~

这样可以在日志和指标中回答“为什么这个请求进入 Graph”，也便于后续改变规则而不破坏恢复。

### 7.8 原始 Turn 与可加载配置

为了让 Graph、StopHook、Follow-up 和其他扩展都挂在同一个原始 Turn 上，建议把 Turn 分成两个层次：

1. 原始 Turn 骨架：任何请求都必须拥有，负责输入、上下文、Task、运行和最终结果；
2. TurnConfig：可选配置投影，在 Turn 创建时加载，负责选择真正的 Task 类型和挂载 RegularExecutor 扩展；
3. TaskState：当前 Task 的可恢复状态，由具体 Task 持有；
4. GraphSnapshot：GraphExecutor 的可变 Graph 状态，不直接展开到 TurnConfig。

~~~python
class TurnConfig(BaseModel):
    config_version: int = 1
    task_type: Literal[
        "regular",
        "review",
        "compact",
    ] = "regular"
    execution_mode: Literal["core", "task", "graph"] = "core"
    task_policy_ref: str | None = None
    graph_config: GraphExecutorConfig | None = None
    hook_config: HookConfig | None = None
    follow_up_config: FollowUpConfig | None = None
    child_turn_config: ChildTurnConfig | None = None
    approval_config: ApprovalConfig | None = None
    compact_config: CompactConfig | None = None
    plugin_refs: list[str] = Field(default_factory=list)

class GraphExecutorConfig(BaseModel):
    graph_id: str | None = None
    initial_graph_ref: str | None = None
    planner_role: str = "main_agent.plan_agent"
    graph_policy_ref: str | None = None
    max_concurrency: int | None = None
    max_graph_patches: int | None = None

class FollowUpConfig(BaseModel):
    enabled: bool = True
    allow_model_follow_up: bool = True
    allow_pending_input: bool = True
    allow_child_followup_task: bool = True
    max_follow_up_count: int = 32
    mailbox_delivery_policy: Literal["current_turn", "next_turn", "governed"] = "governed"
~~~

TurnConfig 的职责是声明和装配，不保存所有执行结果。推荐的持久化关系：

~~~text
turn.json
  -> input
  -> turn_config
  -> task_type
  -> task_state_ref
  -> graph_state_ref
  -> status

task.json
  -> TaskState

graph/snapshot.json
  -> GraphSnapshot
  -> graph revision / nodes / frontier / gates / artifacts
~~~

因此：

- Graph 可以作为原始 Turn 的 execution_mode 和配置；
- Graph mode 下 `task_type` 仍然是 `regular`；Graph 不是 TaskFactory 创建的独立 Task，而是 RegularExecutor 根据 TurnConfig 装载的执行器；
- GraphExecutor 使用原始 Turn 的 run_id、thread_id、turn_id、事件流和控制；
- GraphRunner 仍持有 Graph revision、Node、frontier 和 Board 状态；
- StopHook、SubagentStop、Follow-up、Mailbox 和审批都是当前 Turn 的扩展或状态；
- 删除这些扩展配置后，Turn 仍能使用 RegularTask 的 Core mode 完成最小请求；
- 删除 Graph 配置不应删除原始 Turn，也不应导致另一个根 Run 被创建。

## 8. TurnRunner 的职责

### 8.1 TurnRunner 负责的事情

- 创建或加载 Turn；
- 校验 Run、Thread、user、tenant 作用域；
- 校验幂等键；
- 生成和持久化统一状态；
- 选择或恢复 Task；
- 将暂停、取消和超时传递给当前 Task；
- 订阅和规范化 Task 事件；
- 处理 Task 返回的 TaskOutcome；
- 统一生成最终响应；
- 记录 completion / failure / continuation；
- 防止同一 Run 被两个 worker 同时执行；
- 为 SSE、回放和 API 提供一致的状态。

### 8.2 TurnRunner 不负责的事情

- 决定某个 Graph 节点的业务内容；
- 直接调用 LLM；
- 实现 Tool 的输入输出逻辑；
- 计算 Graph frontier；
- 解析 DomainPack 的专业知识；
- 直接修改 Artifact 的业务 schema；
- 在没有 Task 授权的情况下调用另一个 Agent OS 入口。

### 8.3 MainAgent、TurnRunner、GraphRunner 的边界

| 组件 | 主要问题 | 不应承担 |
|---|---|---|
| MainAgent | 用户想做什么？如何回答？需要什么能力？ | 顶层状态机、HTTP 响应、Graph 节点调度 |
| TurnRunner | 这次 Turn 现在处于什么状态？下一步如何恢复？ | 业务规划细节、Tool 实现、Graph 算法 |
| GraphRunner | 当前 Graph 哪些节点可执行？结果如何推进？ | Chat 请求路由、全局响应封装、身份认证 |
| Task | 当前执行策略怎么推进？ | 绕过 Runner 结束 Run |
| ToolRegistry | 某个 Tool 是否允许、如何执行、如何审计？ | 自己修改 Graph 或 Turn |

## 8.4 HookRuntime 的位置

HookRuntime 是 TurnRunner 和 Task 之间的受治理决策层：

~~~text
TurnRunner
  -> Task
       -> HookRuntime
            -> input hooks
            -> tool hooks
            -> compact hooks
            -> stop hooks
       -> TaskOutcome
  -> Turn lifecycle callbacks
  -> ResponseEnvelope
~~~

HookRuntime 不替代 Task，也不替代 TurnRunner：

- Task 决定何时提出“我准备结束”；
- StopHook 决定这次结束请求是否可以被接受，或者是否需要继续推进；
- TurnRunner 负责真正结束 Turn、保存状态和生成响应；
- Turn lifecycle callback 在生命周期节点上观察或更新扩展状态，但不应重新启动 Task。

## 9. Task 执行策略

### 9.1 RegularTask

RegularTask 是 Root Turn 的默认执行容器，始终经过统一 Turn loop。它适用于：

- 普通问答；
- 不需要专业 DomainPack 的 Core 能力；
- 只使用 Core Tool 的任务；
- 启用 GraphExecutor 的 Graph mode 任务。

RegularTask 不把 Graph 伪装成 Tool call，也不把 Graph 提升成另一种顶层 Task。它把 `execution_mode` 作为 RegularExecutor 的配置：core/task 使用 CoreLoopExecutor，graph 在同一个 loop 中装载 GraphExecutor。两种 step executor 都返回 TurnStepOutcome，再由同一个 RegularExecutor 处理 Follow-up、StopHook、控制和持久化。

当前第一阶段的实际形态是：`RegularExecutor` 先作为单一 dispatch/checkpoint 边界，CoreLoopExecutor 内部仍调用 MainAgent 的有限 provider/tool loop，GraphExecutor 内部仍调用一次完整的 GraphRunner run/resume。后续迁移才会把 provider/tool step、Follow-up 和 StopHook 的继续/暂停判断逐步移动到 RegularExecutor；这不改变 Graph 作为 RegularTask 配置的架构结论。

~~~text
RegularTask
  -> RegularExecutor.loop（原始 MainAgent loop 的统一承载）
       -> 读取 Turn state / transcript / control
       -> 根据 execution_mode 选择当前 step executor
            -> CoreLoopExecutor
            ->（GraphExecutor）
       -> 执行一个 Turn step
       -> 收集 Provider / Tool / Node / ChildTurn / Artifact 结果
       -> 将并发结果按序合并到状态
       -> 持久化 checkpoint
       -> FollowUpRuntime
       -> 完成候选时运行 StopHook
       -> continue / wait / pause / complete
~~~

RegularTask/RegularExecutor 的循环上限、工具权限、预算、取消、Hook、Follow-up 和审计由统一运行时约束。Provider、CoreLoopExecutor 和 GraphExecutor 只负责各自的执行步骤；GraphRunner 不得自行结束 Root Turn。

### 9.2 RegularTask 的执行模式

RegularTask 可以使用以下执行模式：

| execution_mode | 内部执行器 | 适用范围 |
|---|---|---|
| core | CoreLoopExecutor | 普通回答、Core Tool |
| task | CoreLoopExecutor + TaskState | 可恢复 Core Tool、长循环、中间 Artifact |
| graph | RegularExecutor 内的 GraphExecutor | DomainPack、Node 并发、Artifact、Gate、patch、局部重跑 |

task mode 不是第二个 Agent，也不是另一个顶层 Task。它只是 RegularTask 的持久化增强：

| 特征 | core | task |
|---|---|---|
| 模型循环 | 有限 | 有限 |
| Core Tool | 可用 | 可用 |
| transcript checkpoint | 最小化 | 强制 |
| 中间 Artifact | 可选 | 推荐或强制 |
| 暂停恢复 | 基本支持 | 强制支持 |
| Graph node | 无 | 无 |
| DomainPack subagent | 不允许 | 不允许 |
| 局部重跑 | 整个 Turn 重做 | 通过 continuation 重做 |

第一阶段可以让 task mode 直接复用 CoreLoopExecutor，只改变 state store 和恢复要求。

### 9.3 GraphExecutor

GraphExecutor 是挂载在 RegularExecutor 内部的 Graph step executor。它不是 RegularTask 的并列执行路径，不是 Root Task，也不拥有另一套 MainAgent loop：

~~~text
RegularTask
  -> RegularExecutor.loop
       -> GraphExecutor.step()
            -> 读取或创建当前 Turn 的 GraphSnapshot
            -> 调用 GraphRunnerAdapter
            -> 执行当前 frontier（可受控并发）
            -> 接收 Node / ChildTurn 反馈
            -> 将每个并发 Node 的状态推进写回同一 GraphSnapshot
            -> 返回 GraphStepOutcome
       -> RegularExecutor 统一处理 outcome
            -> FollowUpRuntime
            -> StopHook / SubagentStop
            -> pause / approval / completion
~~~

GraphExecutor 不重新实现：

- ready leaf 计算；
- frontier governance；
- node 并发；
- compound expansion；
- Graph patch；
- Gate evaluation；
- Board 提交。

这些仍属于 GraphRunner 或 Graph 领域模块。GraphExecutor 负责把这些能力接入 RegularExecutor，而不是复制一份调度器。

GraphExecutor 必须实现：

- Graph state 与 TurnTaskState 的绑定；
- Graph snapshot 的加载和保存协调；
- 将 GraphRunner 的结果转换成 GraphStepOutcome；
- 在安全点向 RegularExecutor 返回 continuation、approval、pause 或 completion candidate；
- 将 Graph 事件补充 event_id、turn_id、task_type=regular 和 execution_mode=graph。

GraphExecutor 的状态模型必须支持“一张图、多条并发 Node 进度”：

~~~python
class GraphExecutionState(BaseModel):
    graph_id: str
    revision: int
    nodes: dict[str, NodeState]
    frontier: list[str]
    in_flight: dict[str, ChildExecutionRef]
    pending_feedback: list[NodeFeedback]
    pending_patches: list[GraphPatch]
    gates: dict[str, GateResult]
~~~

`in_flight` 记录并发 Node 或 ChildTurn 的位置，`pending_feedback` 记录异步结果；只有 RegularExecutor 在安全点消费反馈并推进 Node 状态，避免并发 worker 直接互相修改 Turn。物理上可以把该对象单独持久化为 GraphSnapshot，但逻辑所有权仍属于当前 RegularTask/Turn。

GraphExecutor 不得：

- 直接生成最终 ResponseEnvelope；
- 直接结束 Root Turn；
- 直接修改 TurnRunner 的状态；
- 在 Graph node 内创建无 parent 关系的根 Run；
- 让每个 Node 绕过 RegularExecutor loop 自己实现一套 StopHook 和 Follow-up。

### 9.4 ReviewTask

ReviewTask 用于审查已有结果，可以审查：

- Core Tool Loop 产出的回答；
- Graph 生成的专业报告；
- 某个 Artifact 的 schema、来源、风险或完整性；
- 用户明确要求的复核。

ReviewTask 的输入不应是一个未经限定的对话历史，而应是显式的：

~~~python
class ReviewInput(BaseModel):
    target_artifact_ids: list[str]
    review_policy: str
    required_checks: list[str]
    output_schema: str
~~~

ReviewTask 输出：

- review Artifact；
- blocking / non-blocking finding；
- 是否允许进入最终回答；
- 需要补跑的建议。

ReviewTask 不能自行修改原 Artifact，也不能绕过 Gate 直接把未通过的结果标记为最终交付。

### 9.5 CompactTask

CompactTask 对应 Codex 的上下文压缩思路，但 Cognitrade 需要同时考虑：

- transcript；
- Board 中的 Artifact 引用；
- Graph snapshot；
- Tool call 审计；
- 用户可见消息；
- 可恢复状态。

CompactTask 的目标是减少下一次模型调用的输入，不是删除审计数据。

压缩后的结果必须区分：

| 数据 | 是否可压缩 | 是否保留原文 |
|---|---|---|
| 模型上下文摘要 | 可以 | 可在 transcript archive 保留 |
| Tool 原始返回值 | 可以从模型上下文移除 | 必须保留审计引用 |
| Artifact | 只压缩引用 | 必须保留版本和 lineage |
| Graph snapshot | 只做索引优化 | 必须保留可恢复状态 |
| 用户原文 | 不应丢失 | 强制保留 |
| 审批与控制记录 | 不应丢失 | 强制保留 |

### 9.6 Task 类型选择规则

建议使用显式规则加可观察原因，而不是只根据 request_kind 做二分：

~~~text
普通对话
  -> RegularTask

Core Tool + 可恢复 / 中间 Artifact / 长循环
  -> RegularTask(execution_mode=task)

Review 已有产物
  -> ReviewTask

上下文或状态超过压缩阈值
  -> CompactTask

DomainPack subagent / 并发 / Gate / 审批 / Graph patch / 局部重跑
  -> RegularTask(execution_mode=graph)
       -> RegularExecutor
            -> GraphExecutor
~~~

只要命中 Graph 条件，就让 RegularTask 进入 `execution_mode=graph`，由 RegularExecutor 装载 GraphExecutor。不能在 GraphExecutor 或 GraphRunner 中再偷偷创建另一个顶层 AgentOS Run。

## 10. Turn 状态机

### 10.1 统一状态

建议使用以下 canonical Turn 状态：

~~~text
received
  -> ready
  -> running
       -> paused
       -> waiting_approval
       -> completed
       -> gated
       -> failed
       -> cancelled
       -> timed_out

paused -> running
waiting_approval -> running
~~~

### 10.2 状态含义

| 状态 | 含义 | 是否可恢复 |
|---|---|---|
| received | 已接收但尚未完成路由和 Task 选择 | 是 |
| ready | 已选定 Task，等待执行 | 是 |
| running | Task 正在推进 | 由控制协议决定 |
| paused | 用户或系统暂停，保留 checkpoint | 是 |
| waiting_approval | 等待明确审批 | 是 |
| completed | 已产生可以交付的最终结果 | 否，新的修改应创建新 Turn |
| gated | 有结果但 blocking Gate 未通过 | 是，可审批、补跑或 review |
| failed | 不可继续的执行失败 | 可通过 retry 创建 continuation |
| cancelled | 用户或系统取消 | 可通过新 continuation 重做 |
| timed_out | 超出 Turn 或资源时间限制 | 可通过新 continuation 重做 |

failed、cancelled 和 timed_out 是终态，不应通过修改同一 snapshot 伪装成重新开始。恢复语义应记录新的 continuation，同时仍保留原始终态。

### 10.3 合法迁移

~~~text
received -> ready
ready -> running
running -> paused
running -> waiting_approval
running -> completed
running -> gated
running -> failed
running -> cancelled
running -> timed_out
paused -> running
waiting_approval -> running
~~~

以下迁移必须拒绝并写入审计事件：

- completed -> running；
- failed -> running；
- cancelled -> running；
- timed_out -> running；
- 任意状态直接跳过审批进入 completed；
- 任意 Task 自己修改 Turn 状态而不经过 TurnRunner。

### 10.4 Task 内部状态

Turn 状态不应替代 Task 或 Graph 的内部状态。

例如：

~~~text
Turn: running
  Graph: running
    Node A: completed
    Node B: waiting_approval
    Node C: pending
~~~

此时 Turn 应映射为 waiting_approval，因为 Graph 没有安全地继续执行的条件，但 Graph 内部仍保留 node 状态。

## 11. 普通 Turn 的完整流程

### 11.1 请求进入

~~~text
Client
  -> AgentOS.handle_request(RequestEnvelope)
  -> identity / tenant scope 校验
  -> idempotency key 查找
  -> RequestIntake.resolve()
  -> MainAgent.route()
  -> TaskFactory.select()
  -> TurnRunner 创建或恢复 Turn
~~~

AgentOS 在迁移后的职责应收敛为入口适配：

1. 验证请求身份和作用域；
2. 处理兼容 API 的请求转换；
3. 调用一次 RequestIntake；
4. 调用 MainAgent 路由；
5. 把标准 TurnInput 交给 TurnRunner；
6. 返回 TurnRunner 生成的 ResponseEnvelope。

它不应该在 domain_task 和普通对话之间分别实现两套执行和响应收敛。

### 11.2 MainAgent 路由

MainAgent.route() 只回答：

- 用户意图是什么；
- 是否需要澄清；
- 需要哪些能力；
- 是否使用 RegularTask 的 Core mode、task mode 或 Graph mode；
- 是否需要 Review；
- 当前输入是否足够创建一个 Turn。

路由结果必须可持久化：

~~~python
class RouteDecision(BaseModel):
    status: Literal["accepted", "needs_clarification"]
    intent: str | None
    request_kind: str
    selected_capabilities: list[str]
    mode_hint: str | None
    slots: dict[str, Any]
    constraints: list[str]
    reason_codes: list[str]
~~~

### 11.3 RegularTask 的模型循环

~~~text
TurnRunner: running
  -> RegularTask 读取 checkpoint
  -> RegularExecutor 读取 execution_mode
       |
       +-- core / task
       |    -> MainAgent 组装受限上下文
       |    -> Provider 返回 message 或 tool_calls
       |
       +-- graph
            -> RegularExecutor 内的 GraphExecutor.step()
            -> GraphRunner 执行当前 frontier
            -> 返回 GraphStepOutcome
       |
       +-- message
       |    -> 写入 assistant message
       |    -> completed
       |
       +-- tool_calls 或 GraphStepOutcome
            -> ToolRegistry 校验身份、权限、schema、预算和审批
            -> 执行 Tool / Node / Role
            -> 保存 ToolResult / Artifact 和审计事件
            -> 更新 transcript / GraphSnapshot checkpoint
            -> FollowUpRuntime
            -> 再次执行 RegularExecutor step
~~~

必须满足：

- 每次模型调用有 request / response 标识；
- 每个 Tool call 有幂等键；
- Tool 结果写入 transcript 前先完成 schema 和来源校验；
- provider 不可用时的 deterministic fallback 仍必须经过同一 ToolRegistry；
- 每轮循环都检查 pause、cancel、timeout 和 approval；
- 命中循环上限时返回可解释的 failed 或 gated，而不是静默截断。

### 11.4 普通 Turn 的结束

TurnRunner 接收 TaskOutcome 后：

1. 校验结果中的 Artifact 是否属于当前 user、tenant、run 和 thread；
2. 校验最终回答是否引用了允许交付的 Artifact；
3. 写入 TurnSnapshot；
4. 写入 ResponseEnvelope；
5. 使用幂等方式持久化 response；
6. 发出 turn_completed 或对应终态事件；
7. 释放 Run worker lease。

若 Task 返回 paused 或 waiting_approval，则不得写入“已完成”的最终响应。

## 12. Graph Turn 的完整流程

### 12.1 Graph mode 启动

~~~text
MainAgent.route()
  -> TaskFactory.select(regular, execution_mode=graph)
  -> TurnRunner 创建或恢复原始 Turn
  -> RegularTask 启动同一个 Turn loop
  -> RegularExecutor 挂载 GraphExecutor
  -> MainAgent.PlanRole 生成初始 Graph 或加载既有 Graph
  -> GraphExecutor 创建或恢复 GraphSnapshot
  -> GraphExecutor.step()
~~~

选择 Graph mode 的条件包括：

- 使用 DomainPack subagent；
- 需要多个受治理节点；
- 需要并行 frontier；
- 需要专业 Artifact；
- 需要 Gate；
- 需要审批；
- 需要 Graph patch；
- 需要局部重跑；
- 需要在多个 role 之间传递 Board Artifact。

### 12.2 GraphExecutor 与 GraphRunner

建议的适配器接口：

~~~python
class GraphRunnerAdapter(Protocol):
    def step(
        self,
        *,
        run_id: str,
        thread_id: str,
        turn_id: str,
        request: TurnInput,
        graph_snapshot: GraphSnapshot | None,
        event_sink: Callable[[GraphEvent], None],
    ) -> GraphStepOutcome:
        ...

    def pause(self, run_id: str, *, reason: str) -> None:
        ...

    def cancel(self, run_id: str, *, reason: str) -> None:
        ...
~~~

GraphExecutor 的职责：

- 作为 RegularExecutor 的可选 step executor 运行在 RegularTask 的唯一 Turn loop 内；
- 提供 turn_id 和统一上下文；
- 加载正确的 GraphSnapshot；
- 将 AgentControl 映射给 GraphRunner；
- 监听 GraphRunner 的事件；
- 将 GraphExecutionResult 转成 GraphStepOutcome；
- 将 GraphStepOutcome 交给 RegularExecutor；
- 不直接生成最终 TaskOutcome 或 ResponseEnvelope。

GraphExecutor 与 GraphRunner 的并发边界：

~~~text
RegularExecutor.loop（串行拥有 Turn 状态）
  -> GraphExecutor.step()
       -> GraphRunner 计算 ready frontier
       -> 在并发预算内启动 Node / ChildTurn
       -> 收集 subagent feedback
       -> 在安全点按 sequence 合并 feedback
       -> 推进同一 GraphSnapshot 中每个 Node 的状态
       -> 计算下一 frontier / gate / patch
       -> 返回 GraphStepOutcome
~~~

这里的“并发”只表示 Node 或 ChildTurn 的工作可以并行进行，不表示存在多个 MainAgent loop。并发 worker 不得直接写 TurnSnapshot；它们只能提交带有 `operation_id`、`node_id`、`graph_revision` 和 sequence 的反馈，由 GraphExecutor/RegularExecutor 串行归并。这样可以同时满足吞吐、可恢复和确定性回放。

建议的 GraphStepOutcome：

~~~python
class GraphStepOutcome(BaseModel):
    status: Literal[
        "continue_graph",
        "needs_follow_up",
        "waiting_approval",
        "paused",
        "completion_candidate",
        "failed",
    ]
    completed_node_ids: list[str] = Field(default_factory=list)
    ready_node_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    pending_approval_ids: list[str] = Field(default_factory=list)
    continuation: Continuation | None = None
    graph_state_ref: str | None = None
~~~

GraphRunner 的职责：

- 维护 Graph revision；
- 展开 compound；
- 计算 ready frontier；
- 处理并发治理；
- 执行 leaf node；
- 提交 Artifact；
- 评估 gate；
- 处理 Graph patch；
- 保存 Graph snapshot。

### 12.3 当前 GraphRunner 的复用方式

当前 GraphRunner 主循环已经包含以下能力：

- 检查 run controls；
- 处理 pending patch；
- 处理 approval；
- 处理 paused；
- 计算 completed / skipped / failed / pending；
- 计算 ready frontier；
- 执行 frontier；
- 将 subagent 输出写回 snapshot；
- 评估下一轮 frontier；
- 结束时执行 gate evaluation；
- 处理 timeout 和 cancellation。

第一阶段应保留这些能力，通过适配器将其包装成 GraphExecutor.step。迁移时重点观察：

1. GraphRunner 是否重复写入与 TurnRunner 冲突的顶层状态；
2. Graph 事件是否缺少 turn_id、task_type=regular 和 execution_mode=graph；
3. Graph 的 response 是否直接绕过 RegularExecutor 或 TurnRunner；
4. Graph pause/resume 是否能从同一个 TurnSnapshot 和 GraphSnapshot 恢复；
5. GraphStepOutcome 是否能精确映射到 completed、gated、failed、cancelled 和 timed_out。

### 12.4 Graph 状态映射

| GraphRunner 状态 | Turn 状态 | 说明 |
|---|---|---|
| running | running | 仍有节点或 frontier 在推进 |
| paused | paused | 控制暂停，Graph snapshot 已保存 |
| waiting_approval | waiting_approval | 有待审批请求 |
| completed | completed | 所有 blocking gate 通过 |
| gated | gated | 有 blocking gate 未通过 |
| failed | failed | halt 节点或不可恢复 Graph 错误 |
| cancelled | cancelled | 取消已被 Graph 接收并落盘 |
| timed_out | timed_out | Graph 或 Run 超时 |

GraphRunner 不应把 gated 转成 failed，因为“结果存在但不允许交付”和“执行失败”是两个不同的产品语义。

### 12.5 Graph 最终回答

GraphExecutor 可以提交给 RegularExecutor：

- final_artifact_ids；
- Gate 结果；
- 风险和警告；
- assistant_message_draft；
- usage；
- child turn 引用。

但最终面向用户的 assistant_message 由 MainAgent 的 FinalAnswerRole 生成，并由 TurnRunner 提交。这样可以保证：

- 未通过 blocking Gate 的报告不会被伪装成已完成；
- 报告 Artifact 与自然语言回答保持不同职责；
- API、Chat 和 SSE 使用同一最终响应字段。

## 13. MainAgent 的职责调整

### 13.1 保留的职责

MainAgent 仍然是 Root Agent，保留：

- RequestIntake 结果解释；
- 用户目标和约束理解；
- Core / Task / Graph 路由；
- 初始 Graph 计划；
- Graph patch 的决策角色；
- 对 Tool / Artifact / Gate 结果进行总结；
- 生成最终 assistant message；
- 产生面向用户的 progress 建议。

### 13.2 移出的职责

从 MainAgent 移至 TurnRunner 或 Task 的职责：

- Turn 状态机；
- Run worker lease；
- pause / resume / cancel；
- timeout；
- idempotency；
- Task 恢复；
- Task / GraphStep 结果到 ResponseEnvelope 的统一映射；
- 顶层事件持久化；
- 顶层 response 持久化。

从 MainAgent 移至 RegularExecutor 的职责：

- 原 `MainAgent._run_agent_loop()` 的循环控制；
- loop limit、每轮控制检查和安全点；
- checkpoint 提交顺序；
- FollowUpRuntime、StopHook 和 TurnStepOutcome 的统一收敛。

从 MainAgent 移至 CoreLoopExecutor 的职责：

- provider tool loop；
- 单次 provider / Tool step；
- ToolResult transcript；
- tool call 结果转换。

从 MainAgent 移至 GraphExecutor / GraphRunner 的职责：

- frontier；
- Graph node 调度；
- Graph snapshot；
- Graph patch；
- Graph gate；
- node-level retry / rerun。

### 13.3 建议的 MainAgent 接口

~~~python
class MainAgent:
    def route(
        self,
        *,
        request: RequestEnvelope,
        run_id: str,
        thread_id: str,
        intake_resolver: RequestIntakeResolver,
        runtime_context: RuntimeContext,
    ) -> RouteDecision:
        ...

    def choose_task(
        self,
        *,
        request: TurnInput,
        decision: RouteDecision,
        capability_catalog: CapabilityCatalog,
    ) -> TaskSelection:
        ...

    def plan_graph(
        self,
        *,
        context: PlanningContext,
    ) -> GraphPlan:
        ...

    def compose_final_answer(
        self,
        *,
        request: TurnInput,
        outcome: TaskOutcome,
    ) -> AssistantMessage:
        ...
~~~

MainAgent.run_request() 可以暂时保留为兼容方法，但标准入口不应再直接调用它。兼容方法内部应尽快转发到 TurnRunner，而不是保留第二套实现。

## 14. ChildTurn 与动态 Runtime Role

### 14.1 ChildTurn 的定位

ChildTurn 是一个独立的子执行生命周期，通常由 Graph node 或 MainAgent 派生：

~~~text
Parent Turn
  -> ChildTurn A: data retrieval role
  -> ChildTurn B: risk review role
  -> ChildTurn C: report composer role
~~~

ChildTurn 必须有自己的：

- turn_id；
- thread_id 或 child thread；
- task snapshot；
- transcript；
- event sequence；
- usage；
- status；
- output Artifact。

但它必须记录 parent 关系：

~~~python
class TurnRelation(BaseModel):
    parent_turn_id: str | None
    parent_run_id: str
    parent_node_id: str | None
    relation: Literal["child", "review", "continuation", "compact"]
~~~

### 14.2 Parent 与 Child 的数据边界

ChildTurn 可以：

- 读取 parent 明确授权的输入 Artifact；
- 使用 parent 授权的 Tool 和 RoleContract；
- 产出新的 Artifact；
- 提交 control request；
- 返回结构化 TaskOutcome。

ChildTurn 不可以：

- 直接修改 parent Turn 状态；
- 直接修改 parent Graph revision；
- 绕过 parent 的 tenant scope；
- 将未经 schema 校验的文本写成共享事实；
- 自行生成新的无关联根 Run。

Parent 在安全点接收 child result，再决定是否写入 Board 或应用 Graph patch。

### 14.3 RoleContract

动态 runtime role 不能只靠一个名称和 prompt 接入。建议定义显式 RoleContract：

~~~python
class RoleContract(BaseModel):
    role_id: str
    role_version: str
    display_name: str
    objective: str
    input_artifact_types: list[str]
    output_artifact_types: list[str]
    required_capabilities: list[str]
    allowed_tools: list[str]
    denied_tools: list[str] = Field(default_factory=list)
    max_model_turns: int
    timeout_seconds: int
    requires_approval: bool = False
    can_spawn_child_turn: bool = False
    failure_mode: Literal["halt", "continue", "report"] = "halt"
    source: Literal["builtin", "pack", "runtime", "user"] = "builtin"
~~~

RoleContract 是能力和治理的声明，不是执行器本身。

### 14.4 RoleExecutorAdapter

~~~python
class RoleExecutorAdapter(Protocol):
    def execute(
        self,
        *,
        role: RoleContract,
        context: TurnContext,
        input_artifacts: list[ArtifactRef],
    ) -> TaskOutcome:
        ...
~~~

适配器内部可以使用：

- RegularTask；
- CoreLoopExecutor；
- GraphExecutor；
- 受限的 provider/tool loop。

但所有路径必须回到统一 ToolRegistry、EventStore 和 ArtifactBoard。

### 14.5 动态 role 的接入流程

~~~text
runtime role definition
  -> schema validation
  -> policy validation
  -> RoleRegistry materialization
  -> RoleContract
  -> RoleExecutorAdapter
  -> ChildTurn or Graph Node
  -> Artifact + audit event
~~~

动态 role 不能通过字符串拼接直接注入 system prompt 后绕过：

- 工具白名单；
- 输入输出 schema；
- 预算；
- 审批；
- 失败策略；
- 版本和来源。

## 15. Thread、Run、Turn、Task、Graph、Node、Artifact 关系

### 15.1 关系图

~~~text
Tenant
  |
  +-- User
       |
       +-- Thread
            |
            +-- Run
                 |
                 +-- Root Turn
                 |    |
                 |    +-- Task
                 |         +-- provider/tool steps
                 |         +-- assistant message
                 |
                 +-- Graph
                      |
                      +-- Node
                           |
                           +-- ChildTurn
                                |
                                +-- Task
                                     |
                                     +-- Artifact
                                            |
                                            +-- Artifact lineage
~~~

### 15.2 实体对照表

| 实体 | 生命周期 | 所有者 | 关键关系 | 是否用户可见 |
|---|---|---|---|---|
| Thread | 多个请求 | Chat/session 层 | user、tenant、Run | 是 |
| Run | 一次业务运行 | Agent OS / RunStore | Thread、Root Turn | 是 |
| Turn | 一次执行推进 | TurnRunner | Run、Task、事件 | 部分 |
| Task | 一种执行策略 | TurnRunner 创建，Task 自管理内部状态 | Turn | 通常不可见 |
| Graph | 一次计划版本序列 | GraphExecutor / GraphRunner | Run、Node、revision | 可视化 |
| Node | Graph 内执行单元 | GraphRunner | Graph、Role、Artifact | 可视化 |
| ChildTurn | 子任务生命周期 | Parent Turn | parent turn、node | 可追踪 |
| Tool call | 一次工具调用 | ToolRegistry | Task / Node / Turn | 审计可见 |
| Artifact | 结构化事实或交付物 | ArtifactBoard | producer、parent、source | 是 |
| Event | 状态和行为记录 | EventStore | Run、Turn、Task、Node | SSE / 回放 |

### 15.3 重要约束

不要把以下概念混成同一层：

1. Turn 是生命周期；
2. Task 是执行策略；
3. Graph 是计划和依赖；
4. Node 是 Graph 的执行单元；
5. ChildTurn 是子生命周期；
6. Tool call 是一次外部能力调用；
7. Artifact 是跨步骤共享的结构化结果。

这条边界是整个方案能否长期演进的关键。

## 16. 统一事件协议

### 16.1 事件包络

建议所有普通 Turn、Task Mode、Graph 和 ChildTurn 事件都落到同一个事件包络：

~~~python
class TurnEvent(BaseModel):
    event_id: str
    sequence: int
    event_type: str
    occurred_at: datetime
    tenant_id: str
    user_id: str
    run_id: str
    thread_id: str
    turn_id: str
    parent_turn_id: str | None = None
    task_type: str | None = None
    graph_id: str | None = None
    graph_revision: int | None = None
    node_id: str | None = None
    actor: str
    payload: dict[str, Any]
~~~

### 16.2 最小事件集合

| 事件 | 触发时机 |
|---|---|
| turn_received | 创建 Turn |
| turn_ready | 完成路由和 Task 选择 |
| turn_started | Task 开始 |
| task_selected | 记录选择策略和原因 |
| model_call_started | Provider 调用开始 |
| model_call_completed | Provider 返回 |
| tool_call_started | Tool 执行开始 |
| tool_call_completed | Tool 执行完成 |
| artifact_committed | Artifact 批次提交 |
| child_turn_started | 子 Turn 开始 |
| child_turn_completed | 子 Turn 完成 |
| followup_requested | Follow-up 被请求 |
| followup_queued | Follow-up 进入 mailbox |
| followup_delivered | Follow-up 已交付给目标 Turn |
| followup_deferred | Follow-up 延迟到下一 Turn |
| followup_duplicate | 重复 Follow-up 被幂等拦截 |
| turn_paused | Turn 进入暂停 |
| turn_waiting_approval | 等待审批 |
| turn_resumed | Turn 恢复 |
| turn_completed | 正常完成 |
| turn_gated | Gate 未通过 |
| turn_failed | 执行失败 |
| turn_cancelled | 取消完成 |
| turn_timed_out | 超时完成 |

Graph 的现有事件可以保留事件名，但要增加统一包络字段，或者由适配器转成 turn_event payload。

### 16.3 SSE 与回放

SSE 应以 event_id 和 sequence 为游标：

~~~text
客户端连接
  -> Last-Event-ID
  -> EventStore 查询该事件之后的事件
  -> 先回放缺失事件
  -> 再订阅新事件
~~~

前端必须按 event_id 去重，不应只显示“最后一条进度”。这样暂停、恢复、Graph frontier 和 Tool 失败才能完整回放。

### 16.4 事件幂等

同一行为重试时：

- event_id 不应重复；
- 业务 operation_id 可以重复，但必须返回原结果或标记 duplicate；
- 重复收到同一个 Graph patch 不得再次改变 Graph；
- 重复收到同一个 approval decision 不得重复执行 Tool；
- EventStore 写入失败时，Task 不应悄悄继续而不产生可恢复记录。

### 16.5 Hook Pipeline 与 StopHook

前一版方案只定义了 Task、Tool、审批和控制，没有单独定义 Hook。参考 Codex 后，Hook 必须成为 Turn 架构中的一等扩展点。

#### 16.5.1 两类 Stop 机制必须分开

Codex 中至少有两种容易被混淆的“stop”：

| 机制 | 触发位置 | 能否阻止当前执行结束 | 主要用途 |
|---|---|---:|---|
| Stop / SubagentStop Hook | Task 已判断本轮没有后续工作、准备结束之前 | 能 | 质量检查、补充要求、让模型继续 |
| TurnStop lifecycle callback | Task 已经完成、TurnRunner 即将发出最终完成事件 | 不应能 | 扩展清理、统计、状态收尾 |

两者不能合并成一个 on_stop()：

- StopHook 是执行决策点；
- TurnStop callback 是生命周期通知点；
- StopHook 可以把 Task 从“准备结束”拉回 running；
- TurnStop callback 不应重新开启一个已经完成的 Task。

#### 16.5.2 Cognitrade 的 Hook 分类

建议统一注册以下 Hook 事件：

| Hook 事件 | 触发时机 | 典型结果 |
|---|---|---|
| SessionStart | Thread 或运行环境初始化 | 注入上下文、阻止启动 |
| UserPromptSubmit | 用户输入即将进入 Turn | 注入上下文、阻止输入 |
| PreToolUse | Tool 即将执行 | 允许、阻止、修改输入 |
| PermissionRequest | Tool 或 Node 请求审批 | 允许、拒绝、转人工 |
| PostToolUse | Tool 执行完成 | 记录、补充上下文、报告风险 |
| PreCompact | 开始压缩上下文前 | 允许或阻止压缩 |
| PostCompact | 压缩完成后 | 检查压缩结果、补充上下文 |
| Stop | Root Task 准备结束 | 完成、阻止并要求继续 |
| SubagentStop | ChildTurn 准备结束 | 完成、阻止子任务结束 |
| TurnStart | Turn 已创建并开始执行 | 扩展生命周期初始化 |
| TurnStop | Turn 正常完成前 | 扩展收尾，不重新驱动 Task |
| TurnAbort | Turn 被中断 | 释放资源、记录中断原因 |
| TurnError | Turn 遇到终止错误 | 记录错误和扩展状态 |

其中前九类是可影响执行的 Hook，后四类是 Turn lifecycle callback。实现时应使用不同的返回接口，避免扩展作者误以为 TurnStop 可以继续驱动模型。

#### 16.5.3 StopHook 输入

Root Turn 和 ChildTurn 的 StopHook 输入应显式区分：

~~~python
class StopHookInput(BaseModel):
    session_id: str
    run_id: str
    thread_id: str
    turn_id: str
    task_type: str
    target: Literal["stop", "subagent_stop"]
    cwd: str | None = None
    transcript_ref: str | None = None
    model: str | None = None
    permission_mode: str | None = None
    stop_hook_active: bool
    last_assistant_message: str | None = None
    parent_turn_id: str | None = None
    agent_id: str | None = None
    agent_type: str | None = None
    agent_transcript_ref: str | None = None
~~~

约束：

- Root Turn 使用 stop；
- 由用户请求派生的 ChildTurn 使用 subagent_stop；
- 内部合成的系统任务可以选择不触发用户配置的 StopHook；
- ChildTurn 不能伪装成 Root Turn 调用 stop；
- transcript 使用引用而不是把完整敏感内容直接传给 Hook；
- Hook 输入必须带当前 Turn 的身份和租户作用域。

#### 16.5.4 StopHook 输出

建议使用接近 Codex 的结构化结果：

~~~python
class StopHookOutcome(BaseModel):
    hook_events: list[HookRunRecord]
    should_stop: bool = False
    stop_reason: str | None = None
    should_block: bool = False
    block_reason: str | None = None
    continuation_fragments: list[PromptFragment] = Field(default_factory=list)
~~~

语义：

1. should_stop 为 true：允许 Task 结束，并记录 stop_reason。
2. should_block 为 true 且有非空 block_reason：不结束 Task，把原因作为受信任的 continuation 输入。
3. should_block 为 true 但没有原因：Hook 结果非法，不能生成无内容的继续请求。
4. Hook 输出格式错误、执行失败或超时：产生 HookRunRecord，按策略决定 fail-open 或 fail-closed，但不能静默忽略。
5. 多个 Hook 同时返回 block：按声明顺序合并 continuation fragments。
6. 某个 Hook 返回 should_stop 时，不能再把同一批结果降级为 should_block。

对于安全和权限 Hook，默认建议 fail-closed；对于质量提示和统计 Hook，可以配置为 fail-open。该策略必须写入 HookDefinition，而不能由异常处理代码临时决定。

#### 16.5.5 StopHook 的执行时机

RegularTask 的 core、task 和 graph execution mode 都必须经过以下关键流程：

~~~text
Provider 返回，且没有后续 Tool call
  -> Task 生成 last_assistant_message
  -> HookRuntime.preview_stop()
  -> 发出 hook_started
  -> HookRuntime.run_stop()
  -> 发出 hook_completed
       |
       +-- should_block
       |     -> 将 block_reason / continuation_fragments 写入当前 Turn
       |     -> stop_hook_active = true
       |     -> 返回 running
       |     -> 再次进入模型循环
       |
       +-- should_stop 或无阻止结果
             -> Task 返回准备完成的 TaskOutcome
             -> TurnRunner 进入最终收敛
~~~

这意味着 StopHook 不是 Turn 完成后的装饰性回调，而是模型循环的最后一个可继续执行点。

#### 16.5.6 stop_hook_active 与递归保护

阻止结束后，Task 必须把 stop_hook_active 设置为 true，并在后续 StopHook 输入中携带该字段。

还应增加运行时保护：

- 每个 Turn 限制 StopHook continuation 次数；
- 每次 continuation 增加 hook_iteration；
- 超过上限时进入 failed 或 gated，并记录原因；
- continuation 必须拥有 operation_id，重复事件不能重复注入；
- StopHook 自己不能创建一个无 parent 关系的新根 Run；
- Hook 产生的文本必须标记为 hook-originated，不能被当作用户原文。

推荐的默认治理字段：

~~~python
class HookPolicy(BaseModel):
    max_stop_continuations: int = 3
    timeout_seconds: int = 30
    failure_mode: Literal["fail_open", "fail_closed"] = "fail_closed"
    allow_prompt_continuation: bool = True
    allow_input_rewrite: bool = False
~~~

#### 16.5.7 GraphExecutor 中的 StopHook 映射

GraphExecutor 不拥有独立的模型结束点，因此不能复制一套独立的 StopHook 循环。建议把 Graph StopHook 放在 Graph 已经达到可结束安全点之后，并由 RegularExecutor 继续执行统一的结束判断：

~~~text
GraphRunner
  -> 所有必要 Node 完成
  -> Artifact 已提交
  -> blocking Gate 已评估
  -> GraphExecutor 生成 completion candidate
  -> StopHook / SubagentStop Hook
       |
       +-- block
       |     -> 生成 Graph continuation request
       |     -> 由 MainAgent PlanRole 决定补节点、重跑或 Review
       |
       +-- stop
             -> 交给 MainAgent FinalAnswerRole
             -> TurnRunner 最终完成
~~~

Graph 中的 block 不应直接让 Hook 自己修改 Graph。它只能返回：

- 缺少的数据或检查项；
- 风险原因；
- 建议补充的 capability；
- continuation prompt；
- 是否建议进入 ReviewTask。

只有 MainAgent PlanRole 能在 Graph safe point 生成并提交 Graph patch。

#### 16.5.8 Turn lifecycle callback

Turn lifecycle callback 与 StopHook 分开定义：

~~~python
class TurnLifecycleContributor(Protocol):
    def on_turn_start(self, input: TurnStartInput) -> None:
        ...

    def on_turn_stop(self, input: TurnStopInput) -> None:
        ...

    def on_turn_abort(self, input: TurnAbortInput) -> None:
        ...

    def on_turn_error(self, input: TurnErrorInput) -> None:
        ...
~~~

建议执行顺序：

~~~text
Task 返回正常完成
  -> TurnRunner 持久化最终 Task 状态
  -> TurnStop callback
  -> 写入 turn_completed / turn_gated
  -> 持久化 ResponseEnvelope
  -> 释放 worker lease
~~~

取消或错误时：

~~~text
Task 返回取消 / 错误
  -> TurnAbort 或 TurnError callback
  -> 写入 turn_cancelled / turn_failed / turn_timed_out
  -> 释放资源
~~~

TurnStop callback 不得：

- 修改 TurnStatus；
- 重新调用 Provider；
- 创建 continuation；
- 绕过 TurnRunner 写 ResponseEnvelope；
- 把 gated 改为 completed。

如果扩展需要改变交付结论，应使用 ReviewTask、Graph patch 或显式控制请求，而不是在 TurnStop callback 中隐式修改。

#### 16.5.9 Hook 事件与回放

Hook 必须产生和普通 Tool 一样可回放的记录：

~~~python
class HookRunRecord(BaseModel):
    hook_run_id: str
    hook_name: str
    hook_event: str
    source: str
    status: Literal["started", "completed", "blocked", "stopped", "failed", "timed_out"]
    started_at: datetime
    completed_at: datetime | None = None
    exit_code: int | None = None
    output_summary: str | None = None
    decision: str | None = None
    reason: str | None = None
~~~

Hook 输出的完整 stdout、stderr 和敏感输入应遵守脱敏策略；事件中默认只保存摘要、hash 和引用。

回放时：

- 默认只重放 HookRunRecord，不重新执行 Hook；
- 明确 retry 时才重新运行；
- Hook 是外部副作用或可能访问文件、网络，因此重跑必须有 operation_id；
- 旧 Hook 结果不能在新 Turn 中无条件复用。

#### 16.5.10 Hook 流程验收

必须验证：

1. 普通回答完成前能触发 StopHook。
2. StopHook block 能把 Turn 拉回 running。
3. continuation prompt 被记录为 Hook 产生，而不是用户输入。
4. StopHook 连续 block 超限后进入可解释终态。
5. should_stop 优先于 should_block。
6. 多个 Hook 的 block 原因顺序稳定。
7. Root Turn 使用 Stop，ChildTurn 使用 SubagentStop。
8. 内部合成任务不会误触发用户配置的 SubagentStop。
9. HookStarted / HookCompleted 可通过 SSE 和回放看到。
10. TurnStop callback 不能重新驱动模型或改变最终状态。
11. 取消走 TurnAbort，正常结束走 TurnStop，异常走 TurnError。
12. Hook 执行失败不会无事件地消失。

### 16.6 Follow-up、Mailbox 与 followup_task

本方案把 follow-on 统一命名为 Follow-up，但必须区分它的来源和生命周期。Codex 中的 follow-up 不是一种单一状态，也不是所有 continuation 的同义词。

#### 16.6.1 四种后续推进

| 类型 | 产生者 | 目标 | 是否新建根 Run | 典型处理 |
|---|---|---|---:|---|
| Model follow-up | Provider / Tool loop | 当前 Task | 否 | 同一 Turn 继续采样 |
| Pending-input follow-up | 用户 steer、系统输入、mailbox | 当前 Turn 或下一 Turn | 否 | 输入队列按 delivery phase 注入 |
| Child followup_task | Parent Agent | 已存在的非 Root ChildTurn | 否 | 触发目标 ChildTurn 的新一轮工作 |
| Recovery continuation | TurnRunner / 用户 | 原 Turn 的恢复分支 | 否 | 从 checkpoint 恢复或创建 continuation |

StopHook block 产生的 continuation prompt 也会让当前 Task 继续，但它是 Hook 决策结果，不应和用户 follow-up 或 child followup_task 混成同一来源。

#### 16.6.2 Model follow-up

Provider 一次采样结束后，RegularTask 应同时检查两个条件：

~~~text
model_needs_follow_up
  = Provider 表示还有后续工作，例如 Tool 结果需要继续处理

has_pending_input
  = 当前 Turn 的输入队列中存在可在此时交付的输入

needs_follow_up = model_needs_follow_up || has_pending_input
~~~

只要 needs_follow_up 为 true，Task 就不能进入 StopHook 或 TurnStop。它必须：

1. 保存当前采样结果；
2. 保存 Tool call、Tool result 或输入队列的 checkpoint；
3. 如果 model_needs_follow_up 为 true，接受当前 Turn 的 mailbox delivery；
4. 将可交付的 pending input 取出并经过 UserPromptSubmit 等输入 Hook；
5. 重新构造模型上下文；
6. 进入下一轮采样。

这解决一个重要边界：模型已经输出可见文本，不代表 Turn 一定已经结束；只要还有必须处理的 Tool、输入或协作消息，就必须继续推进。

#### 16.6.3 Mailbox delivery phase

当前 Turn 的 mailbox 交付需要显式记录阶段：

~~~text
CurrentTurn
  -> 当前 Turn 可以消费 mailbox / pending input

NextTurn
  -> 当前 Turn 已到回答边界
  -> queue-only 消息保留到下一 Turn
~~~

建议在 TurnTaskState 中保存：

~~~python
class MailboxDeliveryPhase(str, Enum):
    CURRENT_TURN = "current_turn"
    NEXT_TURN = "next_turn"

class PendingInputItem(BaseModel):
    input_id: str
    source: Literal["user", "system", "child", "hook", "tool"]
    kind: Literal["user_input", "inter_agent_communication", "context", "hook_prompt"]
    text: str | None = None
    target_turn_id: str | None = None
    trigger_turn: bool = False
    operation_id: str
    created_at: datetime
~~~

交付规则：

- 用户 steer 输入可以重新打开当前 Turn 的 mailbox delivery；
- Tool call 产生的继续工作可以重新打开当前 Turn；
- Hook continuation prompt 明确属于当前 Task；
- 带 trigger_turn 的 child communication 可以触发目标 ChildTurn 的下一轮；
- queue-only 的 child communication 在当前 Turn 已经到达回答边界后，保留到下一 Turn；
- 已经进入 NextTurn 的消息不能被普通轮询偷偷带回当前 Turn；
- 交付顺序必须稳定，重复 input_id 或 operation_id 不得重复注入。

#### 16.6.4 同一 Turn 的 Follow-up 流程

~~~text
RegularTask 采样
  -> 产生 assistant message / Tool result
  -> 检查 model_needs_follow_up
  -> 检查 has_pending_input
       |
       +-- 有 follow-up
       |     -> 持久化当前 checkpoint
       |     -> 选择 CurrentTurn 可交付输入
       |     -> 执行输入 Hook
       |     -> 将输入加入下一次模型上下文
       |     -> Task 继续 running
       |
       +-- 无 follow-up
             -> 进入 StopHook
             -> 可能被 Hook block 再次继续
             -> 最终才返回 TaskOutcome
~~~

在 context window 接近上限时：

~~~text
needs_follow_up && context_limit_reached
  -> 先执行 CompactTask / auto compact
  -> 保持当前 Task 的逻辑 continuation
  -> compact 成功后继续采样
  -> compact 被 Hook 阻止或失败则返回可解释结果
~~~

auto compact 不是 follow-up 的替代品，而是 follow-up 过程中保护上下文容量的内部步骤。

#### 16.6.5 Child followup_task

followup_task 是 Parent Agent 对已经存在的 ChildTurn 发送后续工作，要求遵守以下约束：

1. target 必须是已有的非 Root agent；
2. 不能通过 followup_task 给 Root Agent 发消息；
3. target 需要通过 agent_id 或稳定的 canonical task name 解析；
4. message 进入目标 ChildTurn 的 mailbox；
5. target 空闲时，触发一个新的 ChildTurn Turn；
6. target 运行时，在采样消息边界或 pending Tool call 完成后交付；
7. followup_task 自身是一个 Tool call，需要经过 ToolRegistry、权限、审计和幂等；
8. Parent 不直接修改 ChildTurn 状态，只提交一条受治理的 follow-up input。

建议接口：

~~~python
class FollowupTaskRequest(BaseModel):
    parent_turn_id: str
    target_child_turn_id: str
    target_agent_id: str
    message: str
    trigger_turn: bool = True
    operation_id: str

class FollowupTaskResult(BaseModel):
    target_child_turn_id: str
    accepted: bool
    delivery: Literal["queued", "delivered_current_turn", "triggered_new_turn"]
    target_turn_id: str | None = None
    duplicate: bool = False
~~~

followup_task 不等同于 send_message：

| 工具语义 | 是否触发目标 Turn | 适用场景 |
|---|---:|---|
| send_message | 否，通常只投递消息 | 传递信息，不要求立即工作 |
| followup_task | 是，空闲时触发，运行时排队到边界 | 给已有 agent 布置后续工作 |
| wait_agent | 否 | 等待 mailbox 或完成通知 |
| interrupt_agent | 取消当前目标 Turn | 中断当前执行但保留 agent 可用 |

#### 16.6.6 Child follow-up 的生命周期

~~~text
Parent Turn
  -> ToolRegistry.followup_task()
  -> 校验 target 是非 Root ChildTurn
  -> 写入 child mailbox
       |
       +-- ChildTurn idle
       |     -> 创建 child follow-up Turn
       |     -> ChildTask.start()
       |
       +-- ChildTurn running
             -> 保持当前 Turn
             -> 在采样消息边界交付
             -> 或等待当前 Tool 完成后交付
             -> ChildTask.resume_current_turn()
~~~

每次 child follow-up 都应产生：

- followup_task_started；
- followup_task_queued 或 followup_task_delivered；
- child_turn_started 或 child_turn_steered；
- child_turn_completed；
- parent notification。

Parent 收到 child 完成通知后，可以继续自己的 Task，也可以在 Graph safe point 读取新的 Artifact。Child 完成通知不能直接让 Parent 跳过当前 frontier 或绕过 PlanRole。

#### 16.6.7 Follow-up 与 StopHook 的边界

三者的顺序必须明确：

~~~text
pending input / child follow-up
  -> 先判断是否应交付给当前 Turn
  -> 有可交付 follow-up 则继续 Task
  -> 没有 follow-up 才调用 StopHook
  -> StopHook block 才生成 Hook continuation
  -> StopHook 允许结束后才进入 TurnStop callback
~~~

不能出现以下错误：

- 有 pending Tool result 时先运行 StopHook 并结束 Turn；
- StopHook block 被错误记录成用户 follow-up；
- followup_task 直接修改 parent Turn 状态；
- queue-only child message 在当前回答已经提交后又插入当前 Turn；
- child follow-up 创建新的无 parent 根 Run；
- follow-up 重试导致重复执行同一个外部 Tool。

#### 16.6.8 GraphExecutor 中的 Follow-up

GraphExecutor 需要把 follow-up 映射为 Graph 控制，而不是让任意消息直接改变 Graph：

| Follow-up 来源 | Graph 处理 |
|---|---|
| 当前 Node 的 Tool 结果 | 由当前 Node Task 继续 |
| 当前 Graph 的用户补充 | 写入 Run/Turn 输入，交给 PlanRole 判断是否 patch |
| Parent 给 ChildTurn 的 followup_task | 投递目标 ChildTurn，结果在 safe point 汇入 |
| Hook continuation | 生成受限 continuation request，不能直接改 Graph |
| Gate 之后的人工意见 | 创建 ReviewTask 或新 continuation |

Graph 的 follow-up 必须携带：

- base graph revision；
- parent turn / node；
- 输入 Artifact 引用；
- operation_id；
- 期望的交付边界；
- 是否允许触发新 ChildTurn；
- 是否需要审批。

GraphRunner 仍然只在 safe point 应用 patch。Follow-up 的存在不等于允许实时修改在途 frontier。

#### 16.6.9 持久化和恢复

Follow-up 必须和 Turn 一起持久化：

~~~text
turn.json
  -> follow_up_state
       -> pending_input_ids
       -> mailbox_delivery_phase
       -> last_delivered_sequence
       -> follow_up_count
       -> child_follow_up_ids
       -> compact_before_continue

events.jsonl
  -> followup_requested
  -> followup_queued
  -> followup_delivered
  -> followup_deferred
  -> followup_duplicate
  -> followup_completed
~~~

进程重启后：

1. 读取 mailbox delivery phase；
2. 按 sequence 恢复未交付输入；
3. 校验 operation_id 是否已经产生结果；
4. 如果目标 ChildTurn 空闲，恢复其 follow-up Turn；
5. 如果目标 ChildTurn 已完成，拒绝旧 follow-up 或要求创建新的 continuation；
6. 不能因为恢复而重复执行已有成功 receipt 的 Tool。

#### 16.6.10 Follow-up 验收标准

必须测试：

1. Provider 返回 Tool call 后，当前 Turn 会继续而不是提前结束。
2. model_needs_follow_up 和 has_pending_input 任一为 true 时不会进入 StopHook。
3. 当前 Turn 的用户 steer 输入可以在下一次采样前被交付。
4. 回答边界后的 queue-only child message 延迟到下一 Turn。
5. Tool call 会重新打开当前 Turn 的 mailbox delivery。
6. 上下文超限时先 compact，再继续原 Task。
7. followup_task 不能 targeting Root Agent。
8. 空闲 ChildTurn 的 followup_task 会触发新 ChildTurn Turn。
9. 运行中的 ChildTurn 会在消息边界或 Tool 完成后接收 follow-up。
10. send_message 不会意外触发新的 ChildTurn。
11. 重复 followup_task operation_id 返回 duplicate，不重复投递。
12. Parent 能收到每个 ChildTurn follow-up Turn 的完成通知。
13. Follow-up、StopHook continuation 和 recovery continuation 在事件中有不同 source。
14. 重启后 pending input、mailbox phase 和 child follow-up 都能恢复。

## 17. 暂停、取消、超时和审批

### 17.1 控制请求

控制请求应绑定到：

- run_id；
- turn_id；
- 可选 task_type；
- 可选 graph_id / node_id；
- actor；
- reason；
- created_at；
- expires_at；
- request_id。

控制请求自身也应有状态和审计记录。

### 17.2 Pause

暂停不是立即杀死当前 Python 调用，而是在安全点停止继续推进：

~~~text
用户请求 pause
  -> AgentControl 写入 pause request
  -> 当前 Task 检查控制
  -> 当前模型 / Tool 调用结束或响应取消
  -> 保存 checkpoint
  -> TurnRunner 写入 paused
  -> 发出 turn_paused
~~~

对于不可中断的外部数据源调用，必须先等待调用返回或达到调用超时，不能伪装成已经停止。

### 17.3 Cancel

取消需要区分：

- cancel requested：已经请求取消；
- cancelled：当前 Task 已经停止并持久化；
- cancellation ignored：外部调用没有提供取消能力，必须记录风险。

只有 cancelled 才是最终响应中的取消终态。

### 17.4 Timeout

建议至少分三层：

| 层级 | 作用 |
|---|---|
| provider timeout | 限制单次模型调用 |
| tool / node timeout | 限制单个能力执行 |
| turn timeout | 限制整个 Turn |

最底层 timeout 发生时，Task 根据 failure_mode 决定重试、跳过、等待或失败；TurnRunner 负责最终状态收敛。

### 17.5 Approval

审批生命周期：

~~~text
pending -> approved -> execution
pending -> rejected
approved -> expired / revoked
~~~

审批必须绑定：

- run_id / turn_id；
- node_id 或 tool_id；
- capability / role 版本；
- 输入摘要；
- 风险说明；
- 审批人；
- 审批时间；
- 过期时间。

没有有效审批的 Task 只能返回 waiting_approval，不得直接执行，也不得将等待伪装成失败。

## 18. 错误模型与恢复

### 18.1 错误分层

~~~text
TurnError
  +-- InputError
  +-- RouteError
  +-- ProviderError
  +-- ToolError
  +-- PolicyError
  +-- ApprovalError
  +-- GraphError
  +-- ArtifactError
  +-- PersistenceError
  +-- ControlError
~~~

错误对象至少包含：

~~~python
class TaskError(BaseModel):
    code: str
    message: str
    retryable: bool
    operation_id: str | None = None
    source: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
~~~

### 18.2 错误映射

| 错误 | 默认处理 | 是否自动重试 |
|---|---|---|
| 输入缺失 | needs_clarification | 否 |
| provider 临时错误 | 保存 checkpoint 后 retry | 按策略 |
| Tool schema 错误 | 失败并要求修复调用 | 通常否 |
| 数据源限流 | 延迟、排队或可解释失败 | 按 retry-after |
| 权限拒绝 | failed | 否 |
| 审批缺失 | waiting_approval | 否 |
| Graph node 可恢复失败 | retry / patch / continue | 按 node 策略 |
| blocking Gate 不通过 | gated | 否，需 review 或补跑 |
| 持久化失败 | 停止继续执行 | 不应盲目重试副作用 |
| Run 超时 | timed_out | 通过新 continuation |

### 18.3 恢复原则

恢复必须先读取已持久化的：

1. TurnSnapshot；
2. TaskSnapshot；
3. GraphSnapshot（如有）；
4. ArtifactBoard；
5. pending approval；
6. control requests；
7. 最近事件和 operation receipts。

恢复不能仅依赖内存中的 MainAgent state，也不能因为进程重启而重新随机生成一个 Graph。

### 18.4 幂等和副作用

所有可能产生外部副作用的 Tool 必须有 operation_id 或 idempotency key。恢复时：

- 已有成功 receipt：直接返回原结果；
- 有进行中 receipt：检查 lease 或等待；
- 没有 receipt：才允许开始调用；
- 无法证明调用幂等：进入人工审批或以可解释失败结束。

## 19. 持久化设计

### 19.1 统一的 Turn 记录

建议新增或逐步形成以下 canonical 记录：

~~~text
run/
  turn.json
  task.json
  request.json
  response.json
  events.jsonl
  controls.jsonl
  approvals.json
  artifacts/
  graph/
    snapshot.json
    patches.jsonl
  transcript/
    checkpoint.json
~~~

不要求第一阶段立刻改变所有文件名。关键是确定：

- 哪个文件或 store 是 Turn 状态的真源；
- 哪个文件是 Task 的真源；
- GraphRunner 的 snapshot 如何被引用；
- response 是否只能由 TurnRunner 写入；
- 事件是否能独立回放。

### 19.2 与当前 RunStore 的兼容

迁移期间建议：

1. 保留现有 run 目录；
2. 新增 turn/task 元数据文件；
3. 旧的 agent_os_pending.json、clarification_pending.json 继续可读；
4. 由兼容层将旧状态投影成 TurnSnapshot；
5. 新入口只写 canonical Turn 字段；
6. 经过一个稳定周期后再删除旧写入路径。

禁止直接删除旧文件后依赖内存状态恢复，否则会放大线上重启风险。

### 19.3 Snapshot 与事件的职责

| 数据 | Snapshot | Event |
|---|---|---|
| 当前 Turn 状态 | 是 | 是 |
| 所有状态变化历史 | 否 | 是 |
| 当前 Task 类型 | 是 | 是 |
| Graph revision | 是 | 是 |
| Tool 原始输出 | 通过引用 | 可记录摘要和 receipt |
| 最终回答 | 是 | 是 |
| 恢复 checkpoint | 是 | 变化事件 |

Snapshot 用于快速恢复，Event 用于回放和审计，两者不能互相替代。

## 20. 迁移计划

迁移目标是每一步都有可运行版本，避免一次性重写 Agent OS。

### Phase 0：冻结协议和建立基线

目标：

- 先不改变行为；
- 确认当前 Core、Task、Graph 的状态和事件；
- 建立 Turn 级测试夹具。

工作：

- 定义 TurnStatus、TurnInput、TaskOutcome 的最小模型；
- 为现有普通请求和 domain_task 记录 task selection；
- 给现有事件补充 run_id、thread_id、request_id；
- 盘点 SessionStart、UserPromptSubmit、PreToolUse、PostToolUse、PreCompact、PostCompact、Stop 和 SubagentStop 的现有或计划入口；
- 明确哪些 Hook 可以阻止执行，哪些仅用于生命周期通知；
- 记录当前 pending input、mailbox 和 follow-up 的交付边界；
- 增加核心回放测试；
- 测量普通 Turn、Graph Turn、暂停恢复和失败恢复。

退出条件：

- 现有测试全部通过；
- 能从一次请求生成一个可读的 Turn trace；
- 没有改变现有响应语义；
- 能识别当前状态来源和重复写入点。

### Phase 1：引入 TurnRunner，接管 RegularTask

目标：

- 让 RegularTask 成为原始 Turn 的统一执行容器；
- 暂时保留 Graph 旧入口。

工作：

1. 创建 TurnSnapshot、TaskSnapshot 和 TurnConfig；
2. 创建 RegularTask 和 RegularExecutor；
3. 把 MainAgent._run_agent_loop() 的循环控制迁入 RegularExecutor，把其中的 provider/tool step 抽取为 CoreLoopExecutor；
4. AgentOS 普通请求改为调用 TurnRunner；
5. 旧 MainAgent.run_request() 保留兼容转发；
6. 在 RegularTask 中接入 HookRuntime，先支持 StopHook 和 UserPromptSubmit；
7. 在 RegularTask 中加入 model follow-up、pending input 和 mailbox delivery phase；
8. 引入 execution_mode=core / task；
9. 统一普通请求的 pause、cancel、response 和 event。

退出条件：

- 普通对话、Core Tool、provider fallback 行为不变；
- 重启后可以从 checkpoint 恢复；
- 相同 idempotency key 不重复执行 Tool；
- MainAgent 不再直接写最终 ResponseEnvelope。

### Phase 2：GraphExecutor 适配现有 GraphRunner

目标：

- 让 domain_task 通过 RegularTask 的 execution_mode=graph 进入同一个 TurnRunner；
- GraphRunner 继续负责 Graph 内部执行。

工作：

1. 实现 GraphRunnerAdapter.step；
2. 实现 GraphExecutor；
3. 将 GraphStepOutcome 映射回 RegularExecutor；
4. 将 Graph event 包装为 TurnEvent；
5. 将 Graph 状态映射为 TurnState 的 execution_mode=graph；
6. 在 Graph safe point 接入 StopHook，Hook 只提交 continuation 建议，不直接修改 Graph；
7. 让 GraphExecutor 区分 Node 内部 follow-up、用户补充和 ChildTurn followup_task；
8. 由 TurnRunner 统一调用最终回答和 response 持久化。

退出条件：

- 普通和 Graph 请求都从同一个 TurnRunner 和 RegularTask 进入；
- Graph pause/resume/cancel/timeout 通过统一控制入口工作；
- Graph 的 gated 语义没有变成 failed；
- Graph 结果仍保留完整 Artifact、Gate 和 node 状态；
- SSE 可以同时消费普通和 Graph Turn。

### Phase 3：统一控制、恢复和 Worker lease

目标：

- 消除 Core 与 Graph 的重复生命周期控制。

工作：

- AgentControl 以 Turn/Run 为统一入口；
- GraphRunner 只消费 Graph 级控制；
- RegularTask 的 Core / Graph mode 都使用统一 worker lease；
- 统一超时和取消传播；
- 统一恢复入口和 continuation；
- 对重复 resume 做幂等保护。

退出条件：

- 同一个 Run 不会被两个 worker 同时推进；
- 进程重启后 Core 和 Graph 都能恢复；
- pause/resume/cancel 的事件序列一致；
- 旧入口不能绕过 scope 和 lease。

### Phase 4：ChildTurn 和 RoleContract

目标：

- 把 child agent 和动态 runtime role 接入统一 Turn 模型。

工作：

- 定义 ChildTurn parent relation；
- 定义 RoleContract；
- 创建 RoleExecutorAdapter；
- 将 role 的 Tool、schema、预算和审批接入 Registry；
- 为 Root Turn 和 ChildTurn 分别接入 Stop / SubagentStop；
- 增加 followup_task 的非 Root 校验、队列投递、空闲触发和运行中边界交付；
- Graph node 通过 ChildTurn 获得独立状态；
- parent 只在安全点消费 child Artifact。

退出条件：

- child 可以独立暂停、失败和恢复；
- child 失败不会破坏 parent snapshot；
- parent 能看到 child 的完整事件和 Artifact lineage；
- 动态 role 不存在绕过权限和事件的调用路径。

### Phase 5：ReviewTask 和 CompactTask

目标：

- 在统一生命周期上增加 Codex 风格的 review 和 compact。

工作：

- ReviewTask 审查 Core 或 Graph Artifact；
- CompactTask 压缩模型上下文但保留审计数据；
- 接入 PreCompact / PostCompact Hook，并让 Hook 失败策略可配置；
- 验证 compact 前后的 follow-up checkpoint、pending input 和 mailbox phase 不丢失；
- 通过 continuation 触发 review / compact；
- 为每种 Task 增加独立指标和恢复测试。

退出条件：

- review 不能篡改原 Artifact；
- compact 后可以继续原 Task；
- 压缩不会删除用户原文、审批记录或 Tool receipt；
- Task 类型增加不需要新增 AgentOS 顶层分支。

### Phase 6：清理旧路径

目标：

- 让 TurnRunner 成为唯一标准运行入口。

工作：

- 删除 AgentOS 中的 Core/Graph 双分支执行代码；
- 将 MainAgent.run_request() 降为明确的 deprecated adapter 或移除；
- 删除重复的顶层 response、control 和 event 写入；
- 统一文档、API、SSE、Chat 和 Admin 查询；
- 收敛旧状态兼容读取。

退出条件：

- 新增 Task 类型只需注册 TaskFactory；
- API、CLI、Chat 不再区分两套运行实现；
- 所有历史 Run 都能通过兼容投影回放；
- 旧兼容入口有明确下线时间和监控。

## 21. 测试方案

### 21.1 TurnRunner 单元测试

必须覆盖：

- 新建 Turn；
- 重复 idempotency key；
- 正确 Task 选择；
- 非法状态迁移；
- paused -> running；
- waiting_approval -> running；
- completed / failed / gated 等终态不可重复执行；
- response 只由 Runner 持久化；
- Task 抛出异常时生成统一 TaskError；
- event sequence 单调递增。

### 21.2 RegularTask 测试

至少覆盖：

- provider 直接返回 assistant message；
- 一次 Tool call 后完成；
- 多轮 Tool loop；
- loop limit；
- provider unavailable 的 deterministic fallback；
- Tool schema 验证失败；
- Tool timeout；
- Tool cancel；
- Tool approval；
- checkpoint 后进程重启恢复；
- 重试不重复外部副作用。

### 21.3 GraphExecutor 测试

至少覆盖：

- 新建 Graph；
- 从已有 GraphSnapshot 恢复；
- Graph frontier 事件被正确包装；
- Graph paused 映射为 Turn paused；
- waiting approval 映射正确；
- blocking Gate 失败映射为 gated；
- Graph failure 映射为 failed；
- Graph timeout 和 cancel；
- Graph patch 后 revision 和 Artifact lineage 保持；
- GraphRunner 旧事件与新 TurnEvent 不重复造成错误状态。

### 21.4 ChildTurn 测试

- parent/child 关系正确；
- child 使用 parent 授权的 Artifact；
- child 不能越权访问其他 tenant；
- child 不能直接修改 parent Turn；
- child 失败后 parent 可以继续或按 failure_mode 停止；
- parent 恢复时 child 状态可重建；
- child Artifact 可以被 parent 安全消费。

### 21.5 属性和回放测试

建议增加以下不变量测试：

1. 任意合法事件序列回放后，snapshot 状态等于最后合法状态。
2. 重复提交同一 operation_id 不产生第二个外部副作用。
3. 重复提交同一 Graph patch 不改变第二次 revision。
4. 任意终态都存在对应的终态事件。
5. gated 永远不会被 ResponseEnvelope 映射成 completed。
6. 任何 Artifact 都能追溯到 producer、Task、Run 和输入来源。
7. 任意 child turn 都能追溯到 parent turn。

### 21.6 端到端测试矩阵

| 场景 | RegularTask core | RegularTask task | RegularTask graph |
|---|---:|---:|---:|
| 普通回答 | 必测 | - | - |
| Core Tool | 必测 | 必测 | 可选 |
| provider fallback | 必测 | 必测 | - |
| 暂停恢复 | 必测 | 必测 | 必测 |
| 审批 | 可选 | 必测 | 必测 |
| 多节点并发 | - | - | 必测 |
| Gate | - | 可选 | 必测 |
| 局部重跑 | - | 可选 | 必测 |
| 子 agent | - | 可选 | 必测 |
| 进程重启恢复 | 必测 | 必测 | 必测 |

## 22. 观测与指标

### 22.1 Turn 级指标

- Turn 数量和各终态比例；
- received -> running 延迟；
- running -> completed 延迟；
- pause、resume、cancel、timeout 数量；
- 幂等命中数；
- 恢复成功率；
- continuation 数量；
- 每种 Task 的 p50 / p95 / p99 延迟。

### 22.2 Task 级指标

- RegularTask provider 调用次数；
- Tool calls / Turn；
- loop limit 命中率；
- RegularTask task mode checkpoint 次数；
- 同一 Turn 的 model follow-up 次数；
- pending input 和 mailbox 延迟交付次数；
- followup_task 接受、排队、触发、重复和拒绝数量；
- ReviewTask finding 数；
- CompactTask 压缩前后 token 数；
- GraphExecutor frontier 数；
- Graph patch 数和接受率；
- child turn 数和失败率。

### 22.3 资源和治理指标

- 每个 Run 的 token、成本和耗时；
- 每个 Tool 的 timeout、retry、rate limit；
- 数据源空结果率；
- approval 等待时长；
- worker lease 冲突；
- Artifact schema 校验失败；
- Gate 失败率；
- 跨租户访问拒绝数。

### 22.4 Trace 字段

所有日志和 trace 至少包含：

~~~text
tenant_id
user_id
run_id
thread_id
turn_id
parent_turn_id
task_type
graph_id
graph_revision
node_id
operation_id
event_id
actor
~~~

没有这些字段的旧日志可以继续保留，但不能作为新架构的唯一诊断依据。

## 23. 兼容策略

### 23.1 API 兼容

外部 API 继续返回 ResponseEnvelope，但增加或规范以下字段：

~~~python
class ResponseEnvelope(BaseModel):
    run_id: str
    thread_id: str
    turn_id: str
    status: TurnStatus
    task_type: str
    message: str | None
    artifact_ids: list[str]
    final_artifact_ids: list[str]
    pending_approval_ids: list[str]
    continuation: Continuation | None
~~~

旧客户端不识别 turn_id 或 task_type 时，可以忽略新增字段。旧的 status 映射必须明确记录，不应把 gated 和 failed 混成同一个结果。

### 23.2 旧 Run 兼容读取

对于没有 turn.json 的历史 Run：

1. 从 request.json 和现有 agent state 推导 TurnInput；
2. 从 Graph snapshot 是否存在判断 Task 类型；
3. 从 pending clarification 推导 received / ready；
4. 从 response.json 推导 completed；
5. 无法可靠推导的状态标记为 legacy_unknown，并禁止自动继续执行；
6. 旧 Run 只允许回放或人工明确重跑。

### 23.3 特性开关

建议至少有：

~~~text
COGNITRADE_TURN_RUNNER_ENABLED
COGNITRADE_TURN_RUNNER_REGULAR_ENABLED
COGNITRADE_TURN_RUNNER_GRAPH_ENABLED
COGNITRADE_TURN_RUNNER_CHILD_ENABLED
~~~

切换策略：

- 开发环境先全开；
- 测试环境按请求类型分组；
- 生产先普通 Core，再 Graph；
- 出现结果差异时只回退执行入口，不删除新产生的事件和 snapshot；
- 回退后的新请求仍需记录 legacy task_type。

## 24. 风险与取舍

### 24.1 主要风险

#### 风险 1：双重持久化

如果 TurnRunner 和 GraphRunner 都认为自己是顶层 owner，可能出现状态覆盖或事件重复。

缓解：

- 明确 TurnRunner 是 Turn 级真源；
- GraphRunner 是 Graph 级真源；
- 规定谁写哪些字段；
- 增加 snapshot revision 和事件 sequence 校验。

#### 风险 2：状态映射丢失业务语义

Graph 的 gated、waiting approval 和 node failure 不能简单转换成 generic failed。

缓解：

- 使用显式状态映射表；
- 对每个映射增加集成测试；
- ResponseEnvelope 保留 Graph summary 和 Gate summary。

#### 风险 3：MainAgent 仍然通过兼容方法绕过 Runner

如果 run_request() 仍有真实实现，调用方会继续分裂。

缓解：

- 兼容方法内部只转发；
- 在日志中标记 legacy entry；
- 为直接调用增加测试告警；
- Phase 6 删除或正式 deprecated。

#### 风险 4：ChildTurn 过度创建

每个 Tool call 或每个 Graph node 都创建 ChildTurn，会放大持久化和可观察性开销。

缓解：

- ChildTurn 只用于有独立生命周期、权限、恢复或产物边界的子工作；
- 普通 Tool call 仍然只是 Tool call；
- Graph node 仍然可以在当前 GraphRunner 内直接执行轻量 role。

#### 风险 5：过早引入全局 scheduler

如果在 Turn 协议未稳定前引入跨 Run 调度，会让资源、租约、公平性和恢复问题同时爆炸。

建议：

- 先保留当前 Run 内调度；
- 用指标确认多 Run 并发瓶颈；
- 需要时再将 worker lease 和队列抽象成独立模块。

### 24.2 关键取舍

| 取舍 | 推荐选择 | 原因 |
|---|---|---|
| 一次性重写 GraphRunner vs 适配器 | 先适配器 | 降低回归风险 |
| 所有请求都创建 Graph vs 按需 Graph | 按需 Graph | 保持普通问答低开销 |
| Task 直接改 Turn vs 返回 Outcome | 返回 Outcome | 生命周期边界清晰 |
| ChildTurn 替代所有 Node vs 选择性使用 | 选择性使用 | 控制复杂度和成本 |
| 事件只存最新状态 vs 事件流 | Snapshot + Event | 支持恢复、回放、审计 |
| plugin 安装决定顶层路径 vs 能力影响 Task 选择 | 能力影响选择 | 避免两套运行时 |

## 25. 不建议的方案

### 25.1 不建议把 MainAgent 改成更大的 while loop

这只能把两条路径放在一个函数中，不能解决：

- 状态 owner 不清晰；
- Graph 和 Core 恢复不同；
- Review / Compact 无自然入口；
- child 生命周期缺少边界；
- 测试仍依赖一个深模块。

### 25.2 不建议所有请求都强制进入 Graph

这样会让普通问答承担：

- Graph snapshot；
- node state；
- frontier；
- ArtifactBoard；
- gate；
- 更高延迟和持久化成本。

统一 Turn 不等于统一 Graph。

### 25.3 不建议把一个 Graph node 当成一个 Turn

Node 是 Graph 计划中的执行单元；Turn 是可恢复的用户意图生命周期。把二者混合后，局部重跑、并行 frontier 和最终响应都会变得难以解释。

### 25.4 不建议把动态 role 直接当作自由 prompt

没有 RoleContract 的动态 role 无法可靠校验：

- 输入输出；
- 工具权限；
- 预算；
- 审批；
- 失败策略；
- 版本和来源。

### 25.5 不建议让 Task 自己生成 ResponseEnvelope

这会重新引入多套响应语义。Task 返回领域结果，TurnRunner 统一生成 API 响应。

## 26. 实施顺序

推荐的具体提交顺序：

1. 只新增模型和协议，不改变入口行为；
2. 新增事件包装器和 Turn trace；
3. 将普通 Core Loop 包装为 RegularTask；
4. 让 AgentOS 普通路径通过 TurnRunner；
5. 加入 GraphRunnerAdapter；
6. 让 domain_task 通过 RegularTask 的 execution_mode=graph；
7. 统一 control、lease、response 和 recovery；
8. 接入 ChildTurn 和 RoleContract；
9. 加入 ReviewTask；
10. 加入 CompactTask；
11. 清理旧入口。

每一步都应有单独提交和可回退开关。不要在同一提交中同时重写 MainAgent、GraphRunner、API response、前端状态和持久化格式。

## 27. 验收标准

### 27.1 架构验收

- 所有标准请求都通过 AgentOS -> TurnRunner -> Task；
- AgentOS 不再按普通 / domain_task 分别实现顶层生命周期；
- MainAgent 不再拥有顶层 pause/resume/cancel 状态机；
- GraphRunner 不再直接生成最终 API ResponseEnvelope；
- 新增 Task 类型不需要增加 AgentOS 顶层分支。

### 27.2 行为验收

- 普通 Core 对话结果与迁移前一致；
- Graph 研究任务结果与迁移前一致；
- Graph gated 语义保留；
- 普通和 Graph 都支持统一的暂停、恢复、取消和超时；
- provider fallback 仍然可用；
- Tool 的权限、审计、幂等和限流不被绕过；
- 任何最终回答都能追溯到对应 Turn 和 Run。

### 27.3 恢复验收

- 进程在 provider 调用后退出，可以从 checkpoint 恢复；
- 进程在 Tool 返回后退出，不重复外部副作用；
- 进程在 Graph frontier 后退出，可以恢复下一轮 frontier；
- 等待审批的 Turn 可以在审批后继续；
- 相同 idempotency key 不创建重复 Run；
- 历史 Run 可以回放，即使不能自动继续。

### 27.4 可观察性验收

- 每个 Turn 有完整的 start 到终态事件；
- 普通和 Graph 事件字段一致；
- SSE 支持 Last-Event-ID 续传；
- child turn 能被 parent trace 找到；
- Graph node 和 Artifact lineage 可跳转；
- task selection 原因可查询；
- 失败可以定位到 provider、Tool、Node、Role 或持久化层。

## 28. 一个普通 Turn 的示例

用户请求：

~~~text
“帮我查一下今天的市场数据。”
~~~

执行：

~~~text
1. AgentOS 验证 user/tenant，创建 run_id、thread_id。
2. RequestIntake 判定为 accepted。
3. MainAgent.route 选择 Core capability。
4. TaskFactory 选择 RegularTask，原因是无需 DomainPack、Graph 或 Gate。
5. TurnRunner 写入 turn_received、turn_ready、task_selected。
6. RegularTask 调用 provider。
7. Provider 返回一个市场数据 Tool call。
8. ToolRegistry 校验权限、schema、速率限制和幂等键。
9. Tool 返回数据，写入 tool_call_completed。
10. RegularTask 将结果放入受限 transcript，再调用 provider。
11. Provider 生成回答。
12. TurnRunner 校验回答和引用，写入 response。
13. Turn 进入 completed。
~~~

整个过程中没有创建 Graph，但生命周期仍然与 Graph Turn 相同。

## 29. 一个 Graph Turn 的示例

用户请求：

~~~text
“做一份包含数据拉取、风险分析和最终报告的个股研究。”
~~~

执行：

~~~text
1. MainAgent.route 识别 DomainPack、多个 role、专业 Artifact 和 Gate。
2. TaskFactory 选择 RegularTask，设置 execution_mode=graph。
3. MainAgent.PlanRole 生成初始 Graph。
4. RegularExecutor 挂载 GraphExecutor，调用 GraphRunnerAdapter.step。
5. GraphRunner 计算 ready frontier，执行数据拉取节点。
6. 数据节点提交带来源和版本的 Artifact。
7. 风险节点读取声明的输入 Artifact 并生成分析 Artifact。
8. Report Composer 生成报告 Artifact。
9. Gate 检查来源完整性、数据新鲜度和风险字段。
10. 若 Gate 通过，MainAgent 生成最终 assistant_message。
11. TurnRunner 写入 final_artifact_ids 和 response。
12. Turn 进入 completed。
~~~

如果 Gate 未通过：

~~~text
GraphRunner -> gated
GraphExecutor -> GraphStepOutcome(status=gated)
RegularTask -> TaskOutcome(status=gated)
TurnRunner -> turn_gated
用户看到“需要补充或人工确认”，而不是“执行失败”
~~~

## 30. 总结

Cognitrade 要借鉴 Codex 的不是某个具体类名，而是一个清晰的运行时分层：

~~~text
统一 Turn 生命周期
  -> RegularTask
       -> RegularExecutor 原始 Turn loop
            -> CoreLoopExecutor
            ->（GraphExecutor）
  -> 特殊 Task
       -> ReviewTask
       -> CompactTask
~~~

推荐的最终职责关系是：

~~~text
AgentOS       入口、身份、兼容
MainAgent     路由、计划、观察、最终回答
TurnRunner    生命周期、控制、恢复、事件、最终响应
Task          当前执行策略
RegularExecutor RegularTask 的唯一串行 Turn loop
GraphExecutor  Graph 配置在 RegularExecutor 内的 step executor
GraphRunner   Graph 内部调度和状态推进
ToolRegistry  工具治理和副作用边界
ArtifactBoard 结构化事实和 lineage
ChildTurn     独立子执行生命周期
~~~

最重要的实施原则有三条：

1. 先统一 Turn 边界，再迁移执行代码；
2. 先用 GraphRunnerAdapter 接入，确认协议后再重构 GraphRunner；
3. skill/plugin 的安装状态只影响能力和 Task 选择，不创建另一套顶层运行时。

按 Phase 0 到 Phase 6 推进，可以在保持现有行为和持久化兼容的前提下，逐步把 Cognitrade 的 Main Agent 变成类似 Codex 的统一 Turn/Task 架构。

## 31. 完整 Turn 链路：原始骨架与配置挂载

本节是整个方案的最终运行链路。约定如下：

- 链路外的节点属于任何请求都需要的原始 Turn；
- 全角括号 （...） 内是可加载配置、可选 Task、Hook、Plugin 或治理扩展；
- 删除链路中所有全角括号及其中内容后，剩下的就是最小原始 Turn；
- 括号内的扩展不能拥有独立的根生命周期，不能创建第二个无关联 Run；
- 括号内的 Graph、StopHook、Follow-up、Mailbox、Approval 和 Plugin 都共享当前 Turn 的身份、控制、事件和持久化边界。

### 31.1 删除配置后的原始 Turn

下面是删除所有可选配置后的最小链路：

~~~text
用户输入
  -> AgentOS.handle_request
  -> RunStore.create_or_load
  -> TurnRunner.start_or_resume
  -> TurnSnapshot
  -> TurnContext
  -> MainAgent.route
  -> TaskFactory.select
  -> RegularTask.start_or_resume
  -> RegularExecutor.loop
  -> MainAgent / Provider
  -> ToolRegistry
  -> TaskOutcome
  -> TurnRunner.finalize
  -> ResponseEnvelope
  -> TurnComplete
~~~

这条骨架不假设请求一定使用 Graph，也不假设一定安装了某个 Pack、Skill 或 Plugin。最小普通请求可以在 TurnTask 中使用 RegularTask 完成。

实现注记：当前第一阶段为了兼容既有路由，`MainAgent.route()` 在 `TurnRunner` 创建/恢复具体执行配置之前完成；因此现有入口的可观察顺序是 `AgentOS.handle_request -> MainAgent.route -> TurnRunner -> RegularTask`。目标架构会把路由结果作为 TurnContext 的前置阶段纳入同一生命周期，但不会因此创建第二个 Turn。

### 31.2 完整配置投影链路

~~~text
用户输入
  -> AgentOS.handle_request
     （身份校验、tenant scope、兼容 API 转换、RequestEnvelope 归一化）
  -> RunStore.create_or_load
     （idempotency key、已有 response、已有 continuation、worker lease）
  -> TurnRunner.start_or_resume
     （加载 TurnConfig、恢复 TurnSnapshot、校验合法状态迁移）
  -> TurnSnapshot
     （task_type=regular / review / compact；execution_mode=core / task / graph）
     （task_config_ref、task_state_ref、graph_state_ref、plugin_refs）
  -> TurnContext
     （HookRuntime）
     （FollowUpRuntime）
     （Mailbox）
     （AgentControl）
     （ToolRegistry、RoleRegistry、ArtifactBoard、ApprovalStore）
  -> MainAgent.route
     （RequestIntake）
     （Skill / Plugin / DomainPack 能力目录）
     （needs_clarification）
  -> TaskFactory.select
     （RegularTask 的 core / task execution_mode）
     （GraphExecutorConfig）
     （ReviewTaskConfig 或 CompactTaskConfig）
  -> RegularTask.start_or_resume
  -> RegularExecutor.loop
     （RegularExecutor 挂载 GraphExecutor）
     （从同一个 TurnContext 加载 GraphSnapshot）
     （MainAgent.PlanRole 生成或恢复 Graph）
     （GraphRunnerAdapter 调用 GraphRunner）
  -> MainAgent / Provider
     （SessionStart Hook）
     （UserPromptSubmit Hook）
     （Skill / Plugin / MCP 上下文注入）
     （模型上下文压缩前的 PreCompact Hook）
  -> ToolRegistry
     （PreToolUse Hook）
     （PermissionRequest Hook）
     （Approval）
     （Tool schema、权限、预算、资源租约、幂等和审计）
     （PostToolUse Hook）
  -> TaskOutcome
     （model_needs_follow_up）
     （pending input / user steer）
     （Mailbox delivery phase: CurrentTurn / NextTurn）
     （ChildTurn followup_task）
     （StopHook / SubagentStop）
     （Hook block 产生 continuation prompt）
     （Graph frontier、Node、Artifact、Gate）
  -> TurnRunner.finalize
     （TurnStop lifecycle callback）
     （TurnAbort / TurnError lifecycle callback）
     （最终 Artifact 校验、assistant_message、response 幂等持久化）
  -> ResponseEnvelope
     （status、task_type、graph_id、artifact_ids、continuation、pending approvals）
  -> TurnComplete
     （HookCompleted、SSE、回放、释放 worker lease）
~~~

上面链路中，括号内的内容都是“当前 Turn 的可加载配置或扩展执行器”。删除括号内容后，不应改变原始 Turn 的输入、Task、Outcome、最终响应和完成事件的基本顺序。

### 31.3 TurnConfig 的加载时机

配置加载发生在 TurnRunner 创建或恢复 Turn 之后、Task 执行之前：

~~~text
RequestEnvelope
  -> TurnRunner.resolve_config()
       （读取默认配置）
       （读取 user / tenant / project 配置）
       （读取 Skill / Plugin / DomainPack 声明）
       （校验 managed policy、权限和版本）
       （解析 task_type）
       （解析 GraphExecutorConfig）
       （解析 HookConfig）
       （解析 FollowUpConfig）
  -> TurnSnapshot.config_hash
  -> TaskFactory.restore_or_create()
  -> Task.start_or_resume()
~~~

配置必须生成稳定的 config_hash 和 config_version：

- 同一个恢复操作使用原始配置快照；
- 配置文件后来变化不能隐式改变已经运行的 Turn；
- 新的用户 follow-up 可以明确选择继承原配置或创建新配置；
- Graph patch 不能改变 Turn 的身份配置，只能改变 Graph revision；
- Hook 和 Plugin 版本必须进入事件和审计记录。

### 31.4 不调用 Graph 的完整展开

当 task_type 没有被配置为 Graph 时，Graph 扩展被删除，保留以下执行：

~~~text
用户输入
  -> AgentOS.handle_request
  -> RunStore.create_or_load
  -> TurnRunner.start_or_resume
  -> TurnSnapshot
  -> TurnContext
  -> MainAgent.route
  -> TaskFactory.select
  -> RegularTask
  -> RegularExecutor.loop
  -> Provider
  -> ToolRegistry
  -> TaskOutcome
  -> TurnRunner.finalize
  -> ResponseEnvelope
  -> TurnComplete
~~~

每一轮 RegularTask 的内部顺序：

~~~text
RegularTask
  -> 读取 transcript checkpoint
  -> Provider
  -> 判断是否有 Tool calls
       |
       +-- 有 Tool calls
       |    -> ToolRegistry
       |    -> Tool result
       |    -> 持久化 checkpoint
       |    -> FollowUpRuntime
       |         （model follow-up）
       |         （pending input）
       |         （Mailbox）
       |    -> Provider 下一轮
       |
       +-- 没有 Tool calls
            -> FollowUpRuntime
                 （检查 model_needs_follow_up）
                 （检查 has_pending_input）
            -> StopHook
                 （Stop）
                 （block -> continuation prompt -> Provider 下一轮）
                 （stop -> TaskOutcome）
~~~

这里的 FollowUpRuntime、StopHook 和 Mailbox 都在同一个 RegularTask/Turn 内运行，不会因为一次 Tool call 或一次 Hook block 创建新的根 Turn。

### 31.5 调用 Graph 的完整展开

当 `execution_mode=graph` 时，原始 Turn 的外层链路不变，只在 RegularExecutor 的当前 step 中启用 GraphExecutor：

~~~text
用户输入
  -> AgentOS.handle_request
  -> RunStore.create_or_load
  -> TurnRunner.start_or_resume
  -> TurnSnapshot
  -> TurnContext
  -> MainAgent.route
  -> TaskFactory.select
  -> RegularTask
  -> RegularExecutor.loop
       （execution_mode=graph）
       （GraphExecutorConfig）
       （GraphSnapshot ref）
       （MainAgent.PlanRole）
       （DomainPack / RoleContract）
       （RegularExecutor -> GraphExecutor -> GraphRunnerAdapter -> GraphRunner）
       （Graph revision、compound expansion、pending patch、approval）
       （ready frontier、governed frontier、Node / ChildTurn）
       （ToolRegistry、ArtifactBoard、Gate evaluation）
       （Node follow-up、ChildTurn followup_task、Graph safe point）
       （StopHook / SubagentStop、Graph continuation request）
  -> TaskOutcome
  -> TurnRunner.finalize
       （FinalAnswerRole）
       （assistant_message Artifact）
       （final_artifact_ids）
       （TurnStop callback）
  -> ResponseEnvelope
  -> TurnComplete
~~~

GraphExecutor 只能替换 RegularExecutor 当前 step 的内部执行策略，不能替换 TurnRunner 或 RegularTask/RegularExecutor Turn loop 的：

- 身份和租户校验；
- Turn 状态迁移；
- Run worker lease；
- pause / resume / cancel / timeout；
- event_id 和 sequence；
- 最终 response 持久化；
- TurnStop / TurnAbort / TurnError 生命周期；
- 幂等和恢复入口。

### 31.6 Graph Turn 内部的 Follow-up 和 StopHook 顺序

Graph 内部不应在每个 Node 结束后直接结束原始 Turn。完整顺序是：

~~~text
RegularExecutor.loop
  -> GraphExecutor.step()
       -> GraphRunner 执行 Node（frontier 内可并发）
       -> 接收 Node / ChildTurn feedback
       -> 按 sequence 归并到同一 GraphSnapshot
  -> Node Tool / Role 完成
     （PostToolUse）
  -> 提交 Node Artifact
  -> 记录 ChildTurn 结果
     （ChildTurn followup_task）
  -> 更新 GraphSnapshot
  -> 到达 Graph safe point
  -> PlanRole 评估是否需要 patch
     （用户 follow-up）
     （Hook continuation）
     （Gate finding）
  -> 应用合法 Graph patch
  -> 计算下一轮 frontier
  -> 仍有 pending / ready Node
       -> GraphRunner 继续
  -> 没有 pending / ready Node
  -> Gate evaluation
  -> Graph completion candidate
  -> StopHook
     （Root Turn: Stop）
     （ChildTurn: SubagentStop）
     （block -> continuation request -> PlanRole / ReviewTask）
     （stop -> TaskOutcome）
  -> TurnRunner.finalize
~~~

关键点：

- Node 完成不是原始 Turn 完成；
- ChildTurn 完成不是 Parent Turn 完成；
- followup_task 只能向已有的非 Root ChildTurn 投递；
- Graph patch 只能在 safe point 应用；
- StopHook block 不能直接修改 Graph；
- Gate 未通过时进入 gated，不应伪装成 completed；
- 所有扩展都必须沿着同一个 TurnContext 传递身份和治理约束。

### 31.7 最终的配置投影模型

可以把整个设计压缩成以下模型：

~~~text
OriginalTurn
  -> input
  -> context
  -> task
  -> outcome
  -> response
  -> completion

TurnConfig
  -> task_type
  -> execution_mode
  -> graph_config
  -> hook_config
  -> follow_up_config
  -> child_turn_config
  -> approval_config
  -> plugin_refs

TaskState
  -> transcript / checkpoints
  -> pending input / mailbox
  -> follow-up counters
  -> stop hook state
  -> graph state reference（启用 Graph 时）

GraphSnapshot
  -> one graph per RegularTask/Turn
  -> graph revision
  -> nodes / frontier / in-flight positions
  -> pending subagent feedback
  -> artifact lineage
  -> gate evaluations
  -> graph patches
~~~

最终原则：

1. 原始 Turn 是唯一的用户意图生命周期。
2. Graph 是原始 Turn 的一种 Task 配置和执行策略，不是第二个根 Turn。
3. StopHook、SubagentStop、Follow-up、Mailbox、Approval 和 Plugin 都是当前 Turn 的可加载扩展。
4. 配置删除后，原始 Turn 仍可通过 RegularTask 完成。
5. Graph 的可变状态逻辑上属于 RegularTask 状态，可以物理上通过 GraphSnapshot 引用持久化，但不能把整个 Graph 塞进 TurnConfig。
6. 任何扩展都只能返回 TaskOutcome、ControlRequest、Artifact 或 continuation，不能绕过 TurnRunner 直接结束或重启 Turn。

### 31.8 可剥离的最终 Turn 链路

以下链路是本方案的权威版本。括号外只放原始 Turn；所有可加载配置和扩展都放在全角括号内。删除所有全角括号及其内容后，链路仍然保持完整：

~~~text
用户输入
  -> AgentOS.handle_request
     （RequestIntake、identity scope、idempotency、兼容 API）
  -> RunStore.create_or_load
     （worker lease、已有 response、已有 continuation）
  -> TurnRunner.start_or_resume
     （TurnConfig、config_version、config_hash、恢复策略）
  -> TurnSnapshot
     （task_type、task_config_ref、task_state_ref、graph_state_ref、plugin_refs）
  -> TurnContext
     （HookRuntime：SessionStart、UserPromptSubmit、PreToolUse、PermissionRequest、PostToolUse、PreCompact、PostCompact、Stop、SubagentStop）
     （FollowUpRuntime：model follow-up、pending input、user steer、Mailbox、CurrentTurn / NextTurn）
     （ToolRegistry、RoleRegistry、ArtifactBoard、ApprovalStore、AgentControl）
  -> MainAgent.route
     （Skill、Plugin、DomainPack 能力目录）
     （clarification policy、task selection reason）
  -> TaskFactory.select
     （RegularTask 的 core / task execution_mode）
     （RegularTask 的 graph execution_mode、GraphExecutorConfig、GraphSnapshot ref、GraphRunnerAdapter）
     （ReviewTask / CompactTask）
  -> RegularTask.start_or_resume
  -> RegularExecutor.loop
     （CoreLoopExecutor -> MainAgent / Provider -> ToolRegistry -> Tool result -> FollowUpRuntime -> Provider）
     （GraphExecutor -> MainAgent.PlanRole -> GraphRunner -> frontier -> 并发 Node / ChildTurn -> feedback -> GraphSnapshot -> ArtifactBoard -> Gate）
     （ChildTurn.followup_task -> existing non-root ChildTurn mailbox -> child follow-up Turn）
     （StopHook / SubagentStop -> block continuation 或允许结束）
  -> TaskOutcome
     （Graph status、Node status、Artifact IDs、Gate result、pending approvals、child Turn IDs、continuation）
  -> TurnRunner.finalize
     （TurnStop lifecycle callback、TurnAbort、TurnError）
     （FinalAnswerRole、assistant_message、最终 Artifact 校验）
     （response 幂等持久化、event replay、SSE、释放 worker lease）
  -> ResponseEnvelope
     （task_type、graph_id、graph_revision、artifact_ids、final_artifact_ids、continuation、pending approvals）
  -> TurnComplete
     （HookStarted / HookCompleted、followup events、Graph events、审计记录）
~~~

删除括号后的结果：

~~~text
用户输入
  -> AgentOS.handle_request
  -> RunStore.create_or_load
  -> TurnRunner.start_or_resume
  -> TurnSnapshot
  -> TurnContext
  -> MainAgent.route
  -> TaskFactory.select
  -> RegularTask.start_or_resume
  -> RegularExecutor.loop
  -> TaskOutcome
  -> TurnRunner.finalize
  -> ResponseEnvelope
  -> TurnComplete
~~~

这条可剥离链路表达最终设计边界：

- Graph 是 RegularTask 的一种配置化执行模式；
- StopHook 和 SubagentStop 是当前 Turn 的结束决策插件；
- FollowUpRuntime 和 followup_task 是当前 Turn 或 ChildTurn 的后续推进插件；
- Mailbox 是当前 Turn 的输入交付状态，不是另一个 Turn；
- GraphRunner 是 GraphExecutor 的内部调度器，不是第二个 Agent OS 入口；
- TurnRunner 是唯一拥有原始 Turn 生命周期和最终完成权的组件。
