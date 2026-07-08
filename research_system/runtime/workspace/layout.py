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
        settings.proposals_root / "pending",
        settings.proposals_root / "approved",
        settings.proposals_root / "rejected",
        settings.subagent_discovery_root / "pending",
        settings.subagent_discovery_root / "approved",
        settings.subagent_discovery_root / "rejected",
        settings.reports_root,
        settings.memory_root / "exports",
        settings.memory_root / "cache",
        settings.config_root,
        settings.taxonomy_root,
    ]


def workspace_template_candidates(settings: "Settings", template_root: Path) -> list[tuple[list[Path], Path]]:
    return [
        (
            [
                template_root / "workspace" / "config" / "retrieval_policy.yml",
                template_root / "research_system" / "config" / "retrieval_policy.yml",
            ],
            settings.retrieval_policy_path,
        ),
        (
            [
                template_root / "workspace" / "config" / "methodology_manifest.yml",
                template_root / "research_system" / "config" / "methodology_manifest.yml",
            ],
            settings.methodology_manifest_path,
        ),
        (
            [
                template_root / "workspace" / "config" / "review_policy.yml",
                template_root / "research_system" / "config" / "review_policy.yml",
            ],
            settings.review_policy_path,
        ),
        (
            [template_root / "workspace" / "config" / "router_defaults.yml"],
            settings.router_defaults_path,
        ),
        (
            [template_root / "workspace" / "config" / "subagent_discovery_policy.yml"],
            settings.subagent_discovery_policy_path,
        ),
        (
            [template_root / "workspace" / "config" / "subagent_discovery_approval_policy.yml"],
            settings.subagent_discovery_approval_policy_path,
        ),
        (
            [template_root / "workspace" / "config" / "subagent_discovery_activation_policy.yml"],
            settings.subagent_discovery_activation_policy_path,
        ),
        (
            [template_root / "workspace" / "config" / "provider_policy.yml"],
            settings.provider_policy_path,
        ),
        (
            [template_root / "workspace" / "config" / "data_sources.yml"],
            settings.data_sources_path,
        ),
        (
            [template_root / "workspace" / "config" / "http_json_sources.yml"],
            settings.http_json_sources_path,
        ),
        (
            [template_root / "workspace" / "config" / "mcp_servers.yml"],
            settings.mcp_servers_path,
        ),
        (
            [template_root / "workspace" / "config" / "openapi_sources.yml"],
            settings.openapi_sources_path,
        ),
        (
            [template_root / "workspace" / "config" / "skill_sources.yml"],
            settings.skill_sources_path,
        ),
        (
            [
                template_root / "workspace" / "taxonomy" / "dimensions.yml",
                template_root / "research_system" / "taxonomy" / "dimensions.yml",
            ],
            settings.taxonomy_dimensions_path,
        ),
        (
            [template_root / "workspace" / "taxonomy" / "dimension_aliases.yml"],
            settings.taxonomy_dimension_aliases_path,
        ),
        (
            [template_root / "workspace" / "taxonomy" / "dimension_patterns.yml"],
            settings.taxonomy_dimension_patterns_path,
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
