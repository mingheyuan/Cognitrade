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
  - `F5` toggles raw trace sections on top of the task-card view
- incoming runtime events now update the in-memory trace payload during the run

Current behavior:

- `subagent_started/completed/failed/subagent_trace`
- `dynamic_action_inserted`
- `waiting_external` lifecycle

all accumulate into the current session trace payload while the run is still active.

That makes the Python TUI more Codex-like in practice:

- transcript remains the conversation surface
- transcript is no longer just one flat mixed stream: active turn-scoped slots are now rendered as a distinct `live_tail`, which is much closer to Codex's "committed transcript + in-flight active cell/live tail" split
- the transcript footer/hint behavior is now closer to Codex's instructional-vs-contextual bottom pane split: idle transcript mode surfaces ambient thread context (`thread/topic/next-step`) instead of only repeating control hints, while running/clarification/trace/events/threads states still switch back to explicit instructional guidance
- idle transcript mode now also surfaces a lightweight active-context label (`intent / phase / run status`) alongside thread context, which is the local Python analogue of Codex's passive footer context plus active agent label
- trace acts as a live task/agent inspection surface
- `F2` can switch to trace during the run without needing a post-run CLI fetch when live trace cache already exists
- trace rendering now groups runtime activity into `phase / agent` task cards before the raw trace sections
- runtime events now carry `action_id / node_id / objective`, so trace cards can anchor to real task lineage instead of only coarse phase grouping
- trace cards now render explicit task sections for `spawned_subagents`, `waits`, and `dynamic_inserts`, which is materially closer to Codex’s parent/child task feel
- large traces can now be narrowed to a single task card in-place via lightweight focus controls, without replacing the current prompt_toolkit scene model
- trace rows now carry clearer child-task labels such as `task_name` and `agent_path`, so spawned work units are not shown only as a bare subagent type
- raw `subagent_trace / dynamic_trace / wait_trace` sections can now be hidden when the task cards are enough, which keeps the UI closer to a task-first inspection model
- trace task cards now group spawned child work into nested child-agent summaries with per-child status counts and objective text, so parent/child task structure is more directly visible in the TUI
- the TUI now has a dedicated `threads` panel, reachable from trace via `F6`, that exposes current-thread metadata, fork/archive lineage, and scoped thread lists
- the threads panel now also renders a lightweight lineage tree from `parent_thread_id` relationships, so forked threads are visible as parent/child structure instead of only flat list rows
- the threads panel can cycle `active / all / archived` scopes in-place via `F7`, which moves the shell a step closer to Codex’s thread-first browsing model instead of relying only on slash commands
- the threads panel now supports lightweight thread focus and direct thread switching in-place via `F8 / F9`, so thread browsing is no longer purely read-only
- the threads panel now also supports in-panel lifecycle actions on the focused thread via `F10 / F11`, covering `fork` and `archive/unarchive` without dropping back to slash commands
- the threads panel now supports explicit lineage-tree navigation via `Ctrl+F8`, so focus can follow the visible parent/child tree instead of only the flat scoped list
- the focused thread in the threads panel now also exposes a lightweight content preview, including compact summary text, recent turns, and checkpoint presence, so thread browsing is no longer limited to metadata-only inspection
- the focused thread in the threads panel now also renders an explicit `parent / current / children` family block, so lineage inspection is no longer limited to a flat metadata card plus a separate global tree
- the focused-thread family block now also renders explicit `ancestry` and recursive `descendants`, so thread lineage inspection is no longer limited to one-hop parent/child metadata
- focused-thread selection is now explicitly treated as `resume` rather than only `switch`: `/thread <id>` and TUI `F9` restore saved session state from checkpoint/summary-backed thread records and surface whether the resumed thread had checkpoint and compact-summary state available
- active chat threads can now also be explicitly renamed via `/rename <name>`, and that user-defined thread name is surfaced through `/thread`, `/threads`, the threads panel, and focused-thread preview instead of relying only on auto-derived topic text
- chat threads can now also be explicitly inspected without changing the active session via `/thread-read <thread_id>`, which exposes metadata, compact summary, checkpoint presence, and recent turns as a read-only thread browser primitive instead of overloading resume
- trace task cards now support a summary-vs-expanded child view toggle via `F12`, so spawned child work can collapse back to a task-first summary instead of always printing nested detail rows
- trace task cards now also support persistent per-task collapse via `Ctrl+F12`, so one focused task can stay folded while the rest of the trace view remains expanded
- trace task cards now also support per-child focus inside the focused task via `Ctrl+F3 / Ctrl+F4`, which is a closer analogue to drilling into one spawned work unit instead of only toggling the whole child layer
- trace task cards now render spawned child work as a lightweight lineage tree derived from `agent_path`, so child agents are no longer shown only as flat summaries inside the parent task
- trace task cards now also support branch-level child-tree focus via `Ctrl+F9 / Ctrl+F10`, so inspection can follow a visible lineage branch instead of only the flat child list
- trace branch drill-down now follows a unified `next drill target` model instead of only jumping between branch labels: leaf branches first fall into direct work units, and repeated `Ctrl+F9` continues across concrete calls before leaving the subtree
- trace child work units now also carry a stable `agent_label` derived from runtime trace payloads, so the task tree can present friendlier child names than raw `subagent_type`/`task_name` alone
- the trace child tree now also supports explicit branch collapse via `Ctrl+F11`, so one focused subtree can be folded without collapsing the whole task card
- focused trace branches now behave more like a real subtree drill-down: selecting a deeper branch narrows the visible child tree to that branch lineage instead of only adding a marker on the full tree
- subagent runtime trace rows now also carry a stable work-unit id (`invocation_id`, rendered as `call_id`), so repeated child work of the same type can be distinguished as concrete spawned calls instead of only by tree position
- at least one real execution chain now emits genuinely nested `agent_path` values from runtime itself: the distiller flow publishes `preprocess / chunking / chunks / postprocess` subpaths, so deeper trace trees are no longer purely a UI artifact
- a common production path now does the same: `collector` emits symbol-scoped nested paths for `stock_compare` (`collector/compare/<symbol>/stock_snapshot_collector`), so multi-stock collection shows as a true subtree instead of one flat child repeated
- the retrieval back-half now also emits real nested runtime paths: `retriever` publishes `indexing / search / decision` subpaths, so the common `collect -> retrieve` chain no longer flattens all retrieval work into one child
- the reporting back-half now follows the same model: `reporter` publishes `strategy / deterministic / fallback / llm / llm error` subpaths, so the common `collect -> retrieve -> report` chain can surface a true end-to-end execution tree instead of ending in one synthetic report node
- the shell now also exposes a first-class event timeline inspection surface:
  - CLI: `research-system events <run_id>`
  - chat: `/events <run_id|last>` or `/events-read <run_id|last>`
  - TUI: `F6` now cycles `trace -> events -> threads`
  - this uses persisted `events.jsonl` and a typed `SessionHistory.events()` payload instead of overloading replay output
- the events panel is no longer read-only:
  - `F3 / F4` can focus or clear one concrete timeline event
  - `F9` on a focused event can jump directly into the corresponding trace task/call inspect when the event carries `action_id` / `node_id` / `invocation_id`
  - `F9` on a focused trace task/call can now also jump back into the corresponding events timeline row
  - when a trace task/call is already focused, switching into the events panel now automatically narrows the visible event timeline to the matching task slice instead of showing the whole run history
  - this turns the Python TUI into a bidirectional, context-sensitive `events <-> trace` inspector instead of two adjacent but disconnected panels

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

Additional targeted regression for the TUI threads panel and scope cycling:

- `python -m unittest tests.test_chat_keybindings tests.test_cli_output tests.test_chat_session_bootstrap`
  - `88 tests OK`

Additional targeted regression for child-lineage trace tree rendering:

- `python -m unittest tests.test_chat_keybindings tests.test_cli_output tests.test_chat_session_bootstrap`
  - `88 tests OK`

Additional targeted regression for threads lineage-tree focus:

- `python -m unittest tests.test_chat_keybindings tests.test_cli_output tests.test_chat_session_bootstrap`
  - `90 tests OK`

Additional targeted regression for trace branch-level lineage focus:

- `python -m unittest tests.test_chat_keybindings tests.test_cli_output tests.test_chat_session_bootstrap`
  - `93 tests OK`

Additional targeted regression for runtime-emitted nested trace paths:

- `python -m unittest tests.test_distiller_service tests.test_cli_output tests.test_chat_keybindings tests.test_chat_session_bootstrap`
  - `102 tests OK`

Additional targeted regression for collector compare symbol-scoped nested paths:

- `python -m unittest tests.test_stock_compare_flow tests.test_cli_output tests.test_chat_keybindings tests.test_chat_session_bootstrap`
  - `105 tests OK`

Additional targeted regression for nested retriever runtime paths:

- `python -m unittest tests.test_retriever_service tests.test_cli_output tests.test_chat_presenter tests.test_session_history tests.test_run_payloads`
  - `120 tests OK`

## Remaining Gaps To Reach a More Codex-like Python UX

### 1. Thread lifecycle is still weaker than Codex

Missing or incomplete:

- explicit thread resume by thread id outside the current chat checkpoint semantics
- richer lineage presentation for child threads beyond the current focused-thread metadata block
- deeper child nesting beyond the current one-level child focus/collapse model

Recommended Python path:

- extend `chat_thread_store` and thread metadata persistence
- keep pushing the threads panel toward a real thread browser, then add minimal in-TUI actions only if they do not muddy the current prompt_toolkit layout
- keep run history and thread history separate

### 2. Trace is inspectable after a run and now navigable as a live pane, but its presentation is still thinner than Codex

Current state:

- live stream exists
- post-run trace query now exists
- TUI transcript/trace toggle now exists
- live trace cache now updates from runtime events during the run

Missing:

- stronger live grouping of parent/child task lineage inside the trace panel
- richer parent/child lineage cards beyond the current action-anchored task cards
- deeper child-agent nesting beyond the current one-level child summary grouping
- selective collapse/expand controls for large task trees beyond the current single-task focus mode

What improved this turn:

- the shell is no longer only `trace-first`; it now has an `events-first` inspection surface alongside trace and threads
- persisted run events can be inspected directly without replaying the full run payload
- the TUI now has a distinct events panel instead of forcing event history into transcript or replay output
- live runtime events now also accumulate into a dedicated `events_payload` cache during the run, not only after the run is complete
- focused timeline events can now jump into matching trace task/call inspect, and focused trace task/calls can jump back into the matching event row, so event history and subagent task trees are linked instead of isolated
- trace focus can now also constrain the events panel to a contextual event slice, so switching panels preserves more task-local context instead of resetting the operator back to the full run timeline
- the events panel now uses that contextual slice consistently for both rendering and focus/navigation, rather than rendering a narrowed slice while still stepping through the full raw event list
- event inspection scope is now explicit in the TUI status/hint bars (`all / task / branch / call`), so the operator can see whether the event timeline is global or trace-scoped without inferring it from the rendered rows
- during live runs, when the operator is already in the events panel with an active trace context, incoming `events_payload` patches now auto-follow the latest matching event inside that contextual slice instead of leaving focus pinned to an older row
- that live contextual event focus is now also maintained even while another panel is open, so switching back into events does not require recomputing focus from scratch or dropping back to an older matching row
- the trace panel now also surfaces the currently linked event as its own lightweight block, so the shared trace/event inspect context is visible even before switching over to the events panel
- the events panel now mirrors that linkage back in the other direction via a lightweight linked-trace block, so the operator can see the current task/branch/call context without leaving the event timeline
- the events timeline itself now also carries a lightweight linked marker on the current trace-linked row, so the shared context is visible directly in the scrolling event list instead of only in side blocks
- the focused trace task line and focused work-unit line now also carry a lightweight linked-event marker, so trace-side inspection no longer relies only on the separate linked-event detail block to show alignment with the event timeline
- the focused trace branch block now carries that same linked-event marker as well, so task / branch / call inspection all expose the shared event alignment directly in the trace view
- the focused trace child tree now also marks the currently linked branch/work-unit inline, so shared trace/event context is no longer limited to side blocks and focused summaries alone
- trace child-tree branch nodes now also expose aggregated subtree metadata (`child_branches`, `direct_work_units`, `total_work_units`, subtree `status_counts`), so the tree is not only a path browser but also a lightweight recursive task-summary surface

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
  - `/subagent-read <run_id> <call_id>`
  - future `/run-plan` or `/run-steps`

What now exists:

- the shell now has an explicit call-scoped subagent inspection primitive:
  - CLI: `research-system subagent-read <run_id> <call_id>`
  - chat: `/subagent-read <run_id|last> <call_id>`
- this reads one concrete spawned work-unit by stable `invocation_id` / `call_id` instead of forcing the operator to reconstruct it indirectly from the full trace tree or event timeline
- the payload is intentionally inspect-first:
  - top-level run/subagent fields
  - trace rows for that call only
  - event timeline rows for that call only
- this keeps the current Python runtime aligned with the earlier recommendation: make spawned work first-class inspectable before attempting arbitrary subagent spawn/wait controls
- the shell now also has an explicit run-step inspection primitive:
  - CLI: `research-system run-steps <run_id>`
  - chat: `/run-steps <run_id|last>`
- this exposes the persisted `task_plan.json` plus supervisor-selected `next_actions` as a concrete shell surface, which is a safer Codex-aligned control/inspect primitive than arbitrary spawned-agent control

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

Latest trace-tree tightening:

- runtime subagent rows now carry stable `invocation_id` / `call_id`
- TUI child grouping now keys off `call_id` when present instead of only `task_name/label`
- repeated same-type spawned work therefore renders as separate concrete child work units, which is closer to Codex's multi-call subagent inspection model

Completion audit entrypoint:

- see `plan/AllPlan/codex_completion_matrix.md` for the current `proved / partial / open` matrix against the user goal
