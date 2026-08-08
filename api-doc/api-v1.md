# Cognitrade API v1

## Runtime Boundary

External clients use FastAPI. They do not call the runtime directly.

```text
Web / API client
  -> FastAPI /api/v1
    -> AgentOSRunService
      -> AgentOS
        -> DomainPack Registry
        -> Capability Planner
        -> ExecutionGraphRunner
        -> ArtifactBoard
        -> Governance, Approval, and Layered Memory
```

FastAPI owns authentication, validation, CORS, error envelopes, request IDs,
SSE transport, and mutation serialization. AgentOS owns planning, execution,
artifacts, gates, replay, and long-term memory.

There are 50 versioned operations. Proposal, dynamic subagent-discovery,
ActionPlan, Supervisor, Handler, Blackboard, CLI, and unversioned business APIs
are not part of the current runtime.

## Authentication

Every `/api/v1` operation requires a Bearer token:

```http
Authorization: Bearer <COGNITRADE_API_TOKEN>
```

For isolated loopback development only, authentication may be disabled with:

```text
COGNITRADE_API_ALLOW_UNAUTHENTICATED=true
COGNITRADE_API_HOST=127.0.0.1
```

Transport and validation errors use a shared envelope:

```json
{
  "error": {
    "code": "resource_not_found",
    "message": "The requested resource was not found",
    "request_id": "01J..."
  }
}
```

## Research Runs

`POST /api/v1/runs` is the standard Task Intake endpoint. It validates a
`RequestEnvelope`, builds an ExecutionGraph from installed DomainPack
capabilities, executes it through AgentOS, and returns a `ResponseEnvelope`.

```http
POST /api/v1/runs
Content-Type: application/json

{
  "intent": "stock_check",
  "user_query": "Review supplied evidence for 600000.",
  "constraints": {"research_profile": "medium"},
  "slots": {"symbols": ["600000"]}
}
```

The response identifies the canonical protocol and graph state:

```json
{
  "run_id": "01J...",
  "status": "completed",
  "result": {
    "protocol": "agent_os",
    "graph_id": "01J...",
    "graph_revision": 1,
    "artifact_ids": ["01J..."],
    "final_artifact_ids": ["01J..."]
  }
}
```

The installed `finance_ashare` Pack accepts these intents through the same
endpoint: `stock_check`, `stock_compare`, `stock_screen`, `fund_research`,
`portfolio_research`, `sector_research`, `review_thesis`, and
`distill_kol_logic`. When `intent` is omitted, Pack Task Intake resolves it
from the query or returns a structured
clarification instead of silently defaulting to stock analysis.

`stock_check`, `stock_compare`, `stock_screen`, `sector_research`, and
`review_thesis` require an explicit `constraints.research_profile`. The only
accepted values are `lite`, `medium`, and `deep`; there is no server default
and the removed `quick` profile is rejected. Their UZI 3.9.2 execution
contracts are:

| profile | Fetchers | Panel | Institutional methods | DDGS budget | Playwright fallback |
| --- | ---: | ---: | ---: | ---: | --- |
| `lite` | 7 / 20 | 10 | 1 | 0 | off |
| `medium` | 20 / 20 | 51 | 17 | 30 | opt-in, 6 dimensions |
| `deep` | 20 / 20 | 66 Agent role-play seats | 18 | 60 | 10 dimensions after explicit sensitive-source enablement; includes Bull-Bear debate |

`stock_screen` additionally requires an explicit
`constraints.investment_cycle` (`short_term`, `mid_term`, or `long_term`). The
Web UI leaves both research depth and cycle unselected; no `medium` profile or
`mid_term` cycle is inferred by the client, Task Intake, API, or Pack runtime.

All declared intents accept the common boolean constraint `background`. When
`true`, the API reserves the run immediately and executes it asynchronously;
it is a transport/execution option rather than a Finance Pack business filter.

`GET /api/v1/runs` 返回倒序运行历史，支持 `limit=1..100`（默认 20）、从 0
开始的 `offset`，以及可选 `status` 过滤。客户端应逐页读取直到返回数量少于
`limit`，从而完整载入历史运行。

### Fund research

`fund_research` uses the Agent OS fund chain and real public-fund evidence; it
does not add a finance-specific HTTP endpoint.

```http
POST /api/v1/runs
Content-Type: application/json

{
  "intent": "fund_research",
  "user_query": "比较 110011 和 005827，按长期持有评估",
  "constraints": {
    "fund_codes": ["110011", "005827"],
    "fund_category": "混合型",
    "candidate_pool_size": 8,
    "investment_cycle": "long_term"
  }
}
```

Fund constraints:

| Field | Type | Constraint |
| --- | --- | --- |
| `fund_codes` | string array | one to 20 unique six-digit codes |
| `fund_category` | string | `全部`, `股票型`, `混合型`, `债券型`, `指数型`, `ETF`, `LOF`, `QDII`, or `FOF` |
| `candidate_pool_size` | integer | 1–20; the Web UI uses a minimum of 2 for comparison |
| `investment_cycle` | string | `short_term`, `mid_term`, or `long_term` |

`investment_cycle` is required. There is no `mid_term` (or any other) server
default: the Web UI starts with an empty selection and the API returns
`missing_constraint` until the user explicitly chooses a cycle.

The public `cashflow_resilience` requested-method identifier is retained, but
its implementation is Pack-native. It reports observed cash-flow, profit,
revenue, debt and cash fields plus directly derived ratios. Missing fields are
returned as `data_gap`; the service does not supply default margins, leverage,
stress scenarios or a synthetic composite score.

The Web UI exposes category selection and explicit-code analysis as separate
modes. Category mode omits `fund_codes`; explicit-code mode sends
`fund_category: "全部"` and evaluates every supplied code using its observed
fund type, so a disabled or silently ignored category cannot mislead users.

The graph produces `fund_snapshot`, `fund_analysis`, and `research_report`
Artifacts. One explicitly supplied fund uses `analysis_mode: "single_fund"`:
it is assessed independently, does not require a comparable sample, and does
not emit a score or rank. Multiple explicit codes and category selection use
`analysis_mode: "comparison"`; ranking then requires at least two comparable
funds and at least 60% observed scoring-field coverage. Missing NAV, ranking,
fee, or disclosed holdings fields remain `data_gap`; the service does not
insert simulated or neutral values. Holdings evidence is split into asset
allocation, direct equities, direct bonds, target-fund/ETF positions, and
look-through positions. A feeder fund's directly held shares are never labeled
as its complete portfolio. For feeder funds, the service reads the latest
official periodic report to resolve the target fund code, name, fair value, NAV
weight, report date, and source URL. It then loads the target ETF's latest
disclosed top-ten securities and reports both the ETF-level weight and the
feeder fund's effective exposure (target-fund weight multiplied by constituent
weight). The target position is split out of the structure provider's `other`
bucket; any residual stays labeled as other assets. If either disclosure layer
is unavailable, its coverage field remains `unavailable`; the service never
infers an ETF identity or constituent weight from the fund name. Look-through
rows are explicitly a top-ten periodic disclosure, not a complete real-time
portfolio. Fund methodology v4 also records the declared performance
benchmark, fund-versus-benchmark period returns, manager tenure segments, and
single-fund scale/share history. When a complete trailing-three-year return is
unavailable, the long-term annualized-return component uses the real
since-inception return and complete observed NAV interval. The 1-year benchmark excess return is an
optional scoring component; when it is unavailable, its weight reduces
coverage rather than becoming zero. Manager tenure and scale changes remain
descriptive evidence and do not receive an automatic positive/negative score.
Provider errors contain only the failed stage and error class; raw upstream
exception text is not exposed in Artifacts or the Web UI.

### Fund top-holding stock report batches

After a `fund_research` run has produced an active `fund_snapshot`, the Web UI
can explicitly fan out one selected equity fund's disclosed holdings into up
to ten independent A-share `stock_check` runs. This is a managed parent batch,
not a second fund-data collection:

- normal equity funds use the frozen `top_holdings` rows;
- ETFs use their own disclosed equity holdings;
- ETF feeder funds use only the frozen `lookthrough_holdings` rows and their
  effective feeder-fund weights;
- unsupported markets remain visible as `unsupported` and are not converted;
- if an ETF target or look-through disclosure is absent, the batch reports the
  gap and never guesses the holding;
- every child report records fund code/name, disclosure date, holding rank,
  effective fund weight, source Artifact and parent batch, without changing the
  stock valuation or score.

The batch has a dedicated worker pool controlled by
`COGNITRADE_FUND_HOLDING_REPORT_CONCURRENCY` (default 5, allowed 1–10). It does
not consume or alter the global Agent OS child-thread limit. Child runs are
hidden from the top-level run history and appear under the parent batch. Each
child is automatically attempted up to three times; manual retry creates a new
child run and retains every previous run ID for audit.

```http
POST /api/v1/runs/{source_run_id}/holding-reports
Content-Type: application/json

{
  "fund_code": "110011",
  "profile": "lite",
  "source_artifact_id": "01J...",
  "confirm_stale": false,
  "force": false
}
```

`profile` is required and must be `lite`, `medium`, or `deep`; the service does
not choose a default. All children in one batch use the same explicitly selected profile. An equivalent existing batch
is returned unless `force: true` explicitly creates another historical batch.
Disclosures 121–180 days old carry a warning. Disclosures older than 180 days
return `409 stale_holdings_confirmation_required` until `confirm_stale: true`
is supplied. A missing disclosure date does not block execution, but forces a
documented partial result.

| Method | Path | Meaning |
| --- | --- | --- |
| `POST` | `/runs/{source_run_id}/holding-reports` | Create or reuse a parent batch from a frozen `fund_snapshot` |
| `GET` | `/runs/{source_run_id}/holding-report-batches?fund_code=110011` | List preserved batches for the source run, optionally filtered by fund |
| `GET` | `/holding-report-batches/{batch_id}` | Read parent progress, child statuses, gaps and report availability |
| `POST` | `/holding-report-batches/{batch_id}/cancel` | Best-effort cancel queued/running children; completed reports are retained |
| `POST` | `/holding-report-batches/{batch_id}/retry` | Retry failed children; optional body `{"symbols":["600519"]}` |
| `POST` | `/holding-report-batches/{batch_id}/continue` | Continue cancelled/failed children; optional symbol filter |
| `GET` | `/holding-report-batches/{batch_id}/reports.zip` | Download child PDFs that passed the UZI critical gate plus an audit README |

Parent status is `completed` only when every supported child completes without
batch-level gaps. A mix of completed, gated, failed, cancelled, or unsupported
items is `completed_partial`; zero successful execution results with all
supported items failed is `failed`. A child is deliverable only when its active
`uzi_native_analysis` has `critical_count=0` and the bound `research_report`
records `report_allowed=true`. No run status or auxiliary artifact can bypass
this gate. The ZIP deliberately
contains individual stock reports only; it does not synthesize a fund-level
holdings HTML/PDF report.

### UZI Tier-1 and portfolio research

Stock intents accept Pack-declared `requested_methods`. UZI-specific values are:

- Tier-1: `ai_readiness`, `earnings_preview`, `model_update`;
- modeling: `three_statement`, `quick_lbo`, `accretion_dilution`, `segmental`;
- workflow: `initiation_report`, `earnings_review`, `catalyst_calendar`,
  `thesis_tracker`, `morning_note`;
- decision support: `ic_memo`, `porter_bcg`, `dd_checklist`,
  `unit_economics`, `value_creation_plan`.

Each value selects an independent Pack capability and Artifact rather than the
old aggregate UZI runtime. `model_update` requires a non-empty `model_updates`
object. `three_statement`, `quick_lbo`, `accretion_dilution`, and
`value_creation_plan` require a corresponding `method_assumptions` entry for
calculation; omitted values produce `data_gap` and are never default-filled.
The assumption object is an exact contract: unknown or missing fields are
rejected. Growth rates use decimal ratios (`0.08` means 8%); LBO debt paydown
must be between `0` and `1`, holding years must be a positive integer, and
entry/exit multiples must be positive. Three-statement growth values must be
greater than `-1`, while capex/revenue must be non-negative. Invalid inputs are
rejected at API validation and are never coerced into a plausible scenario.

```json
{
  "intent": "stock_check",
  "user_query": "为 300750 建立快速 LBO",
  "constraints": {
    "symbol": "300750",
    "requested_methods": ["quick_lbo"],
    "method_assumptions": {
      "quick_lbo": {
        "entry_multiple": 8,
        "debt_multiple": 4,
        "exit_multiple": 8,
        "annual_ebitda_growth": 0.08,
        "annual_debt_paydown_pct": 0.10,
        "hold_years": 5
      }
    }
  }
}
```

`portfolio_research` produces `returns_attribution` and `holding_rebalance`
Artifacts from explicit holdings. When `requested_methods` contains
`portfolio_rebalance`, every holding must provide `asset_class` and
`asset_class_targets` must exactly cover those classes; the additional output
is `portfolio_rebalance`.

```http
POST /api/v1/runs
Content-Type: application/json

{
  "intent": "portfolio_research",
  "user_query": "对持仓做收益归因和再平衡",
  "constraints": {
    "portfolio_holdings": [
      {"ticker": "600519", "weight": 60, "return_pct": 8, "value": 600000, "market": "A", "price": 1500},
      {"ticker": "300750", "weight": 40, "return_pct": -2, "value": 400000, "market": "A", "price": 200}
    ],
    "benchmark_return": 3,
    "rebalance_targets": {"600519": 50, "300750": 50},
    "drift_threshold": 5,
    "fee_model": {
      "A": {"commission_rate": 0.00025, "buy_stamp_rate": 0, "sell_stamp_rate": 0.0005}
    }
  }
}
```

`weight` values are normalized but must be explicit and have a positive total.
If a holding lacks `return_pct`, its contribution remains null, observed-weight
coverage is reduced, and no complete portfolio return or excess return is
reported. `rebalance_targets` must cover every holding when supplied. Without
targets, equal weight is an explicit `assumption_only` policy. Without a
request-supplied `fee_model`, the service does not estimate transaction cost.
Strategic asset-allocation targets never fall back to a balanced or equal-weight
portfolio: missing classifications or targets produce `data_gap`.

### UZI native report export

Completed UZI single-stock runs expose the exact vendored UZI 3.9.2 report
derived only from immutable `uzi_native_analysis` ArtifactBoard records. HTML
and PDF share that same visible DOM/CSS/JavaScript. Cognitrade metadata remains
in API headers and Artifact sidecars. The renderer never invokes the old UZI
CLI, cache, report server, or an upstream checkout.

```http
GET /api/v1/runs/{run_id}/report?format=html
Accept: text/html
Authorization: Bearer <COGNITRADE_API_TOKEN>
```

```http
GET /api/v1/runs/{run_id}/report?format=pdf
Accept: application/pdf
Authorization: Bearer <COGNITRADE_API_TOKEN>
```

`format` is exactly `html` or `pdf`. HTML is self-contained and escapes every
Artifact value before insertion. PDF is rendered from that same HTML by the
deployment's bundled Chromium, so HTML and PDF cannot silently diverge. A run
without an active UZI `research_report` returns `409 uzi_report_unavailable`;
missing Chromium returns `503 uzi_pdf_renderer_unavailable`. Neither failure
falls back to a simulated report.

### Sensitive authenticated source policy

Authenticated sources are disabled by default. The Xueqiu contest/cube source
becomes eligible only when all three runtime settings are configured:

```dotenv
COGNITRADE_SENSITIVE_SOURCES_ENABLED=true
COGNITRADE_SENSITIVE_SOURCE_ALLOWLIST=xueqiu
COGNITRADE_XUEQIU_COOKIE=<runtime secret>
```

The Cookie is read only from the environment and must not be submitted in an
API request or committed to the repository. If any gate is missing, Xueqiu is
recorded as a sanitized source gap while public Taoguba/THS/DPSWang evidence may
still make the `contests` snapshot available. No mock standings or holdings are
generated.

| Method | Path | Canonical source |
| --- | --- | --- |
| `POST` | `/runs` | `AgentOSRunService` → canonical TurnRunner |
| `GET` | `/runs` | canonical Run history |
| `GET` | `/runs/{run_id}` | complete run detail projection |
| `GET` | `/runs/{run_id}/artifacts` | active ArtifactBoard records |
| `GET` | `/runs/{run_id}/report?format=html|pdf` | Pack-owned UZI HTML/PDF report derived from active ArtifactBoard records |
| `GET` | `/runs/{run_id}/replay` | snapshot, artifacts, and events |
| `GET` | `/runs/{run_id}/events` | `execution_events.jsonl` |
| `GET` | `/runs/{run_id}/events/stream` | canonical SSE stream |
| `GET` | `/runs/{run_id}/trace` | graph trace and ArtifactBoard |
| `GET` | `/runs/{run_id}/steps` | ExecutionGraph nodes |
| `GET` | `/runs/{run_id}/metrics` | node, gate, usage, and event metrics |
| `GET` | `/runs/{run_id}/threads/{thread_id}` | graph-scoped thread view |
| `GET` | `/runs/{run_id}/threads/{thread_id}/replay` | graph-scoped replay |
| `GET` | `/runs/{run_id}/threads/{thread_id}/metrics` | graph-scoped metrics |
| `GET` | `/runs/{run_id}/subagents/{invocation_id}` | leaf node invocation |
| `GET` | `/runs/{run_id}/recovery` | resumability state |
| `POST` | `/runs/{run_id}/cancel` | canonical cancellation request |
| `POST` | `/runs/{run_id}/reruns` | rerun from a graph node |

Run mutations are serialized at the HTTP boundary. Reads and cancellation do
not wait for an unrelated run request.

## Domain Dashboards

`GET /api/v1/dashboard/{domain_id}/{view_id}` reads a view declaration from the
installed DomainPack, then projects active ArtifactBoard records and optionally
verified Layered Memory records. The core API has no finance-specific dashboard
logic: artifact type, content path, ordering, and limits are all declared by
the pack.

Each returned artifact item contains `data` plus immutable provenance including
the run ID, artifact ID and version, producer, confidence, risk flags, and
source references. An empty item list means no canonical Agent OS result has
yet been produced; it is not replaced with demo data.

The installed `finance_ashare` pack currently declares `overview`, `screening`,
and `methodology` views. The Methodology view exposes the Pack's controlled
screening contract and verified policy-memory records, never unverified
performance or invented methodology claims.

## Server-Sent Events

`GET /api/v1/runs/{run_id}/events/stream` streams records from
`execution_events.jsonl`. Terminal state comes from
`execution_snapshot.json`. Canonical event names include
`graph_run_started`, `graph_frontier_ready`, `graph_node_completed`,
`graph_patch_applied`, and `graph_run_finished`.

Use `Last-Event-ID` to resume after a received `event_id`. An unknown cursor
returns `409 event_cursor_not_found`. Terminal statuses are `completed`,
`completed_partial`, `cancelled`, `failed`, `timed_out`, and `gated`.

Native browser `EventSource` cannot attach a Bearer header. The frontend uses
authenticated fetch-SSE for this endpoint.

## Chat

Chat is a FastAPI-managed thread store, not an execution runtime. Research
submitted from a thread is converted into the standard AgentOS Task Intake:

| Method | Path |
| --- | --- |
| `POST` | `/chat/threads` |
| `GET` | `/chat/threads` |
| `GET` | `/chat/threads/{thread_id}` |
| `GET` | `/chat/threads/{thread_id}/preview` |
| `PATCH` | `/chat/threads/{thread_id}` |
| `POST` | `/chat/threads/{thread_id}/forks` |
| `POST` | `/chat/threads/{thread_id}/archive` |
| `POST` | `/chat/threads/{thread_id}/unarchive` |
| `POST` | `/chat/threads/{thread_id}/compact` |
| `POST` | `/chat/threads/{thread_id}/runs` |

The thread-run endpoint records the query and returned run ID, then calls the
same AgentOS run service as `POST /runs`.

## Administration

Administrative resources expose canonical AgentOS state only:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/admin/domain-packs` | installed DomainPack summaries |
| `GET` | `/admin/domain-packs/{domain_id}` | full manifest and installed path |
| `GET` | `/admin/runs/{run_id}/approvals` | immutable approval audit records |
| `POST` | `/admin/runs/{run_id}/approvals` | approve a declared high-risk leaf node |
| `POST` | `/admin/runs/{run_id}/approvals/{approval_id}/reject` | reject a pending approval |
| `GET` | `/admin/memory` | episodic, domain, or policy memory |
| `POST` | `/admin/memory/expire` | expire due memory records |
| `POST` | `/admin/memory/{memory_id}/promote` | promote a reviewed memory record |
| `POST` | `/admin/memory/{memory_id}/revoke` | revoke a memory record |
| `GET` | `/dashboard/{domain_id}/{view_id}` | DomainPack-configured ArtifactBoard and memory projection |
| `POST` | `/admin/artifacts/cleanup` | run artifact retention cleanup |
| `POST` | `/admin/qdrant/reindex` | rebuild from ArtifactBoard records |
| `POST` | `/admin/system/checks` | repository checks |

An approval request contains `node_id`, `capability`, `approval_id`, and
`reason`. FastAPI derives the approver from the authenticated principal. The
request is accepted only when all fields match a declared high-risk
ExecutionGraph leaf node, so the Admin API cannot bypass graph governance.

`GET /admin/memory` accepts `layer=episodic|domain|policy`, `domain_id`,
`status`, and `limit`.

## System

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/system/status` | operational summary |
| `POST` | `/system/doctor` | diagnostics |

## Version Boundary

The frontend uses `/api/v1` for Agent OS research and the separately documented
`/api/v2/finance` surface for Finance Workspace data and paper trading.
Unversioned `/api/*` business routes do not exist.

## Frontend Configuration

```text
VITE_COGNITRADE_API_BASE_URL=http://127.0.0.1:8000/api/v1
VITE_COGNITRADE_API_TOKEN=<COGNITRADE_API_TOKEN>
```

The Vite frontend submits Task Intake requests through FastAPI, uses fetch-SSE
for graph events, lists `/runs`, and opens replay data for ExecutionGraph and
ArtifactBoard inspection. Finance Workspace operations are documented in
[`api-v2.md`](./api-v2.md).
