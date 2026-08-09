from __future__ import annotations

import importlib.util
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Callable

import yaml

from research_system.schemas.domain_pack import (
    CapabilityDefinition,
    DomainPackManifest,
    PackToolDefinition,
    SkillDefinition,
)
from research_system.schemas.evaluation import PackEvaluationDefinition


class DomainPackRegistry:
    ARCHIVE_DIRECTORY = ".versions"
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

    def __init__(
        self,
        manifests: list[DomainPackManifest] | None = None,
        *,
        root: Path | None = None,
        require_integrity: bool = False,
        trusted_digests: dict[str, list[str]] | None = None,
    ) -> None:
        self.root = root.resolve() if root is not None else None
        self.require_integrity = bool(require_integrity)
        self.trusted_digests = {
            str(domain_id): {str(digest).strip().lower() for digest in digests if str(digest).strip()}
            for domain_id, digests in (trusted_digests or {}).items()
        }
        self._manifests: dict[str, DomainPackManifest] = {}
        self._pack_paths: dict[str, Path] = {}
        self._pack_digests: dict[str, str] = {}
        self._versioned_manifests: dict[tuple[str, str], DomainPackManifest] = {}
        self._versioned_paths: dict[tuple[str, str], Path] = {}
        self._versioned_digests: dict[tuple[str, str], str] = {}
        self._modules: dict[tuple[str, str, str], ModuleType] = {}
        self._artifact_schemas: dict[tuple[str, str, str], dict] = {}
        for manifest in manifests or []:
            self.register(manifest)
        self.validate_dependencies()

    @classmethod
    def load(
        cls,
        root: Path,
        *,
        require_integrity: bool = False,
        trusted_digests: dict[str, list[str]] | None = None,
    ) -> "DomainPackRegistry":
        registry = cls(
            root=root,
            require_integrity=require_integrity,
            trusted_digests=trusted_digests,
        )
        if not root.exists():
            return registry
        for manifest_path in sorted(root.glob("*/domain.yaml")):
            payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            manifest = DomainPackManifest.model_validate(payload)
            registry.register(manifest, pack_path=manifest_path.parent)
        registry._load_archived_versions(root)
        registry.validate_dependencies()
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
            digest = self._verify_pack(manifest, resolved)
            for skill in manifest.skills:
                self._validate_implementation_path(
                    domain_id=manifest.domain_id,
                    implementation=skill.implementation,
                    pack_root=resolved,
                )
            self._pack_digests[manifest.domain_id] = digest

    def _load_archived_versions(self, root: Path) -> None:
        archive_root = root / self.ARCHIVE_DIRECTORY
        if not archive_root.is_dir():
            return
        for manifest_path in sorted(archive_root.glob("*/*/domain.yaml")):
            payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            manifest = DomainPackManifest.model_validate(payload)
            key = (manifest.domain_id, manifest.version)
            if key in self._versioned_manifests:
                raise ValueError(f"duplicate archived domain pack: {manifest.domain_id}@{manifest.version}")
            if manifest.domain_id in self._manifests and self._manifests[manifest.domain_id].version == manifest.version:
                raise ValueError(
                    f"active and archived domain pack versions collide: {manifest.domain_id}@{manifest.version}"
                )
            resolved = manifest_path.parent.resolve()
            for skill in manifest.skills:
                self._validate_implementation_path(
                    domain_id=manifest.domain_id,
                    implementation=skill.implementation,
                    pack_root=resolved,
                )
            self._versioned_manifests[key] = manifest
            self._versioned_paths[key] = resolved
            self._versioned_digests[key] = self._verify_pack(manifest, resolved)

    def validate_dependencies(self) -> None:
        """Validate all required Pack dependencies before the registry is used."""

        # Active Packs are resolved only against the active catalog. Archived
        # graphs, however, must be able to resolve the matching archived
        # dependency version that was installed with them.
        for manifest in self._manifests.values():
            self._validate_manifest_dependencies(manifest, include_archived=False)
        for manifest in self._versioned_manifests.values():
            self._validate_manifest_dependencies(manifest, include_archived=True)

    def _validate_manifest_dependencies(
        self,
        manifest: DomainPackManifest,
        *,
        include_archived: bool,
    ) -> None:
        for dependency in manifest.dependencies:
            candidates = self._dependency_candidates(
                dependency.domain_id,
                include_archived=include_archived,
            )
            if not candidates:
                if dependency.optional:
                    continue
                raise ValueError(
                    f"domain pack dependency is not installed: "
                    f"{manifest.domain_id}@{manifest.version}->{dependency.domain_id}"
                )
            compatible = [
                target
                for target in candidates
                if self._version_in_range(
                    target.version,
                    min_version=dependency.min_version,
                    max_version=dependency.max_version,
                )
            ]
            if not compatible:
                available = ",".join(target.version for target in candidates)
                raise ValueError(
                    f"domain pack dependency version is incompatible: "
                    f"{manifest.domain_id}@{manifest.version}->{dependency.domain_id}="
                    f"{available}"
                )

    def _dependency_candidates(
        self,
        domain_id: str,
        *,
        include_archived: bool,
    ) -> list[DomainPackManifest]:
        candidates: list[DomainPackManifest] = []
        active = self._manifests.get(domain_id)
        if active is not None:
            candidates.append(active)
        if include_archived:
            candidates.extend(
                manifest
                for (candidate_domain_id, _), manifest in self._versioned_manifests.items()
                if candidate_domain_id == domain_id
            )
        return sorted(
            candidates,
            key=lambda manifest: self._version_key(manifest.version),
            reverse=True,
        )

    def reload(self, root: Path | None = None) -> "DomainPackRegistry":
        """Build a fresh registry snapshot without mutating active callers."""

        source_root = (root or self.root)
        if source_root is None:
            raise ValueError("cannot reload an in-memory DomainPackRegistry without a root")
        return type(self).load(
            source_root,
            require_integrity=self.require_integrity,
            trusted_digests={
                domain_id: sorted(digests)
                for domain_id, digests in self.trusted_digests.items()
            },
        )

    def install(self, source: Path, *, replace: bool = False) -> DomainPackManifest:
        """Atomically install one local Pack and validate the resulting catalog.

        The method deliberately accepts a local directory only. Fetching archives
        or remote packages belongs to a deployment layer, while Registry remains
        responsible for validation and activation.
        """

        if self.root is None:
            raise ValueError("cannot install a Pack into an in-memory registry")
        source = source.resolve()
        if source.is_file() and source.name == "domain.yaml":
            source = source.parent
        if not source.is_dir() or not (source / "domain.yaml").is_file():
            raise ValueError(f"Pack source must contain domain.yaml: {source}")
        payload = yaml.safe_load((source / "domain.yaml").read_text(encoding="utf-8")) or {}
        manifest = DomainPackManifest.model_validate(payload)
        self._validate_pack_layout(manifest, source)
        destination = (self.root / manifest.domain_id).resolve()
        if self.root not in destination.parents:
            raise ValueError("Pack destination escapes the configured registry root")
        if source == destination:
            if not replace:
                return self.get(manifest.domain_id)
            raise ValueError(
                "replacing an active Pack from its own path requires a versioned source"
            )
        old_manifest = self._manifests.get(manifest.domain_id)
        self.root.mkdir(parents=True, exist_ok=True)
        staging_parent = Path(tempfile.mkdtemp(prefix=".pack-staging-", dir=str(self.root)))
        staging = staging_parent / manifest.domain_id
        backup: Path | None = None
        backup_parent: Path | None = None
        archive_path: Path | None = None
        destination_replaced = False
        try:
            shutil.copytree(source, staging)
            if destination.exists() and not replace:
                raise ValueError(f"domain pack is already installed: {manifest.domain_id}")
            if destination.exists() and old_manifest is not None and old_manifest.version == manifest.version:
                raise ValueError(
                    f"Pack replacement must change version: {manifest.domain_id}@{manifest.version}"
                )
            if destination.exists():
                backup_parent = Path(
                    tempfile.mkdtemp(
                        prefix=f".pack-backup-{manifest.domain_id}-",
                        dir=str(self.root.parent),
                    )
                )
                backup = backup_parent / manifest.domain_id
                destination.replace(backup)
            staging.replace(destination)
            destination_replaced = True
            if old_manifest is not None and backup is not None:
                archive_path = self._archive_path(
                    old_manifest.domain_id,
                    old_manifest.version,
                )
                if archive_path.exists():
                    raise ValueError(
                        f"archived Pack version already exists: "
                        f"{old_manifest.domain_id}@{old_manifest.version}"
                    )
                archive_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(backup, archive_path)
            refreshed = self.reload()
            self._adopt(refreshed)
            if backup is not None:
                shutil.rmtree(backup_parent or backup)
            return self.get(manifest.domain_id)
        except Exception:
            if archive_path is not None and archive_path.exists():
                shutil.rmtree(archive_path)
            if destination.exists() and backup is not None:
                shutil.rmtree(destination)
                backup.replace(destination)
            elif destination_replaced and destination.exists() and backup is None:
                shutil.rmtree(destination)
            raise
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)
            if backup_parent is not None:
                shutil.rmtree(backup_parent, ignore_errors=True)

    def uninstall(self, domain_id: str) -> DomainPackManifest:
        """Deactivate a Pack while archiving its version for old Run replay."""

        if self.root is None:
            raise ValueError("cannot uninstall a Pack from an in-memory registry")
        manifest = self.get(domain_id)
        destination = self.pack_path(domain_id).resolve()
        root = self.root.resolve()
        if root not in destination.parents:
            raise ValueError("Pack path escapes the configured registry root")
        backup_parent = Path(
            tempfile.mkdtemp(
                prefix=f".pack-uninstall-{domain_id}-",
                dir=str(root.parent),
            )
        )
        backup = backup_parent / domain_id
        archive_path: Path | None = None
        destination.replace(backup)
        try:
            archive_path = self._archive_path(manifest.domain_id, manifest.version)
            if archive_path.exists():
                raise ValueError(
                    f"archived Pack version already exists: {manifest.domain_id}@{manifest.version}"
            )
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(backup, archive_path)
            refreshed = self.reload()
            self._adopt(refreshed)
            shutil.rmtree(backup_parent)
            return manifest
        except Exception:
            if archive_path is not None and archive_path.exists():
                shutil.rmtree(archive_path)
            backup.replace(destination)
            shutil.rmtree(backup_parent, ignore_errors=True)
            raise

    def pack_digest(self, domain_id: str, *, version: str | None = None) -> str:
        manifest = self.get(domain_id, version=version)
        if version is None or manifest.version == self._manifests.get(domain_id, manifest).version:
            try:
                return self._pack_digests[domain_id]
            except KeyError as exc:
                raise KeyError(f"domain pack has no installed payload: {domain_id}") from exc
        try:
            return self._versioned_digests[(domain_id, manifest.version)]
        except KeyError as exc:
            raise KeyError(
                f"domain pack has no installed payload: {domain_id}@{manifest.version}"
            ) from exc

    @classmethod
    def payload_digest(cls, pack_root: Path) -> str:
        """Hash deterministic Pack payload files, excluding the self-describing manifest."""

        digest = hashlib.sha256()
        root = pack_root.resolve()
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name == "domain.yaml":
                continue
            relative = path.relative_to(root).as_posix()
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def _archive_path(self, domain_id: str, version: str) -> Path:
        if self.root is None:
            raise ValueError("Pack archive requires a filesystem-backed registry")
        path = (self.root / self.ARCHIVE_DIRECTORY / domain_id / version).resolve()
        if self.root not in path.parents:
            raise ValueError("Pack archive path escapes the configured registry root")
        return path

    @staticmethod
    def _version_key(value: str) -> tuple[int, ...]:
        parts = str(value or "").strip().split(".")
        if not parts or any(not part.isdigit() for part in parts):
            raise ValueError(f"Pack versions must be numeric dotted versions: {value}")
        return tuple(int(part) for part in parts)

    @classmethod
    def _version_in_range(
        cls,
        version: str,
        *,
        min_version: str | None,
        max_version: str | None,
    ) -> bool:
        candidate = cls._version_key(version)
        if min_version and candidate < cls._version_key(min_version):
            return False
        return not (max_version and candidate > cls._version_key(max_version))

    def _adopt(self, other: "DomainPackRegistry") -> None:
        self.root = other.root
        self._manifests = other._manifests
        self._pack_paths = other._pack_paths
        self._pack_digests = other._pack_digests
        self._versioned_manifests = other._versioned_manifests
        self._versioned_paths = other._versioned_paths
        self._versioned_digests = other._versioned_digests
        self._modules = {}
        self._artifact_schemas = {}

    @classmethod
    def _validate_pack_layout(cls, manifest: DomainPackManifest, pack_root: Path) -> None:
        missing = [name for name in cls.REQUIRED_DIRECTORIES if not (pack_root / name).is_dir()]
        if missing:
            raise ValueError(
                f"domain pack layout is incomplete: {manifest.domain_id}:{','.join(missing)}"
            )
        missing_schemas = [
            artifact.artifact_type
            for artifact in manifest.artifact_types
            if not (pack_root / "artifact_schemas" / f"{artifact.artifact_type}.json").is_file()
            and not artifact.json_schema
        ]
        if missing_schemas:
            raise ValueError(
                f"domain pack is missing artifact schemas: {manifest.domain_id}:{','.join(missing_schemas)}"
            )

    def _verify_pack(self, manifest: DomainPackManifest, pack_root: Path) -> str:
        self._validate_pack_layout(manifest, pack_root)
        digest = self.payload_digest(pack_root)
        declared_digest = manifest.integrity.payload_sha256
        if declared_digest and digest != declared_digest:
            raise ValueError(
                f"domain pack integrity mismatch: {manifest.domain_id}:{digest}!={declared_digest}"
            )
        trusted = self.trusted_digests.get(manifest.domain_id, set())
        if trusted and digest not in trusted:
            raise ValueError(
                f"domain pack digest is not trusted: {manifest.domain_id}:{digest}"
            )
        if self.require_integrity and not declared_digest and digest not in trusted:
            raise ValueError(
                f"domain pack requires integrity metadata or a trusted digest: {manifest.domain_id}"
            )
        return digest

    @staticmethod
    def _validate_implementation_path(
        *,
        domain_id: str,
        implementation: str,
        pack_root: Path,
    ) -> None:
        if ":" not in implementation:
            raise ValueError(
                f"domain Skill implementation must be module:function: "
                f"{domain_id}:{implementation}"
            )
        module_ref, _function_name = implementation.rsplit(":", 1)
        module_path = (pack_root / Path(*module_ref.split("."))).with_suffix(".py").resolve()
        if pack_root not in module_path.parents or not module_path.is_file():
            raise ValueError(
                f"domain Skill implementation escapes or is missing from its pack: "
                f"{domain_id}:{implementation}"
            )

    def get(self, domain_id: str, *, version: str | None = None) -> DomainPackManifest:
        if version is None:
            try:
                return self._manifests[domain_id]
            except KeyError as exc:
                raise KeyError(f"unknown domain pack: {domain_id}") from exc
        key = (domain_id, version)
        try:
            if self._manifests.get(domain_id, None) is not None and self._manifests[domain_id].version == version:
                return self._manifests[domain_id]
            return self._versioned_manifests[key]
        except KeyError as exc:
            raise KeyError(f"unknown domain pack version: {domain_id}@{version}") from exc

    def list(self) -> list[DomainPackManifest]:
        return [self._manifests[domain_id] for domain_id in sorted(self._manifests)]

    def pack_path(self, domain_id: str, *, version: str | None = None) -> Path:
        if version is None:
            try:
                return self._pack_paths[domain_id]
            except KeyError as exc:
                raise KeyError(f"domain pack has no installed path: {domain_id}") from exc
        active = self._manifests.get(domain_id)
        if active is not None and active.version == version:
            return self._pack_paths[domain_id]
        try:
            return self._versioned_paths[(domain_id, version)]
        except KeyError as exc:
            raise KeyError(f"domain pack has no installed version path: {domain_id}@{version}") from exc

    def capabilities(
        self,
        *,
        domain_id: str,
        capability: str | None = None,
        produces: str | None = None,
        version: str | None = None,
    ) -> list[CapabilityDefinition]:
        rows = list(self.get(domain_id, version=version).capabilities)
        if capability is not None:
            rows = [row for row in rows if row.capability == capability]
        if produces is not None:
            rows = [
                row
                for row in rows
                if produces in {port.artifact_type for port in row.produces}
            ]
        return rows

    def tool_definitions(
        self,
        *,
        domain_id: str,
        version: str | None = None,
    ) -> list[PackToolDefinition]:
        """Load declarative Tool contracts owned by an installed Pack."""
        try:
            pack_root = self.pack_path(domain_id, version=version)
        except KeyError:
            return []
        definitions: list[PackToolDefinition] = []
        for path in sorted((pack_root / "tools").glob("*.yaml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(payload, dict) or not payload.get("name"):
                continue
            # Pack-owned tools use read-only/subagent defaults at the Registry boundary.
            payload = dict(payload)
            payload.setdefault("namespace", "pack")
            payload.setdefault("side_effect_level", "read_only")
            payload.setdefault("allowed_callers", ["subagent"])
            definition = PackToolDefinition.model_validate(payload)
            if not definition.implementation:
                definition = definition.model_copy(
                    update={
                        "implementation": f"tools.{path.stem}:invoke",
                    }
                )
            definitions.append(definition)
        return definitions

    def skill_definitions(
        self,
        *,
        domain_id: str,
        version: str | None = None,
    ) -> list[SkillDefinition]:
        """Return the version-pinned Skill contracts owned by a Pack."""
        return list(self.get(domain_id, version=version).skills)

    def resolve_skill(
        self,
        *,
        domain_id: str,
        skill_name: str,
        version: str | None = None,
    ) -> Callable:
        definitions = [
            item
            for item in self.skill_definitions(domain_id=domain_id, version=version)
            if item.skill_name == skill_name
        ]
        if not definitions:
            raise KeyError(f"unknown domain Skill: {domain_id}:{skill_name}")
        return self.resolve_callable(
            domain_id=domain_id,
            implementation=definitions[0].implementation,
            version=version,
        )

    def resolve_executor(
        self,
        *,
        domain_id: str,
        capability: str,
        version: str | None = None,
        subagent_type: str | None = None,
    ) -> Callable:
        definitions = self.capabilities(
            domain_id=domain_id,
            capability=capability,
            version=version,
        )
        if subagent_type:
            definitions = [
                item for item in definitions if item.subagent_type == subagent_type
            ]
        if not definitions:
            raise KeyError(f"unknown domain capability: {domain_id}:{capability}")
        implementation = str(definitions[0].implementation or "").strip()
        if not implementation:
            raise ValueError(
                f"capability has no executable implementation: {domain_id}:{capability}"
            )
        return self.resolve_callable(
            domain_id=domain_id,
            implementation=implementation,
            version=version,
        )

    def resolve_callable(
        self,
        *,
        domain_id: str,
        implementation: str,
        version: str | None = None,
    ) -> Callable:
        """Load a callable declared by an installed pack without escaping its root."""
        if ":" not in implementation:
            raise ValueError(
                f"domain implementation must be module:function: {domain_id}:{implementation}"
            )
        module_ref, function_name = implementation.rsplit(":", 1)
        relative_module = Path(*module_ref.split("."))
        pack_root = self.pack_path(domain_id, version=version)
        module_path = (pack_root / relative_module).with_suffix(".py").resolve()
        if pack_root not in module_path.parents or not module_path.is_file():
            raise ValueError(
                f"capability implementation escapes or is missing from its pack: {implementation}"
            )
        effective_version = version or self.get(domain_id).version
        cache_key = (domain_id, effective_version, str(module_path))
        module = self._modules.get(cache_key)
        if module is None:
            module_name = (
                f"cognitrade_domain_pack_{domain_id}_{effective_version}_"
                f"{module_path.stem}_{abs(hash(module_path))}"
            )
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

    def artifact_schema(
        self,
        *,
        domain_id: str,
        artifact_type: str,
        version: str | None = None,
    ) -> dict:
        effective_version = version or self.get(domain_id).version
        cache_key = (domain_id, effective_version, artifact_type)
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
            path = self.pack_path(domain_id, version=version) / "artifact_schemas" / f"{artifact_type}.json"
            schema = json.loads(path.read_text(encoding="utf-8"))
        self._artifact_schemas[cache_key] = schema
        return dict(schema)

    def evaluations(
        self,
        *,
        domain_id: str,
        version: str | None = None,
    ) -> list[PackEvaluationDefinition]:
        """Load and validate Pack-owned evaluation declarations."""
        pack_root = self.pack_path(domain_id, version=version)
        manifest = self.get(domain_id, version=version)
        definitions: list[PackEvaluationDefinition] = []
        seen: set[str] = set()
        for relative_path in manifest.evaluations:
            path = (pack_root / str(relative_path)).resolve()
            if pack_root not in path.parents or not path.is_file():
                raise ValueError(
                    f"Pack evaluation path escapes or is missing: {domain_id}:{relative_path}"
                )
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            definition = PackEvaluationDefinition.model_validate(payload)
            if definition.evaluation_id in seen:
                raise ValueError(
                    f"duplicate Pack evaluation: {domain_id}:{definition.evaluation_id}"
                )
            seen.add(definition.evaluation_id)
            definitions.append(definition)
        return definitions


__all__ = ["DomainPackRegistry"]
