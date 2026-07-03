## Codex-style Python Completion Matrix

Goal under audit:

`学习 openai/codex 的命令行 UI 与多线程 subagent 调用方式，但保留 Python 与现有框架，不用 Rust。`

### Requirements

1. Preserve Python and the existing runtime/orchestrator architecture.
2. Learn and absorb Codex's thread-first shell ideas rather than Rust implementation details.
3. Expose multi-thread lifecycle as a first-class control surface.
4. Expose subagent/task execution as a first-class live inspection surface.
5. Make wait/dynamic/runtime execution state visible in the shell.
6. Make event history a first-class surface, not only replay text.
7. Make thread/trace/events surfaces cross-linked enough to resemble a Codex-style inspection shell.
8. Prove the above against the current worktree, not only by design intent.

### Current audit

`proved`

- Python/runtime preservation
  - Evidence:
    - current implementation remains inside `research_system/*`
    - no Rust codepath or protocol port exists
    - orchestrator/supervisor/runtime architecture remains in place
- Thread-first lifecycle surface
  - Evidence:
    - chat commands: `/fork`, `/thread`, `/thread-read`, `/threads`, `/thread-archive`, `/thread-unarchive`, `/rename`
    - TUI thread controls: `F6/F7/F8/F9/F10/F11`, `Ctrl+F8`
    - tests across `tests/test_chat_memory.py`, `tests/test_chat_keybindings.py`, `tests/test_cli_output.py`
- Live trace/task inspection
  - Evidence:
    - `F2` trace panel
    - task/branch/child focus and collapse controls
    - live `trace_payload` accumulation from runtime events
    - nested runtime paths now emitted by `distiller`, `collector`, `retriever`, `reporter`
    - tests across `tests/test_chat_presenter.py`, `tests/test_cli_output.py`, `tests/test_retriever_service.py`, `tests/test_reporter_service.py`, `tests/test_distiller_service.py`, `tests/test_stock_compare_flow.py`
- Event-first inspection
  - Evidence:
    - CLI `research-system events <run_id>`
    - chat `/events <run_id|last>` and `/events-read <run_id|last>`
    - TUI events panel in `F6` cycle
    - live `events_payload` accumulation and rendering
    - tests across `tests/test_session_history.py`, `tests/test_run_payloads.py`, `tests/test_chat_event_adapter.py`, `tests/test_cli_output.py`
- Cross-linking between trace and events
  - Evidence:
    - `F9` events -> trace
    - `F9` trace -> events
    - contextual event slice from trace focus
    - live events focus auto-follow within and outside events panel
    - linked blocks and linked markers on both sides
    - tests in `tests/test_chat_keybindings.py` and `tests/test_cli_output.py`
- Whole-worktree regression evidence
  - Evidence:
    - `python -m unittest discover -s tests -p "test_*.py"` -> `543 tests OK`
    - `python -m compileall research_system tests plan/AllPlan` -> passed

`partial`

- Codex-like shell depth for thread lineage
  - Current proof:
    - thread list, lineage tree, focused-thread family view, preview, resume/fork/archive all exist
    - focused-thread family view now exposes full `ancestry` and recursive `descendants`, not only one-level parent/current/children
  - Remaining gap:
    - lineage visualization is still relatively shallow compared with Codex's stronger thread/task shell semantics
- Codex-like task tree depth
  - Current proof:
    - action-anchored task cards, branch drill-down, call-level inspect, linked markers, nested runtime paths
    - branch nodes in the trace child tree now expose aggregated subtree metadata (`child_branches`, `direct_work_units`, `total_work_units`, subtree `status_counts`) instead of only path labels
  - Remaining gap:
    - still based on lightweight prompt_toolkit cards rather than a deeper recursive task tree model
    - child-agent nesting is stronger than before, but not yet equivalent to a richer full task-tree shell
- Subagent control semantics
  - Current proof:
    - subagent visibility is first-class
    - spawned calls are now directly inspectable as shell primitives via `subagent-read <run_id> <call_id>`
    - run execution steps are now directly inspectable as shell primitives via `run-steps <run_id>`, exposing planned, active, completed, and pending actions from the persisted task plan and supervisor decisions
  - Remaining gap:
    - subagent invocation is still inspect-first rather than spawn/wait controllable
    - there is still no safe higher-level spawned-task control primitive beyond inspect/read surfaces

`open`

- A final requirement-by-requirement sign-off document tying current Python surfaces explicitly to the user objective and remaining non-goals in one concise entrypoint.

### Conclusion

The current worktree already proves a substantial Codex-style Python shell:

- thread-first
- trace-first
- event-first
- subagent-visible
- wait/dynamic state visible
- cross-linked inspection surfaces

What is still not proven as complete is not the existence of these surfaces, but whether the remaining `partial` items should be treated as required for the user's requested end state or as optional future deepening beyond the intended scope.
