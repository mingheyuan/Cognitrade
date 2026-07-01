# Codex CLI Python Mapping

## Objective

Learn the `openai/codex` CLI UI and multi-thread subagent interaction model, but keep the current Python stack and existing Cognitrade architecture instead of introducing Rust.

This file is not a rewrite plan. It is a source-to-target mapping from the current `openai/codex` implementation shape to the existing Python runtime, CLI, memory, and subagent system in this repo.

## Primary Sources Inspected

- `openai/codex` README
- `sdk/python/README.md`
- `sdk/python/examples/06_thread_lifecycle_and_controls/sync.py`
- `sdk/typescript/src/thread.ts`
- `sdk/typescript/src/events.ts`
- `codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs`
- `codex-rs/core/src/tools/handlers/multi_agents_v2/wait.rs`
- `codex-rs/core/src/session/multi_agents.rs`
- `codex-rs/tui/src/tui.rs`

## What Codex Actually Does

### 1. Thread is the top-level conversational/runtime object

Codex treats a `thread` as the durable unit for:

- turn history
- lifecycle operations
- fork/resume/archive/unarchive
- streaming event continuity

Observed source shape:

- Python SDK: `thread_start()`, `thread_resume()`, `thread_fork()`, `thread_archive()`, `thread_unarchive()`
- TypeScript SDK: `Thread.runStreamed(...)`, `Thread.run(...)`

### 2. The UI is event-driven, not response-driven

Codex TUI is built around streaming events such as:

- `thread.started`
- `turn.started`
- `item.started`
- `item.completed`
- `turn.completed`
- `turn.failed`

The practical pattern is:

- render incremental state
- update status in place
- treat agent/tool/subagent work as first-class timeline items

### 3. Multi-agent is explicit and thread-based

Codex multi-agent v2 uses explicit primitives:

- `spawn_agent`
- `wait_agent`
- agent-thread lineage
- fork modes such as `all`, `none`, or last `N` turns

The important behavioral point is not the exact Rust implementation. It is that:

- subagents are spawned as named child work units
- parent and child have visible relationship
- the parent can wait, continue, or be interrupted

### 4. UI control surface is broader than “ask a question”

Codex exposes first-class controls around:

- thread lifecycle
- trace visibility
- approvals / execution context
- event stream inspection
- spawned-agent progress

That is the part worth copying into Python.

## Current Python Repo Mapping

### Thread model

Current Python equivalents already exist:

- session thread identity:
  - `research_system/cli/chat_thread_store.py`
  - `research_system/cli/chat_memory.py`
- thread lifecycle in chat:
  - `/new`
  - `/thread`
  - `/threads`
  - `/compact`
- checkpoint/compact persistence:
  - `research_system/runtime/checkpoint_store.py`
  - `research_system/cli/chat_session_bootstrap.py`

Assessment:

- `thread persistence`: already present
- `thread controls`: already present
- `thread fork`: now present as a Python-native chat/thread capability
- `thread archive/unarchive`: now present as a Python-native chat/thread capability

### Event-driven CLI/TUI

Current Python equivalents:

- event persistence:
  - `research_system/runtime/event_stream.py`
- event presentation:
  - `research_system/runtime/chat_presenter.py`
- TUI shell/view:
  - `research_system/cli/chat_shell.py`
  - `research_system/cli/chat_view.py`
  - `research_system/cli/chat_scene.py`

Assessment:

- phase/subagent/gate events already stream
- presenter already compresses event output into status/event lines
- this is structurally aligned with Codex
- the remaining gap is richer lifecycle inspection and more first-class trace browsing

### Multi-agent / subagent runtime

Current Python equivalents:

- subagent contract:
  - `research_system/subagents/base.py`
  - `research_system/subagents/registry.py`
- orchestration and dynamic insertion:
  - `research_system/runtime/orchestrator.py`
  - `research_system/runtime/supervisor.py`
- trace persistence:
  - `debug.subagent_trace`
  - `debug.dynamic_trace`

Assessment:

- this repo already has subagents
- this repo already has dynamic action insertion
- this repo already emits `subagent_started/completed/failed`
- what was missing compared with Codex was a first-class CLI control surface for inspecting that lifecycle

### Wait model

Codex concept:

- parent can `wait_agent(...)`
- waiting is explicit in the model and in the UI

Current Python equivalent:

- `waiting_external` run state
- `RunControlPlane.set_wait_deadline(...)`
- `_wait_for_external_dependency(...)`

Assessment:

- there is already a wait state
- but it was not exposed as a first-class inspection surface in CLI output

## This Turn’s Concrete Progress

The following Python-native Codex-style control surface has now been added:

### `trace` run command

New behavior:

- CLI:
  - `research-system trace <run_id>`
- chat:
  - `/trace <run_id|last>`

It exposes:

- `subagent_trace`
- `dynamic_trace`
- `wait_trace`

Relevant files:

- `research_system/runtime/session_history.py`
- `research_system/runtime/orchestrator.py`
- `research_system/cli/cli_command_payloads.py`
- `research_system/cli/cli_command_dispatch.py`
- `research_system/cli/chat_commands.py`
- `research_system/cli/chat_command_catalog.py`
- `research_system/cli/app.py`
- `research_system/services/run_payloads.py`
- `research_system/cli/payload_rendering.py`

### Why this matters

This is the closest current Python analogue to Codex’s “thread-local agent lifecycle visibility”:

- spawned/used subagent work is inspectable after the run
- dynamic supervisor insertions are visible
- external waiting state is visible

It does not require Rust, and it does not replace the current architecture.

### Python-native thread fork and lineage

New behavior:

- chat:
  - `/fork`
- thread metadata now records:
  - `thread_source`
  - `parent_thread_id`
  - `fork_turn_count`

Current behavior:

- the active thread can be forked into a child thread
- turns are copied into the child thread
- compact summary/checkpoint are copied when available
- current session switches into the child thread immediately
- `/thread` and `/threads` now expose lineage metadata

Relevant files:

- `research_system/cli/chat_thread_store.py`
- `research_system/cli/chat_memory.py`
- `research_system/cli/chat_actions.py`
- `research_system/cli/chat_commands.py`
- `research_system/cli/chat_command_catalog.py`
- `research_system/cli/chat_state.py`
- `research_system/cli/chat_types.py`
- `research_system/cli/chat_updates.py`

### Python-native thread archive and unarchive

New behavior:

- chat:
  - `/thread-archive [thread_id]`
  - `/thread-unarchive <thread_id>`
  - `/threads [limit] [active|all|archived]`

Current behavior:

- active threads can be archived out of the default thread list
- archived threads remain resumable and visible through explicit archived/all scopes
- archiving the active thread persists a checkpoint and immediately switches the session onto a fresh active thread
- unarchiving restores the thread to the default active thread scope
- `/thread` now exposes archived status and archive timestamp

Relevant files:

- `research_system/cli/chat_thread_store.py`
- `research_system/cli/chat_memory.py`
- `research_system/cli/chat_actions.py`
- `research_system/cli/chat_commands.py`
- `research_system/cli/chat_command_catalog.py`

### Live trace panel in TUI

New behavior:

- TUI:
  - `F2` toggles `transcript <-> trace`
  - `F3` cycles trace task focus
  - `F4` clears trace task focus back to the full task list
- incoming runtime events now update the in-memory trace payload during the run

Current behavior:

- `subagent_started/completed/failed/subagent_trace`
- `dynamic_action_inserted`
- `waiting_external` lifecycle

all accumulate into the current session trace payload while the run is still active.

That makes the Python TUI more Codex-like in practice:

- transcript remains the conversation surface
- trace acts as a live task/agent inspection surface
- `F2` can switch to trace during the run without needing a post-run CLI fetch when live trace cache already exists
- trace rendering now groups runtime activity into `phase / agent` task cards before the raw trace sections
- runtime events now carry `action_id / node_id / objective`, so trace cards can anchor to real task lineage instead of only coarse phase grouping
- trace cards now render explicit task sections for `spawned_subagents`, `waits`, and `dynamic_inserts`, which is materially closer to Codex’s parent/child task feel
- large traces can now be narrowed to a single task card in-place via lightweight focus controls, without replacing the current prompt_toolkit scene model

Relevant files:

- `research_system/runtime/chat_presenter.py`
- `research_system/cli/chat_event_adapter.py`
- `research_system/cli/chat_keybindings.py`
- `research_system/cli/chat_scene.py`
- `research_system/cli/chat_rendering.py`

## Verified Evidence

Targeted regression executed after this change:

- `python -m unittest tests.test_chat_cli tests.test_chat_actions tests.test_cli_command_payloads tests.test_cli_command_dispatch tests.test_run_payloads tests.test_cli_output`
  - `111 tests OK`
- `python -m compileall research_system tests`
  - passed

Additional targeted regression for thread fork/lineage:

- `python -m unittest tests.test_chat_cli tests.test_chat_actions tests.test_chat_memory tests.test_chat_thread_checkpoint tests.test_chat_command_catalog`
  - `62 tests OK`

Additional targeted regression for thread archive/unarchive lifecycle:

- `python -m unittest tests.test_chat_cli tests.test_chat_memory tests.test_chat_actions tests.test_chat_command_catalog tests.test_chat_thread_checkpoint`
  - `69 tests OK`

Additional targeted regression for live trace panel accumulation:

- `python -m unittest tests.test_chat_presenter tests.test_chat_event_adapter tests.test_chat_keybindings tests.test_cli_output tests.test_chat_interactions`
  - `90 tests OK`

Additional targeted regression for grouped trace task-card rendering:

- `python -m unittest tests.test_cli_output tests.test_chat_presenter tests.test_chat_event_adapter tests.test_chat_keybindings`
  - `83 tests OK`

Additional targeted regression for action-level lineage propagation:

- `python -m unittest tests.test_chat_presenter tests.test_chat_event_adapter tests.test_session_history tests.test_cli_output`
  - `78 tests OK`

Additional targeted regression for explicit parent/child trace card sections:

- `python -m unittest tests.test_cli_output tests.test_chat_presenter tests.test_chat_event_adapter tests.test_session_history`
  - `78 tests OK`

## Remaining Gaps To Reach a More Codex-like Python UX

### 1. Thread lifecycle is still weaker than Codex

Missing or incomplete:

- explicit thread resume by thread id outside the current chat checkpoint semantics
- richer lineage and archive metadata presentation inside the TUI itself

Recommended Python path:

- extend `chat_thread_store` and thread metadata persistence
- add richer thread lifecycle actions into the shell/TUI scene
- keep run history and thread history separate

### 2. Trace is inspectable after a run and now navigable as a live pane, but its presentation is still thinner than Codex

Current state:

- live stream exists
- post-run trace query now exists
- TUI transcript/trace toggle now exists
- live trace cache now updates from runtime events during the run

Missing:

- stronger live grouping of parent/child task lineage inside the trace panel
- richer parent/child lineage cards beyond the current `phase / agent` grouping
- explicit child-agent nesting beyond the current `action_id/node_id` anchored grouping
- selective collapse/expand controls for large task trees beyond the current single-task focus mode

Recommended Python path:

- keep prompt_toolkit
- keep extending the current scene/view model
- do not rebuild a Ratatui clone; keep the existing scene/view model
- evolve action-anchored trace cards into clearer parent/child runtime cards
- add lightweight collapse controls before attempting a full TUI tree widget

### 3. Subagent invocation is visible, but not yet controllable as a separate runtime primitive

Codex model:

- explicit `spawn_agent`
- explicit `wait_agent`

Current Python state:

- subagent invocation is internal to services
- dynamic actions are supervisor/orchestrator managed

Recommended Python path:

- do not expose arbitrary subagent spawning first
- instead add safe high-level controls:
  - `/trace`
  - `/events`
  - `/resume`
  - future `/run-plan` or `/run-steps`

Then, only if needed:

- add an internal `spawned_task` record model
- map selected dynamic actions to visible child-task cards in TUI

### 4. Parent/child runtime lineage is still weaker than Codex

Codex keeps stronger child-agent lineage metadata and more explicit child task grouping in the UI.

Recommended Python path:

- extend thread metadata with:
  - `parent_thread_id`
  - `thread_source`
  - `spawn_reason`
- keep the thread metadata path already added
- next add visible parent/child task grouping inside the live trace panel

## Recommended Next Steps

### Highest value next

1. Add thread lineage metadata and thread fork support.
2. Add a trace/transcript toggle in TUI.
3. Add richer run trace sections:
   - mailbox activity
   - gate timeline
   - final agent path summary

### Explicit non-goals for now

- do not port Rust TUI
- do not rebuild `openai/codex` protocol literally
- do not replace the current orchestrator with a new thread runtime

## Bottom Line

The right way to “learn Codex but keep Python” is not to imitate Rust implementation details.

The reusable ideas are:

- thread-first lifecycle
- event-first UI
- first-class subagent visibility
- explicit wait/control semantics

This repo already had much of the architectural substrate.

The new `trace` control surface is the first direct step that makes the current Python CLI behave more like Codex in practice, without abandoning the existing framework.
