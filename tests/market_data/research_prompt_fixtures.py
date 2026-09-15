"""Independent decoder for auditing the DC's non-authoritative prompt view."""


def restore_background(view):
    def pointer(target):
        value = view
        for token in target.split("/")[1:]:
            key = token.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value

    def restore(value):
        if isinstance(value, list):
            return [restore(item) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {"__uzi_same_value_reference__"}:
            return restore(pointer(value["__uzi_same_value_reference__"]))
        for marker in ("__uzi_keyed_table__", "__uzi_row_table__"):
            if set(value) == {marker}:
                table = value[marker]
                def row(items):
                    assert len(items) == len(table["columns"])
                    return {**restore(table["common_values"]),
                            **dict(zip(table["columns"], restore(items), strict=True))}
                return ({key: row(items) for key, items in table["rows"].items()}
                        if marker == "__uzi_keyed_table__" else [row(items) for items in table["rows"]])
        if set(value) == {"__uzi_indexed_sequence__"}:
            sequence = value["__uzi_indexed_sequence__"]
            items = restore(sequence["items"])
            return [items.get(str(index), {"__uzi_prompt_omitted__": True})
                    for index in range(sequence["original_length"])]
        return {key: restore(item) for key, item in value.items()}
    return restore(view)
