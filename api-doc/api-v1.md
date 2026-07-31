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

There are 42 versioned operations. Proposal, dynamic subagent-discovery,
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

| Method | Path | Canonical source |
| --- | --- | --- |
| `POST` | `/runs` | `AgentOSRunService` → canonical TurnRunner |
| `GET` | `/runs` | canonical Run history |
| `GET` | `/runs/{run_id}` | complete run detail projection |
| `GET` | `/runs/{run_id}/artifacts` | active ArtifactBoard records |
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
