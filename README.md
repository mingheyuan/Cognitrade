# Cognitrade

Cognitrade 是以 Agent OS、DomainPack 和 ArtifactBoard 为核心的 A 股研究 Web
应用。当前产品入口是 Web 与版本化 HTTP API；历史 CLI 和旧编排系统已删除。

## 功能

- 自然语言研究入口：个股体检、多股比较、股票筛选、行业研究与 Thesis Review。
- 可追踪运行：Turn/Task、ExecutionGraph、事件流、子 Agent、Artifact、指标和重跑。
- Finance Workspace：证券解析与数据库优先的 Point-in-Time 行情数据中心；覆盖、快照/证据、采集任务、Bundle 修订、归档订阅和 provider 能力均可审计。
- 策略工作台：`/strategies` 从零创建或复制系统模板，编辑、发布不可变版本、绑定基金、生成日报、预演四态信号、回测、验证和晋级；旧 `/methodology` 与 `/backtesting` 前端入口已删除。
- 报告与信号：一天一份 point-in-time 基金报告，前十大持仓必须生成可复用的 canonical UZI 个股子报告；基金本体、持仓 UZI 与结构质量共同生成可配置复合信号，缺失数据不重权也不伪造中性值。Lite / Medium / Deep、持仓和日报只消费已冻结 Bundle/manifest，报告阶段不联网补数。
- 基金回测：`/strategy-backtests/**` 是唯一公开回测入口，仅接受已发布的不可变策略版本；默认执行最近 30 天的 `fund_only` 基金本体净值回测，`holdings_portfolio` 仅保留为高级穿透研究。“检查并启动回测”会内联预检，满足条件时在同一次操作中入队；历史区基于当前已加载的最近 200 次原始运行，默认展示最近 3 个逻辑回测，也可展开这些已加载的原始尝试；展示折叠不会删除审计记录。
- 模拟交易：VeighNa PaperAccount 到价撮合，服务端现金/持仓账本、限价挂单、资金冻结、撤单、费用、T+1 和幂等订单；只有人工确认的 Verified 版本可晋级 Promoted 并通过策略链路下单。
- DomainPack：`finance_ashare` 与 `legal_contract` 通过声明式能力、工具、Gate 和报告合同接入。
- UZI 报告：研究工作台可从 ArtifactBoard 直接打开与上游 v3.9.4 分析及呈现层逐文件对齐的原生 HTML，或下载同源 PDF；旧 UZI 文件数据缓存不迁移、不回退、不作为新报告的事实源。
- 行情数据中心基金入口：基金只作为“前十大持仓股票分析”父入口；从冻结公开披露中固定解析精确 10 只 A 股，再为每只股票准备完整 22 槽 Bundle 和 canonical UZI 报告。支持失败节点重试、完整重跑、暂停/恢复、历史补跑与 late revision；同一股票同一日期跨基金复用 canonical UZI Artifact。ETF 联接只使用已取得的穿透持仓，不推断缺失数据。
- 治理：Bearer 鉴权、作用域隔离、高风险审批、质量 Gate、来源引用和分层记忆。

## 2.0 核心升级：可审计的策略回测闭环

1.0 已有独立的 A 股手工模拟盘，支持服务端现金/持仓账本、FIFO、T+1、费用、
整手校验和幂等订单；它不是一个版本化策略回测系统。2.0 在保留这条能力的基础上，
新增了从研究证据到策略模拟执行的完整治理链：

```text
已发布的不可变策略版本
  + point-in-time 基金日报 / canonical UZI 持仓报告
  -> 同版本四态信号（ALLOW_BUY / WATCH / AVOID / BLOCKED）
  -> “检查并启动回测”内联预检
  -> Hikyuu 技术事件 + Cognitrade 确定性成交/费用账本
  -> 指标、基准、逐笔 lineage 与内容哈希
  -> 机器验证 + 人工确认 Verified
  -> 人工晋级 Promoted
  -> VeighNa 模拟交易（当前策略委托限 holdings_portfolio A 股持仓）
```

| 能力 | v1.0 | 2.0 |
| --- | --- | --- |
| 策略定义 | 没有独立策略业务域 | 草稿、发布不可变版本、版本 diff、归档和审计事件 |
| 回测 | 没有受治理的策略回测 | 单一基金回测入口；`fund_only` 默认，持仓组合为高级模式，支持异步运行、预检、取消和重试 |
| 数据时点 | 模拟下单时取服务端最新行情 | 日报、信号、行情/NAV 和持仓按 point-in-time 固化，禁止前视 |
| 执行模型 | A 股手工模拟订单即时成交 | Hikyuu 技术事件 + 基金/A 股确定性账本，完整计费和 T+1/确认日约束 |
| 可追溯性 | 订单和账户流水 | 每笔成交关联策略版本、内容哈希、信号、日报及市场数据哈希 |
| 上线门槛 | 手工模拟盘可直接下单 | Published → 回测 → 机器检查 → 人工 Verified → 人工 Promoted |

回测输出包括总收益、年化收益、最大回撤、Sharpe、换手率和总费用，并提供基金买入
持有、基金类别指数、期初披露持仓静态组合、沪深 300 或自定义指数基准。机器验证会
检查无前视、lineage 完整性、确定性复跑、样本/信号覆盖、费用完整性和绩效阈值；
机器通过后仍不能自动晋级。

同一租户、用户与输入快照哈希的 `queued` / `running` 请求，在单个回测服务实例内
会复用已有任务，防止连续点击产生并发副本；这不是跨进程唯一性保证。已完成任务仍可
再次运行以验证确定性，失败或取消任务也可重试；这些运行及其父子关系都作为原始审计
记录保留。

完整差异、两种回测模式、费用/基准、数据库与 API 变化、升级步骤和能力边界见
[《Cognitrade 1.0 → 2.0 中文升级报告》](./UPGRADE_REPORT_1.0_TO_2.0_CN.md)。
UZI 对齐细节见 [《Cognitrade 2.0 中文版本说明》](./RELEASE_NOTES_2.0_CN.md)。

## 2.0 数据中心：先采集、补采、确认冻结，再生成报告

正式研究数据已从 UZI 报告运行中抽离。SQLAlchemy 业务库是唯一事实源，
数据准备和报告执行是两个独立阶段：

当前生产基线固定为 UZI-Skill `3.9.4` / `b3665e85b8666477038e11157d8e8feb0e006b76`。
新采集只写 `uzi-3.9.4-b3665e8-v1`；升级前的 `uzi-3.9.4-1b5cdcb-v1`
Bundle 仍可只读物化和冻结 report manifest，但不会再被新任务写入。

```text
显式采集 Job / 显式启用的归档订阅
  -> 统一证券身份解析
  -> UZI 原生 provider 采集 22 维并按日写入可修订 Snapshot 草稿
  -> UZI Stage 1：原生 provider → MX/DDGS → Playwright 自动兜底
  -> 用户按需启动 UZI Agent：suggested-fix 缺口循环 → 三组定性 Agent evidence → 重审
  -> 股票生成精确冻结候选；基金生成 1 个父槽 + 10 只 A 股 × 22 槽的组合候选
  -> 用户核对 proposal hash 与缺口披露后，显式确认发布不可变日级 Bundle
  -> 冻结单股/组合 report manifest
  -> Lite / Medium / Deep / 持仓子报告 / 日报只读执行
```

这里的 22 是“20 个编号 collector（`0_basic`–`19_contests`）+ 2 个采集
collector（`6_fund_holders`、`similar_stocks`）”。`20_valuation_models`、
`21_research_workflow`、`22_deep_methods` 是基于 Bundle 计算的 3 个报告派生结果，
不计入采集完整度。数量、顺序、required fields、freshness 和合法空值规则
均由 `GET /api/v2/finance/market-data/contract` 返回，客户端不应硬编码 `22`。

所有采集入口共享同一个类型化身份解析器，客户端可先通过
`POST /api/v2/finance/market-data/instruments/resolve` 预检。例如 `020464`
解析为 `otc_fund / CN_FUND`，不得
再按深市股票进入 22 槽采集。`CN_FUND` 的契约只有一个
`fund_top_holdings` 槽；该父快照/Bundle 只负责定义报告时点可见的
精确 10 只 A 股及披露权重，不是一份“基金 22 槽”报告。
行情数据中心、Bundle/report-manifest 以及公开持仓报告 API 均固定为 10。
已有 `StrategyDocument` 仍可能保存旧版 5–20 配置，但严格父 Bundle 执行只接受 10；
这些旧文档需要在再次执行前显式迁移，系统不会静默改写其历史内容。

股票可用性与报告门严格跟随 UZI：provider 返回的原生 `DimResult`
决定该维是否有可用数据；`required_fields`、A/B 证据、PIT、过期与冲突
只进入独立审计，不再把 UZI 认为有数据的维度改判为“不可用”。
`report_generatable` 表示 22 个结果槽均已有原生结果或明确缺口，可交给完整 Agent 恢复流程；
`report_ready` 是 UZI Medium 数据阶段预检。最终是否能输出 HTML/PDF，仍由
UZI 完整 Agent 流程结束后的原生 self-review 决定：有 critical 阻断，
warning 允许出报告并披露。超时、未配置、无权限、解析失败或开放搜索
无命中会如实落为 `data_gap`，不会被填成中性分。

外部采集默认关闭：打开页面、访问任何 GET、查询 coverage 或创建报告都不会隐式
联网。用户可创建采集 Job，或同时显式开启单标的订阅和全局
`COGNITRADE_MARKET_DATA_AUTO_COLLECTION_ENABLED=true`。既有且合格的快照默认跳过；
这两个 opt-in 开关对股票和基金归档都生效。任务按标的 × 维度持久化
checkpoint；基金父快照一旦成功落库，重试会复用它并只补尚未就绪的
持仓股票槽，不会因某个子任务失败重采整个基金。
人工 Job 无论股票还是基金都不会自动发布 Bundle：provider 采集完成后，用户可先按需
启动 Agent 缺口补采，也可以直接审阅冻结候选；只有提交候选的精确
`proposal_hash`（降级候选还需明确确认披露）后才会冻结。订阅 Job 的双 opt-in
自动发布能力保持不变。Agent 补采只追加新 Snapshot，不改写既有行；已冻结 Job
不能重试或补采，需要新建 Job 形成新修订。
采集、Agent 与基金父任务的持久租约会定期续约，并以 owner token 阻止过期 worker
覆盖新 worker；Bundle 发布、revision 分配与 Snapshot 撤销同样由数据库锁协调。
兼容 `GET /api/v2/finance/market-data/{symbol}` 中的 `refresh=true` 不再刷新上游；
`POST /api/v2/finance/market-data/report-manifests` 只冻结输入，不直接启动报告 run。
公开证券搜索与报价 GET 同样只读数据库；`refresh=true` 不会采集。只有显式提交
模拟订单或显式请求挂单重新撮合时，服务端才会走独立的实时成交报价路径，这条路径
不写入研究 Bundle，也不能被正式报告消费。

历史 PIT 不用当前值伪造。股票历史 Job 按维度/来源能力处理：宏观、研报、资金流、
政策、事件、龙虎榜和舆情等带独立日期记录的来源会自动查询并裁剪到目标日 cutoff；
能满足严格契约的结果按 B 级历史重建落库，查询过但没有可验证目标日记录，或仅提供
当前状态的来源，则写入可审计 `data_gap`，自动排除评分并在报告中披露。只要 22 个槽
都已有原生结果或明确缺口记录，就可形成 `quality=degraded` 的冻结候选；用户可先让
UZI Agent 尝试恢复并重新生成候选，也可确认缺口披露后冻结；`report_ready`、`report_generatable`
与 `audit_ready` 始终分别展示。

基金的外部持仓获取只允许在上海时区当日的
采集 Job 中发生（用户显式创建，或由双 opt-in 订阅授权调度）；历史日只能
复用已落库、且能证明在 cutoff 前可见的
基金快照/Bundle，缺失时直接失败，不 live 回填：没有父持仓披露就无法知道目标日
应分析哪 10 只股票，不能安全降级成另一个持仓集合。完整方案与已知边界见
[《Point-in-Time 数据中心实施方案》](./plan/market-data-report-decoupling-plan.md)。
Agent OS 正式研究可用 ISO 日期约束 `analysis_as_of_date=YYYY-MM-DD` 精确选择历史
Bundle；未解决槽、无 Bundle 或 cutoff 晚于评估时点仍会失败，安全降级槽则随
manifest 冻结、从 UZI 维度加权中剔除，并进入报告数据缺口区，绝不回退当前值。
基金报告冻结时，10 个股票 Bundle 之外还会把父基金 Bundle ID、
Bundle manifest hash 以及父快照 hash 写入 report manifest 依赖和最终 hash；
父披露变更或失效时，原报告输入不会被静默复用。升级环境需在备份后
显式执行 Alembic `20260820_0003_market_data_point_in_time.py` 和
`20260820_0004_fund_holding_inputs.py`；API 进程启动不会隐式改库。
仅对早期 `create_all`/auto-bootstrap 生成、没有 `alembic_version`，且经逐表、
逐列和约束对比确认与 0003 完全一致的旧本地库，才可在备份后先执行
`alembic stamp 20260820_0003`，再 `alembic upgrade head`。普通已版本化数据库禁止
盲目 `stamp`；生产部署仍必须使用显式“备份 → migrate”流程。

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

部署镜像内可显式核验 UZI 3.9.4 的 22 类真实数据链；该独立 smoke 检查不会在启动时自动执行，
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
该 smoke 全量检查固定串行执行，避免多个 AkShare 路径并发初始化其进程级
JavaScript 运行时；数据中心的显式采集 Job 是独立路径，逻辑任务默认并发 5。

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
`<workspace>/paper_trading/veighna_runtime/.vntrader`；基金回测的 Hikyuu 技术信号在受控
子进程和 `<strategy_object_store>/.hikyuu_runtime` 隔离目录运行。两者都不会依赖运行
用户的主目录，Hikyuu 也不会自动下载远程策略仓库。旧 `<workspace>/backtests/*.json`
仅作为只读历史文件保留，不再由 API 生成或列出。

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
OPENAI_REASONING_EFFORT=low
OPENAI_SERVICE_TIER=default
OPENAI_DISABLE_RESPONSE_STORAGE=true
UZI_LLM_REQUEST_TIMEOUT_SECONDS=480
UZI_LLM_MAX_CONCURRENCY=2
UZI_LLM_TRANSIENT_RETRIES=6
UZI_LLM_RETRY_BASE_SECONDS=2
UZI_LLM_RETRY_MAX_SECONDS=60
```

UZI 报告不再给所有请求套同一个 480 秒上限：Lite、Medium、Deep 的结构化阶段
分别使用 240、600、900 秒；内容较短的四个买入区间始终最多使用 300 秒。
`UZI_LLM_REQUEST_TIMEOUT_SECONDS` 仅作为未归属档位的兼容回退。`similar_stocks`
在原生 UZI 中是 bonus fetcher，但在数据中心契约中是 22 个原始 collector 之一。
直接/legacy 路径的单次 HTTP 默认 5 秒，v3 spawn 路径由上游进程调度器执行
30 秒硬终止；当前日超时会如实记录为可重试 `data_gap` 并阻止严格 Bundle 发布；
历史查询穷尽后可转成排除评分的终态披露，不伪造同行数据。

正式的 Lite、Medium、Deep 个股分析统一执行 UZI `SKILL.md` 的完整 Agent 工作流：
20 个编号维度逐项审阅、66 席 persona 角色评审、宏观/政策、行业/事件、原料/期货三组
六维定性研究，以及机械自审通过后才允许报告交付。档位仍控制数据预算、推理强度、
机构方法和 Deep 独有多空辩论；“快速模式”只由独立 `quick_scan` 意图进入，不会再把
正式 Lite/Medium 报告静默降级成规则引擎骨架。

完整 Agent 阶段使用冻结快照并按 `UZI_LLM_MAX_CONCURRENCY` 调度：角色评审与六维研究
同波并行，维度评论按 5 维一组并发，四类买入区间并发。失败恢复刻意优于原生 CLI 的
整轮重跑：角色评审按分组、六维研究按分组、维度评论按批次、买入区间按派系分别缓存；
机械校验失败时只清除并重试受影响阶段，已经验证的真实证据和模型输出继续复用。

没有模型 Key 时，系统仍可执行确定性 Pack 能力，但需要模型合成的最终答复可能以
`completed_partial` 结束。`COGNITRADE_API_ALLOW_UNAUTHENTICATED=true` 只允许
回环地址的隔离本地开发，生产部署脚本始终启用认证。

可选真实数据源配置：

```dotenv
TUSHARE_TOKEN=<runtime-secret>
MX_APIKEY=<runtime-secret>
COGNITRADE_MARKET_DATA_AUTO_COLLECTION_ENABLED=false
COGNITRADE_MARKET_DATA_COLLECTION_CONCURRENCY=5
COGNITRADE_FUND_HOLDING_REPORT_CONCURRENCY=2
```

三组并发互不传导：数据中心采集股票 Pipeline 并发范围为 1–5、默认 5；持仓报告 worker 默认 2，
可调到 5；模型请求继续由独立的 `UZI_LLM_MAX_CONCURRENCY` 限流，默认 2。
提高持仓 worker 不会把采集或模型并发一并放大；provider 内部仍可以有更严格的独立限流。

2026-08-18 同一真实 provider、同一股票的两轮小样本基准如下；这是启用全档位完整
Agent 工作流前的历史基线，不能直接当作当前版本 SLA/P95：

| 档位 | 平均时间 | 两次范围 | 结果说明 |
| --- | ---: | ---: | --- |
| Lite | 5.39 分钟（墙钟） | 4.20–6.58 分钟 | 两次均可交付 |
| Medium | 15.86 分钟（墙钟） | 11.98–19.73 分钟 | 均被真实资金流缺口质量门拦截 |
| Deep | 42.01 分钟（执行图） | 11.21–72.81 分钟 | 第二次触发 900 秒模型阶段超时，方差很高 |

同环境另跑 5 只真实 A 股的 Lite 冷批次，持仓 worker=5、模型上限=4，峰值确实达到
5，`5/5` 报告可交付，批次墙钟 25.29 分钟，折合吞吐约每 5.06 分钟一份；单股从批次
开始到交付平均 24.24 分钟。该基线中 2 份报告的 narrative 证据路径被机械门拒绝并
按 Lite 规则降级，随后已增加只重试 `narrative_core` 的定向修复。十大持仓在冷缓存、
相同 provider 状态下应按约 45–60 分钟规划，不能把单股均值直接除以 5。

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
  market_data/            # PIT 契约、快照/证据、采集任务、Bundle 与报告输入
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
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q tests
.venv/bin/python -m compileall -q research_system domain_packs scripts

cd frontend
npm run lint
npm test
npm run build:pages
```

部署后可执行：

```bash
./scripts/deploy.sh status
curl -fsS http://127.0.0.1:8080/
```

## 数据真实性

行情、财务、研究 Artifact 和 Agent 结论不得由前端伪造。上游不可用时，API 会返回
明确的质量状态或 `data_gap`，前端显示可读说明。数据中心的 provider
“测试”仅检查依赖和配置是否就绪，不发出网络请求，不能当作数据可获得性证明。少数尚无后端合同的交互使用显式
“本地预览”，只处理浏览器已加载的数据或文件名/大小，不会上传材料或冒充研究结果。
