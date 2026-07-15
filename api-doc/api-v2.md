# Cognitrade API v2

## 版本范围

API v2 新增 Finance Workspace 与模拟交易接口。Agent OS 研究运行、Chat、Dashboard、Admin 与 System 接口仍保留在 `/api/v1`；v2 不复制或修改这些契约。

```text
Frontend
  -> /api/v2/finance
    -> FinanceMarketDataService
      -> finance_ashare DomainPack snapshots
      -> AkShare / CNInfo providers
    -> PaperTradingService
      -> server-side quote
      -> SQLite paper ledger
```

v2 当前包含 8 个操作：

| Method | Path | 用途 |
| --- | --- | --- |
| `GET` | `/api/v2/finance/securities` | 按代码或名称解析 A 股证券 |
| `GET` | `/api/v2/finance/market-data/{symbol}` | 聚合八类 Finance Pack 快照 |
| `GET` | `/api/v2/finance/quotes/{symbol}` | 获取服务端可成交行情 |
| `GET` | `/api/v2/finance/paper/account` | 获取或创建当前模拟账户 |
| `POST` | `/api/v2/finance/paper/account/reset` | 归档并重置模拟账户 |
| `GET` | `/api/v2/finance/paper/portfolio` | 获取现金、权益与持仓估值 |
| `GET` | `/api/v2/finance/paper/orders` | 获取模拟成交记录 |
| `POST` | `/api/v2/finance/paper/orders` | 提交并立即撮合模拟委托 |

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

`quality_status` 为 `available`、`partial` 或具体不可用状态。每个快照统一使用 `available`、`no_data`、`provider_unavailable`、`rate_limited`、`parse_failed`；兼容历史 Artifact 时仍可能读取到 `degraded`、`data_gap` 或 `assumption_only`。数据源失败不会被静默替换成演示数据，也不会向客户端暴露上游连接异常原文。

K 线响应只有区间元数据与最后交易日记录；前端契约禁止用该接口绘制历史价格趋势。

### `GET /api/v2/finance/quotes/{symbol}`

返回模拟盘采用的服务端报价：

```json
{
  "symbol": "600519",
  "name": "贵州茅台",
  "latest_price": 1420.5,
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

- 立即按服务端 Finance Pack 报价成交，不提供挂单或撤单。
- 买入价格增加 5 bps 模拟滑点；卖出价格减少 5 bps。
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
    "market_value": 142050.0,
    "equity": 999892.02,
    "cost_basis": 142157.98,
    "total_pnl": -107.98,
    "total_return_pct": -0.0108,
    "position_count": 1
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

`limit` 范围为 1–200，默认 50。返回当前活动账户的倒序成交记录：

```json
{"orders": []}
```

## 前端配置

研究 API 地址仍配置为：

```text
VITE_COGNITRADE_API_BASE_URL=http://127.0.0.1:8000/api/v1
```

前端 API Client 对 Finance Workspace 请求自动将同一 host 的 `/api/v1` 替换为 `/api/v2`。Bearer token 与 v1 共用。
