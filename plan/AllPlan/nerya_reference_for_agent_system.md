# Nerya 多 Agent 系统借鉴参考

## 1. 文档目的

这份文档用于总结 [Nerya](https://github.com/NeryaAI/Nerya) 中哪些多 Agent 设计值得借鉴到当前 A 股研究系统中。

重点不是照搬它的交易执行和平台工程，而是提炼出适合当前项目的运行骨架，尤其服务于后续这些角色：

- Orchestrator
- Collector
- Distiller
- Retriever
- Reviewer
- Evolver
- Reporter
- Critic

## 2. 总体判断

Nerya 最有借鉴价值的，不是“做了很多 Agent”，而是把多 Agent 运行时做成了可审计、可回放、可演化的本地系统。

真正值得借的核心点有：

- 角色类型化
- Orchestrator 只调度，不亲自推理
- Blackboard + Mailbox 的协作结构
- 文件化 workspace
- Session history / replay
- Proposal + Approval + Rollback 的演化机制
- Gate 机制
- Typed memory

不建议当前项目照搬的部分：

- Trading kernel
- Exchange / wallet / signer / vault 体系
- daemon / SDK / dashboard / gateway 等重平台化能力

## 2.1 双层架构 vs 黑板架构

当前项目最需要先区分两类协作方式：

### A. 双层架构

这里的双层，指：

- 上层：`ResearchOrchestrator / TeamTemplate / TaskGraph / GateEvaluator`
- 下层：`collector / distiller / retriever / reviewer / evolver / reporter / critic`

核心特点：

- 调度关系清晰
- 任务路径明确
- 适合线性或半线性的固定流程
- 实现成本低

优点：

- 适合 MVP
- 职责边界明确
- 调试简单
- 容易做任务依赖和 gate

缺点：

- Agent 间共享中间事实的能力弱
- 一个 Agent 的输出容易被当成另一个 Agent 的“私有返回值”
- 一旦一份中间结果要被多个 Agent 消费，接口会迅速变复杂
- 不利于 replay 和异步演化

### B. 黑板架构

这里的黑板，指：

- 所有 Agent 将中间事实写入 append-only `Blackboard`
- 其他 Agent 从 Blackboard 读取共享证据
- 定向协调通过 `Mailbox`

核心特点：

- 共享事实优先
- 中间产物天然可追溯
- 适合多轮审查、复盘、演化

优点：

- 很适合 Distiller / Reviewer / Evolver 这种多消费者链路
- 天然支持 session replay
- 很适合 proposal 和 typed memory 沉淀
- 易于后续加 Critic 和异步复盘

缺点：

- 纯黑板方案会让简单流程过度工程化
- 如果没有上层调度，谁先做、谁后做会变模糊
- 对 entry schema、gate、run state 要求更高

## 2.2 当前项目的选择

当前项目不应在“双层架构”和“黑板架构”之间二选一，而应采用：

### `双层调度架构 + 黑板协作底座`

即：

- 用双层架构做控制面
- 用黑板做数据与协作面
- 用 mailbox 做定向反馈

为什么这样选：

1. 当前项目的主工作流仍然是强流程型
   - 个股体检
   - 行业研究
   - KOL 蒸馏与复盘
   这些都天然适合由上层 Orchestrator 显式编排

2. 当前项目的关键难点在中间产物复用
   - Distiller 结果会被 Reviewer、Reporter、Evolver 同时消费
   - 纯双层返回值链路会让接口迅速变得脆弱

3. 当前项目未来一定需要 replay / 审计 / proposal
   - 这些都更依赖黑板式事实沉淀，而不是函数返回值

因此，不采用：

- 纯双层直连方案
- 纯黑板无调度方案

而采用“上层明确调度、下层通过黑板共享事实”的融合式协作。

## 3. 代码层观察结论

## 3.1 TeamOrchestrator 的职责边界很清晰

从 `nerya/teams/orchestrator.py` 可见：

- Orchestrator 负责创建 run、落状态、调度任务、推进 phase、汇总结果、执行 gate。
- Orchestrator 自己不调用 LLM。
- 实际认知推理交给 subagent runtime。

这对当前项目极其重要。

建议借法：

- 当前项目未来的 `ResearchOrchestrator` 只负责：
  - 接收任务
  - 选 team template
  - materialize run
  - 调度各 agent
  - 汇总 blackboard
  - 执行 gate
  - 产出最终 context / report
- 不让 Orchestrator 自己承担 Distiller、Reviewer、Reporter 的认知工作。

好处：

- 避免 Orchestrator 变成“超大 prompt 代理”
- 各 Agent 更容易独立测试和替换
- 运行时结构更稳定

## 3.2 Blackboard 是共享事实层，不是聊天记录

从 `nerya/teams/blackboard.py` 可见：

- Blackboard 是 append-only
- 每条记录都有 kind、author、summary、payload、confidence、source_refs、task_id、created_at
- 适合存证据、主张、风险、冲突、记忆摘要

适合借鉴为当前系统的共享事实层。

建议当前项目 blackboard entry 统一结构：

- `entry_id`
- `run_id`
- `kind`
- `author`
- `summary`
- `payload`
- `confidence`
- `source_refs`
- `task_id`
- `created_at`

建议支持的 kind：

- `source_record`
- `logic_block`
- `distilled_dimension`
- `retrieval_evidence`
- `review_finding`
- `risk`
- `conflict`
- `decision_input`
- `memory`
- `report_fragment`

## 3.3 Mailbox 适合做定向消息，而不是共享输出

从 `nerya/teams/mailbox.py` 可见：

- mailbox 是 per-run、per-agent 的 FIFO 文件邮箱
- 适合在 agent 之间传递定向任务完成消息或审查意见

对当前项目的借法：

- Blackboard 存共享事实
- Mailbox 存定向消息

例如：

- Distiller -> Reviewer：某条 candidate_new 需要审查
- Reviewer -> Evolver：某维度连续失败，建议降权
- Retriever -> Reporter：相关证据已备齐
- Critic -> Distiller：当前蒸馏缺少证据锚点

第一版不一定要完整实现 mailbox，但架构上应预留。

## 3.4 Team Template 值得借

Nerya 把成员、任务、依赖、并发、gate 做成模板。

这特别适合当前项目，因为研究任务天然有模式差异。

建议后续至少定义 3 个 team template：

### `stock_check_team`

- collector
- retriever
- reporter
- critic

### `sector_research_team`

- collector
- retriever
- reporter
- critic

### `methodology_evolution_team`

- collector
- distiller
- reviewer
- evolver
- reporter

这样 Distiller 在整体系统中就不会漂移成“大杂烩模块”。

## 4. 运行时设计借鉴

## 4.1 文件化 workspace 非常适合当前阶段

Nerya 的运行时状态主要放在 workspace 文件中，而不是一开始就上重数据库。

这个思路非常适合当前项目当前阶段。

建议当前项目工作区结构逐步演进为：

```text
workspace/
  runs/
  blackboard/
  mailbox/
  memory/
  dimensions/
  proposals/
  reports/
  artifacts/
```

推荐职责：

- `runs/`
  - 每次研究任务、蒸馏任务、复盘任务的 session
- `blackboard/`
  - 共享中间产物
- `mailbox/`
  - 定向消息
- `memory/`
  - typed memory
- `dimensions/`
  - 标准维度池与候选维度池
- `proposals/`
  - 演化 patch 候选
- `reports/`
  - 面向最终用户或研究者的输出
- `artifacts/`
  - 原始记录和中间处理材料

好处：

- 可 diff
- 可 grep
- 可重放
- 可回滚
- 不把系统过早绑定在重 infra 上

## 4.2 Session history / replay 必须借

Nerya 的 `strategy_history` 模块很值得借。

当前项目推荐设计这些 session：

- `distillation_session`
- `research_session`
- `review_session`
- `evolution_session`

每个 session 至少记录：

- 输入对象
- agent 调用顺序
- blackboard 快照
- 中间产物
- gate 结果
- 最终输出
- 失败原因

这会决定系统后续是否可解释、可调优、可审计。

## 5. 演化机制借鉴

## 5.1 不能让 Agent 直接修改正式规则

Nerya 的 `evolution/promotion.py` 非常值得借。

重点思想：

- Agent 可以生成 proposal
- proposal 经过 gate / validation / operator 审批
- 只有审批通过后才 apply
- apply 时保留 before/after 快照

当前项目建议支持的 proposal 类型：

- `dimension_pool_patch`
- `prompt_patch`
- `retrieval_weight_patch`
- `report_template_patch`
- `memory_learning_update`
- `agent_skill_proposal`

这对当前项目特别重要，因为以后：

- Distiller 会发现新维度
- Reviewer 会发现失效逻辑
- Evolver 会建议调整维度权重和 prompt

这些都不该直接写入生产规则。

## 5.2 Gate 机制必须前置

Nerya 的 action gate / validation gate / protected scope 思路很值得借。

当前研究系统建议至少定义：

### Evidence Gate

- 没有原文证据锚点的逻辑块不能进入 Reporter

### Quality Gate

- 低置信度蒸馏结果不能进入正式维度池

### Review Gate

- `candidate_new` 维度不能直接升为 active

### Evolution Gate

- Prompt、维度池、检索权重等修改只能通过 proposal apply

### Protected Scope

- Agent 不允许直接改：
  - active 维度池
  - 正式风险规则
  - 原始抓取结果
  - 已发布研究报告

## 6. Typed Memory 的借鉴

Nerya 的 memory 不是泛泛“向量记忆”，而是分面 typed memory。

这非常适合当前项目。

建议当前项目 memory 分层：

```text
memory/
  global.md
  mistakes.md
  market_regimes.md
  skill_learnings.md
  strategy_learnings/
  distiller_learnings.md
  reviewer_learnings.md
```

建议含义：

- `global.md`
  - 系统级研究原则
- `mistakes.md`
  - 已知错误模式
- `market_regimes.md`
  - 不同市场环境下哪些维度更有效
- `skill_learnings.md`
  - 通用工程和流程经验
- `strategy_learnings/`
  - 个股/行业/主题专项积累
- `distiller_learnings.md`
  - 蒸馏误判经验
- `reviewer_learnings.md`
  - 复盘与验证的共性教训

## 7. 对当前项目的具体借鉴建议

## 7.1 当前阶段最值得优先引入的骨架

优先级建议：

1. Distiller 输出稳定结构化结果
2. Session history
3. Blackboard 标准记录
4. Candidate dimension proposal
5. Reviewer 质量审查
6. Evolver 变更提案
7. ResearchOrchestrator

## 7.1.1 协作方案落地方式

建议当前项目的协作方案按下面拆：

### 控制面

- `ResearchOrchestrator`
- `TeamTemplate`
- `TaskGraph`
- `GateEvaluator`
- `RunStore`

职责：

- 决定要调用哪些 Agent
- 决定顺序、依赖、是否并发
- 决定何时进入 review / report / evolve

### 数据与协作面

- `Blackboard`
- `Mailbox`
- `SessionHistory`
- `ProposalStore`
- `TypedMemory`

职责：

- 沉淀中间事实
- 给多个 Agent 共享同一份证据
- 支持 replay、复盘、演化

### Agent 执行面

- `collector`
- `distiller`
- `retriever`
- `reviewer`
- `evolver`
- `reporter`
- `critic`

## 7.1.2 对话式入口的借法

Nerya 的 README 本身展示的是一种“对话驱动、内部结构化执行”的使用方式，这一点也值得借鉴。

适合当前项目的借法不是做纯聊天系统，而是：

- 外部采用对话式入口
- 内部仍然落成结构化任务
- 由 `ResearchOrchestrator` 负责把对话转成可执行 schema

建议入口意图优先支持：

- `stock_check`
- `sector_research`
- `distill_kol_logic`
- `review_thesis`
- `methodology_evolution`

不建议让 Distiller、Reviewer、Retriever 直接消费原始对话消息。

职责：

- 消费输入上下文和 blackboard
- 产出结构化结果
- 不直接修改 protected scope

## 7.2 当前阶段不建议过早引入的部分

暂不优先：

- daemon
- dashboard
- 双 SDK
- 消息 gateway
- 过于复杂的长期服务编排

原因：

- 当前项目的核心瓶颈不在平台层，而在研究链路是否跑通
- 过早平台化会稀释 Distiller / Reviewer / Evolver 主线

## 8. 对 Distiller Agent 的直接启发

这份借鉴最应该直接作用到 Distiller 上。

### 8.1 Distiller 必须输出中间层

不能只输出最终结果。

至少要保留：

- `normalized_text`
- `quoted_context`
- `author_additions`
- `logic_blocks`
- `candidate_dimensions`
- `confidence_notes`

原因：

- Reviewer 需要审查
- Evolver 需要提案
- Session replay 需要回放

### 8.2 Distiller 不能直接写 active dimension pool

Distiller 只能：

- 识别 `known dimension`
- 产出 `candidate_new`
- 生成 `dimension_pool_patch_input`

最终是否晋升必须交给 Reviewer / Evolver / Operator。

### 8.3 Distiller 错误要能沉淀进 memory

重点记录这类错误：

- 把引用内容误当作者观点
- 把情绪表达误判成有效因子
- 标的归属错误
- 没有时间范围却输出强结论
- 因果链断裂

## 9. 推荐的最小整体骨架

当前项目建议先朝下面的最小骨架演进：

### 9.1 运行骨架

- `ResearchOrchestrator`
- `TeamTemplate`
- `TaskGraph`
- `Blackboard`
- `Mailbox`
- `RunStore`
- `GateEvaluator`
- `ProposalStore`
- `SessionHistory`

### 9.2 Agent 角色

- `collector`
- `distiller`
- `retriever`
- `reviewer`
- `evolver`
- `reporter`
- `critic`

### 9.3 演化主链

`session close -> review findings -> memory write -> proposal draft -> operator approve -> apply patch`

### 9.4 协作主链

`ResearchOrchestrator 决定任务 -> Agent 执行 -> 结果写入 Blackboard -> 下游 Agent 读取 Blackboard -> GateEvaluator 判断 -> Reporter 汇总 / Evolver 提案`

## 10. 结论

Nerya 对当前项目最值得借鉴的不是“交易系统本身”，而是它把多 Agent 系统的基础件做成了可运行、可审计、可演进的骨架：

- 类型化角色
- 强边界 Orchestrator
- blackboard / mailbox
- 文件化 workspace
- typed memory
- session history / replay
- proposal + gate + rollback

对当前项目最优借法是：

- 先把这些骨架思想内化到研究系统
- 在架构选择上采用“`双层调度架构 + 黑板协作底座`”
- 让 Distiller 从第一版开始就输出可接入 Blackboard、Memory、Proposal、Review 流程的结构
- 暂时不照搬 Nerya 的交易和平台工程部分
