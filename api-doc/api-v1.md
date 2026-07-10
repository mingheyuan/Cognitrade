# Cognitrade API v1 接口文档

> 文档文件：`api-doc/api-v1.md`
> URL 版本前缀：`/api/v1`
> 实现基线：2026-07-10，`research_system/api/`
> 项目性质：面向 A 股研究的 CLI-first 多 Agent 研究系统

## 1. 文档定位

本文件是根据 `research_system/`、运行时 schema、CLI 命令、持久化结构和测试用例整理出的 **API v1 完整契约**。它同时说明：

1. 当前后端已经实现的 Python/CLI 能力；
2. FastAPI 服务如何把这些能力映射为 `/api/v1` REST/SSE 接口；
3. 请求、响应、状态、错误、鉴权和安全边界；
4. 当前源码尚未实现或无法保证的行为。

### 1.1 当前实现状态

当前仓库已经实现 FastAPI 入站服务。可调用入口是：

- HTTP/SSE：`research_system.api.app:app`，业务前缀 `/api/v1`；
- CLI：`python -m research_system.cli.app <command>`；
- Python：`research_system.runtime.orchestration.orchestrator.ResearchOrchestrator`；
- 本地持久化：`workspace/runs/`、`workspace/memory/` 和 chat thread store。

本文第 4 节列出的 37 个 `/api/v1` 业务操作均已注册为 FastAPI 路由。传输层负责 Bearer 鉴权、CORS 白名单、输入校验、统一错误、同步核心线程池调度、写操作串行化以及从 `events.jsonl` 输出 SSE；研究逻辑仍由现有 `ResearchOrchestrator` 和 store 执行。

启动命令：

```bash
python -m research_system.api
```

默认地址为 `http://127.0.0.1:8000`。框架生成的 `/docs`、`/redoc` 和 `/openapi.json` 是开发/发现入口，不属于版本化业务资源。

### 1.2 版本命名与演进规则

- v1 文档固定命名为 `api-doc/api-v1.md`，接口路径固定使用 `/api/v1`。
- 后续只要新增业务接口，必须新增一份完整文档 `api-doc/api-v2.md`，新接口统一放在 `/api/v2`。
- `api-v2.md` 必须是可独立阅读的完整契约，不能只记录相对 v1 的差异。
- v1 发布后仅允许修正文案、示例和不改变契约的勘误；不得在 v1 中静默增加接口。
- 删除字段、修改字段类型、改变必填性、改变状态码或状态语义均属于破坏性变更，必须升级版本。
- v1 客户端必须忽略响应中的未知字段；事件消费者必须忽略未知 `event_type`。

## 2. 后端能力地图

| 领域 | 主要代码 | 当前入口 | FastAPI v1 实现 |
| --- | --- | --- | --- |
| 研究运行 | `runtime/orchestration/`、`runtime/execution/` | `ResearchOrchestrator.run`、CLI `run` | 线程池执行、全局 mutation lock |
| 运行控制 | `runtime/core/control_plane.py`、`runtime/execution/core/`、run store | `cancel`、`resume`、`rerun_from` | 取消、恢复检查、局部重跑 |
| 过程观测 | `runtime/orchestration/*admin_support.py`、`runtime/storage/runs/`、`workspace/runs/*` | `events`、`trace`、`steps`、`metrics` | REST 查询与持久化 SSE 尾随 |
| 研究数据 | `schemas/snapshots.py`、collector/retriever | 黑板与 `result.json` | 保持来源、缺失项、降级信息 |
| 估值与报告 | `schemas/valuation.py`、reporter | `ResponseEnvelope.result.report` | 输出多意图报告联合结构 |
| 提案审批 | `runtime/proposals/` | proposal CLI/Python 方法 | 服务级鉴权、评审主体写入、配置写回 |
| 子 Agent 发现 | `runtime/orchestration/subagent_discovery_*` | discovery CLI/Python 方法 | 审批与动态注册保护 |
| Chat thread | `cli/memory/`、`cli/commands/`、thread store | chat 斜杠命令、`api/chat_service.py` | 传输无关 facade、进程内写锁 |
| 诊断维护 | `runtime/diagnostics/`、retention、Qdrant | `status/doctor/check/cleanup/reindex` | 线程池调度、写操作互斥、服务级鉴权 |

## 3. 通用协议

### 3.1 Base URL 与媒体类型

```text
Base URL: /api/v1
Request:  Content-Type: application/json; charset=utf-8
Response: Content-Type: application/json
SSE:      Content-Type: text/event-stream; charset=utf-8
```

所有 JSON 字段使用 `snake_case`，所有时间使用带时区的 ISO 8601/RFC 3339 字符串，服务端统一保存为 UTC，例如 `2026-07-10T08:30:00Z`。

### 3.2 标识符

| 标识符 | 说明 |
| --- | --- |
| `request_id` | 默认由核心生成 ULID；调用方可传入用于幂等追踪，但 v1 不承诺自动去重 |
| `run_id` | 每次实际运行生成一个 ULID；局部重跑也会生成新的 `run_id` |
| `event_id` | 每条原始运行事件的 ULID，可用于 SSE 断点续传 |
| `thread_id` | 运行时线程通常为 ULID；chat thread 为 `thread_` 加 12 位十六进制字符 |
| `turn_id`、`item_id`、`entry_id` | 运行内部对象 ULID |
| `proposal_id`、`ticket_id` | 当前均为不带资源前缀的 26 位 ULID |
| `invocation_id` | 子 Agent 调用标识 |

服务端必须把所有路径标识符当作不可信输入，校验允许字符和长度；禁止把未经校验的 `run_id`、`thread_id` 等直接拼接为文件路径，以防目录穿越。

### 3.3 鉴权与权限

FastAPI v1 对全部 `/api/v1` 路由执行静态 Bearer token 校验。服务启动时默认要求配置 `COGNITRADE_API_TOKEN`：

```http
Authorization: Bearer <token>
```

token 使用常量时间比较；缺少或错误 token 返回 `401` 和 `WWW-Authenticate: Bearer`。OpenAPI 使用 `BearerAuth` HTTP security scheme，可在 `/docs` 的 Authorize 入口设置凭据。仅在未配置 token 的隔离本机开发环境中可设置 `COGNITRADE_API_ALLOW_UNAUTHENTICATED=true`；无鉴权模式只允许绑定 `127.0.0.1`、`localhost` 或 `::1`。开发 CORS origin 通过 `COGNITRADE_API_CORS_ORIGINS` 显式配置，`*` 会导致启动校验失败。

当前 v1 只有一个服务级 token，持有者可访问全部业务与管理接口，尚未实现用户、角色或租户模型。生产多用户部署建议权限如下，属于后续安全演进而非当前已实现的角色系统：

| 权限 | 可访问范围 |
| --- | --- |
| `research:run` | 创建、查看、取消和重跑本人/本租户研究运行 |
| `research:read` | 查看报告、事件、trace、metrics 和 chat thread |
| `admin:review` | 审核提案和子 Agent 发现工单 |
| `admin:operate` | 激活子 Agent、执行检查、清理和 Qdrant 重建 |

`replay`、`trace`、检查输出和系统状态可能包含原始问题、绝对路径、数据源信息或错误堆栈。当前只能部署在共享同一信任边界的单租户环境；实现资源所有权和字段脱敏前，不得把服务级 token 分发给不受信任用户。v1 尚未实现应用层限流。

### 3.4 通用错误响应

FastAPI 错误统一使用：

```json
{
  "error": {
    "code": "resource_not_found",
    "message": "The requested resource was not found",
    "request_id": "01J..."
  }
}
```

| HTTP 状态 | 使用场景 |
| --- | --- |
| `400` | JSON 语法错误、非法标识符 |
| `401` | 未认证 |
| `404` | run、thread、subagent、proposal 或 discovery ticket 不存在 |
| `409` | 状态冲突，如未审批工单激活、不可重跑状态 |
| `422` | Pydantic 字段校验失败、未知重跑节点、注册合同不完整 |
| `500` | 未处理异常、存储损坏、部分写入失败 |
| `503` | 服务未就绪或依赖不可用 |

研究本身得到 `failed`、`timed_out`、`cancelled` 或 `needs_clarification` 时，仍返回 HTTP `200` 和 `ResponseEnvelope`；这些是领域状态，不等同于 HTTP 传输失败。请求尚未进入运行时的校验错误才返回 `4xx`。

当前 v1 没有角色/租户授权、应用层限流和上游异常专用映射，因此不主动产生 `403`、`429` 或 `502`；这些能力若补充会改变错误契约，应随新 API 版本发布。

错误响应会携带 `X-Request-ID`，body 中也包含同一 `request_id`。未知服务端异常返回 `internal_server_error`，不会把异常消息或 traceback 写入响应。

### 3.5 分页与列表

当前 Python `history()` 没有游标，proposal/discovery 列表只支持 `status` 和 `limit`。v1 统一约定：

- `limit` 默认值见各接口，合法范围 `1..100`；
- v1 不承诺游标分页；
- run 列表按 run 目录名（当前为近似创建时间有序的 ULID）倒序；proposal/discovery 列表按各自 store 的更新时间倒序；
- 大规模游标分页应作为 v2 能力设计。

## 4. 接口总览

下列 37 个操作均已由 FastAPI 注册，并由 OpenAPI 契约测试防止路径漂移。除 `/docs`、`/redoc`、`/openapi.json` 外，所有业务路由都需要第 3.3 节的 Bearer token。

### 4.1 研究运行与观测

| 方法 | 路径 | 用途 | 核心映射 |
| --- | --- | --- | --- |
| `POST` | `/runs` | 同步创建并执行研究 | `run(request)` |
| `GET` | `/runs` | 运行历史 | `history()` |
| `GET` | `/runs/{run_id}/replay` | 完整回放快照 | `replay(run_id)` |
| `GET` | `/runs/{run_id}/events` | 规范化事件时间线 | `events(run_id)` |
| `GET` | `/runs/{run_id}/events/stream` | 原始事件 SSE | 尾随 `events.jsonl` |
| `GET` | `/runs/{run_id}/trace` | 运行与动态编排追踪 | `trace(run_id)` |
| `GET` | `/runs/{run_id}/steps` | 顶层计划步骤 | `steps(run_id)` |
| `GET` | `/runs/{run_id}/metrics` | 运行指标 | `metrics(run_id)` |
| `GET` | `/runs/{run_id}/threads/{thread_id}` | 线程详情 | `thread(run_id, thread_id)` |
| `GET` | `/runs/{run_id}/threads/{thread_id}/replay` | 线程范围回放 | `thread_replay(...)` |
| `GET` | `/runs/{run_id}/threads/{thread_id}/metrics` | 线程范围指标 | `thread_metrics(...)` |
| `GET` | `/runs/{run_id}/subagents/{invocation_id}` | 子 Agent 调用详情 | `subagent(...)` |
| `GET` | `/runs/{run_id}/recovery` | 检查恢复信息 | `resume(run_id)` |
| `POST` | `/runs/{run_id}/cancel` | 请求取消 | `cancel(run_id)` |
| `POST` | `/runs/{run_id}/reruns` | 从顶层节点重跑 | `rerun_from(...)` |
| `POST` | `/runs/{run_id}/subagent-reruns` | 从子 DAG 节点重跑 | `rerun_subagent_from(...)` |

### 4.2 Chat thread

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/chat/threads` | 新建 chat thread |
| `GET` | `/chat/threads` | 列出 chat thread |
| `GET` | `/chat/threads/{thread_id}` | 读取 thread 与 turn |
| `GET` | `/chat/threads/{thread_id}/preview` | 获取精简预览 |
| `PATCH` | `/chat/threads/{thread_id}` | 修改标题 |
| `POST` | `/chat/threads/{thread_id}/forks` | 分叉 thread |
| `POST` | `/chat/threads/{thread_id}/archive` | 归档 thread |
| `POST` | `/chat/threads/{thread_id}/unarchive` | 取消归档 |
| `POST` | `/chat/threads/{thread_id}/compact` | 压缩历史上下文 |

Chat thread 通过 `research_system.api.chat_service.ChatService` 暴露；它为每次操作创建独立 `ChatSessionState`，并使用进程内 `RLock` 保护 index 的 read-modify-write，不共享 CLI TUI session。

### 4.3 管理与系统

| 方法 | 路径 | 用途 | 核心映射 |
| --- | --- | --- | --- |
| `GET` | `/admin/proposals` | 提案列表 | `list_proposals` |
| `GET` | `/admin/proposals/{proposal_id}` | 提案详情 | `show_proposal` |
| `POST` | `/admin/proposals/{proposal_id}/reviews` | 审批提案 | `review_proposal` |
| `GET` | `/admin/subagent-discoveries` | 能力发现工单列表 | `list_subagent_discovery_requests` |
| `GET` | `/admin/subagent-discoveries/{ticket_id}` | 工单详情 | `show_subagent_discovery_request` |
| `POST` | `/admin/subagent-discoveries/{ticket_id}/reviews` | 审批工单 | `review_subagent_discovery_request` |
| `POST` | `/admin/subagent-discoveries/{ticket_id}/activations` | 激活发现能力 | `activate_subagent_discovery_request` |
| `GET` | `/system/status` | 运行环境摘要 | `build_status` |
| `POST` | `/system/doctor` | 执行诊断 | `run_doctor` |
| `POST` | `/admin/system/checks` | 编译与单元测试 | `run_checks` |
| `POST` | `/admin/artifacts/cleanup` | 清理旧 artifact | `cleanup` |
| `POST` | `/admin/qdrant/reindex` | 重建 Qdrant 索引 | `rebuild_qdrant` |

## 5. 核心研究接口

### 5.1 `POST /runs`

同步创建并执行一次研究。当前 `ResearchOrchestrator.run()` 会一直阻塞到终态；v1 不虚构队列或后台 job，所以成功进入核心后返回 HTTP `200`。

#### 请求体：`RequestEnvelope`

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `request_id` | string | 否 | 服务端 ULID | 请求追踪标识；不自动保证幂等 |
| `intent` | string/null | 否 | `null` | 显式意图；省略时由 router 推断 |
| `user_query` | string | 是 | - | 用户自然语言问题 |
| `conversation_context` | object[] | 否 | `[]` | 历史对话上下文；v1 保留开放对象 |
| `slots` | object | 否 | `{}` | schema 已定义，但当前主研究链路不读取 |
| `constraints` | object | 否 | `{}` | 各意图的结构化约束，见第 10 节 |
| `clarification_status` | enum | 否 | `not_needed` | `not_needed`、`pending`、`resolved` |
| `created_at` | datetime | 否 | 服务端当前时间 | 客户端通常不传 |

请求模型禁止未知顶层字段。`intent` 当前支持：

```text
stock_check
stock_compare
stock_screen
sector_research
distill_kol_logic
review_thesis
methodology_evolution
```

显式传入未知 `intent` 时，FastAPI v1 在进入 router 前返回 `422 invalid_intent`。`user_query` 会去除首尾空白，空字符串或超过 20,000 字符返回 `422 invalid_query`。

#### 示例

```http
POST /api/v1/runs
Content-Type: application/json

{
  "intent": "stock_check",
  "user_query": "分析贵州茅台当前投资价值",
  "conversation_context": [],
  "constraints": {
    "symbol": "600519",
    "investment_cycle": "long_term",
    "snapshot_types": ["market", "fundamental", "notice"]
  }
}
```

#### 响应体：`ResponseEnvelope`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `request_id` | string | 原请求标识 |
| `run_id` | string/null | 已创建运行的标识 |
| `intent` | string/null | 实际路由意图 |
| `status` | enum | `accepted`、`needs_clarification`、`running`、`completed`、`cancelled`、`failed`、`timed_out` |
| `message` | string | 面向调用方的状态摘要 |
| `clarification` | object/null | `{question, missing_fields[]}` |
| `progress` | `ProgressState` | 当前进度 |
| `result` | object/null | 完成结果或失败上下文 |
| `artifacts` | object[] | 关联 artifact；目前通常为空 |
| `errors` | string[] | 机器可读/可展示错误摘要 |
| `created_at` | datetime | 响应创建时间 |
| `updated_at` | datetime | 响应更新时间 |

`ProgressState`：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `current_phase` | string/null | 当前阶段 |
| `active_agent` | string/null | 当前 Agent |
| `latest_event` | string/null | 最近事件类型 |
| `status_message` | string | 状态说明 |
| `percent` | integer | `0..100` |
| `updated_at` | datetime | UTC 时间 |

完成示例：

```json
{
  "request_id": "01J...",
  "run_id": "01J...",
  "intent": "stock_check",
  "status": "completed",
  "message": "Research completed",
  "clarification": null,
  "progress": {
    "current_phase": "report",
    "active_agent": "reporter",
    "latest_event": "run_completed",
    "status_message": "Research completed",
    "percent": 100,
    "updated_at": "2026-07-10T08:30:00Z"
  },
  "result": {
    "intent": "stock_check",
    "report": {
      "conclusion": "...",
      "evidence": [],
      "risk": "...",
      "degraded": false
    }
  },
  "artifacts": [],
  "errors": [],
  "created_at": "2026-07-10T08:29:00Z",
  "updated_at": "2026-07-10T08:30:00Z"
}
```

#### 澄清语义

缺少关键参数时响应为：

```json
{
  "request_id": "01J...",
  "run_id": "01J...",
  "intent": "stock_check",
  "status": "needs_clarification",
  "message": "Clarification required",
  "clarification": {
    "question": "请提供要分析的股票代码或名称。",
    "missing_fields": ["symbol"]
  },
  "progress": {
    "current_phase": "clarify",
    "active_agent": null,
    "latest_event": "clarification_requested",
    "status_message": "Clarification required",
    "percent": 0,
    "updated_at": "2026-07-10T08:30:00Z"
  },
  "result": null,
  "artifacts": [],
  "errors": [],
  "created_at": "2026-07-10T08:30:00Z",
  "updated_at": "2026-07-10T08:30:00Z"
}
```

当前 Python API 没有“继续同一 run”的公共方法。调用方补充信息后必须重新 `POST /runs`；如需保留上下文，把原问题和澄清答案放入 `conversation_context`，并设置 `clarification_status: "resolved"`。同一 run 的多轮继续能力应留到 v2。

#### 失败语义

- gate 校验失败：`status=failed`，`errors=["gate_validation_failed"]`，`result` 含 `intent`、`status` 和 `gate_failures[]`。
- 运行异常：尽可能转换为 `status=failed` 的 `ResponseEnvelope`。
- router、预检、存储初始化等在进入核心保护区前抛出的异常，由 FastAPI 错误处理器返回 `4xx/5xx` 通用错误响应。
- timeout 是运行时控制点检查，不是对正在阻塞的外部调用做硬中断。

### 5.2 `GET /runs`

返回运行历史，对应 `ResearchOrchestrator.history()`。

查询参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `limit` | integer | `20` | `1..100`，FastAPI 在过滤后切片 |
| `status` | string | - | 按 `OrchestratorStatus` 过滤 |

响应为 JSON 数组。当前行对象来自 `summary.json`/`run_state.json`，是开放结构；通常含 `run_id`、`request_id`、`intent`、`status`、`display_summary`、`updated_at` 等。客户端必须容忍字段缺失和新增。

> 不定义 `GET /runs/{run_id}` 返回原始 `ResponseEnvelope`，因为当前持久化层不能可靠重建创建请求时的完整响应。需要完整详情时使用 `/replay`。

### 5.3 `GET /runs/{run_id}/replay`

返回完整运行回放，适合调试或内部详情页。

```json
{
  "run_id": "01J...",
  "request": {},
  "events": [],
  "blackboard": [],
  "threads": [],
  "thread_tree": [],
  "result": {},
  "summary": {},
  "display_summary": "01J00000000000000000000010 approved",
  "control_state": {},
  "runtime_control_stage_decisions": []
}
```

这里的 `events` 是原始 `RunEvent[]`，而 `/events` 返回规范化时间线。`request`、`blackboard` 和 `result` 可能很大且敏感，不应开放给匿名用户。

### 5.4 `GET /runs/{run_id}/events`

返回适合 UI 展示的规范化时间线。下面只展示事件主体；实际响应还固定包含 `request`、`summary`、`display_summary`、`control_state`、`runtime_control_stage_decisions`、`threads`、`thread_tree` 和 `event_counts`：

```json
{
  "run_id": "01J...",
  "events": [
    {
      "event_type": "agent_completed",
      "phase": "collect",
      "agent": "collector",
      "message": "Collection completed",
      "thread_id": "01J...",
      "turn_id": "01J...",
      "action_id": "collect_stock",
      "node_id": "collect_stock",
      "task_name": "collect_stock",
      "agent_path": "collector",
      "agent_label": "Collector",
      "subagent_type": null,
      "invocation_id": null,
      "status": "completed",
      "objective": "Collect verified snapshots",
      "child_thread_id": null,
      "child_turn_id": null,
      "notes": [],
      "duration_ms": 823,
      "wait_timeout_seconds": null,
      "created_at": "2026-07-10T08:30:00Z"
    }
  ]
}
```

规范化事件会丢失原始 `event_id`、部分 `payload` 和 `artifacts`，不能用于 SSE 断点续传；需要断点续传时必须读取原始 `events.jsonl`。

### 5.5 `GET /runs/{run_id}/events/stream`

SSE 事件流由 `RunEventSource` 实现，数据源是持久化的原始 `events.jsonl`；它会先回放游标后的已有事件，再轮询追加事件。

请求头：

```http
Accept: text/event-stream
Last-Event-ID: 01J...
```

浏览器原生 `EventSource` 不能设置 `Authorization` 请求头；前端必须使用支持 header 的 fetch-SSE 客户端，或经同源 BFF 转发。不得把长期服务 token 放进查询字符串。

响应：

```text
id: 01J...
event: agent_progress
data: {"event_id":"01J...","request_id":"01J...","run_id":"01J...","intent":"stock_check","event_type":"agent_progress","phase":"collect","agent":"collector","message":"...","payload":{},"artifacts":[],"created_at":"2026-07-10T08:30:00Z"}

```

响应头至少包含：

```http
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

实现要求：

- 使用 `event_id` 作为 SSE `id`，`event_type` 作为 SSE `event`，完整 `RunEvent` JSON 作为 `data`；
- 收到 `Last-Event-ID` 后从 `events.jsonl` 找到该事件，并按追加顺序补发其后的事件；游标不存在返回 `409 event_cursor_not_found`；
- 按 `COGNITRADE_API_SSE_HEARTBEAT_SECONDS` 发送 `: heartbeat` 注释心跳；
- 不使用运行时 `event_listener`，而是以持久化事件作为恢复和补发来源；
- 同时观察 `run_state.json`；发现 `completed/cancelled/failed/timed_out/needs_clarification` 后继续执行 4 次安静轮询，补发终态写入之后才追加的事件，再关闭连接；
- 连接断开不自动取消研究运行。

`event_type` 在 schema 中是开放字符串，不是 enum。第 12 节列出的只是当前源码已知值，客户端不得做穷举校验。

### 5.6 `GET /runs/{run_id}/trace`

返回动态编排、控制面和子 Agent 追踪：

```text
run_id
request
summary
display_summary
control_state
runtime_control_stage_decisions[]
subagent_trace[]
dynamic_trace[]
action_plan_patches[]
wait_trace[]
threads[]
thread_tree
event_counts
```

响应为开放对象，主要供调试页面和运维使用。不得把其中的原始 prompt、凭据、绝对路径或 provider 错误直接展示给低权限客户端。

### 5.7 `GET /runs/{run_id}/steps`

返回顶层行动计划及聚合计数：

```text
run_id, request, summary, display_summary
plan_id, intent, strategy, entry_strategy, entry_reason
subagent_required, dispatch_trace[], notes[]
threads[], thread_tree
steps[], step_counts, status_counts
```

`steps[]` 单项：

| 字段 | 类型 |
| --- | --- |
| `step_index` | integer |
| `action_id`、`node_id` | string |
| `phase`、`agent`、`objective` | string/null |
| `status` | string |
| `tags` | string[] |
| `inputs` | object |
| `active_from_supervisor` | boolean/null |
| `supervisor_note` | string/null |
| `next_action_payload` | object/null |

### 5.8 `GET /runs/{run_id}/metrics`

返回运行聚合指标，主要字段：

```text
counts
rates
latency_ms: {count, avg, max, p50, p95}
turn_usage
fanout
threads[]
thread_tree
degrade_reason_counts
event_type_counts
dag_event_counts
```

没有数据的指标可能为 `0`、空对象或空数组；客户端不得假定每次运行都执行所有 Agent。

### 5.9 `GET /runs/{run_id}/threads/{thread_id}`

读取一次研究运行中的某个运行时线程，而不是 chat thread。响应字段：

```text
run_id, thread_id, scope, scope_thread_ids[]
request, summary, display_summary, control_state
thread, threads[], thread_tree
events[], blackboard[]
subagent_trace[], dynamic_trace[], action_plan_patches[], wait_trace[]
items[], metrics, event_counts
```

此接口的 `scope_thread_ids` 只包含所选线程；`/replay` 和 `/metrics` 两个线程范围接口才默认包含其后代。`thread` 采用 `RuntimeThreadState`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `thread_id`、`run_id`、`request_id` | string | 标识符 |
| `intent` | string | 线程研究意图 |
| `status` | enum | `ready/running/waiting/completed/failed/cancelled` |
| `parent_thread_id` | string/null | 父线程 |
| `active_turn_id` | string/null | 当前 turn |
| `total_turns` | integer | 非负整数 |
| `created_at`、`updated_at` | datetime | 时间 |

### 5.10 `GET /runs/{run_id}/threads/{thread_id}/replay`

返回限定在线程及其后代范围内的 replay 数据。结构与 `/runs/{run_id}/replay` 类似，但不包含范围外的事件、黑板条目和子 Agent 调用。

### 5.11 `GET /runs/{run_id}/threads/{thread_id}/metrics`

返回限定在线程及其后代范围内的 metrics。指标字段与 `/runs/{run_id}/metrics` 一致。

### 5.12 `GET /runs/{run_id}/subagents/{invocation_id}`

读取一次子 Agent 调用的观测视图。当前 payload 是开放对象，稳定的高层字段包括：

```text
run_id
invocation_id
request
summary
display_summary
subagent_type, task_name, agent_path, agent_label
status, event_type, action_id, node_id
thread_id, turn_id, phase, agent, objective
child_thread_id, child_turn_id, duration_ms, notes[]
trace_rows[]
events[]
event_counts
item, item_id, degrade_reason, created_at, updated_at（存在 item 时）
llm_used, output_keys[]（存在对应 item payload 时）
```

如果 `invocation_id` 在该 run 中不存在，返回 `404 nested_resource_not_found`。

### 5.13 `GET /runs/{run_id}/recovery`

对应源码方法名 `resume(run_id)`，但它 **只检查恢复状态，不会恢复执行**。为避免误导，REST 路径使用 `/recovery`。

响应：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `run_id` | string | 运行标识 |
| `status` | string | 当前 `OrchestratorStatus` |
| `resumable` | boolean | 是否具备恢复前提 |
| `checkpoint` | object/null | checkpoint 摘要/内容 |
| `control_state` | object | 运行控制状态 |
| `runtime_control_stage_decisions` | object[] | 控制阶段决策，最多受内部保留策略限制 |
| `runtime_control_stage_count` | integer | 决策数量 |
| `latest_runtime_control_stage_decision` | object/null | 最近决策 |
| `recovery_guidance` | object | `{status, reason, suggested_next_step}` 恢复建议 |
| `resume_from` | object/null | `{current_phase, active_agent, completed_nodes, pending_nodes}` 建议恢复位置 |

只有 `failed`、`timed_out` 或 `cancelled` 且存在 checkpoint 时，`resumable=true`。`recovery_guidance.status` 当前可能值：

```text
resume_unavailable
resume_ready
resume_after_action_replan
resume_after_discovery_intervention
resume_with_local_replan
resume_after_manual_cancel
```

真正的自动恢复尚未形成公共方法；调用方可根据 `resume_from` 使用重跑接口，新运行会得到新的 `run_id`。

### 5.14 `POST /runs/{run_id}/cancel`

请求取消运行，无请求体。

```json
{
  "run_id": "01J...",
  "status": "cancelled",
  "message": "Cancellation requested",
  "control_state": {},
  "cancellation_requested": true
}
```

只有 `received`、`identifying_intent`、`ready`、`running`、`waiting_external`、`gated` 被视为活跃状态。对终态 run 重复调用时返回 HTTP `200`，`cancellation_requested=false`，不改变领域终态、summary 或 cancellation control 标记。

取消是协作式控制点检查，不能保证立即中断正在阻塞的 LLM、网络或工具调用。

### 5.15 `POST /runs/{run_id}/reruns`

从原运行顶层 DAG 的指定节点开始重跑，并创建一个新 run。

```json
{
  "from_node": "retrieve"
}
```

响应为完整 `ResponseEnvelope`。语义：

- 读取源 run 的 `request.json` 和 `task_plan.json`；
- 校验 `from_node` 是顶层计划节点；
- 新建 `run_id`，重用可恢复缓存，执行该节点及其后代；
- 源 run 不被修改。

源 run 不存在返回 `404`；缺失请求/计划返回 `409 source_run_incomplete`；未知节点返回 `422 unknown_rerun_node`。

### 5.16 `POST /runs/{run_id}/subagent-reruns`

从某个顶层 action 内部的子 DAG 节点开始重跑：

```json
{
  "action_node": "collect",
  "dag_id": "collector_dag",
  "from_node": "market_snapshot"
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `action_node` | 是 | 顶层 action 节点 |
| `dag_id` | 是 | 子 DAG 标识 |
| `from_node` | 是 | 子 DAG 起始节点 |

响应为新 run 的 `ResponseEnvelope`。顶层 `action_node` 不存在时返回 `422 unknown_rerun_node`；当前核心没有在执行前完整验证 `dag_id/from_node`，未知子节点可能被核心收敛为 HTTP `200`、`status=failed` 的领域响应，未知 `dag_id` 也可能未应用预期的子 DAG 起点。这是 v1 已知契约缺口，调用方只能使用源 run 实际持久化的 DAG/node ID。

## 6. Chat thread 接口

> 本节中的 thread 是持久化对话线程，与第 5 节的运行时 thread 不同。

### 6.1 `POST /chat/threads`

新建空 thread，无请求体。成功返回 `201`：

```json
{
  "thread_id": "thread_a1b2c3d4e5f6"
}
```

需要标签时创建后调用 `PATCH /chat/threads/{thread_id}`。

### 6.2 `GET /chat/threads`

查询参数：

| 参数 | 默认 | 约束 |
| --- | --- | --- |
| `limit` | `10` | `1..100` |
| `scope` | `active` | `active`、`all`、`archived` |

响应为数组，按 `last_turn_at` 降序。列表项：

```text
thread_id
last_turn_at
turn_count
lifetime_turn_count
thread_label
topic_title
topic_intent
thread_source
parent_thread_id
fork_turn_count
archived
archived_at
```

`thread_source` 当前通常为 `new` 或 `fork`。`turn_count` 是当前未压缩存储条数，`lifetime_turn_count` 包含已经 compact/archive 的历史 turn。

### 6.3 `GET /chat/threads/{thread_id}`

详情字段：

```text
thread_id, thread_label, topic_title, topic_intent
thread_source, parent_thread_id, fork_turn_count
archived, archived_at, unarchived_at
has_checkpoint, has_compact_summary
summary_text, summary_turn_count
turn_count, lifetime_turn_count
turns[]
```

`turns[]`：

| 字段 | 类型 |
| --- | --- |
| `thread_id` | string |
| `turn_seq` | integer |
| `role` | string |
| `content` | string |
| `turn_type` | string |
| `intent` | string/null |
| `run_id` | string/null |
| `metadata` | object |
| `recorded_at` | datetime/null |

响应合并 active 与 archived turns，并按 `(turn_seq, recorded_at)` 排序。只暴露 `has_checkpoint`，不返回 checkpoint 原文，因为它包含当前意图、最近问题、澄清状态和研究摘要等敏感会话状态。

### 6.4 `GET /chat/threads/{thread_id}/preview`

查询参数 `turn_limit` 默认 `3`，合法范围 `1..20`。返回 thread 的列表摘要、`summary_text` 和最近 `turn_limit` 条 turn，用于选择器或侧边栏；不会返回 checkpoint。

### 6.5 `PATCH /chat/threads/{thread_id}`

```json
{
  "thread_label": "医药板块长期跟踪"
}
```

成功返回：

```json
{
  "thread_id": "thread_a1b2c3d4e5f6",
  "thread_label": "医药板块长期跟踪"
}
```

v1 只允许修改 `thread_label`，未知字段返回 `422`。
服务端应压缩连续空白，并把标签限制为 `1..120` 个字符。

### 6.6 `POST /chat/threads/{thread_id}/forks`

从指定 thread 的当前完整上下文创建分支，无请求体。

```json
{
  "thread_id": "thread_fedcba987654",
  "parent_thread_id": "thread_a1b2c3d4e5f6"
}
```

成功返回 `201`。源 thread 不被修改。

### 6.7 `POST /chat/threads/{thread_id}/archive`

无请求体。返回：

```json
{
  "thread_id": "thread_a1b2c3d4e5f6",
  "archived_at": "2026-07-10T08:30:00Z",
  "next_thread_id": null,
  "metadata": {}
}
```

CLI 在归档当前 active thread 时会 checkpoint 并自动新建下一个 thread；REST 没有隐式 active session，因此 v1 固定 `next_thread_id=null`。重复归档保持 HTTP `200`。

### 6.8 `POST /chat/threads/{thread_id}/unarchive`

无请求体。返回：

```json
{
  "thread_id": "thread_a1b2c3d4e5f6",
  "unarchived_at": "2026-07-10T08:30:00Z",
  "metadata": {}
}
```

### 6.9 `POST /chat/threads/{thread_id}/compact`

手动压缩较早 turn，保留近期上下文。返回：

```json
{
  "thread_id": "thread_a1b2c3d4e5f6",
  "summary_text": "...",
  "last_turn_at": "2026-07-10T08:30:00Z",
  "source_turn_count": 18,
  "covered_turn_count": 10,
  "delta_turn_count": 10,
  "metadata": {
    "last_user_query": "...",
    "last_run_id": "01J...",
    "previous_summary_count": 0,
    "delta_turn_seq_start": 1,
    "delta_turn_seq_end": 10,
    "topic_title": "创新药跟踪"
  }
}
```

当前默认自动 compact 阈值为 18 个 active turns，并保留最后 8 条；该阈值是运行配置，不属于稳定 API 常量。

> 上线阻断：当前 chat thread 持久化模型没有 `owner_id` 或 `tenant_id`。在增加所有权字段、迁移旧数据并实施行级授权前，不得把这些接口暴露给多用户环境。

## 7. 提案管理接口

提案由 `methodology_evolution` 内部生成；v1 不提供人工创建、编辑或删除提案的接口。

### 7.1 `GET /admin/proposals`

查询参数：`status=pending|approved|rejected`，`limit` 默认 `50`、范围 `1..100`。

响应为数组，单项字段：

```text
proposal_id
status
proposal_type
title
sample_size
benchmark_scope
review_reason
reviewer
created_from_run_id
updated_at
display_summary
```

`proposal_type`：

```text
DimensionPoolPatch
RetrievalWeightPatch
MemoryLearningUpdate
PromptPatchProposal
MethodologyTemplatePatch
ValuationHeuristicPatch
```

### 7.2 `GET /admin/proposals/{proposal_id}`

详情字段：

```text
proposal_id, status, stored_status, proposal_type
title, summary, author_agent
sample_size, benchmark_scope
review_reason, reviewer, reviewed_at, review_history[]
created_from_run_id, created_from_review_id
before_metrics, after_metrics
evidence_summary, related_entry_ids[]
created_at, updated_at, display_summary
```

`stored_status` 是提案所在持久化目录推导出的状态，用于诊断文件路径与模型 `status` 是否一致。

`review_history[]`：

```text
decision: pending|approved|rejected
reviewer
reason
reviewed_at
previous_status
source
```

### 7.3 `POST /admin/proposals/{proposal_id}/reviews`

```json
{
  "decision": "approve",
  "reason": "样本与回测指标满足写回阈值"
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `decision` | 是 | `approve` 或 `reject` |
| `reason` | 否 | 审批理由 |

`reviewer` 必须从认证主体生成，不接受客户端伪造。成功响应：

```json
{
  "proposal_id": "01J00000000000000000000010",
  "status": "approved",
  "message": "proposal approved",
  "display_summary": {},
  "result": {
    "proposal_id": "01J00000000000000000000010",
    "status": "approved",
    "manifest": {},
    "retrieval_policy": {},
    "memory_record": {}
  }
}
```

审批通过可能依次写入 proposal 文件、`methodology_manifest.yml`、`retrieval_policy.yml` 和方法论 memory。当前流程不是事务，后段失败可能留下部分写入。FastAPI 已加进程内互斥并记录 reviewer，但尚无持久 HTTP 审计和独立失败告警。

提案进入 `approved`/`rejected` 后不可反转。对同一终态重复提交相同 decision 返回 HTTP `200` 和原终态；提交相反 decision 返回 `409 proposal_already_finalized`。这比底层当前“任何终态都 200”更严格，是 v1 适配合同。

## 8. 子 Agent 能力发现接口

发现工单由运行时识别 capability gap 后自动创建。v1 不提供人工创建或删除工单的接口。

### 8.1 `GET /admin/subagent-discoveries`

查询参数：`status=pending|approved|rejected`，`limit` 默认 `50`、范围 `1..100`。

列表项：

```text
ticket_id
status
intent
stage
capability
search_query
candidate_entry_count
activation_entry_count
recommended_candidate_subagent_type
updated_at
```

### 8.2 `GET /admin/subagent-discoveries/{ticket_id}`

返回 `SubagentDiscoveryTicketDetail`：

```text
ticket_id
status
stored_status
capability_gap
tool_discovery_request
reviewer
review_reason
reviewed_at
created_at
updated_at
candidate_catalog_entries[]
candidate_recommendation
candidate_shadow_recommendation
activated_entries[]
review_history[]
notes[]
```

`capability_gap`：

```text
gap_id
intent
stage
capability
preferred_subagent_type
requires_llm
reason
candidate_subagent_types[]
available_stage_capabilities[]
available_intents[]
policy_source: rule|llm|hybrid|explicit
notes[]
```

`tool_discovery_request`：

```text
discovery_request_id
intent
stage
capability
search_query
preferred_protocols[]   # 默认 mcp/openapi/skill/http_json
reason
policy_source
notes[]
```

候选推荐：

```text
candidate_subagent_type
source_protocol
reason
policy_source: rule|llm|hybrid|explicit|llm_shadow
confidence: 0..1|null
notes[]
```

### 8.3 `POST /admin/subagent-discoveries/{ticket_id}/reviews`

```json
{
  "decision": "approve",
  "reason": "候选工具只读且合同完整"
}
```

`decision` 只能为 `approve` 或 `reject`，`reviewer` 由认证主体生成。底层 Python 还允许传入 `candidate_catalog_entries` 覆盖候选，但这相当于注入可执行 catalog 合同，v1 HTTP 接口不开放该字段；未知字段返回 `422`。

响应：

```json
{
  "ticket_id": "01J00000000000000000000020",
  "status": "approved",
  "message": "discovery ticket approved",
  "result": {}
}
```

`result` 为完整 ticket detail。终态不可反转；同一 decision 重复提交为 HTTP `200`，相反 decision 返回 `409 discovery_ticket_already_finalized`。

### 8.4 `POST /admin/subagent-discoveries/{ticket_id}/activations`

只有 `approved` 工单可激活。请求体禁止未知字段，支持三种互斥模式。

#### A. 委托已有子 Agent

```json
{
  "candidate_subagent_type": "new_market_reader",
  "delegate_subagent_type": "market_snapshot_reader",
  "reason": "复用已审核的只读执行器"
}
```

#### B. 使用候选自带注册模板

```json
{
  "candidate_subagent_type": "new_market_reader",
  "registration_template_id": "template_akshare_market",
  "timeout_seconds": 10,
  "reason": "按审核模板激活"
}
```

#### C. 显式运行时注册

```json
{
  "candidate_subagent_type": "mcp_market_reader",
  "registration_type": "mcp_tool_runner",
  "source_id": "market_mcp",
  "tool_name": "get_market_snapshot",
  "timeout_seconds": 10,
  "reason": "只读 MCP 工具已完成审核"
}
```

完整字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `candidate_subagent_type` | string/null | 要激活的候选；唯一候选或已有 recommendation 时可省略 |
| `delegate_subagent_type` | string/null | 委托目标，只能用于 A 模式 |
| `registration_template_id` | string/null | 候选模板，只能用于 B 模式 |
| `registration_type` | enum/null | C 模式必填；B 模式如提供必须与模板一致 |
| `skill_name` | string/null | skill 类注册参数 |
| `snapshot_type` | string/null | snapshot skill 参数 |
| `source_id` | string/null | HTTP/OpenAPI/MCP 已配置数据源 |
| `tool_name` | string/null | MCP 工具名 |
| `endpoint_path` | string/null | 以 `/` 开头的相对 HTTP 路径 |
| `http_method` | enum/null | `GET` 或 `POST` |
| `openapi_operation_id` | string/null | OpenAPI operationId |
| `timeout_seconds` | number/null | 必须大于 0 |
| `reason` | string/null | 激活理由 |

`activated_by` 不接受客户端传入，由认证主体生成。注册类型：

| `registration_type` | 必要参数 | 主要约束 |
| --- | --- | --- |
| `snapshot_skill_runner` | `skill_name`, `snapshot_type` | skill 已安装；stage 仅空或 `collect` |
| `skill_runner` | `skill_name` | skill 已安装；候选有 object input/output JSON schema |
| `http_json_runner` | `source_id`, `endpoint_path`, `http_method` | 已批准无密钥数据源、相对路径、GET/POST、object 合同 |
| `openapi_http_runner` | `source_id`, `openapi_operation_id` | operation 从已配置 source/template 的 spec 解析为 GET/POST，object 合同 |
| `mcp_tool_runner` | `source_id`, `tool_name` | 已配置 stdio server、read-only 工具、object 合同 |

委托模式与运行时注册必须恰好存在一个。B 模式允许显式字段覆盖模板默认值，最终合同一致性由核心注册 builder 校验。HTTP、OpenAPI、MCP 候选只允许 `side_effect_level=none|read_only`。

为阻断任意远程 URL 和本机路径读取，FastAPI v1 **不接受** `openapi_spec_uri`；该字段会作为未知字段返回 `422 validation_error`。OpenAPI spec 必须来自管理员预先配置并审核的 source 或 registration template。

响应：

```json
{
  "ticket_id": "01J00000000000000000000020",
  "status": "approved",
  "message": "activated discovery runtime alias mcp_market_reader",
  "result": {}
}
```

`result` 为完整 ticket detail。激活项：

```text
activation_id
ticket_id
subagent_type
delegate_subagent_type
runtime_registration
catalog_entry
status: active|inactive
activated_by
activation_reason
activated_at
notes[]
```

`runtime_registration` 包含请求中的注册字段及 `notes[]`。相同 `candidate_subagent_type` 已激活时，当前底层按候选类型幂等返回既有结果，不会用新参数更新。v1 返回 HTTP `200`；首次创建返回 `201`。

当前没有 deactivate、reactivate、update 或 delete 方法，不能在 v1 中虚构这些接口。

#### `SubagentCatalogEntry`

候选和激活详情中的 catalog entry：

```text
subagent_type
summary
capabilities[]
stage
cost_class: low|medium|high|null
latency_class: low|medium|high|null
side_effect_level: none|read_only|write_local|write_external
suitable_intents[]
tags[]
requires_llm
allow_parallel
max_concurrency
fallback_subagent_type
error_fallback_subagent_type
input_schema_name
output_schema_name
input_json_schema
output_json_schema
runtime_registration_hints
runtime_registration_templates[]
```

仓库没有统一全局 catalog：collector、retriever、reporter 等 service 各自维护局部 Registry。因此 v1 不提供误导性的全局 catalog 接口。若 v2 增加 catalog 查询，必须携带 service provenance 并定义聚合去重规则。

## 9. 系统诊断与维护接口

### 9.1 `GET /system/status`

返回环境和最近运行摘要：

```json
{
  "project_root": "/srv/cognitrade",
  "workspace_root": "/srv/cognitrade/workspace",
  "runs_root": "/srv/cognitrade/workspace/runs",
  "qdrant_mode": "local",
  "qdrant_collection_name": "research_evidence",
  "provider_name": "openai",
  "default_model": "GPT-5.5",
  "total_runs": 12,
  "status_counts": {"completed": 10, "failed": 2},
  "recent_runs": []
}
```

`recent_runs` 固定最多 5 条，是 summary/run state 的开放对象，并补充 `run_id`、可选 `display_summary` 和 `child_thread_count`。

此接口只读取配置和本地运行状态，不探测 OpenAI、Qdrant 或数据源，不能作为 readiness。响应当前会包含绝对路径且没有非管理员视图。FastAPI 在服务启动时初始化 workspace，使后续 GET 不负责 seed 布局。

### 9.2 `POST /system/doctor`

执行配置、目录、provider chain 和有限网络诊断，无请求体。可能发生网络 I/O，所以使用 POST。

```json
{
  "status": "completed",
  "checks": [
    {"name": "openai_api_key", "status": "passed"},
    {"name": "network_domestic", "status": "passed", "detail": "..."}
  ]
}
```

check 单项至少有 `name`、`status=passed|failed|skipped`，并可能有 `path` 或 `detail`。只要有一项 failed，顶层为 `failed`；业务诊断失败仍返回 HTTP `200`，执行器异常才返回 `500`。

诊断覆盖：

- `.env`、OpenAI key/base URL 和超时配置；
- router 默认值、缓存、Redis；
- market/fundamental/notice/sector/lhb/capital_flow/sentiment/events/policy provider chain；
- 国内/海外网络；
- retrieval、methodology、provider、data source、MCP、taxonomy、review 配置；
- workspace、runs、reports、memory、taxonomy、Qdrant 路径或 URL；
- runs 是否存在。

限制：OpenAI 只检查 key 是否存在；远程 Qdrant 只检查 URL 配置，不探活；真正外部网络探测仅是部分站点 HEAD 和可选 Redis health。不得把 doctor 宣称为完整依赖 readiness。URL、错误 detail 和路径当前没有面向低权限主体的独立脱敏视图。

### 9.3 `POST /admin/system/checks`

执行以下同步检查：

1. `compileall research_system/`；
2. 如有 tests，`compileall tests/`；
3. `unittest discover -p 'test_*.py'`。

```json
{
  "status": "completed",
  "checks": [
    {"name": "compile_research_system", "status": "passed", "path": "..."},
    {
      "name": "unit_tests",
      "status": "passed",
      "tests_run": 120,
      "failures": 0,
      "errors": 0,
      "skipped": 2,
      "output": "..."
    }
  ]
}
```

此操作耗时且会生成 `__pycache__`。当前由服务级 token 和 mutation lock 保护，但没有独立 `admin:operate` 角色或接口级超时；生产部署仍应在网关限制访问和超时。v1 按现有同步语义返回 HTTP `200`；如未来改为异步 job，属于 v2 合同。`output` 可能包含堆栈和本地路径。

### 9.4 `POST /admin/artifacts/cleanup`

按配置清理过期 run artifact，无请求体、没有 dry-run。

```json
{
  "artifacts_retention_days": 30,
  "max_artifacts_size_mb": 1024,
  "removed_files": [],
  "skipped_runs": [],
  "artifact_bytes_after_cleanup": 0,
  "artifact_size_limit_bytes": 1073741824,
  "size_limit_exceeded": false
}
```

以 `workspace/runs/*` 的 run 目录 mtime 判断过期；过期后删除该 run 的 `artifacts/**` 文件，不删除 run、checkpoint 或其他运行记录。大小上限当前只报告，不强制删除。单文件删除失败会把 run 加入 `skipped_runs` 并继续。返回的绝对路径当前未脱敏。

### 9.5 `POST /admin/qdrant/reindex`

先重置 collection，再扫描每个 run 的 blackboard，重建以下 kind 的索引：

```text
source_record
verified_snapshot
logic_block
distilled_dimension
review_finding
```

Qdrant 不可用：

```json
{
  "status": "skipped",
  "message": "Qdrant unavailable",
  "qdrant_error": "..."
}
```

完成：

```json
{
  "status": "completed",
  "message": "Qdrant collection rebuilt",
  "collection_name": "research_evidence",
  "indexed_runs": 12,
  "indexed_entries": 842,
  "skipped_entries": 3,
  "skipped_runs": []
}
```

这是破坏性、非事务操作：reset 后 upsert 中途异常会留下部分索引。当前只有服务级 token 和全局互斥锁，没有独立管理员角色或持久审计日志；部署方必须额外限制入口，并只在维护窗口执行。执行异常返回 `500`。

## 10. 研究意图与 `constraints` 合同

核心 `RequestEnvelope.constraints` 当前是未类型化 object，很多错误直到运行中才暴露。显式提供 `intent` 时，v1 适配层按本节做意图相关校验；省略 `intent` 时只能按所有公开字段的并集校验，核心推断出意图后不会再次回到 API 层复验。未知约束字段返回 `422 unknown_constraint`，内部字段除外部明确列出者不得暴露。

### 10.1 公共约束

| 字段 | 类型 | 默认 | 适用范围 | 说明 |
| --- | --- | --- | --- | --- |
| `symbol` | string | 查询解析 | 单股/比较 | 单股票代码或核心可识别名称 |
| `symbols` | string[] | 查询解析 | 比较 | 显式传入时 1..20 个、不得重复；与可选 `symbol` 合并后至少 2 个不同标的 |
| `sector_query` | string | 查询解析 | 筛选/板块 | 板块、行业或主题查询 |
| `investment_cycle` | enum | `mid_term` | 股票/筛选/复核 | `short_term`、`mid_term`、`long_term` |
| `planning_context_hints` | object | `{}` | 多数意图 | 规划偏好与上下文提示 |
| `snapshot_types` | enum[] | 见意图 | 股票/比较/复核 | 股票快照包 |
| `allow_partial_snapshot_types` | boolean | `false` | 股票/比较/复核 | 可选快照失败后是否继续 |
| `include_policy_snapshot` | boolean | `false` | 板块/复核 | 是否追加政策快照 |

股票 `snapshot_types` 允许：

```text
market
fundamental
notice
lhb
capital_flow
sentiment
events
```

其中只有 `lhb`、`capital_flow`、`sentiment`、`events` 属于可跳过的 optional 类型。`sector` 和 `policy` 虽然属于全局 `SnapshotType`，但传给股票 snapshot bundle 会产生 `unsupported snapshot_type`。

`planning_context_hints` 识别：

```text
thread_topic
thread_intent
previous_summary
recommended_next_step
open_question
thread_status
methodology_benchmark_scope
methodology_memory_type
methodology_memory_match_score
```

它也可由 `conversation_context` 中 `type=chat_thread_profile`、`relevant_thread_profile`、`relevant_methodology_memory` 的对象生成。

`source_type` 是内部采集来源覆盖，不作为普通 v1 客户端字段；`routing_trace` 和 `chat_dispatch` 也是内部约束。

### 10.2 `stock_check`

默认执行图：`collect → retrieve → report`。

```json
{
  "intent": "stock_check",
  "user_query": "分析贵州茅台 600519 的长期投资价值",
  "constraints": {
    "symbol": "600519",
    "snapshot_types": ["market", "fundamental", "notice", "capital_flow"],
    "allow_partial_snapshot_types": true,
    "investment_cycle": "long_term",
    "planning_context_hints": {
      "recommended_next_step": "focus on cashflow resilience"
    }
  }
}
```

| 约束 | 必填 | 默认 |
| --- | --- | --- |
| `symbol` | 是；也可从 query 解析 | - |
| `snapshot_types` | 否 | `market,fundamental,notice` |
| `allow_partial_snapshot_types` | 否 | `false` |
| `investment_cycle` | 否 | `mid_term` |
| `planning_context_hints` | 否 | `{}` |

正常采集后会生成 `comps`、`dcf`、`cashflow_resilience` 三种方法论结果，但最终 report 中只保留投影摘要，完整估值对象保存在黑板/replay。

### 10.3 `stock_compare`

默认执行图：`collect → retrieve → report`。

```json
{
  "intent": "stock_compare",
  "user_query": "比较招商银行、江苏银行和成都银行",
  "constraints": {
    "symbols": ["600036", "600919", "601838"],
    "snapshot_types": ["market", "fundamental", "notice"],
    "allow_partial_snapshot_types": false,
    "investment_cycle": "mid_term"
  }
}
```

显式提供 `symbols` 时，重复值返回 `422`；`symbol` 如同时提供会参与不同标的数量检查。只提供 `symbol` 或完全不提供结构化标的时，API 允许核心继续从 `user_query` 解析，当前无法在传输层保证最终得到两个标的。银行类比较会额外生成 `bank_compare_scorecard`。

### 10.4 `stock_screen`

默认执行图：`collect → screen → [screen_enrich] → [screen_review] → retrieve → report`，方括号阶段由配置和结果动态决定。

```json
{
  "intent": "stock_screen",
  "user_query": "筛选创新药板块值得跟踪的公司",
  "constraints": {
    "sector_query": "创新药板块",
    "candidate_pool_size": 12,
    "investment_cycle": "mid_term",
    "prefer_methodology": "comps",
    "top_k": 5,
    "min_score": 0.8,
    "watchlist_top_k": 3,
    "watchlist_min_score": 0.25,
    "enrichment_top_n": 2,
    "skip_enrichment": false
  }
}
```

| 字段 | 类型 | 默认值 | 约束 |
| --- | --- | --- | --- |
| `sector_query` | string | 查询解析/全市场切片 | 可选 |
| `candidate_pool_size` | integer | `8` | 建议 `1..100` |
| `investment_cycle` | enum | `mid_term` | 公共枚举 |
| `prefer_methodology` | enum | `comps` | `comps/dcf/cashflow_resilience` |
| `top_k` | integer | `5` | `1..candidate_pool_size` |
| `min_score` | number | 短 `1.0`、中 `0.75`、长 `0.5` | 有限数字 |
| `watchlist_top_k` | integer | `2` | `0..candidate_pool_size` |
| `watchlist_min_score` | number | 中 `0.25`，其他 `0.0` | 有限数字 |
| `enrichment_top_n` | integer | `2` | `0..candidate_pool_size` |
| `skip_enrichment` | boolean | `false` | - |
| `planning_context_hints` | object | `{}` | - |

当前只有 `prefer_methodology=comps` 会在筛选阶段直接挂载 Comps；其他值主要影响策略和报告说明。

### 10.5 `sector_research`

默认执行图：`collect → [动态补采] → retrieve → report`。

```json
{
  "intent": "sector_research",
  "user_query": "最近创新药板块怎么样",
  "constraints": {
    "sector_query": "创新药板块",
    "include_policy_snapshot": true,
    "planning_context_hints": {
      "recommended_next_step": "validate policy catalysts"
    }
  }
}
```

`sector_query` 必须提供或可从 query 解析。`include_policy_snapshot=true` 时，在主 `sector` snapshot 外追加 `policy` snapshot。

### 10.6 `distill_kol_logic`

默认执行图：`collect → distill → critic → retrieve → report`。

当前结构化 `slots/constraints` 对该意图没有实际作用，`.jsonl` 路径必须直接出现在 `user_query`：

```json
{
  "intent": "distill_kol_logic",
  "user_query": "蒸馏 C:\\data\\xueqiu\\statuses.jsonl",
  "slots": {},
  "constraints": {}
}
```

`schemas/task_requests.py` 虽定义 `source_ref`，但编排器入口没有读取。当前正则只识别 Windows 盘符路径，且 FastAPI 尚未实现允许目录白名单，因此该意图只能向与服务端处于同一信任边界的调用方开放；不要把它暴露给不受信任用户。将 `source_ref` 变成正式上传/对象存储引用属于 v2 变更。

### 10.7 `review_thesis`

默认执行图：`collect → [review] → retrieve → report`。

```json
{
  "intent": "review_thesis",
  "user_query": "复核上次创新药判断",
  "constraints": {
    "review_target_run_id": "01JTARGET...",
    "snapshot_types": ["market", "fundamental", "notice", "events"],
    "include_policy_snapshot": true,
    "investment_cycle": "mid_term",
    "planning_context_hints": {
      "recommended_next_step": "validate catalyst and relative direction"
    }
  }
}
```

`review_target_run_id` 必填；缺失时返回 `422 review_target_run_id_required`，不存在时由核心返回 `404`。当前没有 owner/tenant 模型，无法执行跨租户隐藏或授权。

### 10.8 `methodology_evolution`

这是内部方法论演进链路，不应授予普通用户。`target_run_id` 或 `target_run_ids` 至少一个必填。

```json
{
  "intent": "methodology_evolution",
  "user_query": "基于这些运行演进研究方法论",
  "constraints": {
    "target_run_ids": ["01JTARGETA...", "01JTARGETB..."],
    "auto_approve": true,
    "force_auto_approve": false,
    "min_dimension_sample_size": 2,
    "min_valuation_sample_size": 2,
    "min_review_pattern_sample_size": 2,
    "min_dimension_average_confidence": 0.5,
    "min_valuation_average_confidence": 0.5,
    "max_memory_support_age_days": 180,
    "min_dimension_memory_support_entity_scope_count": 2,
    "min_valuation_memory_support_entity_scope_count": 2
  }
}
```

`auto_approve` 默认 `true`，会执行自动门控并可能写配置/memory。`force_auto_approve=true` 绕过阈值，但仍需 `auto_approve=true` 才实际批准。当前服务级 token 持有者均可调用，且没有独立审计记录；实现 `admin:review` 权限和持久审计前，不得向普通用户开放。

自动生成器当前产生 `MethodologyTemplatePatch`、`ValuationHeuristicPatch`、`MemoryLearningUpdate`；完整 proposal 通过第 7 节接口读取，run report 本身只含 evidence 投影。

## 11. 业务响应与领域 Schema

### 11.1 `result.report` 联合结构

成功响应固定包裹为：

```json
{
  "intent": "stock_check",
  "report": {}
}
```

当前没有统一 `ReportResponse` Pydantic 模型，LLM、fallback 和银行确定性分支存在字段差异。v1 把 report 定义为按 intent 区分的联合结构。

所有正常分支应读取的公共字段：

| 字段 | 类型 | 必填性 | 说明 |
| --- | --- | --- | --- |
| `conclusion` | string | 是 | 最终结论 |
| `evidence` | `ReportEvidence[]` | 是 | 主要证据，普通分支通常最多 5 条、比较分支可更多 |
| `risk` | string | 是 | 主要风险 |
| `follow_up` | string/null | 否 | 建议下一步 |
| `degraded_note` | string/null | 否 | 降级说明 |
| `subagent_trace` | object[] | 否 | 子 Agent 摘要追踪 |
| `subagent_plans` | object[] | 否 | 子计划 |
| `subagent_plan_patches` | object[] | 否 | 子计划补丁 |
| `debug` | object | 否 | 大型诊断字段，不应作为普通 UI 合同 |
| `scorecard` | object/null | 否 | 蒸馏专用 |
| `screening_scorecard` | object/null | 否 | 筛选专用 |
| `candidates` | object[]/null | 否 | 筛选候选 |
| `methodologies` | object[]/null | 否 | 方法论文本投影 |
| `review_summary` | object/null | 否 | 复核摘要 |
| `degraded` | boolean | 否 | 是否整体降级 |
| `degrade_reason` | string/null | 否 | 降级原因 |

确定性银行比较分支和部分 fallback 不保证输出所有可选字段。普通客户端应忽略 `debug`，需要过程诊断时使用 trace/steps/events 接口。

### 11.2 `ReportEvidence`

```json
{
  "evidence_id": "01J...",
  "title": "market_snapshot",
  "summary": "market snapshot for 600519...",
  "metadata": {
    "phase": "collect",
    "agent": "collector",
    "record_type": "verified_snapshot",
    "source_type": "market_snapshot",
    "dimension_family": null,
    "scenario_tags": [],
    "methodology_tags": [],
    "methodology_type": null,
    "peer_group_tags": [],
    "assumption_tags": [],
    "sensitivity_tags": [],
    "valuation_band_label": null,
    "memory_priority_rank": -1
  },
  "source_refs": [],
  "retrieval_hint": null,
  "score": 1.0
}
```

稳定最小字段为 `evidence_id`、`title`、`summary`、`metadata`、`source_refs`；`retrieval_hint` 和 `score` 在部分路径可缺失。`source_refs` 是开放对象数组，不能假设所有 provider 都提供相同键。

### 11.3 `stock_check` / `stock_compare` 方法论投影

```json
{
  "title": "methodology_dcf:600519",
  "source_type": "methodology_dcf",
  "methodology_type": "dcf",
  "valuation_band_label": "120.0|150.0|180.0",
  "summary": "methodology_result type=dcf symbol=600519\n..."
}
```

`methodologies[]` 只是投影，不是完整 valuation object。比较结果也没有稳定的顶层 `compare_targets` 或 `ranked_candidates`；排序可能在 `conclusion`、`evidence[].retrieval_hint` 或 `methodologies[].summary`。

银行比较的原始 `bank_compare_scorecard` 主要字段：

```text
scorecard_type
candidate_count
ranked_candidates[]
metric_weights
missing_metrics[]
summary
```

`ranked_candidates[]` 含 `symbol,name,score,weight_coverage,latest_price,latest_pct_change,latest_amount,pb,pe_ttm,market_cap,float_market_cap,market_total_shares,market_float_shares,banking_metrics,highlights,missing_metrics,rank`。

### 11.4 `stock_screen` 报告

专有字段：`screening_scorecard`、`candidates[]`、`methodologies[]`、`review_summary`。

报告候选投影：

```json
{
  "symbol": "300363",
  "name": "博腾股份",
  "title": "screen_candidate:300363",
  "summary": "...",
  "score": 3.25,
  "why_it_made_the_list": "...",
  "main_risk": "...",
  "missing_evidence": [],
  "deep_checked": true,
  "candidate_tier": "core"
}
```

`candidate_tier` 为 `core|watchlist`。黑板中的原始候选还可能有：

```text
sector, theme, latest_price, latest_pct_change, latest_amount
revenue_proxy, notice_count, has_financial_abstract
score_breakdown
degraded_sources[]
capital_flow_score, sentiment_score, lhb_score, event_score
methodology, valuation_band, valuation_view
```

`score_breakdown` 当前键：`liquidity,momentum,fundamental,notice,capital_flow,sentiment,lhb,events,deep_check_bonus,evidence_penalty`。

筛选 scorecard：

```text
investment_cycle
investment_cycle_assumption
candidate_count
eligible_candidate_count
top_candidate_count
watchlist_candidate_count
deep_checked_count
watchlist_deep_checked_count
enrichment_target_count
enrichment_pending
min_score
watchlist_min_score
preferred_methodology
methodology_notes[]
average_score
sector_context_present
summary
planning_context_hints
screening_strategy
```

### 11.5 `sector_research` 报告

没有稳定的专有顶层结构。板块快照及其 `facts.signal_scorecard` 通过 `evidence` 投影。不要把通用 `scorecard` 误当成板块 signal scorecard。

### 11.6 `distill_kol_logic` 报告

专有 `scorecard`：

```json
{
  "total_logic_blocks": 8,
  "speaker_type_counts": {
    "author": 4,
    "reply": 1,
    "repost": 1,
    "reported": 0,
    "quoted": 1,
    "uncertain": 1
  },
  "author_ratio": 0.5,
  "quoted_like_ratio": 0.5,
  "review_strength": "medium",
  "attribution_risk": "high",
  "setup_label": "mixed_attribution",
  "summary": "...",
  "finding_types": ["quoted_attribution"]
}
```

枚举：`review_strength`、`attribution_risk` 为 `low|medium|high`；`setup_label` 为 `author_led|mixed_attribution|quote_dominated`。Logic blocks、dimensions、critic findings 通过 `evidence[]` 投影，不直接作为 report 顶层数组。

### 11.7 `review_thesis` 报告

`review_summary`：

```text
review_required
finding_count
reviewable_claim_count
claim_type_counts
benchmark_scope_counts
benchmark_scope_explanations
verdict_by_claim_type
overall_verdict
summary_text
confirmed
rejected
inconclusive
notes[]
```

`overall_verdict` 当前是自由字符串，常见值为：

```text
needs_review
no_findings
confirmed
confirmed_with_caveats
rejected
mixed
inconclusive
```

### 11.8 `methodology_evolution` 报告

没有顶层 `proposals[]`。proposal draft 只以 evidence 投影出现：

```json
{
  "title": "proposal_draft:MethodologyTemplatePatch",
  "summary": "proposal_draft type=MethodologyTemplatePatch status=pending\n...",
  "metadata": {"source_type": "proposal_draft"}
}
```

完整提案必须通过 `/admin/proposals` 获取。

### 11.9 `VerifiedSnapshot`

`SnapshotType`：

```text
market
fundamental
notice
sector
lhb
capital_flow
sentiment
events
policy
```

`SnapshotSource`：

```json
{
  "skill": "market_snapshot_skill",
  "vendor": "akshare",
  "from_cache": false,
  "provider_id": "akshare_market",
  "source_id": "akshare",
  "chain_position": 0,
  "dim": "market"
}
```

`skill`、`vendor` 必填；其余字段可为 null 或默认值。

`VerifiedSnapshot`：

```json
{
  "snapshot_type": "market",
  "symbol": "600519",
  "sector": null,
  "theme": null,
  "query": "分析 600519",
  "snapshot_date": "2026-07-10",
  "facts": {},
  "source": {},
  "primary_source": null,
  "fallback_sources": [],
  "field_sources": {},
  "fallback_applied": false,
  "missing_fields": [],
  "source_confidence": "medium",
  "source_trace": [],
  "degraded": false,
  "degrade_reason": null,
  "created_at": "2026-07-10T08:00:00Z"
}
```

`facts` 是供应商相关开放 object。当前主要键：

| 类型 | `facts` 主要字段 |
| --- | --- |
| `market` | `market_hist`, `market_overview` |
| `fundamental` | `symbol`, `info`, `financial_abstract`, `financial_analysis`, `financial_abstract_new`, `financial_benefit_new`, `financial_debt_new`, `financial_cash_new`, `banking_metrics`, `bank_peer_comparison` |
| `notice` | `symbol`, `window_start`, `window_end`, `latest_notice`, `recent_notices`, `notice_count` |
| `sector` | `query`, `sector_name`, `sector_type`, `start_date`, `end_date`, `data_source`, `spot`, `latest_hist`, `recent_hist`, `top_constituents`, `constituent_count`, `benchmark`, `relative_strength`, `catalyst_samples`, `theme_news`, `policy_signals`, `regulatory_signals`, `breadth`, `signal_scorecard`, `catalyst_digest` |
| `lhb` | `symbol`, `latest_lhb_date`, `buy_detail`, `sell_detail`, `lhb_statistics`, `buy_top_total`, `sell_top_total` |
| `capital_flow` | `symbol`, `market`, `latest_flow`, `recent_flows`, `main_flow_rank` |
| `sentiment` | `symbol`, `market_symbol`, `hot_rank_latest`, `hot_rank_detail`, `hot_keywords`, `news_samples` |
| `events` | `symbol`, `event_notices`, `event_keywords` |
| `policy` | `theme`, `policy_signals`, `regulatory_signals`, `policy_digest` |

降级 snapshot 会设置 `degraded=true`、`degrade_reason`，并可能在 `facts` 中只含 `availability=degraded` 和 `note`。`source_confidence` 当前是自由字符串，常见 `medium/high`；客户端不得当作封闭 enum。

### 11.10 估值模型

完整估值模型仅在 replay/blackboard 等内部详情中保证出现；report 使用第 11.3 节投影。

公共结构：

```json
{
  "methodology_type": "dcf",
  "target_symbol": "600519",
  "inputs": {},
  "assumptions": {},
  "outputs": {},
  "sensitivity": {},
  "warnings": [],
  "evidence_refs": [],
  "confidence": 0.65,
  "assumption_tags": [],
  "sensitivity_tags": [],
  "valuation_band": {
    "low": 120.0,
    "base": 150.0,
    "high": 180.0,
    "currency": "CNY"
  },
  "created_at": "2026-07-10T08:00:00Z"
}
```

| 模型 | `methodology_type` | 专有字段/典型输出 |
| --- | --- | --- |
| `CompsModelResult` | `comps` | `peer_group[]`, `peer_group_tags[]`; `outputs.relative_view` |
| `DCFModelResult` | `dcf` | `base_fcf_proxy`, `present_value`, `terminal_value`, `intrinsic_value_proxy`, `market_to_intrinsic_gap` |
| `CashflowResilienceResult` | `cashflow_resilience` | `cashflow_state`, `debt_capacity_state`, `resilience_score`, `stressed_margin` |

`confidence` 范围 `0..1`。`ValuationBand.low/base/high` 均可为 null，默认币种 `CNY`。

`ValuationPeer` 字段：`symbol,name,sector,theme,latest_price,latest_pct_change,latest_amount,revenue_proxy,peer_score,percentile,notes[]`。

`outputs.relative_view` 常见：`insufficient_peer_signal|relative_discount_like|relative_premium_like|growth_supported_neutral|relative_fair_like`。现金流状态常见 `resilient|mixed|fragile`，偿债状态常见 `comfortable|watch|stressed`；这些值位于开放 `outputs` object，当前 schema 没有强制枚举。

### 11.11 检索模型

领域 schema：

```json
{
  "query": {
    "intent": "stock_check",
    "symbol": "600519",
    "sector": null,
    "themes": [],
    "dimensions": [],
    "time_range": {}
  },
  "evidences": [
    {
      "evidence_id": "01J...",
      "title": "market_snapshot",
      "summary": "...",
      "source_refs": [],
      "metadata": {},
      "score": 1.0
    }
  ],
  "generated_at": "2026-07-10T08:00:00Z"
}
```

实际 Retriever 运行 payload 发生 schema drift，常见为：

```text
intent
query                    # 实际可能是原始字符串而非 RetrievalQuery
evidences[]
degraded
degrade_reason
retrieval_summary
subagent_trace[]
subagent_plans[]
subagent_plan_patches[]
```

v1 对外只通过 report evidence 暴露稳定投影；replay 客户端必须容忍上述两种形态。

### 11.12 蒸馏模型

枚举：

```text
LogicBlockType: claim|evidence|risk|catalyst
SpeakerType: author|reply|quoted|repost|reported|uncertain
DirectionType: bullish|bearish|neutral|uncertain
```

`DistillSourceRecord`：`source_id,title,content,source_type,source_url,author,platform,published_at,metadata,created_at`。

`LogicBlock`：

```text
block_id
block_type
source_record_id
source_span: {start_line>=1, end_line>=1, excerpt}
speaker_type
attribution_label
text
normalized_claim
symbols[]
sectors[]
time_horizon
direction
evidence_refs[]
risk_flags[]
confidence: 0..1
```

`DistilledDimension`：

```text
dimension_id
dimension_name
canonical_dimension_name
dimension_family
dimension_category
alias_hits[]
supported_claim_block_ids[]
evidence_block_ids[]
is_new_candidate
confidence: 0..1
scenario_tags[]
methodology_tags[]
dimension_tags[]
dimension_key
normalization_notes[]
```

`DistillationResult`：

```json
{
  "source_record_id": "source-1",
  "summary": {
    "content_type": "text_records",
    "overall_confidence": 0.72,
    "needs_review": true,
    "major_risks": [],
    "processing_notes": []
  },
  "logic_blocks": [],
  "distilled_dimensions": []
}
```

实际运行 payload 还会增加 `degraded`、`degrade_reason` 和三个 subagent trace/plan 字段。

### 11.13 复核模型

`ReviewFinding`：

```text
review_id
claim_id
claim_type: direction|catalyst|time_window|null
source_run_id
target_entry_id
window_type: trading_days|calendar_days
window_size
anchor_date
evaluation_start_rule
early_invalidation_rule
benchmark_scope
benchmark_symbol
benchmark_name
expected_direction: outperform|underperform|neutral|null
observed_metric
observed_benchmark_metric
excess_metric
verdict: confirmed|rejected|inconclusive
notes
created_at
```

`benchmark_scope` 是自由字符串，常见 `stock_vs_sector|sector_vs_hs300|stock_absolute|screen_candidate`。

`CriticReviewFinding`：

```text
finding_id
target_entry_id
target_kind: logic_block|distilled_dimension|distillation_summary
finding_type
severity: low|medium|high
verdict: needs_review|pass
reason
suggested_action
created_at
```

`finding_type`：`fallback_distillation|llm_timeout|low_confidence|quoted_attribution|low_information_density|sparse_dimension_coverage|duplicate_claim_cluster`。

### 11.14 黑板、gate 与运行记录

`BlackboardEntry`：

```text
entry_id
run_id
thread_id
request_id
kind
agent
phase
status
payload
source_refs[]
confidence: 0..1|null
risk_flags[]
created_at
```

`kind`：

```text
source_record
verified_snapshot
logic_block
distilled_dimension
retrieved_evidence
report_fragment
review_finding
proposal_draft
```

`status` 为 `active|superseded|needs_review`。

`GateVerdict`：

```text
gate_id
run_id
gate_type
verdict: pass|fail|needs_review
reason
blocking_items[]
created_at
```

`RuntimeItemRecord`：`item_id,thread_id,turn_id,run_id,request_id,item_type,status,agent,phase,title,objective,payload,degrade_reason,created_at,updated_at`。`item_type` 为 `action|agent|subagent|thread|memory|degradation`。

## 12. 状态、事件与执行语义

### 12.1 状态层级

这些状态属于不同模型，客户端不得混用：

| 模型 | 合法值 |
| --- | --- |
| `ResponseStatus` | `accepted, needs_clarification, running, completed, cancelled, failed, timed_out` |
| `OrchestratorStatus` | `received, identifying_intent, needs_clarification, ready, running, waiting_external, gated, completed, cancelled, failed, timed_out` |
| `ThreadStatus` | `ready, running, waiting, completed, failed, cancelled` |
| `TurnStatus` | `ready, running, completed, failed, cancelled` |
| `ItemStatus` | `queued, running, completed, failed, degraded, cancelled` |

### 12.2 `RunEvent`

```json
{
  "event_id": "01J...",
  "request_id": "01J...",
  "run_id": "01J...",
  "intent": "stock_check",
  "event_type": "agent_completed",
  "phase": "collect",
  "agent": "collector",
  "message": "Collection completed",
  "payload": {},
  "artifacts": [],
  "created_at": "2026-07-10T08:30:00Z"
}
```

`event_type` 是开放字符串。当前源码中的已知值如下，但不构成封闭枚举：

- 生命周期：`routing_turn_completed`, `request_received`, `intent_identified`, `plan_created`, `run_accepted`, `phase_started`, `phase_completed`, `phase_failed`, `agent_started`, `agent_progress`, `agent_completed`, `agent_failed`, `agent_updated`, `agent_trace`, `review_requested`, `review_completed`, `blackboard_entry_added`, `run_completed`, `run_cancelled`, `run_failed`, `run_timed_out`, `runtime_degraded`, `runtime_degradation_injected`。
- 顶层 DAG：`dag_initialized`, `dag_frontier_updated`, `dag_retry_scheduled`, `dag_node_completed`, `dag_node_isolated_failure`, `dag_dependency_blocked`, `dag_cache_hit`, `dag_partial_rerun_requested`。
- 子 Agent/线程：`subagent_started`, `subagent_completed`, `subagent_failed`, `subagent_updated`, `subagent_trace`, `thread_started`, `thread_completed`, `thread_failed`, `thread_updated`, `thread_trace`；以及 `subagent_dag_initialized`, `subagent_dag_frontier_updated`, `subagent_dag_retry_scheduled`, `subagent_dag_node_completed`, `subagent_dag_node_failed`, `subagent_dag_node_isolated_failure`, `subagent_dag_dependency_blocked`, `subagent_dag_cache_hit`, `subagent_dag_cache_restored`, `subagent_dag_partial_rerun_requested`。
- 动态规划：`supervisor_decision`, `action_plan_patch_applied`, `action_plan_replan_requested`, `action_plan_replanned`, `dynamic_action_inserted`, `action_phase_shadow_suggested`, `action_phase_llm_applied`, `action_phase_hybrid_applied`, `action_phase_decided`。
- Runtime control：`runtime_control_transition_decided`, `runtime_control_protocol_resolved`, `runtime_control_subagent_resolved`, `runtime_control_subagent_phase_resolved`, `runtime_control_subagent_protocol_resolved`, `runtime_control_subagent_transition_decided`。
- capability/discovery：`subagent_catalog_selection_decided`, `subagent_phase_execution_decided`, `subagent_phase_shadow_suggested`, `subagent_phase_llm_applied`, `subagent_phase_hybrid_applied`, `subagent_capability_gap_detected`, `subagent_capability_gap_escalated`, `subagent_tool_discovery_requested`, `subagent_tool_discovery_auto_activated`, `subagent_tool_discovery_protocol_decided`, `subagent_tool_discovery_candidate_decided`, `subagent_tool_discovery_review_decided`, `subagent_tool_discovery_activation_decided`, `subagent_discovery_shadow_suggested`, `subagent_discovery_llm_applied`, `subagent_discovery_hybrid_applied`, `subagent_discovery_candidate_shadow_suggested`, `subagent_discovery_candidate_llm_applied`, `subagent_discovery_candidate_hybrid_applied`。

规划合同中还出现过 `clarification_requested`、`clarification_resolved`、`mailbox_message_sent`、`proposal_created`、`run_resumed`，但当前运行路径未必发送，不能作为 v1 保证。

### 12.3 计划模型

`ActionSpec` 核心字段：

```text
action_id, node_id, phase, agent, objective, inputs
status: pending|completed|skipped
tags[], depends_on[]
max_retries
failure_mode: halt|isolate
cache_scope: run|thread|global|null
cache_key
replayable
```

`ActionPlan`：`plan_id,intent,strategy,actions,notes,entry_strategy,entry_reason,subagent_required,dispatch_trace`。`strategy` 为 `static|dynamic`，`entry_strategy` 为 `rule|llm|explicit_intent`。

计划补丁 decision 为 `continue|stop|replan`，operation 可能是 `insert_action|remove_action|rewire_dependency|replace_subgraph|rerun_from|mark_terminal|request_replan`。

## 13. 当前真实 Python 与 CLI 接口

本节记录当前已经可调用的后端表面，是实现 `/api/v1` 适配器时的来源依据。

### 13.1 FastAPI 应用

```text
research_system/api/app.py            create_app()、lifespan、CORS、request id
research_system/api/dependencies.py   runtime state、Bearer principal、依赖注入
research_system/api/routes/           37 个版本化业务操作
research_system/api/models.py         HTTP 请求模型
research_system/api/validation.py     intent constraints 与 path id 校验
research_system/api/errors.py         统一错误 envelope
research_system/api/sse.py            events.jsonl 尾随与 SSE 编码
research_system/api/chat_service.py   Chat thread 传输无关 facade
```

ASGI import：

```text
research_system.api.app:app
```

应用 lifespan 显式以仓库根目录构建 `Settings`、校验并 seed workspace，然后创建一个长生命周期 `ResearchOrchestrator`。shutdown 调用 `close()` 释放本地 Qdrant。`create_app(settings=..., api_config=..., orchestrator_factory=...)` 支持测试或嵌入式注入。

### 13.2 `ResearchOrchestrator`

构造：

```python
ResearchOrchestrator(
    settings: Settings,
    event_listener=None,
    dependencies: OrchestratorDependencies | None = None,
)
```

公开方法：

```python
run(request: RequestEnvelope) -> ResponseEnvelope

replay(run_id: str) -> dict
trace(run_id: str) -> dict
events(run_id: str) -> dict
metrics(run_id: str) -> dict
steps(run_id: str) -> dict
history() -> list[dict]

thread(run_id: str, thread_id: str) -> dict
thread_replay(run_id: str, thread_id: str) -> dict
thread_metrics(run_id: str, thread_id: str) -> dict
subagent(run_id: str, invocation_id: str) -> dict

resume(run_id: str) -> dict
cancel(run_id: str) -> dict
rerun_from(run_id: str, *, from_node: str) -> ResponseEnvelope
rerun_subagent_from(
    run_id: str,
    *,
    action_node: str,
    dag_id: str,
    from_node: str,
) -> ResponseEnvelope

list_proposals(*, status: str | None = None, limit: int = 50) -> list[dict]
show_proposal(proposal_id: str) -> dict
review_proposal(
    proposal_id: str,
    *,
    decision: str,
    reviewer: str = "manual_cli_review",
    reason: str | None = None,
) -> dict

list_subagent_discovery_requests(
    *, status: str | None = None, limit: int = 50
) -> list[dict]
show_subagent_discovery_request(ticket_id: str) -> dict
review_subagent_discovery_request(
    ticket_id: str,
    *,
    decision: str,
    reviewer: str = "manual_cli_review",
    reason: str | None = None,
    candidate_catalog_entries: list[dict] | None = None,
) -> dict
activate_subagent_discovery_request(
    ticket_id: str,
    *,
    delegate_subagent_type: str | None = None,
    candidate_subagent_type: str | None = None,
    registration_template_id: str | None = None,
    activated_by: str = "manual_cli_activation",
    reason: str | None = None,
    registration_type: str | None = None,
    skill_name: str | None = None,
    snapshot_type: str | None = None,
    source_id: str | None = None,
    tool_name: str | None = None,
    endpoint_path: str | None = None,
    http_method: str | None = None,
    openapi_spec_uri: str | None = None,
    openapi_operation_id: str | None = None,
    timeout_seconds: float | None = None,
) -> dict

cleanup() -> dict
rebuild_qdrant() -> dict
close() -> None
```

并发要求：`ResearchOrchestrator` 持有当前运行上下文和多个 service/registry 引用，不能并发执行多个 run。FastAPI 使用应用级单实例以避免重复打开同一个 local Qdrant，并用 `asyncio.Lock` 串行 `run`、rerun、审批、激活和维护操作；history/trace/events 等只读调用及 cancel 不取该锁。部署固定一个 Uvicorn worker。

### 13.3 CLI 命令映射

通用格式：

```bash
python -m research_system.cli.app <command> --json
```

| CLI | Python/REST 对应 |
| --- | --- |
| `run QUERY [--intent] [--run-id]` | `POST /runs`；`--run-id` 对 review target 的 CLI 适配，不是复用 run ID |
| `history [--limit] [--status]` | `GET /runs` |
| `replay RUN_ID` | `GET /runs/{id}/replay` |
| `trace RUN_ID` | `GET /runs/{id}/trace` |
| `events RUN_ID` | `GET /runs/{id}/events` |
| `metrics RUN_ID` | `GET /runs/{id}/metrics` |
| `run-steps RUN_ID` | `GET /runs/{id}/steps` |
| `run-thread RUN_ID THREAD_ID` | `GET /runs/{id}/threads/{thread_id}` |
| `subagent-read RUN_ID CALL_ID` | `GET /runs/{id}/subagents/{invocation_id}` |
| `resume RUN_ID` | `GET /runs/{id}/recovery`，只检查 |
| `cancel RUN_ID` | `POST /runs/{id}/cancel` |
| `rerun-from RUN_ID NODE_ID` | `POST /runs/{id}/reruns` |
| `rerun-subagent RUN_ID ACTION DAG NODE` | `POST /runs/{id}/subagent-reruns` |
| `proposals` / `proposal` / `review-proposal` | 第 7 节 |
| `subagent-discoveries` / `subagent-discovery` / `review-subagent-discovery` / `activate-subagent-discovery` | 第 8 节 |
| `status` / `doctor` / `check` | 第 9 节 |
| `cleanup` / `reindex-qdrant` | 第 9 节管理员维护接口 |

CLI 的 JSON renderer 即使 payload 顶层 `status=failed`，进程退出码也可能仍为 `0`。自动化脚本必须读取 JSON 业务状态，不能只依赖 exit code。

CLI 在命令分发前会执行 `Settings.validate()` 和 `ensure_workspace_layout()`，后者可能创建缺失目录或复制模板；因此 CLI 的看似只读命令不一定是文件系统零写入。

### 13.4 Chat CLI 能力

当前 chat 管理命令：

```text
/new
/fork
/rename <name>
/thread-read <thread_id>
/thread-archive [thread_id]
/thread-unarchive <thread_id>
/thread [thread_id]
/threads [limit] [active|all|archived]
/compact
```

`/thread <id>` 会把 thread 绑定到当前 CLI session 并恢复 checkpoint，这属于有状态 session 行为，不映射为 v1 thread CRUD。仓库目前也没有独立的 HTTP session resource。

## 14. FastAPI 实现与部署规范

### 14.1 请求处理顺序

`POST /runs` 当前按以下顺序执行：

1. 生成或接受合法的 `X-Request-ID`；
2. 校验服务级 Bearer token；
3. 解析 JSON 和 `RequestEnvelope`；
4. 显式 intent 按意图校验公开 `constraints`；未显式指定时按公开字段并集校验；
5. 获取应用生命周期内的单个 `ResearchOrchestrator`；
6. 获取 mutation lock，在 worker thread 中执行阻塞的 `run()`；
7. 对领域终态返回 HTTP `200` `ResponseEnvelope`；
8. 对传输/校验异常返回统一错误 envelope；
9. 在应用 shutdown 时关闭应用级 orchestrator；启动中途失败也会执行该清理。

v1 不定义 `202 Accepted` 异步 job。如果部署层无法接受最长运行时间，应在实现 v2 时引入 job resource、polling 状态和回调合同，不能让同一路径在不同部署中随机返回 200/202。

### 14.2 错误归一化

| 原始异常/条件 | v1 错误码 | HTTP |
| --- | --- | --- |
| JSON 语法错误 | `invalid_json` | 400 |
| Pydantic validation error | `validation_error` | 422 |
| 非法 intent/constraint | `invalid_intent` / `invalid_constraint` | 422 |
| run 资源路由捕获 `FileNotFoundError` | `resource_not_found` | 404 |
| thread/subagent 路由捕获 lookup `KeyError` | `nested_resource_not_found` | 404 |
| 未知 rerun 节点 | `unknown_rerun_node` | 422 |
| 源 run 缺 request/plan | `source_run_incomplete` | 409 |
| 未审批 discovery 激活 | `discovery_not_approved` | 409 |
| 注册参数/合同错误 | `invalid_runtime_registration` | 422 |
| 未知服务端异常或 store/config 损坏 | `internal_server_error` | 500 |
| 其他未在具体路由声明的 `FileNotFoundError` / `KeyError` / `ValueError` | `internal_server_error` | 500 |
| API runtime 尚未完成启动 | `api_not_ready` | 503 |

错误 `details` 不得包含 API key、Bearer token、Redis/Qdrant 凭据、完整环境变量、任意本机文件内容或未经脱敏的堆栈。

### 14.3 数据与并发

- `run`、两种 rerun、proposal/discovery 审批与激活、check、cleanup 和 Qdrant reindex 共用一个 mutation lock，进程内串行执行。
- Chat thread 所有操作通过独立的进程内 `RLock` 保护 index 与关联文件。
- cancel 与只读观测不获取 mutation lock，可在长运行期间调用。
- `request_id` 在 v1 只做关联，不自动去重。若实现幂等，必须按租户和请求 body hash 建立明确存储合同。
- 运行历史当前无 owner/tenant 字段；多租户上线前需扩展 run、proposal、ticket、chat thread 的归属模型。
- HTTP activation 禁止 `openapi_spec_uri`，只允许使用已审核配置或模板；`distill_kol_logic` 本机文件输入和预配置 MCP/HTTP source 仍需部署级白名单。
- 对 replay/trace/events 设置响应大小上限或流式输出策略；不要一次把无限增长的 JSONL 加载进内存。
- run 超时是软控制；服务进程还需设置上游请求超时、worker 终止策略和僵尸任务回收。
- 当前本地 Qdrant 和 JSON/JSONL store 只支持一个 Uvicorn worker。`python -m research_system.api` 固定 `workers=1`；不要用外部命令覆盖为多 worker。
- 未配置 token 的显式无鉴权模式只能绑定 loopback；配置 token 后即使同时设置 allow flag，仍会强制 Bearer 校验。

### 14.4 缓存与 HTTP 头

- 非 SSE 的 `/api/v1` 响应使用 `Cache-Control: no-store`；SSE 使用 `no-cache`。
- 所有业务响应返回 `X-Request-ID`；它是传输请求标识，与 body 的研究 `request_id` 分属两层。
- CORS 使用显式 origin allowlist，不允许 `*`，且不启用 cookie credential。
- SSE 额外返回 `Cache-Control: no-cache` 和 `X-Accel-Buffering: no`。

### 14.5 待补的持久审计

FastAPI 当前把认证主体写入 proposal/discovery 的 `reviewer`/`activated_by`，但尚无独立、不可篡改的 HTTP 审计 store。生产多用户化前至少补充：

```text
research_run_created
research_run_cancel_requested
research_rerun_created
proposal_reviewed
subagent_discovery_reviewed
subagent_activated
system_checks_started/completed
artifact_cleanup_started/completed
qdrant_reindex_started/completed/failed
```

审计记录应含认证主体、租户、request id、资源 id、动作、结果和时间，但不得记录密钥或完整敏感报告。

## 15. 已知契约缺口与上线阻断项

1. **运行是同步阻塞的。** 当前没有 job、queue、webhook 或状态 polling resource；客户端断连不会终止 worker thread。
2. **`resume()` 不是恢复。** 它只返回可恢复性信息；真正恢复只能近似映射为新 run 的 rerun。
3. **没有同一 run 的澄清续接。** 补充信息会创建新 run。
4. **`slots` 完全未被主研究链路读取。** 不能把它宣传为有效参数。
5. **`task_requests.py` 与真实入口双轨。** 编排器使用 `RequestEnvelope`，且 task request 模型缺少 stock compare。
6. **蒸馏 `source_ref` 未接入。** 当前只能从 `user_query` 提取本机 JSONL 路径，HTTP 层没有上传资源模型。
7. **核心 `constraints` 仍是开放 object。** FastAPI 会前置校验公开字段，但 intent 省略时只能按字段并集校验，推断意图后不会复验；CLI/Python 直接调用也不经过这层校验。
8. **report 没有统一 schema。** LLM、fallback、银行分支的可选字段不同。
9. **Retriever/Distiller 有 schema drift。** 实际 payload 比领域 schema 多 degradation/trace 字段，Retriever 的 query 形态也不同。
10. **snapshot `facts` 未类型化。** 不同供应商返回不同结构。
11. **methodologies 只是文本投影。** 不是完整估值对象；需要 replay/blackboard 查看原始模型。
12. **事件并非所有状态变更都完整发出。** SSE 通过同时观察持久化 run state 正确关闭，但不能补造不存在的领域事件。
13. **事件类型是开放字符串。** 规划中的部分事件当前不保证产生。
14. **超时不是硬中断。** 阻塞的上游调用可能延迟响应终止。
15. **只有服务级 token。** 没有用户、角色、资源 owner 或 tenant；不得直接作为不受信任的多用户服务暴露。
16. **只支持单 worker。** 本地 Qdrant、JSON/JSONL 和进程锁不能跨进程协调。
17. **Python store 仍直接拼接路径。** FastAPI 已校验所有 path id 和引用 run id，CLI/Python 调用方仍需传可信 ID。
18. **history 没有游标。** v1 只适合有限列表，真正分页留到 v2。
19. **doctor 不是 readiness。** 它不实际探测 OpenAI，远程 Qdrant 也只检查 URL。
20. **check 很重且会写缓存文件。** 虽受 mutation lock 和 Bearer 保护，仍应只在维护窗口调用。
21. **cleanup 没有 dry-run，大小上限只报告。** 删除结果还可能含绝对路径。
22. **Qdrant reindex 先 reset 且非事务。** 失败可留下部分索引。
23. **proposal 审批非事务。** 配置和 memory 可能部分写入。
24. **activation 去重粒度是 candidate type。** 新参数不会更新既有激活。
25. **没有应用层限流、响应大小上限和独立 HTTP 审计 store。** 生产公网化前必须补齐。
26. **CLI JSON 失败可能 exit code 0。** 自动化必须检查 payload 状态。
27. **子 DAG 重跑缺完整预检。** 未知子节点可能成为 HTTP `200` 的 failed 领域响应，未知 `dag_id` 可能未应用预期局部起点；只能使用源计划中的真实标识。
28. **SSE 每次轮询重读完整 `events.jsonl`。** 终态 drain 已避免顺序竞态，但长运行或多客户端会放大 I/O/CPU；增量字节偏移尾随留待后续版本。

## 16. v1 实现验收清单

### 16.1 功能

- [x] 本文第 4 节 37 个 v1 操作均已注册并有 OpenAPI 路径契约测试；
- [x] 7 个 research intent 均有公开字段白名单；API 回归覆盖未知 intent/字段、比较、复核、方法论和通用类型/范围等关键负例，核心意图流程由原有测试覆盖；
- [x] run/replay/events/trace/steps/metrics 与 Python facade 的参数和 payload 透传一致；
- [x] rerun 调用创建新 run 的核心方法，不修改源 run；
- [x] recovery 只读调用 `resume()`，不触发恢复执行；
- [x] SSE 支持原始事件、`Last-Event-ID`、心跳、终态 drain 和关闭；
- [x] proposal/discovery 终态幂等与反向冲突语义符合本文；
- [x] maintenance 接口使用 mutation lock 并透传 completed/skipped/failed 业务状态；
- [ ] 独立的持久 HTTP 操作审计尚未实现。

### 16.2 安全

- [x] 所有 `/api/v1` 业务接口使用 OpenAPI `BearerAuth` 和运行时 Bearer token 认证；显式无鉴权模式限制为 loopback；
- [ ] 资源 owner/tenant 授权尚未实现；
- [ ] admin 接口使用独立权限；
- [x] 所有 HTTP path id 和 constraints 中引用的 run id 防目录穿越；
- [ ] 文件输入限制在白名单目录或对象存储；
- [x] HTTP activation 禁止直接传 `openapi_spec_uri`，注册合同限制为已审核 source/template 和只读副作用；
- [ ] trace/replay/error/check/status 完成敏感字段脱敏；
- [x] CORS 白名单、上游超时、单 worker 和进程内并发锁已配置；
- [ ] 请求体大小和应用层速率限制尚未实现；
- [x] 未知异常响应和错误日志不包含原始异常消息；
- [ ] 独立审计日志尚未实现。

### 16.3 兼容性

- [x] JSON 使用 `snake_case`，时间统一 RFC 3339 UTC；
- [x] 未知顶层请求字段返回 422；
- [x] 事件类型保持开放字符串；
- [x] 领域失败使用 200 `ResponseEnvelope`，传输/校验失败使用通用错误 envelope；
- [x] API 发布后冻结本文件；任何新增业务接口新建完整 `api-doc/api-v2.md` 并使用 `/api/v2`。

## 17. 源码索引

| 契约 | 源文件 |
| --- | --- |
| FastAPI 应用与生命周期 | `research_system/api/app.py`, `config.py`, `dependencies.py` |
| FastAPI 路由 | `research_system/api/routes/` |
| HTTP 请求/错误/校验 | `research_system/api/models.py`, `errors.py`, `validation.py` |
| SSE | `research_system/api/sse.py` |
| Chat HTTP facade | `research_system/api/chat_service.py` |
| 请求/响应 | `research_system/schemas/protocol.py` |
| 运行状态/事件/黑板 | `research_system/schemas/runtime.py` |
| 快照 | `research_system/schemas/snapshots.py` |
| 估值 | `research_system/schemas/valuation.py` |
| 检索 | `research_system/schemas/retrieval.py` |
| 蒸馏 | `research_system/schemas/distillation.py` |
| 复核 | `research_system/schemas/review.py` |
| 提案 | `research_system/schemas/proposals.py` |
| 子 Agent/注册 | `research_system/schemas/subagents.py`, `subagent_runtime_registration*.py` |
| 计划 | `research_system/schemas/planning.py` |
| Python facade | `research_system/runtime/orchestration/orchestrator.py` |
| 运行协调 | `research_system/runtime/orchestration/orchestrator_run_coordinator.py` |
| 管理能力 | `research_system/runtime/orchestration/orchestrator_admin.py` 及 `*_admin_support.py` |
| CLI 参数 | `research_system/cli/application/app_parser_support.py` |
| Chat thread | `research_system/cli/memory/chat_thread_*.py` |
| 系统诊断 | `research_system/runtime/diagnostics/` |
| 运行检查 | `research_system/runtime/platform/checks.py` |
| artifact 保留 | `research_system/runtime/storage/runs/retention.py` |
| 运行数据 | `workspace/runs/{run_id}/` |
| Python 依赖 | `requirements.txt` |
| FastAPI 回归测试 | `tests/test_api_v1_*.py` |

---

本文件是 API v1 的完整基线。后续新增任何业务接口时，请复制并更新为 `api-doc/api-v2.md`，同时将 URL 前缀升级为 `/api/v2`。
