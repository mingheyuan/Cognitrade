# A股研究系统 Phase Plan

> 状态说明（2026-07-01）
>
> 这份文档主要保留“当时的阶段拆解思路”，不是当前实现状态的唯一真源。
> 目前代码已经明显超过这里的早期首期边界：`review_thesis`、`methodology_evolution`、thread memory、methodology memory、CLI 的 `thread/run/proposal/review` 控制面都已经落地。
> 如果要判断“现在还差什么才能收尾”，优先看 `completion_matrix.md`、`runtime_schema_contract.md`、`agent_system_implementation_plan.md`。

## 1. 文档目标

这份文档用于把当前主方案拆成可执行的分阶段路线，优先解决以下问题：

- MVP 范围过宽
- 用户意图与系统内部任务混杂
- Gate 规则过于抽象
- Reviewer 的验证对象尚未形式化
- Qdrant / Verified Snapshot / Memory / ProposalStore 的边界还不够硬
- 方法论演化闭环过早进入首期，增加系统复杂度

这份文档不替代：

- 逻辑主方案：[plan.md](./plan.md)
- 实现与框架方案：[agent_system_implementation_plan.md](./agent_system_implementation_plan.md)
- Distiller 专项方案：[distiller_agent_plan.md](./distiller_agent_plan.md)

它的职责是把“先做什么，后做什么，什么必须延后”固定下来。

---

## 2. 分阶段的基本原则

系统继续坚持以下原则：

- 外部入口采用对话式交互
- 内部执行采用结构化任务
- exact claim 优先由 `Verified Snapshot` 约束
- 检索层、事实层、长期记忆层分离
- 方法论更新必须经过 proposal，而不是直接写入正式规则
- 运行过程必须可回放、可审计、可降级
- Skill 能力优先本地接入，不把 MCP 作为首期前置依赖

但首期不追求一次性把全部原则完整落地，而是按阶段逐步接入。

### 2.1 MCP 定位

当前方案对 MCP 的定位是：

**Local Skill First, MCP Optional Backend**

这意味着：

- 首期先完成本地 Skill Runtime 与本地 Skill Registry
- MCP 不作为 Phase 0 到 Phase 4 的硬前提
- 后续如果出现跨进程、跨语言、远程服务或第三方能力接入需求，再补 MCP backend

当前不把 MCP 设为前置依赖的原因：

- 当前系统的关键难点在 Prompt、Schema、Blackboard 和状态机，而不在跨进程工具协议
- 过早引入 MCP 会增加调试、trace、timeout、replay 和 fallback 的复杂度
- 先把 Skill 抽象稳定下来，再接 MCP，迁移成本更低

---

## 3. 当前方案的收缩与修正

### 3.1 首期范围收缩

首期不再同时交付以下完整链路：

- 个股深度体检
- 行业 / 主题研究
- KOL 蒸馏
- 后验验证
- 方法论更新闭环

首期改为只交付：

- `stock_check`
- `sector_research`
- 最小运行时骨架
- 最小证据约束
- 最小可审计能力

### 3.2 用户意图与内部任务拆分

用户意图只保留：

- `stock_check`
- `sector_research`
- `distill_kol_logic`

内部任务单列：

- `clarify`
- `review_thesis`
- `methodology_evolution`

其中：

- `clarify` 用于 Orchestrator 无法稳定识别意图或关键信息缺失时触发追问工作流
- `review_thesis` 与 `methodology_evolution` 在早期阶段只作为系统内部链路存在，不作为默认用户入口

### 3.3 Gate 最小规则固定

第一版固定以下 gate 语义：

- `VerifiedFactGate`
  - 输出中出现精确价格、财务、公告口径时，必须绑定对应 snapshot
- `EvidenceGate`
  - 结论必须有至少一条证据支撑
- `QualityGate`
  - 正式输出必须同时包含结论、证据、风险
- `ReviewGate`
  - 仅在后验验证阶段启用

### 3.4 Reviewer 验证对象收缩

Reviewer 第一版只复核可验证主张，不复核开放式叙事本身。

首批只支持三类主张：

- 方向判断
- 催化判断
- 时间窗口判断

每条主张至少要显式包含：

- 对象
- 方向
- 观察窗口
- 验证口径

其中首期观察窗口至少要能表达：

- `window_type`：`trading_days` 或 `calendar_days`
- `window_size`
- `anchor_date`
- `evaluation_start_rule`

对于价格表现类判断，Reviewer 第一版必须优先采用相对基准归因，而不是绝对涨跌归因。

最小规则：

- 个股优先对比所属板块或主题基准
- 板块或主题优先对比宽基指数
- 没有合适基准时，只允许输出“无法可靠归因”，不允许直接判定维度有效

### 3.5 存储边界固定

- `Qdrant`：存结构化证据与逻辑块
- `Verified Snapshot`：存确定性事实快照
- `ProposalStore`：存待审核的方法论候选
- `Mem0`：作为长期认知层 backend，只存经验证或经审核的长期知识

对 `Mem0` 的首期约束固定为：

- 不直接写入原始材料或未审核候选
- 只允许写入具备来源引用和审核依据的长期知识
- 只有 approved proposal 或等价审核动作后，才允许进入 Mem0
- 读取 `Mem0` 时必须通过统一 memory service，按受控 `memory_type` 和方法论上下文过滤

候选提案不能直接进入长期 memory。

### 3.6 Distiller 质控与长文本经济性

Distiller 是系统质量上限的核心瓶颈，不能只依赖单次 LLM 蒸馏结果直接进入后验统计。

第一版固定以下约束：

- Distiller 输出必须带置信度与风险标记
- 低置信度、因果链不完整或引用归属不清的结果，必须进入 `needs_review`
- Phase 2 必须支持 Critic 或 Reviewer 的抽检入口
- MVP 阶段应预留人工微调或人工确认接口，允许用户修正逻辑块

长文本经济性方面，Phase 2 默认加入轻量预筛节点。

最小规则：

- 在主 Distiller 前增加轻量 `Trimmer` 或同等预筛步骤
- 预筛优先保留数字、因果关系词、专有名词和显式主张句
- 对低信息密度内容优先降级，不强制全量送入主蒸馏链路

---

## 4. 阶段总览

| Phase | 目标 | 对外能力 | 关键限制 |
| --- | --- | --- | --- |
| Phase 0 | 协议与 runtime 骨架 | 无正式用户能力 | 只定对象、状态流、归档结构 |
| Phase 1 | 在线研究最小闭环 | `stock_check`、`sector_research` | 不做 KOL 蒸馏和后验验证 |
| Phase 2 | KOL 蒸馏与结构化存证 | `distill_kol_logic` | 不做自动复核与自动演化 |
| Phase 3 | 后验验证最小闭环 | 内部 `review_thesis` | 只复核可验证主张 |
| Phase 4 | Proposal 与长期认知沉淀 | 内部 `methodology_evolution` | 不做全自动正式规则更新 |

---

## 5. Phase 0：协议与 runtime 骨架

### 5.1 目标

先把系统的最小运行时对象和状态流定住，避免后续 Agent 各自定义字段和协作方式。

### 5.2 包含内容

- 请求 / 响应外壳
- `RunEvent`
- `BlackboardEntry`
- `MailboxMessage`
- `RunStateSnapshot`
- `GateVerdict`
- workspace 目录结构
- `SessionHistory` 最小归档语义
- `clarify` 追问工作流语义
- run 级 timeout / fail-fast 约束
- 本地 Skill Runtime 语义

### 5.3 不包含内容

- 正式研究报告能力
- KOL 蒸馏
- Reviewer
- Evolver

### 5.4 阶段完成标志

- 运行对象命名与字段固定
- 单次 run 的事件、黑板、结果归档路径固定
- 首期 run 清理与保留策略固定
- Gate 类型固定为规则型，不依赖自由裁量 prompt
- 超时、失败、追问的状态流语义固定

---

## 6. Phase 1：在线研究最小闭环

### 6.1 目标

先跑通“用户提问 -> 结构化任务 -> 证据组织 -> 输出报告 -> run 归档”的主链路。

### 6.2 包含内容

- 对话入口转结构化请求
- `ResearchOrchestrator` 最小调度
- `clarify` 追问工作流
- `stock_check`
- `sector_research`
- `Blackboard`
- `SessionHistory`
- `RunEvent`
- `Verified Snapshot` 最小接入
- `Snapshot Skill + Vendor Adapter + Unified Schema` 最小接入
- `Retriever`
- `Reporter`
- `GateEvaluator` 最小版
- 运行时 timeout / 熔断 / 降级返回
- `ResponseEnvelope.progress` 最小结构
- 外部依赖降级矩阵
- `Qdrant` 单 collection 与 metadata filter 最小合同

### 6.3 不包含内容

- KOL 蒸馏主链
- 后验验证
- 自动方法论演化
- Proposal 审核流
- Memory 写回闭环
- MCP 强依赖接入

### 6.4 阶段完成标志

- 能稳定接收个股和行业研究请求
- 输出包含结论、证据、风险
- exact claim 有 snapshot 约束
- Agent 通过统一 snapshot skill 获取精确事实，而不是直连 vendor
- 单次 run 可回放
- 上游数据或模型调用超时后，系统能中断并返回明确失败态，而不是卡死

---

## 7. Phase 2：KOL 蒸馏与结构化存证

### 7.1 目标

让 KOL 文本蒸馏成为系统正式输入，而不是临时脚本产物。

### 7.2 包含内容

- `distill_kol_logic`
- 轻量 `Trimmer` 或同等预筛步骤
- `Collector -> Distiller` 链路
- `LogicBlock`
- `DistilledDimension`
- 原文证据锚点
- 蒸馏结果写入 `Blackboard`
- 蒸馏结果写入 Qdrant 检索层
- Critic / Reviewer 抽检入口
- 人工修正预留接口

### 7.3 不包含内容

- 自动后验验证
- 自动方法论更新
- 复杂新维度准入机制
- 把 Distillation Skill 强制改造成 MCP 服务

### 7.4 阶段完成标志

- 能从现有 KOL 数据源稳定蒸馏结构化结果
- 能区分主张、证据、风险、催化
- 每条蒸馏结果可追溯到原文
- Reporter 或 Retriever 可以直接消费蒸馏产物
- 长文本处理具备预筛和降级路径
- 低置信度结果不会静默进入后验统计

---

## 8. Phase 3：后验验证最小闭环

### 8.1 目标

把“历史主张 -> 观察窗口 -> 验证结果”正式接入系统，但只做最小版。

### 8.2 包含内容

- `review_thesis` 作为内部任务
- Reviewer 消费可验证主张
- `ReviewFinding`
- 观察窗口调度语义
- `ReviewGate`
- 复核结果写入 `Blackboard` 与 `SessionHistory`
- 基于板块 / 指数的基准归因

### 8.3 不包含内容

- 自动胜率面板
- 复杂绩效归因
- 自动维度调权

### 8.4 阶段完成标志

- 至少支持方向判断、催化判断、时间窗口判断的后验复核
- Reviewer 输出结果可供人工审阅
- 失效主张与冲突发现可被记录和检索
- 价格表现类验证默认带基准，不以绝对涨跌直接判定维度有效

---

## 9. Phase 4：Proposal 与长期认知沉淀

### 9.1 目标

把复核结果转成候选方法论，而不是直接改系统规则。

### 9.2 包含内容

- `methodology_evolution` 作为内部任务
- `ProposalStore`
- `pending / approved / rejected`
- `Evolver`
- `Mem0`
- 经审核结果写回长期层
- proposal 证据权重与样本依据

其中写回 Mem0 的对象必须具备：

- 来源 run / review / proposal 引用
- 明确 memory type
- 审核通过或 approved 状态依据

### 9.3 不包含内容

- 全自动正式规则更新
- 无人工干预的 prompt 演化
- 复杂多版本实验平台

首期方法论进化技术边界进一步固定为：

- 允许演化：维度池、检索权重配置、经验记忆写回
- 不允许演化：主 Prompt 模板自动覆盖、自由规则自动改写、多版本实验自动切流
- `PromptPatchProposal` 只允许作为人工审核候选存在，不进入首期自动应用链路

### 9.4 阶段完成标志

- Reviewer 结果可生成 proposal draft
- proposal 有明确状态流
- 只有 approved proposal 才能进入长期 memory 或下一版方法论
- 提案必须附带样本数、基准口径和 before / after 变化说明
- 当前激活的方法论版本可通过 manifest 明确追溯
- `retrieval_policy.yml` 与 `methodology_manifest.yml` 的最小字段合同固定
- `score_weights` 使用固定字段集合，不允许首期自由扩展
- 首期检索打分合同固定为“硬过滤 + 四项软打分”，`RetrievalWeightPatch` 只调权重，不改公式
- `recency_score` 首期固定为线性衰减，并由 `recency_window_days` 承载窗口参数
- `metadata_score` 首期固定为查询中可选 metadata 字段的命中比例
- Reviewer 的基准解析策略应进入 `review_policy.yml`，默认 `sector`，必要时可回退到 `index`
- `artifacts/` 首期虽然保守保留，但应受 retention 与体积上限约束

---

## 10. 跨阶段固定约束

无论系统推进到哪个阶段，都保持以下约束不变：

- Reporter 不直接制造 exact claim
- 没有证据支撑的判断不能包装成结论
- 候选方法论不能直接写入长期 memory
- 长期 memory 只沉淀可复用、可解释、可验证内容
- `Blackboard` 负责共享事实，不负责聊天记录
- `Mailbox` 负责定向反馈，不负责共享事实存储
- `Skill` 先走本地 backend，MCP 只作为可选扩展层

---

## 11. 每阶段建议验收方式

### Phase 0 验收

- 运行对象字段与状态流定义稳定
- run 归档目录结构稳定
- run 清理与保留规则稳定

### Phase 1 验收

- 个股研究请求能完成 run
- 行业研究请求能完成 run
- exact claim 能被 snapshot 约束
- run 可查看事件和结果归档

### Phase 2 验收

- KOL 文本能稳定蒸馏
- 蒸馏结果有证据锚点
- 蒸馏产物可进入检索层

### Phase 3 验收

- 可验证主张能被 Reviewer 复核
- 复核结果可归档、可引用、可追溯
- 价格表现类复核默认采用相对基准

### Phase 4 验收

- 复核结果能转 proposal
- proposal 审核后才能进入长期层
- 至少处理 50 条历史 KOL 观点后，才允许宣称闭环具有初步统计意义

---

## 12. 当前默认决策

如果后续没有新的产品决策，默认采用以下选择：

- 第一优先级是在线研究最小闭环
- 第二优先级是 KOL 蒸馏
- 第三优先级是后验验证
- 第四优先级是方法论演化
- 默认 `provider` 是 `OpenAI API`
- 默认 `agent model` 是 `GPT-5.5`
- 首期默认所有核心 Agent 统一使用 `GPT-5.5`
- 架构保留 provider registry，但首期不做多 provider 并行
- 首期 vendor 选择以“最容易接入、最快满足 skill 合同”为准
- 默认 `market` 数据源是 `AkShare`
- 首期行情快照以日线为主，优先支持 `raw / qfq / hfq`
- 分钟级行情只作为补充能力，不把长期 1 分钟历史作为首期硬依赖
- 架构保留 `data vendor adapter`，后续可补 `TushareAdapter`
- 默认 `fundamental` 数据源也是 `AkShare`
- `fundamental_snapshot_skill` 首期先满足最小财务 snapshot 合同，后续再视稳定性补 `TushareAdapter`
- 默认 `notice` 数据源是 `Cninfo`
- `notice_snapshot_skill` 首期优先保证公告原文锚点与披露信息可追溯，后续再视需要补 `ExchangeNoticeAdapter`
- `Qdrant` 是首期正式检索与结构化证据 backend
- 默认 embedding model 是 `text-embedding-3-small`
- 首期检索默认 dense-only，不启用 reranker
- 但 Retriever 流程必须预留 rerank stage 插槽，便于后续扩展
- `Qdrant` 首期采用单 collection：`research_evidence`
- 首期 metadata filter 只允许依赖固定 payload 字段，不允许自由拼接临时 key
- `Mem0` 是首期正式长期记忆 backend
- 首期只正式交付 `CLI` 入口
- 首期 CLI 命令集合固定为 `run / history / replay / resume / cancel`
- 核心 service 与 response contract 保持传输无关，后续再补 `HTTP/API`
- `review_thesis` 与 `methodology_evolution` 默认不是首期用户入口
- `GateEvaluator` 先做规则型，不做 LLM 自由裁量型
- 首期配置与运行环境固定采用 `.env + yaml`，不把密钥、端点、权重、时间窗口硬编码到代码中
- 首期不强依赖 Docker，但代码与配置应保持可容器化移植，后续允许通过 Docker 部署
