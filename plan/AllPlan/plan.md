# A股研究系统 MVP 方案 + PRD（逻辑主方案）

> 状态说明（2026-07-01）
>
> 这份文档保留的是业务逻辑全景与长期目标，不等同于当前工作树的完成状态。
> 其中很多“Phase 1/2/3/4”的表述属于路线设计历史，不应用来反推当前哪些能力还不存在。
> 当前实现是否已经落地、还差哪些收尾项，以 `completion_matrix.md`、`runtime_schema_contract.md`、`README.md` 为准。

## 1. 文档职责

这份文档只保留系统的业务逻辑、Agent 分工、协作方式、知识层设计、核心工作流与验收目标，不再承载具体代码实现、目录结构、协议字段细节或阶段性交付清单。

文档边界如下：

- 逻辑主方案：`plan.md`
- 分阶段收口方案：[phase_plan.md](./phase_plan.md)
- 代码实现与框架方案：[agent_system_implementation_plan.md](./agent_system_implementation_plan.md)
- Distiller 专项方案：[distiller_agent_plan.md](./distiller_agent_plan.md)
- Nerya 借鉴参考：[nerya_reference_for_agent_system.md](./nerya_reference_for_agent_system.md)

---

## 2. 项目定位

这是一个面向个人或小团队研究使用的 A股智能研究增强系统。

它的目标不是做一个黑箱自动选股机器，而是把：

- 实时行情与财务硬数据
- 新闻、公告、研报、KOL 长文等非结构化材料
- 用户长期偏好与历史研究结论
- 经市场真实反馈持续清洗后的方法论

组织成一个按需触发、可审计、可复盘、可演化的多 Agent 研究工作流，帮助用户高效完成：

1. 个股深度体检
2. 行业 / 主题聚焦研究
3. KOL 逻辑蒸馏与方法论自进化复盘

本系统的核心组合仍然是：

**Multi-Agent Orchestration + Qdrant Metadata-First Retrieval + Mem0 Evolutionary Cognition**

其中：

- 多 Agent 负责分工协作，而不是由一个大 Prompt 包办全部研究过程。
- Qdrant 承载结构化证据与高价值知识单元，服务检索与引用追溯。
- Mem0 承载用户偏好、方法论演化结果和长期认知沉淀。
- 异步复盘链路用真实价格与确定性硬数据清洗软逻辑，推动系统自我优化。

### 2.1 关键技术

主方案层面，当前系统的关键技术只保留到“高层职责”这一层：

- `OpenAI API + GPT-5.5`
  - 作为首期默认 LLM provider 与默认 agent model
  - 首期默认所有核心 Agent 统一使用 `GPT-5.5`
  - 架构上保留 provider registry，但首期不做多 provider 并行
- `AkShare`
  - 作为首期默认 `market` 数据源，服务 `Verified Snapshot` 的行情事实查询
  - 首期优先覆盖 A 股日线、复权价格、成交额、涨跌幅与基础板块行情
  - 分钟级数据只作为补充能力，不把长期 1 分钟历史作为首期硬依赖
- `Python`
  - 作为主实现语言，承载 Agent runtime、任务编排、数据处理与本地运行入口
- `Instructor + Pydantic`
  - 作为关键节点的结构化输出约束层，服务 Distiller、Reviewer、Proposal 等强 schema 环节
- `taxonomy file`
  - 作为标准维度池、别名归并和维度生命周期的统一来源
- `Qdrant`
  - 作为结构化证据和 metadata-first 检索底座
- `Mem0`
  - 作为长期认知沉淀层，承载偏好、方法论和错误经验
- `Verified Snapshot`
  - 作为精确价格、指标、财务与公告口径的确定性事实快照层，约束 Agent 的 exact claim

这些技术在主方案中的作用是定义能力边界，而不是展开实现细节。具体包结构、协议模型和落地方式统一放在实现文档中。

---

## 3. MVP 范围

当前文档描述的是系统目标链路和逻辑全景，不等同于“首期一次性交付清单”。

首期执行顺序、阶段边界与延后事项，以 [phase_plan.md](./phase_plan.md) 为准。

### 3.1 本期要做

当前逻辑方案覆盖三条主链路：

1. 个股深度体检
2. 行业 / 主题聚焦研究
3. KOL 逻辑蒸馏与方法论自进化复盘

但它们不要求在第一阶段同时落地。

按当前分阶段方案：

1. Phase 1 优先交付个股深度体检、行业 / 主题聚焦研究与最小 runtime 闭环
2. Phase 2 再接入 KOL 逻辑蒸馏与结构化存证
3. Phase 3 再接入后验复核
4. Phase 4 最后接入方法论候选更新与长期沉淀

其中第三条目标链路最终包含三件事：

- 蒸馏 KOL 文本中的分析维度、主张、风险与催化
- 用后验价格与硬数据复核这些逻辑是否有效
- 把验证后的结果沉淀为方法论候选和长期记忆

### 3.2 本期不做

- 全市场自动推荐或全量选股引擎
- 高频、自动化下单或交易执行
- 黑箱式买卖建议
- 完整回测平台
- 多用户复杂权限体系
- 以粗暴 chunk-first 为主的传统 RAG

---

## 4. 用户入口与任务类型

### 4.1 对话式用户入口

用户入口默认采用对话式交互，而不是纯表单或纯命令式入口。

首期交付形态固定为：

- `CLI`

但核心运行时必须保持传输无关，后续允许在不改 `task_router`、`ResearchOrchestrator` 和 `ResponseEnvelope` 协议的前提下补 `HTTP/API` 或 Web UI 入口。

典型输入例如：

- “帮我看下寒武纪现在还能不能跟”
- “最近创新药为什么这么强，给我一份主线梳理”
- “把逸修1最近关于 AI 的观点蒸馏一下”
- “把这个观点放到过去 20 天价格里验一下”

对话式入口的价值不在于把系统做成闲聊机器人，而在于：

- 降低用户使用门槛
- 允许多轮澄清任务范围
- 让复杂研究任务自然落到结构化任务流

### 4.2 当前支持的入口意图

逻辑层统一围绕以下意图组织：

- `stock_check`
- `sector_research`
- `distill_kol_logic`
- `clarify`
- `review_thesis`
- `methodology_evolution`

其中按当前分阶段方案：

- Phase 1 默认对外开放：`stock_check`、`sector_research`
- Phase 2 默认对外开放：`distill_kol_logic`
- `clarify` 作为内部追问工作流，在意图不明或槽位不足时触发
- `review_thesis` 与 `methodology_evolution` 在早期阶段默认作为内部任务，不作为首期用户入口

### 4.3 外部对话，内部结构化

系统坚持一个固定原则：

- 外部入口是对话
- 内部执行是结构化任务
- `ResearchOrchestrator` 负责把对话转成可执行任务

如果任务信息不足，系统允许少量澄清追问；补全后再进入执行层。

如果系统无法稳定判定入口意图，也应允许先进入 `clarify` 追问工作流，而不是强行归类到某个研究任务。

### 4.4 实时反馈原则

用户入口应具备接近 Codex 的过程可见性，但这种“实时感”来自运行时事件和阶段进度，而不是来自让下游 Agent 直接面向原始聊天消息工作。

因此逻辑上必须支持：

- 请求已接收
- 正在执行哪个阶段
- 当前由哪个 Agent 处理
- 是否进入复核、提案、报告生成
- 最终完成或失败

对于首期 CLI，进一步固定以下体验目标：

- 支持用户中断当前 run
- 支持增量展示过程摘要与回答输出
- 支持查看历史 run 与历史结果
- 支持从历史记录恢复上下文或重放关键过程

首期 CLI 命令面也收敛为：

- `run`
- `history`
- `replay`
- `resume`
- `cancel`

其中“过程可见性”展示的是运行摘要、阶段进度和中间结论，不把原始 chain-of-thought 作为产品输出要求。

具体协议字段与事件模型放在实现方案中定义。

---

## 5. 系统设计原则

### 5.1 JIT 单次运行与异步反思结合

- 前台任务是按需触发的一次研究运行
- 后台反思是异步的复盘与方法论更新

这保证用户侧体验轻量，同时让系统具备长期演化能力。

### 5.2 硬数据验证软逻辑

- 行情、财务、公告等确定性数据负责约束研究结论
- KOL 文本、新闻叙事、主题逻辑属于软逻辑材料
- 系统必须用硬数据反向校验软逻辑，而不是让文本互相证明文本
- 对于价格、指标、财务口径、公告口径等精确陈述，系统应优先通过 `Verified Snapshot` 类工具提供确定性事实快照，而不是允许 Agent 自由生成 exact claim

### 5.3 研究增强优先，不做自动决策

系统增强的是研究效率、证据组织能力与方法论沉淀能力，而不是直接替用户做自动交易决策。

### 5.4 Memory 与 Retrieval 分层

- 检索层解决“找证据”
- 记忆层解决“沉淀方法论”
- 原始资料层保留可追溯的原文和上下文

### 5.5 Proposal 驱动演化

任何重要演化都应先形成候选提案，再进入审核与应用，而不是让 Agent 直接修改正式方法论。

提案不应只给出主观方向，还应附带最小证据权重说明，例如样本数、基准口径、胜率变化或 before / after 指标变化，避免主观提案泛滥。

### 5.6 双层调度架构 + 黑板协作底座

当前系统明确采用：

**双层调度架构 + 黑板协作底座**

这意味着：

- 上层由 `ResearchOrchestrator` 显式调度任务
- 下层由角色型 Agent 执行各自工作
- 关键中间事实统一写入 `Blackboard`
- 定向补充和审查意见通过 `Mailbox`
- `SessionHistory` 负责回放与审计
- `ProposalStore` 负责承接后续演化提案

不采用纯双层直连，也不采用纯黑板自治。

原因是：

- 当前主链路是强流程型任务，适合上层显式编排
- Distiller、Reviewer、Reporter、Evolver 需要复用同一份中间事实
- 系统后续要支持 replay、proposal 和长期记忆沉淀，天然更适合黑板沉淀

### 5.7 Agent 与 Skill 分层

当前系统进一步明确采用：

**Agent 负责角色与状态，Skill 负责可替换的方法**

这意味着：

- `Agent` 负责输入输出边界、生命周期、状态切换、Blackboard 读写和与其他角色协作
- `Skill` 负责具体方法包，包括 Prompt、局部工具、输入输出校验与 fallback
- Agent 不直接等于 Prompt，也不直接等于 Skill
- Skill 可以扩展能力，但不能绕过 Agent 的状态与数据边界

对于外部数据能力，当前进一步固定一条约束：

- Agent 不直接调用 vendor API
- Agent 只调用统一的数据 Skill
- 数据 Skill 内部再通过 vendor adapter 获取数据，并返回统一 schema 的 `Verified Snapshot`

这意味着：

- 数据源更换发生在 Skill 或 adapter 层，而不是发生在 Agent 角色代码里
- `Collector`、`Retriever`、`Reviewer`、`Reporter` 等角色都只依赖 snapshot 合同，不依赖 `AkShare`、`Tushare` 等具体 vendor 细节

### 5.8 MCP 作为可选扩展层

当前方案不把 MCP 作为系统首期前置依赖，而把它定位为：

**Skill Runtime 的可选 backend**

这意味着：

- 当前阶段优先支持本地 Skill
- Agent 不直接依赖 MCP 才能运行
- 如果后续出现跨进程、跨语言、远程工具或第三方技能接入需求，再补 MCP backend

因此当前系统原则是：

- 先设计好 `Agent + Skill` 分层
- 再视扩展需求决定是否引入 MCP
- 即使未来引入 MCP，也不改变 Agent 角色边界和 Blackboard 协作方式

### 5.9 默认 LLM 选择

当前方案默认采用：

- 默认 `provider`：`OpenAI API`
- 默认 `agent model`：`GPT-5.5`

这意味着：

- `Collector`、`Distiller`、`Retriever`、`Reviewer`、`Evolver`、`Reporter`、`Critic` 在首期默认统一使用 `GPT-5.5`
- 首期不把“不同 Agent 使用不同模型”作为前置优化项
- 运行时仍保留 provider registry，以便后续引入替代 provider 或按 Agent 分层降本

这样收敛的原因是：

- 当前首期关键难点在协议、Prompt、Blackboard、状态机和验证闭环
- 过早做多 provider 或多模型分层，会显著增加 structured output、timeout、fallback 和评测复杂度

### 5.10 默认 `market` 数据源选择

当前方案默认采用：

- 默认 `market` 数据源：`AkShare`

这意味着：

- `Verified Snapshot` 的首期行情查询默认由 `AkShare` 提供
- 该能力默认通过 `market_snapshot_skill` 暴露给 Agent，而不是由 Agent 直接调用 `AkShare`
- 首期以 A 股日线历史行情为主，优先支持 `raw / qfq / hfq`
- 分钟线只作为补充能力接入，不把长期 1 分钟历史行情作为首期强依赖
- 架构上保留 `data vendor adapter`，后续可补 `TushareAdapter` 作为替代或 fallback

这样收敛的原因是：

- `AkShare` 对 A 股日线、复权与板块行情的接入成本更低，更适合首期快速落地
- 当前关键任务是先把 exact claim 约束、研究链路和复核链路跑通，而不是先追求多数据源治理

### 5.11 `Verified Snapshot` 的 Skill 化边界

当前方案把 `Verified Snapshot` 明确拆成三层：

- `Snapshot Skill`
- `Vendor Adapter`
- `Unified Snapshot Schema`

其中：

- `Snapshot Skill` 负责数据请求编排、参数校验、fallback、缓存和失败态约束
- `Vendor Adapter` 负责 `AkShare`、`Tushare`、交易所公告源等具体接入细节
- `Unified Snapshot Schema` 负责把外部返回值统一成系统内部可验证、可追溯的事实对象
- 首期配置层固定采用 `.env + yaml`，不允许把密钥、端点、权重、时间窗口等运行参数硬编码进 Agent / Skill / Adapter

首期还应固定依赖级降级矩阵，至少覆盖：

- LLM structured output 失败时的二次解析或失败返回
- `AkShare` 行情/财务查询失败时的缓存与 exact claim 限制
- `Cninfo` 失败时的公告缺失标记
- `Qdrant` 失败时的“无检索增强”降级模式
- `Mem0` 读写失败时的非阻塞与日志要求

首期固定三类数据 Skill：

- `market_snapshot_skill`
- `fundamental_snapshot_skill`
- `notice_snapshot_skill`

系统中的 Agent 只允许依赖这三类 Skill 合同，而不直接依赖某个 vendor SDK 或网页抓取实现。

首期 vendor 选择原则也进一步固定为：

- 先选最容易接入、最容易跑通、最容易满足 snapshot skill 合同的 vendor
- 不为后期可能更优的 vendor 提前付出首期复杂度
- 只要 Skill 合同、adapter 边界和统一 schema 稳定，后续 vendor 可以替换

在这个原则下，首期默认优先选择“更好拿”的数据源，而不是优先选择“理论上最完整”的数据源。

### 5.12 默认 `fundamental` 数据源选择

当前方案默认采用：

- 默认 `fundamental` 数据源：`AkShare`

这意味着：

- `fundamental_snapshot_skill` 首期默认通过 `AkShare` 接入
- 首期优先目标是先满足财务 exact claim 的最小 snapshot 合同，而不是先做多 vendor 财务治理
- 后续如果 `AkShare` 在财务口径、稳定性或覆盖上成为瓶颈，再切换或补充 `TushareAdapter`

### 5.13 默认 `notice` 数据源选择

当前方案默认采用：

- 默认 `notice` 数据源：`Cninfo`

这意味着：

- `notice_snapshot_skill` 首期默认通过 `Cninfo` 接入
- 公告类 snapshot 优先追求原文锚点、披露日期和公告标题的可追溯性，而不是先追求多 vendor 聚合
- 后续如遇 `Cninfo` 覆盖缺口、访问限制或个别交易所页面差异，再补 `ExchangeNoticeAdapter` 作为 fallback

这样收敛的原因是：

- 公告类最重要的是“官方披露原文”，不是二次整理后的资讯页
- `Cninfo` 作为统一入口，更适合首期用一个 adapter 覆盖大部分公告查询场景

---

## 6. 总体架构

### 6.1 控制面

控制面负责定义“谁先做、谁后做、何时进入 gate、何时结束运行”。

核心对象：

- `ResearchOrchestrator`
- `TeamTemplate`
- `TaskGraph`
- `GateEvaluator`

### 6.2 协作面

协作面负责沉淀共享事实、定向消息、运行历史和演化候选。

核心对象：

- `Blackboard`
- `Mailbox`
- `SessionHistory`
- `ProposalStore`

### 6.3 知识面

知识面负责管理长期知识、结构化证据和演化方法论。

核心对象：

- 维度池
- Qdrant Retrieval Layer
- Mem0 认知层

### 6.4 执行面

执行面由角色型 Agent 组成：

- `collector`
- `distiller`
- `retriever`
- `reviewer`
- `evolver`
- `reporter`
- `critic`

每个 Agent 内部都允许挂载一个或多个 Skill，但 Skill 不能替代 Agent 角色本身。

### 6.5 架构示意

```text
┌──────────────────────────────── 用户入口层 ────────────────────────────────┐
│  对话式入口 / CLI / 后续 Web UI                                           │
└────────────────────────────────────┬──────────────────────────────────────┘
                                     │
                                     ▼
┌──────────────────────────── 控制与编排层 ────────────────────────────────┐
│  ResearchOrchestrator                                                    │
│  TeamTemplate  ·  TaskGraph  ·  GateEvaluator                            │
└────────────────────────────────────┬──────────────────────────────────────┘
                                     │
                                     ▼
┌──────────────────────────── 角色执行层 ──────────────────────────────────┐
│  Collector  ·  Distiller  ·  Retriever  ·  Reviewer  ·  Evolver         │
│  Reporter  ·  Critic                                                     │
└────────────────────────────────────┬──────────────────────────────────────┘
                                     │
                ┌────────────────────┼────────────────────┐
                │                    │                    │
                ▼                    ▼                    ▼
┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│  共享协作层          │  │  事实校验层          │  │  运行沉淀层          │
│  Blackboard          │  │  Verified Snapshot   │  │  SessionHistory      │
│  Mailbox             │  │  Market / Funda /    │  │  ProposalStore       │
│                      │  │  Notice / Sector     │  │                      │
└───────────┬──────────┘  └───────────┬──────────┘  └───────────┬──────────┘
            │                         │                         │
            └───────────────┬─────────┴───────────────┬─────────┘
                            │                         │
                            ▼                         ▼
┌──────────────────────────── 知识与检索层 ────────────────────────────────┐
│  Qdrant Retrieval Layer   ·   taxonomy / 维度池   ·   Mem0 认知层       │
└────────────────────────────────────┬──────────────────────────────────────┘
                                     │
                                     ▼
┌──────────────────────────── 输出与演化层 ────────────────────────────────┐
│  Reports  ·  Mem0 认知沉淀  ·  Methodology Candidates / Approved Updates │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 7. 核心组件与分工

### 7.1 ResearchOrchestrator

负责：

- 接收用户任务
- 识别任务意图
- 组织多轮澄清
- 选择 team template
- 生成任务图
- 调度各 Agent
- 汇总运行状态
- 控制进入 gate、review、proposal、report 的时机
- 承担超时熔断与失败返回

它不直接承担具体研究推理工作。

它还应在外部依赖长时间无响应时中断当前运行，并返回明确失败态或重试建议，避免系统卡死。

### 7.2 TeamTemplate / TaskGraph / GateEvaluator

三者共同定义运行骨架：

- `TeamTemplate`：不同任务类型使用哪些 Agent
- `TaskGraph`：任务依赖、顺序、并发关系与条件流转
- `GateEvaluator`：结果是否足够进入下一阶段

其中 `TaskGraph` 的目标不是为了引入复杂 workflow 平台，而是让“谁先做、谁后做、在什么条件下进入下一节点”成为显式系统规则，而不是埋在 prompt 或脚本流程里

### 7.3 Blackboard

Blackboard 是共享事实层，不是聊天记录层。

它负责承接：

- 原始材料引用
- 蒸馏后的逻辑块
- 检索证据
- 复盘发现
- 风险与冲突
- 报告片段
- 候选记忆

首期存储策略固定为：

- 每个 run 只使用一个 `blackboard.jsonl`
- 不按 `kind`、`phase` 或 `agent` 分文件
- 过滤、导出和快照视图统一在读取层完成

### 7.4 Mailbox

Mailbox 只处理定向消息，不承担共享事实存储职责。

它适合承接：

- Reviewer 对 Distiller 的补充请求
- Critic 对 Reporter 的质疑
- Evolver 对某条候选维度的补证请求

### 7.5 SessionHistory

SessionHistory 记录每次任务运行的上下文、证据、关键判断与结果，供：

- replay
- 审计
- 失败复盘
- 后续提案

首期归档职责固定为：

- `events.jsonl`：过程事件流真源
- `logs.jsonl`：排错与调试日志
- `result.json`：结构化最终结果
- `summary.json`：面向人读的运行摘要

其中：

- 回放、排查和审计优先依赖 `events.jsonl`
- 日志用于定位错误，不替代过程事件真源
- `result.json` 与 `summary.json` 都属于派生产物，不替代过程真源
- `ResponseEnvelope.progress` 首期必须保持结构化对象，而不是自由文本

首期保留策略也应保持保守：

- 事件流、黑板与最终结果默认保留
- 成功 run 的 checkpoint 默认删除
- artifacts 首期默认保留，不做自动清理

同时应固定 `artifacts/` 的边界：

- `artifacts/` 只承接大文件、原始导出、调试材料和中间副产物
- 会被下游 Agent 消费的关键事实，必须进入 `Blackboard`
- 会影响运行判断的关键过程，必须进入 `RunEvent`
- 最终对外结果，必须进入 `result.json` 或 `summary.json`
- 首期应同时提供保守 retention 与体积上限，例如 `artifacts_retention_days` 与 `max_artifacts_size_mb`

同时，运行态核心对象应采用统一 ID 规则，避免 run、event、entry、proposal 各自发明命名方式，破坏追溯链一致性。

### 7.6 ProposalStore

ProposalStore 承接方法论更新候选，而不是正式结果本身。

提案可以来自：

- 维度池调整
- Prompt 调整
- 检索权重更新
- 规则修订
- 经验学习沉淀

首期固定以下约束：

- ProposalStore 采用 `pending / approved / rejected` 三态
- Agent 只能创建 `proposal draft`，不能直接把提案改成 `approved`
- 只有明确审核动作才能触发状态迁移
- 只有 `approved` proposal 才允许影响正式方法论或进入长期记忆

### 7.7 各角色 Agent

#### Collector

负责聚合原始材料与确定性数据，不负责做深层逻辑判断。

#### Distiller

负责将 KOL 文本等非结构化材料蒸馏成逻辑块、维度候选和证据映射。

#### Retriever

负责从 Qdrant 召回与当前任务最相关的结构化证据。

其标准流程应保持为：

- metadata filter
- dense retrieval
- optional rerank
- evidence handoff

首期检索权重不做自由配置，至少固定 metadata、semantic、recency、dimension match 四类权重语义，避免 `RetrievalWeightPatch` 演化成不可审计的任意字典更新。

首期进一步固定：

- 检索先做硬过滤，再做软打分
- `final_score` 由 metadata、semantic、recency、dimension match 四个基础分按固定公式线性组合
- `dimension_match_score` 首期固定为“查询维度覆盖率”，不使用 Jaccard，也不使用完全包含布尔值
- `recency_score` 首期固定为基于 `recency_window_days` 的线性衰减
- `metadata_score` 首期固定为“查询中可选 metadata 字段的命中比例”
- `RetrievalWeightPatch` 只允许调权重，不允许暗中改基础分定义或计算公式

#### Reviewer

负责用硬数据或更强验证逻辑复核历史主张与研究结论。

对价格表现类判断，Reviewer 应优先采用相对基准归因，而不是简单使用绝对涨跌归因。

首期建议把基准解析策略做成配置项 `benchmark_resolution`，默认 `sector`，必要时允许回退到 `index`。

#### Evolver

负责把复盘结论转成方法论更新候选。

#### Reporter

负责把系统中间产物组织成可读的研究输出。

#### Critic

负责作为第二道防线，检查蒸馏误读、证据不足、因果跳跃和报告幻觉。

在蒸馏链路中，Critic 或 Reviewer 至少应承担抽检职责，避免低质量逻辑块直接进入后验统计。

---

## 8. 知识层设计

### 8.1 维度池

维度池是系统的标准分析因子目录，负责统一：

- 标准维度名称
- 分类体系
- 同义表达归并
- 状态管理
- 演化入口

它必须满足两点：

- 能被 Distiller、Retriever、Reviewer、Evolver 共同消费
- 能承载 active、pending、deprecated 等生命周期状态

### 8.2 Qdrant Retrieval Layer

Qdrant 在本系统中不是单纯的向量库，而是结构化检索底座。

它负责：

- 保存逻辑块级证据
- 保存高价值元数据
- 支持 metadata-first 检索
- 支持语义与关键词混合召回
- 支持引用追溯
- 首期默认采用 dense embedding 检索
- 首期默认 embedding model 为 `text-embedding-3-small`
- 首期不启用 reranker，但 Retriever 流程中必须预留 rerank stage 插槽
- 首期固定单 collection：`research_evidence`
- 首期 metadata filter 只允许依赖固定 payload 字段，不允许自由扩展临时 key

对于需要支撑精确数字或时间窗口判断的研究结论，Qdrant 检索层不应替代确定性事实源；它负责召回证据与上下文，而精确口径仍应优先来自 `Verified Snapshot` 类工具

### 8.3 Mem0 认知层

Mem0 负责系统的长期认知沉淀，主要包括：

- 用户偏好
- 方法论版本
- 维度有效性经验
- 错误模式
- 场景化经验

使用约束固定为：

- Mem0 不直接承接原始材料、临时结论或未审核候选
- 写入 Mem0 的对象必须具备明确来源引用，例如 `run_id`、`proposal_id`、`review_id`
- 写入 Mem0 的动作必须经过显式审核或 approved proposal 驱动
- Mem0 中的长期知识必须能追溯回 Blackboard、ReviewFinding 或 ProposalStore
- Mem0 的长期知识类型在首期必须使用固定枚举，而不是自由发明 memory type
- 读取 Mem0 时必须通过统一 memory service，并按 `memory_type`、方法论版本和上下文范围受控过滤

### 8.4 三层知识分工

逻辑上统一拆成三层：

1. 原始资料层：保留原文和不可替代上下文
2. 证据检索层：承载结构化证据与引用
3. 认知方法论层：承载长期方法论和经验演化

---

## 9. 核心工作流

以下工作流描述的是系统目标形态，不表示这些链路必须在同一阶段同时交付。

### 9.1 个股深度体检

1. 用户以对话形式提出个股问题
2. `ResearchOrchestrator` 识别为 `stock_check`
3. Collector 拉取行情、基本面、新闻、公告等材料
4. 如涉及精确价格、指标、财务口径判断，先生成对应的 `Verified Snapshot`
5. Blackboard 写入原始材料、事实快照和任务上下文
6. Retriever 基于标的与当前方法论召回相关证据
7. Reporter 汇总行情、证据链、多空逻辑与风险点
8. Critic 或 GateEvaluator 检查证据是否充足
9. SessionHistory 归档本次运行

### 9.2 行业 / 主题聚焦研究

1. 用户提出行业或主题问题
2. `ResearchOrchestrator` 识别为 `sector_research`
3. Collector 拉取行业新闻、板块构成、关键个股与催化信息
4. 如涉及精确行情、财务或板块口径判断，先生成对应的 `Verified Snapshot`
5. Retriever 召回历史高质量逻辑块和相关研究证据
6. Reporter 组织主线、候选股、分歧点与风险点
7. SessionHistory 记录输入、证据和结论

### 9.3 KOL 逻辑蒸馏与方法论自进化

#### 第一步：采集与蒸馏

1. Collector 获取 KOL 原文或导入历史抓取结果
2. Blackboard 写入原始材料与来源信息
3. Distiller 去噪、切分逻辑块、提取维度、标记风险
4. Distiller 将逻辑块、维度候选与证据映射写入 Blackboard
5. 必要时 Critic 或 Reviewer 对蒸馏结果做二次审查

#### 第二步：价格与硬数据对齐

1. 到达设定观察窗口后触发 Reviewer
2. Reviewer 拉取真实价格与确定性数据
3. Reviewer 对比历史主张与真实结果，并优先基于板块或指数基准判断相对表现
4. Reviewer 写入胜率变化、失效维度、冲突发现与场景标签

#### 第三步：方法论候选更新

1. Evolver 读取 Review Session 与 Blackboard
2. Evolver 生成维度池、权重、Prompt 或规则的提案候选，并附带样本数、基准口径和 before / after 变化说明
3. ProposalStore 承接候选，不直接改正式方法论
4. 通过审核的候选再进入下一版方法论

首期技术上进一步收缩为：

- 允许 Evolver 生成的正式候选类型：维度池调整、检索权重调整、经验记忆沉淀
- Prompt 主模板与自由规则改写可以进入 proposal 讨论，但不进入首期自动写回范围
- `PromptPatchProposal` 首期允许创建，但属于 `manual-only` 类型；即使 approved，也只进入人工待办，不触发自动写回

其中与 Mem0 相关的约束是：

- 只有 `MemoryLearningUpdate` 或 approved proposal 才允许触发 Mem0 写入
- Evolver 不直接写 Mem0，而是提交可审核对象
- Mem0 中的方法论沉淀必须带来源 proposal 或 review 引用

---

## 10. 数据写回策略

### 10.1 写回原则

- 只写回可复用、可解释、可验证的知识
- 不把低信息密度噪音写入长期层
- 不把未经审核的候选直接写成正式规则

### 10.2 写回检索层

适合写回 Qdrant 的内容：

- 去噪后的高价值逻辑块
- 蒸馏后的标准维度标签
- 可被召回和引用的证据片段

### 10.3 写回认知层

适合写回 Mem0 的内容：

- 用户偏好
- 已验证有效的维度经验
- 明确的错误模式
- 经审核的方法论更新结果

这些内容写入 Mem0 时还必须满足：

- 具备 `memory_type`
- 具备 `source_run_id` 或等价来源引用
- 具备审核或 approved 状态依据
- 不允许无来源、无状态、无上下文地写入

### 10.4 不写回的内容

- 纯情绪化表达
- 无法验证的噪音结论
- 与真实市场反馈明显背离且无复用价值的内容

---

## 11. MVP 成功标准

### 11.1 研究输出质量

- 个股与行业报告不流于表面摘要
- 结论有清晰证据链
- 能准确指出多空逻辑、分歧点和关键风险

### 11.2 蒸馏能力

- 能把大段 KOL 文本稳定蒸馏成结构化逻辑块与维度候选
- 能区分主张、证据、风险与催化
- 能保留可追溯的原文证据锚点
- 对长文本具备预筛或降级路径，避免全量蒸馏导致成本失控

### 11.3 认知闭环

- 至少跑通一次“历史逻辑 -> 后验验证 -> 方法论候选更新”的闭环
- 能把失效逻辑降权，把高解释力维度保留下来
- 在宣称闭环有效前，建议至少覆盖 50 条历史 KOL 观点，以降低小样本方差

### 11.4 多 Agent 协作有效

- `ResearchOrchestrator` 能稳定调度主链路
- `Blackboard` 能承接共享事实
- `Mailbox` 能承接定向反馈
- `SessionHistory` 能回放关键运行过程

### 11.5 硬数据约束成立

- 系统最终仍以价格与财务等确定性信息作为验证标准
- 避免纯文本层面的自我循环

---

## 12. 与实现文档的分工

以下内容不再放在主方案中，而统一迁移到实现文档：

- 代码目录结构
- 包与模块命名
- 统一请求 / 响应 / 事件字段定义
- Pydantic / Instructor / taxonomy / Qdrant client 的落位方式
- runtime 文件结构
- 分阶段开发计划
- 测试与交付清单

对应文档见：

- [phase_plan.md](./phase_plan.md)
- [agent_system_implementation_plan.md](./agent_system_implementation_plan.md)
