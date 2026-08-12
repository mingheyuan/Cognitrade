from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from research_system.runtime.platform.settings import Settings


def required_workspace_directories(settings: "Settings") -> list[Path]:
    return [
        settings.workspace_root,
        settings.runs_root,
        settings.reports_root,
        settings.memory_root / "exports",
        settings.memory_root / "cache",
        settings.config_root,
        settings.strategy_object_store_root or settings.workspace_root / "strategy_objects",
    ]


def workspace_template_candidates(settings: "Settings", template_root: Path) -> list[tuple[list[Path], Path]]:
    return [
        (
            [template_root / "workspace" / "config" / "provider_policy.yml"],
            settings.provider_policy_path,
        ),
        (
            [template_root / "workspace" / "config" / "data_sources.yml"],
            settings.data_sources_path,
        ),
        (
            [template_root / "workspace" / "config" / "mcp_servers.yml"],
            settings.mcp_servers_path,
        ),
    ]


def ensure_workspace_layout(settings: "Settings", template_root: Path | None = None) -> None:
    for directory in required_workspace_directories(settings):
        directory.mkdir(parents=True, exist_ok=True)

    root = template_root or Path(__file__).resolve().parents[3]
    for source_candidates, target_path in workspace_template_candidates(settings, root):
        if target_path.exists():
            continue
        source_path = next((path for path in source_candidates if path.exists()), None)
        if source_path is None:
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target_path)
