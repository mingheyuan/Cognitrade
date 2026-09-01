# Cognitrade API v2

## 版本范围

API v2 提供 Finance Workspace、版本化基金报告策略、回测与模拟交易接口。Agent OS 研究运行、Chat、Dashboard、Admin 与 System 接口仍保留在 `/api/v1`；v2 不复制或修改这些契约。

```text
Frontend
  -> /api/v2/finance
    -> MarketDataCenterService
      -> PostgreSQL / SQLite PIT snapshots, evidence and immutable Bundles
      -> explicit collection jobs / explicitly enabled archive subscriptions
      -> UZI 3.9.4 provider adapters (collection phase only)
      -> frozen single-stock / portfolio report manifests
    -> FinanceMarketDataService
      -> DB-only security / market-data / quote GET projections
      -> explicit server-side execution quotes for paper-order POSTs
    -> PaperTradingService
      -> VeighNa PaperAccount headless tick crossing
      -> server-side quote
      -> SQLite paper ledger
    -> Strategy domain
      -> PostgreSQL / SQLite business truth
      -> point-in-time daily reports + canonical UZI holdings
      -> deterministic four-state signals
      -> Hikyuu fund-only / holdings-portfolio adapters
      -> verification -> promotion -> strategy paper orders
      -> content-addressed object store
```

核心操作按业务域分组如下；完整请求/响应 schema 以 `/openapi.json` 为准：

| 业务域 | 主要路径 | 用途 |
| --- | --- | --- |
| 行情数据中心 | `/market-data/**` | PIT 快照/证据、显式采集、归档、不可变 Bundle 和报告输入冻结 |
| 兼容行情与报价预览 | `/securities`、`/market-data/{symbol}`、`/quotes/{symbol}` | 本地证券解析与已发布 Bundle 只读投影；GET 不请求上游 |
| 策略 CRUD | `/strategy-catalog`、`/strategies/**` | 草稿、不可变版本、diff、归档、恢复与审计 |
| 基金绑定 | `/strategy-assignments/**` | 把已发布版本绑定到基金和回测模式 |
| 日报编排 | `/report-jobs/**`、`/report-backfills/**`、`/daily-report-sets/**` | 日任务、补跑、失败节点重试、暂停/恢复和 revision |
| 信号 | `/signal-previews`、`/trade-signals/**` | 基于真实日报预演、生成和查询四态信号 |
| 基金回测 | `/strategy-backtests/**` | 唯一公开回测入口；版本合同预检、运行、重试、取消和结果查询 |
| 治理 | `/strategy-verifications/**`、`/strategies/{id}/versions/{id}/promote` | 自动验证、人工确认和晋级 |
| 策略模拟盘 | `/paper-strategy-bindings`、`/paper-strategy-bindings/{id}/orders`、`/strategy-paper-orders` | Promoted 版本的受控下单与 lineage |
| 通用模拟盘 | `/paper/**` | VeighNa 撮合、账户、组合、订单和撤单 |

## 认证、请求 ID 与错误

所有 `/api/v2` 操作使用与 v1 相同的 Bearer 认证：

```http
Authorization: Bearer <COGNITRADE_API_TOKEN>
```

仅隔离的回环地址本地开发可设置 `COGNITRADE_API_ALLOW_UNAUTHENTICATED=true`。响应带 `X-Request-ID` 和 `Cache-Control: no-store`。错误使用统一信封：

```json
{
  "error": {
    "code": "insufficient_cash",
    "message": "可用资金不足",
    "details": {
      "required": 100055.01,
      "available": 50000.00
    },
    "request_id": "01J..."
  }
}
```

所有资源查询和变更都按认证主体的 `tenant_id + user_id` 作用域隔离；系统模板只读共享。
前端对 `/api/v2/finance` 的 `POST/PUT/PATCH/DELETE` 自动发送 `Idempotency-Key`。调用方也可
自行提供 1–128 个可见 ASCII 字符：同一 owner、方法、目标和 body 会持久化重放成功 JSON
响应并返回 `Idempotent-Replay: true`；相同 key 改变请求返回 `409
idempotency_key_reused`，仍在租约中的重复请求返回 `409 idempotency_request_in_progress`。
失败响应不缓存，过期租约可重领。草稿更新同时要求 `If-Match: W/"<draft_revision>"`。

常见业务错误：

| HTTP | code | 含义 |
| --- | --- | --- |
| `404` | `security_not_found` | 未识别到 A 股代码或名称 |
| `409` | `security_ambiguous` | 名称匹配多只证券 |
| `503` | `quote_unavailable` | 当前没有服务端可成交价格 |
| `409` | `insufficient_cash` | 可用现金不足 |
| `409` | `insufficient_position` | T+1 可卖持仓不足 |
| `409` | `market_closed` | 当前不在 A 股连续竞价时段 |
| `409` | `security_suspended` | 证券停牌或不可交易 |
| `409` | `price_limit_reached` | 买入触及涨停或卖出触及跌停 |
| `409` | `invalid_quote` | 行情价格缺失或不是正数 |
| `422` | `invalid_board_lot` | 委托不符合 100 股规则 |
| `409` | `order_not_cancellable` | 委托已成交或已撤销 |
| `422` | `insufficient_kline_history` | K 线数量不足以运行策略 |
| `503` | `backtest_engine_unavailable` | Hikyuu 未安装 |
| `504` | `backtest_timeout` | 隔离回测超过 120 秒 |
| `409` | `published_bundle_missing` | 指定标的/日期没有已发布 Bundle |
| `409` | `market_data_bundle_incomplete` | 股票 22 个 UZI 结果槽未冻结，或基金父契约槽未通过严格验收 |
| `422` | `mixed_asset_collection_unsupported` | 同一采集 Job 混合了基金和股票 |
| `409` | `fund_holding_bundle_required` | 基金报告未指定已发布的父基金 Bundle |
| `409` | `published_bundle_invalidated` | 报告输入引用的 Bundle 已失效 |
| `409` | `bundle_cutoff_not_reached` | Bundle cutoff 尚未到达，不能提前冻结报告 |
| `409` | `bundle_after_report_evaluation` | Bundle cutoff 晚于请求的 `evaluation_at`，存在未来数据风险 |
| `409` | `report_evaluation_in_future` | 请求的 `evaluation_at` 晚于当前时间 |
| `422` | `validation_error` | 请求字段或范围无效 |

## 证券解析

### `GET /api/v2/finance/securities`

查询参数：

| 参数 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- |
| `query` | 是 | 1–80 字符 | 六位代码、公司简称或常用别名 |
| `limit` | 否 | 1–20，默认 8 | 最大返回数量 |

```http
GET /api/v2/finance/securities?query=茅台&limit=8
```

```json
{
  "query": "茅台",
  "results": [
    {
      "symbol": "600519",
      "name": "贵州茅台",
      "latest_price": 1420.5,
      "pct_change": 1.15,
      "source": "market_data_bundle"
    }
  ]
}
```

## 行情与研究数据

正式研究数据已改为数据库唯一事实源。页面、任何 GET、coverage 查询和报告
创建均不会触发 provider；数据缺失时客户端必须显式创建采集 Job，或先显式启用
归档订阅。旧 UZI/Finance Pack 文件缓存不迁移、不回退。

### 统一证券身份解析

采集和报告入口共享同一类型化身份解析器，不得只依赖六位数字前缀猜测
资产类型。客户端可先调用统一解析端点：

```http
POST /api/v2/finance/market-data/instruments/resolve
Content-Type: application/json

{
  "query": "020464",
  "asset_family": "auto"
}
```

其中 `asset_family` 可为 `auto`、`equity` 或 `fund`。`020464` 这类场外基金会返回
类型化身份，不会被当成深市股票：

```json
{
  "query": "020464",
  "candidates": [
    {
      "symbol": "020464",
      "code": "020464",
      "asset_type": "otc_fund",
      "market": "CN_FUND",
      "exchange": "OTC",
      "exchange_timezone": "Asia/Shanghai",
      "security_type": "etf_feeder",
      "name": "招商中证半导体产业ETF发起式联接A",
      "currency": "CNY",
      "resolution_source": "fund_security_master",
      "instrument_id": "otc_fund:OTC:020464"
    }
  ],
  "resolved": {
    "symbol": "020464",
    "code": "020464",
    "asset_type": "otc_fund",
    "market": "CN_FUND",
    "exchange": "OTC",
    "exchange_timezone": "Asia/Shanghai",
    "security_type": "etf_feeder",
    "name": "招商中证半导体产业ETF发起式联接A",
    "currency": "CNY",
    "resolution_source": "fund_security_master",
    "instrument_id": "otc_fund:OTC:020464"
  },
  "ambiguous": false
}
```

解析结果决定后续使用 `CN_EQUITY` 还是 `CN_FUND`
契约；客户端不应将基金代码送入股票 22 槽收集器。

### 股票 22 槽与基金父单槽契约

`GET /api/v2/finance/market-data/contract?market=CN_EQUITY` 是股票维度数、
顺序、required fields、freshness、历史窗口和 `verified_empty` 规则的
唯一客户端契约。当前股票契约版本为 `uzi-3.9.4-b3665e8-v1`；旧版
`uzi-3.9.4-1b5cdcb-v1` Bundle 保持只读兼容，新采集不会再写入旧契约，
`expected_dimension_count=22`：

- 20 个编号 collector：`0_basic`–`19_contests`。
- 2 个采集 collector：`6_fund_holders`、`similar_stocks`。
- 3 个报告派生维度 `20_valuation_models`、`21_research_workflow`、
  `22_deep_methods` 不在采集契约和 22/22 分母中。

客户端必须读取 `expected_dimension_count` 和有序 `dimensions`，不得自行硬编码。
该契约的 required fields、freshness、PIT 和证据等字段供数据质量审计，
不取代 UZI 自己的可用性判定和报告 self-review。

`GET /api/v2/finance/market-data/contract?market=CN_FUND` 则返回独立的
`cognitrade-fund-top-holdings-pit-v1` 契约，`expected_dimension_count=1`，且唯一槽为
`fund_top_holdings`。该父槽必须准确包含 10 只唯一、可用的标准 A 股代码，
`verified_empty` 不合法。基金契约是持仓展开入口，不是股票 22 槽的子集
或 Deep superset。

### 覆盖与完整性

```http
GET /api/v2/finance/market-data/coverage?symbol=600519.SH&market=CN_EQUITY&as_of_date=2026-08-20
```

响应返回 `expected_dimension_count`、`ready_dimension_count`、`counts`、
`structurally_complete`、`report_ready`、`report_generatable`、`audit_ready`、
`uzi_integrity_by_profile`、`latest_bundle` 以及逐维 `status`、`native_quality`、
`eligible`、`reason`、`audit_eligible`、`audit_reason`、`evidence_grade`、`stale` 和 `conflict`。

对股票，`eligible` 只表示 UZI 原生结果是否有可用 payload；A/B 证据、
required fields、PIT、过期和冲突在 `audit_*` 中单独披露，不重写 UZI 状态。
`report_generatable=true` 表示 22 个槽均有原生结果或明确缺口，可进入 UZI Agent 恢复流程；
`report_ready` 是 Medium 档数据阶段预检，不是最终 HTML 发布结论。最终完整
Agent self-review 仅在 `critical_count > 0` 时阻断，warning 允许出报告并留痕。
基金父 `fund_top_holdings` 契约仍是严格 PIT/A-B 验收，不适用股票 UZI 门语义。

`GET /api/v2/finance/market-data/snapshots` 支持按 `symbol`、`dimension_id`、
`as_of_date` 过滤；`GET /api/v2/finance/market-data/snapshots/{snapshot_id}` 额外返回
payload、字段 provenance、四个 PIT
时间、证据、query manifest 和冲突。列表 `limit` 范围 1–200，默认 50；
`offset` 默认 0。

### 显式采集任务

```http
POST /api/v2/finance/market-data/collection-jobs
Content-Type: application/json

{
  "symbols": ["600519.SH"],
  "start_date": "2026-08-20",
  "end_date": "2026-08-20",
  "dimensions": "all",
  "mode": "missing_or_stale",
  "max_concurrency": 5,
  "publish_when_complete": false,
  "asset_family": "equity"
}
```

`dimensions` 可为 `all` 或契约维度 ID 数组；`mode` 可为 `missing_or_stale` 或
`force_all`；`max_concurrency` 范围 1–5，默认 5；超出范围返回 `422 validation_error`。
该值限制同时运行的股票采集 Pipeline 数，每只股票内部仍使用 UZI 原生 6 路 fetcher。
任务、标的 × 维度子任务、attempt、
checkpoint 和每日结果持久化。重启后只恢复已由用户创建的 pending Job，不会
自行创建新 Job。
人工 Job 的 `publish_when_complete` 必须为 `false`（省略时也是 `false`）；传
`true` 返回 `422 manual_auto_publish_not_allowed`。采集完成只生成可审阅的冻结候选，
不会生成 Bundle。

基金只是前十大持仓股票分析的父入口。一个基金 Job 只接受一只基金、
一个自然日和固定 `top_n=10`；不允许与股票混合创建，也不允许只选部分
持仓股票维度：

```http
POST /api/v2/finance/market-data/collection-jobs
Content-Type: application/json

{
  "symbols": ["020464"],
  "start_date": "2026-08-20",
  "end_date": "2026-08-20",
  "dimensions": "all",
  "mode": "missing_or_stale",
  "max_concurrency": 5,
  "publish_when_complete": false,
  "asset_family": "fund",
  "top_n": 10
}
```

后端先为 `020464 / CN_FUND` 验收并落库单槽
`fund_top_holdings` 父 Snapshot，再按父快照固定的精确 10 只 A 股展开
10 份完整 22 槽采集。基金 Job 响应会标记 `asset_family=fund`、
`top_n=10`，并在 checkpoint 就绪后返回 `expanded_holdings`、
`child_collection_job_ids`、`parent_snapshot_id`；用户确认冻结后才返回
`parent_bundle_id`，
便于审计整条展开链。

```text
GET  /api/v2/finance/market-data/collection-jobs
GET  /api/v2/finance/market-data/collection-jobs/{job_id}
POST /api/v2/finance/market-data/collection-jobs/{job_id}/retry-failed
POST /api/v2/finance/market-data/collection-jobs/{job_id}/agent-recovery
POST /api/v2/finance/market-data/collection-jobs/{job_id}/confirm-freeze
POST /api/v2/finance/market-data/collection-jobs/{job_id}/cancel
```

`retry-failed` 只重置可重试且尚有 attempt 预算的失败/部分维度，不全量重跑。
对基金 Job，父 `fund_top_holdings` 快照一旦成功落库并写入 checkpoint，
后续重试会复用该父快照，只为仍缺失或过期的持仓股票槽创建
child Job；不会因一只持仓股失败而重采整份基金披露。
`agent-recovery` 是用户显式启动的 UZI 完整缺口恢复阶段。原生 Stage 1 先完成
MX→DDGS 与 Playwright 自动兜底；随后 Agent 按 suggested-fix 缺口循环、三组定性
Agent evidence 和完整性复检顺序追加新 Snapshot，并重新计算候选。
`confirm-freeze` 必须提交当前候选的 64 位 `proposal_hash`；候选为 degraded 时
还必须提交 `acknowledge_degraded=true`。股票确认发布 1 个 Bundle；基金确认在同一事务
发布父基金与 10 只持仓股票共 11 个 Bundle。已冻结 Job 不允许重试或 Agent 补采。
运行中 Job 与 Agent stage 会续租并用 owner token 防止过期 worker 覆盖新 worker；
服务重启只接管已过期租约。内部调度或父子任务重放同一 idempotency key 时，只有
规范化请求完全一致才会复用原 Job，否则返回 `409 collection_idempotency_conflict`。
原生 UZI provider 对无 `as_of` 的历史日期不会拿当前值回填，而是记录
`pit_not_supported` / `data_gap`；该缺口需带不可变证据的人工 B 级快照，或新的
PIT-aware adapter。
基金持仓 provider 更严格：只有上海时区当日的采集 Job（用户显式创建，
或由双 opt-in 订阅授权调度）才会访问外部来源；
历史日只查询已落库且通过 PIT 契约的快照/Bundle，缺失时返回
Job 错误 `fund_historical_pit_unavailable`，不 live 采集、不用最新披露回填。

### 人工证据、冲突与撤销

`POST /api/v2/finance/market-data/manual-snapshots` 仅允许 B 级，必须提供可验证的不可变证据，且
`published_at <= cutoff_at`；人工录入仍必须满足该维度的 required fields，不能绕过契约。
`POST /api/v2/finance/market-data/conflicts/{conflict_id}/resolve` 用于选择合法候选或标记无效；
人工覆盖要求先录入 B 级快照。
`POST /api/v2/finance/market-data/snapshots/{snapshot_id}/revoke` 只撤销/替代，
不物理删除已引用数据。

### 不可变 Bundle

```text
GET  /api/v2/finance/market-data/bundles
GET  /api/v2/finance/market-data/bundles/{bundle_id}
POST /api/v2/finance/market-data/bundles/publish
```

发布动作是纯数据库操作，不访问 provider。`CN_EQUITY` 严格候选要求 UZI 22 维
质量门全部通过；若 22 个槽均有原生结果或明确 `data_gap`，则可形成 degraded 候选，
但必须由用户明确确认缺口披露。`CN_FUND` 父 Bundle 必须通过唯一的
`fund_top_holdings` 槽（精确 10 只 A 股）。候选不成立时返回
`409 market_data_bundle_incomplete`。相同 manifest 幂等，内容变化产生新 revision，
不覆盖旧 Bundle；并发发布在同一 symbol/market/day/contract scope 内串行分配 revision。
发布与 Snapshot 撤销共用数据库锁，基金 11 个 scope 还会按全局稳定顺序预锁；不会留下
引用已撤销 Snapshot 的 published Bundle，也不会发布半套基金组合。人工 Job 推荐只经
`confirm-freeze` 发布其精确候选。

### 归档订阅与 provider 能力

```text
GET /api/v2/finance/market-data/subscriptions
PUT /api/v2/finance/market-data/subscriptions/{symbol}
GET /api/v2/finance/market-data/providers
POST /api/v2/finance/market-data/providers/{provider_id}/test
```

单标的归档订阅默认 `enabled=false`。即使某订阅已开，也只有同时设置
`COGNITRADE_MARKET_DATA_AUTO_COLLECTION_ENABLED=true` 后，调度器才会按订阅创建采集
Job；默认全局值为 `false`。基金和股票共享这个“单标的 `enabled=true`
+ 全局开关”双 opt-in 门禁；基金订阅请求必须使用 `market=CN_FUND`，
且调度仍固定为前十大持仓 × 每只股票完整 22 槽。订阅的 `max_concurrency`
同样限定为 1–5，默认 5。

provider 列表只暴露 dependency/credential/policy 是否就绪，不暴露密钥。
`POST .../providers/{provider_id}/test` 当前仅检查配置和本地依赖，不发起网络请求；
成功不等于上游数据在当时可获得。

### 报告输入 manifest

`POST /api/v2/finance/market-data/report-manifests` 从已发布 Bundle 冻结单股或组合的
`symbol -> bundle_id`、权重、父依赖、workflow pins 和 manifest hash。该端点只持久化并返回
`status=frozen` 的报告输入，不直接启动 Agent OS run。Lite / Medium / Deep、基金持仓子报告
和日报的正式执行会通过各自入口冻结/校验同等 manifest，并设置
`constraints.data_source_mode=bundle_only`。报告缺 Bundle 时直接预检失败，不会转入采集。

```http
POST /api/v2/finance/market-data/report-manifests
Content-Type: application/json

{
  "symbols": ["600519.SH"],
  "market_by_symbol": {"600519.SH": "CN_EQUITY"},
  "as_of_date": "2026-08-20",
  "evaluation_at": "2026-08-20T15:30:00+08:00",
  "report_kind": "single_stock",
  "workflow_pins": {"profile": "deep"},
  "weights": {}
}
```

基金前十大持仓 manifest 不接受客户端自行组装的子股列表或权重。
`symbols` 只传父基金代码，并必须指定同日已发布的父基金 Bundle：

```http
POST /api/v2/finance/market-data/report-manifests
Content-Type: application/json

{
  "symbols": ["020464"],
  "as_of_date": "2026-08-20",
  "evaluation_at": "2026-08-20T15:30:00+08:00",
  "report_kind": "fund_top_holdings",
  "workflow_pins": {"profile": "deep"},
  "asset_family": "fund",
  "fund_bundle_id": "01M..."
}
```

服务端从该父 Bundle 的唯一 `fund_top_holdings` 快照中派生精确 10 只 A 股、
披露权重和顺序，然后冻结它们各自的完整 22 槽 Bundle。响应中的
`items` 是这 10 个股票 Bundle；`dependencies` 另外持久化父基金的
`bundle_id`、`bundle_manifest_hash`、`snapshot_id` 和
`snapshot_content_hash`。父依赖也进入规范化 manifest hash，所以即使 10 只
股票 Bundle 未变，基金披露修订也不会误复用旧报告输入；父 Bundle 被撤销
或失效时，关联 report manifest 一并失效。

每个 Snapshot 同时携带 `source`、`primary_source`、`fallback_sources`、
`field_sources` 与 `source_trace`。来源目录固定对齐 UZI 3.9.4 的候选来源，
但目录项只是候选能力；客户端必须以本次 `source_trace[].status` 判断实际是否取到数据，
不能把“存在于目录”解释成“数据可用”。Token、Cookie 和 API Key 只通过运行环境配置，
任何响应都不会返回凭据值。

`evaluation_at` 是报告防未来数据边界，必须是带时区的时间，且满足“不早于本次
所有股票 Bundle 和父依赖 Bundle 的 cutoff、不晚于服务器当前时间”。
早于任一 Bundle cutoff 返回
`409 bundle_after_report_evaluation`，晚于当前时间返回
`409 report_evaluation_in_future`；Bundle 自身 cutoff 尚未到达时返回
`409 bundle_cutoff_not_reached`。未传时由服务端取组合中最晚 Bundle cutoff。合法的
`evaluation_at` 进入 manifest 的规范化 hash，因此同日不同评估时点不会误复用输入。

### 兼容 `GET /api/v2/finance/market-data/{symbol}`

该路径现在只读当日已发布且通过完整性校验的 Bundle，并投影为旧 Finance
Workspace 的 `sections`、`snapshots` 和 `source_refs` 格式。建议传入可本地解析的代码；
无 Bundle 时由 Finance 兼容路由返回 `409 published_bundle_missing`。`refresh=true` 只为兼容旧客户端而保留，
响应会标记 `refresh_requested=true`、`refresh_performed=false`，不读文件缓存、不联网。

### `GET /api/v2/finance/quotes/{symbol}`

只读当日已发布 Bundle 中的报价投影；`refresh=true` 仍不请求上游：

```json
{
  "symbol": "600519",
  "name": "贵州茅台",
  "latest_price": 1420.5,
  "bid_price_1": 1420.0,
  "ask_price_1": 1421.0,
  "pct_change": 1.15,
  "as_of": "2026-07-13",
  "source": {
    "mode": "bundle_only",
    "bundle_id": "01M...",
    "manifest_hash": "..."
  },
  "quality_status": "available",
  "refresh_requested": false,
  "refresh_performed": false
}
```

客户端报价仅供预览。显式提交订单或请求挂单重新撮合时，后端会走独立的实时执行
报价路径并且不接受客户端成交价；该实时价不会回写研究 Bundle，也不会被正式报告消费。

## 模拟交易

模拟盘是服务端持久化账本，不连接券商。数据库默认位于：

```text
<workspace_root>/paper_trading/paper_trading.sqlite3
```

生产 Compose 的 workspace 命名卷会持久化该文件。账户隔离键来自认证 principal 的 `tenant_id` 与 `user_id`；客户端不能在请求体中切换账户。

当前撮合规则：

- VeighNa `vnpy_paperaccount` 负责官方订单模型与到价撮合；Cognitrade
  SQLite 账本负责现金、费用、T+1 和审计，因为 PaperAccount 本身不计算资金。
- 市价单按卖一/买一成交；行情缺少盘口时以最新价上下 5 bps 合成保守买一/卖一。
- 限价买单在限价不低于卖一时成交；限价卖单在限价不高于买一时成交。
- 未触价限价单保持 `pending`，冻结对应现金或 T+1 可卖数量，可撤单或请求重新撮合。
- 佣金为成交额 0.025%，单笔最低 5 元。
- 过户费为成交额 0.001%。
- 卖出印花税为成交额 0.05%。
- 买入数量必须为 100 股整数倍。
- 默认只在上海时区交易日的 `09:30–11:30`、`13:00–15:00` 接受委托，可通过 `COGNITRADE_PAPER_ENFORCE_MARKET_HOURS=false` 为隔离演示环境关闭限制。
- 当日买入记为 T+1，持仓在下一交易日而不是下一自然日可卖；交易日来自离线 XSHG 日历，卖出采用 FIFO 成本。
- 行情声明停牌、不可交易、涨跌停或无有效正价格时拒绝模拟成交。
- 禁止融券和负持仓；清仓时允许卖出全部可用零股。
- 现金与费用以整数分持久化，价格以百万分之一元持久化。

### `GET /api/v2/finance/paper/account`

没有活动账户时自动创建初始资金 100 万元的账户。

```json
{
  "account_id": "01J...",
  "generation": 1,
  "status": "active",
  "currency": "CNY",
  "initial_cash": 1000000.0,
  "cash": 1000000.0,
  "created_at": "2026-07-13T09:00:00+00:00",
  "updated_at": "2026-07-13T09:00:00+00:00",
  "rules": {
    "market": "A-share paper trading",
    "board_lot": 100,
    "settlement": "T+1",
    "short_selling": false,
    "price_source": "server-side Finance Pack quote",
    "matching_engine": "veighna-paperaccount",
    "market_hours_enforced": true,
    "calendar": "exchange_calendars:XSHG",
    "timezone": "Asia/Shanghai",
    "sessions": ["09:30-11:30", "13:00-15:00"]
  }
}
```

### `POST /api/v2/finance/paper/account/reset`

```json
{"initial_cash": 500000}
```

`initial_cash` 范围为 1 万元到 10 亿元。原账户改为 `archived` 并保留订单与持仓审计记录，新账户的 `generation` 加一。

### `POST /api/v2/finance/paper/orders`

```json
{
  "symbol": "600519",
  "side": "buy",
  "quantity": 100,
  "order_type": "limit",
  "limit_price": 1400.0,
  "client_order_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

`client_order_id` 在活动账户内唯一。相同 ID 的重试返回原订单并设置 `idempotent_replay=true`，不会重复扣款或加仓。

成功返回 `201 Created`：

```json
{
  "order_id": "01J...",
  "client_order_id": "550e8400-e29b-41d4-a716-446655440000",
  "symbol": "600519",
  "name": "贵州茅台",
  "side": "buy",
  "order_type": "limit",
  "limit_price": 1400.0,
  "quantity": 100,
  "quote_price": 1420.5,
  "fill_price": 1421.21025,
  "gross_amount": 142121.03,
  "fees": {
    "commission": 35.53,
    "transfer_fee": 1.42,
    "stamp_duty": 0.0,
    "total": 36.95
  },
  "cash_effect": -142157.98,
  "realized_pnl": null,
  "quote_as_of": "2026-07-13",
  "quote_source": "akshare_market",
  "trade_date": "2026-07-13",
  "status": "filled",
  "matching_engine": "veighna-paperaccount",
  "reserved_cash": 0.0,
  "reserved_quantity": 0,
  "created_at": "2026-07-13T09:00:00+00:00",
  "idempotent_replay": false
}
```

### `GET /api/v2/finance/paper/portfolio`

持仓按服务端行情逐只估值。报价失败时使用持仓平均成本并设置 `quote_stale=true`，避免把回退值误认为实时价格。

```json
{
  "account": {},
  "summary": {
    "cash": 857842.02,
    "cash_balance": 857842.02,
    "frozen_cash": 0.0,
    "market_value": 142050.0,
    "equity": 999892.02,
    "cost_basis": 142157.98,
    "total_pnl": -107.98,
    "total_return_pct": -0.0108,
    "position_count": 1,
    "open_order_count": 0
  },
  "positions": [
    {
      "symbol": "600519",
      "name": "贵州茅台",
      "quantity": 100,
      "available_quantity": 0,
      "average_cost": 1421.5798,
      "latest_price": 1420.5,
      "market_value": 142050.0,
      "cost_basis": 142157.98,
      "unrealized_pnl": -107.98,
      "unrealized_pnl_pct": -0.076,
      "quote_as_of": "2026-07-13",
      "quote_stale": false
    }
  ],
  "as_of": "2026-07-13T09:00:00+00:00"
}
```

### `GET /api/v2/finance/paper/orders`

`limit` 范围为 1–200，默认 50；`offset` 从 0 开始，默认 0。返回当前活动账户的
倒序成交记录及完整记录总数，调用方可以逐页读取而不丢失历史委托：

```json
{"orders": [], "total_count": 0, "limit": 50, "offset": 0}
```

### `GET /api/v2/finance/paper/engine`

返回 VeighNa 与 PaperAccount 的安装版本、当前是否使用原生 Tick crossing，以及
资金账本仍由 Cognitrade 管理的边界说明。

### `POST /api/v2/finance/paper/orders/match`

查询最新服务端行情并依次检查当前账户的 `pending` 委托。`refresh=true` 默认跳过
行情缓存。返回已成交委托、未触价 ID 和逐单错误；单个上游错误不会丢失其他挂单。

### `POST /api/v2/finance/paper/orders/{order_id}/cancel`

只有 `pending` 委托可撤销。撤单在同一数据库事务中把状态改为 `cancelled` 并释放
`reserved_cash` 或 `reserved_quantity`。

## 基金报告策略主线

### 策略、版本与基金绑定

`POST /api/v2/finance/strategies` 可用空白文档、完整 `document` 或
`from_strategy_id` 创建个人草稿。`PATCH /strategies/{strategy_id}/draft` 同时校验 body
中的 `expected_revision` 与 `If-Match`，冲突返回当前 revision。系统模板只读；未发布草稿
可以物理删除，已有版本的策略只能归档。

`POST /strategies/{strategy_id}/publish` 把完整策略合同规范化并生成稳定 SHA-256 content
hash。版本一经发布不可编辑或删除；合同固定报告 top N、复合权重、coverage 门、四态阈值、
carry 天数、MA 执行规则、三类资产费率/确认规则、模型与 UZI pin。已有 Assignment 在下一
交易日切换到新版本，不会自动替换 Promoted 模拟盘版本。

`POST /strategy-assignments` 只接受已发布策略：

```json
{
  "strategy_id": "01J...",
  "fund_code": "110011",
  "fund_name": "易方达优质精选混合",
  "mode": "fund_only",
  "enabled": true,
  "schedule": {"time": "18:00", "timezone": "Asia/Shanghai"}
}
```

`mode` 默认为产品主线 `fund_only`；`holdings_portfolio` 是披露持仓的高级穿透研究模拟，
不等于基金真实仓位。两者都必须在该版本允许的模式内。Assignment 可暂停、恢复或删除；
已有历史信号时删除会转为禁用，不破坏 lineage。

### point-in-time 日报、补跑与 UZI

`POST /report-jobs` 接受单个 `as_of_date`；`POST /report-backfills` 接受
`start_date/end_date`。先用 `/report-backfills/estimate` 估算交易日、基金报告和持仓 UZI
调用量。任务持久化 lease、attempt 和进度，可通过 `/retry` 只重试失败股票，或在
`full_rerun=true, confirmed=true` 时创建新 revision；`/pause` 与 `/resume` 不删除旧 attempt。

每个 `DailyReportSet` 固定 `as_of_date`、`effective_date`、revision、input/source/content
hash、coverage 和 Artifact 引用。基金公开信息必须在回测日期当时可获得；没有可信
`published_at/first_seen_at` 的历史证据输出 `BLOCKED`，不会借用当前数据。当前严格
父 Bundle 路径固定对 10 只股票生成 canonical UZI 子报告；同一股票、日期和 pin
跨基金复用一次，基金上下文只作为独立节点保存。

这里的 5–20 仅描述已有 `StrategyDocument` 可能保存的旧配置，不是当前可执行的
基金持仓合同。`/market-data/collection-jobs` 基金采集、
`/market-data/report-manifests` 基金 Bundle 入口及公开持仓报告 API 都固定精确
10 只 A 股；严格父 Bundle 执行会拒绝非 10 请求，旧文档必须显式迁移。

### 确定性四态信号

`POST /signal-previews` 只允许用真实 `report_set_id`，可临时应用某个个人草稿的映射规则，
但不生成伪造报告分数。`POST /trade-signals/recompute` 使用 Assignment 的生效版本持久化
基金和持仓股票信号：

- `ALLOW_BUY`：质量门和固定权重下界达到买入阈值；
- `WATCH`：覆盖率或分数只达到观察区间；
- `AVOID`：应回避，但不等同于数据错误；
- `BLOCKED`：point-in-time、关键证据或覆盖率不满足，不能据此制造买卖。

缺失因子的权重不转移给其他因子。配置允许时可沿用最近信号，但沿用天数从原始报告日期
计算并保留 `carried_from_signal_id`；late revision 会使同日旧信号失效，查询结果仍保留
完整 supersession 链。

### 严格版本回测

先调用 `POST /strategy-backtests/preflight` 检查日期范围内的日报和信号覆盖，再提交：

```json
{
  "strategy_version_id": "01J...",
  "fund_code": "110011",
  "mode": "fund_only",
  "start_date": "2025-01-01",
  "end_date": "2025-12-31",
  "initial_cash": 1000000,
  "benchmark": {},
  "report_policy": "existing_only"
}
```

请求不接受临时权重、信号阈值、MA 周期或费率。`fund_only` 支持 ETF/LOF K 线和场外基金
NAV 确认日账本；`holdings_portfolio` 以基金信号作为总门禁，以持仓 UZI 信号和冻结 MA
规则形成 A 股目标权重。每笔成交必须包含 strategy/version hash、report set、signal ID、
资产适配器、费用模型和数据 snapshot hash。结果曲线进入内容寻址对象存储，数据库保存
指标、状态、hash 与 URI。失败/取消任务可 `/retry`，排队或运行任务可 `/cancel`。

`GET /strategy-backtests` 支持 `strategy_id`、`strategy_version_id`、`fund_code`、`mode`、
`status` 和 `limit=1..200`。前端只查询当前策略，再按当前基金与模式展示；相同输入的重复
运行与重试默认合并，完整原始记录仍保留供审计和验证。

### Verified、Promoted 与策略模拟单

`POST /strategy-verifications` 必须引用同一基金、模式和精确版本的已完成回测。自动检查
point-in-time、报告/信号/成交 lineage、确定性复跑、coverage、样本天数、信号数、六项
费用完整性，以及最大回撤、年化收益、Sharpe、超额收益、换手率和费用影响门槛。机器通过
后仍需 `POST /strategy-verifications/{id}/confirm` 人工确认 Verified。

只有 Verified 版本可由 `POST /strategies/{strategy_id}/versions/{version_id}/promote`
人工晋级。一个 owner 的同一 `account_id + fund_code + mode` 只能存在一个主绑定，重复晋级
同版本幂等，发布新版本不会自动替换它。

`POST /paper-strategy-bindings/{binding_id}/orders` 只执行精确 Promoted 绑定和当前未被取代的
信号。买入同时要求个股与基金门禁为 `ALLOW_BUY`；`BLOCKED` 不制造卖出，卖出必须是版本
允许的技术、风险或报告原因。订单冻结该版本的 board lot、佣金、印花税、过户费和 T+1
规则，并保存完整 strategy/report/signal/order lineage。该操作必须提供与 `client_order_id`
一致的 `Idempotency-Key`。

## 前端配置

研究 API 地址仍配置为：

```text
VITE_COGNITRADE_API_BASE_URL=http://127.0.0.1:8000/api/v1
```

前端 API Client 对 Finance Workspace 请求自动将同一 host 的 `/api/v1` 替换为 `/api/v2`，
并为变更请求生成幂等 key。Bearer token 与 v1 共用。GitHub Pages 构建还设置
`VITE_BASE_PATH`，并生成与 `index.html` 同内容的 `404.html`；路由器会剥离仓库 base path，
因此仓库子路径下的 `/strategies` 可直接访问。

## UZI 数据完整性与真实兜底字段

股票研究的完整恢复记录保存在 `stock_snapshot_bundle.collection` 与
`uzi_native_analysis.recovery_history`，前端从 ArtifactBoard 读取，不重算：

- `uzi_integrity.before_recovery`：主 provider 完成后的 18 项检查、关键/可选缺口、16 个扩展维度缺口与逐项恢复任务；
- `uzi_integrity.after_recovery`：MX、DDGS、Playwright 全部结束后的复检；顶层覆盖率字段与此一致；
- `uzi_recovery.stage_order`：固定为 `primary_providers → integrity_check → mx_api → ddgs → playwright → suggested_fix_loop → qualitative_agent_evidence → integrity_recheck`；
- `uzi_recovery.qualitative_stage`：六类定性维度的 MX→DDGS 尝试及完整 `source_trace`；
- `uzi_recovery.playwright_stage`：按 Lite/Medium/Deep 白名单触发的浏览器最终兜底、质量触发原因、策略来源和解析状态；
- `uzi_recovery.attempts`：供列表展示的统一摘要。失败保留为 `data_gap`，不生成模拟值。

`investor_panel_review.votes[]` 严格按 UZI 3.9.2 执行 242 条规则，并新增：

- `uzi_confidence`：UZI 原始 0–100 置信度；`confidence` 仍为 Agent OS 的 0–1 归一化值；
- `investor_name_en`、`methodology_source`、`avatar_seed`、`tier`：上游席位元数据；
- `skip_reason`：仅用于整席流派锁定、市场不匹配或游资射程不匹配；
- `time_horizon`、`position_sizing`、`what_would_change_my_mind`：上游决策 profile。

逐规则 `skipped_rules`、`data_gap_rules` 不返回具体规则内容；最新 UZI 语义会将因缺数无法计算的规则排除在通过/失败及评分分母之外，并通过
`skipped_rule_count` 暴露跳过数量；有真实观测值但未达阈值的规则仍计为失败。
真实字段缺口同时通过完整性报告与维度 `checks` 单独披露。

Lite/Medium/Deep 必须由用户在研究入口显式选择，后端不会默认选择 Medium；
旧 `quick` 档位和兼容调用已删除。登录态或浏览器来源仍必须经过总开关、
敏感源开关、allowlist 与凭据校验，缺少授权时保留真实 `data_gap`。
