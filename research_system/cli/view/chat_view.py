from __future__ import annotations

from prompt_toolkit.application import Application
from research_system.cli.view.chat_view_build_support import (
    build_chat_application as support_build_chat_application,
    build_chat_view as support_build_chat_view,
)
from research_system.cli.view.chat_view_components import (
    ChatViewComponents,
    TranscriptOutputArea,
    build_layout_children as support_build_layout_children,
)
from research_system.cli.view.chat_view_sync_support import (
    sync_chat_view_from_scene as support_sync_chat_view_from_scene,
)
from research_system.cli.state.chat_types import TuiSession
from research_system.cli.shell.windows_terminal import create_terminal_io


def build_chat_view(session: TuiSession) -> ChatViewComponents:
    return support_build_chat_view(session)


def build_chat_application(*, session: TuiSession, view: ChatViewComponents, key_bindings) -> Application:
    return build_chat_application_runtime(
        session=session,
        view=view,
        key_bindings=key_bindings,
        application_factory=Application,
        terminal_io_factory=create_terminal_io,
    )


def build_chat_application_runtime(
    *,
    session: TuiSession,
    view: ChatViewComponents,
    key_bindings,
    application_factory,
    terminal_io_factory,
) -> Application:
    return support_build_chat_application(
        session=session,
        view=view,
        key_bindings=key_bindings,
        application_factory=application_factory,
        terminal_io_factory=terminal_io_factory,
    )


def build_layout_children(scene, output_area, status_bar, hint_bar, input_area):
    return support_build_layout_children(scene, output_area, status_bar, hint_bar, input_area)


def sync_chat_view_from_scene(session: TuiSession) -> None:
    support_sync_chat_view_from_scene(session)

