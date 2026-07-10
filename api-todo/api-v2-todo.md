# Cognitrade 前端 API 缺口（api-v2 待办）

更新时间：2026-07-10

本文基于当前前端页面和 `/api/v1` 后端实现逐项核对。已存在的 v1 接口继续保持兼容；下列新增或破坏性调整统一进入 `api-v2` 文档与路由，不回写 `api-doc/api-v1.md`。

## 已接入的 v1 能力

| 前端能力 | 当前接口 | 状态 |
| --- | --- | --- |
| 创建研究 | `POST /api/v1/runs` | 已接入；当前为同步响应 |
| 运行历史 | `GET /api/v1/runs` | 已接入 |
| 报告与请求回放 | `GET /api/v1/runs/{run_id}/replay` | 已接入 |
| 阶段事件 | `GET /api/v1/runs/{run_id}/events` | 已接入 |
| DAG 步骤 | `GET /api/v1/runs/{run_id}/steps` | 已接入 |
| 运行指标 | `GET /api/v1/runs/{run_id}/metrics` | 已接入 |
| 取消 / 重跑 | `POST /api/v1/runs/{run_id}/cancel`、`POST /api/v1/runs/{run_id}/reruns` | 已接入 |
| 系统状态 | `GET /api/v1/system/status` | 已接入 |
| 方法论提案审核 | `GET /api/v1/admin/proposals`、`POST /api/v1/admin/proposals/{id}/reviews` | 已接入 |

## P0：研究任务异步化

### 1. 异步创建运行

- 建议：`POST /api/v2/runs`
- 返回：HTTP `202`，至少包含 `run_id`、`status`、`status_url`、`events_url`、`created_at`。
- 原因：v1 在整次研究完成后才返回 `run_id`，前端无法在执行期间订阅新任务、展示真实节点进度或可靠取消。
- 验收：请求在 1 秒内返回；随后可通过状态接口和 SSE 观察 `queued -> running -> terminal`；幂等键可避免重复提交。

### 2. 稳定的运行详情 / 状态聚合

- 建议：`GET /api/v2/runs/{run_id}`。
- 返回：请求摘要、intent、状态、创建/开始/结束时间、进度、当前节点、错误摘要、降级状态、证据数量和产物摘要。
- 原因：当前详情页需要并发请求 replay、events、steps、metrics，列表接口也缺少页面需要的元数据。

### 3. 分页和可筛选的运行列表

- 建议：`GET /api/v2/runs?cursor=&limit=&status=&intent=&query=&from=&to=`。
- 返回：`items`、`next_cursor`、`total`；单项包含标题/查询、时间、耗时、证据数、降级原因。
- 验收：游标稳定；支持按状态、intent、关键词和时间范围组合过滤。

### 4. Agent OS artifact 内容读取

- 建议：
  - `GET /api/v2/runs/{run_id}/artifacts?type=&status=&cursor=&limit=`
  - `GET /api/v2/runs/{run_id}/artifacts/{artifact_id}`
- 返回：artifact 标识、类型、schema/version、producer、`content`、来源引用、父 artifact、confidence、risk flags、状态和创建时间。
- 原因：latest main 的 v1 `result.report` 在 `protocol=agent_os` 时只含 `artifact_ids`、`final_artifact_ids` 和 graph revision。实际 `research_report.content`、`evidence_set.content`、筛选候选等仅落在服务端 artifact board，浏览器没有受认证的版本化读取接口。
- 安全要求：校验 artifact 必须属于路径中的 run；按 principal 授权；不返回服务端绝对路径、prompt、凭据或内部推理。
- 验收：前端可仅凭 HTTP API 读取最终 `research_report` 并展示 conclusion/evidence/risk/follow_up；无法访问的 artifact 返回结构化 403/404。

## P1：页面主能力

### 5. 市场总览与行情序列

- 建议：
  - `GET /api/v2/market/overview`
  - `GET /api/v2/market/breadth?market=CN&date=`
  - `GET /api/v2/securities/{symbol}/quote`
  - `GET /api/v2/securities/{symbol}/series?interval=1d&from=&to=`
- 最少字段：数据截止时间、来源、交易状态、上涨/下跌/平盘数量、指数表现、价格/OHLCV、货币和缺失原因。
- 当前处理：总览和工作台明确显示“行情 API 未提供”，不生成虚假涨跌或图表。

### 6. 持久化筛选结果

- 建议：`GET /api/v2/screens`、`GET /api/v2/screens/{screen_id}`。
- 最少字段：筛选条件、候选、排名、分层、综合分、因子分、理由、风险、数据截止时间及对应 `run_id`。
- 当前处理：只读取最近一次 `stock_screen` 运行的 `report.candidates`；字段缺失显示“—”。

### 7. 方法论只读合同与版本历史

- 建议：
  - `GET /api/v2/methodologies/current`
  - `GET /api/v2/methodologies/versions?cursor=&limit=`
  - `GET /api/v2/methodologies/versions/{version}`
- 最少字段：版本、发布时间、状态、维度/因子权重、变更说明、来源提案、审核人与生效时间。
- 当前处理：提案列表和审核可用；权重、当前版本与版本差异显示接口缺口空态。

### 8. 关注列表

- 建议：`GET/POST /api/v2/watchlists`，`PATCH/DELETE /api/v2/watchlists/{id}`，以及证券条目的增删接口。
- 最少字段：名称、证券标识、添加原因、关联 thesis/run、更新时间。
- 当前处理：不展示或保存模拟关注列表。

### 9. 延续与派生研究

- 建议：
  - `POST /api/v2/runs/{run_id}/continuations`
  - `POST /api/v2/runs/{run_id}/derivations`
- 请求：新问题/目标、继承的证据或 artifact、可选起始节点；返回新的异步 `run_id` 和父子关系。
- 当前处理：运行页“派生研究”禁用；已有 v1 rerun 仅用于从节点重跑。

## P2：效率与交付

### 10. 全局搜索

- 建议：`GET /api/v2/search?q=&types=security,run,thread,artifact&cursor=`。
- 要求：统一命中结构、类型、标题、摘要、更新时间和跳转标识；权限过滤必须在服务端完成。
- 当前处理：顶部搜索框禁用并标明缺口。

### 11. 研究材料上传

- 建议：`POST /api/v2/materials`（multipart 或预签名上传）、`GET/DELETE /api/v2/materials/{id}`。
- 要求：文件大小/类型限制、哈希去重、病毒扫描状态、解析状态、引用信息和有效期；创建 run 时可传 `material_ids`。
- 当前处理：研究输入区的材料按钮禁用；浏览器不直接写本地 workspace。

### 12. 服务端导出

- 建议：`POST /api/v2/runs/{run_id}/exports`、`GET /api/v2/exports/{export_id}`。
- 格式：结构化 JSON、Markdown 报告、证据清单；异步生成并返回过期下载地址。
- 当前处理：前端仅能导出已获取的 JSON，不等同于完整研究包。

### 13. 通知与活动流

- 建议：`GET /api/v2/notifications`、`POST /api/v2/notifications/{id}/read`，可选 SSE 通知流。
- 事件：运行完成/失败、提案待审、提案审核结果和系统检查异常。
- 当前处理：通知入口禁用。

## 跨接口约束

- 新接口统一 `/api/v2`，并新增 `api-doc/api-v2.md` 后才能进入实现。
- 所有时间使用带时区的 ISO 8601；行情和研究结果必须携带 `as_of` 与数据来源。
- 列表统一游标分页；错误继续使用结构化错误码与 `X-Request-ID`。
- 浏览器凭据只能通过 Bearer header 或同源安全会话传递，禁止 token query 参数。
- SSE 需支持 `Last-Event-ID`、断线重连和终态关闭；若使用 Bearer，前端采用 fetch-SSE 或同源 BFF。
- 缺失值返回 `null` 并提供缺失/降级原因，不用 `0`、空字符串或模拟值代替。
