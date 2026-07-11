from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.agent_os import AgentOS
from research_system.runtime.execution.core.graph_runner import ExecutionGraphRunner
from research_system.runtime.platform.settings import Settings
from research_system.schemas.domain_pack import (
    ArtifactTypeDefinition,
    DomainPackDependency,
    DomainPackManifest,
)
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.execution_graph import ArtifactPort, ExecutionGraph, ExecutionNode


REPO_ROOT = Path(__file__).resolve().parents[1]


class DomainPackLifecycleTests(unittest.TestCase):
    def test_dependency_ranges_are_validated_before_registry_use(self) -> None:
        base = DomainPackManifest(
            domain_id="shared_schema",
            version="1.2.0",
            description="Shared schema pack.",
            artifact_types=[ArtifactTypeDefinition(artifact_type="shared_input")],
            capabilities=[],
        )
        dependent = DomainPackManifest(
            domain_id="dependent_pack",
            version="1.0.0",
            description="Dependent pack.",
            dependencies=[
                DomainPackDependency(
                    domain_id="shared_schema",
                    min_version="1.0.0",
                    max_version="1.9.0",
                )
            ],
            artifact_types=[ArtifactTypeDefinition(artifact_type="dependent_input")],
            capabilities=[],
        )

        registry = DomainPackRegistry([dependent, base])

        self.assertEqual(registry.get("shared_schema").version, "1.2.0")

        with self.assertRaisesRegex(ValueError, "dependency is not installed"):
            DomainPackRegistry([dependent])

        incompatible = dependent.model_copy(
            update={
                "dependencies": [
                    DomainPackDependency(
                        domain_id="shared_schema",
                        min_version="2.0.0",
                    )
                ]
            }
        )
        with self.assertRaisesRegex(ValueError, "dependency version is incompatible"):
            DomainPackRegistry([incompatible, base])

    def test_payload_integrity_can_be_declared_or_externally_trusted(self) -> None:
        source = REPO_ROOT / "domain_packs" / "legal_contract"
        digest = DomainPackRegistry.payload_digest(source)

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "packs"
            copied = root / "legal_contract"
            shutil.copytree(source, copied)
            manifest_path = copied / "domain.yaml"
            payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            payload["integrity"] = {"algorithm": "sha256", "payload_sha256": digest}
            manifest_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

            registry = DomainPackRegistry.load(root, require_integrity=True)
            self.assertEqual(registry.pack_digest("legal_contract"), digest)

            payload["integrity"]["payload_sha256"] = "0" * 64
            manifest_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "integrity mismatch"):
                DomainPackRegistry.load(root)

        with TemporaryDirectory() as temporary_directory:
            trusted_root = Path(temporary_directory) / "packs"
            shutil.copytree(source, trusted_root / "legal_contract")
            self.assertEqual(
                DomainPackRegistry.load(
                    trusted_root,
                    require_integrity=True,
                    trusted_digests={"legal_contract": [digest]},
                ).pack_digest("legal_contract"),
                digest,
            )

    def test_install_reload_and_uninstall_are_atomic_registry_operations(self) -> None:
        source = REPO_ROOT / "domain_packs" / "legal_contract"
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "packs"
            registry = DomainPackRegistry.load(root)

            installed = registry.install(source)
            self.assertEqual(installed.domain_id, "legal_contract")
            self.assertEqual([row.domain_id for row in registry.list()], ["legal_contract"])
            self.assertTrue((root / "legal_contract" / "domain.yaml").is_file())

            with self.assertRaisesRegex(ValueError, "already installed"):
                registry.install(source)

            removed = registry.uninstall("legal_contract")
            self.assertEqual(removed.domain_id, "legal_contract")
            self.assertEqual(registry.list(), [])
            self.assertFalse((root / "legal_contract").exists())

    def test_agent_os_rebinds_all_pack_services_after_lifecycle_change(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(REPO_ROOT).model_copy(
                update={
                    "workspace_root": root,
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": root / "packs",
                }
            )
            agent_os = AgentOS(settings)

            installed = agent_os.install_domain_pack(
                REPO_ROOT / "domain_packs" / "legal_contract"
            )
            self.assertEqual(installed["domain_id"], "legal_contract")
            self.assertTrue(agent_os.supported_intents() == [])
            self.assertEqual(
                agent_os.tool_registry.definition("core.graph.inspect").namespace,
                "core",
            )

            removed = agent_os.uninstall_domain_pack("legal_contract")
            self.assertEqual(removed["status"], "uninstalled")
            self.assertEqual(agent_os.list_domain_packs(), [])

    def test_replacement_archives_the_old_version_for_graph_resolution(self) -> None:
        source = REPO_ROOT / "domain_packs" / "legal_contract"
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            registry_root = root / "packs"
            v2_source = root / "legal_contract_v2"
            shutil.copytree(source, v2_source)
            manifest_path = v2_source / "domain.yaml"
            payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            payload["version"] = "0.2.0"
            manifest_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

            registry = DomainPackRegistry.load(registry_root)
            registry.install(source)
            registry.install(v2_source, replace=True)

            self.assertEqual(registry.get("legal_contract").version, "0.2.0")
            archived = registry.get("legal_contract", version="0.1.0")
            self.assertEqual(archived.version, "0.1.0")
            self.assertTrue(
                registry.resolve_executor(
                    domain_id="legal_contract",
                    capability="legal.contract.clause.extract",
                    version="0.1.0",
                )
            )
            self.assertTrue(
                registry.artifact_schema(
                    domain_id="legal_contract",
                    artifact_type="contract_document",
                    version="0.1.0",
                )
            )

            registry.uninstall("legal_contract")
            self.assertEqual(registry.list(), [])
            self.assertEqual(registry.get("legal_contract", version="0.2.0").version, "0.2.0")

    def test_old_graph_executes_against_archived_capability_schema_gate_and_report_contract(self) -> None:
        def write_pack(root: Path, version: str, marker: str) -> Path:
            pack_root = root / f"versioned_pack_{version}"
            for directory in DomainPackRegistry.REQUIRED_DIRECTORIES:
                (pack_root / directory).mkdir(parents=True, exist_ok=True)
            (pack_root / "domain.yaml").write_text(
                f"""
domain_id: versioned_pack
version: {version}
description: Pack version resolution test.
artifact_types:
  - artifact_type: versioned_input
  - artifact_type: versioned_output
capabilities:
  - subagent_type: versioned_worker
    capability: versioned.worker
    implementation: subagents.runtime:worker
    consumes:
      - artifact_type: versioned_input
    produces:
      - artifact_type: versioned_output
  - subagent_type: versioned_gate
    capability: versioned.gate
    implementation: subagents.runtime:gate
    consumes:
      - artifact_type: versioned_output
quality_gates:
  - gate_id: versioned.output
    artifact_type: versioned_output
    evaluator_capability: versioned.gate
    blocking: true
report_contracts:
  - contract_id: versioned.output.{marker}
    artifact_type: versioned_output
    required_sections: [value, {marker}_marker]
    required_source_refs: true
    required_gate_ids: [versioned.output]
governance:
  max_nodes: 4
  max_depth: 4
  max_concurrency: 1
  max_retries_per_node: 0
  required_gate_ids: [versioned.output]
""".strip(),
                encoding="utf-8",
            )
            (pack_root / "artifact_schemas" / "versioned_input.json").write_text(
                '{"type":"object","required":["value"]}',
                encoding="utf-8",
            )
            (pack_root / "artifact_schemas" / "versioned_output.json").write_text(
                (
                    '{"type":"object","required":["value","'
                    f'{marker}_marker"],"properties":{{"{marker}_marker":{{"const":true}}}}}}'
                ),
                encoding="utf-8",
            )
            (pack_root / "subagents" / "runtime.py").write_text(
                f"""
from research_system.schemas.artifacts import ArtifactDraft

def worker(context):
    source = context.require_artifact("versioned_input")
    return ArtifactDraft(
        artifact_type="versioned_output",
        content={{"value": "{marker}", "{marker}_marker": True}},
        source_refs=list(source.source_refs),
        parent_artifact_ids=[source.artifact_id],
        confidence=1.0,
    )

def gate(context):
    artifact = context.require_artifact("versioned_output")
    if artifact.content.get("{marker}_marker") is not True:
        return {{"verdict": "fail", "reason": "wrong Pack version"}}
    return {{"verdict": "pass", "reason": "{marker} gate", "score": 1.0}}
""".strip(),
                encoding="utf-8",
            )
            return pack_root

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_v1 = write_pack(root, "1.0.0", "v1")
            source_v2 = write_pack(root, "2.0.0", "v2")
            registry = DomainPackRegistry.load(root / "registry")
            registry.install(source_v1)

            old_graph = ExecutionGraph(
                domain_id="versioned_pack",
                objective="Execute the versioned capability.",
                governance=registry.get("versioned_pack").governance,
                metadata={
                    "domain_version": "1.0.0",
                    "required_artifact_types": ["versioned_output"],
                },
                nodes=[
                    ExecutionNode(
                        node_id="worker",
                        node_kind="subagent",
                        executor="versioned_worker",
                        capability="versioned.worker",
                        metadata={"domain_version": "1.0.0"},
                        consumes=[ArtifactPort(artifact_type="versioned_input")],
                        produces=[ArtifactPort(artifact_type="versioned_output")],
                    )
                ],
            )
            registry.install(source_v2, replace=True)

            runner = ExecutionGraphRunner(registry=registry, run_root=root / "run")
            result = runner.run(
                graph=old_graph,
                run_id="versioned-run",
                thread_id="versioned-thread",
                request_id="versioned-request",
                seed_artifacts=[
                    ArtifactDraft(
                        artifact_type="versioned_input",
                        content={"value": "input"},
                        source_refs=[{"type": "test", "id": "versioned-input"}],
                    )
                ],
            )

            self.assertEqual(result.snapshot.status, "completed")
            self.assertEqual(result.snapshot.graph.metadata["domain_version"], "1.0.0")
            output = runner.board.get(result.final_artifact_ids[0])
            self.assertIsNotNone(output)
            assert output is not None
            self.assertEqual(output.content["value"], "v1")
            self.assertIn("v1_marker", output.content)
            self.assertNotIn("v2_marker", output.content)
            self.assertEqual(len(result.snapshot.gate_evaluations), 1)
            self.assertEqual(result.snapshot.gate_evaluations[0].reason, "v1 gate")
            self.assertEqual(
                registry.get("versioned_pack", version="1.0.0").version,
                "1.0.0",
            )

    def test_archived_pack_dependencies_are_resolved_and_validated(self) -> None:
        def write_minimal_pack(
            root: Path,
            directory_name: str,
            domain_id: str,
            version: str,
            dependencies: list[dict] | None = None,
        ) -> Path:
            pack_root = root / directory_name
            for directory in DomainPackRegistry.REQUIRED_DIRECTORIES:
                (pack_root / directory).mkdir(parents=True, exist_ok=True)
            payload = {
                "domain_id": domain_id,
                "version": version,
                "description": f"{domain_id} dependency test pack.",
                "dependencies": list(dependencies or []),
                "artifact_types": [{"artifact_type": f"{domain_id}_input"}],
                "capabilities": [],
            }
            (pack_root / "domain.yaml").write_text(
                yaml.safe_dump(payload, sort_keys=False),
                encoding="utf-8",
            )
            (pack_root / "artifact_schemas" / f"{domain_id}_input.json").write_text(
                '{"type":"object"}',
                encoding="utf-8",
            )
            return pack_root

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sources = root / "sources"
            registry_root = root / "registry"
            shared_v1 = write_minimal_pack(sources, "shared_v1", "shared", "1.0.0")
            shared_v2 = write_minimal_pack(sources, "shared_v2", "shared", "2.0.0")
            dependent_v1 = write_minimal_pack(
                sources,
                "dependent_v1",
                "dependent",
                "1.0.0",
                [{"domain_id": "shared", "min_version": "1.0.0", "max_version": "1.9.0"}],
            )
            dependent_v2 = write_minimal_pack(
                sources,
                "dependent_v2",
                "dependent",
                "2.0.0",
                [{"domain_id": "shared", "min_version": "1.0.0", "max_version": "2.9.0"}],
            )

            registry = DomainPackRegistry.load(registry_root)
            registry.install(shared_v1)
            registry.install(dependent_v1)
            registry.install(dependent_v2, replace=True)
            registry.install(shared_v2, replace=True)

            reloaded = DomainPackRegistry.load(registry_root)
            self.assertEqual(
                reloaded.get("dependent", version="1.0.0").dependencies[0].domain_id,
                "shared",
            )

            shutil.rmtree(registry_root / ".versions" / "shared" / "1.0.0")
            with self.assertRaisesRegex(ValueError, "dependency is not installed|dependency version is incompatible"):
                DomainPackRegistry.load(registry_root)


if __name__ == "__main__":
    unittest.main()
