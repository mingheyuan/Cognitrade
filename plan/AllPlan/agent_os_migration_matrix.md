# Cognitrade Agent OS Migration Matrix

This document tracks the migration to the architecture defined in the user-provided Agent OS target. It is an implementation and verification ledger, not a substitute for code or tests.

## Completion Rule

The migration is complete only when every target below has direct runtime and test evidence. Compatibility adapters count as migration scaffolding, not final completion.

| Target | Current evidence | Status | Required completion proof |
| --- | --- | --- | --- |
| One hierarchical DAG protocol | `ExecutionGraph` owns canonical scheduling, hierarchy, persistence, revisioned patching, replay, and rerun. `ActionPlan` and `SubagentPlan` Python DTOs no longer exist in the runtime tree. | Complete | State-machine, graph runner, replay, rerun, and standard-entry tests pass. |
| Compound nodes with executable Subagent leaves | `ExecutionGraph` validates compound/subagent shapes; lazy compound expansion is driven from current Board artifacts. | Complete in Agent OS path | Migrate legacy macro Agent phases into pack-owned compound expansion. |
| Unified ready-leaf concurrency | `ExecutionGraphRunner` schedules all ready leaves across compound boundaries with global and per-capability limits. | Complete | `tests/test_execution_graph_runner.py` proves a parallel legal frontier. |
| Planner owns and can patch the visible graph | Revision guards, safe-point patch queues, cooperative in-flight cancellation, arbitrary replacement, and full patch history are implemented. | Complete | In-flight cancellation/replacement test passes. |
| Artifact Board is the only shared work data plane | Canonical nodes exchange artifact references only. Legacy Blackboard Python implementations are absent from the runtime tree; retained response projections are derived from Board artifacts. | Complete | Artifact Board, run history, and report projection tests pass. |
| Capability-driven planning | Planner connects `consumes`/`produces`, filters tools/permissions/side effects/cost/latency/reliability, and applies pack governance/gates. | Complete | Finance, legal, and runtime-installed audit pack tests pass. |
| DomainPack isolation | Executable finance and legal packs include ontology, schemas, subagents, tools, planner policy, gates, report contracts, and evaluations. Legacy finance orchestration still exists outside the pack. | In progress | Move the remaining finance-specific planner/supervisor/handler code behind the `finance_ashare` pack boundary. |
| Second non-finance pack with zero core changes | `legal_contract` executes end to end; a third unseen audit pack is created and executed at test runtime. | Complete | `test_installing_an_unseen_third_domain_requires_no_core_change`. |
| Fixed governance skeleton | Node/depth/concurrency/retry/token/cost/time limits, tool/permission checks, cache, idempotency, durable control, audited human approval, provenance, schema validation, and blocking gates are runner-enforced. | Complete | Governance, gate, cache, approval, cancellation, replay, and rerun tests pass. |
| Layered memory with promotion gates | Run Board, episodic, domain, and policy stores are distinct; promotions are one-way, evaluated, append-only, and audited. | Complete | `tests/test_layered_memory.py`. |

## Current Audit

- Canonical Agent OS API: `research_system.runtime.agent_os.AgentOS`.
- Canonical runtime: `ExecutionGraphRunner` + `ArtifactBoard` + `DomainPackRegistry`.
- CLI entrypoints: `domain-packs`, `domain-run`, `domain-inspect`, `domain-replay`, `domain-resume`, `domain-rerun`, `domain-pause`, and `domain-cancel`.
- Broad regression is enforced by `.github/workflows/ci.yml` with `python -m unittest discover -s tests -p "test_*.py"`.
- The standard API and CLI research entries call `AgentOS` directly. `ResearchOrchestrator` remains only as a documented administrative/test compatibility facade; it is not the default research execution path.
- Remaining migration work is limited to physically relocating the last finance-specific methodology helpers into `finance_ashare`; this does not create a second production execution path.

## Migration Order

1. Move finance-specific planner, supervisor, gates, handler registry, and methodology services behind the `finance_ashare` pack boundary.
2. Keep compatibility exports isolated under explicit legacy/admin tests and prevent new production call sites.
3. Run the final requirement-by-requirement audit before removing the retained administrative facade.
