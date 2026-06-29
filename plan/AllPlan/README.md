# AllPlan README

这组文档用于让另一个 AI 直接进入实现，不再重新做方案收敛。

## 推荐阅读顺序

1. `phase_plan.md`
2. `plan.md`
3. `agent_system_implementation_plan.md`
4. `runtime_schema_contract.md`
5. `distiller_agent_plan.md`
6. `distiller_prompt_template_spec.md`
7. `nerya_reference_for_agent_system.md`

## 每份文档的作用

- `phase_plan.md`
  - 看分阶段范围、首期交付边界、哪些能力延后

- `plan.md`
  - 看业务逻辑、系统原则、Agent 分工、核心工作流

- `agent_system_implementation_plan.md`
  - 看代码骨架、技术选型、运行时组件、配置对象、CLI 命令合同

- `runtime_schema_contract.md`
  - 看运行时核心对象合同：`RunEvent`、`BlackboardEntry`、状态机等

- `distiller_agent_plan.md`
  - 看 Distiller 的职责、流程、质量控制和 Phase 2 定位

- `distiller_prompt_template_spec.md`
  - 看 Distiller Prompt 模板和结构化输出合同

- `nerya_reference_for_agent_system.md`
  - 只作为参考，不作为当前实现规范真源

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
