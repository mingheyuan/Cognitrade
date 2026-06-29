# Distiller Agent 方案

## 1. 文档职责

这份文档只描述 Distiller Agent 的定位、边界、处理链路、系统集成关系与验收要求。

它不再承载以下内容：

- 具体代码目录
- 模块文件命名
- 技术栈落位细节
- runtime 文件结构
- 阶段性交付清单

这些内容统一迁移到：[agent_system_implementation_plan.md](./agent_system_implementation_plan.md)

如果需要直接进入 Prompt 与输出合同层，以 [distiller_prompt_template_spec.md](./distiller_prompt_template_spec.md) 为准。

---

## 2. 目标

先落一个可独立运行、但天然可接入整体系统的 `Distiller Agent`，把雪球等 KOL 原始帖子 / 长文蒸馏成：

- 逻辑块
- 维度候选
- 证据映射
- 风险标记
- 可供下游消费的中间产物

Distiller 第一版的目标不是把整个多 Agent 系统做完，而是先把这条主链路做稳：

`原始文本 -> 去噪 -> 逻辑切分 -> 维度提取 -> 证据映射 -> 风险标记 -> 结构化输出`

在当前系统里，Distiller 保持为一个角色型 Agent，但其核心蒸馏方法应封装为可替换 Skill。

---

## 3. 当前前提

- 总体逻辑以 [plan.md](./plan.md) 为准
- 分阶段优先级以 [phase_plan.md](./phase_plan.md) 为准
- 现有抓取样例来自 [xueqiu_webbridge_scraper.py](D:\work_26_6_24\scraper\xueqiu_webbridge_scraper.py)
- 当前可直接复用的原始输入是抓取结果目录中的 `statuses.jsonl`
- 第一版默认先只服务 KOL 文本蒸馏，不扩展到公告、研报、新闻

按当前阶段定义，Distiller 属于 Phase 2 核心能力，而不是首期在线研究闭环的前置阻塞项。

---

## 4. 与对话式入口的关系

Distiller 不直接面向最终用户暴露。

用户看到的是对话式入口，但 Distiller 只消费由 `ResearchOrchestrator` 转换后的结构化任务和结构化输入材料。

这意味着：

- 用户侧是自然语言任务
- Distiller 侧是明确来源、明确范围、明确材料的输入对象

这样可以避免自由聊天内容直接污染蒸馏主链。

---

## 5. Agent 定位与职责边界

### 5.1 Distiller 的职责

Distiller 只负责认知提炼，不负责抓取、后验验证或最终研究报告。

核心职责：

- 原始文本标准化
- 去除低价值噪音
- 切分逻辑块
- 提取主张、证据、风险、催化和时间范围
- 识别分析维度
- 合并同义表达
- 建立证据与结论映射
- 输出结构化蒸馏结果

在 MVP 阶段，Distiller 还必须显式输出不确定性，而不是把低质量理解包装成高置信度逻辑。

建议 Distiller 框架固定为：`WorkflowAgent`

### 5.2 不属于 Distiller 的职责

- 网页抓取与翻页
- 登录态维护
- 价格与财务验证
- 方法论正式更新
- 多 Agent 全局调度
- 面向终端用户的最终报告生成
- 直接承担 Skill 注册中心职责

---

## 6. Distiller 在系统中的位置

逻辑上，Distiller 位于以下链路中：

`Collector -> Distiller -> Retriever / Reviewer / Reporter / Evolver`

它是一个角色型 Agent，而不是一个孤立脚本。

进一步说：

- Distiller Agent 承担运行时角色
- Distillation Skill 承担可替换的蒸馏方法
- trimming、维度归并、风险标记也可以拆成子 Skill

它的运行方式应当满足：

- 由 `ResearchOrchestrator` 调度
- 关键中间结果写入 `Blackboard`
- 定向补充请求通过 `Mailbox`
- 运行过程可写入 `SessionHistory`
- 候选更新可进入 `ProposalStore`

---

## 7. 当前选定的协作方式

Distiller 不采用“纯函数直连”作为长期协作模式，也不采用“完全自治的黑板式自组织”。

它服从当前系统统一方案：

**双层调度架构 + 黑板协作底座**

对 Distiller 的具体含义是：

- 上层由 `ResearchOrchestrator` 决定何时执行 Distiller
- Distiller 执行后把共享事实写入 `Blackboard`
- Reviewer、Reporter、Evolver 从 `Blackboard` 消费这些事实
- 如需 Distiller 补充解释或修正，则通过 `Mailbox` 发起定向请求

这保证 Distiller 既能嵌入强流程任务，又不会把自己的中间产物锁死在私有返回值里。

### 7.1 Distiller 的 Agent + Skill 结构

第一版建议 Distiller 内部分成两层：

- `Distiller Agent`
- `Distillation Skill`

其中：

- `Distiller Agent` 负责接收任务、管理阶段、产出事件、写入 Blackboard、响应 Gate
- `Distillation Skill` 负责 Prompt、结构化抽取、局部校验与 fallback

---

## 8. 上下游关系

### 8.1 上游依赖

Distiller 的上游是 `Collector` 或输入适配层。

上游至少要提供：

- 原始正文
- 作者信息
- 发布时间
- 来源 URL
- 可能关联的标的 / 行业 / 元数据

### 8.2 下游消费方

Distiller 的结果至少会被以下角色消费：

- `Retriever`：把逻辑块作为可检索证据
- `Reviewer`：把维度与主张作为复核对象
- `Reporter`：把主张、证据、风险、催化组织成报告
- `Evolver`：把新维度或失效模式转成提案候选

---

## 9. 输入输出契约

### 9.1 输入对象

逻辑上，Distiller 的输入应是单条记录级别的结构化来源对象。

它至少应包含这些语义：

- 任务标识
- 来源类型
- 来源标识
- 作者
- 平台
- 发布时间
- 原始文本或 HTML
- 来源 URL
- 相关标的或行业标签
- 采集阶段附带的质量信息

如果来源是对话式入口，则必须先经过 `ResearchOrchestrator` 的意图识别和槽位补全，再转为 Distiller 可消费的输入。

### 9.2 Distiller 专属任务语义

当用户发起蒸馏类任务时，Distiller 所在链路至少要能回答三个问题：

- 蒸馏谁
- 蒸馏什么范围
- 蒸馏哪类材料

因此在逻辑层面，蒸馏任务至少要有以下信息中的一种：

- 作者
- 已抓取的来源目录
- 直接提供的原始文本

### 9.3 输出对象

Distiller 的输出逻辑上分为四层：

1. `distillation_summary`
2. `logic_blocks`
3. `distilled_dimensions`
4. `blackboard_entries`

### 9.4 distillation_summary

用于概括本次蒸馏整体情况，至少应表达：

- 内容类型
- 总体置信度
- 风险标记
- 处理说明
- 是否建议进入人工复核

### 9.5 logic_blocks

逻辑块是 Distiller 的核心产物。

每个逻辑块至少要表达：

- 它属于主张、证据、风险还是催化
- 它对应原文的哪一段
- 它涉及哪些标的或行业
- 它的方向与时间范围
- 它本身的置信度
- 它是否存在因果归属不清或引用归属不清的风险

### 9.6 distilled_dimensions

每条维度结果至少要表达：

- 标准化后的维度名称
- 维度类别
- 检出的别名或同义表达
- 它支持的主张
- 它依赖的证据块
- 它属于已知维度还是新候选维度
- 它的置信度

### 9.7 blackboard_entries

Distiller 不能只返回最终结果，还必须把共享给下游的中间事实写成 `Blackboard` 可消费的记录。

适合 Distiller 发布的共享记录包括：

- `source_record`
- `logic_block`
- `distilled_dimension`
- `review_hint`
- `memory_candidate`

---

## 10. Prompt 模板与结构化输出设计

Distiller 是系统质量上限的关键节点，因此 Prompt 模板和结构化输出 Schema 必须先于大规模工程接入完成设计与审查。

### 10.1 Prompt 设计原则

Distiller Prompt 第一版至少要满足以下原则：

- 不直接要求模型“总结文章”，而是要求按主张、证据、风险、催化拆分
- 优先抽取可验证主张，不鼓励生成泛泛观点
- 明确区分作者观点、引用观点和背景叙述
- 明确要求保留原文锚点
- 低置信度时允许返回不确定，而不是强行补全因果链
- 对因果方向不明确的内容，必须显式标记风险

### 10.2 推荐 Prompt 结构

建议把 Distiller Prompt 拆成稳定的多段模板，而不是单段长指令：

1. system role
2. task objective
3. extraction rules
4. negative rules
5. output schema reminder
6. confidence and risk rubric

其中负向约束至少要明确：

- 不把情绪表达当成维度
- 不把引用内容默认算作作者主张
- 不把价格结果倒推成原文因果
- 不在证据不足时编造时间范围、标的或方向

### 10.3 推荐抽取顺序

建议 Prompt 引导模型按以下顺序处理，而不是一步出最终答案：

1. 识别材料类型
2. 标记引用与作者新增内容
3. 切分逻辑块
4. 为逻辑块分类
5. 提取候选维度
6. 归并到标准维度
7. 输出风险与置信度

### 10.4 结构化输出 Schema 先行原则

在 Distiller 接入真实运行时之前，至少要先冻结以下对象的字段语义：

- `DistillationSummary`
- `LogicBlock`
- `DistilledDimension`
- `DistillationResult`

Schema 应优先服务：

- Blackboard 写入
- Reviewer 复核
- Reporter 消费
- Qdrant payload 对齐

而不是只为了让一次 LLM 调用“看起来能出结果”。

### 10.5 LogicBlock 最小字段要求

每个 `LogicBlock` 第一版至少应表达：

- `block_id`
- `block_type`
- `source_span`
- `speaker_type`
- `text`
- `normalized_claim`
- `symbols`
- `sectors`
- `time_horizon`
- `direction`
- `confidence`
- `risk_flags`

其中：

- `speaker_type` 至少区分 `author`、`quoted`、`uncertain`
- `block_type` 至少区分 `claim`、`evidence`、`risk`、`catalyst`
- `risk_flags` 至少能表达因果不清、引用不清、上下文不足

### 10.6 DistilledDimension 最小字段要求

每个 `DistilledDimension` 第一版至少应表达：

- `dimension_id`
- `dimension_name`
- `dimension_category`
- `alias_hits`
- `supported_claim_block_ids`
- `evidence_block_ids`
- `is_new_candidate`
- `confidence`
- `normalization_notes`

### 10.7 DistillationSummary 最小字段要求

每个 `DistillationSummary` 第一版至少应表达：

- `content_type`
- `overall_confidence`
- `needs_review`
- `major_risks`
- `processing_notes`

### 10.7A 准协议草案

为了减少后续 schema 漂移，建议在实现前先按下列语义冻结对象。

#### DistillationSummary 准协议

| 字段 | 类型语义 | 说明 |
| --- | --- | --- |
| `content_type` | enum | 如 `short_post`、`long_post`、`reply_chain` |
| `overall_confidence` | float | 0 到 1 的总体置信度 |
| `needs_review` | bool | 是否建议进入抽检或人工复核 |
| `major_risks` | string list | 主要风险标签 |
| `processing_notes` | string list | 本次蒸馏中的降级、截断、异常说明 |

#### LogicBlock 准协议

| 字段 | 类型语义 | 说明 |
| --- | --- | --- |
| `block_id` | string | run 内唯一标识 |
| `block_type` | enum | `claim`、`evidence`、`risk`、`catalyst` |
| `source_span` | object | 原文位置锚点，至少包含起止片段信息 |
| `speaker_type` | enum | `author`、`quoted`、`uncertain` |
| `text` | string | 清洗后的原文片段 |
| `normalized_claim` | string or null | 标准化后的主张表达 |
| `symbols` | string list | 涉及标的 |
| `sectors` | string list | 涉及行业或主题 |
| `time_horizon` | string or null | 时间范围表达 |
| `direction` | enum or null | `bullish`、`bearish`、`neutral`、`uncertain` |
| `confidence` | float | 0 到 1 的块级置信度 |
| `risk_flags` | string list | 风险标签 |

#### DistilledDimension 准协议

| 字段 | 类型语义 | 说明 |
| --- | --- | --- |
| `dimension_id` | string or null | 标准维度 ID，不存在时允许为空 |
| `dimension_name` | string | 标准名或候选名 |
| `dimension_category` | string | 维度类别 |
| `alias_hits` | string list | 命中的原始别名 |
| `supported_claim_block_ids` | string list | 支持该维度的主张块 |
| `evidence_block_ids` | string list | 支持该维度的证据块 |
| `is_new_candidate` | bool | 是否为新候选维度 |
| `confidence` | float | 0 到 1 的维度置信度 |
| `normalization_notes` | string list | 归并说明 |

#### DistillationResult 准协议

| 字段 | 类型语义 | 说明 |
| --- | --- | --- |
| `source_record_id` | string | 对应输入来源 |
| `summary` | object | `DistillationSummary` |
| `logic_blocks` | object list | `LogicBlock` 列表 |
| `distilled_dimensions` | object list | `DistilledDimension` 列表 |
| `blackboard_entries` | object list | 待写入共享事实 |

### 10.8 Prompt 模板验收要求

Prompt 模板在进入运行时前，至少要通过以下检查：

- 短帖、长帖、回复链各一组样例验证
- 作者观点与引用观点区分验证
- 低信息密度文本降级验证
- 因果方向易混淆样例验证
- structured output 成功率验证

如果这些检查不过，优先改 Prompt 和 Schema，不要急着扩大 Agent 链路。

---

## 10. Distiller 的逻辑处理链路

### 10.1 Input Normalization

目标：

- 统一文本格式
- 清理明显的编码和换行问题
- 保留可追溯的原文位置映射
- 识别内容类型

### 10.2 Noise Reduction

目标：

- 去掉低价值 UI 噪音和模板化尾巴
- 区分引用内容与作者新增观点
- 标记上下文不足和转述过重的风险

### 10.2A Lightweight Trimming

目标：

- 在主蒸馏前降低长文本成本
- 优先保留数字、因果关系词、专有名词和显式主张句
- 对低信息密度材料优先降级，而不是一律全量蒸馏

### 10.3 Logic Segmentation

目标：

- 按逻辑而不是按长度切分文本
- 尽量把主张、证据、风险和结论拆成稳定单元

### 10.4 Dimension Extraction

目标：

- 从逻辑块中提取分析因子和主张
- 识别方向、时间范围、涉及对象和因果链

### 10.5 Dimension Normalization

目标：

- 把不同说法归并到同一标准维度
- 无法归并时保留为新候选维度

### 10.6 Confidence Scoring

目标：

- 对逻辑块和维度做双层置信度评估
- 标记歧义、断裂因果、信息不足等风险

### 10.7 Session Writing

目标：

- 让本次蒸馏过程可审计、可回放、可供下游消费

---

## 11. 与整个系统的集成关系

### 11.1 与 Collector 的集成

Collector 不必为了 Distiller 重写抓取脚本，但必须通过适配层把原始抓取结果映射成稳定输入。

### 11.2 与 Retriever 的集成

Distiller 的逻辑块应天然适合作为后续检索单元，而不是需要再次重切分。

### 11.3 与 Reviewer 的集成

每条维度和主张都必须可追溯到原文证据，否则 Reviewer 无法后验验证。

### 11.4 与 Reporter 的集成

Reporter 应直接消费 Distiller 已经拆好的主张、证据、风险和催化，而不是重新猜测文章逻辑。

### 11.5 与 Evolver 的集成

Distiller 可以发现新维度，但不能直接把它写成正式规则；它只能输出候选和补充依据。

### 11.6 与 Orchestrator 的集成

Distiller 对外只需要暴露“接收任务、处理材料、发布结果、返回状态”这组稳定能力。

至于是否继续进入 Reviewer、Reporter 或 Evolver，应由 `ResearchOrchestrator` 与 `GateEvaluator` 决定。

---

## 12. 与全局运行时骨架的对齐

### 12.1 Blackboard 对齐

Distiller 必须保留足够的中间层，供 Reviewer、Reporter、Evolver 和 replay 消费。

至少要保留：

- 清洗后的文本
- 引用内容与作者新增观点的区分
- 逻辑块
- 候选维度
- 置信度说明

### 12.2 Mailbox 对齐

即便第一版不实现完整 Mailbox 运行时，Distiller 也必须预留“可被复核后打回补充”的语义空间。

这也意味着 Distiller 产物不能被视为天然真值，必须支持抽检、打回与人工修正。

### 12.3 ProposalStore 对齐

Distiller 发现的新维度、经验学习和修正规则只能进入候选层，不能直接改动正式方法论。

### 12.4 Typed Memory 对齐

Distiller 必须能沉淀自己的典型错误，例如：

- 把引用观点误判为作者观点
- 把情绪表达误判成有效因子
- 标的映射错误
- 时间范围缺失
- 因果链不完整

---

## 13. 验收标准

### 13.1 功能验收

- 能读取现有雪球抓取结果并完成蒸馏
- 能稳定输出逻辑块与维度候选
- 能区分主张、证据、风险和催化
- 能给出风险标记和置信度

### 13.2 集成验收

- 输出无需改字段语义即可接入检索、复核、报告与演化链路
- 不需要重构 Distiller 即可挂接 `ResearchOrchestrator`、`Blackboard`、`SessionHistory` 与 `ProposalStore`

### 13.3 质量验收

- 对短帖、长帖、回复链都能处理
- 不把引用内容与作者新增观点完全混为一谈
- 对低信息密度文本能降级处理，而不是强行造出高置信度维度
- 每条维度都能追溯到原文证据
- 长文本具备预筛路径，避免无差别全量蒸馏
- 低置信度蒸馏结果能被 Critic / Reviewer / 人工修正链路拦截

---

## 14. 审核时重点关注的问题

- Distiller 的职责边界是否足够清晰
- 逻辑块定义是否稳定
- 维度分类是否过粗或过细
- 与 Blackboard / Reviewer / Evolver 的衔接是否足够顺畅
- “双层调度 + 黑板协作”的方式是否适合蒸馏链路
- 是否接受将所有具体代码和框架落地统一移到实现方案文档中
