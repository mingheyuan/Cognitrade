# A股研究系统 代码实现与框架计划

> 状态说明（2026-07-01）
>
> 这份文档仍是实现层合同的重要入口，但其中前几节的“先做什么后做什么”保留了早期搭框架阶段的顺序描述。
> 当前工作树已经进入“执行层接入 + 冗余收口”，实际能力边界与本地回归状态请配合 `completion_matrix.md` 和 `README.md` 一起看，不要只按这里的首期顺序判断现状。

## 1. 文档职责

这份文档承接系统的具体实现层设计，统一放置：

- 代码目录结构
- 包与模块命名
- 协议模型
- runtime 文件结构
- 技术栈落位
- 各组件实现顺序
- 分阶段交付计划

文档边界如下：

- 逻辑主方案：[plan.md](./plan.md)
- 分阶段收口方案：[phase_plan.md](./phase_plan.md)
- Distiller 逻辑方案：[distiller_agent_plan.md](./distiller_agent_plan.md)
- Distiller Prompt 合同：[distiller_prompt_template_spec.md](./distiller_prompt_template_spec.md)
- Runtime Schema 合同：[runtime_schema_contract.md](./runtime_schema_contract.md)
- Nerya 借鉴参考：[nerya_reference_for_agent_system.md](./nerya_reference_for_agent_system.md)

---

## 2. 实现目标

当前实现层目标不是一次性做完整平台，而是先搭一套最小但可扩展的研究 runtime。

阶段优先级以 [phase_plan.md](./phase_plan.md) 为准。

当前默认顺序是：

1. 先完成协议与 runtime 骨架
2. 先跑通 `stock_check` 与 `sector_research` 的最小在线研究闭环
3. 再接入 Distiller 与 `distill_kol_logic`
4. 最后接入 Reviewer、ProposalStore、Evolver 与长期沉淀链路

在这个顺序下，首期运行时先具备四种能力：

1. 对话式入口转结构化任务
2. `ResearchOrchestrator` 调度单次研究运行
3. `Blackboard + SessionHistory + RunEvent` 的可观测运行过程
4. `Verified Snapshot + Retriever + Reporter + GateEvaluator` 约束最小研究输出

---

## 3. 选定技术栈

### 3.1 Python

主实现语言固定为 `Python`，负责：

- Agent runtime
- Orchestrator
- CLI
- Distiller / Retriever / Reviewer / Evolver / Reporter
- Qdrant / Mem0 适配
- workspace 文件读写

### 3.1.1 默认 LLM provider 与 model

首期默认固定采用：

- 默认 `provider`：`OpenAI API`
- 默认 `agent model`：`GPT-5.5`

首期默认策略：

- `Collector`、`Distiller`、`Retriever`、`Reviewer`、`Evolver`、`Reporter`、`Critic` 统一默认使用 `GPT-5.5`
- 运行时保留 `provider registry` 抽象，但首期不做多 provider 并行编排
- 不把不同 Agent 的模型分层作为首期前置任务，后续如需降本，再基于评测结果拆分

### 3.2 Instructor + Pydantic

结构化 LLM 输出固定采用 `Instructor + Pydantic`。

适用范围：

- Distiller 的逻辑块与维度对象
- Reviewer 的复核发现
- ProposalStore 的提案对象
- 入口层的意图与槽位抽取

选择原因：

- 强 schema 约束
- 便于和 runtime、Blackboard、Qdrant payload 对齐
- 便于测试与回归

### 3.3 taxonomy file

维度池第一版固定采用 `taxonomy file`，作为：

- 标准维度名称
- 类别体系
- 别名归并
- 生命周期状态
- 候选准入规则

的单一事实来源。

### 3.4 Qdrant client

检索层通过 `Qdrant client (Python)` 完成读写。

Qdrant 的定位是：

- 结构化证据层
- metadata-first 检索底座
- 逻辑块级索引层

首期默认检索策略：

- embedding model：`text-embedding-3-small`
- retrieval strategy：`metadata-first + dense semantic search`
- reranker：首期不启用
- 但 Retriever 必须预留第二阶段 rerank 接口或空插槽，保证后续可以在不改协议的情况下接入重排

### 3.4.1 默认 `market` vendor

首期默认固定采用：

- 默认 `market` 数据源：`AkShare`

首期默认策略：

- `Verified Snapshot` 的行情查询优先通过 `market_snapshot_skill -> AkShareAdapter` 完成
- 默认支持 A 股日线历史行情、成交额、涨跌幅与 `raw / qfq / hfq`
- 分钟级数据仅作为补充能力，不把长期 1 分钟历史作为首期强依赖
- 保留 `data vendor adapter` 抽象，后续可补 `TushareAdapter`

### 3.4.2 默认 `fundamental` vendor

首期默认固定采用：

- 默认 `fundamental` 数据源：`AkShare`

首期默认策略：

- `fundamental_snapshot_skill` 首期默认通过 `AkShare` 接入
- 首期先满足财务指标、报表字段和披露口径的最小 snapshot 合同
- 不为了后续可能更优的 vendor 提前引入多源财务治理复杂度
- 后续如果稳定性、口径一致性或覆盖成为瓶颈，再补 `TushareAdapter`

### 3.4.3 默认 `notice` vendor

首期默认固定采用：

- 默认 `notice` 数据源：`Cninfo`

首期默认策略：

- `notice_snapshot_skill` 首期默认通过 `Cninfo` 接入
- 首期优先满足公告标题、披露日期、公告链接、原文锚点和公告类型的最小 snapshot 合同
- 不把多公告源聚合当作首期前置能力
- 后续如遇覆盖缺口或个别交易所差异，再补 `ExchangeNoticeAdapter`

### 3.5 结构化输出 fallback

关键 Agent 默认优先走结构化输出，但实现上必须支持 graceful fallback：

- provider 支持结构化输出时，优先返回 `Pydantic` 对象
- provider 不支持或本次 structured call 失败时，允许退回自由文本
- 退回自由文本后，必须进入二次解析、降级结果或失败标记，不能静默污染下游结构化层

### 3.6 provider / data vendor 抽象

实现上建议保留两层抽象：

- `provider registry`
- `data vendor adapter`

目的不是一开始支持很多供应商，而是避免：

- LLM provider 细节散落在 Agent 代码里
- 数据源切换或 fallback 时需要重写业务逻辑

当前默认取值：

- `provider registry` 首期只要求注册 `OpenAI API`
- 默认模型固定为 `GPT-5.5`
- `data vendor adapter` 首期只要求注册 `AkShare` 的 `market` adapter
- `data vendor adapter` 首期默认也注册 `AkShare` 的 `fundamental` adapter
- `data vendor adapter` 首期默认注册 `Cninfo` 的 `notice` adapter
- 后续如需接入第二 provider，应保持 Prompt、schema、timeout、fallback 与评测入口的一致抽象

进一步固定一条实现约束：

- Agent 不直接调用 vendor adapter
- Agent 只调用 SkillRuntime 暴露的数据 Skill
- 数据 Skill 再通过 vendor adapter 获取外部数据，并统一转换为 `Verified Snapshot`
- vendor adapter 只作为 `skills/*_snapshot/skill.py` 的内部依赖，不作为 Agent 的公开接口

首期 vendor 选择原则：

- 谁更容易接入并先满足 Skill 合同，就优先用谁
- 不为了未来可能替换的 vendor，提前把首期架构做复杂

### 3.7 Agent Runtime 与 Skill Runtime 分层

实现层建议明确区分两种运行时：

- `agent runtime`
- `skill runtime`

其中：

- `agent runtime` 负责角色生命周期、状态、Blackboard 协作、事件产出、timeout 和 gate
- `skill runtime` 负责具体方法包执行，包括 Prompt、局部工具、输入输出校验和 fallback

Skill 可以被替换、版本化和扩展，但不能直接绕过 Agent runtime 写入关键运行状态。

对于数据类能力，推荐固定为：

- `market_snapshot_skill`
- `fundamental_snapshot_skill`
- `notice_snapshot_skill`

这些 Skill 负责：

- 入参标准化
- vendor 选择与 fallback
- 结果统一映射到 snapshot schema
- run 内缓存、超时、失败态控制

### 3.8 Local Skill First + MCP Optional Backend

当前实现层明确采用：

**Local Skill First, MCP Optional Backend**

第一版默认落地：

- `LocalSkillBackend`
- 本地 Skill Registry
- 本地 schema 校验
- 本地 fallback 控制

第一版明确不要求：

- 所有 Skill 都通过 MCP 暴露
- Agent 依赖 MCP 才能运行
- 在首期就引入跨进程 Skill 编排

后续适合引入 MCP 的场景：

- 跨进程工具或服务接入
- 不同语言实现 Skill
- 远程部署 Skill
- 第三方团队提供 Skill

因此推荐 SkillRuntime 设计成双 backend 结构：

- `LocalSkillBackend`
- `MCPBackend`

但 Phase 0 到 Phase 4 默认只要求 `LocalSkillBackend` 可用。

### 3.5 现有抓取代码的处理方式

现有抓取代码保留在 [scraper](D:\work_26_6_24\scraper) 下，短期不重构进主 runtime。

第一版通过适配层消费：

- `xueqiu_webbridge_scraper.py`
- 其输出目录中的 `statuses.jsonl`

---

## 4. 代码骨架建议

### 4.1 包结构

建议新增一个独立的系统包，而不是继续把运行时代码堆在 `scraper/` 下。

推荐结构：

```text
D:\work_26_6_24\
  research_system\
    __init__.py
    schemas\
      __init__.py
      protocol.py
      task_requests.py
      runtime.py
      distillation.py
      retrieval.py
      review.py
      proposals.py
    runtime\
      __init__.py
      orchestrator.py
      team_templates.py
      task_graph.py
      gates.py
      logger.py
      run_store.py
      checkpoint_store.py
      event_stream.py
      blackboard.py
      mailbox.py
      session_history.py
      proposal_store.py
    agents\
      __init__.py
      collector\
        __init__.py
        service.py
        adapters.py
      distiller\
        __init__.py
        service.py
        pipeline.py
        normalizer.py
        reducer.py
        segmenter.py
        extractor.py
        dimension_normalizer.py
        scorer.py
        session_writer.py
      retriever\
        __init__.py
        service.py
      reviewer\
        __init__.py
        service.py
      evolver\
        __init__.py
        service.py
      reporter\
        __init__.py
        service.py
      critic\
        __init__.py
        service.py
    adapters\
      __init__.py
      sources\
        __init__.py
        xueqiu_statuses.py
      storage\
        __init__.py
        qdrant_adapter.py
        mem0_adapter.py
      vendors\
        __init__.py
        akshare_market_adapter.py
        akshare_fundamental_adapter.py
        cninfo_notice_adapter.py
        exchange_notice_adapter.py
    skills\
      __init__.py
      market_snapshot\
        __init__.py
        skill.py
      fundamental_snapshot\
        __init__.py
        skill.py
      notice_snapshot\
        __init__.py
        skill.py
    taxonomy\
      __init__.py
      loader.py
      dimensions.yml
    config\
      retrieval_policy.yml
      methodology_manifest.yml
    .env.example
    services\
      __init__.py
      task_router.py
      progress_service.py
    cli\
      __init__.py
      app.py
```

### 4.2 为什么这样拆

- `schemas/` 承接全部 Pydantic 模型，避免字段漂移
- `runtime/` 承接编排、黑板、事件、proposal 等运行时骨架
- `agents/` 承接各角色 Agent 的业务逻辑
- `adapters/` 隔离抓取输出、Qdrant、Mem0 等外部边界
- `taxonomy/` 统一维护维度池
- `config/` 承接可版本化的运行策略与方法论配置
- `.env` / `.env.example` 承接环境变量与外部依赖接入配置
- `cli/` 作为本地入口

### 4.3 配置与运行环境约束

首期固定采用“两层配置”：

1. `.env`
2. `yaml`

#### `.env` 承载内容

`.env` 或等价环境变量只负责以下内容：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `QDRANT_URL`
- `QDRANT_API_KEY`
- `MEM0_API_KEY`
- `MEM0_BASE_URL`
- 其他 vendor token / endpoint

首期约束：

- 所有密钥、token、endpoint、环境差异都必须来自 `.env` 或运行环境变量
- 不允许把 API key、base URL、vendor token、外部服务端点硬编码进代码
- 仓库只保留 `.env.example`，不提交真实 `.env`

`.env.example` 首期至少应包含以下键：

- `OPENAI_API_KEY=`
- `OPENAI_BASE_URL=`
- `OPENAI_MODEL=GPT-5.5  # 可替换为 gpt-4o 或 gpt-4-turbo`
- `QDRANT_URL=`
- `QDRANT_API_KEY=`
- `QDRANT_COLLECTION_NAME=research_evidence`
- `MEM0_API_KEY=`
- `MEM0_BASE_URL=`
- `DEFAULT_TIMEZONE=Asia/Shanghai`
- `LOG_LEVEL=INFO`

#### `yaml` 承载内容

`yaml` 只负责可审计、可版本化、可提案演化的配置，例如：

- `taxonomy/dimensions.yml`
- `config/retrieval_policy.yml`
- `config/methodology_manifest.yml`

首期约束：

- 检索权重、时间窗口、方法论版本、维度池状态等必须进入 `yaml`
- 不允许把这些策略参数散落硬编码在 `Retriever`、`Reviewer`、`Evolver` 代码里
- `RetrievalWeightPatch`、`DimensionPoolPatch` 等 proposal 的写回目标必须是 `yaml` 文件或 `Mem0`，而不是代码常量

#### 统一加载层

实现上应至少有一层统一配置加载，例如：

- `runtime/settings.py`

职责：

- 从 `.env` 读取环境变量
- 加载 `yaml` 配置文件
- 做最小校验与默认值填充
- 向 runtime / adapters / skills 暴露统一 settings 对象

首期 `settings` 对象至少应暴露：

- `provider_name`
- `default_model`
- `openai_api_key`
- `openai_base_url`
- `qdrant_url`
- `qdrant_api_key`
- `qdrant_collection_name`
- `mem0_api_key`
- `mem0_base_url`
- `default_timezone`
- `log_level`
- `artifacts_retention_days`
- `max_artifacts_size_mb`
- `retrieval_policy_path`
- `methodology_manifest_path`
- `taxonomy_dimensions_path`

字段约束：

- `provider_name`
  - 首期默认固定为 `openai`
- `default_model`
  - 首期默认值为 `GPT-5.5`，但必须保持为可配置项
- `qdrant_collection_name`
  - 首期默认固定为 `research_evidence`
- `artifacts_retention_days`
  - 首期默认 `30`
- `max_artifacts_size_mb`
  - 首期默认 `1024`
- `retrieval_policy_path`
  - 默认指向 `workspace/config/retrieval_policy.yml`
- `methodology_manifest_path`
  - 默认指向 `workspace/config/methodology_manifest.yml`
- `taxonomy_dimensions_path`
  - 默认指向 `workspace/taxonomy/dimensions.yml`

建议实现上同时提供：

- `from_env()`
- `load_yaml_configs()`
- `validate()`

模型兼容性约束：

- 代码层不得依赖任何 `GPT-5.5` 特有 API 行为
- 只依赖通用的 chat / structured output 能力抽象
- 在不改业务代码的前提下，应允许通过 `OPENAI_MODEL` 切换到 `gpt-4o` 或 `gpt-4-turbo`

首期明确不允许：

- 在 Agent / Skill / Adapter 内部直接写死 provider 名称、API key、URL、collection 名称、权重或时间窗口
- 通过散落在各模块中的魔法数字控制检索、方法论或超时策略

#### 后续可移植性约束

首期不把 Docker 作为前置依赖，但必须显式保留后续容器化移植能力。

原则：

- 代码与配置分离
- `.env + yaml` 可直接映射到容器环境变量与挂载配置文件
- 本地路径、端口、workspace 根目录不应在代码里写死

后续适合新增：

- `Dockerfile`
- `.dockerignore`
- `docker-compose.yml` 或等价编排文件

这样可以在不改核心业务逻辑的前提下，把系统迁移到 Docker 运行环境。

---

## 5. runtime 工作区结构

建议把“代码目录”和“运行时工作区”分开。

### 5.1 工作区目录

```text
workspace/
  runs/
    <run_id>/
      request.json
      task_plan.json
      run_state.json
      checkpoint.json
      events.jsonl
      logs.jsonl
      blackboard.jsonl
      mailbox/
      artifacts/
      result.json
      summary.json
  memory/
    exports/
    cache/
    mem0_config.json
  proposals/
    pending/
    approved/
    rejected/
  taxonomy/
    dimensions.yml
  reports/
```

### 5.2 目录职责

- `runs/<run_id>/`
  - 单次任务的完整运行快照
- `events.jsonl`
  - 统一事件流，也是 run 过程真源
- `logs.jsonl`
  - 运行日志流，服务错误定位与调试，不替代 `RunEvent`
- `blackboard.jsonl`
  - 单次运行共享事实
- `mailbox/`
  - 定向反馈或补充请求
- `artifacts/`
  - 大文件、原始导出、调试材料和中间副产物
- `result.json`
  - 结构化最终结果
- `summary.json`
  - 面向人读的运行摘要
- `memory/`
  - Mem0 运行配置、导出缓存与本地辅助材料
- `proposals/`
  - 演化提案状态流
- `checkpoint.json`
  - 当前 run 的最近成功阶段、活动节点和恢复信息

### 5.3 第一版建议

第一版以 `runs/<run_id>/` 为中心，不先做跨 run 的复杂聚合层。

这样实现简单，也更利于排查与 replay。

### 5.4 首期持久化组合

当前实现层固定采用以下首期持久化组合：

- `Blackboard`：`runs/<run_id>/blackboard.jsonl`
- `RunEvent`：`runs/<run_id>/events.jsonl`
- 日志：`runs/<run_id>/logs.jsonl`
- `ProposalStore`：`workspace/proposals/<status>/<proposal_id>.json`
- 长期记忆：`Mem0`
- 结构化证据检索：`Qdrant`

这样做的原因是：

- run 内事件流与共享事实优先保证可回放、可审计、可人工排查
- 提案对象数量较小，适合首期用目录加单文件承接人工审核流
- 长期记忆与结构化证据不再做本地临时过渡，直接进入 `Mem0` 与 `Qdrant`

### 5.5 首期 run 清理策略

首期默认采用以下保留规则：

- `events.jsonl`：永久保留
- `logs.jsonl`：永久保留
- `blackboard.jsonl`：永久保留
- `result.json`：永久保留
- `summary.json`：永久保留
- `checkpoint.json`：run 成功完成后默认删除；失败或超时时保留
- `artifacts/`：首期默认保留，但受保留期和体积上限约束

这样做的原因是：

- replay、审计和问题排查依赖事件流与黑板真源
- 成功 run 的 checkpoint 在首期默认没有长期价值，保留反而容易造成恢复语义混乱
- `artifacts/` 的价值与体积差异很大，因此不做激进自动清理，但必须有保守 retention 和体积上限

### 5.6 `artifacts/` 边界

首期固定以下边界：

- `artifacts/` 只存不适合直接塞进事件流或黑板的大文件与副产物
- 例如：原始网页导出、长文本缓存、调试日志、模型原始响应备份、截图、临时分析文件
- 关键结构化事实不得只存在于 `artifacts/`
- 需要下游 Agent 消费的内容，必须写入 `Blackboard`
- 需要解释运行决策的过程，必须写入 `RunEvent`
- 需要作为最终输出交付的内容，必须写入 `result.json` 或 `summary.json`

首期进一步固定：

- `artifacts/` 默认路径保持为 `runs/<run_id>/artifacts/`
- `artifacts_retention_days` 默认 `30`
- `max_artifacts_size_mb` 默认 `1024`
- 超过 retention 或体积上限时，应允许用户手动清理，或做 run 级归档压缩

### 5.7 日志管理

首期新增独立日志层，固定规则如下：

- `RunEvent` 是业务过程真源
- `logs.jsonl` 是排错与调试辅助
- 日志不能替代事件、黑板或最终结果

日志建议至少包含：

- `log_id`
- `run_id`
- `level`
- `component`
- `message`
- `context`
- `created_at`

日志级别首期固定为：

- `DEBUG`
- `INFO`
- `WARNING`
- `ERROR`

建议文件：

- `runtime/logger.py`

---

## 6. 统一协议模型

### 6.0 ID 生成策略

第一版统一要求：

- `request_id`
- `run_id`
- `entry_id`
- `message_id`
- `gate_id`
- `event_id`
- `proposal_id`

全部使用 `ULID` 生成。

实现约束：

- 不在 ID 中编码类型、阶段、状态或 agent 名称
- 文件名直接复用对应对象 ID
- 事件先后顺序以 `events.jsonl` 追加顺序和 `created_at` 为准，不依赖 `event_id` 排序

### 6.1 Request Envelope

入口层统一使用一个请求外壳模型，承载：

- `request_id`
- `intent`
- `user_query`
- `conversation_context`
- `slots`
- `constraints`
- `clarification_status`
- `created_at`

建议模型文件：

- `schemas/protocol.py`

### 6.2 Intent Request Models

每个意图单独定义请求模型：

- `StockCheckRequest`
- `SectorResearchRequest`
- `DistillKolLogicRequest`
- `ClarifyRequest`
- `ReviewThesisRequest`
- `MethodologyEvolutionRequest`

建议模型文件：

- `schemas/task_requests.py`

### 6.3 Response Envelope

统一响应外壳至少承载：

- `request_id`
- `run_id`
- `intent`
- `status`
- `message`
- `clarification`
- `progress`
- `result`
- `artifacts`
- `errors`
- `created_at`
- `updated_at`

其中 `progress` 首期固定为结构化对象，而不是自由文本，至少包含：

- `current_phase`
- `active_agent`
- `latest_event`
- `status_message`
- `percent`
- `updated_at`

字段语义约束：

- `current_phase`
  - 当前所处的顶层 phase 标识，例如 `clarify`、`collect`、`retrieve`、`report`
- `active_agent`
  - 当前正在执行或最近一次产出事件的 agent id；空闲时允许为 `null`
- `latest_event`
  - 最近一次可展示给用户的事件类型或事件摘要 key
- `status_message`
  - 面向 CLI / UI 的简短人类可读状态说明，不写原始 chain-of-thought
- `percent`
  - `0-100` 的整数；首期允许粗粒度估算，不要求精确进度条
- `updated_at`
  - 最近一次 progress 刷新时间

状态枚举固定为：

- `accepted`
- `needs_clarification`
- `running`
- `completed`
- `cancelled`
- `failed`
- `timed_out`

### 6.4 RunEvent

统一事件模型至少承载：

- `event_id`
- `request_id`
- `run_id`
- `intent`
- `event_type`
- `phase`
- `agent`
- `message`
- `payload`
- `artifacts`
- `created_at`

标准事件类型固定为：

- `request_received`
- `intent_identified`
- `clarification_requested`
- `clarification_resolved`
- `run_accepted`
- `phase_started`
- `phase_completed`
- `agent_started`
- `agent_completed`
- `blackboard_entry_added`
- `mailbox_message_sent`
- `review_requested`
- `review_completed`
- `proposal_created`
- `run_completed`
- `run_cancelled`
- `run_failed`
- `run_resumed`
- `run_timed_out`

### 6.5 Blackboard 与 Mailbox 模型

建议定义：

- `BlackboardEntry`
- `MailboxMessage`
- `RunStateSnapshot`
- `GateVerdict`

建议模型文件：

- `schemas/runtime.py`

#### BlackboardEntry 最小字段

`BlackboardEntry` 第一版建议固定以下字段：

- `entry_id`
- `run_id`
- `request_id`
- `kind`
- `agent`
- `phase`
- `status`
- `payload`
- `source_refs`
- `confidence`
- `risk_flags`
- `created_at`

其中：

- `kind` 用于区分共享事实类型
- `payload` 承载该类事实的结构化正文
- `source_refs` 统一指向原始材料、snapshot 或上游 entry
- `status` 用于表达 `active`、`superseded`、`needs_review`

#### Blackboard kind 建议首批固定值

第一版建议先固定以下 `kind`，避免后续 Agent 各自发明类型：

- `source_record`
- `verified_snapshot`
- `logic_block`
- `distilled_dimension`
- `retrieved_evidence`
- `report_fragment`
- `review_finding`
- `proposal_draft`

#### Blackboard 设计原则

第一版 Blackboard 必须满足：

- append-only，不做原地覆盖
- 允许后续 entry supersede 旧 entry
- 所有关键结论都能追溯到 `source_refs`
- 允许 Reviewer、Reporter、Evolver 读取同一份共享事实，而不是复制私有数据
- 每个 run 固定使用单个 `blackboard.jsonl`
- 首期不按 `kind`、`phase` 或 `agent` 分文件

Skill 不直接拥有 Blackboard 的写权限；应由 Agent 统一提交或代理写入，防止方法层绕过运行态边界。

#### RunStateSnapshot 最小字段

`RunStateSnapshot` 第一版建议固定以下字段：

- `run_id`
- `request_id`
- `intent`
- `status`
- `current_phase`
- `active_agent`
- `pending_nodes`
- `completed_nodes`
- `last_event_id`
- `error_summary`
- `updated_at`

其中：

- `status` 对应 Orchestrator 当前状态
- `pending_nodes` 与 `completed_nodes` 用于恢复和回放
- `error_summary` 在失败或超时时承载最新错误摘要

### 6.6 Distiller 专属模型

建议定义：

- `DistillSourceRecord`
- `DistillationSummary`
- `LogicBlock`
- `DistilledDimension`
- `DistillationResult`

建议模型文件：

- `schemas/distillation.py`

### 6.7 Proposal 模型

建议统一提案对象：

- `ProposalDraft`
- `DimensionPoolPatch`
- `PromptPatchProposal`
- `RetrievalWeightPatch`
- `MemoryLearningUpdate`

每个 proposal 至少应附带：

- `sample_size`
- `benchmark_scope`
- `before_metrics`
- `after_metrics`
- `evidence_summary`

建议模型文件：

- `schemas/proposals.py`

首期正式允许的 `proposal_type` 固定为：

- `DimensionPoolPatch`
- `RetrievalWeightPatch`
- `MemoryLearningUpdate`

首期仅保留讨论、不进入自动写回链路的类型：

- `PromptPatchProposal`

`PromptPatchProposal` 首期处理规则：

- 允许创建
- 允许进入 `ProposalStore`
- 允许经历 `pending / approved / rejected` 状态流
- 即使 `approved`，也只生成人工待办或人工审核记录
- 不直接写回 prompt 模板文件
- 不直接改变运行时默认方法论对象

---

## 7. runtime 核心组件实现计划

### 7.1 ResearchOrchestrator

建议职责：

- 接收结构化请求
- 进行 1 到 2 轮澄清
- 选择 `TeamTemplate`
- 生成 `TaskGraph`
- 驱动运行状态流转
- 产出统一响应与事件
- 管理 timeout、熔断与失败降级

建议文件：

- `runtime/orchestrator.py`

#### ResearchOrchestrator 状态机建议

第一版不要把 Orchestrator 写成自由拼接的流程函数，建议显式设计状态机。

最小状态集合：

- `received`
- `identifying_intent`
- `needs_clarification`
- `ready`
- `running`
- `waiting_external`
- `gated`
- `completed`
- `cancelled`
- `failed`
- `timed_out`

关键状态说明：

- `received`
  - 请求已接收，尚未开始路由
- `identifying_intent`
  - 正在识别意图和补槽位
- `needs_clarification`
  - 缺少关键输入，等待追问结果
- `ready`
  - 任务信息完整，可开始执行
- `running`
  - 某一阶段正在调度 Agent
- `waiting_external`
  - 等待外部依赖，例如数据源或长窗口复核
- `gated`
  - 当前阶段完成，等待 GateEvaluator 判断
- `completed`
  - 成功结束
- `cancelled`
  - 用户主动中断
- `failed`
  - 运行失败
- `timed_out`
  - 触发超时熔断

#### 状态转移建议

第一版至少明确以下主转移：

- `received -> identifying_intent`
- `identifying_intent -> needs_clarification`
- `identifying_intent -> ready`
- `needs_clarification -> identifying_intent`
- `ready -> running`
- `running -> waiting_external`
- `running -> gated`
- `waiting_external -> running`
- `gated -> running`
- `gated -> completed`
- `running -> cancelled`
- `waiting_external -> cancelled`
- `running -> failed`
- `running -> timed_out`
- `waiting_external -> timed_out`

#### 状态转移表

| From | Trigger | To | 说明 |
| --- | --- | --- | --- |
| `received` | 创建 run | `identifying_intent` | 请求进入路由 |
| `identifying_intent` | 信息不足 | `needs_clarification` | 触发 `clarify` |
| `identifying_intent` | 意图明确且槽位完整 | `ready` | 可进入执行 |
| `needs_clarification` | 收到追问回复 | `identifying_intent` | 重新判定意图 |
| `ready` | 生成任务图完成 | `running` | 开始调度节点 |
| `running` | 外部依赖等待 | `waiting_external` | 等待数据源或窗口 |
| `waiting_external` | 外部依赖可继续 | `running` | 恢复执行 |
| `running` | 阶段节点完成 | `gated` | 等待 gate 判定 |
| `gated` | gate 放行下一阶段 | `running` | 继续执行 |
| `gated` | gate 判定流程完成 | `completed` | 正常收束 |
| `running` | 用户中断 | `cancelled` | 主动取消 |
| `waiting_external` | 用户中断 | `cancelled` | 主动取消 |
| `running` | 不可恢复错误 | `failed` | 失败结束 |
| `running` | 超时阈值命中 | `timed_out` | 熔断结束 |
| `waiting_external` | 超时阈值命中 | `timed_out` | 外部等待超时 |

#### 状态机设计原则

第一版 Orchestrator 状态机必须满足：

- 每次状态切换都发出 `RunEvent`
- cancelled 必须与 failed、timed_out 区分
- timeout 不能伪装成普通失败
- clarification 不能绕过 `identifying_intent`
- gate 判断必须显式进入 `gated`，不能藏在 Agent 内部
- 支持从 `SessionHistory` 回放状态流

### 7.1A AgentRuntime

建议新增统一 `AgentRuntime`，负责：

- 加载 Agent 配置
- 装配 Skill
- 注入 Blackboard / EventStream / Mailbox / Gate
- 统一 timeout、重试和失败降级
- 统一将 Skill 输出转成 Agent 级产物

### 7.1B SkillRuntime

建议新增统一 `SkillRuntime`，负责：

- Skill 注册与发现
- Skill 版本选择
- 输入 schema 校验
- 输出 schema 校验
- Skill fallback 路径

同时建议 SkillRuntime 预留 backend 抽象：

- `LocalSkillBackend`
- `MCPBackend`

其中：

- 第一版只要求 `LocalSkillBackend`
- `MCPBackend` 作为后续扩展能力预留，不进入首期阻塞项

### 7.2 TeamTemplate

建议先固化 3 个模板：

- `stock_check_team`
- `sector_research_team`
- `methodology_evolution_team`

建议文件：

- `runtime/team_templates.py`

### 7.3 TaskGraph

建议支持：

- 顺序依赖
- 可选并发
- 条件分支
- gate 后续跳转

建议文件：

- `runtime/task_graph.py`

### 7.4 GateEvaluator

第一版建议至少支持：

- `EvidenceGate`
- `QualityGate`
- `ReviewGate`
- `EvolutionGate`
- `VerifiedFactGate`
- `DistillationQualityGate`

建议文件：

- `runtime/gates.py`

### 7.5 Blackboard

建议能力：

- append-only 写入
- 按 kind 过滤
- 按 agent 过滤
- 按 run_id 过滤
- 导出 run 级快照
- 首期固定为单个 `blackboard.jsonl`，过滤与视图在读取层完成

建议文件：

- `runtime/blackboard.py`

### 7.6 Mailbox

建议能力：

- run 内定向消息
- per-agent 收件箱
- FIFO 读取
- 消息状态标记

建议文件：

- `runtime/mailbox.py`

### 7.7 SessionHistory

建议能力：

- 记录 request、events、blackboard、result
- 支持 replay
- 支持失败回放
- 与 checkpoint 恢复信息联动
- 明确 `events.jsonl` 是过程真源
- `result.json` 与 `summary.json` 作为派生产物输出

建议文件：

- `runtime/session_history.py`

### 7.8 ProposalStore

建议能力：

- 存 pending / approved / rejected
- 保留 before / after 快照
- 为后续人工审核预留入口
- Agent 只能写入 draft 或 pending，不允许直接写 approved
- 状态迁移必须显式记录审核人、审核时间和审核理由

建议文件：

- `runtime/proposal_store.py`

#### Proposal 文件格式

首期固定采用：

- `workspace/proposals/pending/<proposal_id>.json`
- `workspace/proposals/approved/<proposal_id>.json`
- `workspace/proposals/rejected/<proposal_id>.json`

每个 proposal 第一版至少应包含：

- `proposal_id`
- `proposal_type`
- `status`
- `title`
- `summary`
- `created_from_run_id`
- `created_from_review_id`
- `author_agent`
- `sample_size`
- `benchmark_scope`
- `before_metrics`
- `after_metrics`
- `evidence_summary`
- `related_entry_ids`
- `reviewer`
- `reviewed_at`
- `review_reason`
- `created_at`
- `updated_at`

#### 状态迁移规则

首期固定以下状态迁移：

- `pending -> approved`
- `pending -> rejected`

首期不支持：

- Agent 直接创建 `approved`
- `approved -> pending`
- `rejected -> pending`

每次状态迁移都必须：

- 产出 `RunEvent` 或审核事件
- 更新 proposal 文件中的审核字段
- 保留原始 `proposal_id`

### 7.9 EventStream

建议能力：

- 统一产出 `RunEvent`
- CLI 订阅
- Web UI / SSE / polling 后续可复用

同时应保留超时、熔断、重试建议等运行态事件。

### 7.10 CheckpointStore

建议能力：

- 记录最近成功 phase
- 记录最近成功节点
- 记录可恢复的输入与局部状态引用
- run 成功完成后自动清理或归档 checkpoint

建议文件：

- `runtime/checkpoint_store.py`

建议文件：

- `runtime/event_stream.py`

---

## 8. Agent 实现计划

### 8.0 Agent 框架约束

第一版不要求所有 Agent 统一使用单一范式。

建议固定三类框架：

- `WorkflowAgent`
- `ReActAgent`
- `PlanExecuteAgent`

选择原则：

- 固定阶段、强 schema、强质控任务优先 `WorkflowAgent`
- 探索式工具调用任务优先 `ReActAgent`
- 多步验证、比较、提案任务优先 `PlanExecuteAgent`

### 8.1 Collector

第一版不重写现有抓取链路，先做适配器。

最小能力：

- 读取 `statuses.jsonl`
- 转换为 `DistillSourceRecord`
- 把抓取目录组织成可被 `ResearchOrchestrator` 调度的输入材料

建议框架：

- `ReActAgent`

### 8.2 Distiller

Distiller 是第一批应优先实现的核心 Agent。

建议框架：

- `WorkflowAgent`

建议内部模块：

- `normalizer.py`
- `trimmer.py`
- `reducer.py`
- `segmenter.py`
- `extractor.py`
- `dimension_normalizer.py`
- `scorer.py`
- `session_writer.py`
- `pipeline.py`

逻辑顺序：

1. Input normalization
2. Lightweight trimming
3. Noise reduction
4. Logic segmentation
5. Dimension extraction
6. Dimension normalization
7. Confidence scoring
8. Session writing

LLM 结构化输出通过 `Instructor + Pydantic` 实现。

同时建议保留：

- structured output 失败时的降级路径
- 降级结果进入 `needs_review` 或低置信度输出，而不是静默继续
- Distiller 输出后的 Critic / Reviewer 抽检入口
- 人工修正接口预留

### 8.3 Retriever

第一版目标：

- 先消费 Distiller 的逻辑块和 metadata
- 支持按标的、行业、维度标签和时间范围过滤
- 为 Reporter 提供引用证据
- 固定保留 `filter -> dense retrieve -> optional rerank -> evidence handoff` 四段式流程

建议框架：

- `WorkflowAgent`

### 8.4 Reviewer

第一版目标：

- 针对历史逻辑块和维度进行后验验证
- 输出 `review_finding`
- 为 Evolver 提供候选输入

对价格表现类结论，Reviewer 应优先采用相对基准归因：

- 个股对比所属板块或主题基准
- 板块或主题对比宽基指数
- 无法确定基准时，输出“无法可靠归因”，而不是直接判定维度有效

### 8.4A `review_policy.yml` 建议

首期建议新增：

- `workspace/config/review_policy.yml`

最小字段至少包含：

- `policy_id`
- `version`
- `benchmark_resolution`
- `default_broad_index`
- `updated_at`

字段约束：

- `benchmark_resolution`
  - 首期固定受控枚举：`symbol`、`sector`、`index`、`custom`
- `default_broad_index`
  - 当 sector 级基准无法可靠解析时的回退指数，例如 `HS300`

首期默认策略：

- 默认 `benchmark_resolution = sector`
- 如果 sector 级基准缺失或数据不可用，允许回退到 `index`
- `custom` 只在用户显式指定时启用

同时应优先消费：

- verified market snapshot
- verified fundamental snapshot
- verified notice snapshot

建议框架：

- `PlanExecuteAgent`

### 8.5 Evolver

第一版目标：

- 不直接应用更新
- 只生成候选提案

建议框架：

- `PlanExecuteAgent`

### 8.6 Reporter

第一版目标：

- 直接消费 Blackboard 和检索结果
- 输出结构化研究摘要
- 保留证据引用

建议框架：

- `WorkflowAgent`

### 8.7 Critic

第一版可以是轻量角色，重点检查：

- 证据不足
- 因果跳跃
- 蒸馏误读
- 报告幻觉

在 Phase 2 默认承担 Distiller 抽检入口，阻止低质量逻辑块直接进入 Reviewer 统计链路。

建议框架：

- `WorkflowAgent`

### 8.8 Skill 合同

所有 Skill 第一版至少应声明：

- `skill_id`
- `version`
- `input_schema`
- `output_schema`
- `allowed_tools`
- `side_effect_policy`
- `validator`
- `fallback_policy`

其中数据类 Skill 还应额外声明：

- `supported_snapshot_type`
- `allowed_vendors`
- `default_vendor`
- `cache_policy`
- `timeout_policy`
- `failure_schema`

Skill 设计原则：

- Skill 不直接控制 run 状态
- Skill 不直接决定 gate
- Skill 不直接写入长期 memory
- Skill 通过 Agent 输出进入 Blackboard
- Skill 不要求首期必须运行在 MCP 上
- 数据 Skill 不直接暴露 vendor 原始返回值，必须统一映射成 `Verified Snapshot`

---

## 9. taxonomy 实现计划

### 9.1 taxonomy 文件位置

建议位置：

- `research_system/taxonomy/dimensions.yml`
- `workspace/taxonomy/dimensions.yml`

其中：

- 代码包内版本用于默认模板
- workspace 版本用于实际运行和后续演化

### 9.1A 首期方法论对象写回目标

首期固定以下写回映射：

- `DimensionPoolPatch`
  - 写回目标：`workspace/taxonomy/dimensions.yml`
- `RetrievalWeightPatch`
  - 写回目标：`workspace/config/retrieval_policy.yml`
- `MemoryLearningUpdate`
  - 写回目标：`Mem0`

首期不允许直接写回：

- `distiller_prompt_template_spec.md`
- 主 Prompt 模板文件
- 自由规则文本文件

### 9.2 taxonomy 文件承载内容

至少包含：

- 标准维度 ID
- 标准名称
- 分类
- aliases
- 状态
- 描述
- 准入与演化元数据

### 9.3 taxonomy 加载器

建议能力：

- 加载与校验
- alias 归并
- 状态判断

### 9.3A `retrieval_policy.yml` 最小字段

首期 `workspace/config/retrieval_policy.yml` 至少包含：

- `policy_id`
- `version`
- `embedding_model`
- `vector_size`
- `metadata_filters_enabled`
- `dense_top_k`
- `final_top_k`
- `recency_window_days`
- `rerank_enabled`
- `rerank_model`
- `rerank_top_n`
- `score_weights`
- `updated_at`

字段语义约束：

- `embedding_model`
  - 首期默认：`text-embedding-3-small`
- `vector_size`
  - 与当前 embedding 维度保持一致
- `metadata_filters_enabled`
  - 首期默认：`true`
- `dense_top_k`
  - dense 检索候选数
- `final_top_k`
  - 最终交给 Reporter 或下游的证据数
- `recency_window_days`
  - 首期 recency 线性衰减窗口天数；建议默认 `30`
- `rerank_enabled`
  - 首期默认：`false`
- `rerank_model`
  - 首期可为空，但字段必须预留
- `rerank_top_n`
  - 首期可为空，但字段必须预留
- `score_weights`
  - 首期固定为结构化对象，不使用自由 key-value map

### 9.3B `score_weights` 首期结构

首期 `score_weights` 固定只允许以下字段：

- `metadata_score_weight`
- `semantic_score_weight`
- `recency_score_weight`
- `dimension_match_weight`

字段语义约束：

- `metadata_score_weight`
  - 非硬过滤 metadata 命中分的权重
- `semantic_score_weight`
  - dense embedding 召回得分权重
- `recency_score_weight`
  - 时间新鲜度权重
- `dimension_match_weight`
  - 维度标签匹配权重

### 9.3C 首期检索打分合同

首期固定把 Retriever 分成两层：

1. 硬过滤
2. 软打分

#### 硬过滤

硬过滤只负责裁剪候选，不参与最终分值加权。

首期允许进入硬过滤的字段：

- `record_type`
- `source_type`
- `symbol`
- `sector`
- `event_date`
- `run_id`

首期默认不把 `dimension_tags` 放入硬过滤必选条件，避免 Distiller 标签噪声直接误杀召回结果。

#### 软打分

对通过硬过滤的候选，首期固定计算以下四个基础分，且都必须归一化到 `[0, 1]`：

- `metadata_score`
- `semantic_score`
- `recency_score`
- `dimension_match_score`

最终总分固定为：

`final_score = metadata_score_weight * metadata_score + semantic_score_weight * semantic_score + recency_score_weight * recency_score + dimension_match_weight * dimension_match_score`

#### 四个基础分的首期定义

`semantic_score`

- 来自 dense embedding 检索分
- 在候选集内部归一化到 `[0, 1]`

`recency_score`

- 依据 `event_date` 与查询参考时间的距离计算
- 首期固定使用线性衰减，不允许实现侧自由改成指数或其他函数
- 越新分越高，越旧分越低
- 固定公式：

`recency_score = max(0, 1 - delta_days / recency_window_days)`

其中：

- `delta_days`
  - `event_date` 与查询参考时间之间的自然日差值绝对值
- `recency_window_days`
  - 从 `retrieval_policy.yml` 读取的正整数窗口；首期建议默认 `30`

补充约束：

- 当 `delta_days = 0` 时，`recency_score = 1.0`
- 当 `delta_days >= recency_window_days` 时，`recency_score = 0.0`
- 查询参考时间首期默认取 run 创建时间或显式查询截止时间

`metadata_score`

- 只对未作为硬过滤条件使用的 metadata 命中做加分
- 首期允许纳入：`direction`、可选的 `source_type` 偏好、可选的 `record_type` 偏好
- 已经作为硬过滤使用的字段，不允许再次重复记满分，避免双重计算
- 首期固定公式：

`metadata_score = matched_optional_metadata_fields / max(1, total_optional_metadata_fields_in_query)`

其中：

- `optional_metadata_fields`
  - 首期固定只允许：`direction`、`source_type`、`record_type`
- `matched_optional_metadata_fields`
  - 查询中显式提供且候选命中的可选 metadata 字段数
- `total_optional_metadata_fields_in_query`
  - 查询中实际出现、且未被用作硬过滤条件的可选 metadata 字段数

补充约束：

- 如果查询没有任何可选 metadata 字段，则 `metadata_score = 0.0`
- 已作为硬过滤条件使用的字段，不计入 `metadata_score`

`dimension_match_score`

- 首期固定使用“查询维度覆盖率”，不使用 Jaccard，也不使用完全包含布尔值
- 计算前必须先经过 taxonomy alias 归一化
- 固定公式：

`dimension_match_score = |intersection(query_dimension_tags, candidate_dimension_tags)| / max(1, |query_dimension_tags|)`

含义：

- 如果候选覆盖了查询中的全部维度标签，则得 `1.0`
- 如果只覆盖一部分，则按覆盖比例计分
- 如果查询没有显式维度标签，则 `dimension_match_score` 首期默认记为 `0.0`

#### RetrievalWeightPatch 的约束

首期 `RetrievalWeightPatch` 只允许调整权重值，不允许：

- 修改四个基础分的定义
- 修改 `dimension_match_score` 的计算公式
- 修改哪些字段属于硬过滤、哪些字段属于软打分

如果要修改这些规则，应进入新的 proposal 类型或新的 retrieval policy version，而不是伪装成单纯权重调整。

首期约束：

- 所有权重使用 `0.0 - 1.0` 浮点表示
- 首期允许部分字段为 `0.0`
- 不允许在首期自由扩展新字段

### 9.4 首期方法论版本表示

首期不做复杂版本管理服务，固定采用一个轻量 manifest：

- `workspace/config/methodology_manifest.yml`

第一版至少记录：

- `active_methodology_id`
- `taxonomy_version`
- `retrieval_policy_version`
- `memory_snapshot_ref`
- `approved_proposal_ids`
- `updated_at`

其作用是：

- 明确当前运行时到底加载哪一版方法论对象
- 让 approved proposal 的影响可追溯
- 避免通过隐式文件覆盖来推断“当前方法论”

### 9.4A `methodology_manifest.yml` 最小字段

首期 `workspace/config/methodology_manifest.yml` 至少包含：

- `active_methodology_id`
- `version`
- `status`
- `taxonomy_version`
- `retrieval_policy_version`
- `memory_snapshot_ref`
- `approved_proposal_ids`
- `activated_at`
- `updated_at`
- `notes`

字段语义约束：

- `active_methodology_id`
  - 当前激活的方法论标识
- `version`
  - manifest 自身版本
- `status`
  - 首期至少支持 `active`
- `taxonomy_version`
  - 当前对应的 taxonomy 版本号或 hash
- `retrieval_policy_version`
  - 当前对应的 retrieval policy 版本号或 hash
- `memory_snapshot_ref`
  - 一个结构化对象，至少包含 `snapshot_id`、`memory_ids`、`approved_proposal_ids`、`generated_at`
- `approved_proposal_ids`
  - 当前方法论由哪些 approved proposal 构成

首期要求 `memory_snapshot_ref` 是结构化对象，不能只是自由文本备注。
- active / pending / deprecated 过滤

建议文件：

- `taxonomy/loader.py`

---

## 10. Qdrant 接入计划

### 10.1 第一版目标

第一版先完成“与 Qdrant 兼容的 payload 结构”和“本地侧模型对齐”，不强依赖在线实例。

### 10.1A Collection 与命名约束

首期固定使用单 collection 策略，避免在 Phase 1 就按对象类型过度拆库。

- collection name：`research_evidence`
- collection scope：单工作区单集合
- 首期不按 `logic_block` / `distilled_dimension` / `review_finding` 拆成多 collection
- 通过 payload 中的 `record_type`、`source_type`、`symbol`、`dimension_tags` 等字段做过滤

后续如果检索规模、schema 演化或性能成为瓶颈，再考虑分 collection，但不作为首期前置决策。

### 10.2 写入对象

建议每个逻辑块写入时至少携带：

- 文档来源信息
- 标的 / 行业信息
- 蒸馏维度标签
- 方向
- 置信度
- 时间信息

### 10.2A 首期 payload 最小固定字段

第一版写入 `research_evidence` collection 的 payload 至少固定包含：

- `entry_id`
- `record_type`
- `source_type`
- `source_id`
- `run_id`
- `author_agent`
- `symbol`
- `sector`
- `title`
- `content_summary`
- `dimension_tags`
- `direction`
- `confidence`
- `event_date`
- `created_at`
- `source_refs`

字段语义约束：

- `entry_id`
  - 与 Blackboard / run 内对象对齐的主引用 id
- `record_type`
  - 首期固定使用受控枚举，例如 `logic_block`、`distilled_dimension`、`retrieved_evidence`
- `source_type`
  - 材料来源类型，例如 `kol_post`、`notice`、`news`、`manual_note`
- `source_id`
  - 外部材料或内部源对象 id
- `symbol`
  - 首期允许单值字符串；没有标的时可为 `null`
- `sector`
  - 首期允许单值字符串；没有行业/主题时可为 `null`
- `dimension_tags`
  - 受控标签列表，不允许把整段自然语言塞进标签
- `direction`
  - 首期允许 `bullish`、`bearish`、`neutral`、`unknown`
- `source_refs`
  - 必须能回指到原文锚点、公告链接或 Blackboard entry

### 10.2B 首期 metadata filter 固定字段

Retriever 第一版允许依赖以下固定 metadata filter 字段：

- `record_type`
- `source_type`
- `symbol`
- `sector`
- `dimension_tags`
- `direction`
- `event_date`
- `run_id`

首期不允许 Retriever 依赖临时字段或自由拼接 payload key 做过滤。

补充边界：

- metadata filter 属于硬过滤层，只负责裁剪候选
- `metadata_score` 属于软打分层，只对未作为硬过滤条件使用的 metadata 命中加分
- 同一字段首期不允许既作为硬过滤强约束，又在软打分里重复记满分

### 10.3 适配层

建议文件：

- `adapters/storage/qdrant_adapter.py`

适配层职责：

- 把 `LogicBlock` 转成 Qdrant payload
- 统一读写接口
- 避免在 Agent 内部自由拼接字典

第一版额外约束：

- 所有写入统一通过 `qdrant_adapter.py`
- Adapter 负责 collection name、payload 映射、vector size 一致性校验
- Agent / Skill 不直接拼接 Qdrant filter 对象
- metadata filter 由 Retriever 或 adapter 层统一构造

同时建议固定 Blackboard entry kind，至少覆盖：

- `source_record`
- `verified_snapshot`
- `logic_block`
- `distilled_dimension`
- `retrieved_evidence`
- `review_finding`
- `proposal_draft`

---

## 10.1 Verified Snapshot Skill 计划

### 10.1 目标

为所有 exact claim 提供确定性事实源，降低 Agent 对价格、指标、财务和公告口径的脑补空间。

### 10.2 第一版建议 Skill

- `market_snapshot_skill`
- `fundamental_snapshot_skill`
- `notice_snapshot_skill`

### 10.3 接入原则

- Reporter 不直接制造 exact claim
- Distiller 主要负责原文映射，不负责凭空给出精确数字
- Reviewer 和 Critic 在发现精确结论时，优先回查 verified snapshot
- Agent 不直接调用 vendor API，只调用统一 snapshot skill
- snapshot skill 内部通过 vendor adapter 取数，并统一返回 `Verified Snapshot`

第一版还应至少考虑：

- run 内缓存
- 超时控制
- 重试与降级
- 明确失败态返回

### 10.3A 外部依赖降级矩阵

首期应把以下依赖级降级行为固定成实现约束：

#### LLM / structured output

- structured output 成功
  - 返回标准 `Pydantic` 对象
- structured output 调用失败，但自由文本成功
  - 进入二次解析；若解析成功，结果标记为降级产物
  - 若解析失败，返回失败态或 `needs_review`
- LLM 全部失败
  - 当前 Agent 进入失败或超时分支，不允许伪造结构化结果继续

#### `market_snapshot_skill -> AkShare`

- 命中 run 内缓存
  - 允许直接返回缓存结果，并标记 `from_cache=true`
- 实时请求超时，但存在可接受缓存
  - 允许返回缓存 snapshot，并在结果中标记“非实时”
- 实时请求失败且无缓存
  - Reporter / Reviewer 只能输出“数据不可用”或降级说明，不允许脑补 exact claim

#### `fundamental_snapshot_skill -> AkShare`

- 请求成功
  - 返回标准化 `Verified Snapshot`
- 请求失败且存在最近有效缓存
  - 允许返回缓存，但必须保留 `snapshot_time`
- 请求失败且无缓存
  - 相关财务 exact claim 不允许进入高置信结论

#### `notice_snapshot_skill -> Cninfo`

- 请求成功
  - 返回公告标题、日期、链接、锚点等最小合同
- 请求失败
  - 允许研究结果继续，但必须显式标记“公告原文未取回”
  - 不允许把未验证公告内容包装成确定性事实

#### `Qdrant`

- 检索成功
  - 正常走 metadata filter + dense retrieval
- 检索失败
  - 允许降级为“无检索增强”运行模式
  - Reporter 应降低证据覆盖率预期，并显式标记检索层不可用
- 首期不要求本地第二检索 backend 自动接管

#### `Mem0`

- 读取失败
  - 允许当前 run 在无长期记忆增强条件下继续
- 写入失败
  - 不阻塞当前研究 run 的完成
  - 必须产出 `WARNING` 或 `ERROR` 级日志，并保留待补写痕迹

#### 日志与事件流

- `logs.jsonl` 写入失败
  - 不应阻塞主 run，但必须尽量保留 `RunEvent`
- `events.jsonl` 写入失败
  - 视为严重运行错误，因为会破坏 replay / audit 能力

### 10.4 第一版最小合同

三类 snapshot skill 第一版至少都应声明：

- `skill_id`
- `supported_snapshot_type`
- `input_schema`
- `output_schema`
- `allowed_vendors`
- `default_vendor`
- `validator`
- `fallback_policy`

其中 `output_schema` 不允许由具体 vendor 自定义，必须统一落到系统内部的 `Verified Snapshot` 协议对象。

---

## 11. Mem0 接入计划

### 11.1 第一版目标

第一版直接把 `Mem0` 作为正式长期记忆 backend 接入，而不是只保留接口边界。

### 11.2 适合写入的内容

- 用户偏好
- 方法论版本摘要
- 维度有效性经验
- 错误模式

### 11.2A Mem0 最小写入对象合同

第一版写入 `Mem0` 的对象至少应包含：

- `memory_id`
- `memory_type`
- `title`
- `content`
- `tags`
- `source_run_id`
- `source_review_id`
- `source_proposal_id`
- `source_entry_ids`
- `approval_status`
- `confidence`
- `created_at`
- `updated_at`

字段语义约束：

- `memory_type`
  - 首期固定枚举：`user_preference`、`methodology_summary`、`dimension_experience`、`error_pattern`
- `source_run_id`
  - 必填，除非该对象明确继承自更上游已知来源
- `source_proposal_id`
  - 对于方法论沉淀类对象，首期原则上必填
- `approval_status`
  - 首期至少支持：`approved`

### 11.2A.1 `memory_type` 首期正式枚举

首期固定只允许以下取值：

- `user_preference`
- `methodology_summary`
- `dimension_experience`
- `error_pattern`

不允许在首期自由扩展字符串枚举。

### 11.2B Mem0 写入入口约束

首期固定以下写入入口：

- approved 的 `MemoryLearningUpdate`
- approved proposal 触发的方法论摘要更新
- 经明确审核的用户偏好沉淀

首期明确不允许：

- Distiller 直接写 Mem0
- Reviewer 直接写 Mem0
- 未经审核的 Blackboard entry 直接写 Mem0

### 11.3 适配层

建议文件：

- `adapters/storage/mem0_adapter.py`

### 11.3B Mem0 读取合同

首期读取 `Mem0` 时，必须通过统一 adapter 或 service 完成，不允许 Agent 直接自由查询 backend。

建议文件：

- `services/memory_service.py`

首期最小查询输入至少包含：

- `query_text`
- `memory_types`
- `symbol`
- `sector`
- `methodology_version`
- `limit`

字段语义约束：

- `query_text`
  - 用于语义检索或关键词检索的主查询文本
- `memory_types`
  - 首期为受控枚举列表；未显式传入时由调用 Agent 的默认策略决定
- `symbol`
  - 可选；存在时优先作为过滤条件
- `sector`
  - 可选；用于行业或主题场景过滤
- `methodology_version`
  - 可选；存在时优先限制到当前激活方法论对应的 memory snapshot
- `limit`
  - 正整数；首期不允许无限制拉取长期记忆

### 11.3C Agent 读取边界

首期对不同 Agent 的默认读取范围固定为：

- `Retriever`
  - 可读取：`dimension_experience`、`methodology_summary`
  - 目的：辅助证据召回与维度优先级判断
- `Reviewer`
  - 可读取：`dimension_experience`、`error_pattern`
  - 目的：辅助复核历史失效模式与常见误判
- `Reporter`
  - 可读取：`user_preference`、`methodology_summary`
  - 目的：控制输出风格、关注点和已批准的方法论摘要
- `Evolver`
  - 可读取：全部首期 `memory_type`
  - 目的：生成 proposal 时综合长期经验
- `Distiller`
  - 首期默认不直接读取 `Mem0`
  - 避免长期经验直接污染原文蒸馏主链

首期不允许：

- Agent 绕过 `memory_service.py` 直接按 backend SDK 查询
- Agent 无限制读取全部 memory type 后自行筛选

### 11.3D 默认过滤优先级

首期 `Mem0` 读取时，统一按以下优先级过滤：

1. `methodology_version`
2. `memory_types`
3. `symbol`
4. `sector`
5. `limit`

补充约束：

- 如果存在 `memory_snapshot_ref`，应优先限制在当前激活方法论对应的 `memory_ids`
- 没有 `symbol` 或 `sector` 时，允许退化到全局长期经验
- 不允许把同一次 run 刚写入但尚未 approved 的候选当成长期正式经验回读

### 11.3E 读取失败降级

首期固定以下降级行为：

- `Mem0` 读取成功
  - 返回受控 memory 对象列表
- `Mem0` 读取失败
  - 当前 run 允许在“无长期记忆增强”模式下继续
  - 必须记录 `WARNING` 或 `ERROR` 级日志
- `Mem0` 返回为空
  - 视为正常空结果，不视为系统错误
  - 下游 Agent 不允许因为空结果伪造长期经验

### 11.3A Mem0 去重与更新规则

首期固定以下规则：

- Mem0 默认 append，不做静默覆盖
- 如同类知识被新 approved proposal 替代，应通过新的 memory 对象 supersede 旧对象
- supersede 关系必须保留来源引用
- 不通过“按标题覆盖”或“按最近一次写入覆盖”这类隐式规则更新长期知识

### 11.4 首期定位

- `Mem0` 是首期正式长期记忆 backend
- 不再额外设计本地 typed memory 主存储作为过渡层
- 需要长期沉淀的对象统一经审核后写入 `Mem0`

### 11.5 `memory_snapshot_ref` 语义

`methodology_manifest.yml` 中的 `memory_snapshot_ref` 首期固定表示为：

- 当前激活方法论依赖的 Mem0 逻辑快照对象

首期至少应能追溯到：

- 一组 `memory_id`
- 对应的 `approved_proposal_ids`
- 最近一次激活时间

第一版固定结构为：

- `snapshot_id`
- `memory_ids`
- `approved_proposal_ids`
- `generated_at`

---

## 12. 对话入口实现计划

### 12.1 task_router

入口层建议通过 `task_router.py` 实现三件事：

- 意图识别
- 槽位抽取
- 澄清问题生成

当意图不明时，应显式进入 `clarify` 工作流，而不是强制归类。

### 12.2 progress_service

实时反馈建议独立成服务层，避免 Orchestrator 自己拼用户提示。

它负责：

- phase 文案
- agent 文案
- 最近事件摘要
- 增量回答片段
- 中断后的状态提示

首期展示原则：

- 展示运行摘要、阶段进度和中间结论
- 不把原始 chain-of-thought 作为 CLI 输出对象

### 12.3 第一版入口形态

优先级建议：

1. CLI
2. 简单聊天 UI 或轮询接口
3. WebSocket / SSE

首期固定策略：

- 只正式交付 `CLI`
- `task_router`、`progress_service`、`ResearchOrchestrator` 与 `ResponseEnvelope` 按传输无关设计
- CLI 应同时支持人读输出和结构化输出模式
- CLI 应支持用户主动中断当前 run
- CLI 应支持查看历史 run 与历史结果
- CLI 应支持增量显示过程摘要与回答输出
- `HTTP/API` 作为后续 transport adapter 接入，而不是首期核心范围

### 12.4 首期 CLI 命令合同

首期固定支持以下命令：

- `run`
- `history`
- `replay`
- `resume`
- `cancel`

#### `run`

最小参数：

- `query`

可选参数：

- `--intent`
- `--json`
- `--run-id`

语义：

- 创建新请求并启动新 run
- 默认输出人读模式
- `--json` 时输出 `ResponseEnvelope`

#### `history`

最小参数：

- 无

可选参数：

- `--limit`
- `--status`
- `--json`

语义：

- 查看历史 run 列表
- 默认返回最近若干 run 的摘要

#### `replay`

最小参数：

- `run_id`

可选参数：

- `--json`

语义：

- 回放指定 run 的关键事件流与最终结果
- 不重新执行任务

#### `resume`

最小参数：

- `run_id`

可选参数：

- `--json`

语义：

- 仅当 run 处于可恢复状态时恢复执行
- 成功完成的 run 不允许 resume

#### `cancel`

最小参数：

- `run_id`

可选参数：

- `--json`

语义：

- 对 `running` 或 `waiting_external` 的 run 发出中断请求
- 成功后应使状态机进入 `cancelled`

---

## 12.1 checkpoint / resume 计划

### 12.1 目标

为长任务提供可恢复运行，而不是中断后全量重跑。

这不替代 timeout / 熔断；两者应同时存在。

首期默认策略：

- 只有失败、超时或显式中断的 run 保留 `checkpoint.json`
- 成功完成的 run 默认删除 `checkpoint.json`
- checkpoint 不是审计真源，审计仍然依赖 `events.jsonl` 与 `blackboard.jsonl`

### 12.2 第一版适用范围

- Distiller 批量蒸馏
- ResearchOrchestrator 多阶段任务
- Reviewer 长窗口复核

### 12.3 基本策略

- 每个 `run_id` 保存 checkpoint
- checkpoint 至少记录最近完成的 phase 与节点
- 恢复时优先从最近成功节点继续
- 成功结束后清理或归档 checkpoint

---

## 13. 分阶段实现路线

### Phase 0：协议与 runtime 骨架

交付：

- `schemas/` 全量基础模型
- `runtime/` 空骨架
- `RunEvent`、`BlackboardEntry`、`ResponseEnvelope`
- workspace 目录创建器
- `AgentRuntime` / `SkillRuntime` 空骨架
- `LocalSkillBackend` 空骨架

### Phase 1：在线研究最小闭环

交付：

- `ResearchOrchestrator`
- `stock_check` 与 `sector_research` 两类任务
- `TeamTemplate`
- `TaskGraph`
- `Retriever`
- `Reporter`
- `Verified Snapshot` 最小接入
- `EvidenceGate`、`QualityGate`、`VerifiedFactGate`
- 统一 response / progress 输出
- 本地 Skill 装配可运行

### Phase 2：Distiller 与 KOL 蒸馏接入

交付：

- `statuses.jsonl` 适配器
- Distiller pipeline
- `trimmer.py`
- 单条蒸馏
- 批量蒸馏
- `LogicBlock`
- `DistilledDimension`
- `events.jsonl`
- `blackboard.jsonl`
- Distillation Session
- 蒸馏结果写入检索层
- Critic / Reviewer 抽检入口
- 人工修正预留接口

### Phase 3：Reviewer 最小后验验证

交付：

- Reviewer 后验验证最小版
- `ReviewFinding`
- `ReviewGate`
- 面向可验证主张的观察窗口语义
- 基准归因规则
- run 级 replay

其中观察窗口第一版至少固定以下字段：

- `window_type`
- `window_size`
- `anchor_date`
- `evaluation_start_rule`
- `early_invalidation_rule`

首期字段语义约束：

- `window_type`
  - 只允许：`trading_days`、`calendar_days`
- `window_size`
  - 正整数
- `anchor_date`
  - 默认指向主张生成日期或原文发布日期
- `evaluation_start_rule`
  - 首期至少支持：`same_day_close`、`next_trading_day_open`
- `early_invalidation_rule`
  - 首期可为空，但字段必须预留

### Phase 4：ProposalStore / Evolver / Mem0

交付：

- 提案草案生成
- pending / approved / rejected 管理
- 错误模式沉淀
- 方法论候选更新链路
- proposal 证据权重与样本依据

### 13.5 MCP 后续扩展阶段（非首期）

这不是当前 MVP 的必做阶段，只在确实需要时启动。

可选交付：

- `MCPBackend`
- Skill 远程注册与发现
- 跨进程 Skill 调用 trace
- MCP 层 timeout / retry / fallback
- 第三方 Skill 接入约束

---

## 14. 测试计划

### 14.1 schema 测试

- 所有 Pydantic 模型 round-trip
- 枚举值与状态流校验
- taxonomy 文件校验

### 14.2 runtime 测试

- Orchestrator 状态流
- Blackboard 追加与读取
- Mailbox 定向消息
- SessionHistory 归档与 replay
- checkpoint / resume
- run 中断后恢复
- timeout / 熔断 / 降级返回

### 14.3 Distiller 测试

- 短帖
- 长帖
- 回复链
- 低信息密度内容
- 新维度候选
- structured output fallback
- trimmer 预筛路径
- Critic 抽检路径
- 人工修正后的再入链验证

### 14.4 集成测试

- `statuses.jsonl -> Distiller -> Blackboard`
- `Blackboard -> Reporter`
- `ReviewFinding -> ProposalDraft`
- verified snapshot -> Reviewer / Critic
- provider / vendor 适配边界
- benchmark 归因路径
- proposal evidence summary 完整性

---

## 15. 当前建议的落地顺序

如果马上开始写代码，建议顺序固定为：

1. `schemas/`
2. `runtime/blackboard.py`、`runtime/event_stream.py`、`runtime/session_history.py`
3. `runtime/orchestrator.py`、`runtime/gates.py`
4. `adapters/verified/`、`agents/retriever/`、`agents/reporter/`
5. `agents/collector/`
6. `agents/distiller/`
7. `adapters/sources/xueqiu_statuses.py`
8. `agents/reviewer/`
9. `runtime/proposal_store.py`、`agents/evolver/`

原因很简单：

- 先把对象和运行时骨架定住
- 再把在线研究最小闭环跑通
- 然后再把 Distiller 接入为第二阶段能力
- 最后再接演化与提案系统

---

## 16. 这份文档替代了什么

以下内容已经从主 `plan.md` 和 Distiller 专项文档中抽离并统一放到这里：

- 统一请求 / 响应 / 事件协议的字段层定义
- Python / Instructor / Pydantic / taxonomy / Qdrant client 的落位
- 代码包与模块目录树
- runtime 工作区结构
- Distiller 的代码模块拆分
- 分阶段实现计划
- 实现侧测试清单
