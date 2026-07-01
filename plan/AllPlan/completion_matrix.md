# Completion Matrix（2026-07-01）

这份文档不是新的方案，也不替代 `runtime_schema_contract.md` 或 `agent_system_implementation_plan.md`。

它只回答一件事：当前这个“执行层接入 + 冗余收口”目标，哪些部分已经有代码与回归证据，哪些还只算部分完成，哪些还不能收尾。

## 状态定义

- `proved`
  - 当前代码路径已经落地，且有直接测试或本地全量回归证据。
- `partial`
  - 主体能力已落地，但最终 sign-off 还差最后一轮文档、测试或重复层审计。
- `open`
  - 仍缺代码实现，或缺能支撑收尾判断的证据。

## Completion Matrix

### 1. 短期 thread memory

- 目标
  - `thread / checkpoint / compact / summary chain / query-aware recall` 已成为正式会话记忆层，而不是一次性上下文拼接。
- 证据
  - 代码：`research_system/cli/chat_memory.py`、`research_system/cli/chat_thread_store.py`
  - 测试：`tests/test_chat_memory.py`、`tests/test_chat_thread_checkpoint.py`
- 当前状态
  - `proved`
- 备注
  - 已有直接证据覆盖 thread 切换、checkpoint 恢复、自动 compact、summary history、跨 thread relevant recall。

### 2. 长期 structured memory

- 目标
  - `mem0` 风格的 chat memory 与 methodology memory 已通过统一 memory service 落地，并具备结构化写入/读取合同。
- 证据
  - 代码：`research_system/services/memory_service.py`、`research_system/adapters/storage/mem0_adapter.py`
  - 测试：`tests/test_methodology_memory.py`、`tests/test_chat_controller.py`、`tests/test_review_thesis_flow.py`
- 当前状态
  - `proved`
- 备注
  - 当前约束已经明确：长期记忆通过统一 memory service 访问，不再允许 agent 直接绕过服务层访问 backend。

### 3. memory-aware planning 与执行链

- 目标
  - `planner -> collector -> screener -> retriever -> reviewer -> reporter` 已真正消费 thread/profile 与 methodology memory，而不只是 planner 层知道上下文。
- 证据
  - 代码：`research_system/runtime/planner.py`、`research_system/runtime/supervisor.py`、`research_system/services/planning_context.py`
  - Agent 侧：`collector/service.py`、`screener/service.py`、`retriever/service.py`、`reviewer/service.py`、`screen_reviewer/service.py`、`reporter/service.py`
  - 测试：`tests/test_planner_supervisor.py`、`tests/test_retriever_service.py`、`tests/test_reviewer_service.py`、`tests/test_reporter_service.py`
  - Orchestrator 级证据：`tests/test_chat_controller.py`、`tests/test_stock_screen_flow.py`、`tests/test_review_thesis_flow.py`
- 当前状态
  - `proved`
- 备注
  - `screen_reviewer` 本轮已补齐 `planning_context_hints` 接线，不再是 `stock_screen` 链上的 memory-aware 例外节点。

### 4. methodology evolution 闭环

- 目标
  - 多 run 聚合、proposal 生成、auto gate、manual review、review history、approved 后写回 manifest / methodology memory / retrieval policy 已形成闭环。
- 证据
  - 代码：`research_system/services/methodology_evolver.py`、`research_system/services/proposal_generator.py`、`research_system/services/retrieval_policy_service.py`
  - 测试：`tests/test_methodology_evolution.py`、`tests/test_methodology_evolver.py`、`tests/test_proposal_generator.py`、`tests/test_proposal_store.py`、`tests/test_retrieval_policy_service.py`
- 当前状态
  - `proved`
- 备注
  - 这部分后续还能继续迭代，但就“是否已形成可用闭环”而言，当前已经过线。

### 5. CLI 以 `thread / run / proposal / review` 为一等控制面

- 目标
  - chat/CLI 不再只是把 query 丢给 orchestrator，而是有明确的 thread、run、proposal、review 控制面。
- 证据
  - 代码：`research_system/cli/chat_commands.py`、`research_system/cli/chat_actions.py`、`research_system/cli/chat_command_catalog.py`
  - 共享层：`research_system/cli/cli_command_payloads.py`、`research_system/cli/cli_command_dispatch.py`、`research_system/cli/research_request_payloads.py`
  - 测试：`tests/test_chat_cli.py`、`tests/test_chat_actions.py`、`tests/test_chat_command_catalog.py`、`tests/test_cli_command_dispatch.py`、`tests/test_chat_research_runner.py`
- 当前状态
  - `proved`
- 备注
  - `thread / threads / compact / review / history / replay / resume / cancel / proposals / proposal / review-proposal` 均有直接命令解析或 dispatch 测试。

### 6. 已知薄包装、兼容壳、重复 wiring 的收口

- 目标
  - 清掉已经没有独立边界价值的薄层，而不是为了“文件更少”继续把职责揉回一起。
- 已完成收口
  - `chat_startup.py -> chat_session_bootstrap.py`
  - `proposal_cli.py -> cli_command_payloads.py`
  - `chat_help.py -> chat_command_catalog.py`
  - 重复 `chat_event_listener -> chat_event_adapter.py`
- 证据
  - 代码：`research_system/cli/chat_session_bootstrap.py`、`research_system/cli/chat_event_adapter.py`、`research_system/cli/cli_command_payloads.py`
  - 测试：`tests/test_chat_session_bootstrap.py`、`tests/test_chat_event_adapter.py`、`tests/test_cli_command_payloads.py`、`tests/test_chat_research_runner.py`、`tests/test_chat_shell.py`、`tests/test_windows_terminal.py`
- 当前状态
  - `proved`
- 保留理由
  - `research_system/cli/research_request_payloads.py` 是 `app.py` 与 `chat_research_runner.py` 共享的 `RequestEnvelope` 构造边界，不再是一次性兼容壳。
  - `research_system/cli/chat_research_runner.py` 负责 chat 到 orchestrator 的研究执行边界，承接 event streaming、dispatch trace、conversation context 与 orchestrator 生命周期。
  - `research_system/cli/chat_fallback.py` 负责 line-mode 输入循环与 session patch/checkpoint 持久化，和 TUI 入口边界不同。
  - `research_system/cli/windows_terminal.py` 负责 prompt_toolkit 的 TTY/console fallback，与 view/layout 逻辑分开是合理边界。

### 7. 重复测试与测试入口收口

- 目标
  - 测试入口与真实模块对齐，避免测试继续依赖已删除薄包装；能合并的重复测试尽量合并。
- 已完成收口
  - `test_chat_help.py -> test_chat_command_catalog.py`
  - `test_chat_research_runner.py` 已直接对齐 `chat_event_adapter.py`
  - `test_chat_startup.py` 已并回 `test_chat_session_bootstrap.py`
  - `test_proposal_cli.py` 已删除并并回共享 payload/dispatch 覆盖
  - `test_research_request_payloads.py` 已并回 `test_chat_research_runner.py`
- 当前状态
  - `proved`
- 审计结论
  - 已完成一轮小型重复测试审计；最明显、最值得合并的孤立 builder 入口已收口。
  - 当前剩余小测试文件主要对应真实模块边界，不再是沿用旧薄包装的影子入口。

### 8. 文档与计划文件收口

- 目标
  - 当前实现状态、边界和本地回归结果要能在 README / plan 文档里被准确找到，而不是继续靠口头同步。
- 已完成收口
  - `README.md` 已同步当前本地回归结果：`398 tests OK`
  - `plan/AllPlan/README.md` 已同步当前阶段状态：`执行层接入 + 冗余收口`
  - `phase_plan.md`、`plan.md`、`agent_system_implementation_plan.md`、`latest_optimization_plan.md` 已补状态说明，明确“当前真状态 vs 历史路线/未来优化项”
  - 本文件补上 completion audit 入口
- 当前状态
  - `proved`
- 剩余判断
  - 无额外剩余判断；当前文档入口已经对齐到本地完成态。

### 9. 全链路回归稳定性

- 目标
  - 不是局部用例过，而是当前工作树至少有一轮全量回归与编译检查通过。
- 证据
  - `python -m unittest discover -s tests -p "test_*.py"` -> `Ran 398 tests ... OK`
  - `python -m compileall research_system tests` -> 通过
- 当前状态
  - `proved`
- 备注
  - 该证据对应本地状态日期：`2026-07-01`

## 当前结论

当前目标已经有足够证据支持 `complete`。

支撑这个结论的当前证据是：

1. memory-aware 执行链已经贯通到 `planner -> collector -> screener -> retriever -> reviewer -> reporter`，并有 orchestrator 级测试覆盖。
2. CLI 的 `thread / run / proposal / review` 控制面已经收口，相关共享 builder/dispatch 层已替代旧薄包装。
3. 剩余小型 CLI/runtime 文件已完成边界审计，并有直接测试证明它们是运行边界而不是兼容壳。
4. 文档入口、计划状态说明与 completion audit 已同步到当前实现状态。
5. 本地全量回归与编译检查已通过：`Ran 398 tests ... OK` 与 `python -m compileall plan/AllPlan research_system tests`。
