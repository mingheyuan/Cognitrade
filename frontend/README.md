# Cognitrade Web

Cognitrade Web 是现有 CLI-first A 股多 Agent 研究系统的前端工作台。它把自然语言研究入口、结构化研究产物、Agent 进度、证据链和方法论演进组织在同一套界面中。

## 本地运行

```bash
npm install
npm run dev
```

默认地址为 `http://127.0.0.1:4173/`。

```bash
npm run lint
npm run build
npm run preview
```

## 页面

- `研究工作台`：三栏研究驾驶舱，包含线程、报告、运行轨迹、数据质量和证据。
- `研究总览`：快捷研究入口、候选池、板块研究信号和系统状态。
- `智能筛选`：核心 / 观察分层、综合分与因子拆解、搜索和筛选。
- `方法论`：维度权重、版本演进、提案审核和写回保护规则。
- `运行记录`：当前 DAG、事件摘要和历史 run 操作入口。

所有行情、公司、评分与运行数据目前均是用于界面验收的演示快照，页面内有明确标识。

## 后端接入边界

仓库已经在 `research_system/api/` 提供 FastAPI 薄传输层，浏览器不需要也不能直接调用 CLI 或读取 `workspace/runs`。服务复用现有稳定协议：

- 请求 / 响应：`research_system.schemas.protocol.RequestEnvelope`、`ResponseEnvelope`
- 研究入口：`research_system.runtime.orchestration.orchestrator.ResearchOrchestrator.run`
- 实时进度：FastAPI 从持久化 `events.jsonl` 输出原始 `RunEvent` SSE
- 历史与详情：`history / replay / events / steps / metrics / trace`

前端主要使用的 HTTP 边界：

```text
POST /api/v1/runs
GET  /api/v1/runs
GET  /api/v1/runs/{run_id}/replay
GET  /api/v1/runs/{run_id}/events
GET  /api/v1/runs/{run_id}/events/stream
GET  /api/v1/runs/{run_id}/steps
GET  /api/v1/runs/{run_id}/metrics
POST /api/v1/runs/{run_id}/cancel
POST /api/v1/runs/{run_id}/reruns
GET  /api/v1/system/status
```

完整的 37 个接口、请求/响应和错误合同见根目录 [`api-doc/api-v1.md`](../api-doc/api-v1.md)。`src/research-contracts.ts` 仍是演示 UI 的早期草案，尚未覆盖 replay、取消等真实响应；接入时应以 OpenAPI 和 `api-v1.md` 生成或校准类型。

API 默认监听 `http://127.0.0.1:8000`，开发 CORS 白名单已包含 Vite 的 `http://127.0.0.1:4173` 和 `http://localhost:4173`。所有 `/api/v1` 请求都需要 Bearer token；前端接入时应通过受控登录/BFF 或运行时输入获得短期凭据，不能把服务端 token 写进 `VITE_*`。

SSE 接口同样要求 Bearer token，而浏览器原生 `EventSource` 不能添加 `Authorization` header；接入实时进度时使用 fetch-SSE 或同源 BFF，不要把 token 放进 URL query。

## 安全边界

- OpenAI、Qdrant、Redis 和数据供应商密钥只能存在服务端。
- 不要把密钥写入任何 `VITE_*` 变量；它们会进入浏览器构建产物。
- 普通研究视图只展示阶段、事件摘要和中间产物，不展示模型内部推理过程。
- 所有估值与研究判断都必须保留数据截止时间、来源、缺失字段和降级原因。
