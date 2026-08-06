from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACK_ROOT = REPOSITORY_ROOT / "domain_packs" / "finance_ashare"
SCHEMA_ROOT = PACK_ROOT / "artifact_schemas"
UI_CONTRACT_PATH = (
    REPOSITORY_ROOT
    / "frontend"
    / "src"
    / "artifacts"
    / "finance-artifact-renderers.json"
)


def _resolve_local_ref(schema: dict, root: dict) -> dict:
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return schema
    current = root
    for part in reference.removeprefix("#/").split("/"):
        current = current[part]
    return {**current, **{key: value for key, value in schema.items() if key != "$ref"}}


def _schema_field_paths(schema: dict, prefix: str = "", *, root: dict | None = None) -> list[str]:
    root = root or schema
    schema = _resolve_local_ref(schema, root)
    paths: list[str] = []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return paths
    for name, child_value in properties.items():
        child = _resolve_local_ref(child_value, root) if isinstance(child_value, dict) else {}
        path = f"{prefix}.{name}" if prefix else name
        paths.append(path)
        paths.extend(_schema_field_paths(child, path, root=root))
        item = child.get("items")
        if isinstance(item, dict):
            paths.extend(_schema_field_paths(item, f"{path}[]", root=root))
    return paths


def _field_digest(schema: dict) -> str:
    payload = "\n".join(_schema_field_paths(schema, root=schema)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


class FinanceArtifactUiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = yaml.safe_load((PACK_ROOT / "domain.yaml").read_text(encoding="utf-8"))
        self.declared = {
            str(row["artifact_type"])
            for row in self.manifest.get("artifact_types") or []
        }
        self.schemas = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in SCHEMA_ROOT.glob("*.json")
        }
        self.ui_contract = json.loads(UI_CONTRACT_PATH.read_text(encoding="utf-8"))
        self.renderers = dict(self.ui_contract.get("artifacts") or {})

    def test_manifest_schema_and_renderer_inventories_are_exact(self) -> None:
        self.assertEqual(set(self.schemas), self.declared)
        self.assertEqual(set(self.renderers), self.declared)
        self.assertEqual(len(self.renderers), 44)

    def test_every_artifact_has_a_named_dedicated_renderer(self) -> None:
        renderer_names: list[str] = []
        forbidden_markers = ("generic", "unknown", "fallback", "rawjson", "raw_json")
        for artifact_type, contract in self.renderers.items():
            with self.subTest(artifact_type=artifact_type):
                title = str(contract.get("title") or "").strip()
                renderer = str(contract.get("renderer") or "").strip()
                self.assertTrue(title)
                self.assertTrue(renderer.endswith("ArtifactRenderer"))
                self.assertFalse(any(marker in renderer.casefold() for marker in forbidden_markers))
                renderer_names.append(renderer)
        self.assertEqual(len(renderer_names), len(set(renderer_names)))

    def test_schema_field_digests_require_explicit_renderer_review(self) -> None:
        for artifact_type, schema in self.schemas.items():
            with self.subTest(artifact_type=artifact_type):
                expected = str(self.renderers[artifact_type].get("schema_field_digest") or "")
                self.assertEqual(expected, _field_digest(schema))

    def test_artifact_roots_reject_undeclared_business_fields(self) -> None:
        for artifact_type, schema in self.schemas.items():
            with self.subTest(artifact_type=artifact_type):
                self.assertEqual(schema.get("type"), "object")
                self.assertIsInstance(schema.get("properties"), dict)
                self.assertTrue(schema.get("properties"))
                self.assertIs(schema.get("additionalProperties"), False)

    def test_fund_artifact_nested_objects_reject_undeclared_fields(self) -> None:
        def assert_closed(node: dict, path: str) -> None:
            if node.get("type") == "object" or isinstance(node.get("properties"), dict):
                self.assertIs(
                    node.get("additionalProperties"),
                    False,
                    msg=f"open nested fund contract: {path}",
                )
                for name, child in (node.get("properties") or {}).items():
                    if isinstance(child, dict):
                        assert_closed(child, f"{path}.{name}")
            item = node.get("items")
            if isinstance(item, dict):
                assert_closed(item, f"{path}[]")

        for artifact_type in ("fund_snapshot", "fund_analysis"):
            with self.subTest(artifact_type=artifact_type):
                assert_closed(self.schemas[artifact_type], artifact_type)

    def test_specialist_delivery_contracts_close_all_pack_owned_objects(self) -> None:
        def assert_closed(node: dict, root: dict, path: str) -> None:
            node = _resolve_local_ref(node, root)
            if node.get("type") == "object" or isinstance(node.get("properties"), dict):
                if node.get("x-business-extensible") is not True:
                    self.assertIs(
                        node.get("additionalProperties"),
                        False,
                        msg=f"open specialist delivery contract: {path}",
                    )
                for name, child in (node.get("properties") or {}).items():
                    if isinstance(child, dict):
                        assert_closed(child, root, f"{path}.{name}")
            item = node.get("items")
            if isinstance(item, dict):
                assert_closed(item, root, f"{path}[]")
            for branch_key in ("anyOf", "oneOf", "allOf"):
                for index, branch in enumerate(node.get(branch_key) or []):
                    if isinstance(branch, dict):
                        assert_closed(branch, root, f"{path}.{branch_key}[{index}]")

        for artifact_type in (
            "screening_result",
            "sector_analysis",
            "thesis_review",
            "source_record_set",
            "distillation_result",
            "distillation_review",
        ):
            with self.subTest(artifact_type=artifact_type):
                schema = self.schemas[artifact_type]
                assert_closed(schema, schema, artifact_type)

    def test_uzi_decision_and_portfolio_contracts_close_pack_owned_objects(self) -> None:
        def assert_closed(node: dict, root: dict, path: str) -> None:
            node = _resolve_local_ref(node, root)
            if node.get("type") == "object" or isinstance(node.get("properties"), dict):
                if node.get("x-business-extensible") is not True:
                    self.assertIs(node.get("additionalProperties"), False, msg=f"open UZI contract: {path}")
                for name, child in (node.get("properties") or {}).items():
                    if isinstance(child, dict):
                        assert_closed(child, root, f"{path}.{name}")
            item = node.get("items")
            if isinstance(item, dict):
                assert_closed(item, root, f"{path}[]")
            for branch_key in ("anyOf", "oneOf", "allOf"):
                for index, branch in enumerate(node.get(branch_key) or []):
                    if isinstance(branch, dict):
                        assert_closed(branch, root, f"{path}.{branch_key}[{index}]")

        for artifact_type in (
            "valuation_model", "investor_panel_review", "risk_scan", "uzi_dimension_coverage",
            "returns_attribution", "holding_rebalance", "portfolio_rebalance",
        ):
            with self.subTest(artifact_type=artifact_type):
                schema = self.schemas[artifact_type]
                assert_closed(schema, schema, artifact_type)

    def test_portfolio_rebalance_schema_declares_every_success_payload_field(self) -> None:
        properties = self.schemas["portfolio_rebalance"]["properties"]
        self.assertTrue({
            "target_mode",
            "drift_threshold_pp",
            "asset_classes",
            "portfolio_value",
        }.issubset(properties))

    def test_generated_frontend_contract_is_current(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(REPOSITORY_ROOT / "scripts" / "generate_finance_artifact_contracts.py"),
                "--check",
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
