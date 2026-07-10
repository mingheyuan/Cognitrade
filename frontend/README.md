# Cognitrade Web

Cognitrade Web 是现有 CLI-first A 股多 Agent 研究系统的前端工作台。它把自然语言研究入口、结构化研究产物、Agent 进度、证据链和方法论演进组织在同一套界面中。

## 本地运行

在项目根目录一键启动前后端：

```bash
./scripts/service.sh start
```

该命令默认要求根目录 `.env` 已配置 `COGNITRADE_API_TOKEN`。仅在隔离的本机开发环境中，可显式启动 loopback 无认证模式：

```bash
./scripts/service.sh start --local-unauthenticated
```

如果已经配置 token，此参数不会关闭 Bearer 认证。

如果只开发前端，则在当前 `frontend/` 目录执行：

```bash
npm install
npm run dev
```

默认地址为 `http://127.0.0.1:4173/`。

前端默认连接 `http://127.0.0.1:8000/api/v1`。可以在页面右上角的 API 状态按钮中修改地址并输入 Bearer token；地址持久化到 `localStorage`，token 只保存在当前标签页的 `sessionStorage`。也可以只配置非敏感地址：

```bash
VITE_COGNITRADE_API_BASE_URL=http://127.0.0.1:8000/api/v1 npm run dev
```

不要配置 `VITE_COGNITRADE_API_TOKEN`，所有 `VITE_*` 值都会进入浏览器构建产物。

```bash
npm run lint
npm run build
npm run preview
```

## 页面

- `研究工作台`：真实研究入口、结构化报告、运行轨迹、指标和证据。
- `研究总览`：运行统计、ArtifactBoard 筛选候选、板块信号和系统状态。
- `智能筛选`：读取最新 `screening_result` Artifact 的候选、评分、数据缺口与来源运行。
- `方法论`：读取 DomainPack 方法论契约和已验证的 Policy Memory；不显示未验证的提案或回测数据。
- `运行记录`：当前 DAG、事件摘要和历史 run 操作入口。

页面不再内置行情、公司、评分或运行样本。后端未提供的字段统一显示“—”或接口缺口提示，不会用演示值补齐。

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
GET  /api/v1/dashboard/{domain_id}/{view_id}
```

完整的 v1 接口、请求/响应和错误合同见根目录 [`api-doc/api-v1.md`](../api-doc/api-v1.md)。`src/research-contracts.ts` 与 `src/api-client.ts` 已覆盖当前页面实际使用的运行、回放、事件、指标、系统状态与 Dashboard 合同；新增接口仍应以 OpenAPI 和版本化接口文档为准。

当前 `POST /runs` 是同步请求，前端提交期间只能展示全局等待状态，完成并返回 `run_id` 后才加载详情。Dashboard v1 仅投影 Pack 声明的视图；通用版本化 Artifact 内容读取、实时行情、上传、派生研究和全局搜索等缺口同样见 [`api-todo/api-v2-todo.md`](../api-todo/api-v2-todo.md)。

API 默认监听 `http://127.0.0.1:8000`，开发 CORS 白名单已包含 Vite 的 `http://127.0.0.1:4173` 和 `http://localhost:4173`。所有 `/api/v1` 请求都需要 Bearer token；前端接入时应通过受控登录/BFF 或运行时输入获得短期凭据，不能把服务端 token 写进 `VITE_*`。

SSE 接口同样要求 Bearer token，而浏览器原生 `EventSource` 不能添加 `Authorization` header。当前页面通过 REST 轮询活动运行；后续切换 fetch-SSE 或同源 BFF 时，不要把 token 放进 URL query。

## 安全边界

- OpenAI、Qdrant、Redis 和数据供应商密钥只能存在服务端。
- 不要把密钥写入任何 `VITE_*` 变量；它们会进入浏览器构建产物。
- 普通研究视图只展示阶段、事件摘要和中间产物，不展示模型内部推理过程。
- 所有估值与研究判断都必须保留数据截止时间、来源、缺失字段和降级原因。
