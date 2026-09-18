"""Independent decoder for assertions against the compact prompt contract."""

def decode_evidence(root):
    def pointer(path):
        value = root
        for part in path.split("/")[1:]:
            part = part.replace("~1", "/").replace("~0", "~")
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value

    def decode(value):
        if isinstance(value, dict):
            if set(value) == {"__uzi_same_value_reference__"}:
                return decode(pointer(value["__uzi_same_value_reference__"]))
            if set(value) == {"__uzi_indexed_sequence__"}:
                sparse = value["__uzi_indexed_sequence__"]
                return [decode(sparse["items"][str(index)]) if str(index) in sparse["items"]
                        else {"__uzi_prompt_omitted__": True} for index in range(sparse["length"])]
            return {key: decode(child) for key, child in value.items()}
        if isinstance(value, list):
            return [decode(child) for child in value]
        return value
    return decode(root)


def decode_paths(value):
    if isinstance(value, list):
        return value
    return sorted(prefix + suffix for prefix, suffixes in value["__uzi_path_groups__"].items()
                  for suffix in suffixes)
