# Cognitrade

## Agent OS runtime

The domain-neutral runtime uses one authoritative `ExecutionGraph`, an
`ArtifactBoard` data plane, capability-driven planning, installable DomainPacks,
and runner-enforced governance. The standard research workflow also persists and
inspects this graph; `task_plan.json` remains a compatibility projection for old
finance tooling during the final pack extraction.

Installed packs:

```bash
python -m research_system.cli.app domain-packs --json
```

Run a pack by artifact contract:

```bash
python -m research_system.cli.app domain-run legal_contract "Review this contract" \
  --artifact "contract_document=@contract.txt" --json
```

Run controls and inspection:

```bash
python -m research_system.cli.app domain-inspect <run_id> --json
python -m research_system.cli.app domain-replay <run_id> --json
python -m research_system.cli.app domain-pause <run_id> --json
python -m research_system.cli.app domain-resume <run_id> --json
python -m research_system.cli.app domain-rerun <run_id> <node_id> --json
python -m research_system.cli.app domain-cancel <run_id> --json
```

Each pack must provide `domain.yaml` plus `ontology`, `artifact_schemas`,
`subagents`, `tools`, `planner_policies`, `quality_gates`, `report_contracts`,
and `evaluations`. A pack may also declare versioned `skills` in
`domain.yaml`, with implementations under its own `skills` directory.
The loader rejects incomplete packs before planning.

当前目录是项目运行根目录。

`Cognitrade` 是一个面向 A 股研究场景的 CLI-first 多 Agent 研究系统。当前代码主线已经从早期最小闭环推进到：

- 顶层研究路由：显式 intent、动态路由表、规则/语义匹配、LLM fallback
- 研究运行时：`planner -> orchestrator -> collector/retriever/reporter`
- 数据层：provider chain、fallback merge、Qdrant 检索、长期 memory
- 对话层：默认 line mode、持久化 chat thread、auto compact、checkpoint、query-aware memory recall

## 当前支持的研究意图

当前代码中的顶层 research intent 有：

- `stock_check`
- `stock_compare`
- `stock_screen`
- `sector_research`
- `distill_kol_logic`
- `review_thesis`
- `methodology_evolution`

其中 `methodology_evolution` 目前更偏内部演进链路，不是主要的手工输入入口。

## 目录结构

```text
Cognitrade/
├─ api-doc/            # HTTP API 版本化契约
├─ api-todo/           # 下一版接口缺口与验收条件
├─ deploy/             # 生产镜像与 Nginx 反向代理配置
├─ frontend/           # React + Vite 实时研究工作台
├─ research_system/    # 主代码
├─ scripts/deploy.sh   # 环境安装与容器部署脚本
├─ tests/              # 本地回归测试
├─ workspace/          # 运行数据、配置、taxonomy、runs、memory、Qdrant 本地库
├─ requirements.txt    # Python 运行与 API 依赖
├─ compose.yaml        # API + Nginx Web 容器编排
├─ .env
└─ .env.example
```

`workspace/config/` 和 `workspace/taxonomy/` 是当前运行时真正读取的配置目录，不是包内静态配置目录。

## 本地开发

正式运行统一使用下面的容器部署脚本。本地开发需要热更新时，分别启动 FastAPI 与 Vite，避免再维护一套重复的进程管理脚本：

```bash
# 首次安装后端依赖
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# 终端 1：后端会读取项目根目录 .env
python -m research_system.api

# 终端 2：前端热更新服务器
cd frontend
npm ci
npm run dev
```

默认 API 地址为 `http://127.0.0.1:8000`，Vite 前端地址为 `http://127.0.0.1:4173`。根目录 `.env` 必须配置 `COGNITRADE_API_TOKEN`；前端页面中输入同一个 Bearer token 后即可连接后端。

## 一键安装与容器部署

生产或长期运行只使用 `scripts/deploy.sh`。它会检查并按需安装 Docker、生成权限为 `600` 的 `.env` 和 256 位 API Token、构建并启动 FastAPI `api` 容器与 Nginx `web` 前端容器，通过 Nginx 同源代理 `/api`，最后同时检查网页和带认证 API：

```bash
# 默认完整流程：安装/检查环境、初始化配置、构建并后台启动 api + web
./scripts/deploy.sh --yes

# 要求必须配置 OpenAI，且更新基础镜像
./scripts/deploy.sh deploy --yes --pull --require-openai
```

前端由 Nginx `web` 容器提供，默认只监听 `http://127.0.0.1:8080`；API 容器不直接发布宿主机端口。浏览器右上角 API 地址使用 `/api/v1`，Bearer token 可通过以下命令读取：

```bash
./scripts/deploy.sh token
```

常用运维命令：

```bash
./scripts/deploy.sh check
./scripts/deploy.sh status
./scripts/deploy.sh logs all
./scripts/deploy.sh restart
./scripts/deploy.sh stop
./scripts/deploy.sh down       # 删除容器和网络，但保留 workspace 数据
```

对外提供服务时显式绑定地址，并在前方配置 TLS、防火墙和访问控制：

```bash
./scripts/deploy.sh deploy --web-host 0.0.0.0 --web-port 8080
```

容器部署默认从 AWS Public ECR 匿名拉取 Docker Official Images，避开不可达的 Docker Hub；也可在 `.env` 中替换为公司私有仓库：

```env
COGNITRADE_PYTHON_IMAGE=registry.example.com/library/python:3.11-slim-bookworm
COGNITRADE_NODE_IMAGE=registry.example.com/library/node:22-alpine
COGNITRADE_NGINX_IMAGE=registry.example.com/library/nginx:alpine
```

模型运行配置：

```env
PROVIDER_NAME=openai
OPENAI_API_KEY=replace-with-your-key
OPENAI_BASE_URL=https://your-openai-compatible-endpoint
OPENAI_MODEL=gpt-5.5
OPENAI_REVIEW_MODEL=gpt-5.5
OPENAI_REASONING_EFFORT=xhigh
OPENAI_DISABLE_RESPONSE_STORAGE=true
```

`OPENAI_BASE_URL` 会自动补 `/v1`；调用固定使用 OpenAI Responses API 和 Bearer 认证。`OPENAI_DISABLE_RESPONSE_STORAGE=true` 会向普通与流式 Responses 请求发送 `store=false`。运行数据保存在 `cognitrade_workspace` Docker 命名卷中，API 使用固定非 root 用户；重建镜像或执行 `down` 不会删除研究数据，新版本配置模板只补充缺失文件，不覆盖运维修改。

## Web 研究工作台

仓库现在包含一套面向现有研究运行时设计的 Web 前端，位于 `frontend/`。产品方向是“对话驱动、过程可见、证据可追溯”的研究工作台，而不是行情门户或自动交易终端。

当前包含：

- 研究工作台：问题输入、结构化个股报告、Agent 阶段、证据与数据完整度
- 研究总览：候选、板块信号、最近运行和基础设施状态
- 智能筛选：核心池 / 观察池、评分拆解、筛选与搜索
- 方法论：维度权重、后验演进、待审核提案与写回保护
- 运行记录：运行 DAG、阶段事件、历史筛选和回放入口

只单独启动前端：

```bash
cd frontend
npm install
npm run dev
```

生产构建与检查：

```bash
npm run lint
npm run build
```

前端已经接入 `/api/v1`：研究运行、历史回放、阶段事件、指标、系统状态和方法论提案均来自后端。尚未具备后端接口的行情、方法论版本、上传和全局搜索会显示明确空态，不再用演示值填充；缺口见 [`api-todo/api-v2-todo.md`](./api-todo/api-v2-todo.md)。接入与凭据说明见 [`frontend/README.md`](./frontend/README.md)。OpenAI、Qdrant、Redis 和数据源密钥必须留在服务端，不能放入 `VITE_*` 环境变量。

## FastAPI 服务

推荐使用上面的统一脚本。以下命令适合只调试后端或排查启动问题。

首次运行先安装 Python 依赖：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

在根目录 `.env` 中配置一个强随机 Bearer token：

```env
COGNITRADE_API_TOKEN=replace-with-a-strong-random-token
```

启动单进程 API：

```bash
python -m research_system.api
```

默认监听 `http://127.0.0.1:8000`。FastAPI 交互文档位于 `/docs`，OpenAPI JSON 位于 `/openapi.json`。业务接口统一使用 `/api/v1`，例如：

```bash
export COGNITRADE_API_TOKEN=replace-with-a-strong-random-token
curl http://127.0.0.1:8000/api/v1/system/status \
  -H "Authorization: Bearer $COGNITRADE_API_TOKEN"
```

本地 Qdrant 与 JSON/JSONL store 不支持多进程并发写，当前服务固定使用一个 Uvicorn worker。研究运行、重跑、审批、激活和维护写操作会串行执行；取消和只读观测不等待研究锁。仅在未配置 token 的隔离本机开发环境中，才可设置 `COGNITRADE_API_ALLOW_UNAUTHENTICATED=true` 跳过 Bearer 校验；此模式只允许绑定 `127.0.0.1`、`localhost` 或 `::1`。

## 后端 API 契约

Agent OS 研究、Chat、Dashboard 与管理接口见 [`api-doc/api-v1.md`](./api-doc/api-v1.md)；Finance Workspace 行情聚合与持久化模拟交易接口见 [`api-doc/api-v2.md`](./api-doc/api-v2.md)。后续新增不兼容业务契约时继续递增文档与 URL 主版本，不在已有版本中静默破坏接口。

## 环境变量

先准备根目录 `.env`。最小示例见 [`.env.example`](./.env.example)。

当前常用项：

```env
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=GPT-5.5
QDRANT_URL=
QDRANT_API_KEY=
QDRANT_COLLECTION_NAME=research_evidence

ROUTER_REDIS_URL=
ROUTER_REDIS_ACTIVE_KEY=cognitrade:router:intent_rules:active
ROUTER_REDIS_KEY_PREFIX=cognitrade:router:intent_rules:
ROUTER_REDIS_LAST_GOOD_KEY=cognitrade:router:intent_rules:last_good
ROUTER_REDIS_TIMEOUT_SECONDS=0.2
ROUTER_RULES_CACHE_TTL_SECONDS=2.0
ROUTER_SEED_DEFAULTS=true

PROVIDER_PROBE_TIMEOUT_SECONDS=1.0
COGNITRADE_PROVIDERS_MARKET=
COGNITRADE_PROVIDERS_FUNDAMENTAL=
COGNITRADE_PROVIDERS_NOTICE=
COGNITRADE_PROVIDERS_SECTOR=
COGNITRADE_PROVIDERS_CAPITAL_FLOW=
COGNITRADE_PROVIDERS_SENTIMENT=
COGNITRADE_PROVIDERS_LHB=
COGNITRADE_PROVIDERS_EVENTS=
COGNITRADE_PROVIDERS_POLICY=
TUSHARE_TOKEN=

DEFAULT_TIMEZONE=Asia/Shanghai
LOG_LEVEL=INFO
LLM_REQUEST_TIMEOUT_SECONDS=25
RUN_TIMEOUT_SECONDS=120
EXTERNAL_WAIT_TIMEOUT_SECONDS=30

# Tool resource leases: file is the local default; use Redis for multi-host workers.
TOOL_RESOURCE_LEASE_BACKEND=file
TOOL_RESOURCE_LEASE_REDIS_URL=
TOOL_RESOURCE_LEASE_KEY_PREFIX=cognitrade:tool:lease:
TOOL_RESOURCE_LEASE_REDIS_TIMEOUT_SECONDS=0.5
MEMORY_VECTOR_SEARCH_ENABLED=false

COGNITRADE_API_TOKEN=
COGNITRADE_API_PRINCIPALS=
COGNITRADE_API_ALLOW_UNAUTHENTICATED=false
COGNITRADE_API_CORS_ORIGINS=http://127.0.0.1:4173,http://localhost:4173
COGNITRADE_API_HOST=127.0.0.1
COGNITRADE_API_PORT=8000
COGNITRADE_PAPER_ENFORCE_MARKET_HOURS=true
```

说明：

- `OPENAI_BASE_URL` 若填写兼容 OpenAI 的网关地址，代码会归一化到 `/v1`
- 不配置 `QDRANT_URL` 时，会优先使用本地文件模式
- 长期 memory 当前统一落在本地 `workspace/memory/`，包含 `records.jsonl`、`memory_summary.md` 和 `MEMORY.md`
- `ROUTER_*` 用于 Redis 动态路由控制面；未配置时会退回本地 `router_defaults.yml`
- `COGNITRADE_PROVIDERS_*` 用于 provider chain 覆盖，优先级高于默认 `provider_policy.yml`；行情默认按 AkShare → BaoStock 执行真实回退
- 配置 `TUSHARE_TOKEN` 后，基本面在 AkShare 不可用时会自动回退到 TuShare；未配置密钥时不会把 TuShare 放入执行链
- Finance 快照统一输出 `available / no_data / provider_unavailable / rate_limited / parse_failed`，上游异常原文不会直接返回前端
- `COGNITRADE_PAPER_ENFORCE_MARKET_HOURS=true` 使用 XSHG 交易日历与上海连续竞价时段约束模拟成交
- `COGNITRADE_API_TOKEN` 默认必填；API 会从 `.env` 和进程环境读取，环境变量优先
- `COGNITRADE_API_PRINCIPALS` 可配置 JSON 主体映射，例如
  `[{"token":"...","actor_id":"analyst-a","user_id":"user-a","tenant_id":"tenant-a","roles":["user"]}]`；配置后主体身份以映射为准，不能由 `X-User-ID` 或 `X-Tenant-ID` 改写
- 未配置 token 时，`COGNITRADE_API_ALLOW_UNAUTHENTICATED=true` 仅允许 loopback host；配置 token 后仍强制认证
- `COGNITRADE_API_CORS_ORIGINS` 是逗号分隔的显式浏览器 origin 白名单，不允许 `*`

## 配置文件

当前运行时会自动 seed 并读取这些配置：

```text
workspace/config/
├─ retrieval_policy.yml
├─ methodology_manifest.yml
├─ review_policy.yml
├─ router_defaults.yml
├─ provider_policy.yml
└─ data_sources.yml

workspace/taxonomy/
├─ dimensions.yml
├─ dimension_aliases.yml
└─ dimension_patterns.yml
```

## 常用命令

在项目根目录执行。

### 运行一次研究

```bash
python -m research_system.cli.app run "帮我看下 000001 这个股票" --json
python -m research_system.cli.app run "分析茅台、宁德时代、比亚迪，谁更值得投资" --json
python -m research_system.cli.app run "最近创新药板块怎么样" --json
python -m research_system.cli.app run "请蒸馏这个雪球 statuses 文件 D:\\path\\to\\statuses.jsonl" --intent distill_kol_logic --json
python -m research_system.cli.app run "复核上次创新药判断" --intent review_thesis --run-id <target_run_id> --json
```

### 交互模式

默认 `chat` 是 line mode，交互体验更接近 `codex`，依赖终端原生滚动：

```bash
python -m research_system.cli.app chat
```

如果你明确要进入全屏 TUI：

```bash
python -m research_system.cli.app chat --ui tui
```

如果 `--ui tui` 在当前终端不可用，会自动回退到 line mode。

### chat 的线程和记忆模型

当前 `chat` 不是一次性输入壳，而是持久化 thread：

- 每个 chat 会话都有稳定 `thread_id`
- thread 会持久化 recent turns、summary history、archive 和 checkpoint
- thread 超过阈值会 auto compact，生成可复用摘要链
- 后续 query 会自动带入：
  - 当前 thread profile
  - recent turns
  - compact summary
  - recent chat research memory
  - query-aware relevant memory

当前大致分三层记忆：

1. `thread short-term memory`
   - 当前 chat thread 的 recent turns、summary、checkpoint
2. `chat long-term memory`
   - 跨会话的 chat research summary，落在本地 `records.jsonl + memory_summary.md + MEMORY.md`
3. `methodology long-term memory`
   - 经审核沉淀的方法论记忆，用于 retrieval/evolution 辅助

### chat 常用指令

```text
/help
/exit
/new
/thread [thread_id]
/threads [limit]
/compact
/intent <intent|auto>
/run-id <run_id|clear>
/thinking <on|off>
/events <off|compact|full>
/stop
/run <query>
/review <run_id> <query>
/history [limit] [status]
/status
/doctor
/check
/replay <run_id|last>
/resume <run_id|last>
/cancel <run_id|last>
/cleanup
/reindex
/last
/clear
```

行为说明：

- 直接输入自然语言，默认会被当成 research query 或 basic chat
- 多轮 follow-up 会优先沿当前 thread 和最近研究上下文续接
- 如果上一轮停在 `needs_clarification`，下一句普通输入会自动作为补充信息继续
- `/new` 新建 thread
- `/thread <thread_id>` 切换到已有 thread，并恢复 checkpoint / memory
- `/threads` 查看最近 thread 列表、topic、intent 和 active marker
- `/compact` 手动压缩当前 thread
- `/last` 查看当前 session 默认值和最近 run
- `/clear` 清空当前 intent 和 review target 等默认值

### 其他 CLI 命令

```bash
python -m research_system.cli.app history --json
python -m research_system.cli.app replay <run_id> --json
python -m research_system.cli.app resume <run_id> --json
python -m research_system.cli.app cancel <run_id> --json
python -m research_system.cli.app status --json
python -m research_system.cli.app doctor --json
python -m research_system.cli.app check --json
python -m research_system.cli.app cleanup --json
python -m research_system.cli.app reindex-qdrant --json
```

## 运行产物

Agent OS 的标准入口会生成：

```text
workspace/runs/<run_id>/
├─ request.json
├─ agent_os_request.json
├─ execution_graph.json          # Graph Mode
├─ execution_snapshot.json       # Graph Mode
├─ main_agent_state.json         # Core / Task Mode
├─ execution_events.jsonl
├─ artifact_board.jsonl          # Graph Mode canonical data plane
├─ response.json
└─ summary.json
```

`execution_events.jsonl` 和 `artifact_board.jsonl` 是 Agent OS 的运行事件与共享事实真源。
旧运行历史或兼容 facade 产生的 `task_plan.json`、`events.jsonl`、`blackboard.jsonl` 不属于正常入口，
也不能作为新的 Subagent 输入。

和 chat / memory 相关的持久化产物主要在：

```text
workspace/memory/
├─ exports/
└─ cache/
```

## 当前已实现能力

### stock_check

- 个股 market/fundamental/notice/sector 快照
- methodology 证据：`comps`、`dcf`、`cashflow_resilience`
- `Retriever` 去重、摘要和方法论 memory 注入
- `Reporter` 生成最终研究结论

### stock_compare

- 多标的对比入口
- 支持代码和常见股票名场景
- 对每只标的补方法论证据后做横向比较

### stock_screen

- 候选池构建与筛选
- top candidate 补采快照与轻量 review
- 新 snapshot 维度增强：
  - `lhb`
  - `capital_flow`
  - `sentiment`
  - `events`

### sector_research

- 板块识别与真实板块快照
- `HS300` 基准对照
- 相对强弱、广度、催化、政策与信号摘要

### distill_kol_logic

- `collector -> distiller -> critic -> retriever -> reporter`
- `logic_block / distilled_dimension / review_finding` 落 blackboard
- chunked distillation
- dimension normalization
- Qdrant 入库与回灌

### review_thesis

- `collector -> reviewer -> retriever -> reporter`
- 支持 `--run-id <target_run_id>` 复核旧 run
- 当前最小可复核 claim：
  - `direction`
  - `catalyst`
  - `time_window`

## 本地检查

```bash
python -m research_system.cli.app check --json
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall research_system tests
```

截至 `2026-07-10`，FastAPI 传输层已本地通过：

- `python -m unittest discover -s tests -p "test_api_v1_*.py"`：`51 tests OK`
- 编排器 reindex/cancel 回归模块：`4 tests OK`
- `python -m compileall research_system tests`
- `python -m pip check`

全量测试含 Windows 终端专用模块和依赖本地 `.env` 的用例，应在对应平台与隔离测试环境中执行，不能用全局注入的真实 provider key 跑回归。

`doctor` 现在会额外报告：

- 路由配置与当前版本
- provider chain / network profile
- workspace/config/taxonomy 布局
- local Qdrant / memory 根目录可见性

## 当前边界

- `methodology_evolution` 仍是最小闭环，不是完整的自动演进系统
- 长期记忆当前已经统一收敛到本地 memory adapter；后续仍可继续优化记忆策略，但不再依赖 `mem0`
- 数据源链已支持 provider/fallback，但还不是最终的全量浏览器/搜索 fallback 方案
- `chat` 已有 thread、checkpoint、compact、query-aware recall，但还没完全等同于 OpenHands/LangGraph 的完整状态机
