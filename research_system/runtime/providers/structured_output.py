"""Normalize Pydantic JSON Schema for the Codex structured-output contract."""
from copy import deepcopy


def strict_schema(schema: dict) -> dict | None:
    # Codex's strict schema cannot express arbitrary keyed maps. Closing them
    # would silently erase report dimensions / evidence / dashboard fields.
    # Such schemas use prompt JSON + unchanged Pydantic validation instead.
    def has_open_map(node):
        if isinstance(node, list):
            return any(has_open_map(child) for child in node)
        if not isinstance(node, dict):
            return False
        if node.get("type") == "object" and (
            isinstance(node.get("additionalProperties"), dict)
            or node.get("additionalProperties") is True
            or (not node.get("properties") and node.get("additionalProperties") is not False)
        ):
            return True
        return any(has_open_map(child) for child in node.values())

    if has_open_map(schema):
        return None
    value = deepcopy(schema)

    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            if node.get("default", ...) is None:
                node.pop("default")
            for child in node.values():
                if isinstance(child, (dict, list)):
                    visit(child)
    visit(value)
    return value
