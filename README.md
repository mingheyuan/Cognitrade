# Cognitrade

Cognitrade 是以 Agent OS、DomainPack 和 ArtifactBoard 为核心的 A 股研究 Web
应用。当前产品入口是 Web 与版本化 HTTP API；历史 CLI 和旧编排系统已删除。

## 功能

- 自然语言研究入口：个股体检、多股比较、股票筛选、行业研究与 Thesis Review。
- 可追踪运行：Turn/Task、ExecutionGraph、事件流、子 Agent、Artifact、指标和重跑。
- Finance Workspace：证券解析、行情、K 线摘要、基本面、资金流、舆情、公告、事件和龙虎榜。
- 模拟交易：VeighNa PaperAccount 到价撮合，服务端现金/持仓账本、限价挂单、资金冻结、撤单、费用、T+1 和幂等订单。
- 策略回测：独立 Hikyuu 回测实验室，使用 Finance Pack 历史 K 线运行双均线策略并持久化资产曲线、绩效和成交记录。
- DomainPack：`finance_ashare` 与 `legal_contract` 通过声明式能力、工具、Gate 和报告合同接入。
- 治理：Bearer 鉴权、作用域隔离、高风险审批、质量 Gate、来源引用和分层记忆。

## 快速部署

完整部署脚本会检查/安装 Docker、初始化 `.env`、构建并启动 API 与前端：

```bash
./scripts/deploy.sh --yes
```

常用命令：

```bash
./scripts/deploy.sh status
./scripts/deploy.sh token
./scripts/deploy.sh logs all
./scripts/deploy.sh restart
./scripts/deploy.sh down
```

部署镜像内可显式核验 UZI 3.9.2 的 22 类真实数据链；该检查不会在启动时自动执行，
数据源无返回会记录为 `data_gap`，不会生成模拟数据：

```bash
COGNITRADE_PROVIDER_SMOKE_SYMBOL=600519 \
COGNITRADE_PROVIDER_SMOKE_SECTOR=白酒 \
./scripts/deploy.sh provider-smoke
```

可用 `COGNITRADE_PROVIDER_SMOKE_DIMENSIONS=market,kline,events` 只检查指定维度。
来源目录与 UZI 3.9.2 固定提交逐项对齐，共 70 个候选来源；“已登记”不等于本次
“已取到”，运行结果以各 Snapshot 的 `source_trace` 为准。雪球组合来源仍需同时显式
开启敏感来源、加入 `xueqiu` allowlist 并配置 Cookie；淘股吧、同花顺模拟盘等公开比赛
证据可以独立降级运行。
全量检查固定串行执行，避免多个 AkShare 路径并发初始化其进程级 JavaScript 运行时。

默认访问地址是 `http://127.0.0.1:8080`。浏览器 Token 可通过
`./scripts/deploy.sh token` 获取。

若 Docker 命名卷中仍是旧状态文件，先显式执行一次：

```bash
./scripts/deploy.sh migrate
```

迁移会校验并生成 `turn_state.json` / `task_state.json` 后删除旧别名，并写入
迁移标记。正常部署和启动不会隐式迁移。

## 本地开发

后端：

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
COGNITRADE_API_ALLOW_UNAUTHENTICATED=true \
COGNITRADE_API_HOST=127.0.0.1 \
.venv/bin/python -m uvicorn research_system.api.app:app --host 127.0.0.1 --port 8000
```

首次安装会下载 VeighNa、Hikyuu 及其量化/绘图依赖，体积明显大于纯 API
依赖。Hikyuu 回测在受控子进程和 `<workspace>/backtests/.hikyuu_runtime`
隔离目录运行，不会初始化用户主目录或自动下载远程策略仓库。

前端：

```bash
cd frontend
npm install
npm run dev
```

Vite 开发环境默认连接 `http://127.0.0.1:8000/api/v1`。也可以在 Web 顶栏配置
API 地址和 Bearer Token；Token 只保存在当前标签页的 `sessionStorage`。

## 必要配置

部署模式必须设置：

```dotenv
COGNITRADE_API_TOKEN=<high-entropy-token>
```

模型能力需要：

```dotenv
OPENAI_API_KEY=<key>
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.5
OPENAI_REVIEW_MODEL=gpt-5.5
OPENAI_REASONING_EFFORT=xhigh
OPENAI_DISABLE_RESPONSE_STORAGE=true
```

没有模型 Key 时，系统仍可执行确定性 Pack 能力，但需要模型合成的最终答复可能以
`completed_partial` 结束。`COGNITRADE_API_ALLOW_UNAUTHENTICATED=true` 只允许
回环地址的隔离本地开发，生产部署脚本始终启用认证。

可选真实数据源配置：

```dotenv
TUSHARE_TOKEN=<runtime-secret>
MX_APIKEY=<runtime-secret>
```

TuShare 配置 Token 只表示客户端可以鉴权；具体 `daily`、`fina_indicator`、`moneyflow`、
`top_list` 等接口仍取决于账号积分与权限。接口无权限时会记录脱敏的
`provider_unavailable` 并继续 AkShare/eFinance/BaoStock/直连 HTTP 回退，不会生成假数据。
密钥只放在被 Git 忽略的 `.env` 或部署密钥系统中。

镜像源可覆盖：

```dotenv
COGNITRADE_PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.11-slim-bookworm
COGNITRADE_NODE_IMAGE=public.ecr.aws/docker/library/node:22-alpine
COGNITRADE_NGINX_IMAGE=public.ecr.aws/docker/library/nginx:alpine
```

完整变量见 [`.env.example`](./.env.example)。

## API

- Swagger UI：`/docs`
- OpenAPI：`/openapi.json`
- v1 文档：[`api-doc/api-v1.md`](./api-doc/api-v1.md)
- v2 Finance 文档：[`api-doc/api-v2.md`](./api-doc/api-v2.md)

业务接口只有 `/api/v1` 与 `/api/v2`。未版本化 `/api/*` 业务路由不再提供。
新增不兼容合同使用新的 API 主版本，不向旧主版本静默加入破坏性行为。

## 项目结构

```text
research_system/
  api/                    # FastAPI、鉴权、SSE 与版本路由
                            # VeighNa 模拟撮合与 Hikyuu 隔离回测 facade
  runtime/                # Agent OS、Turn、Graph、Tool、Pack、Memory
  schemas/                # Pydantic 协议
  services/methodologies/ # 当前被 Pack 调用的方法论计算
domain_packs/
  finance_ashare/
  legal_contract/
frontend/                 # React + TypeScript + Vite
scripts/                  # 部署、迁移和校验
workspace/                # 运行数据（生产由命名卷持久化）
  paper_trading/          # SQLite 模拟盘账本
  backtests/              # Hikyuu 回测结果与隔离运行目录
```

当前架构说明见 [`plan/current-architecture.md`](./plan/current-architecture.md)。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall -q research_system domain_packs scripts

cd frontend
npm run lint
npm test -- --run
npm run build
```

部署后可执行：

```bash
./scripts/deploy.sh status
curl -fsS http://127.0.0.1:8080/
```

## 数据真实性

行情、财务、研究 Artifact 和 Agent 结论不得由前端伪造。上游不可用时，API 会返回
明确的质量状态或数据缺口，前端显示可读说明。少数尚无后端合同的交互使用显式
“本地预览”，只处理浏览器已加载的数据或文件名/大小，不会上传材料或冒充研究结果。
