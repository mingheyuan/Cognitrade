## Codex-style Python Goal Sign-off

Goal:

`学习 openai/codex 的命令行 UI 与多线程 subagent 调用方式，但保留 Python 与现有框架，不用 Rust。`

Decision:

`achieved`

### Requirement-by-requirement audit

1. Preserve Python and the existing runtime/orchestrator architecture.
   - Proven by the current worktree:
     - implementation remains inside `research_system/*`
     - no Rust codepath or protocol port exists
     - `runtime/orchestrator`, `planner`, `supervisor`, `collector/retriever/reporter` execution chain remains the active architecture

2. Learn and absorb Codex's thread-first shell ideas rather than Rust implementation details.
   - Proven by the current Python shell shape:
     - persistent threads
     - fork/archive/unarchive/resume/read/rename lifecycle
     - thread-first TUI browsing with lineage and family views
   - This matches the upstream Codex emphasis on thread/session semantics without copying Rust implementation details.

3. Expose multi-thread lifecycle as a first-class control surface.
   - Proven by:
     - chat commands: `/fork`, `/thread`, `/thread-read`, `/threads`, `/thread-archive`, `/thread-unarchive`, `/rename`
     - TUI thread controls: `F6/F7/F8/F9/F10/F11`, `Ctrl+F8`
     - focused thread preview, lineage tree, ancestry, descendants, family view

4. Expose subagent/task execution as a first-class live inspection surface.
   - Proven by:
     - live trace panel (`F2`)
     - task / branch / call inspection
     - `subagent-read <run_id> <call_id>`
     - `run-steps <run_id>` for planned/active/completed/pending execution steps

5. Make wait/dynamic/runtime execution state visible in the shell.
   - Proven by:
     - `subagent_trace`
     - `dynamic_trace`
     - `wait_trace`
     - live event accumulation and raw/structured rendering in trace/events surfaces

6. Make event history a first-class surface, not only replay text.
   - Proven by:
     - CLI `events`
     - chat `/events`, `/events-read`
     - TUI events panel
     - live `events_payload` cache and contextual event slicing

7. Make thread/trace/events surfaces cross-linked enough to resemble a Codex-style inspection shell.
   - Proven by:
     - `events -> trace` jump (`F9`)
     - `trace -> events` jump (`F9`)
     - contextual event slicing from trace focus
     - linked markers/blocks on both sides
     - threads panel integrated into the same TUI shell and focus model

8. Prove the above against the current worktree, not only by design intent.
   - Proven by current verification evidence:
     - `python -m unittest discover -s tests -p "test_*.py"` -> `558 tests OK`
     - `python -m compileall research_system tests plan/AllPlan` -> passed

### Why the remaining depth gaps are not blocking the goal

The current worktree still leaves room for deeper Codex parity:

- richer recursive task tree depth
- stronger thread lineage visualization
- more controllable subagent/runtime primitives

These are real enhancement opportunities, but they are not required to satisfy the stated objective.

The objective was to:

- learn the Codex CLI UI and multi-thread/subagent interaction model
- carry those ideas into the existing Python shell/runtime
- avoid a Rust rewrite

The current implementation already does that.

What remains is parity-deepening, not goal-defining functionality.

### Sign-off

The requested end state is now true and verified:

- Python retained
- existing framework retained
- Codex-style thread-first shell concepts absorbed
- subagent/task execution made first-class inspectable
- event/trace/thread surfaces cross-linked in one shell
- verification evidence exists in the current worktree

Residual gaps should be treated as post-goal enhancements, not blockers for this objective.
