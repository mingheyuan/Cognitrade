from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Callable

import yaml

from research_system.schemas.domain_pack import CapabilityDefinition, DomainPackManifest


class DomainPackRegistry:
    REQUIRED_DIRECTORIES = (
        "ontology",
        "artifact_schemas",
        "subagents",
        "tools",
        "planner_policies",
        "quality_gates",
        "report_contracts",
        "evaluations",
    )

    def __init__(self, manifests: list[DomainPackManifest] | None = None) -> None:
        self._manifests: dict[str, DomainPackManifest] = {}
        self._pack_paths: dict[str, Path] = {}
        self._modules: dict[tuple[str, str], ModuleType] = {}
        self._artifact_schemas: dict[tuple[str, str], dict] = {}
        for manifest in manifests or []:
            self.register(manifest)

    @classmethod
    def load(cls, root: Path) -> "DomainPackRegistry":
        registry = cls()
        if not root.exists():
            return registry
        for manifest_path in sorted(root.glob("*/domain.yaml")):
            payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            manifest = DomainPackManifest.model_validate(payload)
            registry.register(manifest, pack_path=manifest_path.parent)
        return registry

    def register(self, manifest: DomainPackManifest, *, pack_path: Path | None = None) -> None:
        if manifest.domain_id in self._manifests:
            raise ValueError(f"duplicate domain pack: {manifest.domain_id}")
        self._manifests[manifest.domain_id] = manifest
        if pack_path is not None:
            resolved = pack_path.resolve()
            missing = [name for name in self.REQUIRED_DIRECTORIES if not (resolved / name).is_dir()]
            if missing:
                raise ValueError(
                    f"domain pack layout is incomplete: {manifest.domain_id}:{','.join(missing)}"
                )
            missing_schemas = [
                artifact.artifact_type
                for artifact in manifest.artifact_types
                if not (resolved / "artifact_schemas" / f"{artifact.artifact_type}.json").is_file()
                and not artifact.json_schema
            ]
            if missing_schemas:
                raise ValueError(
                    f"domain pack is missing artifact schemas: {manifest.domain_id}:{','.join(missing_schemas)}"
                )
            self._pack_paths[manifest.domain_id] = resolved

    def get(self, domain_id: str) -> DomainPackManifest:
        try:
            return self._manifests[domain_id]
        except KeyError as exc:
            raise KeyError(f"unknown domain pack: {domain_id}") from exc

    def list(self) -> list[DomainPackManifest]:
        return [self._manifests[domain_id] for domain_id in sorted(self._manifests)]

    def pack_path(self, domain_id: str) -> Path:
        try:
            return self._pack_paths[domain_id]
        except KeyError as exc:
            raise KeyError(f"domain pack has no installed path: {domain_id}") from exc

    def capabilities(
        self,
        *,
        domain_id: str,
        capability: str | None = None,
        produces: str | None = None,
    ) -> list[CapabilityDefinition]:
        rows = list(self.get(domain_id).capabilities)
        if capability is not None:
            rows = [row for row in rows if row.capability == capability]
        if produces is not None:
            rows = [
                row
                for row in rows
                if produces in {port.artifact_type for port in row.produces}
            ]
        return rows

    def resolve_executor(self, *, domain_id: str, capability: str) -> Callable:
        definitions = self.capabilities(domain_id=domain_id, capability=capability)
        if not definitions:
            raise KeyError(f"unknown domain capability: {domain_id}:{capability}")
        implementation = str(definitions[0].implementation or "").strip()
        if not implementation:
            raise ValueError(
                f"capability has no executable implementation: {domain_id}:{capability}"
            )
        return self.resolve_callable(domain_id=domain_id, implementation=implementation)

    def resolve_callable(self, *, domain_id: str, implementation: str) -> Callable:
        """Load a callable declared by an installed pack without escaping its root."""
        if ":" not in implementation:
            raise ValueError(
                f"domain implementation must be module:function: {domain_id}:{implementation}"
            )
        module_ref, function_name = implementation.rsplit(":", 1)
        relative_module = Path(*module_ref.split("."))
        module_path = (self.pack_path(domain_id) / relative_module).with_suffix(".py").resolve()
        pack_root = self.pack_path(domain_id)
        if pack_root not in module_path.parents or not module_path.is_file():
            raise ValueError(
                f"capability implementation escapes or is missing from its pack: {implementation}"
            )
        cache_key = (domain_id, str(module_path))
        module = self._modules.get(cache_key)
        if module is None:
            module_name = f"cognitrade_domain_pack_{domain_id}_{module_path.stem}_{abs(hash(module_path))}"
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load domain capability module: {module_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._modules[cache_key] = module
        executor = getattr(module, function_name, None)
        if not callable(executor):
            raise TypeError(f"domain capability implementation is not callable: {implementation}")
        return executor

    def artifact_schema(self, *, domain_id: str, artifact_type: str) -> dict:
        cache_key = (domain_id, artifact_type)
        cached = self._artifact_schemas.get(cache_key)
        if cached is not None:
            return dict(cached)
        manifest = self.get(domain_id)
        definition = next(
            (item for item in manifest.artifact_types if item.artifact_type == artifact_type),
            None,
        )
        if definition is None:
            raise KeyError(f"unknown domain artifact type: {domain_id}:{artifact_type}")
        if definition.json_schema:
            schema = dict(definition.json_schema)
        else:
            path = self.pack_path(domain_id) / "artifact_schemas" / f"{artifact_type}.json"
            schema = json.loads(path.read_text(encoding="utf-8"))
        self._artifact_schemas[cache_key] = schema
        return dict(schema)


__all__ = ["DomainPackRegistry"]
