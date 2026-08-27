#!/usr/bin/env python3
"""Generate the frontend's Finance Artifact type inventory from Pack schemas.

The generated module is intentionally limited to backend-owned facts: declared
artifact types and JSON-Schema field paths.  Renderer ownership and field
coverage remain hand-authored in the frontend, so a newly added backend field
cannot silently become "covered" merely by running this generator.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACK_ROOT = REPOSITORY_ROOT / "domain_packs" / "finance_ashare"
SCHEMA_ROOT = PACK_ROOT / "artifact_schemas"
DOMAIN_MANIFEST = PACK_ROOT / "domain.yaml"
DEFAULT_OUTPUT = (
    REPOSITORY_ROOT
    / "frontend"
    / "src"
    / "artifacts"
    / "generated"
    / "finance-artifact-contracts.ts"
)


def _declared_artifact_types() -> list[str]:
    # Avoid making PyYAML a requirement of the frontend generation command.
    # The manifest's artifact_types block is deliberately simple and ends at
    # the first top-level key after it.
    lines = DOMAIN_MANIFEST.read_text(encoding="utf-8").splitlines()
    in_artifacts = False
    result: list[str] = []
    for line in lines:
        if line == "artifact_types:":
            in_artifacts = True
            continue
        if in_artifacts and line and not line.startswith(" "):
            break
        marker = "  - artifact_type: "
        if in_artifacts and line.startswith(marker):
            result.append(line.removeprefix(marker).strip())
    if not result:
        raise RuntimeError(f"No artifact types found in {DOMAIN_MANIFEST}")
    return result


def _load_schema(artifact_type: str) -> dict[str, Any]:
    path = SCHEMA_ROOT / f"{artifact_type}.json"
    if not path.is_file():
        raise RuntimeError(f"Missing schema for declared artifact: {artifact_type}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Artifact schema must be an object: {path}")
    return value


def _resolve_local_ref(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return schema
    current: Any = root
    for part in reference.removeprefix("#/").split("/"):
        if not isinstance(current, dict) or part not in current:
            raise RuntimeError(f"Unresolvable local schema reference: {reference}")
        current = current[part]
    if not isinstance(current, dict):
        raise RuntimeError(f"Local schema reference is not an object: {reference}")
    return {**current, **{key: value for key, value in schema.items() if key != "$ref"}}


def _field_paths(schema: dict[str, Any], prefix: str = "", *, root: dict[str, Any] | None = None) -> list[str]:
    root = root or schema
    schema = _resolve_local_ref(schema, root)
    paths: list[str] = []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return paths
    for name, raw_child in properties.items():
        child = _resolve_local_ref(raw_child, root) if isinstance(raw_child, dict) else {}
        path = f"{prefix}.{name}" if prefix else name
        paths.append(path)
        child_type = child.get("type")
        types = set(child_type if isinstance(child_type, list) else [child_type])
        if "object" in types or isinstance(child.get("properties"), dict):
            paths.extend(_field_paths(child, path, root=root))
        if "array" in types or "items" in child:
            item = child.get("items")
            if isinstance(item, dict):
                paths.extend(_field_paths(item, f"{path}[]", root=root))
    return paths


def generate() -> str:
    artifact_types = _declared_artifact_types()
    schemas = {artifact_type: _load_schema(artifact_type) for artifact_type in artifact_types}
    schema_types = {path.stem for path in SCHEMA_ROOT.glob("*.json")}
    declared = set(artifact_types)
    if schema_types != declared:
        missing = sorted(declared - schema_types)
        extra = sorted(schema_types - declared)
        raise RuntimeError(f"Artifact/schema inventory mismatch; missing={missing}, extra={extra}")

    lines = [
        "// Run: python scripts/generate_finance_artifact_contracts.py",
        "// Renderer ownership and field coverage are intentionally not generated.",
        "",
        "export const FINANCE_ARTIFACT_TYPES = [",
        *[f"  {json.dumps(value)}," for value in artifact_types],
        "] as const",
        "",
        "export type FinanceArtifactType = typeof FINANCE_ARTIFACT_TYPES[number]",
        "",
    ]
    lines.append("export const FINANCE_ARTIFACT_SCHEMA_FIELDS = {")
    for artifact_type in artifact_types:
        lines.append(f"  {json.dumps(artifact_type)}: [")
        lines.extend(
            f"    {json.dumps(field)},"
            for field in _field_paths(schemas[artifact_type], root=schemas[artifact_type])
        )
        lines.append("  ],")
    lines.extend(
        [
            "] as unknown as never",  # replaced below to keep prettier output stable
        ]
    )
    # The closing line above is assembled this way so accidental search/replace
    # cannot produce an untyped object.  Normalize it in the final text.
    text = "\n".join(lines) + "\n"
    return text.replace("] as unknown as never\n", "} as const satisfies Record<FinanceArtifactType, readonly string[]>\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Fail when generated output is stale")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    expected = generate()
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""
        if current != expected:
            raise SystemExit(f"Generated Finance Artifact contracts are stale: {args.output}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()
