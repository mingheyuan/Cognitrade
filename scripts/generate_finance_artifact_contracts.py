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


def _typescript_literal(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return str(value)
    return "unknown"


def _typescript_type(schema: dict[str, Any], *, indent: int = 0, root: dict[str, Any] | None = None) -> str:
    root = root or schema
    schema = _resolve_local_ref(schema, root)
    if "const" in schema:
        return _typescript_literal(schema["const"])
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return " | ".join(_typescript_literal(item) for item in enum)

    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        variants = [
            _typescript_type({**schema, "type": item}, indent=indent, root=root)
            for item in raw_type
        ]
        return " | ".join(dict.fromkeys(variants))
    if raw_type == "string":
        return "string"
    if raw_type in {"number", "integer"}:
        return "number"
    if raw_type == "boolean":
        return "boolean"
    if raw_type == "null":
        return "null"
    if raw_type == "array" or "items" in schema:
        item = schema.get("items")
        item_type = _typescript_type(item, indent=indent, root=root) if isinstance(item, dict) else "unknown"
        return f"Array<{item_type}>"
    if raw_type == "object" or isinstance(schema.get("properties"), dict):
        properties = schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            return "Record<string, unknown>"
        required = set(schema.get("required") or [])
        padding = " " * indent
        child_padding = " " * (indent + 2)
        rows = []
        for name, raw_child in properties.items():
            child = raw_child if isinstance(raw_child, dict) else {}
            optional = "" if name in required else "?"
            rows.append(
                f"{child_padding}{json.dumps(name, ensure_ascii=False)}{optional}: "
                f"{_typescript_type(child, indent=indent + 2, root=root)}"
            )
        return "{\n" + ";\n".join(rows) + f";\n{padding}}}"
    return "unknown"


def _type_name(artifact_type: str) -> str:
    return "".join(part.capitalize() for part in artifact_type.split("_")) + "Content"


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
    for artifact_type in artifact_types:
        lines.extend(
            [
                f"export type {_type_name(artifact_type)} = {_typescript_type(schemas[artifact_type], root=schemas[artifact_type])}",
                "",
            ]
        )
    lines.extend(
        [
            "export type FinanceArtifactContentMap = {",
            *[
                f"  {json.dumps(artifact_type)}: {_type_name(artifact_type)}"
                for artifact_type in artifact_types
            ],
            "}",
            "",
            "export const FINANCE_ARTIFACT_SCHEMA_FIELDS = {",
        ]
    )
    for artifact_type in artifact_types:
        lines.append(f"  {json.dumps(artifact_type)}: [")
        for field in _field_paths(schemas[artifact_type], root=schemas[artifact_type]):
            lines.append(f"    {json.dumps(field)},")
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
