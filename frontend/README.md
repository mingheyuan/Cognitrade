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

仓库目前没有 FastAPI、Flask、SSE 或 WebSocket 等入站服务。浏览器不能直接调用 CLI，也不能读取 `workspace/runs`。正确做法是在 `ResearchOrchestrator` 外增加一个薄 transport adapter，并复用现有稳定协议：

- 请求 / 响应：`research_system.schemas.protocol.RequestEnvelope`、`ResponseEnvelope`
- 研究入口：`research_system.runtime.orchestration.orchestrator.ResearchOrchestrator.run`
- 实时进度：`ResearchOrchestrator(..., event_listener=...)` 接收 `RunEvent`
- 历史与详情：`history / replay / events / steps / metrics / trace`

建议的最小 HTTP 边界：

```text
POST /api/runs
GET  /api/runs
GET  /api/runs/{run_id}
GET  /api/runs/{run_id}/events
GET  /api/runs/{run_id}/events/stream
GET  /api/runs/{run_id}/steps
GET  /api/runs/{run_id}/metrics
POST /api/runs/{run_id}/cancel
POST /api/runs/{run_id}/rerun
GET  /api/status
```

SSE 适配器收到同步 `RunEvent` 回调后应只入队，不能在回调内执行阻塞 I/O。前端领域契约草案位于 `src/research-contracts.ts`。

## 安全边界

- OpenAI、Qdrant、Redis 和数据供应商密钥只能存在服务端。
- 不要把密钥写入任何 `VITE_*` 变量；它们会进入浏览器构建产物。
- 普通研究视图只展示阶段、事件摘要和中间产物，不展示模型内部推理过程。
- 所有估值与研究判断都必须保留数据截止时间、来源、缺失字段和降级原因。
