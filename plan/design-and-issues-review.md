# 《目前设计和问题.md》问题核验与修改建议

## 结论摘要

原文的大方向成立，但有三类表述需要收紧：

1. `unsafe frontier` 的吞吐问题已被代码和最小探针直接证实，属于当前最明确的调度缺陷。
2. artifact 问题是“生产者绑定契约不完整”带来的数据正确性风险，目前不能写成已经稳定发生的串错。
3. 动态 runtime role 已完成发现、审批、持久化和 materialization 的独立链路，但没有证据表明它已经接入 canonical Agent OS GraphRunner；“动态 role 不能提升吞吐”应改成“当前主图链路尚未完成端到端接入，因此尚无稳定吞吐收益证据”。

本次审查基于分支 `plan/design-and-issues` 的当前代码和 `D:\Cognitrade\目前设计和问题.md`。没有修改任何代码，也没有纳入工作区原有的 `frontend/vite.config.ts` 变更。

## 可信度分级

| 原文判断 | 结论 | 说明 |
| --- | --- | --- |
| `depends_on` 支持多依赖并校验环 | 已确认 | `ExecutionGraph` 对依赖边和层级边分别做环校验。 |
| ready frontier 可以并行执行 | 已确认 | `GraphRunner._execute_frontier()` 对选中的多个节点使用 `ThreadPoolExecutor`。 |
| 一个 unsafe 节点会阻塞整个 frontier | 部分成立，问题真实 | 它不会让其他节点永久阻塞，但 `_governed_frontier()` 会让本轮只选择第一个 unsafe 节点，其他 ready 节点被延后。 |
| artifact 可能取到错误的最新版本 | 部分成立，属于风险 | runner 支持 `producer_node_id`，但 planner 没有把规划出的 producer 自动写入 selector；无显式绑定时按 artifact 类型筛选并取最后一个候选。 |
| 每个 frontier 后都会重新规划 | 部分成立 | 主循环每轮都会调用 `_evaluate_frontier()`；若 planner 返回 `None`，不会产生 patch，但仍可能有规划和 Board 扫描开销。 |
| 多 Run 缺少全局调度、公平性和 backpressure | 基本成立，需限定范围 | `AgentOSRunService` 有后台 run 线程池，但没有看到跨 Run 的租户公平、资源配额或 backpressure 调度。 |
| 动态 role 没有接入 canonical Agent OS Graph | 高可信，尚缺端到端测试 | `AgentOS` 创建 GraphRunner 时没有传入 discovery store 或 alias overlay；现有 runtime materializer 返回的是 legacy `run(**kwargs)` 对象，而 GraphRunner 通过 registry 解析 `ExecutionNodeContext` executor。 |
| 比 Codex parent-child topology 更接近真正 DAG | 暂无法验证 | 这是外部系统比较，当前仓库没有 benchmark、统一任务样本或对方实现证据。应改为假设或单独的对比实验结论。 |

## 逐项核验

### 1. DAG 结论需要加边界

原文称“是真 DAG”。更准确的说法是：当前 `depends_on` 依赖图是经过环校验的 DAG，`parent_node_id` 又是一套独立的层级关系；lazy compound 展开和 graph patch 会让图随 revision 演进。因此它是“每个 revision 的受治理 DAG / 动态演进 DAG”，不是一次生成后永远固定的静态 DAG。

证据：

- `research_system/schemas/execution_graph.py:131` 附近分别校验 dependency 和 hierarchy 的 acyclic 约束。
- `research_system/runtime/execution/core/graph_runner.py:574-680` 在运行过程中展开 compound、应用 patch，再计算 frontier。

建议把原文第 1 点改为：

> `depends_on` 构成受校验的依赖 DAG，`parent_node_id` 单独表达层级；lazy compound 和 graph patch 允许 DAG 按 revision 动态演进。

### 2. `unsafe frontier` 是已确认的调度缺陷

代码如下：

```python
unsafe = [node for node in ready if not node.parallel_safe]
if unsafe:
    return [unsafe[0]]
```

位置：`research_system/runtime/execution/core/graph_runner.py:2746-2766`。

最小探针构造了同一 ready frontier 中的 `unsafe` 和无依赖关系的 `safe` 节点，实际输出为：

```text
['unsafe']
```

所以原文第 2 个缺点真实存在，但建议把“阻塞整个 frontier”改成：

> 当 ready 集合包含任意 `parallel_safe=false` 节点时，本轮 frontier 退化为只执行第一个 unsafe 节点；其他 ready 节点不会被标记为 blocked，但会被延后。

修改建议：

- 最小修复可以先保证调度结果确定、无关节点不因 ready 列表中的 unsafe 节点全部退化为串行。
- 更稳妥的长期方案是为 capability 声明资源冲突域或锁键。`parallel_safe=false` 作为没有细粒度声明时的全局互斥兜底；有不同锁键的节点可以并行，同锁键节点串行。
- 不建议直接把所有 safe 节点与 unsafe 节点无条件并行，因为 `parallel_safe=false` 可能代表外部副作用、共享资源或非幂等约束。
- 增加回归测试：一个长耗时 unsafe 节点和一个无关 safe 节点同时 ready，验证 safe 节点不会被不必要地延迟到 unsafe 完成后才开始。

优先级：P0（如果目标是吞吐和多节点并行）；数据结构和锁域方案可分两步实施。

### 3. artifact 依赖是契约歧义，不是已证实的结果错误

planner 会根据 `consumes` 推导 `depends_on`：

- `research_system/runtime/planning/capability_planner.py:273-323` 通过 `resolve_artifact()` 找到 producer，并将 producer node 加入依赖。
- `research_system/runtime/planning/capability_planner.py:876-915` 只根据已有 `available_artifact_refs` 选择初始 `input_artifact_ids`。

runner 侧已经支持 producer selector：

- `research_system/runtime/execution/core/graph_runner.py:1429-1455` 支持 `producer_node_id`。
- 如果没有具体 `input_artifact_ids`，runner 会按 run、artifact type 和 selector 列出 active artifacts；对于非 `multiple` port，`research_system/runtime/execution/core/graph_runner.py:1460` 取 `candidates[-1:]`。

因此问题的准确描述是：

> `depends_on`、`consumes` 和 `input_artifact_ids` 之间存在重复表达；planner 会建立执行先后关系，但没有始终把消费端口绑定到目标 producer。多个节点、seed artifact、动态 patch 或同类型重试结果同时存在时，按类型取最后一个候选存在拿错来源的风险。

当前不能直接断言“生产环境已经拿到了错误 producer”，因为 planner 通常会为一个 artifact type 选择一个 producer，且已有测试覆盖正常 artifact lineage。需要补充专门的多 producer 回归测试后再定量描述。

修改建议：

- 小改方案：planner 在生成消费端口时，将 `resolve_artifact()` 得到的 producer node 写入 `selector["producer_node_id"]`，并在 graph validation 阶段检查 selector 对应节点确实是 `depends_on` 中的 producer。
- 完整方案：引入显式 artifact edge / producer binding，令 artifact edge 成为来源真相；`depends_on` 可以由 artifact edge 派生，减少三套字段各自维护。
- 对 retry、cache hit、rerun 和 invalidation 明确版本选择规则，避免“最后写入”成为隐含业务语义。
- 增加两个 producer 产出同一 `artifact_type`、consumer 只依赖其中一个的测试，并断言 `input_artifact_ids` 和 `producer.node_id` 一致。

优先级：P0/P1 之间。若该类 artifact 会影响最终投资或合规结论，按数据正确性问题列 P0；否则先按 P1 做契约收紧。

### 4. frontier 后的 planner 评估确实存在，但“每轮重规划”表述过强

主循环在每个 frontier 完成后无条件调用：

- `research_system/runtime/execution/core/graph_runner.py:680` 调用 `_evaluate_frontier()`。
- `research_system/runtime/execution/core/graph_runner.py:2470-2490` 调用 `decide_frontier()` 或 `evaluate_frontier()`；返回 `None` 时不应用 patch。
- `CapabilityDynamicPlanner.evaluate_frontier()` 在 `research_system/runtime/planning/capability_planner.py:1017`，没有需要 patch 的情况会返回 `None`，但仍可能做 request 校验和 Board/artifact 检查。

另外，当前 `CapabilityPlanner` 生成的 graph strategy 在 `research_system/runtime/planning/capability_planner.py:142` 和 `:493` 都是 `dynamic`。因此原文指出控制面开销是合理的，但“每轮都重新规划”应改成“每轮都进入 frontier evaluation 路径，只有部分情况会产生 replan patch”。

修改建议：

- 对 `graph.strategy == "static"` 或 template graph 跳过 frontier evaluation。
- 对 dynamic graph 只在失败、control request、数据缺口、超时或显式 replan 信号出现时评估。
- 将 planner evaluation 的耗时、调用次数、产生 patch 的比例记录下来，先用数据决定是否需要更复杂的缓存或批量评估。

优先级：P1。它是控制面优化，不应先于数据来源绑定和 executor 协议统一。

### 5. 全局调度问题成立，但要区分后台 admission 和图内执行

当前存在两个不同层次的线程池：

- `GraphRunner` 在 `research_system/runtime/execution/core/graph_runner.py:1201-1205` 为选中的 frontier 创建图内线程池。
- `AgentOSRunService` 在 `research_system/api/agent_os_run_service.py:25-30` 有后台 run 线程池，其作用主要是接纳和恢复 background run。

现有代码没有显示出一个跨 Run 统一控制 capability 资源、租户公平、全局 token/cost 配额或 backpressure 的 scheduler。因此原文第 5 点基本成立，但建议改为：

> 当前有后台 Run 线程池和单图 frontier 并发限制，但尚未看到跨 Run 的资源调度、公平性、租户配额和 backpressure 实现。

修改建议：

- 先补充运行指标：queued time、running time、每个 tenant 的 active runs、节点等待原因、线程池占用和外部数据源限流次数。
- 只有在多 Run 压测证明存在资源争用后，再引入共享 scheduler；其最小职责应是 admission、租户并发上限和资源 semaphore，而不是一次引入完整工作流引擎。

优先级：P1/P2，取决于实际并发规模。

### 6. 动态 runtime role 的主图接入缺口可信度高

`AgentOS._runner()` 当前传给 `ExecutionGraphRunner` 的主要依赖是：

- `DomainPackRegistry`
- `CapabilityDynamicPlanner`
- memory、tool registry、`PackSkillRuntime`

位置：`research_system/runtime/agent_os.py:1138-1156`。这里没有传入 `SubagentDiscoveryStore`，也没有显式创建 activated alias overlay。

全仓库搜索显示，`activated_alias_entries()` 的实际调用只出现在 store 自身和相关测试中，没有发现它进入 `CapabilityPlanner` 或 `DomainPackRegistry` 的生产接线。另一方面：

- `research_system/runtime/orchestration/subagent_runtime_materialization.py:59-74` 返回 runtime registered subagent。
- `research_system/subagents/runtime_registered.py` 中的实现协议是 `run(**kwargs)`。
- `GraphRunner` 在 `research_system/runtime/execution/core/graph_runner.py:1273` 使用 `registry.resolve_executor()` 并传入 `ExecutionNodeContext`。

因此“发现、审批、materialize”和“能被 canonical graph 执行”之间确实缺少可证明的桥接。现有 `tests/test_subagent_runtime_materialization.py` 和 `tests/test_subagent_discovery_store.py` 只验证前半段，相关测试本轮共通过，但没有覆盖 end-to-end graph execution。

修改建议：

1. 增加一个明确的 runtime role catalog/registry overlay，把 active alias 投影成 GraphRunner 能解析的 capability definition。
2. 增加 `SubagentExecutorAdapter`，统一完成 `ExecutionNodeContext -> legacy run(**kwargs) -> NodeExecutionOutput` 的输入、输出、错误、超时和 artifact 映射。
3. 显式定义两套 policy 的映射：`allow_parallel`、`max_concurrency`、`max_retries`、`timeout_seconds`、side effect 和权限不能一部分来自 `SubagentRuntimePolicy`、一部分来自 `CapabilityDefinition` 而无优先级规则。
4. 加一条集成测试：创建并激活 runtime alias，刷新 overlay，planner 选中 alias，GraphRunner 实际执行并把返回值转为合法 artifact。

优先级：P0（如果动态 role 是产品要求的可执行能力）；否则应在产品文档中明确它当前只是治理/候选能力，避免被误当作可用 executor。

### 7. 规则式 planner 和“提效”结论需要指标支撑

planner 的候选排序有偏好、fallback、成本、延迟、可靠性等规则，但当前核验没有发现 critical path、全局成本最优、跨 Run 资源均衡或动态负载预测。因此原文第“规则式规划”是合理的架构描述，不一定是 bug。

“具备较强提效潜力”“已经可以减少等待时间和重复计算”目前属于设计推断。要升级为事实，需要至少有同一任务、同一数据和同一并发限制下的对照数据：

- 顺序执行 vs frontier 并行的 wall-clock、节点等待时间和吞吐。
- cache hit、重复执行、retry、fallback、artifact invalidation 的比例。
- planner evaluation 的耗时和 patch 命中率。
- 多 Run 下的 p50/p95 延迟、租户公平和外部数据源限流。

建议把“能提效”改成“存在可验证的提效路径”，在完成基准测试后再写收益数字。

### 8. 原文需要修正的引用和措辞

- 原文第 173 行的路径重复了 `research_system`：实际应为 `D:\Cognitrade\Cognitrade\research_system\runtime\core\agent_control.py:852`。
- “unsafe 节点阻塞整个 frontier”应改成“本轮 frontier 被收缩为一个节点，其他 ready 节点被延后”。
- “动态 role 暂时主要提升治理，不能直接提升吞吐”应改成“当前尚无动态 role 进入 canonical GraphRunner 的端到端证据，因此不能把它的吞吐收益当作已实现能力”。
- 与 Codex 的比较应标注为“待基准验证”，不要作为当前仓库的事实结论。
- “恢复和局部重跑设计比较完整”建议拆成已覆盖能力和待验证能力；完整性需要恢复、重放、失效、跨进程 worker 崩溃等集成测试，而不能只依据函数存在。

## 建议的修改优先级

### P0：先保证执行正确性和可执行性

- 为 unsafe frontier 增加资源冲突/互斥语义，并修复无关 ready 节点被整体延后的调度行为。
- 明确 artifact producer binding；至少由 planner 自动写入 `producer_node_id`，并做 graph validation。
- 如果动态 role 是当前产品范围，打通 alias overlay、GraphRunner executor adapter 和一条端到端测试；如果不是，暂时从主图可执行能力宣称中移除。

### P1：减少控制面开销并统一契约

- static/template graph 跳过无必要的 frontier evaluation；dynamic graph 只在事件触发时评估。
- 用共享 `RoleContract` 或清晰的 projection 统一 `SubagentDefinition` 与 `CapabilityDefinition`，先统一字段语义和优先级，不必立即删除现有类型。
- 增加运行指标和多 Run 压测；有数据后再决定是否建设全局 scheduler。

### P2：在基础正确性稳定后再扩展

- critical-path 优化、资源感知调度、成本/延迟预测。
- 条件边、map/reduce、循环和复杂分支。
- 通用工作流语言或大规模 role 自动组合。

当前任务以 artifact DAG 为主，先把来源、调度和执行协议做成可测试的闭环，再引入这些复杂控制结构。

## 建议补充的测试与观测

### 必要回归测试

- `unsafe + unrelated safe`：验证安全节点不会因无关 unsafe 节点而不必要地等待。
- 两个 producer 产出同一 artifact type：验证 consumer 只拿到声明的 producer。
- seed artifact、retry、cache hit、rerun_from、invalidation 的来源选择一致性。
- activated runtime alias 从 discovery store 到 planner、registry、GraphRunner 的端到端执行。
- static graph 不调用 frontier planner；dynamic graph 在 control request/缺失 artifact 时才产生 patch。
- 多 Run 下租户并发限制、队列公平性和外部 source rate limit。

### 必要观测指标

- `ready_count`、`selected_count`、frontier wait duration、因 unsafe/resource lock 延迟的节点数。
- planner evaluation count、evaluation duration、patch count、patch acceptance rate。
- 每个 artifact 的 declared producer、actual producer、selection reason 和 lineage mismatch。
- 每个 role 的 cache hit、retry、fallback、timeout、token/cost、成功率。
- 每个 tenant 的 active run、queued run、p50/p95 latency 和 backpressure 次数。

## 最终建议

这份文档可以保留“Graph Task 方向正确”的总体判断，但应把它从结论性宣传稿改成带证据等级的设计评审。最先处理的不是增加更多 role，而是三条闭环：

```text
ready frontier -> 资源感知调度 -> 可观测的并行结果
artifact edge  -> producer binding -> 可验证的 lineage
runtime alias  -> executor adapter -> GraphRunner 可执行
```

在这三条闭环有回归测试和基准数据之前，不建议把“比 Codex 更接近 DAG”或“已经显著提效”写成确定性结论。

## 本次验证记录

最小调度探针：构造一个 `parallel_safe=false` 和一个无关 `parallel_safe=true` 的 ready frontier，调用 `_governed_frontier()`，输出为 `['unsafe']`。

相关测试命令：

```powershell
$env:PYTHONPATH='.'
pytest -q tests/test_execution_graph.py tests/test_execution_graph_runner.py tests/test_subagent_runtime_materialization.py tests/test_subagent_discovery_store.py
```

结果：`46 passed in 6.83s`。测试通过不代表动态 role 已接入 GraphRunner；目前恰恰缺少覆盖该接线的集成测试。
