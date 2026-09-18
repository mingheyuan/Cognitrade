"""Resolve publication identities from deployed research code and model settings."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import (
    UZI_REPORT_VERSION,
    UZI_SOURCE_COMMIT,
)
from research_system.market_data.native_projection import PROJECTION_VERSION
from research_system.market_data.quant_auxiliary import REPLAY_POLICY
from research_system.runtime.providers.codex_transport import execution_identity


@lru_cache(maxsize=1)
def deployed_prompt_hash() -> str:
    root = Path(__file__).resolve().parents[2] / "domain_packs" / "finance_ashare"
    files = [
        *root.joinpath("subagents").glob("uzi*.py"),
        *root.joinpath("vendor", "uzi_3_9_2").rglob("*.md"),
        *root.joinpath("vendor", "uzi_3_9_2").rglob("*.py"),
        *root.joinpath("artifact_schemas").glob("uzi*.json"),
        # Input transport and normalization change the actual model evidence
        # even when UZI's own prompts do not change. Include them in the same
        # persisted execution identity; do not alter historical V1 hashes.
        root / "subagents" / "runtime.py",
        root / "domain.yaml",
        root / "artifact_schemas" / "snapshot.json",
    ]
    digest = hashlib.sha256()
    for path in sorted(set(files)):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    # Historical-input admission is part of execution identity too. A new
    # safety guard must not silently resume an older pinned model workflow.
    for name in (
        "commodity_pit.py", "report_inputs.py", "quant_auxiliary.py",
        "bundles.py", "native_projection.py", "native_numbers.py", "evidence_bindings.py",
        "report_execution_dependencies.py",
    ):
        path = root.parents[1] / "research_system" / "market_data" / name
        digest.update(f"research_system/market_data/{name}".encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    for name in ("runtime/agent_os.py", "api/agent_os_run_service.py"):
        path = root.parents[1] / "research_system" / name
        digest.update(f"research_system/{name}".encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def deployed_workflow_pins(settings: Any = None) -> dict[str, Any]:
    return {
        "workflow_commit": UZI_SOURCE_COMMIT,
        "skill_version": UZI_REPORT_VERSION,
        "agent_workflow_mode": "full",
        "snapshot_projection_version": PROJECTION_VERSION,
        "native_quant_auxiliary_replay_policy": REPLAY_POLICY,
        "model_id": str(getattr(settings, "default_model", "gpt-5.6-luna")),
        "model_parameters": {
            **execution_identity(),
            "reasoning_effort": getattr(settings, "codex_reasoning_effort", None),
            "stage_reasoning_policy": "Explicit UZI stage/profile overrides take precedence; pinned in prompt_hash",
            "sampling_parameters": "Codex runtime managed; temperature not configurable",
        },
        "prompt_hash": deployed_prompt_hash(),
    }


def resolve_publication_document(document, settings: Any = None):
    from research_system.strategies.contracts import is_report_conditions
    if not is_report_conditions(document):
        return document  # Historical V1 serialization/hash remains untouched.
    runtime = deployed_workflow_pins(settings)
    resolved = document.model_copy(deep=True)
    resolved.pins = resolved.pins.model_copy(update={
        "uzi_source_commit": runtime["workflow_commit"],
        "uzi_methodology_version": runtime["skill_version"],
        "model_id": runtime["model_id"],
        "model_parameters": runtime["model_parameters"],
        "prompt_hash": runtime["prompt_hash"],
    })
    return resolved
