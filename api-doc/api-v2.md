# Cognitrade API v2

## 版本范围

API v2 提供 Finance Workspace、版本化基金报告策略、回测与模拟交易接口。Agent OS 研究运行、Chat、Dashboard、Admin 与 System 接口仍保留在 `/api/v1`；v2 不复制或修改这些契约。

```text
Frontend
  -> /api/v2/finance
    -> FinanceMarketDataService
      -> finance_ashare DomainPack snapshots
      -> UZI 3.9.2 provider chains (AkShare/eFinance/TuShare/BaoStock/direct HTTP/DDGS/browser)
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
| 行情 | `/securities`、`/market-data/{symbol}`、`/quotes/{symbol}` | 证券解析、快照与成交行情 |
| 策略 CRUD | `/strategy-catalog`、`/strategies/**` | 草稿、不可变版本、diff、归档、恢复与审计 |
| 基金绑定 | `/strategy-assignments/**` | 把已发布版本绑定到基金和回测模式 |
| 日报编排 | `/report-jobs/**`、`/report-backfills/**`、`/daily-report-sets/**` | 日任务、补跑、失败节点重试、暂停/恢复和 revision |
| 信号 | `/signal-previews`、`/trade-signals/**` | 基于真实日报预演、生成和查询四态信号 |
| 策略回测 | `/strategy-backtests/**` | 版本合同预检、运行、重试、取消和结果查询 |
| 治理 | `/strategy-verifications/**`、`/strategies/{id}/versions/{id}/promote` | 自动验证、人工确认和晋级 |
| 策略模拟盘 | `/paper-strategy-bindings`、`/paper-strategy-bindings/{id}/orders`、`/strategy-paper-orders` | Promoted 版本的受控下单与 lineage |
| 通用模拟盘 | `/paper/**` | VeighNa 撮合、账户、组合、订单和撤单 |
| 旧技术诊断 | `/backtests/**` | 兼容性 Hikyuu 单股票技术回测；新前端不再调用 |

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
前端对 `/api/v2/finance` 的 `POST/PATCH/DELETE` 自动发送 `Idempotency-Key`。调用方也可
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
      "source": "akshare_security_master"
    }
  ]
}
```

## 行情与研究数据

### `GET /api/v2/finance/market-data/{symbol}`

`symbol` 可为六位代码或可唯一解析的证券名称。`refresh=true` 跳过已有 Finance Pack 分区缓存并重新请求上游；默认使用各快照自己的 TTL。

接口并行读取以下快照：

| section | Snapshot | 主要字段 |
| --- | --- | --- |
| `market` | `market` | 最新价、涨跌幅、开高低收、量额、PE/PB/PEG/PCF/PS、市值、估值日期 |
| `kline` | `kline` | 起止日期、复权、条数、最后交易日 OHLC/量/额/换手 |
| `fundamental` | `fundamental` | 公司资料、财务摘要、盈利、资产负债、现金流、财务分析、银行指标与同行比较 |
| `capital_flow` | `capital_flow` | 最近资金流与主力资金排名 |
| `sentiment` | `sentiment` | 热度排名、关键词和新闻样本 |
| `notices` | `notice` | 最近 30 天公告窗口与公告记录 |
| `events` | `events` | 回购、增减持、中标、业绩等事件关键词和时间线 |
| `lhb` | `lhb` | 最近上榜日期、买卖席位、买卖额和统计记录 |

```json
{
  "symbol": "600519",
  "name": "贵州茅台",
  "retrieved_at": "2026-07-13T09:00:00+00:00",
  "quality_status": "partial",
  "quality": {
    "market": "available",
    "kline": "available",
    "fundamental": "available",
    "capital_flow": "degraded",
    "sentiment": "available",
    "notice": "available",
    "events": "available",
    "lhb": "data_gap"
  },
  "sources": {
    "market": {"provider_id": "akshare_market", "vendor": "akshare"}
  },
  "sections": {
    "market": {
      "overview": {
        "latest_price": 1420.5,
        "pct_change": 1.15,
        "pe_ttm": 22.8,
        "pb": 7.3,
        "peg": 2.1,
        "pcf": 21.4,
        "ps": 9.8,
        "market_cap": 1780000000000,
        "float_market_cap": 1780000000000,
        "valuation_date": "2026-07-13"
      },
      "history_summary": {
        "bar_count": 20,
        "latest_bar": {"open": 1402, "high": 1435, "low": 1398, "close": 1420.5}
      }
    },
    "kline": {
      "start_date": "20260413",
      "end_date": "20260713",
      "adjust": "",
      "bar_count": 60,
      "latest_bar": {
        "date": "2026-07-13",
        "open": 1402,
        "high": 1435,
        "low": 1398,
        "close": 1420.5,
        "volume": 2500000,
        "amount": 3540000000,
        "turnover": 0.2
      }
    },
    "fundamental": {},
    "capital_flow": {},
    "sentiment": {},
    "notices": {},
    "events": {},
    "lhb": {}
  }
}
```

`quality_status` 为 `available`、`partial` 或具体不可用状态。数据源快照使用
`available`、`no_data`、`provider_unavailable`、`rate_limited`、`parse_failed`；
Pack 质量 Gate 还可能标记 `degraded`、`data_gap` 或 `assumption_only`。数据源失败
不会被静默替换成演示数据，也不会向客户端暴露上游连接异常原文。

每个 Snapshot 同时携带 `source`、`primary_source`、`fallback_sources`、
`field_sources` 与 `source_trace`。来源目录固定对齐 UZI 3.9.2 的 70 个 source ID，
但目录项只是候选来源；客户端必须以本次 `source_trace[].status` 判断实际是否取到数据，
不能把“存在于目录”解释成“数据可用”。Token/Cookie/API Key 只通过运行环境配置，任何
响应都不会返回密钥值。

K 线响应包含区间元数据、`bars` 历史序列与最后交易日记录。前端行情图和
Hikyuu 回测均消费同一真实 `bars` 数据，不由浏览器生成价格。

### `GET /api/v2/finance/quotes/{symbol}`

返回模拟盘采用的服务端报价：

```json
{
  "symbol": "600519",
  "name": "贵州茅台",
  "latest_price": 1420.5,
  "bid_price_1": 1420.0,
  "ask_price_1": 1421.0,
  "pct_change": 1.15,
  "as_of": "2026-07-13",
  "source": {"provider_id": "akshare_market", "vendor": "akshare"},
  "quality_status": "available"
}
```

客户端报价仅供预览。提交订单时后端会重新取价，且不接受客户端成交价。

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
  "mode": "holdings_portfolio",
  "enabled": true,
  "schedule": {"time": "18:00", "timezone": "Asia/Shanghai"}
}
```

`mode` 为 `fund_only` 或 `holdings_portfolio`，且必须在该版本允许的模式内。Assignment 可
暂停、恢复或删除；已有历史信号时删除会转为禁用，不破坏 lineage。

### point-in-time 日报、补跑与 UZI

`POST /report-jobs` 接受单个 `as_of_date`；`POST /report-backfills` 接受
`start_date/end_date`。先用 `/report-backfills/estimate` 估算交易日、基金报告和持仓 UZI
调用量。任务持久化 lease、attempt 和进度，可通过 `/retry` 只重试失败股票，或在
`full_rerun=true, confirmed=true` 时创建新 revision；`/pause` 与 `/resume` 不删除旧 attempt。

每个 `DailyReportSet` 固定 `as_of_date`、`effective_date`、revision、input/source/content
hash、coverage 和 Artifact 引用。基金公开信息必须在回测日期当时可获得；没有可信
`published_at/first_seen_at` 的历史证据输出 `BLOCKED`，不会借用当前数据。配置的 top
5/10/20 每只股票都需要 canonical UZI 子报告；同一股票、日期和 pin 跨基金复用一次，基金
上下文只作为独立节点保存。

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
  "mode": "holdings_portfolio",
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

## 旧单股票 Hikyuu 技术回测（兼容诊断）

这些 `/backtests/**` 端点为旧技术诊断兼容接口，新 `/strategies` 前端不再调用，也不能
用于 Verified 或 Promoted。服务端把 Finance Pack `kline.bars` 转换为 Hikyuu 外部
`Stock/KRecord`，使用 `SYS_Simple + SG_Cross + MM_FixedCount` 执行注册策略。
每次回测在 120 秒受控子进程中运行，隔离 HOME 已预置本地空策略仓库，因此不会
自动下载 Hikyuu Hub，也不会影响 API 主进程。

`GET /api/v2/finance/backtests/engine` 的 `strategies` 返回可用策略及其动态参数合同。
当前包括 `ma_cross`（双均线交叉）和 `price_ma_cross`（价格均线趋势）；前端严格按
该合同生成参数表单。新增内置策略统一在
`research_system/api/backtest_strategies.py` 注册，并由隔离 runner 显式实现，不执行
用户提交的任意 Python 代码。

### `POST /api/v2/finance/backtests`

```json
{
  "symbol": "600519",
  "strategy": "ma_cross",
  "strategy_parameters": {
    "fast_period": 5,
    "slow_period": 20
  },
  "initial_cash": 1000000,
  "trade_size": 1000,
  "refresh": false
}
```

约束由所选策略的参数合同决定；双均线策略要求 `slow_period > fast_period >= 2`。
`trade_size` 为 100 股整数倍，有效 K 线至少为策略最长周期加 5 根。未知策略、未知
参数、越界参数都会以结构化 422 错误拒绝。结果包含：

- 回测 ID、Hikyuu 版本、标的、参数和数据来源；
- 初始/期末权益、总收益、年化收益、最大回撤、胜率和盈亏比；
- 全量账户权益曲线；
- Hikyuu 原生成交记录及费用。

结果默认持久化到：

```text
<workspace_root>/backtests/<backtest_id>.json
```

`GET /api/v2/finance/backtests` 返回摘要列表，支持 `limit=1..100` 与从 0 开始的
`offset`，响应同时包含 `total_count`、`limit`、`offset`；`GET
/api/v2/finance/backtests/{backtest_id}` 返回完整可回放结果。

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
- `uzi_recovery.stage_order`：固定为 `primary_providers → integrity_check → mx_api → ddgs → playwright → integrity_recheck`；
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
