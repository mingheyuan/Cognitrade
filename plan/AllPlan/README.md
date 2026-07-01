# AllPlan README

Codex-style Python mapping entry:

- If the target is to learn `openai/codex` CLI UI and multi-agent/subagent patterns while keeping Python and the current architecture, start with `codex_cli_python_mapping.md`.

这组文档用于让另一个 AI 直接进入实现，不再重新做方案收敛。

当前代码已经落地了大部分底座升级，尤其是：

- Redis 动态顶层路由
- provider/data-source registry
- 新 snapshot 维度
- 蒸馏维度标准化与 Qdrant metadata 增强
- `Comps / DCF / cashflow_resilience`
- chat thread / compact / checkpoint / memory 分层

当前阶段已经不是“继续搭框架”，而是“执行层接入 + 冗余收口”：

- 短期 thread memory 与长期 methodology memory 已开始贯通到 `planner -> collector -> screener -> retriever -> reviewer -> reporter`
- 本轮又补齐了 `stock_screen` 动态 `screen_reviewer` 对 planning/methodology context hints 的消费，review 节点不再是这条链上的例外
- CLI/chat 控制面已收口到 `thread / run / proposal / review`
- 当前完成度审计已落到 `completion_matrix.md`，可以直接看哪些部分已经 `proved`、哪些还只是 `partial`
- 最近一轮本地验证已通过 `python -m unittest discover -s tests -p "test_*.py"` 全量回归和 `python -m compileall research_system tests`
- CLI/chat 层里只做启动期拼装的 `chat_startup.py` 已并回 `chat_session_bootstrap.py`
- proposal 命令的 payload builder 已并回统一 `cli_command_payloads.py`，不再保留独立 `proposal_cli.py`
- chat help / event adapter 相关测试入口也已对齐到当前真实模块，不再沿用已删除薄包装的旧模块名

因此，这个目录里最值得优先看的不再是早期“是否要这样做”，而是：

1. 现有合同和边界是什么
2. 还没做完的深层能力在哪里
3. 当前优化计划如何接着往下落

## 推荐阅读顺序

1. `phase_plan.md`
2. `plan.md`
3. `agent_system_implementation_plan.md`
4. `runtime_schema_contract.md`
5. `distiller_agent_plan.md`
6. `distiller_prompt_template_spec.md`
7. `nerya_reference_for_agent_system.md`
8. `latest_optimization_plan.md`

如果是要继续当前实现，建议实际进入顺序改成：

1. `completion_matrix.md`
2. `runtime_schema_contract.md`
3. `agent_system_implementation_plan.md`
4. `latest_optimization_plan.md`
5. 其余分阶段与参考文档

## 每份文档的作用

- `phase_plan.md`
  - 看分阶段范围、首期交付边界、哪些能力延后

- `plan.md`
  - 看业务逻辑、系统原则、Agent 分工、核心工作流

- `agent_system_implementation_plan.md`
  - 看代码骨架、技术选型、运行时组件、配置对象、CLI 命令合同

- `runtime_schema_contract.md`
  - 看运行时核心对象合同：`RunEvent`、`BlackboardEntry`、状态机等

- `completion_matrix.md`
  - 看当前目标哪些已经有代码与回归证据，哪些仍只是部分收口
  - 这是状态审计，不替代 schema/contract 真源

- `distiller_agent_plan.md`
  - 看 Distiller 的职责、流程、质量控制和 Phase 2 定位

- `distiller_prompt_template_spec.md`
  - 看 Distiller Prompt 模板和结构化输出合同

- `nerya_reference_for_agent_system.md`
  - 只作为参考，不作为当前实现规范真源

- `latest_optimization_plan.md`
  - 看当前阶段后的最新优化方向，包括数据源链、蒸馏维度标准化、subagent 收敛、methodology evolution 与 mem0 落地
  - 这也是当前继续开发时最贴近现状的增量入口

## 实现时的真源优先级

如果文档之间出现理解冲突，优先级按下面顺序处理：

1. `runtime_schema_contract.md`
2. `agent_system_implementation_plan.md`
3. `phase_plan.md`
4. `plan.md`
5. `distiller_prompt_template_spec.md`
6. `distiller_agent_plan.md`
7. `nerya_reference_for_agent_system.md`

## 当前实现原则

- 首期只正式交付 `CLI`
- `OpenAI API + GPT-5.5` 默认值，但模型名保持可配置
- `Qdrant` 是首期正式检索 backend
- `Mem0` 是首期正式长期记忆 backend
- `Verified Snapshot` 通过 snapshot skill 接入，不允许 Agent 直连 vendor
- `Blackboard`、`RunEvent`、`ProposalStore`、`Mem0`、`CLI` 合同已经收口
- `ResponseEnvelope.progress`、依赖级降级矩阵、`Qdrant` 单 collection / payload / metadata filter 合同已补齐
- `Mem0` 的写入与读取合同都已固定，Agent 只能通过统一 memory service 访问长期记忆
- 配置与运行环境固定采用 `.env + yaml`，不允许把密钥、权重、窗口或端点硬编码进代码
- `.env.example` 最小键集与 `runtime/settings.py` 的 settings 对象字段也已固定
- 首期不强依赖 Docker，但文档已显式保留后续 Docker 容器化移植路径

## 不建议再参考的材料

以下文件不是当前实现主线，默认不要作为规范真源：

- `A_Share_Research_System_MVP_PRD 3.md`
- `tradingagents_reference_for_agent_system.md`
