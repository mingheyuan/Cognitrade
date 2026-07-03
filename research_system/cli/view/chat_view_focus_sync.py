from __future__ import annotations


def sync_focus_target(session) -> None:
    app_layout = getattr(session.app, "layout", None) if session.app is not None else None
    if app_layout is None or session.ui_state.last_focus_target == session.focus_target:
        return
    try:
        if session.focus_target == "commands_popup" and getattr(session, "commands_popup", None) is not None:
            app_layout.focus(session.commands_popup)
        elif session.input_area is not None:
            app_layout.focus(session.input_area)
    except Exception:  # pragma: no cover - prompt_toolkit focus fallback
        pass
    session.ui_state.last_focus_target = session.focus_target

