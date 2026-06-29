# Runtime Schema Contract

## 1. 文档目标

这份文档用于冻结运行时核心对象的最小字段、状态语义和状态转移约束。

它面向后续实现：

- `schemas/runtime.py`
- `runtime/blackboard.py`
- `runtime/orchestrator.py`
- `runtime/event_stream.py`
- `runtime/logger.py`

相关文档：

- 逻辑主方案：[plan.md](./plan.md)
- 分阶段方案：[phase_plan.md](./phase_plan.md)
- 实现方案：[agent_system_implementation_plan.md](./agent_system_implementation_plan.md)

---

## 2. 核心对象范围

本合同第一版只冻结以下对象：

- `BlackboardEntry`
- `MailboxMessage`
- `RunStateSnapshot`
- `GateVerdict`
- `RunEvent`
- `ResearchOrchestrator` 状态机

日志不作为本合同主对象冻结，但首期要求：

- 日志独立落盘到 `logs.jsonl`
- 不允许用日志替代 `RunEvent`

其中与 Reviewer 相关的 `ReviewFinding` 如进入 Blackboard，应至少在 `payload` 中包含：

- `claim_id`
- `window_type`
- `window_size`
- `anchor_date`
- `evaluation_start_rule`
- `benchmark_scope`
- `verdict`

### 2.1 ID 生成规则

第一版统一采用：

- `request_id`
- `run_id`
- `entry_id`
- `message_id`
- `gate_id`
- `event_id`
- `proposal_id`

全部使用 `ULID` 生成。

统一原则：

- ID 只负责全局唯一与时间有序，不承载业务语义
- 类型、阶段、agent、状态等信息放在独立字段，不编码进 ID 前缀
- 事件顺序以 `events.jsonl` 的追加顺序和 `created_at` 为准，不依赖 `event_id` 排序

---

## 3. BlackboardEntry 合同

### 3.1 最小字段

| 字段 | 类型语义 | 说明 |
| --- | --- | --- |
| `entry_id` | string | entry 唯一标识 |
| `run_id` | string | 所属运行 |
| `request_id` | string | 所属请求 |
| `kind` | enum | 共享事实类型 |
| `agent` | string | 生产该 entry 的 agent |
| `phase` | string | 所属阶段 |
| `status` | enum | `active`、`superseded`、`needs_review` |
| `payload` | object | 结构化正文 |
| `source_refs` | object list | 原始材料、snapshot 或上游 entry 引用 |
| `confidence` | float or null | 0 到 1 |
| `risk_flags` | string list | 风险标签 |
| `created_at` | datetime string | 创建时间 |

### 3.2 kind 固定首批值

第一版固定以下 `kind`：

- `source_record`
- `verified_snapshot`
- `logic_block`
- `distilled_dimension`
- `retrieved_evidence`
- `report_fragment`
- `review_finding`
- `proposal_draft`

### 3.3 设计原则

- append-only，不做原地覆盖
- 更新通过新增 entry 完成
- 旧 entry 被替代时标记 `superseded`
- 所有关键结论必须可追溯到 `source_refs`
- 首期默认落盘到 run 级单个 `blackboard.jsonl`
- 不按 `kind`、`phase` 或 `agent` 预分片

其中 `proposal_draft` 的 `payload` 第一版至少应包含：

- `proposal_id`
- `proposal_type`
- `title`
- `summary`
- `sample_size`
- `benchmark_scope`
- `before_metrics`
- `after_metrics`
- `evidence_summary`

---

## 4. MailboxMessage 合同

### 4.1 最小字段

| 字段 | 类型语义 | 说明 |
| --- | --- | --- |
| `message_id` | string | 消息唯一标识 |
| `run_id` | string | 所属运行 |
| `from_agent` | string | 发送方 |
| `to_agent` | string | 接收方 |
| `message_type` | enum | `supplement_request`、`challenge`、`review_feedback` |
| `payload` | object | 消息正文 |
| `status` | enum | `pending`、`read`、`resolved` |
| `created_at` | datetime string | 创建时间 |

---

## 5. RunStateSnapshot 合同

### 5.1 最小字段

| 字段 | 类型语义 | 说明 |
| --- | --- | --- |
| `run_id` | string | run 标识 |
| `request_id` | string | 请求标识 |
| `intent` | string | 当前意图 |
| `status` | enum | 对应 Orchestrator 状态 |
| `current_phase` | string or null | 当前 phase |
| `active_agent` | string or null | 当前活动 agent |
| `pending_nodes` | string list | 未完成节点 |
| `completed_nodes` | string list | 已完成节点 |
| `last_event_id` | string or null | 最近事件 |
| `error_summary` | string or null | 错误摘要 |
| `updated_at` | datetime string | 更新时间 |

### 5.2 设计原则

- 必须能支持 checkpoint 恢复
- 必须能支持状态回放
- timeout 与 failed 必须可区分

---

## 6. GateVerdict 合同

### 6.1 最小字段

| 字段 | 类型语义 | 说明 |
| --- | --- | --- |
| `gate_id` | string | gate 唯一标识 |
| `run_id` | string | 所属运行 |
| `gate_type` | string | 如 `EvidenceGate`、`VerifiedFactGate` |
| `verdict` | enum | `pass`、`fail`、`needs_review` |
| `reason` | string | 判定原因 |
| `blocking_items` | string list | 未满足项 |
| `created_at` | datetime string | 判定时间 |

---

## 7. RunEvent 合同

### 7.1 最小字段

| 字段 | 类型语义 | 说明 |
| --- | --- | --- |
| `event_id` | string | 事件标识 |
| `request_id` | string | 请求标识 |
| `run_id` | string | run 标识 |
| `intent` | string | 当前意图 |
| `event_type` | string | 事件类型 |
| `phase` | string or null | 当前阶段 |
| `agent` | string or null | 当前 agent |
| `message` | string | 事件说明 |
| `payload` | object or null | 事件载荷 |
| `artifacts` | object list | 关联产物引用，不替代事件正文或黑板事实 |
| `created_at` | datetime string | 创建时间 |

### 7.2 关键事件类型

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

`RunEvent` 首期默认顺序追加到 `runs/<run_id>/events.jsonl`，并作为运行过程真源保存。

---

## 8. ResearchOrchestrator 状态机合同

### 8.1 最小状态集合

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

### 8.2 状态转移表

| From | Trigger | To | 说明 |
| --- | --- | --- | --- |
| `received` | 创建 run | `identifying_intent` | 请求进入路由 |
| `identifying_intent` | 信息不足 | `needs_clarification` | 进入 `clarify` |
| `identifying_intent` | 意图明确且槽位完整 | `ready` | 进入执行准备 |
| `needs_clarification` | 收到追问回复 | `identifying_intent` | 重新识别 |
| `ready` | 任务图生成完成 | `running` | 开始执行 |
| `running` | 外部依赖等待 | `waiting_external` | 等待数据或窗口 |
| `waiting_external` | 外部依赖满足 | `running` | 恢复执行 |
| `running` | 当前阶段完成 | `gated` | 进入 gate 判定 |
| `gated` | gate 放行下一阶段 | `running` | 继续执行 |
| `gated` | gate 判定流程完成 | `completed` | 正常收束 |
| `running` | 用户中断 | `cancelled` | 主动取消 |
| `waiting_external` | 用户中断 | `cancelled` | 主动取消 |
| `running` | 不可恢复错误 | `failed` | 失败结束 |
| `running` | 超时阈值命中 | `timed_out` | 熔断结束 |
| `waiting_external` | 超时阈值命中 | `timed_out` | 等待超时 |

### 8.3 状态机原则

- 每次状态切换都必须产生 `RunEvent`
- `cancelled` 必须与 `failed`、`timed_out` 区分
- `timed_out` 不能混同于 `failed`
- `clarification` 必须回到 `identifying_intent`
- `gated` 必须显式存在，不能藏进 Agent 内部

---

## 9. 首版实现优先级

实现顺序建议：

1. `RunEvent`
2. `BlackboardEntry`
3. `RunStateSnapshot`
4. `GateVerdict`
5. `ResearchOrchestrator` 状态机
6. `MailboxMessage`

理由：

- 先定事件和共享事实
- 再定运行态快照
- 最后接消息和外围协作
