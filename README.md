# Cognitrade

Cognitrade 是以 Agent OS、DomainPack 和 ArtifactBoard 为核心的 A 股研究 Web
应用。当前产品入口是 Web 与版本化 HTTP API；历史 CLI 和旧编排系统已删除。

## 功能

- 自然语言研究入口：个股体检、多股比较、股票筛选、行业研究与 Thesis Review。
- 可追踪运行：Turn/Task、ExecutionGraph、事件流、子 Agent、Artifact、指标和重跑。
- Finance Workspace：证券解析、行情、K 线摘要、基本面、资金流、舆情、公告、事件和龙虎榜。
- 策略工作台：`/strategies` 从零创建或复制系统模板，编辑、发布不可变版本、绑定基金、生成日报、预演四态信号、回测、验证和晋级；旧 `/methodology` 与 `/backtesting` 前端入口已删除。
- 报告与信号：一天一份 point-in-time 基金报告，前十大持仓必须生成可复用的 canonical UZI 个股子报告；基金本体、持仓 UZI 与结构质量共同生成可配置复合信号，缺失数据不重权也不伪造中性值。
- 策略回测：Hikyuu 执行 `fund_only`、ETF/LOF 与场外基金 NAV、`holdings_portfolio` 三类资产适配；回测只接受已发布版本，成交逐笔追溯到策略版本、日报和信号，不允许页面临时覆盖阈值、均线或费率。
- 模拟交易：VeighNa PaperAccount 到价撮合，服务端现金/持仓账本、限价挂单、资金冻结、撤单、费用、T+1 和幂等订单；只有人工确认的 Verified 版本可晋级 Promoted 并通过策略链路下单。
- DomainPack：`finance_ashare` 与 `legal_contract` 通过声明式能力、工具、Gate 和报告合同接入。
- UZI 报告：研究工作台可从 ArtifactBoard 直接打开与上游 v3.9.4 分析及呈现层逐文件对齐的原生 HTML，或下载同源 PDF；不依赖旧 UZI CLI、缓存和报告服务。
- 基金持仓股票报告：从冻结公开披露生成可配置 top 5/10/20 的 UZI 股票报告，支持失败节点重试、完整重跑、暂停/恢复、历史补跑与 late revision；同一股票同一日期跨基金复用 canonical UZI Artifact。ETF 联接只使用已取得的穿透持仓，不推断缺失数据。
- 治理：Bearer 鉴权、作用域隔离、高风险审批、质量 Gate、来源引用和分层记忆。

## 快速部署

完整部署脚本会检查/安装 Docker、初始化 `.env`、构建镜像，显式执行“迁移前备份 → Alembic → 旧对象只读索引 → 工作区迁移”，再启动 API 与前端：

```bash
./scripts/deploy.sh --yes
```

常用命令：

```bash
./scripts/deploy.sh status
./scripts/deploy.sh token
./scripts/deploy.sh logs all
./scripts/deploy.sh runtime-check
./scripts/deploy.sh migrate
./scripts/deploy.sh restart
./scripts/deploy.sh down
```

完整部署会安装 API、数据源、Playwright Chromium、VeighNa PaperAccount 和
Hikyuu，不把模拟盘或回测留成运行时可选项。镜像构建阶段会先执行一次无网络运行环境
检查；`deploy`、`start`、`restart` 完成后还会以实际容器用户再次检查 Python 依赖图、
API 导入、Chromium、VeighNa/Hikyuu 原生模块、策略数据库迁移状态以及对象存储读写权限。任一关键能力
缺失都会让命令返回非零状态。

部署镜像内可显式核验 UZI 3.9.4 的 22 类真实数据链；该检查不会在启动时自动执行，
数据源无返回会记录为 `data_gap`，不会生成模拟数据：

```bash
COGNITRADE_PROVIDER_SMOKE_SYMBOL=600519 \
COGNITRADE_PROVIDER_SMOKE_SECTOR=白酒 \
./scripts/deploy.sh provider-smoke
```

可用 `COGNITRADE_PROVIDER_SMOKE_DIMENSIONS=market,kline,events` 只检查指定维度。
来源目录与 UZI 3.9.4 固定提交逐项对齐，共 73 个候选来源；“已登记”不等于本次
“已取到”，运行结果以各 Snapshot 的 `source_trace` 为准。雪球组合来源仍需同时显式
开启敏感来源、加入 `xueqiu` allowlist 并配置 Cookie；淘股吧、同花顺模拟盘等公开比赛
证据可以独立降级运行。
全量检查固定串行执行，避免多个 AkShare 路径并发初始化其进程级 JavaScript 运行时。

默认访问地址是 `http://127.0.0.1:8080`。浏览器 Token 可通过
`./scripts/deploy.sh token` 获取。

单独升级已有环境时显式执行：

```bash
./scripts/deploy.sh migrate
```

迁移先在 `workspace/backups/strategy_migrations/` 生成一致性 SQLite 与工作区索引备份，
再执行 Alembic、旧 Artifact/回测引用索引及 Run 状态规范化。`deploy` 命令显式调用这条
链；普通 `start`、`restart` 和 API 进程启动不会隐式改库。

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
依赖。VeighNa 的 headless 撮合状态固定写入
`<workspace>/paper_trading/veighna_runtime/.vntrader`；Hikyuu 回测在受控子进程和
`<workspace>/backtests/.hikyuu_runtime` 隔离目录运行。两者都不会依赖运行用户的主目录，
Hikyuu 也不会自动下载远程策略仓库。

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

策略业务域本地单实例默认使用 SQLite；生产多实例使用 PostgreSQL，并给对象存储配置所有
API/worker 都可访问的持久化 POSIX 路径：

```dotenv
COGNITRADE_DATABASE_URL=postgresql+psycopg://cognitrade:<password>@<host>:5432/cognitrade
COGNITRADE_DATABASE_AUTO_BOOTSTRAP=false
COGNITRADE_OBJECT_STORE_ROOT=/mnt/cognitrade/strategy_objects
```

Compose 的默认 SQLite 与对象目录位于 `cognitrade_workspace` 命名卷；外部 PostgreSQL
需要由平台执行快照/备份策略，外部对象目录也必须显式挂载进容器。生产预检会拒绝未迁移
数据库、不可写对象目录或 `COGNITRADE_DATABASE_AUTO_BOOTSTRAP=true`。

模型能力需要：

```dotenv
OPENAI_API_KEY=<key>
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.6-luna
OPENAI_REASONING_EFFORT=xhigh
OPENAI_SERVICE_TIER=default
OPENAI_DISABLE_RESPONSE_STORAGE=true
UZI_LLM_REQUEST_TIMEOUT_SECONDS=480
UZI_LLM_MAX_CONCURRENCY=2
UZI_LLM_TRANSIENT_RETRIES=6
UZI_LLM_RETRY_BASE_SECONDS=2
UZI_LLM_RETRY_MAX_SECONDS=60
```

没有模型 Key 时，系统仍可执行确定性 Pack 能力，但需要模型合成的最终答复可能以
`completed_partial` 结束。`COGNITRADE_API_ALLOW_UNAUTHENTICATED=true` 只允许
回环地址的隔离本地开发，生产部署脚本始终启用认证。

可选真实数据源配置：

```dotenv
TUSHARE_TOKEN=<runtime-secret>
MX_APIKEY=<runtime-secret>
COGNITRADE_FUND_HOLDING_REPORT_CONCURRENCY=2
```

TuShare 配置 Token 只表示客户端可以鉴权；具体 `daily`、`fina_indicator`、`moneyflow`、
`top_list` 等接口仍取决于账号积分与权限。接口无权限时会记录脱敏的
`provider_unavailable` 并继续 AkShare/eFinance/BaoStock/直连 HTTP 回退，不会生成假数据。
密钥只放在被 Git 忽略的 `.env` 或部署密钥系统中。

镜像源可覆盖：

```dotenv
COGNITRADE_PYTHON_IMAGE=python:3.11-slim-bookworm
COGNITRADE_NODE_IMAGE=node:22-alpine
COGNITRADE_NGINX_IMAGE=nginx:alpine
COGNITRADE_PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
COGNITRADE_NPM_REGISTRY=https://registry.npmmirror.com
COGNITRADE_DEBIAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
COGNITRADE_DEBIAN_SECURITY_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian-security
```

包与 Debian 镜像仅影响构建；不配置时使用 PyPI、npm 与 Debian 官方源。量化依赖集中声明在
`requirements-quant.txt`，并由主 `requirements.txt` 强制包含，因此容器部署与本地
`pip install -r requirements.txt` 安装的是同一套完整能力。

完整变量见 [`.env.example`](./.env.example)。

## GitHub Pages 前端

仓库包含 `.github/workflows/pages.yml`。在 GitHub Pages 选择 GitHub Actions 作为来源，并
设置仓库变量 `COGNITRADE_API_BASE_URL=https://api.example.com/api/v1`；推送 `main` 后会构建
前端、自动采用仓库子路径，并复制 `index.html` 为 `404.html`，因此直接访问
`/<repo>/strategies` 也能恢复 SPA 路由。

GitHub Pages 只托管静态前端，后端仍需单独部署为 HTTPS 服务。后端
`COGNITRADE_API_CORS_ORIGINS` 必须包含实际 Pages Origin，例如
`https://<owner>.github.io`；Bearer Token 和数据库密码不得写入仓库变量或 Vite 构建变量。
Token 仍由用户在页面顶栏输入并仅保存在当前标签页 `sessionStorage`。

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
                            # VeighNa 模拟撮合与 Hikyuu 隔离回测适配
  strategies/             # 策略合同、Repository、日报/信号/回测/验证/晋级
  runtime/                # Agent OS、Turn、Graph、Tool、Pack、Memory
  schemas/                # Pydantic 协议
  services/methodologies/ # 当前被 Pack 调用的方法论计算
domain_packs/
  finance_ashare/
  legal_contract/
frontend/                 # React + TypeScript + Vite
scripts/                  # 部署、迁移和校验
migrations/               # Alembic 生产 schema 迁移
workspace/                # 运行数据（生产由命名卷持久化）
  paper_trading/          # SQLite 模拟盘账本
  backtests/              # Hikyuu 回测结果与隔离运行目录
  strategy_objects/       # 内容寻址的报告与策略回测大对象
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
npm run build:pages
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
